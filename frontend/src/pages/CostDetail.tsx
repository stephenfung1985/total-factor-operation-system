import { useMemo, useState } from 'react';
import { api, download, type CostMatrix, type CostOptions } from '../api';
import type { PageProps } from '../App';
import { PageHead, Seg, useLoad, useToast, yuan, Icon } from '../ui';

type Dim = 'period' | 'market' | 'block' | 'team';
const DIMS: { id: Dim; label: string }[] = [
  { id: 'period', label: '时间' }, { id: 'market', label: '市场' }, { id: 'block', label: '区块' }, { id: 'team', label: '队伍' },
];

export default function CostDetail({ me }: PageProps) {
  const toast = useToast();
  const { data: opts, error: optErr } = useLoad(() => api.get<CostOptions>('/api/cost-detail/options'), []);
  const [picked, setPicked] = useState<string[]>([]);
  const [q, setQ] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [view, setView] = useState<Dim>('period');
  const [exportDims, setExportDims] = useState<Dim[]>(['period', 'market', 'block', 'team']);
  const [withDetail, setWithDetail] = useState(true);
  const [busy, setBusy] = useState(false);

  const params = useMemo(() => {
    const p = new URLSearchParams();
    if (picked.length) p.set('items', picked.join(','));
    if (start) p.set('start', start);
    if (end) p.set('end', end);
    return p;
  }, [picked, start, end]);
  const bad = !!start && !!end && start > end;
  const { data, error } = useLoad(
    () => (bad ? Promise.resolve(null) : api.get<CostMatrix>(`/api/cost-detail?${new URLSearchParams({ by: view, ...Object.fromEntries(params) })}`)),
    [view, params.toString(), bad],
  );

  const shown = useMemo(() => (opts ? opts.items.filter((i) => !q || i.name.includes(q) || i.code.includes(q)) : []), [opts, q]);
  const toggle = (code: string) => setPicked((cur) => (cur.includes(code) ? cur.filter((c) => c !== code) : [...cur, code]));
  const toggleDim = (d: Dim) => setExportDims((cur) => (cur.includes(d) ? cur.filter((x) => x !== d) : DIMS.map((x) => x.id).filter((x) => x === d || cur.includes(x))));

  const doExport = async () => {
    if (!exportDims.length && !withDetail) {
      toast('请至少选择一个导出维度或“事项明细”', true);
      return;
    }
    setBusy(true);
    try {
      const p = new URLSearchParams(params);
      p.set('by', exportDims.join(','));
      p.set('detail', String(withDetail));
      await download(`/api/cost-detail/export.xlsx?${p}`, '费用明细.xlsx');
      toast('已导出');
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHead title="费用导出" sub={`每项成本科目按时间、市场、区块、队伍汇总，便于单项成本对比分析 · 数据范围：${opts?.scope ?? me.scope_label}`}
        right={<button type="button" className="btn primary" disabled={busy || bad} onClick={doExport}>{Icon.download()}{busy ? '导出中…' : '导出 Excel'}</button>} />
      {(optErr || error) && <div className="notice">{optErr || error}</div>}

      <section className="card pad">
        <h2>筛选与导出设置</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 16 }}>
          <label className="field">起始期间
            <select className="input" value={start} onChange={(e) => setStart(e.target.value)}>
              <option value="">不限</option>
              {opts?.periods.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="field">截止期间
            <select className="input" value={end} onChange={(e) => setEnd(e.target.value)}>
              <option value="">不限</option>
              {opts?.periods.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
        </div>
        {bad && <div className="notice">起始期间不能晚于截止期间</div>}
        <div className="field">
          成本科目 <span className="small muted">（可多选；选上级科目即包含其全部下级；不选则为全部{me.role === 'dept' ? '归口' : ''}科目）</span>
          <div className="btn-row" style={{ alignItems: 'center' }}>
            <input className="input" style={{ flex: '1 1 220px', width: 'auto' }} placeholder="按名称或编码搜索科目" value={q} onChange={(e) => setQ(e.target.value)} />
            <button type="button" className="btn sm" onClick={() => setPicked([])}>清空（{picked.length}）</button>
          </div>
          <div className="picker" role="group" aria-label="成本科目">
            {shown.map((i) => (
              <label key={i.code} className={i.is_leaf ? '' : 'group'} style={{ paddingLeft: 12 + (i.level - 1) * 16 }}>
                <input type="checkbox" checked={picked.includes(i.code)} onChange={() => toggle(i.code)} />
                <span>{i.name}</span><span className="small muted num">{i.code}</span>
              </label>
            ))}
            {opts && !shown.length && <div className="muted" style={{ padding: 12 }}>没有匹配的科目</div>}
          </div>
        </div>
        <div className="field">
          导出的工作表
          <div className="chk-row">
            {DIMS.map((d) => (
              <label key={d.id}><input type="checkbox" checked={exportDims.includes(d.id)} onChange={() => toggleDim(d.id)} />按{d.label}</label>
            ))}
            <label><input type="checkbox" checked={withDetail} onChange={(e) => setWithDetail(e.target.checked)} />事项明细（逐笔，便于 Excel 透视）</label>
          </div>
        </div>
      </section>

      <section className="card list">
        <div className="card-head center">
          <h2>预览</h2>
          <Seg label="预览维度" value={view} onChange={setView} options={DIMS.map((d) => ({ id: d.id, label: '按' + d.label }))} />
        </div>
        <div className="scroll-x" style={{ maxHeight: 560, overflow: 'auto' }}>
          {data && data.rows.length > 0 && (
            <table className="matrix">
              <thead>
                <tr>
                  <th className="name">成本科目</th>
                  {data.columns.map((c) => <th key={c.key}>{c.label}</th>)}
                  <th>合计（元）</th>
                </tr>
              </thead>
              <tbody>
                <tr className="wells">
                  <td className="name">涉及井数</td>
                  {data.columns.map((c) => <td key={c.key} className="r num">{c.wells}</td>)}
                  <td />
                </tr>
                {data.rows.map((r) => (
                  <tr key={r.code} className={r.is_leaf ? '' : 'grp'}>
                    <td className="name" style={{ paddingLeft: 12 + (r.level - 1) * 14 }}>{r.name}</td>
                    {data.columns.map((c) => (
                      <td key={c.key} className={'r num' + (r.values[c.key] ? '' : ' zero')}>{r.values[c.key] ? yuan(r.values[c.key]) : '-'}</td>
                    ))}
                    <td className="r num bold">{yuan(r.total)}</td>
                  </tr>
                ))}
                <tr className="total">
                  <td className="name">合计（叶子科目）</td>
                  {data.columns.map((c) => <td key={c.key} className="r num">{yuan(c.total)}</td>)}
                  <td className="r num">{yuan(data.grand_total)}</td>
                </tr>
              </tbody>
            </table>
          )}
          {data && data.rows.length > 0 && !data.columns.length && <div className="muted" style={{ padding: 24 }}>所选科目和期间内没有费用数据</div>}
          {data && !data.rows.length && <div className="muted" style={{ padding: 24 }}>没有可见的成本科目</div>}
          {!data && !error && !bad && <div className="loading">加载中…</div>}
        </div>
        {data && <div className="small muted" style={{ padding: '10px 24px 14px' }}>
          金额取经济事项有效金额合计（冲销已抵消），单位：元；共 {data.event_count} 笔事项。市场、区块取单井基础信息，队伍取事项发生时的施工队伍。
        </div>}
      </section>
    </>
  );
}
