import { useMemo } from 'react';
import { api, type Overview as OV, type OverviewWell } from '../api';
import { periodLabel, useLoad, wan } from '../ui';

const DONE = ['完钻', '完井', '已结算'];
const sum = (ws: OverviewWell[], k: 'revenue' | 'total_cost' | 'fixed' | 'variable') => ws.reduce((a, w) => a + w[k], 0);
const W = 520;
const H = 240;
const PAD = { l: 52, r: 12, t: 16, b: 30 };

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}

/** 经营总览顶部的整体经营情况：月度趋势、成本构成、单井净利润。 */
export default function OverviewCharts({ periods, period, wells, dept, open }: {
  periods: string[]; period: string; wells: OverviewWell[]; dept: boolean; open: (w: string) => void;
}) {
  const list = useMemo(() => [...periods].sort().slice(-12), [periods]);
  const trend = useLoad(() => Promise.all(list.map((p) => api.get<OV>(`/api/overview?period=${p}`))), [list.join(',')]);
  return (
    <section className="charts" aria-label="整体经营情况">
      <div className="card pad">
        <div className="sec-head"><h3>月度经营趋势<small>万元</small></h3>
          <Legend items={dept ? [['var(--accent)', '固定成本'], ['var(--accent-soft)', '变动成本']] : [['var(--accent)', '形象收入'], ['var(--mute)', '总成本'], ['var(--good)', '净利润']]} /></div>
        {trend.data ? <Trend ov={trend.data} dept={dept} period={period} /> : <div className="loading">{trend.error || '加载中…'}</div>}
      </div>
      <div className="card pad">
        <div className="sec-head"><h3>成本构成<small>{periodLabel(period)}</small></h3>
          <Legend items={[['var(--accent)', '固定成本'], ['var(--accent-soft)', '变动成本']]} /></div>
        <Composition wells={wells} />
      </div>
      {!dept && (
        <div className="card pad wide">
          <div className="sec-head"><h3>单井净利润<small>{periodLabel(period)} · 万元 · 点击下钻</small></h3>
            <Legend items={[['var(--good)', '盈利'], ['var(--bad)', '亏损']]} /></div>
          <WellProfit wells={wells} open={open} />
        </div>
      )}
    </section>
  );
}

function Legend({ items }: { items: [string, string][] }) {
  return (
    <span className="legend">
      {items.map(([c, n]) => <span key={n}><i style={{ background: c }} />{n}</span>)}
    </span>
  );
}

function Trend({ ov, dept, period }: { ov: OV[]; dept: boolean; period: string }) {
  const rows = ov.map((o) => {
    const s = { fixed: sum(o.wells, 'fixed'), variable: sum(o.wells, 'variable'), revenue: sum(o.wells, 'revenue'), cost: sum(o.wells, 'total_cost') };
    return { p: o.period, ...s, net: s.revenue - s.cost };
  });
  const series: { name: string; color: string; v: (r: typeof rows[number]) => number }[] = dept
    ? [{ name: '固定成本', color: 'var(--accent)', v: (r) => r.fixed }, { name: '变动成本', color: 'var(--accent-soft)', v: (r) => r.variable }]
    : [{ name: '形象收入', color: 'var(--accent)', v: (r) => r.revenue }, { name: '总成本', color: 'var(--mute)', v: (r) => r.cost },
       { name: '净利润', color: 'var(--good)', v: (r) => r.net }];
  const vals = rows.flatMap((r) => series.map((s) => s.v(r)));
  const max = niceMax(Math.max(0, ...vals));
  const min = Math.min(0, ...vals);
  const lo = min < 0 ? -niceMax(-min) : 0;
  const y = (v: number) => PAD.t + ((max - v) / (max - lo)) * (H - PAD.t - PAD.b);
  const gw = (W - PAD.l - PAD.r) / Math.max(rows.length, 1);
  const bw = Math.min(26, (gw * 0.7) / series.length);
  const ticks = [lo, lo + (max - lo) / 4, lo + ((max - lo) * 2) / 4, lo + ((max - lo) * 3) / 4, max];
  const labels = rows.length <= 4;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="chart" role="img" aria-label="月度经营趋势">
      {ticks.map((t) => (
        <g key={t}>
          <line x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} stroke="var(--sep)" strokeWidth={t === 0 ? 1.2 : 0.6} />
          <text x={PAD.l - 6} y={y(t) + 4} textAnchor="end" fontSize="12" fill="var(--text2)">{wan(t).replace(/\.00$/, '')}</text>
        </g>
      ))}
      {rows.map((r, i) => {
        const x0 = PAD.l + i * gw + (gw - bw * series.length) / 2;
        return (
          <g key={r.p} opacity={r.p === period ? 1 : 0.55}>
            {series.map((s, j) => {
              const v = s.v(r);
              const color = !dept && s.name === '净利润' && v < 0 ? 'var(--bad)' : s.color;
              return (
                <g key={s.name}>
                  <rect x={x0 + j * bw} y={Math.min(y(v), y(0))} width={bw - 2} height={Math.max(Math.abs(y(v) - y(0)), 1)} rx="3" fill={color}>
                    <title>{`${periodLabel(r.p)} ${s.name}：${wan(v)} 万元`}</title>
                  </rect>
                  {labels && <text x={x0 + j * bw + (bw - 2) / 2} y={(v >= 0 ? y(v) - 4 : y(v) + 12)} textAnchor="middle" fontSize="11" fill="var(--text2)">{wan(v)}</text>}
                </g>
              );
            })}
            <text x={PAD.l + i * gw + gw / 2} y={H - 10} textAnchor="middle" fontSize="12" fill="var(--text2)">{Number(r.p.slice(5, 7))} 月</text>
          </g>
        );
      })}
    </svg>
  );
}

