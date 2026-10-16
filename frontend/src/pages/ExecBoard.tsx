import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { Overview as OV, OverviewWell } from '../api';
import { fmt, wan } from '../ui';
import type { Dim, DimFilter } from './OverviewCharts';

/**
 * 经营总览首屏（决策层）：利润瀑布、成本 × 利润率四象限、非生产时间、亏损井。
 * 视觉遵循 dataviz 规范：细柱（≤ 24px、数据端 4px 圆角、基线方角）、实线细网格、选择性直接标注、
 * 悬停提示（数值在前、名称在后）、每张图附屏幕阅读器可读的数据表；颜色只用强调蓝 + 中性灰，盈亏用状态色并配文字。
 */

const NPT = ['待命', '复杂情况'];
const RIG_HOURS = 720;   // 1 台月按 30 天 × 24 小时折算
const QUAD_DIMS: { id: Dim; label: string }[] = [{ id: 'team', label: '队伍' }, { id: 'block', label: '区块' }, { id: 'market', label: '市场' }];

function useWidth(min = 280): [React.RefObject<HTMLDivElement | null>, number] {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(560);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(min, Math.floor(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, [min]);
  return [ref, w];
}

interface Tip { x: number; y: number; value: string; label: string; sub?: string }

function Tooltip({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  return (
    <div className="xtip" style={{ left: tip.x, top: tip.y }} role="status">
      <b>{tip.value}</b><span>{tip.label}</span>{tip.sub && <em>{tip.sub}</em>}
    </div>
  );
}

function niceStep(span: number, n = 4) {
  const raw = span / n;
  const p = 10 ** Math.floor(Math.log10(raw || 1));
  const m = raw / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}

/** 刻度：外扩到整步长，保证首末刻度包住全部数据（数据不会画出坐标区）。 */
function ticks(lo: number, hi: number, n = 4): number[] {
  if (hi - lo < 1e-9) hi = lo + 1;
  const s = niceStep(hi - lo, n);
  const out = [];
  for (let v = Math.floor(lo / s + 1e-9) * s; v <= Math.ceil(hi / s - 1e-9) * s + s * 1e-6; v += s) out.push(Math.abs(v) < s * 1e-6 ? 0 : v);
  return out;
}

/** 柱形：数据端 4px 圆角，基线端方角。 */
function bar(x: number, w: number, yBase: number, yEnd: number) {
  const r = Math.min(4, Math.abs(yEnd - yBase) / 2, w / 2);
  if (yEnd <= yBase) {   // 向上
    return `M${x},${yBase} V${yEnd + r} Q${x},${yEnd} ${x + r},${yEnd} H${x + w - r} Q${x + w},${yEnd} ${x + w},${yEnd + r} V${yBase} Z`;
  }
  return `M${x},${yBase} V${yEnd - r} Q${x},${yEnd} ${x + r},${yEnd} H${x + w - r} Q${x + w},${yEnd} ${x + w},${yEnd - r} V${yBase} Z`;
}

const SrTable = ({ caption, head, rows }: { caption: string; head: string[]; rows: (string | number)[][] }) => (
  <div className="sr-only">
    <table>
      <caption>{caption}</caption>
      <thead><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
    </table>
  </div>
);

export default function ExecBoard({ data, open, filter, onFilter }: {
  data: OV; open: (w: string, period?: string) => void; filter: DimFilter | null; onFilter: (f: DimFilter | null) => void;
}) {
  return (
    <section className="exec" aria-label="决策层经营概览">
      <Waterfall data={data} />
      <Quadrant wells={data.wells} filter={filter} onFilter={onFilter} />
      <NptCard data={data} />
      <LossWells wells={data.wells} open={open} />
    </section>
  );
}

// ---------- ② 利润瀑布 ----------

function Waterfall({ data }: { data: OV }) {
  const [ref, W] = useWidth();
  const [tip, setTip] = useState<Tip | null>(null);
  const s = data.sum;
  const gm = s.revenue - s.variable;
  const net = gm - s.fixed;
  const steps = [
    { k: '形象收入', short: '收入', a: 0, b: s.revenue, kind: 'total' },
    { k: '变动成本', short: '变动', a: s.revenue, b: gm, kind: 'cost' },
    { k: '毛益额', short: '毛益', a: 0, b: gm, kind: 'total' },
    { k: '固定成本', short: '固定', a: gm, b: net, kind: 'cost' },
    { k: '净利润', short: '净利', a: 0, b: net, kind: 'net' },
  ];
  const H = 280;
  const P = { l: 56, r: 12, t: 28, b: 34 };
  const vals = steps.flatMap((x) => [x.a, x.b]);
  const vmin = Math.min(0, ...vals);
  const vmax = Math.max(0, ...vals);
  const pad = (vmax - vmin) * 0.12;   // 给柱端数值标签留空间
  const tk = ticks(vmin < 0 ? vmin - pad : 0, vmax > 0 ? vmax + pad : 0);
  const lo = Math.min(tk[0], 0);
  const hi = tk[tk.length - 1];
  const y = (v: number) => P.t + ((hi - v) / (hi - lo || 1)) * (H - P.t - P.b);
  const band = (W - P.l - P.r) / steps.length;
  const bw = Math.min(24, band * 0.5);
  const narrow = band < 84;   // 窄屏：类别用简称，数值只标收入、毛益、净利润（成本扣减看提示和数据表）
  const label = (v: number) => (narrow ? fmt(v / 1e4, 0) : wan(v));
  const color = (kind: string, v: number) => (kind === 'cost' ? 'var(--chart-neutral)' : kind === 'net' ? (v >= 0 ? 'var(--good)' : 'var(--bad)') : 'var(--accent)');
  const ratio = (v: number) => (s.revenue ? `占形象收入 ${((v / s.revenue) * 100).toFixed(1)}%` : undefined);
  return (
    <div className="card pad exec-card">
      <div className="sec-head">
        <h3>利润瀑布<small>万元</small></h3>
        <span className="legend"><span><i style={{ background: 'var(--accent)' }} />收入与毛益</span><span><i style={{ background: 'var(--chart-neutral)' }} />成本扣减</span></span>
      </div>
      <div ref={ref} className="xchart" onMouseLeave={() => setTip(null)}>
        <svg width={W} height={H} role="img" aria-label={`利润瀑布：形象收入 ${wan(s.revenue)} 万元，净利润 ${wan(net)} 万元`}>
          {tk.map((t) => (
            <g key={t}>
              <line x1={P.l} x2={W - P.r} y1={y(t)} y2={y(t)} className={t === 0 ? 'xbase' : 'xgrid'} />
              <text x={P.l - 8} y={y(t) + 4} textAnchor="end" className="xtick">{wan(t).replace(/\.00$/, '')}</text>
            </g>
          ))}
          {steps.map((st, i) => {
            const x = P.l + band * i + (band - bw) / 2;
            const v = st.kind === 'cost' ? st.a - st.b : st.b;
            const yA = y(st.a);
            const yB = y(st.b);
            const up = st.b >= st.a;
            const next = steps[i + 1];
            return (
              <g key={st.k} tabIndex={0} className="xmark"
                onMouseMove={(e) => setTip({ x: e.nativeEvent.offsetX, y: e.nativeEvent.offsetY, value: `${st.kind === 'cost' ? '−' : ''}${wan(v)} 万元`, label: st.k, sub: ratio(v) })}
                onFocus={() => setTip({ x: x + bw / 2, y: Math.min(yA, yB), value: `${wan(v)} 万元`, label: st.k, sub: ratio(v) })}
                onBlur={() => setTip(null)}>
                <rect x={P.l + band * i} y={P.t} width={band} height={H - P.t - P.b} fill="transparent" />
                <path d={bar(x, bw, up ? yA : yA, yB)} fill={color(st.kind, st.b)} />
                {(!narrow || st.kind !== 'cost') && (
                  <text x={x + bw / 2} y={up ? Math.min(yA, yB) - 8 : Math.max(yA, yB) + 15} textAnchor="middle" className="xval">
                    {st.kind === 'cost' ? '−' : ''}{label(v)}
                  </text>
                )}
                <text x={x + bw / 2} y={H - 10} textAnchor="middle" className="xcat">{narrow ? st.short : st.k}</text>
                {next && <line x1={x + bw} x2={P.l + band * (i + 1) + (band - bw) / 2} y1={yB} y2={yB} className="xlink" />}
              </g>
            );
          })}
        </svg>
        <Tooltip tip={tip} />
      </div>
      <SrTable caption="利润瀑布（万元）" head={['项目', '金额']} rows={steps.map((st) => [st.k, wan(st.kind === 'cost' ? st.a - st.b : st.b)])} />
    </div>
  );
}

// ---------- ③ 成本 × 利润率四象限 ----------

interface Pt { name: string; x: number; y: number; footage: number; wells: number; revenue: number; net: number }

function Quadrant({ wells, filter, onFilter }: { wells: OverviewWell[]; filter: DimFilter | null; onFilter: (f: DimFilter | null) => void }) {
  const [dim, setDim] = useState<Dim>('team');
  const [ref, W] = useWidth();
  const [tip, setTip] = useState<Tip | null>(null);
  const { pts, skipped, ax, ay } = useMemo(() => {
    const g = new Map<string, { cf: number; f: number; rev: number; net: number; n: number }>();
    for (const w of wells) {
      const k = (w[dim] as string) || '—';
      const o = g.get(k) || { cf: 0, f: 0, rev: 0, net: 0, n: 0 };
      o.cf += w.cost_with_footage; o.f += w.footage; o.rev += w.revenue; o.net += w.net_profit; o.n += 1;
      g.set(k, o);
    }
    const all = [...g.entries()];
    const ok = all.filter(([, o]) => o.f > 0 && o.rev > 0);
    const tot = ok.reduce((a, [, o]) => ({ cf: a.cf + o.cf, f: a.f + o.f, rev: a.rev + o.rev, net: a.net + o.net }), { cf: 0, f: 0, rev: 0, net: 0 });
    return {
      pts: ok.map(([name, o]) => ({ name, x: o.cf / o.f, y: o.net / o.rev, footage: o.f, wells: o.n, revenue: o.rev, net: o.net })) as Pt[],
      skipped: all.length - ok.length,
      ax: tot.f ? tot.cf / tot.f : 0, ay: tot.rev ? tot.net / tot.rev : 0,
    };
  }, [wells, dim]);
  const H = 300;
  const P = { l: 52, r: 20, t: 18, b: 40 };
  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  const xspan = Math.max(...xs, ax) - Math.min(...xs, ax) || Math.max(ax, 1) * 0.2;
  const yspan = Math.max(...ys, ay, 0) - Math.min(...ys, ay, 0) || 0.2;
  const xt = ticks(Math.min(...xs, ax) - xspan * 0.2, Math.max(...xs, ax) + xspan * 0.2);
  const yt = ticks(Math.min(...ys, ay, 0) - yspan * 0.15, Math.max(...ys, ay, 0) + yspan * 0.15);
  const [x0, x1, y0, y1] = [xt[0], xt[xt.length - 1], yt[0], yt[yt.length - 1]];
  const sx = (v: number) => P.l + ((v - x0) / (x1 - x0 || 1)) * (W - P.l - P.r);
  const sy = (v: number) => P.t + ((y1 - v) / (y1 - y0 || 1)) * (H - P.t - P.b);
  const maxF = Math.max(...pts.map((p) => p.footage), 1);
  const rad = (f: number) => 6 + 16 * Math.sqrt(f / maxF);
  const flag = (p: Pt) => p.x > ax && p.y < ay;
  const labelAll = pts.length <= 8;
  // 直接标注：放在气泡右侧，越界则放左侧；与已放置的标签重叠就不标（名称仍在提示和数据表里）
  const labels = useMemo(() => {
    const placed: { x0: number; x1: number; y0: number; y1: number }[] = [];
    const out = new Map<string, { x: number; y: number; anchor: 'start' | 'end' }>();
    const order = [...pts].sort((a, b) => Number(flag(b)) - Number(flag(a)) || b.footage - a.footage);
    for (const p of order) {
      if (!labelAll && !flag(p)) continue;
      const cx = sx(p.x);
      const cy = sy(p.y);
      const r = rad(p.footage);
      const w = p.name.length * 12 + 4;
      const right = cx + r + 6 + w <= W - 4;
      const x0 = right ? cx + r + 6 : cx - r - 6 - w;
      const box = { x0, x1: x0 + w, y0: cy - 9, y1: cy + 9 };
      const hit = (b: { x0: number; x1: number; y0: number; y1: number }) => b.x0 < box.x1 && box.x0 < b.x1 && b.y0 < box.y1 && box.y0 < b.y1;
      const bubbles = pts.filter((q) => q !== p).map((q) => {
        const qr = rad(q.footage);
        return { x0: sx(q.x) - qr, x1: sx(q.x) + qr, y0: sy(q.y) - qr, y1: sy(q.y) + qr };
      });
      if (placed.some(hit) || bubbles.some(hit)) continue;   // 标签不压别的标签，也不压别的气泡
      placed.push(box);
      out.set(p.name, { x: right ? cx + r + 6 : cx - r - 6, y: cy + 4, anchor: right ? 'start' : 'end' });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pts, W, labelAll, ax, ay]);
  const pick = (p: Pt) => onFilter(filter?.dim === dim && filter.value === p.name ? null : { dim, value: p.name });
  const show = (p: Pt, x: number, y: number) => setTip({ x, y, value: `${fmt(p.x, 0)} 元/米 · 利润率 ${(p.y * 100).toFixed(1)}%`, label: p.name,
    sub: `进尺 ${fmt(p.footage, 0)} 米 · ${p.wells} 口井 · 净利润 ${wan(p.net)} 万元` });
  return (
    <div className="card pad exec-card exec-wide">
      <div className="sec-head">
        <h3>每米成本 × 利润率<small>气泡大小 = 进尺 · 点击筛选下方井列表</small></h3>
        <div className="seg" role="group" aria-label="对比维度">
          {QUAD_DIMS.map((d) => <button key={d.id} type="button" className={dim === d.id ? 'on' : ''} aria-pressed={dim === d.id} onClick={() => setDim(d.id)}>{d.label}</button>)}
        </div>
      </div>
      {pts.length === 0 ? <div className="empty">本期间没有进尺数据（进尺来自已生效的成本日报）</div> : (
        <div ref={ref} className="xchart" onMouseLeave={() => setTip(null)}>
          <svg width={W} height={H} role="img" aria-label={`${pts.length} 个${QUAD_DIMS.find((d) => d.id === dim)?.label}的每米成本与利润率`}>
            {yt.map((t) => (
              <g key={'y' + t}>
                <line x1={P.l} x2={W - P.r} y1={sy(t)} y2={sy(t)} className={t === 0 ? 'xbase' : 'xgrid'} />
                <text x={P.l - 8} y={sy(t) + 4} textAnchor="end" className="xtick">{Math.round(t * 100)}%</text>
              </g>
            ))}
            {xt.map((t) => <text key={'x' + t} x={sx(t)} y={H - P.b + 18} textAnchor="middle" className="xtick">{fmt(t, 0)}</text>)}
            <text x={W - P.r} y={H - 6} textAnchor="end" className="xtick">元/米</text>
            <line x1={sx(ax)} x2={sx(ax)} y1={P.t} y2={H - P.b} className="xavg" />
            <line x1={P.l} x2={W - P.r} y1={sy(ay)} y2={sy(ay)} className="xavg" />
            <text x={sx(ax) + 6} y={P.t + 10} className="xquad">平均 {fmt(ax, 0)} 元/米</text>
            <text x={P.l + 6} y={P.t + 10} className="xquad">低成本 · 高利润</text>
            <text x={W - P.r - 6} y={H - P.b - 8} textAnchor="end" className="xquad">高成本 · 低利润</text>
            {[...pts].sort((a, b) => b.footage - a.footage).map((p) => {
              const cx = sx(p.x);
              const cy = sy(p.y);
              const r = rad(p.footage);
              const on = filter?.dim === dim && filter.value === p.name;
              return (
                <g key={p.name} className="xmark xdot" tabIndex={0} role="button" aria-pressed={on} aria-label={`${p.name}：每米成本 ${fmt(p.x, 0)} 元，利润率 ${(p.y * 100).toFixed(1)}%`}
                  onMouseMove={(e) => show(p, e.nativeEvent.offsetX, e.nativeEvent.offsetY)} onFocus={() => show(p, cx, cy - r)} onBlur={() => setTip(null)}
                  onClick={() => pick(p)} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(p); } }}>
                  <circle cx={cx} cy={cy} r={Math.max(r, 12)} fill="transparent" />
                  <circle cx={cx} cy={cy} r={r} fill={flag(p) ? 'var(--bad)' : 'var(--accent)'} fillOpacity={0.82} stroke={on ? 'var(--text)' : 'var(--card)'} strokeWidth={2} />
                  {labels.has(p.name) && <text x={labels.get(p.name)!.x} y={labels.get(p.name)!.y} textAnchor={labels.get(p.name)!.anchor} className="xlabel">{p.name}</text>}
                </g>
              );
            })}
          </svg>
          <Tooltip tip={tip} />
        </div>
      )}
      <div className="xfoot">
        <span className="legend"><span><i className="dot" style={{ background: 'var(--bad)' }} />需关注：每米成本高于平均且利润率低于平均</span><span><i className="dot" style={{ background: 'var(--accent)' }} />其余</span></span>
        {skipped > 0 && <span className="small muted">{skipped} 个无进尺或无收入，未列入</span>}
      </div>
      <SrTable caption="每米成本与利润率" head={['对象', '每米成本（元/米）', '利润率', '进尺（米）', '井数']}
        rows={pts.map((p) => [p.name, fmt(p.x, 0), `${(p.y * 100).toFixed(1)}%`, fmt(p.footage, 0), p.wells])} />
    </div>
  );
}

