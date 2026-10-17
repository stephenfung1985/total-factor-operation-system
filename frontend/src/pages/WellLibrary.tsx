import { Fragment, useState } from 'react';
import { api } from '../api';
import { fmt, Icon, useLoad } from '../ui';

interface Lib {
  columns: { key: string; label: string }[]; rows: Record<string, unknown>[]; role: string; scope: string; staff_visible: boolean;
}
const NUM_COLS = new Set(['design_depth', 'pre_days', 'drill_days', 'completion_days', 'staff_total']);
const STATUS_TAG: Record<string, string> = { 计划: '', 正钻: 'info', 完钻: 'warn', 完井: 'good' };
const SEC = ['一开', '二开', '三开', '四开', '五开'];
const msLabel = (k: string) => {
  const m = /^(开钻|完钻)(\d)$/.exec(k);
  return m ? `${SEC[Number(m[2]) - 1]}${m[1]}` : k;
};

const store = {
  get: () => { try { return localStorage.getItem('tfos.wellLib.open') !== '0'; } catch { return true; } },
  set: (v: boolean) => { try { localStorage.setItem('tfos.wellLib.open', v ? '1' : '0'); } catch { /* 隐私模式下忽略 */ } },
};

/** 基础信息库：已登记各井的基础信息，可下拉 / 收起；字段与范围由后端按角色裁剪。onPick 用于基层队把某口井调入下方表单。 */
export function WellLibrary({ refresh, onPick, collapsible = true }: { refresh?: unknown; onPick?: (wellId: string) => void; collapsible?: boolean }) {
  const { data, error } = useLoad(() => api.get<Lib>('/api/wellinfo/library'), [refresh]);
  const [open, setOpen] = useState(collapsible ? store.get() : true);
  const [q, setQ] = useState('');
  const [sel, setSel] = useState<string | null>(null);
  const toggle = () => { setOpen(!open); store.set(!open); };
  const rows = (data?.rows || []).filter((r) => !q || Object.values(r).some((v) => typeof v === 'string' && v.includes(q)));
  const cell = (key: string, v: unknown) => {
    if (v === null || v === undefined || v === '') return '—';
    if (key === 'status') return <span className={'tag ' + (STATUS_TAG[String(v)] || '')}>{String(v)}</span>;
    if ((key === 'reg_lag' || key === 'comp_lag') && String(v).startsWith('滞后')) return <span className="tag bad">{String(v)}</span>;
    if (NUM_COLS.has(key) && typeof v === 'number') return fmt(v, key === 'staff_total' ? 0 : 2);
    if (key === 'spud_sections') return `${v} 开`;
    if (key === 'submitted_at') return String(v).slice(0, 16);
    return String(v);
  };
  return (
    <section className="card pad" aria-label="基础信息库">
      <div className="sec-head">
        {collapsible ? (
          <button type="button" className="btn link" style={{ padding: 0, minHeight: 0, gap: 6, display: 'inline-flex', alignItems: 'center' }}
            aria-expanded={open} onClick={toggle}>
            {open ? Icon.down(16) : Icon.right(16)}<h3 style={{ margin: 0 }}>基础信息库</h3>
          </button>
        ) : <h3>基础信息库</h3>}
        <span className="small muted">{data ? `${data.scope} · ${data.rows.length} 口井` : error || '加载中…'}</span>
      </div>
      {open && data && (
        <>
          <input className="input" style={{ maxWidth: 320 }} placeholder="按井号、区块、队号等筛选" aria-label="筛选基础信息库" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="scroll-x">
            <table className="ftable" style={{ minWidth: 720, whiteSpace: 'nowrap' }}>
              <thead><tr>{data.columns.map((c) => <th key={c.key} className={NUM_COLS.has(c.key) ? 'r' : ''}>{c.label}</th>)}</tr></thead>
              <tbody>
                {rows.length === 0 && <tr><td colSpan={data.columns.length} className="muted">没有符合条件的井</td></tr>}
                {rows.map((r) => {
                  const id = String(r.well_id);
                  const on = sel === id;
                  return (
                    <Fragment key={id}>
                      <tr style={{ cursor: 'pointer' }} tabIndex={0} aria-expanded={on} onClick={() => setSel(on ? null : id)}
                        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSel(on ? null : id); } }}>
                        {data.columns.map((c) => <td key={c.key} className={(NUM_COLS.has(c.key) ? 'r num' : '') + (c.key === 'well_id' ? ' bold' : '')}>{cell(c.key, r[c.key])}</td>)}
                      </tr>
                      {on && (
                        <tr className="sub"><td colSpan={data.columns.length}>
                          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 20px', alignItems: 'center' }}>
                            {Object.entries(r.milestones as Record<string, string>).map(([k, v]) => <span key={k} className="small"><span className="muted">{msLabel(k)}</span> {v}</span>)}
                            {Object.keys(r.milestones as object).length === 0 && <span className="small muted">暂无施工节点</span>}
                            {data.staff_visible && Object.entries((r.staff || {}) as Record<string, number>).map(([k, v]) => <span key={k} className="small"><span className="muted">{k}</span> {v} 人</span>)}
                            {onPick && <button type="button" className="btn sm primary" onClick={() => onPick(id)}>调入下方表单</button>}
                          </div>
                        </td></tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
