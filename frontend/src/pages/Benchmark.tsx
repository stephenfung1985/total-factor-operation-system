import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { api, type BenchGroup, type Benchmark } from '../api';
import type { PageProps } from '../App';
import { fmt, Icon, Kpis, PageHead, periodLabel, useLoad, wan, yuan } from '../ui';

type Dim = 'market' | 'block' | 'team';
const DIMS: { id: Dim; label: string }[] = [{ id: 'market', label: '市场' }, { id: 'block', label: '区块' }, { id: 'team', label: '队伍' }];
type Filters = Record<Dim, string[]>;

interface Metric { id: keyof BenchGroup; label: string; better: 'high' | 'low'; kind: 'rate' | 'wan' | 'yuan' }
const METRICS: Metric[] = [
  { id: 'net_rate', label: '利润率', better: 'high', kind: 'rate' },
  { id: 'gm_rate', label: '毛益率', better: 'high', kind: 'rate' },
  { id: 'avg_net', label: '单井净利润', better: 'high', kind: 'wan' },
  { id: 'avg_revenue', label: '单井形象收入', better: 'high', kind: 'wan' },
  { id: 'avg_cost', label: '单井总成本', better: 'low', kind: 'wan' },
  { id: 'cost_per_m', label: '单位进尺成本', better: 'low', kind: 'yuan' },
];
const CHART_MAX = 20;
const COLS_TAIL = '0.6fr 0.6fr 1fr 1fr 1fr 0.8fr 1fr 0.8fr 1fr 0.8fr 1fr 0.9fr 24px';

const val = (g: BenchGroup, m: Metric) => g[m.id] as number | null;
const unit = (m: Metric) => (m.kind === 'rate' ? '%' : m.kind === 'wan' ? '万元' : '元/米');
function show(v: number | null | undefined, m: Metric) {
  if (v === null || v === undefined) return '—';
  return m.kind === 'rate' ? (v * 100).toFixed(1) + '%' : m.kind === 'wan' ? wan(v) : yuan(v);
}
/** 与基准的差：比率用百分点，金额用相对百分比；按“越高 / 越低越好”判断好坏。 */
function diff(v: number | null, base: number | null, m: Metric) {
  if (v === null || base === null) return { text: '—', good: null as boolean | null };
  const d = v - base;
  const text = m.kind === 'rate' ? `${d >= 0 ? '+' : ''}${(d * 100).toFixed(1)} pp`
    : base !== 0 ? `${d >= 0 ? '+' : ''}${((d / Math.abs(base)) * 100).toFixed(1)}%` : '—';
  return { text, good: Math.abs(d) < 1e-9 ? null : (d > 0) === (m.better === 'high') };
}

