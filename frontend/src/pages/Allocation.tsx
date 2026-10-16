import { useEffect, useState } from 'react';
import { api } from '../api';
import type { PageProps } from '../App';
import { Drawer, fmt, PageHead, useLoad, useToast, yuan } from '../ui';

interface Opts {
  items: { item_code: string; item_name: string; owner_dept: string }[]; bases: string[]; periods: string[];
  teams: { team_id: string; team_no: string; name: string }[];
}
interface Pool {
  pool_id: number; period: string; item_code: string; item_name: string; scope_team_id: string | null; team_no: string | null;
  basis: string; amount: number; note: string | null; status: string; created_by: string; created_at: string; n: number;
}
interface Share { team_id: string; well_id: string; w: number; amount: number; ratio: number }
interface Preview { total: number; basis_total: number; unit: string; shares: Share[] }
interface PoolDetail extends Pool { unit: string; total: number; events: { well_id: string; team_no: string | null; quantity: number; amount: number }[] }

const COLS = '0.5fr 0.8fr 1.8fr 0.9fr 0.6fr 1fr 0.6fr 0.9fr';

export default function Allocation(_: PageProps) {
  const toast = useToast();
  const opts = useLoad(() => api.get<Opts>('/api/allocation/options'), []);
  const pools = useLoad(() => api.get<Pool[]>('/api/allocation/pools'), []);
  const [f, setF] = useState({ period: '', item_code: '', basis: '队天', amount: '', scope_team_id: '', note: '' });
  const [pv, setPv] = useState<Preview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const detail = useLoad(() => (open === null ? Promise.resolve(null) : api.get<PoolDetail>(`/api/allocation/pools/${open}`)), [open]);

  useEffect(() => {
    if (opts.data && !f.period) setF((x) => ({ ...x, period: opts.data!.periods[0] || '', item_code: opts.data!.items[0]?.item_code || '' }));
  }, [opts.data, f.period]);

  const body = () => ({ ...f, amount: f.amount === '' ? null : Number(f.amount), scope_team_id: f.scope_team_id || null, note: f.note || null });
  useEffect(() => {
    setPv(null);
    setErr(null);
    if (!f.period || !f.item_code || !f.amount || Number(f.amount) <= 0) return;
    let alive = true;
    api.post<Preview>('/api/allocation/preview', body()).then((r) => alive && setPv(r)).catch((e: Error) => alive && setErr(e.message));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f.period, f.item_code, f.basis, f.amount, f.scope_team_id]);

  const submit = async () => {
    setBusy(true);
    try {
      const r = await api.post<{ shares: Share[]; batch: { created: number; reversed: number } }>('/api/allocation/pools', body());
      toast(`已分摊到 ${r.shares.length} 个队井；规则计算的同科目金额已冲销 ${r.batch.reversed} 笔`);
      setF((x) => ({ ...x, amount: '', note: '' }));
      pools.reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const voidPool = async (id: number) => {
    if (!window.confirm('作废后将冲销该费用池的全部分摊事项，并恢复规则计算。继续？')) return;
    try {
      const r = await api.post<{ reversed: number }>(`/api/allocation/pools/${id}/void`);
      toast(`已作废，冲销 ${r.reversed} 笔分摊事项`);
      setOpen(null);
      pools.reload();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const o = opts.data;
  const d = detail.data && detail.data.pool_id === open ? detail.data : null;
  return (
    <>
      <PageHead title="费用分摊" sub="月末财务取数形成费用池，按队天或进尺分到队、到井；分摊金额取代该井该月同科目的规则计算，尾差归最后一口井" />
      <section className="card" style={{ padding: 16 }}>
        <div className="form-grid">
          <label className="field">归属期间
            <select className="input" value={f.period} onChange={(e) => setF({ ...f, period: e.target.value })}>
              {o?.periods.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
          </label>
          <label className="field">科目（折旧、长摊、项目部运行、制造费）
            <select className="input" value={f.item_code} onChange={(e) => setF({ ...f, item_code: e.target.value })}>
              {o?.items.map((i) => <option key={i.item_code} value={i.item_code}>{i.item_name}</option>)}
            </select>
          </label>
          <label className="field">分摊标准
            <select className="input" value={f.basis} onChange={(e) => setF({ ...f, basis: e.target.value })}>
              {o?.bases.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
          </label>
          <label className="field">范围
            <select className="input" value={f.scope_team_id} onChange={(e) => setF({ ...f, scope_team_id: e.target.value })}>
              <option value="">全部在施队伍</option>
              {o?.teams.map((t) => <option key={t.team_id} value={t.team_id}>{t.team_no} {t.name}</option>)}
            </select>
          </label>
          <label className="field">费用池金额（元）
            <input className="input" inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />
          </label>
          <label className="field">备注
            <input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="如财务凭证号" />
          </label>
        </div>
        {err && <div className="notice" style={{ marginTop: 12 }}>{err}</div>}
        {pv && (
          <div style={{ marginTop: 14 }}>
            <div className="sec-label">分摊预览 · 池内合计 {fmt(pv.basis_total, 2)} {pv.unit}</div>
            {pv.shares.map((s) => (
              <div key={s.team_id + s.well_id} className="kvline" style={{ gridTemplateColumns: '1fr 1fr 1fr 1fr' }}>
                <span className="bold">{s.well_id}</span><span className="muted num">{fmt(s.w, 2)} {pv.unit}</span>
                <span className="muted num">{(s.ratio * 100).toFixed(2)}%</span><span className="num r">{yuan(s.amount)} 元</span>
              </div>
            ))}
          </div>
        )}
        <div className="btn-row" style={{ paddingTop: 14, justifyContent: 'flex-end' }}>
          <button type="button" className="btn primary" style={{ flex: '0 1 200px' }} disabled={busy || !pv} onClick={submit}>{busy ? '分摊中…' : '确认分摊'}</button>
        </div>
      </section>

      <section className="card list">
        <div className="scroll-x">
          <div style={{ minWidth: 860 }}>
            <div className="grid-row grid-head" style={{ gridTemplateColumns: COLS }}>
              <span>费用池</span><span>期间</span><span>科目</span><span>范围</span><span>标准</span><span className="r">金额（元）</span><span>状态</span><span>录入</span>
            </div>
            {pools.data?.length === 0 && <div className="empty">还没有费用池</div>}
            {pools.data?.map((p) => (
              <button key={p.pool_id} type="button" className="grid-row line" style={{ gridTemplateColumns: COLS }} onClick={() => setOpen(p.pool_id)}>
                <span className="bold num">#{p.pool_id}</span><span className="num">{p.period}</span><span>{p.item_name}</span>
                <span>{p.team_no ? p.team_no : '全部'}</span><span>{p.basis}</span><span className="r num">{yuan(p.amount)}</span>
                <span><span className={'tag ' + (p.status === '有效' ? 'good' : '')}>{p.status}</span></span><span className="muted">{p.created_by}</span>
              </button>
            ))}
          </div>
        </div>
      </section>

      <Drawer open={open !== null} onClose={() => setOpen(null)} label="费用池详情"
        head={<>
          <div className="hint">费用池 #{open}{d ? ` · ${d.period}` : ''}</div>
          <div style={{ fontSize: 24, fontWeight: 700 }}>{d ? d.item_name : '加载中…'}</div>
          {d && <div className="small muted">{d.status} · 按{d.basis}分摊 · {yuan(d.amount)} 元 · {d.created_by} {d.created_at.slice(0, 16)}{d.note ? ' · ' + d.note : ''}</div>}
        </>}
        foot={d?.status === '有效' ? <button type="button" className="btn danger" style={{ flex: 1 }} onClick={() => voidPool(d.pool_id)}>作废并冲销</button> : undefined}>
        {d && (
          <section className="sec">
            <div className="sec-label">分摊结果（有效事项合计 {yuan(d.total)} 元）</div>
            {d.events.length === 0 && <div className="hint">无有效事项</div>}
            {d.events.map((e, i) => (
              <div key={i} className="kvline" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                <span className="bold">{e.well_id}{e.team_no ? ` · ${e.team_no}` : ''}</span>
                <span className="muted num">{fmt(e.quantity, 2)} {d.unit}</span><span className="num r">{yuan(e.amount)} 元</span>
              </div>
            ))}
          </section>
        )}
      </Drawer>
    </>
  );
}
