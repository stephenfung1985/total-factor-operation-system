import { useRef, useState } from 'react';
import { api } from './api';
import { useLoad, useToast } from './ui';

const ACCEPT = '.xlsx,.xlsm,.csv,.tsv,.docx,.doc,.pdf,.txt,.md';

/** 填报页的“AI 解析”按钮：选文档或表格 → 后端抽取 → 把建议值交给页面填入表单（不保存，核对后仍走原提交流程）。 */
export function AiFillButton<T extends { notes: string[] }>({ url, extra, disabled, onResult }: {
  url: string; extra?: Record<string, string>; disabled?: boolean; onResult: (r: T) => void;
}) {
  const toast = useToast();
  const st = useLoad(() => api.get<{ enabled: boolean }>('/api/fill/ai-status'), []);
  const ref = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const off = st.data ? !st.data.enabled : false;

  const pick = async (f: File) => {
    const fd = new FormData();
    fd.append('file', f);
    Object.entries(extra || {}).forEach(([k, v]) => fd.append(k, v));
    setBusy(true);
    try {
      onResult(await api.post<T>(url, fd));
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
      if (ref.current) ref.current.value = '';
    }
  };
  return (
    <>
      <button type="button" className="btn" disabled={disabled || busy || off} onClick={() => ref.current?.click()}
        title={off ? '管理员未配置 AI 服务，请手工填报' : '上传 Excel / CSV / Word / PDF / TXT / Markdown，AI 识别后填入表单，核对后再提交'}>
        {busy ? 'AI 解析中…' : 'AI 解析导入'}
      </button>
      <input ref={ref} type="file" accept={ACCEPT} hidden onChange={(e) => e.target.files?.[0] && pick(e.target.files[0])} />
    </>
  );
}

export function AiNotes({ notes, onClose }: { notes: string[] | null; onClose: () => void }) {
  if (!notes) return null;
  return (
    <div className="notice info" style={{ display: 'flex', gap: 8, alignItems: 'flex-start', justifyContent: 'space-between' }}>
      <div>
        {notes.map((n, i) => <div key={i}>{n}</div>)}
      </div>
      <button type="button" className="btn sm link" onClick={onClose}>知道了</button>
    </div>
  );
}
