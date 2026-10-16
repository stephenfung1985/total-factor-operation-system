import { useMemo, useState } from 'react';
import type { Overview as OV, OverviewWell } from '../api';
import { periodLabel, wan } from '../ui';

export const DONE = ['完钻', '完井', '已结算'];
export type Dim = 'team' | 'block' | 'market' | 'well_type';
export const DIMS: { id: Dim; label: string }[] = [{ id: 'team', label: '队伍' }, { id: 'block', label: '区块' }, { id: 'market', label: '市场' }, { id: 'well_type', label: '井型' }];
export interface DimFilter { dim: Dim; value: string }

const sum = (ws: OverviewWell[], k: 'revenue' | 'total_cost' | 'fixed' | 'variable') => ws.reduce((a, w) => a + w[k], 0);

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

/** 经营总览首屏之下的成本构成（决策层首屏图表见 ExecBoard）。 */
export default function OverviewCharts({ data }: { data: OV }) {
  return (
    <section className="charts" aria-label="成本构成">
      <Composition data={data} />
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
