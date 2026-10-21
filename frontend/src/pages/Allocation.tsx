import { useEffect, useState } from 'react';
import { api } from '../api';
import type { PageProps } from '../App';
import { Drawer, fmt, PageHead, useLoad, useToast, yuan } from '../ui';

interface Opts { items: { item_code: string; item_name: string; owner_dept: string }[]; scopes: string[]; year: string; ref_rig_months: Record<string, number> }
interface Pool {
  pool_id: number; period: string; item_code: string; item_name: string; scope_team_id: string | null; team_no: string | null;
  scope_market: string | null; prev_rig_months: number | null;
  basis: string; amount: number; note: string | null; status: string; created_by: string; created_at: string; n: number;
}
interface Share { team_id: string; well_id: string; period: string; w: number; amount: number }
interface Preview { total: number; allocated: number; prev_rig_months: number; unit: string; rate: number; period: string; shares: Share[]; skipped: Share[] }
interface PoolDetail extends Pool { unit: string; total: number; rate: number | null; events: { period: string; well_id: string; team_no: string | null; quantity: number; amount: number }[] }

const BASIS_LABEL: Record<string, string> = { 队天: '按队天', 进尺: '按进尺', 台月: '元/台月' };
const ANNUAL = '台月';
const scopeOf = (p: Pool) => p.scope_market || p.team_no || '全部';

const COLS = '0.5fr 0.6fr 1.8fr 0.6fr 1.1fr 0.9fr 1fr 0.6fr 0.9fr';