export default function BenchmarkPage({ setRoute }: PageProps) {
  const [period, setPeriod] = useState('all');
  const [dims, setDims] = useState<Dim[]>(['market']);
  const [filters, setFilters] = useState<Filters>({ market: [], block: [], team: [] });
  const [baseSel, setBaseSel] = useState('total');   // total / scope / g:<分组名>
  const [metricId, setMetricId] = useState<keyof BenchGroup>('net_rate');
  const [open, setOpen] = useState<string | null>(null);

  const query = useMemo(() => {
    const q = new URLSearchParams({ period, dims: dims.join(',') });
    DIMS.forEach((d) => filters[d.id].forEach((v) => q.append(d.id, v)));
    return q.toString();
  }, [period, dims, filters]);
  const { data, error } = useLoad(() => api.get<Benchmark>(`/api/benchmark?${query}`), [query]);
  const m = METRICS.find((x) => x.id === metricId)!;

  const toggleDim = (d: Dim) => setDims((cur) => {
    if (cur.includes(d)) return cur.length > 1 ? cur.filter((x) => x !== d) : cur;
    return DIMS.map((x) => x.id).filter((x) => x === d || cur.includes(x));
  });
  // 选了具体对象的维度自动参与分组，才能逐一对比
  const pick = (d: Dim, values: string[]) => {
    setFilters((f) => ({ ...f, [d]: values }));
    if (values.length > 1 && !dims.includes(d)) toggleDim(d);
  };
  const clearAll = () => setFilters({ market: [], block: [], team: [] });

  const nameOf = (g: BenchGroup) => (data ? data.dims.map((d) => g.keys[d]).join(' · ') : '');
  const sorted = useMemo(() => {
    if (!data) return [];
    return [...data.groups].sort((a, b) => {
      const va = val(a, m), vb = val(b, m);
      if (va === null) return 1;
      if (vb === null) return -1;
      return m.better === 'high' ? vb - va : va - vb;
    });
  }, [data, m]);

  // 基准：全部井合计 / 所选范围合计 / 某一分组（标杆）
  const benchGroup = baseSel.startsWith('g:') ? sorted.find((g) => nameOf(g) === baseSel.slice(2)) : undefined;
  useEffect(() => {
    if (!data) return;
    if (baseSel === 'scope' && !data.filtered) setBaseSel('total');
    if (baseSel.startsWith('g:') && !benchGroup) setBaseSel(data.filtered ? 'scope' : 'total');
  }, [data, baseSel, benchGroup]);
  const baseGroup = benchGroup ?? (baseSel === 'scope' && data?.filtered ? data.scope : data?.total);
  const baseName = benchGroup ? `标杆 ${nameOf(benchGroup)}` : baseSel === 'scope' && data?.filtered ? '所选范围合计' : '全部井合计';
  const base = baseGroup ? val(baseGroup, m) : null;
  const scope = data?.scope;
  const stale = data && (data.dims.join(',') !== dims.join(',') || data.period !== period);

  return (
    <>
      <PageHead title="对标分析" sub="选定要对比的市场、区块或队伍，按维度分组逐一对标；基准可选全部井、所选范围或指定标杆" />
      <div className="filters" role="group" aria-label="对标条件">
        <label className="pill">{Icon.calendar()}
          <select aria-label="期间" value={period} onChange={(e) => setPeriod(e.target.value)}>
            <option value="all">全部期间</option>
            {(data?.periods || []).slice().reverse().map((p) => <option key={p} value={p}>{periodLabel(p)}</option>)}
          </select>
        </label>
        {DIMS.map((d) => (
          <MultiSelect key={d.id} label={d.label} options={data?.options[d.id] || []} value={filters[d.id]} onChange={(v) => pick(d.id, v)} />
        ))}
        {data?.filtered && <button type="button" className="btn sm link" style={{ minHeight: 34 }} onClick={clearAll}>清空对象</button>}
      </div>
      <div className="filters" role="group" aria-label="分组与基准">
        <div className="pill" style={{ padding: '3px 3px 3px 12px' }}>
          分组方式
          <div className="seg" role="group" aria-label="分组方式（可多选）">
            {DIMS.map((d) => (
              <button key={d.id} type="button" className={dims.includes(d.id) ? 'on' : ''} aria-pressed={dims.includes(d.id)} onClick={() => toggleDim(d.id)}>
                {dims.includes(d.id) ? '✓ ' : ''}{d.label}
              </button>
            ))}
          </div>
        </div>
        <label className="pill">对标基准
          <select aria-label="对标基准" value={benchGroup ? baseSel : baseSel === 'scope' && data?.filtered ? 'scope' : 'total'} onChange={(e) => setBaseSel(e.target.value)}>
            <option value="total">全部井合计</option>
            {data?.filtered && <option value="scope">所选范围合计</option>}
            {sorted.length > 1 && (
              <optgroup label="指定标杆">
                {sorted.map((g) => <option key={nameOf(g)} value={'g:' + nameOf(g)}>{nameOf(g)}</option>)}
              </optgroup>
            )}
          </select>
        </label>
        <label className="pill">排序指标
          <select aria-label="排序指标" value={String(metricId)} onChange={(e) => setMetricId(e.target.value as keyof BenchGroup)}>
            {METRICS.map((x) => <option key={x.id} value={x.id}>{x.label}（{x.better === 'high' ? '越高越好' : '越低越好'}）</option>)}
          </select>
        </label>
      </div>
      {error && <div className="notice">{error}</div>}
      {data && scope && baseGroup && (
        <>
          <Kpis items={[
            { label: '对比对象', value: String(data.groups.length), unit: `组 · ${scope.well_count} 口井` },
            { label: `${baseName} · 利润率`, value: baseGroup.net_rate === null ? '—' : (baseGroup.net_rate * 100).toFixed(1), unit: '%' },
            { label: `${baseName} · 单井净利润`, value: wan(baseGroup.avg_net), unit: '万元' },
            { label: `${baseName} · 单位进尺成本`, value: baseGroup.cost_per_m === null ? '—' : yuan(baseGroup.cost_per_m), unit: '元/米' },
          ]} />

          <section className="card pad" style={{ opacity: stale ? 0.6 : 1 }}>
            <div className="sec-head">
              <h2>{m.label}对标<span className="hint" style={{ fontWeight: 400, marginLeft: 8 }}>{m.better === 'high' ? '越高越好' : '越低越好'} · {unit(m)}</span></h2>
              <span className="small muted">竖线为{baseName} {show(base, m)}{sorted.length > CHART_MAX ? `；显示前 ${CHART_MAX} 组，完整数据见下表` : ''}</span>
            </div>
            {sorted.length < 2
              ? <div className="empty" style={{ padding: 20 }}>只有一个对比对象。请在上方选择两个及以上的市场、区块或队伍，或增加分组方式</div>
              : <BarChart groups={sorted.slice(0, CHART_MAX)} metric={m} base={base} baseLabel={benchGroup ? '标杆' : '基准'}
                  benchName={benchGroup ? nameOf(benchGroup) : null} nameOf={nameOf} />}
          </section>

          <section className="card list">
            <div className="card-head">
              <h2>对标明细</h2>
              <div className="hint">按{m.label}排序；“{m.label}对标”列与{baseName}比较；点击分组展开所含单井</div>
            </div>
            <div className="scroll-x">
              <div style={{ minWidth: 1180 }}>
                <TableHead dims={data.dims} labels={data.dim_labels} metric={m} />
                {sorted.map((g) => {
                  const key = nameOf(g);
                  return (
                    <Fragment key={key}>
                      <Row g={g} dims={data.dims} m={m} base={base} open={open === key} bench={benchGroup === g}
                        onClick={() => setOpen(open === key ? null : key)} />
                      {open === key && g.items.map((it) => (
                        <button key={it.well_id + it.period} type="button" className="grid-row line"
                          style={{ gridTemplateColumns: `${data.dims.map(() => '1fr').join(' ')} ${COLS_TAIL}`, background: 'var(--fill)', minHeight: 44, fontSize: 13 }}
                          onClick={() => setRoute({ screen: 'overview', well: it.well_id, period: it.period })} aria-label={`下钻到 ${it.well_id} ${it.period}`}>
                          <span style={{ gridColumn: `span ${data.dims.length}`, paddingLeft: 16 }}>
                            {it.well_id} · {it.period}{it.remark && <> <span className="tag">{it.remark}</span></>}
                          </span>
                          <span /><span />
                          <span className="r num">{wan(it.revenue)}</span><span className="r num">{wan(it.total_cost)}</span>
                          <span className="r num">{wan(it.gross_margin)}</span><span />
                          <span className={'r num' + (it.net_profit < 0 ? ' neg' : '')}>{wan(it.net_profit)}</span><span /><span />
                          <span className="r num">{it.footage ? fmt(it.footage, 0) : '—'}</span><span /><span />
                          {Icon.chevron()}
                        </button>
                      ))}
                    </Fragment>
                  );
                })}
                {data.filtered && <Row g={data.scope} dims={data.dims} m={m} base={base} totalLabel="所选范围合计" />}
                <Row g={data.total} dims={data.dims} m={m} base={base} totalLabel="全部井合计" />
              </div>
            </div>
          </section>
        </>
      )}
    </>
  );
}

