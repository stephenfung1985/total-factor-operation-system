import { api, type AiFillField, type AiFillTrace } from './api';
import { useLoad } from './ui';

const show = (v: string) => (v === '' ? '（空）' : v);

function TraceRows({ rows }: { rows: AiFillField[] }) {
  return (
    <>
      {rows.map((f) => (
        <div key={f.id} className="msg">
          <span className="bold">{f.field_label}</span>
          <span className={'tag ' + (f.modified ? 'warn' : 'good')} style={{ marginLeft: 6 }}>{f.modified ? '人工修改' : '采纳建议'}</span>
          <div>建议 {show(f.suggested)} → 提交 {show(f.submitted)}</div>
          <div className="small muted">任务 #{f.ai_task_id} · {f.operator} · {f.submitted_at.slice(0, 16)}</div>
        </div>
      ))}
    </>
  );
}

/** 某张日报或新开井登记提交后的字段级 AI 核对留痕。没有 AI 填过的字段时不占位置。 */
export function AiTracePanel({ objectType, objectId, refresh }: {
  objectType: '日报' | '新开井'; objectId: number | null; refresh?: string;
}) {
  const st = useLoad(
    () => (objectId == null
      ? Promise.resolve(null)
      : api.get<AiFillTrace>(`/api/fill/ai-trace?object_type=${encodeURIComponent(objectType)}&object_id=${objectId}`)),
    [objectType, objectId, refresh]);
  if (objectId == null) return null;
  if (st.error) return <div className="msg block">AI 核对留痕加载失败：{st.error}</div>;
  const data = st.data;
  if (!data || data.summary.fields === 0 || !data.latest[0]) return null;
  const s = data.summary;
  const head = data.latest[0];
  const older = [...new Set(data.fields.map((f) => f.capture_no))].filter((n) => n !== head.capture_no);
  const at = head.submitted_at.slice(0, 16);
  return (
    <details className="card pad" style={{ gap: 8 }} open={s.modified > 0}>
      <summary className="bold" style={{ cursor: 'pointer' }}>AI 核对留痕（{s.fields} 项，人工改过 {s.modified} 项）</summary>
      <div className="hint" style={{ whiteSpace: 'normal' }}>
        建议值来自解析任务，提交时与最终值对比。AI 不直接入库，这里只记人核对后的结果。
        {data.tasks.length > 0 && <div>{data.tasks.map((t) => `#${t.task_id} ${t.task_type} · ${t.created_by} · ${t.created_at.slice(0, 16)}`).join('；')}</div>}
        <div>本次提交 · {head.operator} · {at} · 采纳 {s.unchanged} 项</div>
      </div>
      <TraceRows rows={data.latest} />
      {older.length > 0 && (
        <details>
          <summary className="small" style={{ cursor: 'pointer' }}>更早的提交（{older.length} 次）</summary>
          {older.map((n) => <TraceRows key={n} rows={data.fields.filter((f) => f.capture_no === n)} />)}
        </details>
      )}
    </details>
  );
}