export default function Allocation(_: PageProps) {
  const toast = useToast();
  const opts = useLoad(() => api.get<Opts>('/api/allocation/options'), []);
  const pools = useLoad(() => api.get<Pool[]>('/api/allocation/pools'), []);
  const [f, setF] = useState({ item_code: '', scope_market: '', amount: '', prev_rig_months: '', note: '' });
  const [pv, setPv] = useState<Preview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<number | null>(null);
  const detail = useLoad(() => (open === null ? Promise.resolve(null) : api.get<PoolDetail>(`/api/allocation/pools/${open}`)), [open]);

  const num = (v: string) => (v === '' ? null : Number(v));
  const body = () => ({ ...f, amount: num(f.amount), prev_rig_months: num(f.prev_rig_months), scope_market: f.scope_market || null, note: f.note || null });
  useEffect(() => {
    setPv(null);
    setErr(null);
    if (!f.item_code || !(Number(f.amount) > 0) || !(Number(f.prev_rig_months) > 0)) return;
    let alive = true;
    api.post<Preview>('/api/allocation/preview', body()).then((r) => alive && setPv(r)).catch((e: Error) => alive && setErr(e.message));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f.item_code, f.amount, f.prev_rig_months, f.scope_market]);

  const submit = async () => {
    setBusy(true);
    try {
      const r = await api.post<{ shares: Share[]; batch: { created: number; reversed: number } }>('/api/allocation/pools', body());
      toast(`已分摊到 ${r.shares.length} 个队井；规则计算的同科目金额已冲销 ${r.batch.reversed} 笔`);
      setF((x) => ({ ...x, amount: '', prev_rig_months: '', note: '' }));
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
  const ref = o?.ref_rig_months[f.scope_market];
  const depts = [...new Set(o?.items.map((i) => i.owner_dept || '其他'))];
  return (
    <>
      <PageHead title="费用分摊" sub={`分摊标准 = 上年该科目发生总额 ÷ 上年实际钻机台月；${o?.year ?? '本'} 年度沿用上年标准，按各井每月钻机台月分到队、到井，取代同科目规则计算`} />
      <section className="card" style={{ padding: 16 }}>
        <div className="form-grid">
          <label className="field">科目
            <select className="input" value={f.item_code} onChange={(e) => setF({ ...f, item_code: e.target.value })}>
              <option value="">请选择科目</option>
              {depts.map((g) => (
                <optgroup key={g} label={g}>
                  {o?.items.filter((i) => (i.owner_dept || '其他') === g).map((i) => <option key={i.item_code} value={i.item_code}>{i.item_code} {i.item_name}</option>)}
                </optgroup>
              ))}
            </select>
          </label>
          <label className="field">范围
            <select className="input" value={f.scope_market} onChange={(e) => setF({ ...f, scope_market: e.target.value })}>
              <option value="">全部</option>
              {o?.scopes.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </label>
          <label className="field">上年发生总额（元）
            <input className="input" inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />
          </label>
          <label className="field">上年实际钻机台月
            <input className="input" inputMode="decimal" value={f.prev_rig_months} onChange={(e) => setF({ ...f, prev_rig_months: e.target.value })}
              placeholder={ref ? `参考 ${fmt(ref, 2)}` : ''} />
            {ref !== undefined && (
              <span className="small muted" style={{ whiteSpace: 'nowrap' }} title="按施工时段自动算出的上年实际钻机台月，仅供参考">
                参考 {fmt(ref, 2)} 台月
                {ref > 0 && String(ref) !== f.prev_rig_months && (
                  <> · <a href="#" onClick={(e) => { e.preventDefault(); setF({ ...f, prev_rig_months: String(ref) }); }}>填入</a></>
                )}
              </span>
            )}
          </label>
          <label className="field">备注
            <input className="input" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} placeholder="如财务凭证号" />
          </label>
        </div>
        {err && <div className="notice" style={{ marginTop: 12 }}>{err}</div>}
        {pv && (
          <div style={{ marginTop: 14, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div className="formula">
              标准 {yuan(pv.rate)} 元/台月 = 上年发生总额 {yuan(pv.total)} 元 ÷ 上年实际钻机 {fmt(pv.prev_rig_months, 2)} 台月
              <div className="small muted" style={{ marginTop: 4 }}>{pv.period} 年各井每月分得 标准 × 当月台月；本次分摊 {yuan(pv.allocated)} 元{pv.skipped.length ? `，${pv.skipped.length} 个井月已有历史导入金额，不再分摊` : ''}</div>
            </div>
            {pv.shares.map((s) => (
              <div key={s.team_id + s.well_id + s.period} className="kvline" style={{ gridTemplateColumns: '0.8fr 1fr 1fr 1fr' }}>
                <span className="muted num">{s.period}</span>
                <span className="bold">{s.well_id}</span><span className="muted num">{fmt(s.w, 2)} {pv.unit}</span>
                <span className="num r">{yuan(s.amount)} 元</span>
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
              <span>费用池</span><span>年度</span><span>科目</span><span>范围</span><span>标准</span><span className="r">上年台月</span><span className="r">上年发生总额（元）</span><span>状态</span><span>录入</span>
            </div>
            {pools.data?.length === 0 && <div className="empty">还没有费用池</div>}
            {pools.data?.map((p) => (
              <button key={p.pool_id} type="button" className="grid-row line" style={{ gridTemplateColumns: COLS }} onClick={() => setOpen(p.pool_id)}>
                <span className="bold num">#{p.pool_id}</span><span className="num">{p.period}</span><span>{p.item_name}</span>
                <span>{scopeOf(p)}</span>
                <span className="num">{p.prev_rig_months ? `${yuan(p.amount / p.prev_rig_months)} 元/台月` : BASIS_LABEL[p.basis] || p.basis}</span>
                <span className="r num">{p.prev_rig_months ? fmt(p.prev_rig_months, 2) : '—'}</span><span className="r num">{yuan(p.amount)}</span>
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
          {d && <div className="small muted">{d.status} · {scopeOf(d)} · {BASIS_LABEL[d.basis] || d.basis}{d.basis === ANNUAL && d.rate ? ` · 标准 ${yuan(d.rate)} 元/台月` : ''} · {d.prev_rig_months ? `上年发生 ${yuan(d.amount)} 元 ÷ ${fmt(d.prev_rig_months, 2)} 台月` : `池 ${yuan(d.amount)} 元`} · {d.created_by} {d.created_at.slice(0, 16)}{d.note ? ' · ' + d.note : ''}</div>}
        </>}
        foot={d?.status === '有效' ? <button type="button" className="btn danger" style={{ flex: 1 }} onClick={() => voidPool(d.pool_id)}>作废并冲销</button> : undefined}>
        {d && (
          <section className="sec">
            <div className="sec-label">分摊结果（有效事项合计 {yuan(d.total)} 元）</div>
            {d.events.length === 0 && <div className="hint">无有效事项</div>}
            {d.events.map((e, i) => (
              <div key={i} className="kvline" style={{ gridTemplateColumns: '0.8fr 1.2fr 1fr 1fr' }}>
                <span className="muted num">{e.period}</span>
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
