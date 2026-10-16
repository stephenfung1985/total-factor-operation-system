import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { Overview as OV, OverviewWell } from '../api';
import { fmt, wan } from '../ui';

/**
 * 经营总览首屏（决策层）：利润瀑布、亏损井。
 * 视觉遵循 dataviz 规范：细柱（≤ 24px、数据端 4px 圆角、基线方角）、实线细网格、选择性直接标注、
 * 悬停提示（数值在前、名称在后）、每张图附屏幕阅读器可读的数据表；颜色只用强调蓝 + 中性灰，盈亏用状态色并配文字。
 */


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

export default function ExecBoard({ data, open }: { data: OV; open: (w: string, period?: string) => void }) {
  return (
    <section className="exec" aria-label="决策层经营概览">
      <Waterfall data={data} />
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
