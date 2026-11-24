import { useEffect, useMemo, useState } from 'react';
import { api, type QuadBlock, type QuadMap } from '../api';
import type { PageProps } from '../App';
import { Drawer, fmt, Modal, PageHead, PeriodPicker, useLoad, useToast, wan, yuan } from '../ui';

type View = 'all' | '盈利' | '微利' | '亏损' | '战略' | '不可判定';
type SortKey = 'priority' | 'net_asc' | 'net_rate_asc' | 'gm_rate_asc' | 'rev_desc';

const TIERS: { id: Exclude<View, 'all' | '不可判定'>; label: string; tone: string; hint: string }[] = [
  { id: '盈利', label: '盈利', tone: 'good', hint: '优先保障资源' },
  { id: '微利', label: '微利', tone: 'warn', hint: '提效控本优化' },
  { id: '亏损', label: '亏损', tone: 'bad', hint: '限制新增 / 止损' },
  { id: '战略', label: '战略', tone: 'info', hint: '手工标记 · 限额复评' },
];

const SORTS: { id: SortKey; label: string }[] = [
  { id: 'priority', label: '管理优先（问题在前）' },
  { id: 'net_asc', label: '净利润从低到高' },
  { id: 'net_rate_asc', label: '利润率从低到高' },
  { id: 'gm_rate_asc', label: '毛益率从低到高' },
  { id: 'rev_desc', label: '形象收入从高到低' },
];

function rateText(x: number | null | undefined): string {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  return `${(x * 100).toFixed(1)}%`;
}
function lineText(x: number | null | undefined): string {
  if (x === null || x === undefined) return '不设下限';
  const n = x * 100;
  return `${Number.isInteger(n) ? n.toFixed(0) : n.toFixed(2)}%`;
}
function known(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return '未知';
  return fmt(v, digits);
}
function tagOf(q: string): string {
  if (q === '盈利') return 'good';
  if (q === '亏损') return 'bad';
  if (q === '微利') return 'warn';
  if (q === '战略') return 'info';
  return '';
}
function toInput(v: number | null): string {
  if (v === null || v === undefined) return '';
  return String(Math.round(v * 100000) / 1000);
}
function priorityRank(b: QuadBlock): number {
  if (b.quadrant === '亏损') return 0;
  if (b.quadrant === '战略') return b.loss_visible ? 1 : 2;
  if (b.quadrant === '微利') return 3;
  if (b.quadrant === '盈利') return 4;
  return 5;
}

