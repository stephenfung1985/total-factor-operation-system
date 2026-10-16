import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';

// ---------- 数字格式 ----------

export function fmt(x: number | null | undefined, d = 2): string {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  return x.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}
/** 金额（元）一律保留整数；单价、数量、天数等非金额仍用 fmt。 */
export const yuan = (x: number | null | undefined) => fmt(x, 0);
export const wan = (x: number) => fmt(x / 1e4, 2);
export const pct = (a: number, b: number) => (b > 0 ? ((a / b) * 100).toFixed(1) : '—');
export const periodLabel = (p: string) => (p ? `${p.slice(0, 4)} 年 ${Number(p.slice(5, 7))} 月` : '');
export const today = () => new Date().toISOString().slice(0, 10);

// ---------- 图标 ----------

const S = { fill: 'none', stroke: 'currentColor', strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
export const Icon = {
  brand: () => <svg width="18" height="18" viewBox="0 0 24 24" {...S} strokeWidth="1.8"><path d="M12 3v18" /><path d="M7 7h10" /><path d="M8.5 11h7" /><path d="M10 15h4" /></svg>,
  moon: () => <svg width="18" height="18" viewBox="0 0 24 24" {...S} strokeWidth="1.8"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" /></svg>,
  sun: () => <svg width="18" height="18" viewBox="0 0 24 24" {...S} strokeWidth="1.8"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg>,
  chevron: () => <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="2" className="chev"><path d="M9 6l6 6-6 6" /></svg>,
  down: (n = 14) => <svg width={n} height={n} viewBox="0 0 24 24" {...S} strokeWidth="2.2" style={{ color: 'var(--text2)' }}><path d="M6 9l6 6 6-6" /></svg>,
  right: (n = 14) => <svg width={n} height={n} viewBox="0 0 24 24" {...S} strokeWidth="2.2" style={{ color: 'var(--text2)' }}><path d="M9 6l6 6-6 6" /></svg>,
  trash: () => <svg width="15" height="15" viewBox="0 0 24 24" {...S} strokeWidth="1.9"><path d="M4 7h16" /><path d="M10 11v6M14 11v6" /><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12" /><path d="M9 7V4h6v3" /></svg>,
  close: () => <svg width="14" height="14" viewBox="0 0 24 24" {...S} strokeWidth="2.2"><path d="M6 6l12 12" /><path d="M18 6L6 18" /></svg>,
  download: () => <svg width="16" height="16" viewBox="0 0 24 24" {...S} strokeWidth="2"><path d="M12 4v11" /><path d="M7 10l5 5 5-5" /><path d="M5 20h14" /></svg>,
  calendar: () => <svg width="14" height="14" viewBox="0 0 24 24" {...S} strokeWidth="1.8" style={{ color: 'var(--text2)' }}><rect x="3" y="5" width="18" height="16" rx="3" /><path d="M3 10h18" /><path d="M8 3v4" /><path d="M16 3v4" /></svg>,
  report: () => <svg width="18" height="18" viewBox="0 0 24 24" {...S} strokeWidth="1.7"><path d="M7 3h7l4 4v14H7z" /><path d="M14 3v4h4" /><path d="M10 12h5" /><path d="M10 16h5" /></svg>,
  overview: () => <svg width="18" height="18" viewBox="0 0 24 24" {...S} strokeWidth="1.7"><path d="M5 20V11" /><path d="M11 20V5" /><path d="M17 20v-6" /><path d="M3 20h18" /></svg>,
  rules: () => <svg width="18" height="18" viewBox="0 0 24 24" {...S} strokeWidth="1.7"><path d="M5 4h10a4 4 0 0 1 4 4v12H9a4 4 0 0 1-4-4z" /><path d="M9 9h6" /><path d="M9 13h6" /></svg>,
  perm: () => <svg width="18" height="18" viewBox="0 0 24 24" {...S} strokeWidth="1.7"><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" /><path d="M9 12l2 2 4-4" /></svg>,
  well: () => <svg width="18" height="18" viewBox="0 0 24 24" {...S} strokeWidth="1.7"><path d="M12 3l5 18" /><path d="M12 3L7 21" /><path d="M9 13h6" /><path d="M4 21h16" /></svg>,
  master: () => <svg width="18" height="18" viewBox="0 0 24 24" {...S} strokeWidth="1.7"><ellipse cx="12" cy="6" rx="7" ry="3" /><path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6" /><path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3" /></svg>,
  review: () => <svg width="18" height="18" viewBox="0 0 24 24" {...S} strokeWidth="1.7"><path d="M9 11l3 3 8-8" /><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9" /></svg>,
  bench: () => <svg width="18" height="18" viewBox="0 0 24 24" {...S} strokeWidth="1.7"><path d="M4 6h10" /><path d="M4 12h16" /><path d="M4 18h7" /><path d="M17 3v6" /></svg>,
  batch: () => <svg width="18" height="18" viewBox="0 0 24 24" {...S} strokeWidth="1.7"><path d="M21 12a9 9 0 1 1-3-6.7" /><path d="M21 4v5h-5" /></svg>,
};
export const NAV_ICON: Record<string, () => ReactNode> = {
  report: Icon.report, overview: Icon.overview, rules: Icon.rules, perm: Icon.perm, wellinfo: Icon.well,
  masterdata: Icon.master, review: Icon.review, batches: Icon.batch, wellreg: Icon.well, benchmark: Icon.bench, costdetail: Icon.report, allocation: Icon.batch,
};

// ---------- 通用组件 ----------

export function Seg<T extends string>({ value, options, onChange, large, label }: {
  value: T; options: { id: T; label: string }[]; onChange: (v: T) => void; large?: boolean; label?: string;
}) {
  return (
    <div className={'seg' + (large ? ' lg' : '')} role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.id} type="button" className={o.id === value ? 'on' : ''} aria-pressed={o.id === value} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Drawer({ open, onClose, wide, label, head, children, foot }: {
  open: boolean; onClose: () => void; wide?: boolean; label: string; head: ReactNode; children: ReactNode; foot?: ReactNode;
}) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <button type="button" className="scrim" aria-label={'关闭' + label} onClick={onClose} />
      <aside className={'drawer' + (wide ? ' wide' : '')} aria-label={label} role="dialog">
        <div className="drawer-head">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4, minWidth: 0 }}>{head}</div>
          <button type="button" className="close" aria-label="关闭" onClick={onClose}>{Icon.close()}</button>
        </div>
        <div className="drawer-body">{children}</div>
        {foot && <div className="drawer-foot">{foot}</div>}
      </aside>
    </>
  );
}

