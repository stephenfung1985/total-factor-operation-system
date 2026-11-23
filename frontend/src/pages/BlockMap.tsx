import { useEffect, useMemo, useState } from 'react';
import { api, type QuadBlock, type QuadMap, type QuadThresholds } from '../api';
import type { PageProps } from '../App';
import { Drawer, fmt, PageHead, PeriodPicker, useLoad, useToast, yuan } from '../ui';

type View = 'all' | '盈利' | '微利' | '亏损' | '战略' | '不可判定';
const VIEWS: { id: View; label: string }[] = [
  { id: 'all', label: '全部' }, { id: '盈利', label: '盈利' }, { id: '微利', label: '微利' },
  { id: '亏损', label: '亏损' }, { id: '战略', label: '战略' }, { id: '不可判定', label: '不可判定' },
];
const FILL: Record<string, string> = { '盈利': 'var(--good)', '微利': 'var(--warn)', '亏损': 'var(--bad)', '战略': 'var(--accent)', '不可判定': 'var(--mute)' };

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
  if (q === '微利' || q === '战略') return 'warn';
  return 'info';
}
function toInput(v: number | null): string {
  if (v === null || v === undefined) return '';
  return String(Math.round(v * 100000) / 1000);
}

function Scatter({ blocks, th, current, onPick }: { blocks: QuadBlock[]; th: QuadThresholds; current: string | null; onPick: (b: QuadBlock) => void }) {
  const pts = blocks.filter((b) => b.net_rate !== null && b.gm_rate !== null);
  if (!pts.length) {
    return <p className="msg">本期没有可以落在图上的区块。收入为零、为负或数据不足的区块不画在原点，避免看起来像 0%。</p>;
  }
  const xs = pts.map((b) => b.net_rate as number);
  const ys = pts.map((b) => b.gm_rate as number);
  const gx = [th.profit_net_min, th.thin_net_min].filter((v): v is number => v !== null);
  const gy = [th.profit_gm_min, th.thin_gm_min].filter((v): v is number => v !== null);
  const allX = [...xs, ...gx];
  const allY = [...ys, ...gy];
  const span = (arr: number[]) => {
    const lo = Math.min(...arr), hi = Math.max(...arr);
    const pad = (hi - lo || 0.1) * 0.14;
    return [lo - pad, hi + pad] as const;
  };
  const [x0, x1] = span(allX);
  const [y0, y1] = span(allY);
  const W = 680, H = 420, L = 56, R = 20, T = 18, B = 40;
  const X = (v: number) => L + ((v - x0) / (x1 - x0)) * (W - L - R);
  const Y = (v: number) => T + ((y1 - v) / (y1 - y0)) * (H - T - B);
  const hLine = (v: number | null, dashed: boolean) => v === null ? null : (
    <line x1={L} x2={W - R} y1={Y(v)} y2={Y(v)} stroke={dashed ? 'var(--warn)' : 'var(--accent)'} strokeDasharray={dashed ? '4 4' : undefined} />
  );
  const vLine = (v: number | null, dashed: boolean) => v === null ? null : (
    <line y1={T} y2={H - B} x1={X(v)} x2={X(v)} stroke={dashed ? 'var(--warn)' : 'var(--accent)'} strokeDasharray={dashed ? '4 4' : undefined} />
  );
  const sameGm = th.profit_gm_min !== null && th.thin_gm_min !== null && Math.abs(th.profit_gm_min - th.thin_gm_min) < 1e-9;
  return (
    <svg className="quad-plot" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="区块利润率与毛益率散点">
      <line x1={L} x2={L} y1={T} y2={H - B} stroke="var(--border)" />
      <line x1={L} x2={W - R} y1={H - B} y2={H - B} stroke="var(--border)" />
      {hLine(th.profit_gm_min, false)}
      {!sameGm && hLine(th.thin_gm_min, true)}
      {vLine(th.profit_net_min, false)}
      {vLine(th.thin_net_min, true)}
      <text x={W / 2} y={H - 8} textAnchor="middle" fill="var(--text2)" fontSize="12">利润率</text>
      <text x="16" y={H / 2} fill="var(--text2)" fontSize="12" transform={`rotate(-90 16 ${H / 2})`} textAnchor="middle">毛益率</text>
      <text x={L} y={H - 22} fill="var(--text3)" fontSize="11">{rateText(x0)}</text>
      <text x={W - R} y={H - 22} textAnchor="end" fill="var(--text3)" fontSize="11">{rateText(x1)}</text>
      {pts.map((b, i) => (
        <g key={b.block} className="quad-dot" onClick={() => onPick(b)} role="button" tabIndex={0}
          aria-label={`${b.block} ${b.quadrant_label} 利润率 ${rateText(b.net_rate)} 毛益率 ${rateText(b.gm_rate)}`}
          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onPick(b); } }}>
          <circle cx={X(b.net_rate as number)} cy={Y(b.gm_rate as number)} r={current === b.block ? 8 : 6}
            fill={FILL[b.quadrant] || 'var(--accent)'} stroke={current === b.block ? 'var(--text)' : 'transparent'} strokeWidth="2" />
          <text x={X(b.net_rate as number) + 10} y={Y(b.gm_rate as number) + (i % 2 === 0 ? -10 : 16)} fill="var(--text)" fontSize="12">{b.block}</text>
        </g>
      ))}
    </svg>
  );
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
  const [picked, setPicked] = useState<string | null>(null);
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
  const shown = useMemo(() => (data ? data.blocks.filter((b) => view === 'all' || b.quadrant === view) : []), [data, view]);
  const current = data?.blocks.find((b) => b.block === picked) || null;
  const canOpenWell = me.nav.some((g) => g.items.some((i) => i.id === 'overview'));

  useEffect(() => {
    const s = current?.strategy;
    setRationale(s?.rationale || '');
    setReviewDue(s?.review_due || '');
    setRisk(s?.risk_limit || '');
    setClearReason('');
  }, [current?.block, current?.strategy?.marked_at]);

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
      toast(confirm ? '已确认当前阈值。确认前的分类不再当作正式标准' : '已保存。尚未确认，地图仍按待确认参考显示');
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
      toast('已撤回确认。数值还在，但不再当作已批准标准');
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
      toast('已标记战略。基础财务分类仍然保留');
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
  const th = data?.thresholds;
  return (
    <>
      <PageHead title="四维经营地图" sub={data ? `数据范围：${data.scope} · ${data.scope_note}` : '按区块看利润率和毛益率，辅助钻机布局和资源投入'}
        right={<PeriodPicker value={period} periods={list} allowYear onChange={(p) => setRoute({ screen: 'quadmap', period: p })} />} />
      {map.error && <div className="notice">{map.error}</div>}
      {!data && !map.error && <div className="loading">加载中…</div>}
      {th && th.state !== 'confirmed' && (
        <div className="notice" role="status">
          <b>{th.state_label}。</b>
          {th.state === 'unset'
            ? '分类阈值还没设完，地图不判断盈利、微利、亏损，也不给出投入建议。'
            : (th.note || '当前数字尚未经经营管理确认。0% 在初始参考里只表示“是否覆盖成本”的会计零线，不是公司已经批准的目标利润率。')}
        </div>
      )}
      {th?.state === 'confirmed' && (
        <div className="notice info" role="status">阈值已于 {th.confirmed_at} 由 {th.confirmed_by} 确认。这是分类标准，不是自动调度或拨款。</div>
      )}

      {data && (
        <>
          <div className="seg" role="group" aria-label="四类视图">
            {VIEWS.map((v) => (
              <button key={v.id} type="button" className={view === v.id ? 'on' : ''} aria-pressed={view === v.id} onClick={() => setView(v.id)}>
                {v.label}{v.id !== 'all' && <span className="muted"> {data.counts[v.id] || 0}</span>}
              </button>
            ))}
          </div>
          <section className="card pad">
            <div className="card-head" style={{ padding: 0 }}>
              <h2>利润率 × 毛益率</h2>
              <span className="small muted">实线是盈利线，虚线是微利线。战略区块单独着色，仍保留基础分类。</span>
            </div>
            <Scatter blocks={shown} th={th!} current={picked} onPick={(b) => setPicked(b.block)} />
            <div className="quad-legend">
              {(['盈利', '微利', '亏损', '战略'] as const).map((id) => (
                <span key={id}><i style={{ background: FILL[id] }} />{id}</span>
              ))}
              {th?.thin_net_min === null && <span className="muted">微利不设利润率下限，图上没有第二条竖线。</span>}
            </div>
          </section>

          <section className="card" style={{ padding: '8px 0 12px' }}>
            <div className="quad-scroll">
              <table className="quad-table">
                <thead>
                  <tr>
                    <th>区块</th><th>视图</th><th>基础分类</th>
                    <th className="r">形象收入</th><th className="r">毛益额</th><th className="r">毛益率</th>
                    <th className="r">净利润</th><th className="r">利润率</th>
                    <th className="r">井数</th><th className="r">队伍</th><th className="r">钻机台月</th><th>钻机台数</th><th>建议</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((b) => (
                    <tr key={b.block} className={'pick' + (picked === b.block ? ' on' : '')} onClick={() => setPicked(b.block)}>
                      <td className="bold">{b.block}</td>
                      <td><span className={'tag ' + tagOf(b.quadrant)}>{b.quadrant_label}</span></td>
                      <td>{b.quadrant === '战略' ? b.financial_label : '—'}</td>
                      <td className="r num">{b.well_count ? yuan(b.revenue) : '—'}</td>
                      <td className="r num">{b.well_count ? yuan(b.gross_margin) : '—'}</td>
                      <td className="r num">{rateText(b.gm_rate)}</td>
                      <td className="r num">{b.well_count ? yuan(b.net_profit) : '—'}</td>
                      <td className="r num">{rateText(b.net_rate)}</td>
                      <td className="r num">{b.well_count}</td>
                      <td className="r">{known(b.team_count, 0)}</td>
                      <td className="r num">{known(b.rig_months)}</td>
                      <td>未知</td>
                      <td>{b.advice.title}</td>
                    </tr>
                  ))}
                  {!shown.length && <tr><td colSpan={13} className="muted">这个视图下没有区块。</td></tr>}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card pad">
            <h2>计算与分类口径</h2>
            <p className="msg">金额与经营总览、对标分析同源，按区块把井的金额加总后再算比率，不平均各井的比率。</p>
            <ul className="quad-formula">
              {Object.values(data.formula).map((line) => <li key={line}>{line}</li>)}
              {data.classes.map((c) => <li key={c.id}><b>{c.id}。</b>{c.rule}</li>)}
            </ul>
            <p className="msg">
              当前盈利线：利润率 {lineText(th?.profit_net_min)}，毛益率 {lineText(th?.profit_gm_min)}。
              微利线：利润率 {lineText(th?.thin_net_min)}，毛益率 {lineText(th?.thin_gm_min)}。
              状态：{th?.state_label}。
            </p>
            <p className="small muted">{data.disclaimer}没有独立钻机台账，钻机台数一律显示未知，不把队伍数当成台数。系统没有区块预算，投入金额显示未知。</p>
          </section>

          {data.can_maintain && th && (
            <section className="card pad">
              <h2>阈值设置</h2>
              <p className="msg">只有管理端和系统管理员能改。页面按百分数填写：0 表示 0%，5 表示 5%。保存后若未点确认，分类仍标成待确认，不会把这些数字说成已经批准的标准。</p>
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
                <button type="button" className="btn" disabled={busy} onClick={() => saveThresholds(false)}>保存，仍待确认</button>
                <button type="button" className="btn primary" disabled={busy} onClick={() => saveThresholds(true)}>确认采用为分类标准</button>
                {th.confirmed && <button type="button" className="btn" disabled={busy} onClick={withdraw}>撤回确认</button>}
              </div>
            </section>
          )}
        </>
      )}

      <Drawer open={!!current} onClose={() => setPicked(null)} wide label="区块明细" foot={undefined}
        head={current && (
          <>
            <h2 style={{ margin: 0 }}>{current.block}</h2>
            <div className="small muted">{current.quadrant_label}{current.quadrant === '战略' ? ` · 基础分类 ${current.financial_label}` : ''}</div>
          </>
        )}>
        {current && (
          <div className="quad-detail">
            {current.quadrant === '战略' && current.loss_visible && (
              <div className="notice">战略标记不掩盖亏损。基础分类仍是{current.financial_label}，不能按盈利区块追加投入。</div>
            )}
            {current.quality !== 'ok' && <div className="notice">{current.quality_label}</div>}
            <div className="kpis">
              <div className="kpi flat"><div className="k">形象收入（元）</div><div className="v"><b>{current.well_count ? yuan(current.revenue) : '—'}</b></div></div>
              <div className="kpi flat"><div className="k">变动成本（元）</div><div className="v"><b>{current.well_count ? yuan(current.variable) : '—'}</b></div></div>
              <div className="kpi flat"><div className="k">固定成本（元）</div><div className="v"><b>{current.well_count ? yuan(current.fixed) : '—'}</b></div></div>
              <div className="kpi flat"><div className="k">毛益额 / 毛益率</div><div className="v"><b>{current.well_count ? yuan(current.gross_margin) : '—'}</b><span>{rateText(current.gm_rate)}</span></div></div>
              <div className="kpi flat"><div className="k">净利润 / 利润率</div><div className="v"><b>{current.well_count ? yuan(current.net_profit) : '—'}</b><span>{rateText(current.net_rate)}</span></div></div>
            </div>
            <p className="msg"><b>{current.advice.title}。</b>{current.advice.text}</p>
            <p className="small muted">{current.advice.disclaimer}</p>
            {current.strategy && (
              <div className="notice info">
                战略依据：{current.strategy.rationale}<br />
                复评期：{current.strategy.review_due}{current.strategy.overdue ? '（已过期）' : ''}。风险约束：{current.strategy.risk_limit}<br />
                标记人 {current.strategy.marked_by}，{current.strategy.marked_at}
              </div>
            )}
            <p className="msg">
              纳入汇总的井 {current.well_count} 口。施工队伍：{current.team_count === null ? '未知' : `${current.team_count}（${current.teams.join('、') || '无'}）`}。
              钻机型号：{current.rig_types.length ? current.rig_types.join('、') : '未知'}。
              钻机台月：{known(current.rig_months)}。{current.rig_months_note}
              钻机台数：未知。{current.rig_count_note}
              投入金额：未知。{current.budget_note}
              {current.footage === null ? '' : `进尺 ${fmt(current.footage, 0)} 米。${current.footage_note}`}
            </p>
            {!!current.wells.length && (
              <table className="quad-table">
                <thead>
                  <tr><th>井号</th><th>队伍</th><th>钻机型号</th><th className="r">形象收入</th><th className="r">毛益额</th><th className="r">净利润</th><th className="r">毛益率</th><th className="r">利润率</th></tr>
                </thead>
                <tbody>
                  {current.wells.map((w) => (
                    <tr key={w.well_id} className={canOpenWell ? 'pick' : ''} onClick={() => { if (canOpenWell) setRoute({ screen: 'overview', well: w.well_id, period }); }}>
                      <td className="bold">{w.well_id}</td>
                      <td>{w.team}</td>
                      <td>{w.rig_type || '未知'}</td>
                      <td className="r num">{yuan(w.revenue)}</td>
                      <td className="r num">{yuan(w.gross_margin)}</td>
                      <td className="r num">{yuan(w.net_profit)}</td>
                      <td className="r num">{rateText(w.gm_rate)}</td>
                      <td className="r num">{rateText(w.net_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="small muted">单井比率只作对照。区块分类用的是上面的合计金额，不是这些比率的平均。</p>
            {!!current.idle_wells.length && (
              <p className="msg">未纳入本期汇总的井（没有经济事项）：{current.idle_wells.map((w) => `${w.well_id}（队伍 ${w.team}，钻机型号 ${w.rig_type || '未知'}）`).join('、')}。这些井不进两率，也不计入钻机台月。</p>
            )}
            {data?.can_maintain && (
              <div className="quad-detail">
                <h3>战略标记</h3>
                <p className="small muted">战略不是利润率算出来的。必须写依据、复评期和风险约束。不能写成无限投入。取消后回到基础财务分类。</p>
                <label className="field">战略依据
                  <textarea className="input" aria-label="战略依据" rows={3} value={rationale} onChange={(e) => setRationale(e.target.value)} />
                </label>
                <label className="field">复评期
                  <input className="input" type="date" aria-label="复评期" value={reviewDue} onChange={(e) => setReviewDue(e.target.value)} />
                </label>
                <label className="field">风险约束
                  <textarea className="input" aria-label="风险约束" rows={2} value={risk} onChange={(e) => setRisk(e.target.value)} />
                </label>
                <button type="button" className="btn primary" disabled={busy} onClick={mark}>{current.strategy ? '更新战略标记' : '标记为战略'}</button>
                {current.strategy && (
                  <>
                    <label className="field">取消原因
                      <input className="input" aria-label="取消原因" value={clearReason} onChange={(e) => setClearReason(e.target.value)} />
                    </label>
                    <button type="button" className="btn" disabled={busy} onClick={clearMark}>取消战略标记</button>
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </Drawer>
    </>
  );
}
