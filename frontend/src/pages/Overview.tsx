import { useEffect, useMemo, useRef, useState } from 'react';
import { api, download, type ExportRow, type ItemEvents, type Overview as OV, type ResultImport, type Trace, type TreeNode, type WellResult } from '../api';
import type { PageProps } from '../App';
import { Drawer, fmt, Icon, Kpis, Modal, PageHead, pct, periodLabel, useLoad, useToast, wan, type Crumb } from '../ui';

const WELL_COLS = '0.8fr 0.8fr 0.8fr 1.2fr 0.6fr 1fr 1fr 1fr 0.8fr 1fr 0.8fr 1fr 24px';
const DEPT_COLS = '0.8fr 0.8fr 0.8fr 1.2fr 0.6fr 1fr 1fr 1fr 1fr 24px';
const TREE_COLS = 'minmax(0, 2.4fr) 1fr 0.6fr 1.1fr 1.1fr 0.7fr 24px';
const EV_COLS = '1fr 0.9fr 1fr 0.6fr 1.1fr 1.2fr 1.5fr 24px';

function kpiSet(k: { revenue: number; total_cost: number; fixed: number; variable: number }, dept: boolean, full: boolean) {
  if (dept) {
    return [
      { label: '归口成本合计', value: wan(k.total_cost), unit: '万元' },
      { label: '其中：固定成本', value: wan(k.fixed), unit: '万元' },
      { label: '其中：变动成本', value: wan(k.variable), unit: '万元' },
    ];
  }
  const gm = k.revenue - k.variable;
  const net = k.revenue - k.total_cost;
  return [
    { label: '形象收入', value: wan(k.revenue), unit: '万元' },
    { label: '总成本', value: wan(k.total_cost), unit: '万元' },
    ...(full ? [{ label: '固定成本', value: wan(k.fixed), unit: '万元' }, { label: '变动成本', value: wan(k.variable), unit: '万元' }] : []),
    { label: '毛益额', value: wan(gm), unit: '万元' },
    { label: '毛益率', value: pct(gm, k.revenue), unit: '%' },
    { label: '净利润', value: wan(net), unit: '万元' },
    { label: '利润率', value: pct(net, k.revenue), unit: '%' },
  ];
}

export default function Overview({ me, route, setRoute }: PageProps) {
  const dept = me.role === 'dept';
  const periods = useLoad(() => api.get<string[]>('/api/periods'), []);
  const period = route.period || periods.data?.[0] || '';
  const navLabel = me.nav.flatMap((g) => g.items).find((i) => i.id === 'overview')?.label || '经营总览';
  const go = (well?: string, item?: string) => setRoute({ screen: 'overview', well, item, period });

  const crumbs: Crumb[] = [{ label: navLabel, go: route.well ? () => go() : undefined }];
  if (route.well) crumbs.push({ label: route.well, go: route.item ? () => go(route.well) : undefined });

  const periodPicker = (
    <label className="pill">
      {Icon.calendar()}
      <select aria-label="会计期间" value={period} onChange={(e) => setRoute({ ...route, period: e.target.value })}>
        {(periods.data || []).map((p) => <option key={p} value={p}>{periodLabel(p)}</option>)}
      </select>
    </label>
  );

  if (!period) return <div className="loading">{periods.error || '加载中…'}</div>;
  if (route.well && route.item) {
    return <Level2 well={route.well} item={route.item} period={period} crumbs={crumbs} picker={periodPicker} />;
  }
  if (route.well) {
    return <Level1 well={route.well} period={period} crumbs={crumbs} picker={periodPicker} dept={dept} open={(c) => go(route.well, c)} />;
  }
  return <Level0 period={period} title={navLabel} picker={periodPicker} dept={dept} open={(w) => go(w)}
    canImport={me.role === 'finance'} onImported={(p) => { periods.reload(); setRoute({ screen: 'overview', period: p }); }} />;
}