export function Modal({ open, onClose, title, children, wide, closable = true }: { open: boolean; onClose: () => void; title: string; children: ReactNode; wide?: boolean; closable?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <button type="button" className="scrim" aria-label="关闭" onClick={onClose} />
      <div className="modal" role="dialog" aria-label={title} style={wide ? { width: 920 } : undefined}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2>{title}</h2>
          {closable && <button type="button" className="close" aria-label="关闭" onClick={onClose}>{Icon.close()}</button>}
        </div>
        {children}
      </div>
    </>
  );
}

export interface KpiItem { label: string; value: string; unit: string; delta?: { text: string; tone: 'good' | 'bad' | 'flat' } }

export function Kpis({ items, flat, board }: { items: KpiItem[]; flat?: boolean; board?: boolean }) {
  return (
    <div className={'kpis' + (board ? ' board' : '')} style={flat ? { gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))' } : undefined}>
      {items.map((k) => (
        <div key={k.label} className={'kpi' + (flat ? ' flat' : '')}>
          <div className="k">{k.label}</div>
          <div className="v"><b>{k.value}</b><span>{k.unit}</span></div>
          {k.delta && <div className={'delta ' + k.delta.tone}>{k.delta.text}</div>}
        </div>
      ))}
    </div>
  );
}

// ---------- 提示 ----------

type ToastFn = (msg: string, err?: boolean) => void;
const ToastCtx = createContext<ToastFn>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastHost({ children }: { children: ReactNode }) {
  const [t, setT] = useState<{ msg: string; err: boolean } | null>(null);
  const timer = useRef<number>(0);
  const show = useCallback<ToastFn>((msg, err = false) => {
    setT({ msg, err });
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setT(null), err ? 5000 : 3000);
  }, []);
  return (
    <ToastCtx.Provider value={show}>
      {children}
      {t && <div className={'toast' + (t.err ? ' err' : '')} role="status">{t.msg}</div>}
    </ToastCtx.Provider>
  );
}

/** 简单的数据加载 hook：deps 变化时重新请求。 */
export function useLoad<T>(fn: () => Promise<T>, deps: unknown[]): { data: T | null; error: string | null; reload: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [n, setN] = useState(0);
  useEffect(() => {
    let alive = true;
    setError(null);
    fn().then((d) => alive && setData(d)).catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, n]);
  return { data, error, reload: () => setN((x) => x + 1) };
}

export interface Crumb { label: string; go?: () => void }

export function PageHead({ title, sub, crumbs, right }: { title: string; sub?: ReactNode; crumbs?: Crumb[]; right?: ReactNode }) {
  return (
    <header className="page-head">
      {crumbs && crumbs.length > 1 && (
        <nav className="crumbs" aria-label="下钻路径">
          {crumbs.map((c, i) => (
            <span key={i} style={{ display: 'contents' }}>
              {i > 0 && <span className="sep" aria-hidden="true">›</span>}
              {c.go ? <button type="button" onClick={c.go}>{c.label}</button> : <span className="cur">{c.label}</span>}
            </span>
          ))}
        </nav>
      )}
      <div className="title-row">
        <div>
          <h1>{title}</h1>
          {sub && <div className="sub">{sub}</div>}
        </div>
        {right}
      </div>
    </header>
  );
}

/** 期间选择：年、月分开选；allowYear 时月份可选“全年”（按月累加）。periods 为 YYYY-MM 列表。 */
export function PeriodPicker({ value, periods, onChange, allowYear }: { value: string; periods: string[]; onChange: (p: string) => void; allowYear?: boolean }) {
  const years = [...new Set(periods.map((p) => p.slice(0, 4)))].sort().reverse();
  const year = value.slice(0, 4);
  const isYear = value.length === 4;
  const months = periods.filter((p) => p.startsWith(year)).map((p) => p.slice(5)).sort().reverse();
  const pickYear = (y: string) => {
    const ms = periods.filter((p) => p.startsWith(y)).sort();
    if (isYear && allowYear) return onChange(y);
    const keep = y + '-' + value.slice(5);
    onChange(periods.includes(keep) ? keep : ms[ms.length - 1]);
  };
  return (
    <div className="pill period" role="group" aria-label="会计期间">
      {Icon.calendar()}
      <select aria-label="年份" value={year} onChange={(e) => pickYear(e.target.value)}>
        {years.map((y) => <option key={y} value={y}>{y} 年</option>)}
      </select>
      <select aria-label="月份" value={isYear ? 'all' : value.slice(5)} onChange={(e) => onChange(e.target.value === 'all' ? year : year + '-' + e.target.value)}>
        {allowYear && <option value="all">全年</option>}
        {months.map((m) => <option key={m} value={m}>{Number(m)} 月</option>)}
      </select>
    </div>
  );
}
