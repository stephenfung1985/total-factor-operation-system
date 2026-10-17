import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { Overview as OV, OverviewWell } from '../api';
import { fmt, wan } from '../ui';

/**
 * 经营总览首屏（决策层）：利润瀑布、排行（队伍 / 区块 / 甲方 × 进尺 / 创收 / 创效，可下钻）。
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
      <Rankings wells={data.wells} open={open} />
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

// ---------- ③ 排行：队伍 / 区块 / 甲方单位 × 进尺榜 / 创收榜 / 创效榜，可下钻到井 ----------

type Board = 'footage' | 'revenue' | 'net';
type GroupBy = 'team' | 'block' | 'client';
const BOARDS: { id: Board; label: string; title: string; unit: string }[] = [
  { id: 'footage', label: '进尺榜', title: '进尺', unit: '米' },
  { id: 'revenue', label: '创收榜', title: '形象收入', unit: '万元' },
  { id: 'net', label: '创效榜', title: '净利润', unit: '万元' },
];
const GROUPS: { id: GroupBy; label: string }[] = [{ id: 'team', label: '队伍' }, { id: 'block', label: '区块' }, { id: 'client', label: '甲方单位' }];
const TOP = 10;
const metric = (w: OverviewWell, b: Board) => (b === 'footage' ? w.footage : b === 'revenue' ? w.revenue : w.net_profit);
const groupKey = (w: OverviewWell, g: GroupBy) => (g === 'team' ? w.team : g === 'block' ? w.block : w.client) || '—';

interface Row { key: string; sub: string; value: number; wells: OverviewWell[]; well?: OverviewWell }

function Rankings({ wells, open }: { wells: OverviewWell[]; open: (w: string, period?: string) => void }) {
  const [board, setBoard] = useState<Board>('net');
  const [group, setGroup] = useState<GroupBy>('team');
  const [drill, setDrill] = useState<string | null>(null);
  useEffect(() => setDrill(null), [wells, group]);
  const b = BOARDS.find((x) => x.id === board)!;
  const g = GROUPS.find((x) => x.id === group)!;
  const show = (v: number) => (board === 'footage' ? fmt(v, 0) : wan(v));
  const rows: Row[] = useMemo(() => {
    if (drill) {
      return wells.filter((w) => groupKey(w, group) === drill)
        .map((w) => ({ key: w.well_id, sub: [w.rig_type && w.rig_type !== '—' ? w.rig_type : '', group === 'team' ? w.block : w.team].filter(Boolean).join(' · '),
          value: metric(w, board), wells: [w], well: w }))
        .sort((x, y) => y.value - x.value);
    }
    const by = new Map<string, OverviewWell[]>();
    for (const w of wells) by.set(groupKey(w, group), [...(by.get(groupKey(w, group)) ?? []), w]);
    return [...by.entries()].map(([k, ws]) => {
      const rigs = [...new Set(ws.map((w) => w.rig_class).filter(Boolean))];
      return { key: k, sub: `${ws.length} 口${group === 'team' && rigs.length === 1 ? ` · ${rigs[0]}` : ''}`,
        value: ws.reduce((a, w) => a + metric(w, board), 0), wells: ws };
    }).sort((x, y) => y.value - x.value);
  }, [wells, group, board, drill]);
  const top = rows.slice(0, drill ? 20 : TOP);
  const vals = top.map((r) => r.value);
  const lo = Math.min(0, ...vals);
  const hi = Math.max(0, ...vals);
  const x = (v: number) => ((v - lo) / (hi - lo || 1)) * 100;
  const total = rows.reduce((a, r) => a + r.value, 0);
  const scope = drill ? `${g.label}：${drill}` : `按${g.label}`;
  return (
    <div className="card pad exec-card">
      <div className="sec-head">
        <h3>{drill ? <button type="button" className="btn link" style={{ padding: 0, minHeight: 0, fontSize: 'inherit', fontWeight: 'inherit' }} onClick={() => setDrill(null)}>‹ {g.label}排行</button> : `${g.label}排行`}<small>{b.title} · {b.unit}</small></h3>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <label className="pill">维度
            <select aria-label="排行维度" value={group} onChange={(e) => setGroup(e.target.value as GroupBy)}>
              {GROUPS.map((x) => <option key={x.id} value={x.id}>{x.label}</option>)}
            </select>
          </label>
          <div className="seg" role="group" aria-label="排行指标">
            {BOARDS.map((x) => <button key={x.id} type="button" className={x.id === board ? 'on' : ''} aria-pressed={x.id === board} onClick={() => setBoard(x.id)}>{x.label}</button>)}
          </div>
        </div>
      </div>
      <div className="small muted">{scope} · {drill ? `${rows.length} 口井` : `${rows.length} 个${g.label}`} · 合计 {show(total)} {b.unit}{drill ? ' · 点击井下钻到科目' : ' · 点击下钻到井'}</div>
      {rows.length === 0 ? <div className="empty">本期间没有数据</div> : (
        <div className="rank rank-wide">
          {top.map((r, i): ReactNode => {
            const neg = r.value < 0;
            const z = x(0);
            const v = x(r.value);
            const tip = `${r.key}：${b.title} ${show(r.value)} ${b.unit}`;
            return (
              <button key={r.key} type="button" className="rank-row pick" title={tip}
                onClick={() => (r.well ? open(r.well.well_id, r.well.last_period) : setDrill(r.key))}>
                <span className="rank-name"><em className="rank-sub" style={{ marginLeft: 0, marginRight: 6 }}>{i + 1}</em>{r.key}<em className="rank-sub">{r.sub}</em></span>
                <span className="rank-plot">
                  <span className="rank-bar" style={{ left: Math.min(z, v) + '%', width: Math.abs(v - z) + '%', background: neg ? 'var(--bad)' : board === 'net' ? 'var(--good)' : 'var(--accent)' }} />
                  {lo < 0 && hi > 0 && <span className="rank-zero" style={{ left: z + '%' }} />}
                </span>
                <span className={'rank-val num' + (neg ? ' neg' : '')}>{show(r.value)}</span>
              </button>
            );
          })}
          {rows.length > top.length && <div className="rank-gap">其余 {rows.length - top.length} {drill ? '口井见下方井列表' : `个${g.label}未列出`}</div>}
        </div>
      )}
      <SrTable caption={`${scope}${b.label}`} head={['名次', drill ? '井号' : g.label, `${b.title}（${b.unit}）`]}
        rows={rows.map((r, i) => [i + 1, r.key, show(r.value)])} />
    </div>
  );
}
