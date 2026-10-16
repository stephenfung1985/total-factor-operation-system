import { useEffect, useMemo, useRef, useState } from 'react';
import { api, download, type ExportRow, type ItemEvents, type KpiSum, type Overview as OV, type OverviewSum, type OverviewWell, type ResultImport, type Trace, type TreeNode, type WellResult } from '../api';
import type { PageProps } from '../App';
import OverviewCharts, { DONE } from './OverviewCharts';
import ExecBoard from './ExecBoard';
import { Drawer, fmt, Icon, Kpis, Modal, PageHead, pct, periodLabel, PeriodPicker, useLoad, useToast, wan, yuan, type Crumb, type KpiItem } from '../ui';

const WELL_COLS = '0.8fr 0.8fr 0.8fr 1.2fr 0.6fr 1fr 1fr 1fr 0.8fr 1fr 0.8fr 1fr 24px';
const TREE_COLS = 'minmax(0, 2.4fr) 1fr 0.6fr 1.1fr 1.1fr 0.7fr 24px';
const EV_COLS = '1fr 0.9fr 1fr 0.6fr 1.1fr 1.2fr 1.5fr 24px';

/** 与上期比较：金额类取相对变化，比率类取百分点；good / bad 按“越高越好 / 越低越好”着色。 */
function delta(cur: number, prev: number | undefined, kind: 'amt' | 'rate', better: 'high' | 'low', label: string): KpiItem['delta'] {
  if (prev === undefined || (kind === 'amt' && prev === 0)) return undefined;
  const d = cur - prev;
  const text = kind === 'rate' ? `${label} ${d >= 0 ? '+' : ''}${(d * 100).toFixed(1)} pp` : `${label} ${d >= 0 ? '+' : ''}${((d / Math.abs(prev)) * 100).toFixed(1)}%`;
  if (Math.abs(d) < 1e-9) return { text, tone: 'flat' };
  return { text, tone: (d > 0) === (better === 'high') ? 'good' : 'bad' };
}

/** 首屏指标卡：规模（井数、进尺）→ 收入与毛益（形象收入、毛益额、毛益率）→ 利润（净利润、利润率、百元收入营业成本）。 */
function execKpis(s: OverviewSum, prev: OverviewSum | undefined, label: string): KpiItem[] {
  const gm = s.revenue - s.variable;
  const net = s.revenue - s.total_cost;
  const pgm = prev ? prev.revenue - prev.variable : undefined;
  const pnet = prev ? prev.revenue - prev.total_cost : undefined;
  const opt = (cur: number | undefined, p: number | undefined, kind: 'amt' | 'rate', better: 'high' | 'low') =>
    (cur === undefined || p === undefined ? undefined : delta(cur, p, kind, better, label));
  const ratio = (a: number | undefined, base: number | undefined) => (a !== undefined && base ? a / base : undefined);
  const wellDelta = prev ? s.well_count - prev.well_count : undefined;
  const per100 = (x: OverviewSum) => (x.revenue ? (x.total_cost / x.revenue) * 100 : undefined);   // 百元收入营业成本 = 总成本 ÷ 形象收入 × 100
  const c100 = per100(s);
  return [
    { label: '井数', value: String(s.well_count), unit: '口',
      delta: wellDelta === undefined ? undefined : { text: `${label} ${wellDelta > 0 ? '+' : ''}${wellDelta} 口`, tone: wellDelta === 0 ? 'flat' : wellDelta > 0 ? 'good' : 'bad' } },
    { label: '进尺', value: fmt(s.footage, 0), unit: '米', delta: opt(s.footage, prev?.footage, 'amt', 'high') },
    { label: '形象收入', value: wan(s.revenue), unit: '万元', delta: opt(s.revenue, prev?.revenue, 'amt', 'high') },
    { label: '毛益额', value: wan(gm), unit: '万元', delta: opt(gm, pgm, 'amt', 'high') },
    { label: '毛益率', value: pct(gm, s.revenue), unit: '%', delta: opt(ratio(gm, s.revenue), ratio(pgm, prev?.revenue), 'rate', 'high') },
    { label: '净利润', value: wan(net), unit: '万元', delta: opt(net, pnet, 'amt', 'high') },
    { label: '利润率', value: pct(net, s.revenue), unit: '%', delta: opt(ratio(net, s.revenue), ratio(pnet, prev?.revenue), 'rate', 'high') },
    { label: '百元收入营业成本', value: c100 === undefined ? '—' : fmt(c100, 0), unit: '元', delta: opt(c100, prev && per100(prev), 'amt', 'low') },
  ];
}

