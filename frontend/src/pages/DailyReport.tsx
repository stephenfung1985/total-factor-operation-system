import { useEffect, useRef, useState } from 'react';
import { api, download, type Check, type Report, type ReportCatalog } from '../api';
import type { PageProps } from '../App';
import { fmt, Icon, PageHead, today, useLoad, useToast } from '../ui';

interface Mine { report_id: number; well_id: string; report_date: string; version: number; status: string; review_comment: string | null }
interface Draft { depth_start: number; depth_end: number; hours: Record<string, number>; lines: Record<string, number>; notes: Record<string, string> }

const STATUS_TAG: Record<string, string> = { 草稿: '', 驳回: 'bad', 已提交: 'warn', 已审核: 'good', 新建: '' };
const STATUS_LABEL: Record<string, string> = { 新建: '未保存', 已审核: '已生效', 已提交: '待生效', 驳回: '已退回', 已被更正: '已被更正' };
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
  }, '日报已生效，规则引擎已按新数量计算');
  const onCorrect = () => act(async () => show(await api.post<Report>(`/api/reports/${rep!.report_id}/correct`)),
    '已生成更正版本草稿，提交后替代原日报并冲销重算');
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
  const hasVal = (k: string) => !!draft && Number(draft.lines[k] || 0) !== 0;
  // 只列参与科目费用计算（有已发布规则计价）的项目；旧数据里已有数量的无规则项目保留，便于清除
  const rowsOf = (sec: string) => defs.filter((d) => d.section === sec && (d.priced || hasVal(d.key)));
  const lineSecs = sections.filter((x) => x.id !== 'drill' && rowsOf(x.id).length > 0);
  const total = draft ? conds.reduce((a, h) => a + (Number(draft.hours[h]) || 0), 0) : 0;
  const footage = draft ? draft.depth_end - draft.depth_start : 0;
  const blocks = checks.filter((c) => c.level === '阻断');
  const warns = checks.filter((c) => c.level !== '阻断');
  const setHour = (h: string, v: string) => setDraft((d) => d && { ...d, hours: { ...d.hours, [h]: parseFloat(v) || 0 } });
  const setLine = (k: string, v: string) => setDraft((d) => d && { ...d, lines: { ...d.lines, [k]: v === '' ? 0 : parseFloat(v) || 0 } });
  const setNote = (k: string, v: string) => setDraft((d) => d && { ...d, notes: { ...d.notes, [k]: v } });
  const clearLine = (k: string) => setDraft((d) => {
    if (!d) return d;
    const lines = { ...d.lines };
    const notes = { ...d.notes };
    delete lines[k];
    delete notes[k];
    return { ...d, lines, notes };
  });
  const left = 24 - total;
  const staff = Object.entries(rep?.staff || {});

  return (
    <>
      <PageHead title="成本日报" sub="一井一队一天一张，只报量、不填金额；只列本井当天参与费用计算的项目"
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
          <div className="rep-bar">
            <span className={'tag ' + (STATUS_TAG[rep.status] || '')}>{STATUS_LABEL[rep.status] || rep.status}{rep.version > 1 ? ` · v${rep.version}` : ''}</span>
            {[['队伍', rep.header.team], ['区块', rep.header.block], ['井型', rep.header.well_type], ['开次', rep.header.phase]].map(([k, v]) => (
              <span key={k}><span className="muted">{k}</span> {v}</span>
            ))}
            <span className="muted">人员按基础信息自动核算：{staff.length ? staff.map(([k, v]) => `${k} ${v}`).join(' · ') + ' 人' : '未填写'}</span>
          </div>
          {rep.status === '驳回' && rep.review_comment && <div className="notice">{rep.review_comment}{rep.supersedes_report_id ? '（原日报暂时保持生效，修改后提交即替代）' : '（原数据已冲销，修改后重新提交）'}</div>}
          {rep.status === '新建' && rep.carried && <div className="notice info">在用设备已沿用上一张日报，请按当天实际修改。</div>}

          <div className="split">
            <div className="grow">
              <section className="card pad">
                <div className="sec-head"><h3><span className="step-no">1</span>进尺与工况</h3><span className="small muted">井深填当天起止，进尺自动算</span></div>
                <div className="form-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
                  <label className="field">起始井深（米）<input className="input" type="number" inputMode="decimal" disabled={!editable} value={draft.depth_start}
                    onChange={(e) => setDraft({ ...draft, depth_start: parseFloat(e.target.value) || 0 })} /></label>
                  <label className="field">结束井深（米）<input className="input" type="number" inputMode="decimal" disabled={!editable} value={draft.depth_end}
                    onChange={(e) => setDraft({ ...draft, depth_end: parseFloat(e.target.value) || 0 })} /></label>
                  <div className="field"><span>进尺</span><span className={'readout' + (footage < 0 ? ' bad' : '')}>{fmt(footage, 0)} 米</span></div>
                </div>
                <div className="hours-head">
                  <span className="bold">工况时间（小时）</span>
                  <span className={Math.abs(left) < 1e-6 ? 'ok' : 'bad'}>
                    合计 {fmt(total, 1)} · {Math.abs(left) < 1e-6 ? '已满 24 小时' : left > 0 ? `还差 ${fmt(left, 1)} 小时` : `超出 ${fmt(-left, 1)} 小时`}
                  </span>
                </div>
                <div className="hours-grid">
                  {conds.map((h) => (
                    <label key={h} className={'hour' + (Number(draft.hours[h]) > 0 ? ' on' : '')}>
                      <span>{h}</span>
                      <input className="input num" type="number" step="0.5" min="0" inputMode="decimal" disabled={!editable} value={draft.hours[h] || ''} placeholder="0"
                        onChange={(e) => setHour(h, e.target.value)} aria-label={h + '（小时）'} />
                    </label>
                  ))}
                </div>
              </section>

              {lineSecs.map((sec, i) => (
                <section key={sec.id} className="card pad">
                  <div className="sec-head"><h3><span className="step-no">{i + 2}</span>{sec.title}</h3><span className="small muted">{sec.how}</span></div>
                  <div className="rlines">
                    {rowsOf(sec.id).map((d) => {
                      const note = NOTE_LABEL[sec.id];
                      return (
                        <div key={d.key} className={'rline' + (hasVal(d.key) ? ' on' : '')}>
                          <span className="rl-name" title={`${d.item_code} ${d.item_name}`}>
                            {d.label}{d.group !== d.label && <small>{d.group}</small>}
                            {!d.priced && <span className="tag warn">暂无规则</span>}
                          </span>
                          <span className="qty">
                            <input className="input" type="number" min="0" step="any" inputMode="decimal" disabled={!editable} aria-label={`${d.label}（${d.unit}）`}
                              value={draft.lines[d.key] || ''} placeholder="0" onChange={(e) => setLine(d.key, e.target.value)} />
                            <em>{d.unit}</em>
                          </span>
                          {note ? (
                            <input className="input rl-note" disabled={!editable} aria-label={d.label + note} value={draft.notes[d.key] ?? ''}
                              placeholder={d.needs_doc ? `${note}（必填）` : `${note}（选填）`} onChange={(e) => setNote(d.key, e.target.value)} />
                          ) : <span />}
                          {editable && !d.priced
                            ? <button type="button" className="row-x" aria-label={'清除' + d.label} onClick={() => clearLine(d.key)}>{Icon.close()}</button>
                            : <span />}
                        </div>
                      );
                    })}
                  </div>
                </section>
              ))}
              {lineSecs.length === 0 && <div className="card pad muted">本井当天没有需要报量的规则计价项目，只填进尺与工况即可。</div>}
            </div>

            <aside className="aside sticky">
              <section className="card pad" style={{ gap: 12 }}>
                <div className="sec-head">
                  <h3>{editable ? '检查并提交' : '处理状态'}</h3>
                  {editable && <span className={'tag ' + (blocks.length ? 'bad' : 'good')}>{blocks.length ? `${blocks.length} 项需修改` : '可以提交'}</span>}
                </div>
                {editable && checks.length === 0 && <div className="msg">全部校验通过</div>}
                {editable && blocks.map((m, i) => <div key={'b' + i} className="msg block">· {m.message}</div>)}
                {editable && warns.map((m, i) => <div key={'w' + i} className="msg">· {m.level}：{m.message}</div>)}
                {editable && (
                  <>
                    <button type="button" className="btn primary" disabled={busy || blocks.length > 0} onClick={onSubmit}>提交并生效</button>
                    {warns.some((m) => m.level === '警告') && <div className="hint" style={{ whiteSpace: 'normal' }}>有警告也能提交，生效后由管理端抽查</div>}
                    <button type="button" className="btn" disabled={busy} onClick={onSave}>保存草稿</button>
                  </>
                )}
                {rep.status === '已提交' && <div className="notice info">旧流程提交的日报，等待管理端确认生效。</div>}
                {rep.status === '已审核' && (
                  <>
                    <div className="notice info">已生效，规则引擎已计算金额{rep.checked_at ? `；管理端已抽查（${rep.checked_at.slice(5, 16)}）` : ''}。发现报错可随时发起更正。</div>
                    <button type="button" className="btn" disabled={busy} onClick={onCorrect}>发起更正</button>
                  </>
                )}
              </section>
              <details className="card pad" style={{ gap: 10 }}>
                <summary className="bold" style={{ cursor: 'pointer' }}>离线填报（Excel）</summary>
                <div className="hint" style={{ lineHeight: '19px', whiteSpace: 'normal' }}>按日期区间导出，每天一个工作表；离线填好后导入，先存为草稿再逐张提交。</div>
                <div className="form-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
                  <label className="field">开始<input className="input" type="date" value={range.start} onChange={(e) => setRange({ ...range, start: e.target.value })} /></label>
                  <label className="field">结束<input className="input" type="date" value={range.end} onChange={(e) => setRange({ ...range, end: e.target.value })} /></label>
                </div>
                <div className="btn-row">
                  <button type="button" className="btn" disabled={busy} onClick={onExport}>导出模板</button>
                  <button type="button" className="btn" disabled={busy} onClick={() => fileRef.current?.click()}>导入 Excel</button>
                  <input ref={fileRef} type="file" accept=".xlsx" hidden onChange={(e) => e.target.files?.[0] && onImport(e.target.files[0])} />
                </div>
              </details>
              {rep.history.length > 0 && (
                <details className="card pad" style={{ gap: 8 }}>
                  <summary className="bold" style={{ cursor: 'pointer' }}>处理记录（{rep.history.length}）</summary>
                  {rep.history.map((h, i) => (
                    <div key={i} className="msg">{h.operated_at.slice(5, 16)} · {h.action} · {h.operator}{h.comment ? '：' + h.comment : ''}</div>
                  ))}
                </details>
              )}
            </aside>
          </div>
        </>
      )}
    </>
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
            {m.well_id} · {m.report_date} · {STATUS_LABEL[m.status] || m.status}{m.status === '驳回' || m.status === '草稿' ? '（待办）' : ''}
          </option>
        ))}
      </select>
    </label>
  );
}