function Level0({ period, title, picker, dept, open, canImport, onImported }: {
  period: string; title: string; picker: React.ReactNode; dept: boolean; open: (w: string) => void;
  canImport: boolean; onImported: (period: string) => void;
}) {
  const { data, error, reload } = useLoad(() => api.get<OV>(`/api/overview?period=${period}`), [period]);
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const tpl = () => download('/api/results/template.xlsx', '单井经营情况导入模板.xlsx').catch((e: Error) => toast(e.message, true));
  return (
    <>
      <PageHead title={title} sub={data ? '数据范围：' + data.scope : undefined}
        right={
          <div className="head-actions">
            {canImport && (
              <>
                <button type="button" className="btn sm link" onClick={tpl}>下载模板</button>
                <button type="button" className="btn sm link" onClick={() => fileRef.current?.click()}>模板导入</button>
                <input ref={fileRef} type="file" accept=".xlsx" hidden onChange={(e) => { if (e.target.files?.[0]) setFile(e.target.files[0]); e.target.value = ''; }} />
              </>
            )}
            {picker}
          </div>
        } />
      <ResultImportModal file={file} onClose={() => setFile(null)} onDone={(p) => { if (p === period) reload(); else onImported(p); }} />
      {error && <div className="notice">{error}</div>}
      {data && (
        <>
          <Kpis items={kpiSet(data.sum, dept, false)} />
          <section className="card list">
            <div className="card-head">
              <h2>单井经营结果</h2>
              <div className="hint">点击任一口井，下钻到收入与成本科目</div>
            </div>
            <div className="scroll-x">
              <div style={{ minWidth: dept ? 820 : 1000 }}>
                <div className="grid-row grid-head" style={{ gridTemplateColumns: dept ? DEPT_COLS : WELL_COLS }}>
                  <span>井号</span><span>区块</span><span>井型</span><span>队伍</span><span>开次</span>
                  {dept ? (
                    <><span className="r">归口成本（万元）</span><span className="r">固定（万元）</span><span className="r">变动（万元）</span></>
                  ) : (
                    <><span className="r">形象收入（万元）</span><span className="r">总成本（万元）</span><span className="r">毛益额（万元）</span>
                      <span className="r">毛益率</span><span className="r">净利润（万元）</span><span className="r">利润率</span></>
                  )}
                  <span>备注</span><span />
                </div>
                {data.wells.length === 0 && <div className="empty">本期间没有经营数据</div>}
                {data.wells.map((w) => {
                  const gm = w.revenue - w.variable;
                  const net = w.revenue - w.total_cost;
                  return (
                    <button key={w.well_id} type="button" className="grid-row line" aria-label={'下钻到 ' + w.well_id}
                      style={{ gridTemplateColumns: dept ? DEPT_COLS : WELL_COLS }} onClick={() => open(w.well_id)}>
                      <span className="bold">{w.well_id}</span><span>{w.block}</span><span>{w.well_type}</span><span>{w.team}</span><span>{w.phase}</span>
                      {dept ? (
                        <><span className="r num bold">{wan(w.total_cost)}</span><span className="r num">{wan(w.fixed)}</span><span className="r num">{wan(w.variable)}</span></>
                      ) : (
                        <><span className="r num">{wan(w.revenue)}</span><span className="r num">{wan(w.total_cost)}</span>
                          <span className={'r num bold' + (gm < 0 ? ' neg' : '')}>{wan(gm)}</span><span className="r num muted">{pct(gm, w.revenue)}%</span>
                          <span className={'r num bold' + (net < 0 ? ' neg' : '')}>{wan(net)}</span><span className="r num muted">{pct(net, w.revenue)}%</span></>
                      )}
                      <span>{w.remark && <span className="tag">{w.remark}</span>}</span>
                      {Icon.chevron()}
                    </button>
                  );
                })}
              </div>
            </div>
          </section>
        </>
      )}
    </>
  );
}