function kpiSet(k: KpiSum, o: { prev?: KpiSum; label?: string; full?: boolean } = {}): KpiItem[] {
  const { prev, label = '环比', full } = o;
  const cmp = (cur: number, p: number | undefined, better: 'high' | 'low') => delta(cur, p, 'amt', better, label);
  const gm = k.revenue - k.variable;
  const net = k.revenue - k.total_cost;
  const pgm = prev ? prev.revenue - prev.variable : undefined;
  const pnet = prev ? prev.revenue - prev.total_cost : undefined;
  const rate = (a: number, b: number | undefined, base: number, pbase: number | undefined) =>
    delta(base ? a / base : 0, b !== undefined && pbase ? b / pbase : undefined, 'rate', 'high', label);
  return [
    { label: '形象收入', value: wan(k.revenue), unit: '万元', delta: cmp(k.revenue, prev?.revenue, 'high') },
    { label: '总成本', value: wan(k.total_cost), unit: '万元', delta: cmp(k.total_cost, prev?.total_cost, 'low') },
    ...(full ? [{ label: '固定成本', value: wan(k.fixed), unit: '万元' }, { label: '变动成本', value: wan(k.variable), unit: '万元' }] : []),
    { label: '毛益额', value: wan(gm), unit: '万元', delta: cmp(gm, pgm, 'high') },
    { label: '毛益率', value: pct(gm, k.revenue), unit: '%', delta: rate(gm, pgm, k.revenue, prev?.revenue) },
    { label: '净利润', value: wan(net), unit: '万元', delta: cmp(net, pnet, 'high') },
    { label: '利润率', value: pct(net, k.revenue), unit: '%', delta: rate(net, pnet, k.revenue, prev?.revenue) },
  ];
}

export default function Overview({ me, route, setRoute }: PageProps) {
  const periods = useLoad(() => api.get<string[]>('/api/periods'), []);
  // 总览默认展示当年全年累计（当年还没有数据则取最近有数据的年份）；下钻到单井时默认最近一个月
  const list = periods.data || [];
  const thisYear = String(new Date().getFullYear());
  const defaultYear = list.some((p) => p.startsWith(thisYear)) ? thisYear : list[0]?.slice(0, 4) || '';
  const period = route.period || (route.well ? list[0] : defaultYear) || '';
  const navLabel = me.nav.flatMap((g) => g.items).find((i) => i.id === 'overview')?.label || '经营总览';
  const go = (well?: string, item?: string, p?: string) => setRoute({ screen: 'overview', well, item, period: p ?? period });

  const crumbs: Crumb[] = [{ label: navLabel, go: route.well ? () => go() : undefined }];
  if (route.well) crumbs.push({ label: route.well, go: route.item ? () => go(route.well) : undefined });

  const pickerFor = (allowYear: boolean) => (
    <PeriodPicker value={period} periods={periods.data || []} allowYear={allowYear} onChange={(p) => setRoute({ ...route, period: p })} />
  );
  const periodPicker = pickerFor(false);

  if (!period) return <div className="loading">{periods.error || '加载中…'}</div>;
  if (route.well && route.item) {
    return <Level2 well={route.well} item={route.item} period={period} crumbs={crumbs} picker={periodPicker} />;
  }
  if (route.well) {
    return <Level1 well={route.well} period={period} crumbs={crumbs} picker={periodPicker} open={(c) => go(route.well, c)} />;
  }
  return <Level0 period={period} title={navLabel} picker={pickerFor(true)} open={(w, p) => go(w, undefined, p)}
    canImport={me.role === 'finance'} onImported={(p) => { periods.reload(); setRoute({ screen: 'overview', period: p }); }} />;
}

type Sort = 'net' | 'revenue' | 'cost' | 'id';
const SORTS: { id: Sort; label: string }[] = [{ id: 'net', label: '净利润（高→低）' }, { id: 'revenue', label: '形象收入' }, { id: 'cost', label: '总成本' }, { id: 'id', label: '井号' }];

