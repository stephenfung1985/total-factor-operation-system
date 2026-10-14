import { useEffect, useState } from 'react';
import { api, type Check, type InfoDerived, type InfoFields, type InfoMine, type InfoOptions, type InfoRecord } from '../api';
import type { PageProps } from '../App';
import { fmt, Icon, PageHead, useLoad, useToast } from '../ui';

const FIELD_KEYS: (keyof InfoFields)[] = ['well_id', 'period', 'market', 'captain', 'rig_type', 'client', 'block', 'well_category',
  'well_type', 'spud_sections', 'design_depth', 'milestones', 'staff'];
const STATUS_TAG: Record<string, string> = { 草稿: '', 已提交: 'good', 新建: 'info' };
const WELL_TAG: Record<string, string> = { 计划: '', 正钻: 'info', 完钻: 'warn', 完井: 'good' };

const wellLabel = (m: InfoMine) =>
  `${m.well_id} · ${m.well_status} · ${m.returned ? '退回待修改' : m.status === '草稿' ? `v${m.version} 草稿` : `v${m.version} 已提交`}`;
const toInput = (v: string | null | undefined) => (v ? v.replace(' ', 'T') : '');
const fromInput = (s: string) => (s ? s.replace('T', ' ') : null);
const pick = (r: InfoRecord): InfoFields => Object.fromEntries(FIELD_KEYS.map((k) => [k, structuredClone(r[k])])) as unknown as InfoFields;
const days = (x: number | null | undefined) => (x ? fmt(x, 2) : '0.00');

