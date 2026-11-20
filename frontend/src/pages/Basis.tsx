import { useState } from 'react';
import { api } from '../api';
import type { PageProps } from '../App';
import { PageHead, useLoad, useToast } from '../ui';

interface BasisRow {
  basis_id: number; dept: string; title: string; note: string | null; submitted_by: string; submitted_at: string;
}

/** 归口部室提交变化依据。不写规则、不计算金额，日报也不在这里审核。 */
export default function Basis({ me }: PageProps) {
  const toast = useToast();
  const list = useLoad(() => api.get<BasisRow[]>('/api/basis'), []);
  const [title, setTitle] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      await api.post('/api/basis', { title: title.trim(), note: note.trim() });
      toast('已提交变化依据，发布规则时可以引用');
      setTitle('');
      setNote('');
      list.reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <PageHead title="变化依据" sub={`${me.scope_label}提交合同、补充协议、制度、调价通知或会议纪要。此处不写规则、不计算金额；日报不经部室审核。`} />
      <section className="card pad" style={{ gap: 12 }}>
        <label className="field">文号与名称 *
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="如：工程技术部〔2026〕12 号 定向服务价格调整通知" />
        </label>
        <label className="field">补充说明
          <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="选填" />
        </label>
        <div className="btn-row">
          <button type="button" className="btn primary" disabled={busy || title.trim().length < 4} onClick={submit}>{busy ? '提交中…' : '提交'}</button>
        </div>
      </section>
      <section className="card list">
        <div className="card-head"><h2>已提交</h2><div className="hint">{list.data?.length ?? 0} 条 · 仅本部室</div></div>
        {list.error && <div className="notice">{list.error}</div>}
        {(list.data || []).map((b) => (
          <div key={b.basis_id} className="grid-row" style={{ gridTemplateColumns: '11rem 1fr', minHeight: 48, borderBottom: '1px solid var(--sep2)' }}>
            <span className="muted small num">{b.submitted_at.slice(0, 16)}</span>
            <span><b>{b.title}</b>{b.note ? <span className="muted"> · {b.note}</span> : null}</span>
          </div>
        ))}
        {list.data && list.data.length === 0 && <div className="empty">还没有提交</div>}
      </section>
    </>
  );
}
