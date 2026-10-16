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

/** 导入依据文件，交给 AI 拆条款、提规则草稿；草稿进入“待确认”，仍由管理端逐条对照原文确认、回算、发布。 */
export default function AiImport({ open, onClose, onDone, onPick }: { open: boolean; onClose: () => void; onDone: () => void; onPick: (ruleId: number) => void }) {
  const toast = useToast();
  const st = useLoad(() => (open ? api.get<AiStatus>('/api/ai/status') : Promise.resolve(null)), [open]);
  const docs = useLoad(() => (open ? api.get<Doc[]>('/api/ai/docs') : Promise.resolve(null)), [open]);
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [f, setF] = useState({ doc_type: '', doc_name: '', doc_no: '', counterparty: '', valid_from: '' });
  const [doc, setDoc] = useState<Doc | null>(null);
  const [out, setOut] = useState<ParseOut | null>(null);
  const [busy, setBusy] = useState<'' | 'upload' | 'parse'>('');

  useEffect(() => {
    if (!open) return;
    setFile(null); setDoc(null); setOut(null);
    setF({ doc_type: '', doc_name: '', doc_no: '', counterparty: '', valid_from: '' });
  }, [open]);

  const doUpload = async () => {
    if (!file) return;
    const fd = new FormData();
    fd.append('file', file);
    Object.entries(f).forEach(([k, v]) => fd.append(k, v));
    setBusy('upload');
    try {
      const d = await api.post<Doc>('/api/ai/docs', fd);
      setDoc(d);
      docs.reload();
      toast(`已上传：提取 ${fmt(d.chars, 0)} 字${d.page_count ? `，${d.page_count} 页` : ''}`);
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy('');
    }
  };
  const doParse = async (d: Doc) => {
    setDoc(d);
    setOut(null);
    setBusy('parse');
    try {
      const r = await api.post<ParseOut>(`/api/ai/docs/${d.doc_id}/parse`);
      setOut(r);
      onDone();
      toast(`AI 解析完成：生成草稿 ${r.created.length} 条，不采纳 ${r.skipped.length} 条，需人工计价 ${r.manual.length} 条`);
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy('');
      docs.reload();
    }
  };

  const s = st.data;
  return (
    <Modal open={open} onClose={onClose} title="导入依据文件 · AI 解析" wide>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {s && !s.enabled && <div className="notice">未配置 AI 服务：文件可以先上传存档，解析需在部署机的 ~/tfos-data/env 设置 TFOS_AI_BASE_URL、TFOS_AI_MODEL（可选 TFOS_AI_API_KEY）后重启。</div>}
        {s?.enabled && <div className="hint" style={{ whiteSpace: 'normal' }}>模型 {s.model} · {s.endpoint} · 提示词 {s.prompt_version}。AI 只生成“待确认”草稿，每条附原文出处；确认、回算、发布仍按现有流程。</div>}

        {!doc && (
          <>
            <div className="form-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))' }}>
              <div className="field"><span>文件 *（PDF 文字版、Word .docx、TXT）</span>
                <button type="button" className="btn" onClick={() => fileRef.current?.click()}>{file ? file.name : '选择文件'}</button>
                <input ref={fileRef} type="file" accept=".pdf,.docx,.txt" hidden onChange={(e) => { const x = e.target.files?.[0]; if (x) { setFile(x); setF((v) => ({ ...v, doc_name: v.doc_name || x.name.replace(/\.[^.]+$/, '') })); } e.target.value = ''; }} />
              </div>
              <label className="field">文件类型 *
                <select className="input" value={f.doc_type} onChange={(e) => setF({ ...f, doc_type: e.target.value })}>
                  <option value="">请选择</option>
                  {s?.doc_types.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              <label className="field">文件名称<input className="input" value={f.doc_name} onChange={(e) => setF({ ...f, doc_name: e.target.value })} /></label>
              <label className="field">文号<input className="input" value={f.doc_no} onChange={(e) => setF({ ...f, doc_no: e.target.value })} placeholder="如 DX-2026-07" /></label>
              <label className="field">对方单位 / 提供部室<input className="input" value={f.counterparty} onChange={(e) => setF({ ...f, counterparty: e.target.value })} /></label>
              <label className="field">生效日<input className="input" type="date" value={f.valid_from} onChange={(e) => setF({ ...f, valid_from: e.target.value })} /></label>
            </div>
            <div className="btn-row">
              <button type="button" className="btn primary" disabled={!file || !f.doc_type || busy !== ''} onClick={doUpload}>{busy === 'upload' ? '上传中…' : '上传并提取文字'}</button>
            </div>
          </>
        )}

        {doc && !out && (
          <div className="card pad" style={{ gap: 8, boxShadow: 'none', background: 'var(--fill)' }}>
            <div className="bold">{doc.doc_name}<span className="small muted"> · {doc.doc_type}{doc.doc_no ? ` · ${doc.doc_no}` : ''}</span></div>
            <div className="small muted">已提取 {fmt(doc.chars, 0)} 字{doc.page_count ? ` · ${doc.page_count} 页` : ''}{s && doc.chars > s.max_chars ? `；超过 ${fmt(s.max_chars, 0)} 字的部分本期不解析（分段解析在第二期）` : ''}</div>
            <div className="btn-row">
              <button type="button" className="btn" onClick={() => setDoc(null)}>换一份</button>
              <button type="button" className="btn primary" disabled={!s?.enabled || busy !== ''} onClick={() => doParse(doc)}>{busy === 'parse' ? 'AI 解析中…（长合同可能需要 1–2 分钟）' : 'AI 解析成规则草稿'}</button>
            </div>
          </div>
        )}

        {out && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <div className="stat-pair" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
              <div className="stat"><span>生成草稿</span><b>{out.created.length}</b></div>
              <div className="stat"><span>不采纳</span><b>{out.skipped.length}</b></div>
              <div className="stat"><span>需人工计价</span><b>{out.manual.length}</b></div>
            </div>
            {out.truncated && <div className="notice">文件超长，本次只解析了前 {fmt(s?.max_chars, 0)} 字。</div>}
            {out.created.length > 0 && <div className="sec-label">草稿（待确认，点击打开逐条对照原文）</div>}
            {out.created.map((r) => (
              <button key={r.rule_id} type="button" className="kvline" style={{ gridTemplateColumns: '1fr auto', border: 'none', background: 'none', textAlign: 'left', color: 'var(--text)', cursor: 'pointer', padding: '4px 0' }}
                onClick={() => { onPick(r.rule_id); onClose(); }}>
                <span><b>{r.name}</b> <span className="muted small">{r.item_code}</span>{r.low_fields.length > 0 && <span className="tag warn" style={{ marginLeft: 6 }}>需核对：{r.low_fields.join('、')}</span>}</span>
                <span className="muted num">置信度 {r.confidence.toFixed(2)}</span>
              </button>
            ))}
            {out.skipped.length > 0 && <div className="sec-label">不采纳</div>}
            {out.skipped.map((x, i) => <div key={i} className="msg">· {x.clause_no} {x.name}：{x.reason}</div>)}
            {out.manual.length > 0 && <div className="sec-label">需人工计价（写不成公式）</div>}
            {out.manual.map((x, i) => <div key={i} className="msg">· {x.clause_no}：{x.reason}{x.text ? `（${x.text.slice(0, 60)}）` : ''}</div>)}
            <div className="btn-row"><button type="button" className="btn" onClick={() => { setDoc(null); setOut(null); setFile(null); }}>继续导入</button></div>
          </div>
        )}

        {docs.data && docs.data.length > 0 && (
          <details>
            <summary className="small muted" style={{ cursor: 'pointer' }}>已上传的依据文件（{docs.data.length}）</summary>
            {docs.data.map((d) => (
              <div key={d.doc_id} className="kvline" style={{ gridTemplateColumns: '1fr auto auto', alignItems: 'center', gap: 8 }}>
                <span>{d.doc_name}<span className="small muted"> · {d.doc_type} · {d.uploaded_at.slice(0, 16)}</span></span>
                <span className="small muted">{d.last_task ? `${d.last_task.status} · 草稿 ${d.last_task.draft_count} · 已发布 ${d.last_task.adoption.published}` : d.parse_status}</span>
                <button type="button" className="btn sm" disabled={!s?.enabled || busy !== ''} onClick={() => doParse(d)}>{d.last_task ? '重新解析' : '解析'}</button>
              </div>
            ))}
          </details>
        )}
      </div>
    </Modal>
  );
}