export default function WellReg(_: PageProps) {
  const toast = useToast();
  const opts = useLoad(() => api.get<InfoOptions>('/api/wellinfo/options'), []);
  const mine = useLoad(() => api.get<InfoMine[]>('/api/wellinfo/mine'), []);
  const [sel, setSel] = useState<string | null>(null);   // 井号；'' 表示登记新井
  const [rec, setRec] = useState<InfoRecord | null>(null);
  const [form, setForm] = useState<InfoFields | null>(null);
  const [checks, setChecks] = useState<Check[]>([]);
  const [derived, setDerived] = useState<InfoDerived | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (sel !== null || !mine.data) return;
    setSel(mine.data[0]?.well_id ?? '');
  }, [mine.data, sel]);

  const show = (r: InfoRecord) => {
    setRec(r);
    setForm(pick(r));
    setChecks(r.checks);
    setDerived(r.derived);
  };
  useEffect(() => {
    if (sel === null) return;
    api.get<InfoRecord>(`/api/wellinfo/current${sel ? '?well_id=' + encodeURIComponent(sel) : ''}`).then(show).catch((e: Error) => toast(e.message, true));
  }, [sel, toast]);

  const editable = !!rec && (rec.status === '新建' || rec.status === '草稿');
  const idEditable = !!rec && (rec.status === '新建' || (rec.status === '草稿' && rec.version === 1 && !rec.registered_at));

  // 实时校验与自动计算（与提交时同一套规则）
  useEffect(() => {
    if (!form || !editable) return;
    const t = window.setTimeout(() => {
      api.post<{ checks: Check[]; derived: InfoDerived }>('/api/wellinfo/check', { ...form, report_id: rec?.report_id })
        .then((r) => { setChecks(r.checks); setDerived(r.derived); }).catch(() => {});
    }, 250);
    return () => window.clearTimeout(t);
  }, [form, editable, rec?.report_id]);

  const set = <K extends keyof InfoFields>(k: K, v: InfoFields[K]) => setForm((f) => f && { ...f, [k]: v });
  const setMs = (k: string, v: string | null) => setForm((f) => f && { ...f, milestones: { ...f.milestones, [k]: v } });
  const setSections = (n: number) => setForm((f) => {
    if (!f) return f;
    const ms = { ...f.milestones };
    for (let k = n + 1; k <= 5; k++) { ms[`开钻${k}`] = null; ms[`完钻${k}`] = null; }
    return { ...f, spud_sections: n, milestones: ms };
  });

  const run = async (fn: () => Promise<InfoRecord>, ok: (r: InfoRecord) => string) => {
    setBusy(true);
    try {
      const r = await fn();
      show(r);
      if (r.well_id !== sel) setSel(r.well_id);
      toast(ok(r));
      mine.reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const save = () => api.post<InfoRecord>('/api/wellinfo', { ...form, report_id: rec?.report_id });
  const onSave = () => run(save, () => '草稿已保存');
  const onSubmit = () => run(async () => {
    const r = await save();
    return api.post<InfoRecord>(`/api/wellinfo/${r.report_id}/submit`);
  }, (r) => '已提交，已汇总到单井基础信息' + (r.batch ? `；开次等变化触发重算：新增 ${r.batch.created} 笔、冲销 ${r.batch.reversed} 笔` : ''));
  const onRevise = () => run(() => api.post<InfoRecord>(`/api/wellinfo/${rec!.report_id}/revise`), (r) => `已生成版本 ${r.version} 草稿，补充节点后提交`);

  const o = opts.data;
  const returned = (mine.data || []).filter((m) => m.returned);
  const blocks = checks.filter((c) => c.level === '阻断');
  const warns = checks.filter((c) => c.level !== '阻断');
  const d = derived;
  const thisYear = new Date().getFullYear();
  const [py, pm] = (form?.period || '-').split('-');

  return (
    <>
      <PageHead title="基础信息" sub="按井登记，随施工进度更新节点与人员；提交后自动汇总到经营总览的单井基础信息"
        right={
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <label className="pill">井号
              <select aria-label="选择井号" value={sel ?? ''} onChange={(e) => setSel(e.target.value)}>
                <option value="">＋ 登记新井</option>
                {mine.data?.map((m) => <option key={m.well_id} value={m.well_id}>{wellLabel(m)}</option>)}
              </select>
            </label>
            {sel !== '' && <button type="button" className="btn primary sm" onClick={() => setSel('')}>登记新井</button>}
          </div>
        } />
      {returned.length > 0 && (
        <div className="notice">企划财务部退回 {returned.length} 口井待修改：{returned.map((m) => m.well_id).join('、')}。在“井号”下拉中选择后修改并重新提交。</div>
      )}

      {rec && form && o && (
        <div className="split">
          <div className="grow">
            {/* 一、基本信息：文本与选择 */}
            <section className="card pad">
              <div className="sec-head">
                <h3>一、基本信息<small>文本与选择</small></h3>
                <span className="small muted">队号取自当前账号；井号提交后不可修改</span>
              </div>
              <div className="form-grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(170px, 1fr))' }}>
                <label className="field">井号 *<input className="input" value={form.well_id} disabled={!idEditable} placeholder="唯一身份识别码"
                  onChange={(e) => set('well_id', e.target.value.trim())} /></label>
                <div className="field"><span>归属年月 *</span>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <select className="input" disabled={!editable} value={py} onChange={(e) => set('period', `${e.target.value}-${pm}`)}>
                      {[thisYear - 1, thisYear, thisYear + 1].map((y) => <option key={y} value={y}>{y} 年</option>)}
                    </select>
                    <select className="input" disabled={!editable} value={pm} onChange={(e) => set('period', `${py}-${e.target.value}`)}>
                      {Array.from({ length: 12 }, (_, i) => String(i + 1).padStart(2, '0')).map((m) => <option key={m} value={m}>{Number(m)} 月</option>)}
                    </select>
                  </div>
                </div>
                <label className="field">市场分布 *<Select disabled={!editable} value={form.market} options={o.markets} onChange={(v) => set('market', v)} /></label>
                <div className="field"><span>队号</span><span className="readout" style={{ fontWeight: 500 }}>{o.team?.team_no ?? rec.team_no}</span></div>
                <label className="field">队长 *<input className="input" disabled={!editable} value={form.captain} onChange={(e) => set('captain', e.target.value)} /></label>
                <label className="field">钻机类型 *<Select disabled={!editable} value={form.rig_type} options={o.rigs} onChange={(v) => set('rig_type', v)} /></label>
                <label className="field">甲方单位 *<input className="input" disabled={!editable} list="dl-clients" value={form.client} onChange={(e) => set('client', e.target.value)} /></label>
                <label className="field">区块 *<input className="input" disabled={!editable} list="dl-blocks" value={form.block} onChange={(e) => set('block', e.target.value)} /></label>
                <label className="field">井别 *<Select disabled={!editable} value={form.well_category} options={o.categories} onChange={(v) => set('well_category', v)} /></label>
                <label className="field">井型 *<Select disabled={!editable} value={form.well_type} options={o.well_types} onChange={(v) => set('well_type', v)} /></label>
                <label className="field">开次（设计） *
                  <select className="input" disabled={!editable} value={form.spud_sections} onChange={(e) => setSections(Number(e.target.value))}>
                    {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n} 开</option>)}
                  </select>
                </label>
              </div>
              {rec.return_comment && rec.status === '草稿' && (
                <div className="notice">退回修改 · {rec.returned_by} · {rec.returned_at?.slice(0, 16)}：{rec.return_comment}</div>
              )}
              <datalist id="dl-clients">{o.clients.map((c) => <option key={c} value={c} />)}</datalist>
              <datalist id="dl-blocks">{o.blocks.map((c) => <option key={c} value={c} />)}</datalist>
            </section>

            {/* 二、井深：数值 */}
            <section className="card pad">
              <div className="sec-head"><h3>二、井深<small>数值</small></h3><span className="small muted">单位：米，可保留两位小数</span></div>
              <table className="ftable">
                <thead><tr><th>项目</th><th>数值</th><th>单位</th></tr></thead>
                <tbody>
                  <tr>
                    <td className="lbl">设计井深 *</td>
                    <td><input className="input num" type="number" min="0" step="0.01" aria-label="设计井深（米）" disabled={!editable} value={form.design_depth || ''}
                      onChange={(e) => set('design_depth', parseFloat(e.target.value) || 0)} /></td>
                    <td className="muted">米</td>
                  </tr>
                </tbody>
              </table>
            </section>

            {/* 三、施工节点：日期时间 */}
            <section className="card pad">
              <div className="sec-head"><h3>三、施工节点<small>日期时间</small></h3><span className="small muted">按施工进度逐步补填，未发生的留空；历时自动计算</span></div>
              <table className="ftable">
                <thead><tr><th>阶段</th><th>开始</th><th>结束</th><th className="r">历时（天）</th></tr></thead>
                <tbody>
                  <tr className="sub"><td colSpan={4}>钻前</td></tr>
                  <tr>
                    <td className="lbl">搬安（平移）/ 砸导管</td>
                    <td><DT v={form.milestones['搬安']} disabled={!editable} onChange={(v) => setMs('搬安', v)} label="搬安（平移）时间" /></td>
                    <td><DT v={form.milestones['砸导管']} disabled={!editable} onChange={(v) => setMs('砸导管', v)} label="砸导管时间" /></td>
                    <td className="r num muted">钻前 {days(d?.pre_days)}</td>
                  </tr>
                  <tr className="sub"><td colSpan={4}>钻进（按设计 {form.spud_sections} 开）· 开始 = 开钻时间，结束 = 该开次完钻</td></tr>
                  {o.sections.slice(0, form.spud_sections).map((name, i) => (
                    <tr key={name}>
                      <td className="lbl">{name}</td>
                      <td><DT v={form.milestones[`开钻${i + 1}`]} disabled={!editable} onChange={(v) => setMs(`开钻${i + 1}`, v)} label={`${name}时间`} /></td>
                      <td><DT v={form.milestones[`完钻${i + 1}`]} disabled={!editable} onChange={(v) => setMs(`完钻${i + 1}`, v)} label={`${name}完钻`} /></td>
                      <td className="r num">{d?.section_days[String(i + 1)] !== undefined ? fmt(d.section_days[String(i + 1)], 2) : '—'}</td>
                    </tr>
                  ))}
                  <tr className="sub"><td colSpan={4}>完钻与完井</td></tr>
                  <tr>
                    <td className="lbl">完钻 / 完井</td>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
                        <DT v={form.milestones['完钻']} disabled={!editable} onChange={(v) => setMs('完钻', v)} label="完钻时间" />
                        {editable && form.milestones[`完钻${form.spud_sections}`] && form.milestones['完钻'] !== form.milestones[`完钻${form.spud_sections}`] && (
                          <button type="button" className="btn sm link" style={{ minHeight: 30, padding: '0 8px' }}
                            onClick={() => setMs('完钻', form.milestones[`完钻${form.spud_sections}`])}>取末开次完钻</button>
                        )}
                      </div>
                    </td>
                    <td><DT v={form.milestones['完井']} disabled={!editable} onChange={(v) => setMs('完井', v)} label="完井时间" /></td>
                    <td className="r num muted">钻井 {days(d?.drill_days)}<br />完井 {days(d?.completion_days)}</td>
                  </tr>
                </tbody>
              </table>
            </section>

            {/* 四、人员：整数 */}
            <section className="card pad">
              <div className="sec-head"><h3>四、人员<small>整数</small></h3><span className="small muted">在册人数；人工、伙食、补贴按这里的人员数量核算，不再逐日填出勤</span></div>
              <table className="ftable">
                <thead><tr><th>用工类型</th><th>人数</th><th>单位</th></tr></thead>
                <tbody>
                  {o.staff.map((s) => (
                    <tr key={s.key}>
                      <td className="lbl">{s.label}</td>
                      <td><input className="input num" type="number" min="0" step="1" aria-label={s.label} disabled={!editable} value={form.staff[s.key] ?? 0}
                        onChange={(e) => set('staff', { ...form.staff, [s.key]: Math.max(0, Math.floor(Number(e.target.value) || 0)) })} /></td>
                      <td className="muted">人</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot><tr><td>合计</td><td className="num" style={{ paddingLeft: 22 }}>{d?.staff_total ?? 0}</td><td className="muted">人</td></tr></tfoot>
              </table>
            </section>
          </div>

          <aside className="aside">
            <section className="card pad" style={{ gap: 12 }}>
              <div className="sec-head">
                <h3>五、自动计算<small>只读</small></h3>
                {d && <span className={'tag ' + (WELL_TAG[d.status] || '')}>{d.status}</span>}
              </div>
              {d && (
                <div className="derived">
                  <div className="stat"><span>钻前周期（天）</span><b>{days(d.pre_days)}</b></div>
                  <div className="stat"><span>钻井周期（天）</span><b>{days(d.drill_days)}</b></div>
                  <div className="stat"><span>完井周期（天）</span><b>{days(d.completion_days)}</b></div>
                  <div className="stat"><span>人员合计</span><b>{d.staff_total}</b></div>
                  <div className="stat wide"><span>登记滞后（填报时间 − 一开时间）</span><b className={d.reg_lag.startsWith('滞后') ? 'bad' : ''} style={{ fontSize: 16 }}>{d.reg_lag}</b></div>
                  <div className="stat wide"><span>完井时间填报滞后（时限 {o.completion_lag_limit} 天）</span><b className={d.comp_lag?.startsWith('滞后') ? 'bad' : ''} style={{ fontSize: 16 }}>{d.comp_lag ?? '未完井'}</b></div>
                </div>
              )}
              <div className="kvline"><span>填报时间</span><span>{rec.registered_at ?? '首次提交时记录'}</span></div>
              <div className="kvline"><span>完井填报</span><span>{rec.completion_reported_at ?? (form.milestones['完井'] ? '提交时记录' : '—')}</span></div>
              <div className="kvline"><span>创建人</span><span>{rec.creator}</span></div>
            </section>

            <section className="card pad" style={{ gap: 12 }}>
              <div className="sec-head">
                <h3>校验结果</h3>
                <span className={'tag ' + (STATUS_TAG[rec.status] || '')}>{rec.status === '新建' ? '未保存' : `版本 ${rec.version} · ${rec.status}`}</span>
              </div>
              {editable ? (
                <>
                  <div className="stat-pair">
                    <div className="stat"><span>阻断</span><b>{blocks.length}</b></div>
                    <div className="stat"><span>警告</span><b>{warns.length}</b></div>
                  </div>
                  {checks.length === 0 && <div className="msg">全部校验通过</div>}
                  {checks.map((m, i) => <div key={i} className={'msg' + (m.level === '阻断' ? ' block' : '')}>· {m.level}：{m.message}</div>)}
                  <button type="button" className="btn primary" disabled={busy || blocks.length > 0} onClick={onSubmit}>
                    {blocks.length > 0 ? '存在阻断项，不能提交' : '提交并汇总'}
                  </button>
                  <button type="button" className="btn" disabled={busy || !form.well_id} onClick={onSave}>保存草稿</button>
                </>
              ) : (
                <>
                  <div className="notice info">已提交并汇总到单井基础信息（{rec.submitted_at}）。施工有新进展时点“更新基础信息”补填节点。</div>
                  <button type="button" className="btn primary" disabled={busy} onClick={onRevise}>更新基础信息</button>
                </>
              )}
            </section>

            {rec.history.length > 0 && (
              <section className="card pad" style={{ gap: 8 }}>
                <h3>版本记录</h3>
                {rec.history.map((h) => (
                  <div key={h.report_id} className="kvline" style={{ gridTemplateColumns: '40px 1fr' }}>
                    <span>v{h.version}</span>
                    <span>{h.status === '草稿' && h.return_comment ? '退回待修改' : h.status}{h.submitted_at ? ' · ' + h.submitted_at : ''} · {h.creator}
                      {h.source === '导入' ? ' · 导入' : ''}{h.return_comment ? <span className="small muted"><br />退回原因：{h.return_comment}</span> : null}</span>
                  </div>
                ))}
              </section>
            )}
          </aside>
        </div>
      )}
    </>
  );
}

function Select({ value, options, onChange, disabled }: { value: string; options: string[]; onChange: (v: string) => void; disabled?: boolean }) {
  const all = value && !options.includes(value) ? [value, ...options] : options;
  return (
    <select className="input" disabled={disabled} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">请选择</option>
      {all.map((x) => <option key={x} value={x}>{x}</option>)}
    </select>
  );
}

function DT({ v, onChange, disabled, label }: { v: string | null | undefined; onChange: (v: string | null) => void; disabled?: boolean; label: string }) {
  return (
    <span className="dt">
      <input type="datetime-local" aria-label={label} title={label} disabled={disabled} value={toInput(v)} onChange={(e) => onChange(fromInput(e.target.value))} />
      {!disabled && v && <button type="button" aria-label={'清除' + label} onClick={() => onChange(null)}>{Icon.close()}</button>}
    </span>
  );
}