function Composition({ wells }: { wells: OverviewWell[] }) {
  const groups = [
    { name: '全部井', ws: wells },
    { name: '正钻井', ws: wells.filter((w) => !DONE.includes(w.status)) },
    { name: '完成井', ws: wells.filter((w) => DONE.includes(w.status)) },
  ];
  return (
    <div className="comp">
      {groups.map(({ name, ws }) => {
        const f = sum(ws, 'fixed');
        const v = sum(ws, 'variable');
        const t = f + v;
        return (
          <div key={name} className="comp-row">
            <div className="comp-head"><b>{name}</b><span className="muted">共 {wan(t)} 万元 · {ws.length} 口</span></div>
            {t > 0 ? (
              <div className="comp-bar" role="img" aria-label={`${name}固定成本 ${wan(f)} 万元，变动成本 ${wan(v)} 万元`}>
                <span style={{ width: (f / t) * 100 + '%', background: 'var(--accent)' }} title={`固定成本 ${wan(f)} 万元`}>{f / t > 0.12 ? `${((f / t) * 100).toFixed(0)}%` : ''}</span>
                <span style={{ width: (v / t) * 100 + '%', background: 'var(--accent-soft)', color: 'var(--text)' }} title={`变动成本 ${wan(v)} 万元`}>{v / t > 0.12 ? `${((v / t) * 100).toFixed(0)}%` : ''}</span>
              </div>
            ) : <div className="comp-bar empty">本期间没有数据</div>}
            {t > 0 && <div className="small muted">固定 {wan(f)} 万元 · 变动 {wan(v)} 万元</div>}
          </div>
        );
      })}
    </div>
  );
}

function WellProfit({ wells, open }: { wells: OverviewWell[]; open: (w: string) => void }) {
  const rows = wells.map((w) => ({ id: w.well_id, net: w.revenue - w.total_cost, done: DONE.includes(w.status) })).sort((a, b) => b.net - a.net);
  if (!rows.length) return <div className="empty">本期间没有经营数据</div>;
  const max = Math.max(...rows.map((r) => Math.abs(r.net)), 1);
  const hasNeg = rows.some((r) => r.net < 0);
  const hasPos = rows.some((r) => r.net > 0);
  const zero = hasNeg && hasPos ? 50 : hasNeg ? 100 : 0;   // 零线位置（%）
  const span = hasNeg && hasPos ? 50 : 100;
  return (
    <div className="wp">
      {rows.map((r) => (
        <button key={r.id} type="button" className="wp-row" onClick={() => open(r.id)} aria-label={`${r.id} 净利润 ${wan(r.net)} 万元，下钻`}>
          <span className="wp-name">{r.id}<em>{r.done ? '完成' : '正钻'}</em></span>
          <span className="wp-plot">
            <span className="wp-zero" style={{ left: zero + '%' }} />
            <span className="wp-bar" style={{
              background: r.net >= 0 ? 'var(--good)' : 'var(--bad)',
              left: (r.net >= 0 ? zero : zero - (Math.abs(r.net) / max) * span) + '%', width: (Math.abs(r.net) / max) * span + '%',
            }} />
          </span>
          <span className={'wp-val num' + (r.net < 0 ? ' neg' : '')}>{wan(r.net)}</span>
        </button>
      ))}
    </div>
  );
}