// ---------- ④ 非生产时间 ----------

function NptCard({ data }: { data: OV }) {
  const s = data.sum;
  const rows = Object.entries(s.hours || {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const max = Math.max(...rows.map(([, v]) => v), 1);
  const share = s.hours_total ? s.npt_hours / s.hours_total : 0;
  const perHour = s.rig_months ? s.cost_with_rig / s.rig_months / RIG_HOURS : 0;
  return (
    <div className="card pad exec-card">
      <div className="sec-head"><h3>时间去向<small>小时 · 来自成本日报工况</small></h3></div>
      {rows.length === 0 ? <div className="empty">本期间没有已生效的工况数据</div> : (
        <>
          <div className="xstats">
            <Stat k="非生产时间" v={fmt(s.npt_hours, 1)} u="小时" />
            <Stat k="占全部工况" v={(share * 100).toFixed(1)} u="%" />
            <Stat k="折合成本（估）" v={wan(s.npt_hours * perHour)} u="万元" hint={`按每台月成本 ÷ ${RIG_HOURS} 小时折算`} />
          </div>
          <div className="rank">
            {rows.map(([k, v]) => {
              const bad = NPT.includes(k);
              return (
                <div key={k} className="rank-row" title={`${k}：${fmt(v, 1)} 小时，占 ${((v / s.hours_total) * 100).toFixed(1)}%`}>
                  <span className="rank-name">{k}</span>
                  <span className="rank-plot"><span className="rank-bar" style={{ width: (v / max) * 100 + '%', background: bad ? 'var(--accent)' : 'var(--chart-neutral)' }} /></span>
                  <span className="rank-val num">{fmt(v, 1)}<em>{((v / s.hours_total) * 100).toFixed(0)}%</em></span>
                </div>
              );
            })}
          </div>
          <span className="legend"><span><i style={{ background: 'var(--accent)' }} />非生产（待命、复杂情况）</span><span><i style={{ background: 'var(--chart-neutral)' }} />生产工况</span></span>
        </>
      )}
    </div>
  );
}

function Stat({ k, v, u, hint }: { k: string; v: string; u: string; hint?: string }) {
  return <div className="xstat" title={hint}><span>{k}</span><b>{v}<em>{u}</em></b></div>;
}

// ---------- ⑤ 亏损井 ----------

function LossWells({ wells, open }: { wells: OverviewWell[]; open: (w: string, period?: string) => void }) {
  const losses = useMemo(() => wells.filter((w) => w.net_profit < 0).sort((a, b) => a.net_profit - b.net_profit), [wells]);
  const top = losses.slice(0, 10);
  const max = Math.max(...top.map((w) => -w.net_profit), 1);
  const total = losses.reduce((a, w) => a + w.net_profit, 0);
  return (
    <div className="card pad exec-card">
      <div className="sec-head">
        <h3>亏损井<small>万元 · 点击下钻到科目</small></h3>
        {losses.length > 0 && <span className="small muted">{losses.length} 口 · 合计 {wan(total)} 万元</span>}
      </div>
      {losses.length === 0 ? <div className="empty good-empty">本期间没有亏损井</div> : (
        <div className="rank rank-wide">
          {top.map((w): ReactNode => (
            <button key={w.well_id} type="button" className="rank-row pick" onClick={() => open(w.well_id, w.last_period)}
              title={`${w.well_id}：净利润 ${wan(w.net_profit)} 万元，利润率 ${w.revenue ? ((w.net_profit / w.revenue) * 100).toFixed(1) + '%' : '—'}`}>
              <span className="rank-name">{w.well_id}<em className="rank-sub">{w.team}</em></span>
              <span className="rank-plot"><span className="rank-bar" style={{ width: (-w.net_profit / max) * 100 + '%', background: 'var(--bad)' }} /></span>
              <span className="rank-val num neg">{wan(w.net_profit)}</span>
            </button>
          ))}
          {losses.length > top.length && <div className="rank-gap">其余 {losses.length - top.length} 口见下方井列表</div>}
        </div>
      )}
    </div>
  );
}