export default function BlockMap({ me, route, setRoute }: PageProps) {
  const periods = useLoad(() => api.get<string[]>('/api/periods'), []);
  const list = periods.data || [];
  const thisYear = String(new Date().getFullYear());
  const defaultYear = list.some((p) => p.startsWith(thisYear)) ? thisYear : list[0]?.slice(0, 4) || '';
  const period = route.period || defaultYear;
  const toast = useToast();
  const map = useLoad(() => api.get<QuadMap>(`/api/quad-map?period=${encodeURIComponent(period)}`), [period]);

  const [view, setView] = useState<View>('all');
  const [query, setQuery] = useState('');
  const [sortBy, setSortBy] = useState<SortKey>('priority');
  const [picked, setPicked] = useState<string | null>(null);
  const [showThreshold, setShowThreshold] = useState(false);
  const [showFormula, setShowFormula] = useState(false);

  const [profitNet, setProfitNet] = useState('');
  const [profitGm, setProfitGm] = useState('');
  const [thinNet, setThinNet] = useState('');
  const [thinGm, setThinGm] = useState('');
  const [thinOpen, setThinOpen] = useState(false);
  const [rationale, setRationale] = useState('');
  const [reviewDue, setReviewDue] = useState('');
  const [risk, setRisk] = useState('');
  const [clearReason, setClearReason] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const t = map.data?.thresholds;
    if (!t) return;
    setProfitNet(toInput(t.profit_net_min));
    setProfitGm(toInput(t.profit_gm_min));
    setThinGm(toInput(t.thin_gm_min));
    setThinOpen(t.thin_net_min !== null);
    setThinNet(toInput(t.thin_net_min));
  }, [map.data]);

  const data = map.data;
  const th = data?.thresholds;
  const current = data?.blocks.find((b) => b.block === picked) || null;
  const canOpenWell = me.nav.some((g) => g.items.some((i) => i.id === 'overview'));

  useEffect(() => {
    const s = current?.strategy;
    setRationale(s?.rationale || '');
    setReviewDue(s?.review_due || '');
    setRisk(s?.risk_limit || '');
    setClearReason('');
  }, [current?.block, current?.strategy?.marked_at]);

  /** 按四个核心层级汇总区块数、收入、净利润与加权两率，供首屏决策卡使用。 */
  const summary = useMemo(() => {
    const blocks = data?.blocks || [];
    return TIERS.map((tier) => {
      const items = blocks.filter((b) => b.quadrant === tier.id);
      let revenue = 0;
      let netProfit = 0;
      let grossMargin = 0;
      let activeCount = 0;
      let lossCount = 0;
      let overdueCount = 0;
      for (const b of items) {
        if (b.well_count > 0) {
          revenue += b.revenue;
          netProfit += b.net_profit;
          grossMargin += b.gross_margin;
          activeCount += 1;
        }
        if (b.loss_visible) lossCount += 1;
        if (b.strategy?.overdue) overdueCount += 1;
      }
      const hasRev = activeCount > 0 && revenue > 0;
      return {
        ...tier,
        count: items.length,
        activeCount,
        revenue,
        netProfit,
        netRate: hasRev ? netProfit / revenue : null,
        gmRate: hasRev ? grossMargin / revenue : null,
        lossCount,
        overdueCount,
      };
    });
  }, [data]);

  const shown = useMemo(() => {
    if (!data) return [];
    const q = query.trim().toLowerCase();
    const filtered = data.blocks.filter((b) => {
      if (view !== 'all' && b.quadrant !== view) return false;
      if (!q) return true;
      return b.block.toLowerCase().includes(q) || b.teams.some((t) => t.toLowerCase().includes(q));
    });
    const numAsc = (a: number | null, b: number | null) => {
      if (a === null && b === null) return 0;
      if (a === null) return 1;
      if (b === null) return -1;
      return a - b;
    };
    return [...filtered].sort((a, b) => {
      if (sortBy === 'priority') {
        const diff = priorityRank(a) - priorityRank(b);
        if (diff !== 0) return diff;
        return a.net_profit - b.net_profit || a.block.localeCompare(b.block, 'zh-CN');
      }
      if (sortBy === 'net_asc') return a.net_profit - b.net_profit || a.block.localeCompare(b.block, 'zh-CN');
      if (sortBy === 'net_rate_asc') return numAsc(a.net_rate, b.net_rate) || a.block.localeCompare(b.block, 'zh-CN');
      if (sortBy === 'gm_rate_asc') return numAsc(a.gm_rate, b.gm_rate) || a.block.localeCompare(b.block, 'zh-CN');
      return b.revenue - a.revenue || a.block.localeCompare(b.block, 'zh-CN');
    });
  }, [data, view, query, sortBy]);

  const sortedWells = useMemo(() => {
    if (!current) return [];
    return [...current.wells].sort((a, b) => a.net_profit - b.net_profit || a.well_id.localeCompare(b.well_id, 'zh-CN'));
  }, [current]);

  const percent = (raw: string, label: string) => {
    const v = Number(raw);
    if (!Number.isFinite(v)) throw new Error(`${label}要填数字。页面按百分数填，0 表示 0%，5 表示 5%`);
    return v / 100;
  };
  const saveThresholds = async (confirm: boolean) => {
    setBusy(true);
    try {
      await api.put('/api/quad-map/thresholds', {
        profit_net_min: percent(profitNet, '盈利利润率'),
        profit_gm_min: percent(profitGm, '盈利毛益率'),
        thin_gm_min: percent(thinGm, '微利毛益率'),
        thin_net_min: thinOpen ? percent(thinNet, '微利利润率') : null,
        confirm,
      });
      toast(confirm ? '已确认当前阈值，正式作为四维地图分类标准' : '已保存，尚未确认，地图仍按待确认参考显示');
      setShowThreshold(false);
      map.reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const withdraw = async () => {
    setBusy(true);
    try {
      await api.post('/api/quad-map/thresholds/withdraw');
      toast('已撤回确认，数值保留但回到待确认状态');
      setShowThreshold(false);
      map.reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const mark = async () => {
    if (!current) return;
    setBusy(true);
    try {
      await api.post('/api/quad-map/strategy', { block: current.block, rationale, review_due: reviewDue, risk_limit: risk });
      toast('已标记战略，基础财务分类仍然保留');
      map.reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const clearMark = async () => {
    if (!current) return;
    setBusy(true);
    try {
      await api.post('/api/quad-map/strategy/clear', { block: current.block, reason: clearReason });
      toast('已取消战略标记，区块回到基础财务分类');
      map.reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  if (!period) return <div className="loading">{periods.error || '加载中…'}</div>;

  const unknownCount = data?.counts['不可判定'] || 0;
  const subText = data
    ? `${data.scope}${th?.state === 'confirmed' ? ` · 标准已确认（盈利 ≥ 利润率 ${lineText(th.profit_net_min)} / 毛益率 ${lineText(th.profit_gm_min)}）` : ` · ${th?.state_label || '待确认'}`}`
    : '按盈利、微利、亏损、战略四个层级分类区块，辅助经营决策';

  return (
    <>
      <PageHead
        title="四维地图"
        sub={subText}
        right={(
          <div className="head-actions">
            <PeriodPicker value={period} periods={list} allowYear onChange={(p) => setRoute({ screen: 'quadmap', period: p })} />
            {data && (
              <button type="button" className="btn sm" onClick={() => setShowFormula(true)}>口径说明</button>
            )}
            {data?.can_maintain && th && (
              <button type="button" className={'btn sm' + (th.state !== 'confirmed' ? ' primary' : '')} onClick={() => setShowThreshold(true)}>
                阈值设置
              </button>
            )}
          </div>
        )}
      />

      {map.error && <div className="notice">{map.error}</div>}
      {!data && !map.error && <div className="loading">加载中…</div>}

      {th && th.state !== 'confirmed' && (
        <div className="notice quad-banner" role="status">
          <span>
            <b>{th.state_label}：</b>
            {th.state === 'unset'
              ? '尚未配置完整阈值，当前不判定盈利、微利、亏损。'
              : '当前采用会计零线作为初始参考，尚未经经营管理确认，不作为正式考核标准。'}
          </span>
          {data?.can_maintain && (
            <button type="button" className="btn sm" onClick={() => setShowThreshold(true)}>确认阈值</button>
          )}
        </div>
      )}

      {data && (
        <>
          <section className="quad-cards" aria-label="四维分类汇总">
            {summary.map((item) => {
              const active = view === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`quad-card tone-${item.tone}${active ? ' on' : ''}`}
                  aria-pressed={active}
                  onClick={() => setView(active ? 'all' : item.id)}
                >
                  <div className="quad-card-top">
                    <span className={'tag ' + item.tone}>{item.label}</span>
                    <span className="small muted">{item.hint}</span>
                  </div>
                  <div className="quad-card-count num">
                    <b>{item.count}</b>
                    <span>个区块</span>
                  </div>
                  <div className="quad-card-metrics">
                    <div className="quad-metric">
                      <span>净利润</span>
                      <b className={'num' + (item.netProfit < 0 ? ' neg' : '')}>
                        {item.activeCount ? `${wan(item.netProfit)} 万` : '—'}
                      </b>
                    </div>
                    <div className="quad-metric">
                      <span>形象收入</span>
                      <b className="num">{item.activeCount ? `${wan(item.revenue)} 万` : '—'}</b>
                    </div>
                  </div>
                  <div className="quad-card-foot num">
                    <span>利润率 {rateText(item.netRate)}</span>
                    <span>毛益率 {rateText(item.gmRate)}</span>
                  </div>
                  {item.id === '战略' && (item.lossCount > 0 || item.overdueCount > 0) && (
                    <div className="quad-card-alert">
                      {item.lossCount > 0 && <span>含底层亏损 {item.lossCount} 个</span>}
                      {item.overdueCount > 0 && <span>已过复评期 {item.overdueCount} 个</span>}
                    </div>
                  )}
                </button>
              );
            })}
          </section>

          <section className="card">
            <div className="quad-toolbar">
              <div className="quad-toolbar-left">
                <input
                  className="input ov-search"
                  type="search"
                  aria-label="搜索区块或队伍"
                  placeholder="搜索区块或队伍…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                {view !== 'all' && (
                  <button type="button" className="chip-x" onClick={() => setView('all')}>
                    筛选：{view} <span aria-hidden="true">×</span>
                  </button>
                )}
                {unknownCount > 0 && (
                  <button
                    type="button"
                    className={'quad-pill-btn' + (view === '不可判定' ? ' on' : '')}
                    aria-pressed={view === '不可判定'}
                    onClick={() => setView(view === '不可判定' ? 'all' : '不可判定')}
                  >
                    不可判定 {unknownCount}
                  </button>
                )}
              </div>
              <div className="quad-toolbar-right">
                <label className="pill">
                  <span className="muted">排序</span>
                  <select aria-label="排序方式" value={sortBy} onChange={(e) => setSortBy(e.target.value as SortKey)}>
                    {SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
                  </select>
                </label>
                <span className="small muted num">显示 {shown.length} / {data.blocks.length} 个区块</span>
              </div>
            </div>

            <div className="quad-scroll">
              <table className="quad-table">
                <thead>
                  <tr>
                    <th>区块</th>
                    <th>当前分类</th>
                    <th>底层财务</th>
                    <th className="r">形象收入（元）</th>
                    <th className="r">净利润（元）</th>
                    <th className="r">利润率</th>
                    <th className="r">毛益率</th>
                    <th className="r">井数 / 台月</th>
                    <th>经营参考</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((b) => (
                    <tr
                      key={b.block}
                      className={'pick' + (picked === b.block ? ' on' : '')}
                      tabIndex={0}
                      onClick={() => setPicked(b.block)}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault();
                          setPicked(b.block);
                        }
                      }}
                    >
                      <td>
                        <div className="quad-block-cell">
                          <span className="bold">{b.block}</span>
                          {b.quadrant === '战略' && b.loss_visible && <span className="tag bad">底层亏损</span>}
                          {b.strategy?.overdue && <span className="tag warn">复评过期</span>}
                        </div>
                      </td>
                      <td><span className={'tag ' + tagOf(b.quadrant)}>{b.quadrant_label}</span></td>
                      <td className="muted">{b.quadrant === '战略' ? b.financial_label : '—'}</td>
                      <td className="r num">{b.well_count ? yuan(b.revenue) : '—'}</td>
                      <td className={'r num bold' + (b.well_count && b.net_profit < 0 ? ' neg' : '')}>
                        {b.well_count ? yuan(b.net_profit) : '—'}
                      </td>
                      <td className={'r num' + (b.net_rate !== null && b.net_rate < 0 ? ' neg' : '')}>{rateText(b.net_rate)}</td>
                      <td className={'r num' + (b.gm_rate !== null && b.gm_rate < 0 ? ' neg' : '')}>{rateText(b.gm_rate)}</td>
                      <td className="r num muted">
                        {b.well_count} 口 · {b.rig_months === null ? '台月未知' : `${fmt(b.rig_months, 1)} 台月`}
                      </td>
                      <td>{b.advice.title}</td>
                    </tr>
                  ))}
                  {!shown.length && (
                    <tr>
                      <td colSpan={9} className="empty">当前筛选条件下没有区块。</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="quad-table-foot small muted">
              <span>点击任意区块行查看单井明细与战略标记。{data.disclaimer}</span>
            </div>
          </section>
        </>
      )}

      <Drawer
        open={!!current}
        onClose={() => setPicked(null)}
        wide
        label="区块明细"
        head={current && (
          <>
            <div className="quad-block-cell">
              <h2 style={{ margin: 0 }}>{current.block}</h2>
              <span className={'tag ' + tagOf(current.quadrant)}>{current.quadrant_label}</span>
              {current.quadrant === '战略' && <span className="tag">底层：{current.financial_label}</span>}
            </div>
            <div className="small muted">{current.advice.title} · 纳入汇总 {current.well_count} 口井</div>
          </>
        )}
      >
        {current && (
          <div className="quad-detail">
            {current.quadrant === '战略' && current.loss_visible && (
              <div className="notice">战略标记不掩盖亏损：该区块底层财务分类仍为{current.financial_label}，严禁按盈利区块无上限追加投入。</div>
            )}
            {current.strategy?.overdue && (
              <div className="notice">该区块战略复评期（{current.strategy.review_due}）已过，请尽快复评确认是否保留战略标记。</div>
            )}
            {current.quality !== 'ok' && <div className="notice">{current.quality_label}</div>}

            <div className="kpis">
              <div className="kpi flat"><div className="k">形象收入（元）</div><div className="v"><b>{current.well_count ? yuan(current.revenue) : '—'}</b></div></div>
              <div className="kpi flat"><div className="k">变动成本（元）</div><div className="v"><b>{current.well_count ? yuan(current.variable) : '—'}</b></div></div>
              <div className="kpi flat"><div className="k">固定成本（元）</div><div className="v"><b>{current.well_count ? yuan(current.fixed) : '—'}</b></div></div>
              <div className="kpi flat"><div className="k">毛益额 / 毛益率</div><div className="v"><b>{current.well_count ? yuan(current.gross_margin) : '—'}</b><span>{rateText(current.gm_rate)}</span></div></div>
              <div className="kpi flat"><div className="k">净利润 / 利润率</div><div className="v"><b className={current.net_profit < 0 ? 'neg' : ''}>{current.well_count ? yuan(current.net_profit) : '—'}</b><span>{rateText(current.net_rate)}</span></div></div>
            </div>

            <div className="quad-advice-box">
              <b>经营参考：{current.advice.title}</b>
              <p className="msg" style={{ margin: '4px 0 0' }}>{current.advice.text}</p>
            </div>

            {current.strategy && (
              <div className="notice info">
                <b>战略依据：</b>{current.strategy.rationale}<br />
                <b>复评期：</b>{current.strategy.review_due}{current.strategy.overdue ? '（已过期）' : ''}　<b>风险约束：</b>{current.strategy.risk_limit}<br />
                <span className="small">标记人：{current.strategy.marked_by}（{current.strategy.marked_at}）</span>
              </div>
            )}

            <div className="kv-grid">
              <div className="kv"><span className="k">施工队伍</span><span className="v">{current.team_count === null ? '未知' : `${current.team_count} 支（${current.teams.join('、') || '无'}）`}</span></div>
              <div className="kv"><span className="k">钻机型号</span><span className="v">{current.rig_types.length ? current.rig_types.join('、') : '未知'}</span></div>
              <div className="kv"><span className="k">钻机台月</span><span className="v num">{known(current.rig_months)}</span></div>
              <div className="kv"><span className="k">进尺（米）</span><span className="v num">{current.footage === null ? '未知' : fmt(current.footage, 0)}</span></div>
            </div>
            <p className="small muted" style={{ margin: 0 }}>
              钻机台数：未知（主数据无独立台账，不以队伍数替代）；区块投入预算：未知。
            </p>

            {!!sortedWells.length && (
              <div className="sec">
                <div className="sec-head">
                  <h3>区块内单井明细<small>按净利润从低到高排列{canOpenWell ? '，点击可下钻单井' : ''}</small></h3>
                </div>
                <div className="quad-scroll">
                  <table className="quad-table">
                    <thead>
                      <tr><th>井号</th><th>队伍</th><th className="r">形象收入</th><th className="r">净利润</th><th className="r">利润率</th><th className="r">毛益率</th></tr>
                    </thead>
                    <tbody>
                      {sortedWells.map((w) => (
                        <tr
                          key={w.well_id}
                          className={canOpenWell ? 'pick' : ''}
                          onClick={() => { if (canOpenWell) setRoute({ screen: 'overview', well: w.well_id, period }); }}
                        >
                          <td className="bold">{w.well_id}</td>
                          <td>{w.team}</td>
                          <td className="r num">{yuan(w.revenue)}</td>
                          <td className={'r num' + (w.net_profit < 0 ? ' neg' : '')}>{yuan(w.net_profit)}</td>
                          <td className={'r num' + (w.net_rate !== null && w.net_rate < 0 ? ' neg' : '')}>{rateText(w.net_rate)}</td>
                          <td className={'r num' + (w.gm_rate !== null && w.gm_rate < 0 ? ' neg' : '')}>{rateText(w.gm_rate)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="small muted" style={{ margin: 0 }}>单井比率仅作定位对照，区块分类按区块合计金额计算，不做单井比率简单平均。</p>
              </div>
            )}

            {!!current.idle_wells.length && (
              <p className="msg" style={{ margin: 0 }}>
                未纳入本期汇总的井（本期无经济事项）：{current.idle_wells.map((w) => `${w.well_id}（${w.team}）`).join('、')}。
              </p>
            )}

            {data?.can_maintain && (
              <div className="quad-detail quad-strategy-box">
                <h3>战略标记管理</h3>
                <p className="small muted" style={{ margin: 0 }}>战略层级不从利润率推导，必须填写依据、复评期和风险约束（不可写成无限投入）。</p>
                <label className="field">战略依据
                  <textarea className="input" aria-label="战略依据" rows={2} value={rationale} onChange={(e) => setRationale(e.target.value)} />
                </label>
                <div className="quad-form">
                  <label className="field">复评截止期
                    <input className="input" type="date" aria-label="复评期" value={reviewDue} onChange={(e) => setReviewDue(e.target.value)} />
                  </label>
                  <label className="field">风险约束
                    <input className="input" aria-label="风险约束" value={risk} onChange={(e) => setRisk(e.target.value)} placeholder="例如：年度亏损上限 500 万元" />
                  </label>
                </div>
                <div className="btn-row">
                  <button type="button" className="btn primary" disabled={busy} onClick={mark}>
                    {current.strategy ? '更新战略标记' : '标记为战略区块'}
                  </button>
                </div>
                {current.strategy && (
                  <div className="quad-clear-row">
                    <label className="field" style={{ flex: 1 }}>取消战略原因
                      <input className="input" aria-label="取消原因" value={clearReason} onChange={(e) => setClearReason(e.target.value)} placeholder="填写取消原因后生效" />
                    </label>
                    <button type="button" className="btn" disabled={busy} onClick={clearMark}>取消战略标记</button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </Drawer>

      <Modal open={showThreshold && !!data?.can_maintain && !!th} onClose={() => setShowThreshold(false)} title="四维地图 · 阈值设置">
        {th && (
          <>
            <p className="msg" style={{ margin: 0 }}>
              按百分数填写（例如 0 表示 0%，5 表示 5%）。仅保存但未确认时，页面仍提示“待确认”，不作为正式考核标准。
            </p>
            {th.state === 'confirmed' && (
              <div className="notice info">当前标准已于 {th.confirmed_at} 由 {th.confirmed_by} 确认生效。</div>
            )}
            <div className="quad-form">
              <label className="field">盈利 · 利润率下限（%）
                <input className="input" inputMode="decimal" aria-label="盈利利润率下限" value={profitNet} onChange={(e) => setProfitNet(e.target.value)} />
              </label>
              <label className="field">盈利 · 毛益率下限（%）
                <input className="input" inputMode="decimal" aria-label="盈利毛益率下限" value={profitGm} onChange={(e) => setProfitGm(e.target.value)} />
              </label>
              <label className="field">微利 · 毛益率下限（%）
                <input className="input" inputMode="decimal" aria-label="微利毛益率下限" value={thinGm} onChange={(e) => setThinGm(e.target.value)} />
              </label>
              <label className="field">微利 · 利润率下限（%）
                <span className="small"><input type="checkbox" checked={thinOpen} onChange={(e) => setThinOpen(e.target.checked)} /> 设置下限</span>
                <input className="input" inputMode="decimal" aria-label="微利利润率下限" disabled={!thinOpen} value={thinOpen ? thinNet : ''} placeholder="不设下限" onChange={(e) => setThinNet(e.target.value)} />
              </label>
            </div>
            <div className="btn-row">
              <button type="button" className="btn" disabled={busy} onClick={() => saveThresholds(false)}>仅保存（待确认）</button>
              <button type="button" className="btn primary" disabled={busy} onClick={() => saveThresholds(true)}>确认采用为正式标准</button>
              {th.confirmed && <button type="button" className="btn danger" disabled={busy} onClick={withdraw}>撤回确认</button>}
            </div>
          </>
        )}
      </Modal>

      <Modal open={showFormula && !!data} onClose={() => setShowFormula(false)} title="四维地图 · 分类口径与规则">
        {data && th && (
          <>
            <div className="notice info">
              当前盈利线：利润率 {lineText(th.profit_net_min)}，毛益率 {lineText(th.profit_gm_min)}。<br />
              当前微利线：利润率 {lineText(th.thin_net_min)}，毛益率 {lineText(th.thin_gm_min)}。<br />
              当前状态：{th.state_label}。
            </div>
            <p className="msg" style={{ margin: 0 }}>{data.scope_note}</p>
            <ul className="quad-formula">
              {data.classes.map((c) => <li key={c.id}><b>{c.id}：</b>{c.rule}</li>)}
              {Object.values(data.formula).map((line) => <li key={line}>{line}</li>)}
            </ul>
            <p className="small muted" style={{ margin: 0 }}>
              {data.disclaimer} 主数据无独立钻机台账，钻机台数显示未知；系统无区块预算，投入金额显示未知。
            </p>
          </>
        )}
      </Modal>
    </>
  );
}