function Level0({ period, title, picker, open, canImport, onImported }: {
  period: string; title: string; picker: React.ReactNode; open: (w: string, period?: string) => void;
  canImport: boolean; onImported: (period: string) => void;
}) {
  const { data, error, reload } = useLoad(() => api.get<OV>(`/api/overview?period=${period}`), [period]);
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [del, setDel] = useState<string | null>(null);
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<Sort>('net');
  const tpl = () => download('/api/results/template.xlsx', '单井经营情况批量导入模板.xlsx').catch((e: Error) => toast(e.message, true));
  const rows = useMemo(() => {
    if (!data) return [];
    const kw = q.trim().toLowerCase();
    const list = data.wells.filter((w) => (!kw || [w.well_id, w.block, w.team, w.market, w.well_type].some((x) => (x || '').toLowerCase().includes(kw))));
    const key = (w: OverviewWell) => (sort === 'net' ? w.revenue - w.total_cost : sort === 'revenue' ? w.revenue : w.total_cost);
    return [...list].sort((a, b) => (sort === 'id' ? a.well_id.localeCompare(b.well_id) : key(b) - key(a)));
  }, [data, q, sort]);
  const isYear = period.length === 4;
  return (
    <>
      <PageHead title={title} sub={data ? `数据范围：${data.scope}${isYear ? ` · ${period} 年累计（${data.months.length} 个月）` : ''}` : undefined}
        right={
          <div className="head-actions">
            {canImport && (
              <>
                <button type="button" className="btn sm link" onClick={tpl}>下载模板</button>
                <button type="button" className="btn sm link" onClick={() => fileRef.current?.click()} title="工作表里横向放多口井，可一次选多个文件">批量导入</button>
                <input ref={fileRef} type="file" accept=".xlsx" multiple hidden onChange={(e) => { if (e.target.files?.length) setFiles([...e.target.files]); e.target.value = ''; }} />
              </>
            )}
            {picker}
          </div>
        } />
      <ResultImportModal files={files} onClose={() => setFiles([])} onDone={(p) => { if (p.slice(0, isYear ? 4 : 7) === period) reload(); else onImported(p); }} />
      <DeleteWellModal well={del} onClose={() => setDel(null)} onDone={() => { setDel(null); reload(); }} />
      {error && <div className="notice">{error}</div>}
      {data && (
        <>
          <Kpis board items={execKpis(data.sum, data.prev?.sum, isYear ? '同比' : '环比')} />
          <ExecBoard data={data} />
          <OverviewCharts data={data} />
          <div className="ov-bar" role="group" aria-label="井列表筛选">
            <input className="input ov-search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜索井号、区块、队伍" aria-label="搜索井" />
            <label className="pill">排序
              <select aria-label="排序" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
                {SORTS.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
              </select>
            </label>
            <span className="small muted ov-count">共 {rows.length} / {data.wells.length} 口</span>
          </div>
          {[{ title: '正钻井', rows: rows.filter((w) => !DONE.includes(w.status)) },
            { title: '完成井', rows: rows.filter((w) => DONE.includes(w.status)) }].map((g) => (
            <WellGroup key={g.title} title={g.title} rows={g.rows} open={open} onDelete={canImport ? setDel : undefined} />
          ))}
        </>
      )}
    </>
  );
}

function Level1({ well, period, crumbs, picker, open }: {
  well: string; period: string; crumbs: Crumb[]; picker: React.ReactNode; open: (code: string) => void;
}) {
  const { data, error } = useLoad(() => api.get<WellResult>(`/api/wells/${well}/result?period=${period}`), [well, period]);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [exportOpen, setExportOpen] = useState(false);

  const rows = useMemo(() => {
    if (!data) return [];
    const hidden: Record<string, boolean> = {};
    const out: TreeNode[] = [];
    for (const n of data.tree) {
      if (n.parent && (hidden[n.parent] || collapsed[n.parent])) {
        hidden[n.code] = true;
        continue;
      }
      out.push(n);
    }
    return out;
  }, [data, collapsed]);

  const toggle = (c: string) => setCollapsed((s) => {
    const n = { ...s };
    if (n[c]) delete n[c];
    else n[c] = true;
    return n;
  });
  const collapseTo2 = () => {
    const c: Record<string, boolean> = {};
    data?.tree.forEach((n) => { if (!n.is_leaf && n.level >= 2) c[n.code] = true; });
    setCollapsed(c);
  };

  return (
    <>
      <PageHead crumbs={crumbs} title={well + ' 单井经营'} sub="成本按固定成本、变动成本分级展开，点击末级项目查看明细" right={picker} />
      {error && <div className="notice">{error}</div>}
      {data?.info.remark && (
        <div className="notice info">
          备注：{data.info.remark}。本期标“历史导入”的金额来自{data.info.import_sources?.length ? data.info.import_sources.join('；') : '期初导入'}，未由规则计算。
        </div>
      )}
      {data && (
        <>
          <section className="card" style={{ padding: '22px 24px', display: 'flex', flexDirection: 'column', gap: 20 }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
              <div className="kv-grid" style={{ flex: '1 1 560px', gap: '12px 20px' }}>
                {[['市场分布', data.info.market], ['队号', data.info.team], ['队长', data.info.captain], ['钻机类型', data.info.rig_type],
                  ['甲方单位', data.info.client], ['区块', data.info.block], ['井别', data.info.well_category], ['井型', data.info.well_type],
                  ['开次', data.info.phase], ['设计井深', fmt(data.info.design_depth, 0) + ' 米'], ['实际井深', fmt(data.info.actual_depth, 0) + ' 米']]
                  .map(([k, v]) => <div key={k} className="kv"><span className="k">{k}</span><span style={{ fontSize: 14 }}>{v}</span></div>)}
              </div>
              {data.can_export && (
                <button type="button" className="btn primary" onClick={() => setExportOpen(true)}>{Icon.download()}导出单井经营情况</button>
              )}
            </div>
            <div style={{ borderTop: '1px solid var(--sep2)', paddingTop: 18 }}>
              <Kpis flat items={kpiSet(data.kpi, { full: true })} />
            </div>
          </section>

          {data.show_revenue && (
            <section className="card list">
              <div className="card-head"><h2>收入</h2></div>
              {[['合同收入', data.kpi.contract], ['形象收入', data.kpi.revenue]].map(([k, v]) => (
                <div key={k as string} className="row-line" style={{ padding: '0 24px', borderTop: '1px solid var(--sep2)', borderBottom: 'none', fontSize: 14 }}>
                  <span>{k}</span><span className="bold num">{yuan(v as number)} 元</span>
                </div>
              ))}
            </section>
          )}

          <section className="card list">
            <div className="card-head center">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <h2>成本构成</h2>
                <div className="hint">总成本 {yuan(data.kpi.total_cost)} 元 = 固定成本 + 变动成本；未发生的项目同样列出</div>
              </div>
              <div className="seg">
                <button type="button" className="on" onClick={() => setCollapsed({})}>全部展开</button>
                <button type="button" onClick={collapseTo2}>收起到二级</button>
              </div>
            </div>
            <div className="scroll-x">
              <div style={{ minWidth: 900 }}>
                <div className="grid-row grid-head" style={{ gridTemplateColumns: TREE_COLS }}>
                  <span>项目</span><span>编码</span><span>归口</span><span>计价类型</span><span className="r">金额（元）</span><span className="r">占总成本</span><span />
                </div>
                {rows.map((n) => <TreeRow key={n.code} n={n} total={data.kpi.total_cost} folded={!!collapsed[n.code]} toggle={toggle} open={open} />)}
              </div>
            </div>
          </section>
          <ExportDrawer open={exportOpen} onClose={() => setExportOpen(false)} well={well} period={period} />
        </>
      )}
    </>
  );
}

function TreeRow({ n, total, folded, toggle, open }: { n: TreeNode; total: number; folded: boolean; toggle: (c: string) => void; open: (c: string) => void }) {
  const indent = { paddingLeft: (n.level - 1) * 20 };
  const share = total > 0 ? ((n.amount / total) * 100).toFixed(1) + '%' : '—';
  if (!n.is_leaf) {
    const top = n.level === 1;
    return (
      <button type="button" className={'grid-row ' + (top ? 'tree-top' : 'tree-sub')} style={{ gridTemplateColumns: TREE_COLS }}
        aria-expanded={!folded} onClick={() => toggle(n.code)}>
        <span className="tree-name" style={indent}>{folded ? Icon.right(top ? 14 : 12) : Icon.down(top ? 14 : 12)}{n.name}</span>
        <span className="muted" style={{ fontWeight: 400 }}>{n.code}</span>
        <span style={{ fontWeight: 400 }}>{top ? '' : n.dept}</span>
        <span className="text3" style={{ fontWeight: 400 }}>{top ? '' : n.calc_type}</span>
        <span className="r num">{yuan(n.amount)}</span>
        <span className="r num muted" style={{ fontWeight: 400 }}>{share}</span><span />
      </button>
    );
  }
  const live = Math.abs(n.amount) > 0.005;
  const ruled = n.origins.some((o) => o === '规则计算' || o === '比例计提');
  const body = (
    <>
      <span style={{ paddingLeft: 20 }}><span className="tree-name" style={indent}>{n.name}{ruled && <span className="tag info">规则</span>}</span></span>
      <span className="muted">{n.code}</span><span>{n.dept}</span><span className={live ? 'text3' : ''}>{n.calc_type}</span>
      <span className={'r num' + (live ? '' : '')} style={{ fontWeight: live ? 500 : 400 }}>{yuan(n.amount)}</span>
      <span className="r num muted" style={live ? undefined : { fontSize: 12 }}>{live ? share : '未发生'}</span>
    </>
  );
  if (!live && n.origins.length === 0) {
    return <div className="grid-row tree-leaf tree-zero" style={{ gridTemplateColumns: TREE_COLS, fontSize: 14 }}>{body}<span /></div>;
  }
  return (
    <button type="button" className="grid-row tree-leaf" style={{ gridTemplateColumns: TREE_COLS }} aria-label={'下钻到 ' + n.name} onClick={() => open(n.code)}>
      {body}{Icon.chevron()}
    </button>
  );
}

function ExportDrawer({ open, onClose, well, period }: { open: boolean; onClose: () => void; well: string; period: string }) {
  const toast = useToast();
  const [rows, setRows] = useState<ExportRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (open && !rows) api.get<ExportRow[]>(`/api/wells/${well}/export/preview?period=${period}`).then(setRows).catch((e: Error) => toast(e.message, true));
  }, [open, rows, well, period, toast]);
  const dl = async () => {
    setBusy(true);
    try {
      await download(`/api/wells/${well}/export.xlsx?period=${period}`, `单井经营情况_${well}_${period}.xlsx`);
      toast('已按《单井经营情况导出模板》生成 Excel');
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const val = (x: ExportRow) => (typeof x.v === 'number' ? (x.k.endsWith('（米）') ? fmt(x.v) : yuan(x.v)) : x.v ?? '');
  return (
    <Drawer open={open} onClose={onClose} wide label="导出预览"
      head={<>
        <div className="hint">导出预览 · 单井经营情况</div>
        <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: '-0.01em' }}>{well} · {periodLabel(period)}</div>
        <div className="small muted">共 {rows?.length ?? '…'} 行，行序与《单井经营情况导出模板》一致</div>
      </>}
      foot={<>
        <button type="button" className="btn primary" style={{ flex: '1 1 200px' }} disabled={busy} onClick={dl}>{busy ? '生成中…' : '下载 Excel（按导出模板）'}</button>
        <button type="button" className="btn" style={{ flex: '0 1 120px' }} onClick={onClose}>关闭</button>
      </>}>
      <div style={{ margin: '-22px -28px -40px' }}>
        <div className="exp-row small muted" style={{ borderBottom: 'none' }}><span>行</span><span>项目（A 列）</span><span className="r">金额（B 列）</span></div>
        {(rows || []).map((x, i) => (
          <div key={i} className={'exp-row ' + x.kind}>
            <span className="no">{i + 1}</span>
            <span style={{ paddingLeft: (x.level - 1) * 14 }}>{x.k}</span>
            <span className="r num">{x.kind === 'section' ? '' : val(x)}</span>
          </div>
        ))}
      </div>
    </Drawer>
  );
}