/** 多选下拉：不选表示全部；选项带井数，按其他维度已选条件联动收窄。 */
function MultiSelect({ label, options, value, onChange }: {
  label: string; options: { value: string; wells: number }[]; value: string[]; onChange: (v: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', close);
    window.addEventListener('keydown', esc);
    return () => { document.removeEventListener('mousedown', close); window.removeEventListener('keydown', esc); };
  }, [open]);
  const shown = options.filter((o) => !q || o.value.includes(q.trim()));
  const summary = value.length === 0 ? '全部' : value.length === 1 ? value[0] : `${value[0]} 等 ${value.length} 个`;
  const toggle = (v: string) => onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v]);
  return (
    <div className="ms" ref={ref}>
      <button type="button" className={'pill ms-btn' + (value.length ? ' on' : '')} aria-haspopup="listbox" aria-expanded={open}
        aria-label={`${label}：${value.length ? value.join('、') : '全部'}`} onClick={() => setOpen(!open)}>
        <span className="muted">{label}</span><b>{summary}</b>{Icon.down(12)}
      </button>
      {open && (
        <div className="ms-pop" role="listbox" aria-multiselectable="true" aria-label={'选择' + label}>
          {options.length > 8 && <input className="input" style={{ height: 34, fontSize: 13, marginBottom: 6 }} placeholder={'搜索' + label} value={q} onChange={(e) => setQ(e.target.value)} autoFocus />}
          <div className="ms-actions">
            <button type="button" onClick={() => onChange(options.map((o) => o.value))}>全选</button>
            <button type="button" onClick={() => onChange([])}>清空（全部）</button>
          </div>
          {shown.length === 0 && <div className="hint" style={{ padding: 8 }}>没有可选的{label}</div>}
          {shown.map((o) => (
            <label key={o.value} className="ms-opt" role="option" aria-selected={value.includes(o.value)}>
              <input type="checkbox" checked={value.includes(o.value)} onChange={() => toggle(o.value)} />
              <span style={{ flex: 1 }}>{o.value}</span><span className="small muted">{o.wells} 口井</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

function TableHead({ dims, labels, metric }: { dims: string[]; labels: Record<string, string>; metric: Metric }) {
  return (
    <div className="grid-row grid-head" style={{ gridTemplateColumns: `${dims.map(() => '1fr').join(' ')} ${COLS_TAIL}` }}>
      {dims.map((d) => <span key={d}>{labels[d]}</span>)}
      <span className="r">井数</span><span className="r">井·月</span>
      <span className="r">形象收入（万元）</span><span className="r">总成本（万元）</span><span className="r">毛益额（万元）</span><span className="r">毛益率</span>
      <span className="r">净利润（万元）</span><span className="r">利润率</span><span className="r">单井净利润（万元）</span>
      <span className="r">进尺（米）</span><span className="r">单位进尺成本（元/米）</span><span className="r">{metric.label}对标</span><span />
    </div>
  );
}

function Row({ g, dims, m, base, open, onClick, totalLabel, bench }: {
  g: BenchGroup; dims: string[]; m: Metric; base: number | null; open?: boolean; onClick?: () => void; totalLabel?: string; bench?: boolean;
}) {
  const d = diff(val(g, m), base, m);
  const cells = (
    <>
      {totalLabel ? <span style={{ gridColumn: `span ${dims.length}` }}>{totalLabel}</span>
        : dims.map((k, i) => <span key={k}>{g.keys[k]}{bench && i === 0 && <> <span className="tag info">标杆</span></>}</span>)}
      <span className="r num">{g.well_count}</span><span className="r num">{g.well_months}</span>
      <span className="r num">{wan(g.revenue)}</span><span className="r num">{wan(g.total_cost)}</span>
      <span className="r num">{wan(g.gross_margin)}</span><span className="r num muted">{g.gm_rate === null ? '—' : (g.gm_rate * 100).toFixed(1) + '%'}</span>
      <span className={'r num' + (g.net_profit < 0 ? ' neg' : '')}>{wan(g.net_profit)}</span>
      <span className="r num muted">{g.net_rate === null ? '—' : (g.net_rate * 100).toFixed(1) + '%'}</span>
      <span className="r num">{wan(g.avg_net)}</span>
      <span className="r num">{g.footage ? fmt(g.footage, 0) : '—'}</span>
      <span className="r num">{g.cost_per_m === null ? '—' : yuan(g.cost_per_m)}</span>
      <span className="r">{bench ? '标杆' : <span className={'tag ' + (d.good === null ? '' : d.good ? 'good' : 'bad')}>{d.text}</span>}</span>
    </>
  );
  const style = { gridTemplateColumns: `${dims.map(() => '1fr').join(' ')} ${COLS_TAIL}` };
  if (totalLabel) return <div className="grid-row line bold" style={{ ...style, background: 'var(--fill)', fontSize: 14 }}>{cells}<span /></div>;
  return (
    <button type="button" className={'grid-row line' + (open ? ' sel' : '')} style={style} aria-expanded={open} onClick={onClick}>
      {cells}{open ? Icon.down(14) : Icon.right(14)}
    </button>
  );
}

/** 单系列横向条形图：一个色相（强调蓝），零线与基准竖线为细线；每条可悬停 / 聚焦查看数值。 */
function BarChart({ groups, metric, base, baseLabel, benchName, nameOf }: {
  groups: BenchGroup[]; metric: Metric; base: number | null; baseLabel: string; benchName: string | null; nameOf: (g: BenchGroup) => string;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const vals = groups.map((g) => val(g, metric));
  const nums = [...vals.filter((v): v is number => v !== null), ...(base !== null ? [base] : []), 0];
  const lo = Math.min(...nums), hi = Math.max(...nums);
  const span = hi - lo || 1;
  const pos = (v: number) => ((v - lo) / span) * 100;
  const zero = pos(0);
  return (
    <div className="bars" onMouseLeave={() => setHover(null)}>
      {groups.map((g, i) => {
        const v = vals[i];
        const name = nameOf(g);
        const isBench = name === benchName;
        const d = diff(v, base, metric);
        const neg = v !== null && v < 0;
        return (
          <div key={name} className={'bar-row' + (hover === i ? ' on' : '')} tabIndex={0}
            aria-label={`${name}：${metric.label} ${show(v, metric)}${isBench ? '（标杆）' : `，与${baseLabel} ${d.text}`}`}
            onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)}>
            <span className="bar-name" title={name}>{isBench ? '★ ' : ''}{name}</span>
            <span className="bar-plot">
              <span className="bar-zero" style={{ left: `${zero}%` }} />
              {base !== null && <span className="bar-base" style={{ left: `${pos(base)}%` }}>{i === 0 && <em>{baseLabel}</em>}</span>}
              {v !== null && (
                <>
                  <span className={'bar' + (neg ? ' neg' : '')} style={{ left: `${Math.min(zero, pos(v))}%`, width: `${Math.abs(pos(v) - zero)}%` }} />
                  <span className="bar-val num" style={neg ? { right: `calc(${100 - pos(v)}% + 6px)` } : { left: `calc(${pos(v)}% + 6px)` }}>{show(v, metric)}</span>
                </>
              )}
              {hover === i && (
                <span className="bar-tip" role="tooltip" style={{ left: `clamp(0px, calc(${pos(v ?? 0)}% - 90px), calc(100% - 200px))` }}>
                  <b>{show(v, metric)}</b>
                  <span>{name}{isBench ? '（标杆）' : ''}</span>
                  <span>{isBench ? '' : `与${baseLabel} ${d.text} · `}{g.well_count} 口井 / {g.well_months} 井·月</span>
                </span>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}
