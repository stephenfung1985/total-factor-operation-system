import { useState } from 'react';
import { api } from '../api';
import type { PageProps } from '../App';
import { PageHead, useLoad, useToast, yuan } from '../ui';

interface Batch {
  batch_id: number; trigger: string; scope: string; engine_version: string; started_at: string; finished_at: string | null;
  event_count: number; amount_change: number; operator: string;
}
const COLS = '0.5fr 1.2fr 1.6fr 0.7fr 1fr 1.2fr 1fr';

export default function Batches(_: PageProps) {
  const toast = useToast();
  const { data, reload } = useLoad(() => api.get<Batch[]>('/api/batches'), []);
  const [busy, setBusy] = useState(false);
  const recalc = async () => {
    setBusy(true);
    try {
      const r = await api.post<{ created: number; reversed: number; amount_change: number }>('/api/recalc');
      toast(`手工重算完成：新增 ${r.created} 笔，冲销 ${r.reversed} 笔，金额变动 ${yuan(r.amount_change)} 元`);
      reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const scope = (s: string) => {
    try {
      const j = JSON.parse(s);
      return [j.wells && `${j.wells.length} 口井`, j.periods && j.periods.join('、'), j.period].filter(Boolean).join(' · ');
    } catch {
      return s;
    }
  };
  return (
    <>
      <PageHead title="历史记录" sub="事实审核通过、规则发布、手工重算都会生成批次；金额不变的事项保留，变化的先冲销再重算"
        right={<button type="button" className="btn primary" disabled={busy} onClick={recalc}>{busy ? '重算中…' : '手工重算'}</button>} />
      <section className="card list">
        <div className="scroll-x">
          <div style={{ minWidth: 860 }}>
            <div className="grid-row grid-head" style={{ gridTemplateColumns: COLS }}>
              <span>批次</span><span>触发</span><span>范围</span><span className="r">事项数</span><span className="r">金额变动（元）</span><span>时间</span><span>触发人</span>
            </div>
            {data?.map((b) => (
              <div key={b.batch_id} className="grid-row line" style={{ gridTemplateColumns: COLS }}>
                <span className="bold num">#{b.batch_id}</span><span>{b.trigger}</span><span className="text3">{scope(b.scope)}</span>
                <span className="r num">{b.event_count}</span><span className={'r num' + (b.amount_change < 0 ? ' neg' : '')}>{yuan(b.amount_change)}</span>
                <span className="muted num">{b.started_at.slice(0, 16)}</span><span>{b.operator}</span>
              </div>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