function Level2({ well, item, period, crumbs, picker }: { well: string; item: string; period: string; crumbs: Crumb[]; picker: React.ReactNode }) {
  const { data, error } = useLoad(() => api.get<ItemEvents>(`/api/wells/${well}/items/${item}/events?period=${period}`), [well, item, period]);
  const [trace, setTrace] = useState<number | null>(null);
  const name = data ? data.item.name.replace(/^（\d+）/, '').replace(/^\d+\./, '') : item;
  return (
    <>
      <PageHead crumbs={[...crumbs, { label: name }]} title={name} sub="规则计算、比例计提与历史导入分别标识来源；冲销记录保留在明细中" right={picker} />
      {error && <div className="notice">{error}</div>}
      {data && (
        <>
          <section className="card chips">
            {[['科目', data.item.code + ' ' + name], ['成本性态', data.item.behavior === '固定' ? '固定成本' : '变动成本'], ['归口', data.item.dept || '—'],
              ['计价类型', data.item.calc_type || '—'], ['总表列', data.item.col || '—'], ['本期合计', yuan(data.total) + ' 元']].map(([k, v]) => (
              <span key={k} className="chip"><span>{k}</span> {v}</span>
            ))}
          </section>
          <section className="card list">
            <div className="card-head">
              <h2>经济事项</h2>
              <div className="hint">点击任一笔，查看计算过程、规则、条款原文与来源事实</div>
            </div>
            <div className="scroll-x">
              <div style={{ minWidth: 860 }}>
                <div className="grid-row grid-head" style={{ gridTemplateColumns: EV_COLS }}>
                  <span>归属日期</span><span className="r">数量</span><span className="r">单价（元）</span><span className="r">系数</span>
                  <span className="r">金额（元）</span><span>规则版本</span><span>来源</span><span />
                </div>
                {data.events.length === 0 && <div className="empty">本期间没有经济事项</div>}
                {data.events.map((e) => (
                  <button key={e.event_id} type="button" className="grid-row line" style={{ gridTemplateColumns: EV_COLS, opacity: e.status === '有效' ? 1 : 0.75 }}
                    aria-label={`查看 ${e.date} 这笔事项的溯源`} onClick={() => setTrace(e.event_id)}>
                    <span className="num">{e.date}{e.status !== '有效' && <> <span className={'tag ' + (e.reversal_of ? 'bad' : 'warn')}>{e.reversal_of ? '冲销' : '已冲销'}</span></>}</span>
                    <span className="r num">{e.qty}</span><span className="r num">{e.price}</span><span className="r num">{e.factor}</span>
                    <span className={'r num bold' + (e.amount < 0 ? ' neg' : '')}>{yuan(e.amount)}</span>
                    <span className="text3">{e.rule}</span><span className="text3">{e.source}</span>
                    {Icon.chevron()}
                  </button>
                ))}
              </div>
            </div>
          </section>
        </>
      )}
      <TraceDrawer id={trace} onClose={() => setTrace(null)} />
    </>
  );
}

function TraceDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  const { data } = useLoad(() => (id === null ? Promise.resolve(null) : api.get<Trace>(`/api/events/${id}/trace`)), [id]);
  const t = data && data.event_id === id ? data : null;
  return (
    <Drawer open={id !== null} onClose={onClose} label="事项溯源"
      head={<>
        <div className="hint">{t?.title ?? '加载中…'}</div>
        <div className={'num' + (t && t.amount < 0 ? ' neg' : '')} style={{ fontSize: 30, fontWeight: 700, letterSpacing: '-0.02em' }}>{t ? yuan(t.amount) + ' 元' : ''}</div>
      </>}>
      {t && (
        <>
          {t.note && <div className="notice">{t.note}</div>}
          <section className="sec"><div className="sec-label">计算过程</div><div className="formula">{t.formula}</div></section>
          <section className="sec">
            <div className="sec-label">适用规则</div>
            {t.rule_lines.map((l) => <div key={l.k} className="kvline"><span>{l.k}</span><span>{l.v}</span></div>)}
          </section>
          {t.clause && (
            <section className="sec">
              <div className="sec-label">条款原文 · {t.clause.ref}</div>
              <p className="quote" style={{ fontSize: 14, lineHeight: '23px', padding: '14px 16px' }}>{t.clause.pre}<mark>{t.clause.hit}</mark>{t.clause.post}</p>
            </section>
          )}
          <section className="sec">
            <div className="sec-label">{t.src_title}</div>
            {t.src_lines.map((l) => <div key={l.k} className="kvline"><span>{l.k}</span><span>{l.v}</span></div>)}
          </section>
        </>
      )}
    </Drawer>
  );
}

