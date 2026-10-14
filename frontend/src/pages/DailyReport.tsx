import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { api, download, type Check, type LineDef, type Report, type ReportCatalog, type ReportSection } from '../api';
import type { PageProps } from '../App';
import { fmt, Icon, PageHead, today, useLoad, useToast } from '../ui';

interface Mine { report_id: number; well_id: string; report_date: string; version: number; status: string; review_comment: string | null }
interface Draft { depth_start: number; depth_end: number; hours: Record<string, number>; lines: Record<string, number>; notes: Record<string, string> }

const STATUS_TAG: Record<string, string> = { 草稿: '', 驳回: 'bad', 已提交: 'warn', 已审核: 'good', 新建: '' };
const CN = ['一', '二', '三', '四', '五', '六'];
const NOTE_LABEL: Record<string, string> = { equip: '设备编号', service: '签证单号', logistics: '单据号' };

export default function DailyReport(_: PageProps) {
  const toast = useToast();
  const wells = useLoad(() => api.get<{ well_id: string }[]>('/api/reports/wells'), []);
  const mine = useLoad(() => api.get<Mine[]>('/api/reports/mine'), []);
  const cat = useLoad(() => api.get<ReportCatalog>('/api/reports/catalog'), []);
  const [well, setWell] = useState('');
  const [date, setDate] = useState('');
  const [rep, setRep] = useState<Report | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [added, setAdded] = useState<Set<string>>(new Set());
  const [checks, setChecks] = useState<Check[]>([]);
  const [busy, setBusy] = useState(false);
  const [range, setRange] = useState({ start: '', end: '' });
  const fileRef = useRef<HTMLInputElement>(null);

  // 默认打开最早的待办日报，否则今天
  useEffect(() => {
    if (!wells.data || !mine.data || well) return;
    const todo = mine.data.find((m) => m.status === '草稿' || m.status === '驳回');
    setWell(todo?.well_id || wells.data[0]?.well_id || '');
    setDate(todo?.report_date || today());
  }, [wells.data, mine.data, well]);

  const show = (r: Report) => {
    setRep(r);
    setDraft({ depth_start: r.depth_start, depth_end: r.depth_end, hours: { ...r.hours }, lines: { ...r.lines }, notes: { ...(r.notes || {}) } });
    setChecks(r.checks);
    setAdded(new Set());
  };
  const load = async (w = well, d = date) => {
    if (!w || !d) return;
    try {
      show(await api.get<Report>(`/api/reports/current?well_id=${w}&date=${d}`));
      setRange({ start: d, end: d });
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [well, date]);

  const editable = !!rep && ['新建', '草稿', '驳回'].includes(rep.status);

  // 实时校验（与提交时同一套规则）
  useEffect(() => {
    if (!rep || !draft || !editable) return;
    const t = window.setTimeout(() => {
      api.post<Check[]>('/api/reports/check', { well_id: well, report_date: date, ...draft }).then(setChecks).catch(() => {});
    }, 250);
    return () => window.clearTimeout(t);
  }, [draft, rep, editable, well, date]);

  const save = async (): Promise<Report | null> => {
    if (!rep || !draft) return null;
    const body = { well_id: well, report_date: date, ...draft };
    const r = rep.report_id ? await api.put<Report>(`/api/reports/${rep.report_id}`, body) : await api.post<Report>('/api/reports', body);
    setRep(r);
    return r;
  };
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try {
      await fn();
      toast(ok);
      mine.reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const onSave = () => act(save, '草稿已保存');
  const onSubmit = () => act(async () => {
    const r = await save();
    if (r) show(await api.post<Report>(`/api/reports/${r.report_id}/submit`));
  }, '日报已提交，等待部室审核');
  const onCorrect = () => act(async () => show(await api.post<Report>(`/api/reports/${rep!.report_id}/correct`)),
    '已生成更正版本草稿，审核通过后原事实冲销重算');
  const onExport = () => act(() => download(`/api/reports/template.xlsx?well_id=${well}&start=${range.start}&end=${range.end}`, '成本日报.xlsx'), '模板已导出');
  const onImport = async (f: File) => {
    const fd = new FormData();
    fd.append('file', f);
    await act(async () => {
      const res = await api.post<{ sheet: string; ok: boolean; message: string }[]>('/api/reports/import', fd);
      const bad = res.filter((x) => !x.ok);
      if (bad.length) throw new Error(bad.map((x) => `${x.sheet}：${x.message}`).join('；'));
      await load();
    }, '导入完成，已存为草稿；请逐张检查后提交');
    if (fileRef.current) fileRef.current.value = '';
  };

  const sections = cat.data?.sections ?? [];
  const conds = cat.data?.work_conditions ?? [];
  const defs = rep?.line_defs ?? [];
  const visible = (d: LineDef) => {
    const has = draft && (draft.lines[d.key] !== undefined && draft.lines[d.key] !== null && Number(draft.lines[d.key]) !== 0);
    return editable ? d.show || !!has || added.has(d.key) : !!has;
  };
  const total = draft ? conds.reduce((a, h) => a + (Number(draft.hours[h]) || 0), 0) : 0;
  const footage = draft ? draft.depth_end - draft.depth_start : 0;
  const blocks = checks.filter((c) => c.level === '阻断');
  const warns = checks.filter((c) => c.level === '警告');
  const tips = checks.filter((c) => c.level === '提示');
  const setHour = (h: string, v: string) => setDraft((d) => d && { ...d, hours: { ...d.hours, [h]: parseFloat(v) || 0 } });
  const setLine = (k: string, v: string) => setDraft((d) => d && { ...d, lines: { ...d.lines, [k]: v === '' ? 0 : parseFloat(v) || 0 } });
  const setNote = (k: string, v: string) => setDraft((d) => d && { ...d, notes: { ...d.notes, [k]: v } });
  const removeLine = (k: string) => {
    setDraft((d) => {
      if (!d) return d;
      const lines = { ...d.lines };
      const notes = { ...d.notes };
      delete lines[k];
      delete notes[k];
      return { ...d, lines, notes };
    });
    setAdded((s) => { const n = new Set(s); n.delete(k); return n; });
  };
  const sectionCount = (id: string) => defs.filter((d) => d.section === id && Number(draft?.lines[d.key] || 0) > 0).length;
  const jump = (id: string) => document.getElementById('sec-' + id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });

  return (
    <>
      <PageHead title="成本日报" sub="一井一队一天一张，只报量、不含金额；只填需要基层采集、参与成本计算的量"
        right={
          <div className="head-actions">
            <label className="pill">井号
              <select value={well} onChange={(e) => setWell(e.target.value)}>
                {(wells.data || []).map((w) => <option key={w.well_id}>{w.well_id}</option>)}
              </select>
            </label>
            <label className="pill">日期<input type="date" value={date} onChange={(e) => e.target.value && setDate(e.target.value)} /></label>
            <RecentSelect mine={mine.data || []} well={well} date={date} onPick={(w, d) => { setWell(w); setDate(d); }} />
          </div>
        } />

      {rep && draft && (
        <>
          <nav className="sec-nav" aria-label="日报分段">
            {sections.map((s, i) => {
              const n = s.id === 'drill' ? (Math.abs(total - 24) < 1e-6 ? '✓' : `${total}h`) : sectionCount(s.id) ? `${sectionCount(s.id)} 项` : '';
              return (
                <button key={s.id} type="button" onClick={() => jump(s.id)}>
                  <span className="no">{CN[i]}</span>{s.title}{n && <span className="cnt">{n}</span>}
                </button>
              );
            })}
          </nav>
          <div className="split">
            <div className="grow">
              <section className="card pad">
                <div className="sec-head">
                  <h3>表头 <span className={'tag ' + (STATUS_TAG[rep.status] || '')}>{rep.status}{rep.version > 1 ? ` · 版本 ${rep.version}` : ''}</span></h3>
                  <span className="small muted">选定井号后自动带出，不可手改</span>
                </div>
                <div className="kv-grid">
                  {[['井号', rep.header.well_id], ['日期', rep.header.date], ['队伍', rep.header.team], ['区块', rep.header.block],
                    ['井型', rep.header.well_type], ['当前开次', rep.header.phase]].map(([k, v]) => (
                    <div key={k} className="kv"><span className="k">{k}</span><span className="v">{v}</span></div>
                  ))}
                </div>
                <div className="notice info">
                  人工费、伙食费、补贴按“基础信息”中的人员数量核算，不用逐日填出勤：{(() => {
                    const e = Object.entries(rep.staff || {});
                    return e.length ? e.map(([k, v]) => `${k} ${v} 人`).join('、') : '基础信息中尚未填写人员数量';
                  })()}。
                </div>
                {rep.status === '驳回' && rep.review_comment && <div className="notice">审核驳回：{rep.review_comment}</div>}
                {rep.status === '新建' && rep.carried && <div className="notice info">在用设备已沿用上一张日报，请按当天实际修改。</div>}
              </section>

              {sections.map((s, i) => s.id === 'drill' ? (
                <section key={s.id} id="sec-drill" className="card pad rsec">
                  <SecHead no={CN[i]} s={s} />
                  <div className="form-grid">
                    <label className="field">起始井深（米）<input className="input" type="number" disabled={!editable} value={draft.depth_start}
                      onChange={(e) => setDraft({ ...draft, depth_start: parseFloat(e.target.value) || 0 })} /></label>
                    <label className="field">结束井深（米）<input className="input" type="number" disabled={!editable} value={draft.depth_end}
                      onChange={(e) => setDraft({ ...draft, depth_end: parseFloat(e.target.value) || 0 })} /></label>
                    <div className="field"><span>进尺（自动计算）</span><span className="readout">{fmt(footage, 0)} 米</span></div>
                  </div>
                  <div className="sub-title">工况时间<span className="small muted">合计必须等于 24 小时</span></div>
                  <div className="hours-grid">
                    {conds.map((h) => (
                      <label key={h} className="hour">
                        <span>{h}</span>
                        <input className="input num" type="number" step="0.5" min="0" disabled={!editable} value={draft.hours[h] || ''} placeholder="0"
                          onChange={(e) => setHour(h, e.target.value)} aria-label={h + '（小时）'} />
                      </label>
                    ))}
                  </div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 14 }}>
                    <span className="muted">合计</span>
                    {Math.abs(total - 24) < 1e-6 ? <span className="ok">{total} 小时 · 校验通过</span> : <span className="bad">{total} 小时 · 必须等于 24 小时</span>}
                  </div>
                </section>
              ) : (
                <LineSection key={s.id} no={CN[i]} s={s} defs={defs.filter((d) => d.section === s.id)} visible={visible} editable={editable}
                  draft={draft} setLine={setLine} setNote={setNote} remove={removeLine}
                  add={(k) => setAdded((x) => new Set(x).add(k))} />
              ))}
            </div>

            <aside className="aside">
              <section className="card pad sticky" style={{ gap: 12 }}>
                <h3>校验结果</h3>
                <div className="stat-pair" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
                  <div className="stat"><span>阻断</span><b>{blocks.length}</b></div>
                  <div className="stat"><span>警告</span><b>{warns.length}</b></div>
                  <div className="stat"><span>提示</span><b>{tips.length}</b></div>
                </div>
                {checks.length === 0 && <div className="msg">全部校验通过</div>}
                {[...blocks, ...warns, ...tips].map((m, i) => (
                  <div key={i} className={'msg' + (m.level === '阻断' ? ' block' : '')}>· {m.level}：{m.message}</div>
                ))}
                {editable && (
                  <>
                    <button type="button" className="btn primary" disabled={busy || blocks.length > 0} onClick={onSubmit}>
                      {blocks.length > 0 ? '存在阻断项，不能提交' : '提交日报'}
                    </button>
                    <button type="button" className="btn" disabled={busy} onClick={onSave}>保存草稿</button>
                  </>
                )}
                {rep.status === '已提交' && <div className="notice info">已提交，等待专业部室审核；如需修改请联系审核人驳回。</div>}
                {rep.status === '已审核' && (
                  <>
                    <div className="notice info">已审核，事实已生效并由规则引擎计算金额。</div>
                    <button type="button" className="btn" disabled={busy} onClick={onCorrect}>发起更正</button>
                  </>
                )}
              </section>
              <section className="card pad" style={{ gap: 10 }}>
                <h3>Excel 模板</h3>
                <div className="hint" style={{ lineHeight: '19px' }}>按井号和日期区间导出，每天一个工作表、按段排列；离线填好后导入。导入先存为草稿，校验与线上一致。</div>
                <div className="form-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
                  <label className="field">开始<input className="input" type="date" value={range.start} onChange={(e) => setRange({ ...range, start: e.target.value })} /></label>
                  <label className="field">结束<input className="input" type="date" value={range.end} onChange={(e) => setRange({ ...range, end: e.target.value })} /></label>
                </div>
                <div className="btn-row">
                  <button type="button" className="btn link" disabled={busy} onClick={onExport}>导出模板</button>
                  <button type="button" className="btn link" disabled={busy} onClick={() => fileRef.current?.click()}>导入 Excel</button>
                  <input ref={fileRef} type="file" accept=".xlsx" hidden onChange={(e) => e.target.files?.[0] && onImport(e.target.files[0])} />
                </div>
              </section>
              {cat.data && (
                <section className="card pad" style={{ gap: 8 }}>
                  <details className="nc">
                    <summary><h3 style={{ display: 'inline' }}>不由基层填报的费用</h3><span className="small muted"> · {cat.data.not_collected.reduce((a, g) => a + g.items.length, 0)} 项</span></summary>
                    {cat.data.not_collected.map((g) => (
                      <div key={g.reason} className="nc-group">
                        <div className="small bold">{g.reason}</div>
                        <div className="small muted">{g.items.map((x) => x.item_name.replace(/^（\d+）|^\d+\./, '')).join('、')}</div>
                      </div>
                    ))}
                  </details>
                </section>
              )}
              {rep.history.length > 0 && (
                <section className="card pad" style={{ gap: 8 }}>
                  <h3>处理记录</h3>
                  {rep.history.map((h, i) => (
                    <div key={i} className="msg">{h.operated_at.slice(5, 16)} · {h.action} · {h.operator}{h.comment ? '：' + h.comment : ''}</div>
                  ))}
                </section>
              )}
            </aside>
          </div>
        </>
      )}
    </>
  );
}

