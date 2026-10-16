import { useMemo, useState } from 'react';
import type { Overview as OV, OverviewWell } from '../api';
import { periodLabel, wan } from '../ui';

export const DONE = ['完钻', '完井', '已结算'];
export type Dim = 'team' | 'block' | 'market' | 'well_type';
export const DIMS: { id: Dim; label: string }[] = [{ id: 'team', label: '队伍' }, { id: 'block', label: '区块' }, { id: 'market', label: '市场' }, { id: 'well_type', label: '井型' }];
export interface DimFilter { dim: Dim; value: string }

const sum = (ws: OverviewWell[], k: 'revenue' | 'total_cost' | 'fixed' | 'variable') => ws.reduce((a, w) => a + w[k], 0);
const net = (w: OverviewWell) => w.revenue - w.total_cost;
const TOP_N = 5;

function Legend({ items }: { items: [string, string][] }) {
  return <span className="legend">{items.map(([c, n]) => <span key={n}><i style={{ background: c }} />{n}</span>)}</span>;
}

function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: { id: T; label: string }[]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => <button key={o.id} type="button" className={value === o.id ? 'on' : ''} aria-pressed={value === o.id} onClick={() => onChange(o.id)}>{o.label}</button>)}
    </div>
  );
}

/** 经营总览顶部的整体经营情况：成本构成、分维度对比、单井净利润；条形一律限量并按统一比例尺，不会爆表。 */
export default function OverviewCharts({ data, dept, open, filter, onFilter }: {
  data: OV; dept: boolean; open: (w: string, period?: string) => void; filter: DimFilter | null; onFilter: (f: DimFilter | null) => void;
}) {
  return (
    <section className="charts" aria-label="整体经营情况">
      <Composition data={data} />
      <DimCompare wells={data.wells} dept={dept} filter={filter} onFilter={onFilter} wide={dept} />
      {!dept && <WellProfit wells={data.wells} open={open} />}
    </section>
  );
}

/** 成本构成：固定 / 变动占比，或二级科目结构（前 8 项，其余合并）。 */
function Composition({ data }: { data: OV }) {
  const [mode, setMode] = useState<'fv' | 'item'>('fv');
  const wells = data.wells;
  const groups = [
    { name: '全部井', ws: wells },
    { name: '正钻井', ws: wells.filter((w) => !DONE.includes(w.status)) },
    { name: '完成井', ws: wells.filter((w) => DONE.includes(w.status)) },
  ];
  const items = useMemo(() => {
    const all = (data.structure || []).filter((d) => d.amount > 0);
    const top = all.slice(0, 8);
    const rest = all.slice(8).reduce((a, d) => a + d.amount, 0);
    return rest > 0 ? [...top, { code: '_', name: '其他', behavior: '', amount: rest }] : top;
  }, [data.structure]);
  const total = items.reduce((a, d) => a + d.amount, 0);
  const max = Math.max(...items.map((d) => d.amount), 1);
  return (
    <div className="card pad">
      <div className="sec-head"><h3>成本构成<small>{periodLabel(data.period)}</small></h3>
        <Seg value={mode} onChange={setMode} label="成本构成视角" options={[{ id: 'fv', label: '固定 / 变动' }, { id: 'item', label: '科目结构' }]} /></div>
      {mode === 'fv' ? (
        <div className="comp">
          <Legend items={[['var(--accent)', '固定成本'], ['var(--accent-soft)', '变动成本']]} />
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
                ) : <div className="comp-bar blank">本期间没有数据</div>}
              </div>
            );
          })}
        </div>
      ) : (
        <div className="rank">
          {items.length === 0 && <div className="empty">本期间没有成本数据</div>}
          {items.map((d) => (
            <div key={d.code} className="rank-row" title={`${d.name}：${wan(d.amount)} 万元，占 ${((d.amount / total) * 100).toFixed(1)}%`}>
              <span className="rank-name">{d.name.replace(/^[（(]?[一二三四五六七八九十0-9]+[）).、]?/, '')}</span>
              <span className="rank-plot"><span className="rank-bar" style={{ width: (d.amount / max) * 100 + '%', background: d.behavior === '固定' ? 'var(--accent)' : 'var(--accent-soft)' }} /></span>
              <span className="rank-val num">{wan(d.amount)}<em>{((d.amount / total) * 100).toFixed(0)}%</em></span>
            </div>
          ))}
          <Legend items={[['var(--accent)', '固定'], ['var(--accent-soft)', '变动']]} />
        </div>
      )}
    </div>
  );
}

