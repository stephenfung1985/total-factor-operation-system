import { useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { fmt, Modal, useLoad, useToast } from '../ui';

interface AiStatus { enabled: boolean; model: string | null; endpoint: string | null; prompt_version: string; doc_types: string[]; max_chars: number }
interface Task {
  task_id: number; status: string; model: string | null; created_at: string; draft_count: number; error: string | null; truncated: number;
  skipped: { clause_no: string; name: string; reason: string }[]; manual: { clause_no: string; text: string; reason: string }[];
  adoption: { pending: number; published: number; modified: number; rejected: number };
}
interface Doc {
  doc_id: number; doc_type: string; doc_no: string | null; doc_name: string; counterparty: string | null; parse_status: string;
  file_name: string; page_count: number; chars: number; uploaded_at: string; last_task: Task | null;
}
interface ParseOut {
  ok: boolean; truncated: boolean; task: Task;
  created: { rule_id: number; rule_code: string; name: string; item_code: string; confidence: number; low_fields: string[] }[];
  skipped: Task['skipped']; manual: Task['manual'];
}
interface Run { state: 'wait' | 'run' | 'ok' | 'fail' | 'stop'; out?: ParseOut; error?: string }

const EMPTY_META = { doc_type: '', doc_name: '', doc_no: '', counterparty: '', valid_from: '' };
const RUN_LABEL: Record<Run['state'], string> = { wait: '排队中', run: 'AI 解析中…', ok: '完成', fail: '失败', stop: '已停止' };

/**
 * 导入依据文件，交给 AI 拆条款、提规则草稿；草稿进入“待确认”，仍由管理端逐条对照原文确认、回算、发布。
 * 支持批量：一次选多份文件上传，或在已上传列表里勾选多份，逐份顺序解析（解析时要占用数据库写锁，不并发）。
 */
export default function AiImport({ open, onClose, onDone, onPick }: { open: boolean; onClose: () => void; onDone: () => void; onPick: (ruleId: number) => void }) {
  const toast = useToast();
  const st = useLoad(() => (open ? api.get<AiStatus>('/api/ai/status') : Promise.resolve(null)), [open]);
  const docs = useLoad(() => (open ? api.get<Doc[]>('/api/ai/docs') : Promise.resolve(null)), [open]);
  const fileRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [f, setF] = useState(EMPTY_META);
  const [failed, setFailed] = useState<{ name: string; error: string }[]>([]);
  const [batch, setBatch] = useState<Doc[]>([]);
  const [runs, setRuns] = useState<Record<number, Run>>({});
  const [pick, setPick] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState<'' | 'upload' | 'parse'>('');
  const stopRef = useRef(false);
  const [stopping, setStopping] = useState(false);

  const reset = () => { setFiles([]); setFailed([]); setBatch([]); setRuns({}); setF(EMPTY_META); };
  useEffect(() => { if (open) { reset(); setPick(new Set()); } }, [open]);

  const multi = files.length > 1;
  const chooseFiles = (list: File[]) => {
    setFiles(list);
    setF((v) => ({ ...v, doc_name: list.length === 1 ? v.doc_name || list[0].name.replace(/\.[^.]+$/, '') : '', doc_no: list.length === 1 ? v.doc_no : '' }));
  };

  /** 逐份上传；重复、扫描件等失败的单独列出，不影响其余文件。 */
  const doUpload = async () => {
    if (!files.length) return;
    setBusy('upload');
    const ok: Doc[] = [];
    const bad: { name: string; error: string }[] = [];
    for (const file of files) {
      const fd = new FormData();
      fd.append('file', file);
      Object.entries({ ...f, doc_name: multi ? '' : f.doc_name, doc_no: multi ? '' : f.doc_no }).forEach(([k, v]) => fd.append(k, v));
      try {
        ok.push(await api.post<Doc>('/api/ai/docs', fd));
      } catch (e) {
        bad.push({ name: file.name, error: (e as Error).message });
      }
    }
    setBusy('');
    setFailed(bad);
    setBatch(ok);
    setRuns({});
    docs.reload();
    if (ok.length) toast(`已上传 ${ok.length} 份，共提取 ${fmt(ok.reduce((a, d) => a + d.chars, 0), 0)} 字${bad.length ? `；${bad.length} 份失败` : ''}`);
    else toast('没有文件上传成功', true);
  };

  /** 顺序解析一批文件；中途可停止（当前这份解析完后停）。 */
  const doParse = async (list: Doc[]) => {
    if (!list.length) return;
    setBatch(list);
    setRuns(Object.fromEntries(list.map((d) => [d.doc_id, { state: 'wait' } as Run])));
    setBusy('parse');
    stopRef.current = false;
    setStopping(false);
    let okN = 0, drafts = 0;
    for (const d of list) {
      if (stopRef.current) {
        setRuns((r) => ({ ...r, [d.doc_id]: { state: 'stop' } }));
        continue;
      }
      setRuns((r) => ({ ...r, [d.doc_id]: { state: 'run' } }));
      try {
        const out = await api.post<ParseOut>(`/api/ai/docs/${d.doc_id}/parse`);
        okN += 1;
        drafts += out.created.length;
        setRuns((r) => ({ ...r, [d.doc_id]: { state: 'ok', out } }));
      } catch (e) {
        setRuns((r) => ({ ...r, [d.doc_id]: { state: 'fail', error: (e as Error).message } }));
      }
    }
    setBusy('');
    setPick(new Set());
    docs.reload();
    if (okN) onDone();
    toast(`批量解析结束：成功 ${okN} / ${list.length} 份，生成草稿 ${drafts} 条`, okN < list.length);
  };

  const s = st.data;
  const started = Object.keys(runs).length > 0;
  const outs = batch.map((d) => runs[d.doc_id]?.out).filter((x): x is ParseOut => !!x);
  const total = (k: 'created' | 'skipped' | 'manual') => outs.reduce((a, o) => a + o[k].length, 0);
  const allDocs = docs.data || [];
  const unparsed = allDocs.filter((d) => !d.last_task || d.last_task.status === '失败');
  const togglePick = (id: number) => setPick((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const canParse = !!s?.enabled && busy === '';

  return (
    <Modal open={open} onClose={onClose} title="导入依据文件 · AI 解析" wide>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {s && !s.enabled && <div className="notice">未配置 AI 服务：文件可以先上传存档，解析需在部署机的 ~/tfos-data/env 设置 TFOS_AI_BASE_URL、TFOS_AI_MODEL（可选 TFOS_AI_API_KEY）后重启。</div>}
        {s?.enabled && <div className="hint" style={{ whiteSpace: 'normal' }}>模型 {s.model} · {s.endpoint} · 提示词 {s.prompt_version}。AI 只生成“待确认”草稿，每条附原文出处；确认、回算、发布仍按现有流程。</div>}

        {!batch.length && (
          <>
            <div className="form-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))' }}>
              <div className="field"><span>文件 *（PDF / Word / TXT，可多选）</span>
                <button type="button" className="btn" onClick={() => fileRef.current?.click()} title={files.map((x) => x.name).join('\n')}>
                  {files.length === 0 ? '选择文件' : multi ? `已选 ${files.length} 份` : files[0].name}
                </button>
                <input ref={fileRef} type="file" accept=".pdf,.docx,.txt" multiple hidden onChange={(e) => { if (e.target.files?.length) chooseFiles([...e.target.files]); e.target.value = ''; }} />
              </div>
              <label className="field">文件类型 *
                <select className="input" value={f.doc_type} onChange={(e) => setF({ ...f, doc_type: e.target.value })}>
                  <option value="">请选择</option>
                  {s?.doc_types.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              {!multi && <label className="field">文件名称<input className="input" value={f.doc_name} onChange={(e) => setF({ ...f, doc_name: e.target.value })} /></label>}
              {!multi && <label className="field">文号<input className="input" value={f.doc_no} onChange={(e) => setF({ ...f, doc_no: e.target.value })} placeholder="如 DX-2026-07" /></label>}
              <label className="field">对方单位 / 提供部室<input className="input" value={f.counterparty} onChange={(e) => setF({ ...f, counterparty: e.target.value })} /></label>
              <label className="field">生效日<input className="input" type="date" value={f.valid_from} onChange={(e) => setF({ ...f, valid_from: e.target.value })} /></label>
            </div>
            {multi && <div className="small muted">多份文件共用文件类型、对方单位和生效日；文件名称取各自的文件名，文号可在解析后的规则里补。</div>}
            <div className="btn-row">
              <button type="button" className="btn primary" disabled={!files.length || !f.doc_type || busy !== ''} onClick={doUpload}>
                {busy === 'upload' ? '上传中…' : multi ? `上传 ${files.length} 份并提取文字` : '上传并提取文字'}
              </button>
            </div>
          </>
        )}

        {failed.length > 0 && (
          <div className="notice" style={{ whiteSpace: 'normal' }}>
            {failed.length} 份上传失败：{failed.map((x) => <div key={x.name}>· {x.name}：{x.error}</div>)}
          </div>
        )}

        {batch.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {started && (
              <div className="stat-pair" style={{ gridTemplateColumns: 'repeat(4, minmax(0, 1fr))' }}>
                <div className="stat"><span>已解析</span><b>{outs.length} / {batch.length}</b></div>
                <div className="stat"><span>生成草稿</span><b>{total('created')}</b></div>
                <div className="stat"><span>不采纳</span><b>{total('skipped')}</b></div>
                <div className="stat"><span>需人工计价</span><b>{total('manual')}</b></div>
              </div>
            )}
            {batch.map((d) => (
              <BatchRow key={d.doc_id} doc={d} run={runs[d.doc_id]} maxChars={s?.max_chars} single={batch.length === 1}
                onPick={(rid) => { onPick(rid); onClose(); }} />
            ))}
            <div className="btn-row">
              {!started && <button type="button" className="btn" disabled={busy !== ''} onClick={reset}>换一批</button>}
              {!started && (
                <button type="button" className="btn primary" disabled={!canParse} onClick={() => doParse(batch)}>
                  {batch.length > 1 ? `AI 解析这 ${batch.length} 份` : 'AI 解析成规则草稿'}
                </button>
              )}
              {busy === 'parse' && <button type="button" className="btn" onClick={() => { stopRef.current = true; setStopping(true); }} disabled={stopping}>{stopping ? '将在当前这份完成后停止' : '当前这份完成后停止'}</button>}
              {started && busy === '' && <button type="button" className="btn" onClick={reset}>继续导入</button>}
            </div>
            {busy === 'parse' && <div className="small muted">逐份解析，长合同每份可能需要 1–2 分钟；请保持窗口打开。</div>}
          </div>
        )}

        {allDocs.length > 0 && (
          <details>
            <summary className="small muted" style={{ cursor: 'pointer' }}>已上传的依据文件（{allDocs.length}）</summary>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center', margin: '8px 0' }}>
              <button type="button" className="btn sm link" disabled={busy !== '' || !unparsed.length} onClick={() => setPick(new Set(unparsed.map((d) => d.doc_id)))}>选中未解析（{unparsed.length}）</button>
              {pick.size > 0 && <button type="button" className="btn sm link" disabled={busy !== ''} onClick={() => setPick(new Set())}>清除选择</button>}
              <button type="button" className="btn sm primary" disabled={!canParse || !pick.size} onClick={() => { setFailed([]); doParse(allDocs.filter((d) => pick.has(d.doc_id))); }}>解析选中（{pick.size}）</button>
            </div>
            {allDocs.map((d) => (
              <label key={d.doc_id} className="kvline" style={{ gridTemplateColumns: 'auto 1fr auto auto', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                <input type="checkbox" checked={pick.has(d.doc_id)} disabled={busy !== ''} onChange={() => togglePick(d.doc_id)} aria-label={`选择 ${d.doc_name}`} />
                <span>{d.doc_name}<span className="small muted"> · {d.doc_type} · {d.uploaded_at.slice(0, 16)}</span></span>
                <span className="small muted">{d.last_task ? `${d.last_task.status} · 草稿 ${d.last_task.draft_count} · 已发布 ${d.last_task.adoption.published}` : d.parse_status}</span>
                <button type="button" className="btn sm" disabled={!canParse} onClick={(e) => { e.preventDefault(); setFailed([]); doParse([d]); }}>{d.last_task ? '重新解析' : '解析'}</button>
              </label>
            ))}
          </details>
        )}
      </div>
    </Modal>
  );
}

/** 批量中的一份：状态 + 结果（单份时直接展开，多份时折叠）。 */
function BatchRow({ doc, run, maxChars, single, onPick }: { doc: Doc; run?: Run; maxChars?: number; single: boolean; onPick: (ruleId: number) => void }) {
  const out = run?.out;
  const tone = run?.state === 'ok' ? 'ok' : run?.state === 'fail' ? 'bad' : 'muted';
  const head = (
    <span style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', alignItems: 'baseline', justifyContent: 'space-between', width: '100%' }}>
      <span><b>{doc.doc_name}</b><span className="small muted"> · {doc.doc_type} · {fmt(doc.chars, 0)} 字{doc.page_count ? ` · ${doc.page_count} 页` : ''}</span></span>
      {run && <span className={'small ' + tone}>{RUN_LABEL[run.state]}{out ? ` · 草稿 ${out.created.length} · 不采纳 ${out.skipped.length} · 人工 ${out.manual.length}` : ''}</span>}
    </span>
  );
  const long = maxChars !== undefined && doc.chars > maxChars;
  const body = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }}>
      {run?.error && <div className="msg block">{run.error}</div>}
      {out?.truncated && <div className="notice">文件超长，本次只解析了前 {fmt(maxChars, 0)} 字。</div>}
      {out && out.created.length > 0 && <div className="sec-label">草稿（待确认，点击打开逐条对照原文）</div>}
      {out?.created.map((r) => (
        <button key={r.rule_id} type="button" className="kvline" style={{ gridTemplateColumns: '1fr auto', border: 'none', background: 'none', textAlign: 'left', color: 'var(--text)', cursor: 'pointer', padding: '4px 0' }}
          onClick={() => onPick(r.rule_id)}>
          <span><b>{r.name}</b> <span className="muted small">{r.item_code}</span>{r.low_fields.length > 0 && <span className="tag warn" style={{ marginLeft: 6 }}>需核对：{r.low_fields.join('、')}</span>}</span>
          <span className="muted num">置信度 {r.confidence.toFixed(2)}</span>
        </button>
      ))}
      {out && out.skipped.length > 0 && <div className="sec-label">不采纳</div>}
      {out?.skipped.map((x, i) => <div key={i} className="msg">· {x.clause_no} {x.name}：{x.reason}</div>)}
      {out && out.manual.length > 0 && <div className="sec-label">需人工计价（写不成公式）</div>}
      {out?.manual.map((x, i) => <div key={i} className="msg">· {x.clause_no}：{x.reason}{x.text ? `（${x.text.slice(0, 60)}）` : ''}</div>)}
    </div>
  );
  const card = { boxShadow: 'none', background: 'var(--fill)', padding: '10px 14px' };
  if (!out && !run?.error) {
    return (
      <div className="card" style={card}>
        {head}
        {!run && long && <div className="small muted" style={{ marginTop: 4 }}>超过 {fmt(maxChars, 0)} 字的部分本期不解析（分段解析在第二期）</div>}
      </div>
    );
  }
  return (
    <details className="card" style={card} open={single}>
      <summary style={{ cursor: 'pointer', listStyle: 'none' }}>{head}</summary>
      {body}
    </details>
  );
}