function SecHead({ no, s }: { no: string; s: ReportSection }) {
  return (
    <div className="rsec-head">
      <h3><span className="sec-no">{no}</span>{s.title}<small>{s.fact}</small></h3>
      <div className="small muted">{s.how}；计入：{s.drives}</div>
    </div>
  );
}

function LineSection({ no, s, defs, visible, editable, draft, setLine, setNote, remove, add }: {
  no: string; s: ReportSection; defs: LineDef[]; visible: (d: LineDef) => boolean; editable: boolean; draft: Draft;
  setLine: (k: string, v: string) => void; setNote: (k: string, v: string) => void; remove: (k: string) => void; add: (k: string) => void;
}) {
  const rows = defs.filter(visible);
  const hidden = defs.filter((d) => !visible(d));
  const groups = useMemo(() => [...new Set(rows.map((d) => d.group))], [rows]);
  const noteLabel = NOTE_LABEL[s.id];
  if (!editable && rows.length === 0) return null;
  return (
    <section id={'sec-' + s.id} className="card pad rsec">
      <SecHead no={no} s={s} />
      {rows.length > 0 && (
        <table className="ftable rtable">
          <thead>
            <tr><th>项目</th><th>数量</th><th>单位</th>{noteLabel && <th>{noteLabel}{s.id !== 'equip' && ' *'}</th>}<th>计价</th>{editable && <th />}</tr>
          </thead>
          <tbody>
            {groups.map((g) => (
              <Fragment key={g}>
                {(groups.length > 1 || rows.filter((d) => d.group === g).length > 1) && <tr className="sub"><td colSpan={noteLabel ? 6 : 5}>{g}</td></tr>}
                {rows.filter((d) => d.group === g).map((d) => (
                  <tr key={d.key}>
                    <td className="lbl">{d.label}<div className="small muted" style={{ fontWeight: 400 }}>{d.item_code} {d.item_name.replace(/^（\d+）|^\d+\./, '')}</div></td>
                    <td><input className="input num" type="number" min="0" step="any" disabled={!editable} aria-label={d.label + '（' + d.unit + '）'}
                      value={draft.lines[d.key] ?? ''} placeholder="0" onChange={(e) => setLine(d.key, e.target.value)} /></td>
                    <td className="muted">{d.unit}</td>
                    {noteLabel && (
                      <td><input className="input" style={{ height: 38, fontSize: 14, maxWidth: 180 }} disabled={!editable} aria-label={d.label + noteLabel}
                        value={draft.notes[d.key] ?? ''} placeholder={d.needs_doc ? '必填' : '选填'} onChange={(e) => setNote(d.key, e.target.value)} /></td>
                    )}
                    <td>{d.priced
                      ? <span className="tag info" title={'参与规则：' + d.rules.join('、')}>规则计价</span>
                      : <span className="tag" title={`暂无已发布规则，由${d.dept || '归口'}部室补规则`}>暂无规则</span>}</td>
                    {editable && <td>{!d.core && <button type="button" className="row-x" aria-label={'移除' + d.label} onClick={() => remove(d.key)}>{Icon.close()}</button>}</td>}
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
      {editable && hidden.length > 0 && (
        <label className="add-line">
          <span>＋ 添加项目</span>
          <select value="" onChange={(e) => e.target.value && add(e.target.value)} aria-label={'添加' + s.title + '项目'}>
            <option value="">从 {hidden.length} 项中选择…</option>
            {[...new Set(hidden.map((d) => d.group))].map((g) => (
              <optgroup key={g} label={g}>
                {hidden.filter((d) => d.group === g).map((d) => <option key={d.key} value={d.key}>{d.label}（{d.unit}）{d.priced ? ' · 规则计价' : ''}</option>)}
              </optgroup>
            ))}
          </select>
        </label>
      )}
      {editable && rows.length === 0 && hidden.length > 0 && <div className="small muted">当天没有发生可不填。</div>}
    </section>
  );
}

function RecentSelect({ mine, well, date, onPick }: { mine: Mine[]; well: string; date: string; onPick: (w: string, d: string) => void }) {
  const key = mine.some((m) => m.well_id === well && m.report_date === date) ? `${well}|${date}` : '';
  const todo = mine.filter((m) => m.status === '草稿' || m.status === '驳回').length;
  return (
    <label className="pill">最近日报
      <select aria-label="最近日报与待办" value={key} onChange={(e) => {
        const m = mine.find((x) => `${x.well_id}|${x.report_date}` === e.target.value);
        if (m) onPick(m.well_id, m.report_date);
      }}>
        <option value="">{todo ? `待办 ${todo} 张` : '选择'}</option>
        {mine.map((m) => (
          <option key={m.report_id} value={`${m.well_id}|${m.report_date}`}>
            {m.well_id} · {m.report_date} · {m.status}{m.status === '驳回' || m.status === '草稿' ? '（待办）' : ''}
          </option>
        ))}
      </select>
    </label>
  );
}