/** 分维度对比：按队伍 / 区块 / 市场 / 井型汇总净利润（部室为成本）；最多 8 行，其余并入“其余 n 项”，点击筛选下方列表。 */
function DimCompare({ wells, dept, filter, onFilter, wide }: { wells: OverviewWell[]; dept: boolean; filter: DimFilter | null; onFilter: (f: DimFilter | null) => void; wide?: boolean }) {
  const [dim, setDim] = useState<Dim>('team');
  const label = dept ? '总成本' : '净利润';
  const rows = useMemo(() => {
    const m = new Map<string, { name: string; v: number; n: number }>();
    wells.forEach((w) => {
      const k = w[dim] || '—';
      const g = m.get(k) || { name: k, v: 0, n: 0 };
      g.v += dept ? w.total_cost : net(w);
      g.n += 1;
      m.set(k, g);
    });
    const all = [...m.values()].sort((a, b) => b.v - a.v);
    if (all.length <= 8) return { shown: all, rest: null as null | { n: number; v: number } };
    const top = all.slice(0, 5);
    const bottom = all.slice(-3);
    const mid = all.slice(5, -3);
    return { shown: [...top, ...bottom], rest: { n: mid.length, v: mid.reduce((a, g) => a + g.v, 0) }, splitAt: 5 };
  }, [wells, dim, dept]);
  const max = Math.max(...rows.shown.map((g) => Math.abs(g.v)), 1);
  const hasNeg = rows.shown.some((g) => g.v < 0);
  const zero = hasNeg ? 50 : 0;
  const span = hasNeg ? 50 : 100;
  return (
    <div className={'card pad' + (wide ? ' wide' : '')}>
      <div className="sec-head"><h3>分维度对比<small>{label} · 万元 · 点击筛选列表</small></h3>
        <Seg value={dim} onChange={(d) => { setDim(d); if (filter && filter.dim !== d) onFilter(null); }} label="对比维度" options={DIMS} /></div>
      <div className="rank">
        {rows.shown.length === 0 && <div className="empty">本期间没有数据</div>}
        {rows.shown.map((g, i) => {
          const active = filter?.dim === dim && filter.value === g.name;
          return (
            <div key={g.name}>
              {rows.rest && i === (rows as { splitAt?: number }).splitAt && <div className="rank-gap">… 其余 {rows.rest.n} 项合计 {wan(rows.rest.v)} 万元 …</div>}
              <button type="button" className={'rank-row pick' + (active ? ' on' : '')} aria-pressed={active} title={`${g.name}：${wan(g.v)} 万元 · ${g.n} 口井`}
                onClick={() => onFilter(active ? null : { dim, value: g.name })}>
                <span className="rank-name">{g.name}</span>
                <span className="rank-plot">
                  <span className="rank-zero" style={{ left: zero + '%' }} />
                  <span className="rank-bar" style={{
                    left: (g.v >= 0 ? zero : zero - (Math.abs(g.v) / max) * span) + '%', width: (Math.abs(g.v) / max) * span + '%',
                    background: dept ? 'var(--accent)' : g.v >= 0 ? 'var(--good)' : 'var(--bad)',
                  }} />
                </span>
                <span className={'rank-val num' + (!dept && g.v < 0 ? ' neg' : '')}>{wan(g.v)}<em>{g.n} 口</em></span>
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/** 单井净利润：盈利前 5 / 亏损前 5，统一比例尺；全部井放在限高可滚动的列表里，井再多也不会撑破版面。 */
function WellProfit({ wells, open }: { wells: OverviewWell[]; open: (w: string, period?: string) => void }) {
  const [all, setAll] = useState(false);
  const sorted = useMemo(() => [...wells].sort((a, b) => net(b) - net(a)), [wells]);
  const gains = sorted.filter((w) => net(w) > 0);
  const losses = sorted.filter((w) => net(w) < 0).reverse();
  const top = gains.slice(0, TOP_N);
  const bottom = losses.slice(0, TOP_N);
  const max = Math.max(...[...top, ...bottom].map((w) => Math.abs(net(w))), ...(all ? sorted.map((w) => Math.abs(net(w))) : []), 1);
  const bar = (w: OverviewWell) => {
    const v = net(w);
    return (
      <button key={w.well_id} type="button" className="rank-row pick" onClick={() => open(w.well_id, w.last_period)} title={`${w.well_id}：净利润 ${wan(v)} 万元，点击下钻`}>
        <span className="rank-name">{w.well_id}</span>
        <span className="rank-plot"><span className="rank-bar" style={{ width: Math.min(100, (Math.abs(v) / max) * 100) + '%', background: v >= 0 ? 'var(--good)' : 'var(--bad)' }} /></span>
        <span className={'rank-val num' + (v < 0 ? ' neg' : '')}>{wan(v)}</span>
      </button>
    );
  };
  return (
    <div className="card pad">
      <div className="sec-head"><h3>单井净利润<small>万元 · 点击下钻</small></h3>
        <span className="small muted bm-ellipsis">共 {wells.length} 口：盈利 {gains.length}、亏损 {losses.length}、持平 {wells.length - gains.length - losses.length}</span></div>
      {wells.length === 0 ? <div className="empty">本期间没有经营数据</div> : (
        <>
          <div className="wp2">
            <div><div className="wp2-t">盈利前 {Math.min(TOP_N, gains.length) || ''}{gains.length === 0 && '（无）'}</div><div className="rank">{top.map(bar)}</div></div>
            <div><div className="wp2-t">亏损前 {Math.min(TOP_N, losses.length) || ''}{losses.length === 0 && '（无）'}</div><div className="rank">{bottom.map(bar)}</div></div>
          </div>
          {wells.length > TOP_N * 2 - 1 && (
            <>
              <button type="button" className="btn sm link" style={{ alignSelf: 'flex-start' }} aria-expanded={all} onClick={() => setAll(!all)}>{all ? '收起全部井' : `查看全部 ${wells.length} 口井`}</button>
              {all && <div className="rank wp2-all">{sorted.map(bar)}</div>}
            </>
          )}
        </>
      )}
    </div>
  );
}