const RES_COLS = '1fr 0.9fr 0.8fr 0.9fr 0.7fr 0.9fr 0.9fr 2.6fr';
const RES_TAG: Record<string, string> = { 导入: 'good', 覆盖: 'info', 无变化: '', 阻断: 'bad' };

function ResultImportModal({ files, onClose, onDone }: { files: File[]; onClose: () => void; onDone: (period: string) => void }) {
  const toast = useToast();
  const [res, setRes] = useState<ResultImport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const post = (commit: boolean) => {
    const fd = new FormData();
    files.forEach((f) => fd.append('file', f));
    return api.post<ResultImport>(`/api/results/import?commit=${commit}`, fd);
  };
  useEffect(() => {
    setRes(null);
    setErr(null);
    if (files.length) post(false).then(setRes).catch((e: Error) => setErr(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [files]);
  const writes = res ? res.sheets.filter((x) => x.action === '导入' || x.action === '覆盖') : [];
  const commit = async () => {
    setBusy(true);
    try {
      const r = await post(true);
      toast(`导入完成：导入 ${r.count['导入'] || 0}、覆盖 ${r.count['覆盖'] || 0}、阻断 ${r.count['阻断'] || 0}；未经基层队登记的井已备注“历史数据井”`);
      onDone(writes[0].period);
      onClose();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={files.length > 0} onClose={onClose} title="单井经营结果批量导入" wide>
      <div className="hint">
        文件：{files.map((f) => f.name).join('、')}。A 列为项目，B 列起每一列一口井一个月（行序与《单井经营情况导出模板》一致），可一次选多个文件；按末级项目导入为“历史导入”经济事项，汇总行由系统重算。
        未经基层钻井队登记的井备注“历史数据井”；同一井同一月再次导入时，只冲销金额有变化的科目并重新导入，没变的科目保留。
      </div>
      {err && <div className="notice">{err}</div>}
      {!res && !err && <div className="loading">正在校验…</div>}
      {res && (
        <>
          <div className="chips" style={{ padding: 0 }}>
            {(['导入', '覆盖', '无变化', '阻断'] as const).map((a) => <span key={a} className="chip"><span>{a}</span> {res.count[a] || 0}</span>)}
          </div>
          <div className="scroll-x" style={{ maxHeight: 380, overflowY: 'auto', border: '1px solid var(--sep)', borderRadius: 12 }}>
            <div className="nowrap" style={{ minWidth: 820 }}>
              <div className="grid-row grid-head" style={{ gridTemplateColumns: RES_COLS, position: 'sticky', top: 0, background: 'var(--card)', zIndex: 1 }}>
                <span>文件 · 工作表</span><span>井号</span><span>期间</span><span>队号</span><span>处理</span>
                <span className="r">形象收入（万元）</span><span className="r">总成本（万元）</span><span>说明</span>
              </div>
              {res.sheets.map((x) => (
                <div key={x.file + x.sheet} className="grid-row" style={{ gridTemplateColumns: RES_COLS, minHeight: 44, fontSize: 13, borderBottom: '1px solid var(--sep2)', padding: '6px 24px' }}>
                  <span className="muted" title={x.file + ' · ' + x.sheet}>{files.length > 1 ? x.file + ' · ' : ''}{x.sheet}</span>
                  <span className="bold">{x.well_id || '—'}{x.new_well && <> <span className="tag">新井</span></>}</span>
                  <span className="num">{x.period || '—'}</span>
                  <span>{x.team_no || '—'}{x.new_team && <> <span className="tag">新队伍</span></>}</span>
                  <span><span className={'tag ' + RES_TAG[x.action]}>{x.action}</span></span>
                  <span className="r num">{wan(x.revenue)}</span><span className="r num">{wan(x.total_cost)}</span>
                  {(() => {
                    const ch = x.changes?.length ? [`变化 ${x.changes.length} 项：` + x.changes.slice(0, 3).map((c) => `${c.item} ${yuan(c.old)}→${yuan(c.new)}`).join('、') + (x.changes.length > 3 ? ' 等' : '')] : [];
                    const text = [...x.blocks.map((b) => '阻断：' + b), ...x.warns.map((w) => '提示：' + w), ...ch].join('；') || '—';
                    return <span className="text3" title={text}>{text}</span>;
                  })()}
                </div>
              ))}
            </div>
          </div>
          <div className="btn-row">
            <button type="button" className="btn" onClick={onClose}>取消</button>
            <button type="button" className="btn primary" disabled={busy || writes.length === 0} onClick={commit}>
              {busy ? '导入中…' : writes.length ? `确认导入（${writes.length} 口井·月）` : '没有需要导入的工作表'}
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}

const PAGE = 10;

function WellGroup({ title, rows, open, onDelete }: { title: string; rows: OverviewWell[]; open: (w: string, period?: string) => void; onDelete?: (w: string) => void }) {
  const cols = WELL_COLS + (onDelete ? ' 32px' : '');
  const [all, setAll] = useState(false);
  const shown = all ? rows : rows.slice(0, PAGE);
  const rev = rows.reduce((a, w) => a + w.revenue, 0);
  const cost = rows.reduce((a, w) => a + w.total_cost, 0);
  if (rows.length === 0) {
    return <section className="card pad ov-none"><h2>{title}<span className="muted" style={{ fontWeight: 400, marginLeft: 8 }}>0 口</span></h2><span className="small muted">本期间没有{title}经营数据</span></section>;
  }
  return (
    <>
      <section className="card list">
        <div className="card-head">
          <h2>{title}<span className="muted" style={{ fontWeight: 400, marginLeft: 8 }}>{rows.length} 口</span></h2>
          <div className="hint ov-sum">{`收入 ${wan(rev)} · 成本 ${wan(cost)} · 净利润 ${wan(rev - cost)}`} · 单位：万元 · 点击下钻</div>
        </div>
        <div className="scroll-x">
          <div className="nowrap" style={{ minWidth: 1000 }}>
            <div className="grid-row grid-head" style={{ gridTemplateColumns: cols }}>
              <span>井号</span><span>区块</span><span>井型</span><span>队伍</span><span>开次</span>
              <span className="r">形象收入</span><span className="r">总成本</span><span className="r">毛益额</span>
              <span className="r">毛益率</span><span className="r">净利润</span><span className="r">利润率</span>
              <span>备注</span><span />{onDelete && <span />}
            </div>
            {rows.length === 0 && <div className="empty">本期间没有{title}经营数据</div>}
            {shown.map((w) => {
              const gm = w.revenue - w.variable;
              const net = w.revenue - w.total_cost;
              return (
                <div key={w.well_id} role="button" tabIndex={0} className="grid-row line" aria-label={'下钻到 ' + w.well_id}
                  style={{ gridTemplateColumns: cols }} onClick={() => open(w.well_id, w.last_period)}
                  onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); open(w.well_id, w.last_period); } }}>
                  <span className="bold">{w.well_id}</span><span>{w.block}</span><span>{w.well_type}</span><span>{w.team}</span><span>{w.phase}</span>
                  <span className="r num">{wan(w.revenue)}</span><span className="r num">{wan(w.total_cost)}</span>
                  <span className={'r num bold' + (gm < 0 ? ' neg' : '')}>{wan(gm)}</span><span className="r num muted">{pct(gm, w.revenue)}%</span>
                  <span className={'r num bold' + (net < 0 ? ' neg' : '')}>{wan(net)}</span><span className="r num muted">{pct(net, w.revenue)}%</span>
                  <span>{w.remark && <span className="tag">{w.remark}</span>}</span>
                  {Icon.chevron()}
                  {onDelete && (
                    <button type="button" className="row-x del" aria-label={'删除 ' + w.well_id} title="删除这口井"
                      onClick={(e) => { e.stopPropagation(); onDelete(w.well_id); }}>{Icon.trash()}</button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
        {rows.length > PAGE && (
          <button type="button" className="more" aria-expanded={all} onClick={() => setAll(!all)}>{all ? '收起' : `显示全部 ${rows.length} 口`}{Icon.down(12)}</button>
        )}
      </section>
    </>
  );
}

/** 手工删除一口井：须输入井号确认；经济事项只冲不改，删除即冲销该井全部事项并从各页面隐藏。 */
function DeleteWellModal({ well, onClose, onDone }: { well: string | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => setText(''), [well]);
  const go = async () => {
    setBusy(true);
    try {
      const r = await api.del<{ reversed: number }>(`/api/wells/${encodeURIComponent(well!)}`);
      toast(`已删除 ${well}，冲销 ${r.reversed} 笔经济事项`);
      onDone();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={!!well} onClose={onClose} title={`删除 ${well ?? ''}`}>
      <div className="small" style={{ lineHeight: '22px' }}>
        删除后这口井会冲销全部经济事项，并从经营总览、对标分析、费用导出和填报列表中隐藏；冲销记录保留，可在历史记录中追溯。
        未经基层钻井队登记的历史数据井，再次导入即可恢复。
      </div>
      <label className="field"><span style={{ whiteSpace: 'nowrap' }}>输入井号 <b>{well}</b> 确认</span>
        <input className="input" value={text} onChange={(e) => setText(e.target.value)} autoFocus placeholder={well ?? ''}
          onKeyDown={(e) => e.key === 'Enter' && text.trim() === well && !busy && go()} />
      </label>
      <div className="btn-row">
        <button type="button" className="btn" onClick={onClose}>取消</button>
        <button type="button" className="btn danger" disabled={busy || text.trim() !== well} onClick={go}>{busy ? '删除中…' : '删除'}</button>
      </div>
    </Modal>
  );
}