function Level1({ well, period, crumbs, picker, dept, open }: {
  well: string; period: string; crumbs: Crumb[]; picker: React.ReactNode; dept: boolean; open: (code: string) => void;
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
              <Kpis flat items={kpiSet(data.kpi, dept, true)} />
            </div>
          </section>

          {data.show_revenue && (
            <section className="card list">
              <div className="card-head"><h2>收入</h2></div>
              {[['合同收入', data.kpi.contract], ['形象收入', data.kpi.revenue]].map(([k, v]) => (
                <div key={k as string} className="row-line" style={{ padding: '0 24px', borderTop: '1px solid var(--sep2)', borderBottom: 'none', fontSize: 14 }}>
                  <span>{k}</span><span className="bold num">{fmt(v as number)} 元</span>
                </div>
              ))}
            </section>
          )}

          <section className="card list">
            <div className="card-head center">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <h2>成本构成</h2>
                <div className="hint">总成本 {fmt(data.kpi.total_cost)} 元 = 固定成本 + 变动成本；未发生的项目同样列出</div>
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
        <span className="r num">{fmt(n.amount)}</span>
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
      <span className={'r num' + (live ? '' : '')} style={{ fontWeight: live ? 500 : 400 }}>{fmt(n.amount)}</span>
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
  const val = (v: ExportRow['v']) => (typeof v === 'number' ? fmt(v) : v ?? '');
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
            <span className="r num">{x.kind === 'section' ? '' : val(x.v)}</span>
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
              ['计价类型', data.item.calc_type || '—'], ['总表列', data.item.col || '—'], ['本期合计', fmt(data.total) + ' 元']].map(([k, v]) => (
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
                    <span className={'r num bold' + (e.amount < 0 ? ' neg' : '')}>{fmt(e.amount)}</span>
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
        <div className={'num' + (t && t.amount < 0 ? ' neg' : '')} style={{ fontSize: 30, fontWeight: 700, letterSpacing: '-0.02em' }}>{t ? fmt(t.amount) + ' 元' : ''}</div>
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

function ResultImportModal({ file, onClose, onDone }: { file: File | null; onClose: () => void; onDone: (period: string) => void }) {
  const toast = useToast();
  const [res, setRes] = useState<ResultImport | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const post = (commit: boolean) => {
    const fd = new FormData();
    fd.append('file', file!);
    return api.post<ResultImport>(`/api/results/import?commit=${commit}`, fd);
  };
  useEffect(() => {
    setRes(null);
    setErr(null);
    if (file) post(false).then(setRes).catch((e: Error) => setErr(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file]);
  const writes = res ? res.sheets.filter((x) => x.action === '导入' || x.action === '覆盖') : [];
  const commit = async () => {
    setBusy(true);
    try {
      const r = await post(true);
      toast(`导入完成：导入 ${r.count['导入'] || 0}、覆盖 ${r.count['覆盖'] || 0}、阻断 ${r.count['阻断'] || 0}；已备注“历史数据导入”`);
      onDone(writes[0].period);
      onClose();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={!!file} onClose={onClose} title="单井经营结果模板导入" wide>
      <div className="hint">
        文件：{file?.name}。每个工作表一口井一个月，A、B 两列与《单井经营情况导出模板》一致；按末级项目导入为“历史导入”经济事项，汇总行由系统重算。
        已导入的科目规则不再重复计算；同一井同一月再次导入时，金额有变化的冲销后重新导入。
      </div>
      {err && <div className="notice">{err}</div>}
      {!res && !err && <div className="loading">正在校验…</div>}
      {res && (
        <>
          <div className="chips" style={{ padding: 0 }}>
            {(['导入', '覆盖', '无变化', '阻断'] as const).map((a) => <span key={a} className="chip"><span>{a}</span> {res.count[a] || 0}</span>)}
          </div>
          <div className="scroll-x" style={{ maxHeight: 380, overflowY: 'auto', border: '1px solid var(--sep)', borderRadius: 12 }}>
            <div style={{ minWidth: 820 }}>
              <div className="grid-row grid-head" style={{ gridTemplateColumns: RES_COLS, position: 'sticky', top: 0, background: 'var(--card)', zIndex: 1 }}>
                <span>工作表</span><span>井号</span><span>期间</span><span>队号</span><span>处理</span>
                <span className="r">形象收入（万元）</span><span className="r">总成本（万元）</span><span>说明</span>
              </div>
              {res.sheets.map((x) => (
                <div key={x.sheet} className="grid-row" style={{ gridTemplateColumns: RES_COLS, minHeight: 44, fontSize: 13, borderBottom: '1px solid var(--sep2)', padding: '6px 24px' }}>
                  <span className="muted">{x.sheet}</span>
                  <span className="bold">{x.well_id || '—'}{x.new_well && <> <span className="tag">新井</span></>}</span>
                  <span className="num">{x.period || '—'}</span>
                  <span>{x.team_no || '—'}{x.new_team && <> <span className="tag">新队伍</span></>}</span>
                  <span><span className={'tag ' + RES_TAG[x.action]}>{x.action}</span></span>
                  <span className="r num">{wan(x.revenue)}</span><span className="r num">{wan(x.total_cost)}</span>
                  <span className="text3" style={{ lineHeight: '18px' }}>
                    {[...x.blocks.map((b) => '阻断：' + b), ...x.warns.map((w) => '提示：' + w)].join('；') || '—'}
                  </span>
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
