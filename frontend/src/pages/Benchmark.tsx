import { useEffect, useMemo, useRef, useState } from 'react';
import { api, type BenchGroup, type Benchmark } from '../api';
import type { PageProps } from '../App';
import { fmt, Icon, PageHead, periodLabel, useLoad, wan, yuan } from '../ui';

/** 对比维度按“市场 › 区块 › 队伍 › 井”逐级下钻；每次只看一个维度，多维靠下钻和筛选。 */
type Dim = 'market' | 'block' | 'team' | 'well';
const ORDER: { id: Dim; label: string }[] = [{ id: 'market', label: '市场' }, { id: 'block', label: '区块' }, { id: 'team', label: '队伍' }, { id: 'well', label: '井' }];
const FILTER_DIMS = ORDER.slice(0, 3);
type Filters = Record<Dim, string[]>;
const NO_FILTER: Filters = { market: [], block: [], team: [], well: [] };

/** sum：分组合计类指标（进尺、收入、毛益额、净利润），以“全部井 / 所选范围”为基准时取每组平均值比较。 */
interface Metric { id: keyof BenchGroup; label: string; better: 'high' | 'low'; kind: 'rate' | 'wan' | 'yuan' | 'm'; sum?: boolean }
const METRICS: Metric[] = [
  { id: 'net_rate', label: '利润率', better: 'high', kind: 'rate' },
  { id: 'gm_rate', label: '毛益率', better: 'high', kind: 'rate' },
  { id: 'avg_net', label: '单井净利润', better: 'high', kind: 'wan' },
  { id: 'avg_revenue', label: '单井收入', better: 'high', kind: 'wan' },
  { id: 'avg_cost', label: '单井成本', better: 'low', kind: 'wan' },
  { id: 'cost_per_m', label: '单位进尺成本', better: 'low', kind: 'yuan' },
];
/** 排名下拉：按分组合计的经营指标，口径同单井经营结果（毛益额 = 收入 − 变动成本，净利润 = 收入 − 总成本）。 */
const TOTALS: Metric[] = [
  METRICS[0],
  { id: 'footage', label: '进尺', better: 'high', kind: 'm', sum: true },
  { id: 'revenue', label: '收入', better: 'high', kind: 'wan', sum: true },
  { id: 'gross_margin', label: '毛益额', better: 'high', kind: 'wan', sum: true },
  METRICS[1],
  { id: 'net_profit', label: '净利润', better: 'high', kind: 'wan', sum: true },
];
const ALL_METRICS = [...TOTALS, ...METRICS.slice(2)];
const CHART_MAX = 20;
const COLS = 'minmax(120px, 1.6fr) 0.6fr 1fr 1fr 0.9fr 1.1fr 1fr 24px';
const COLS_X = 'minmax(120px, 1.6fr) 0.6fr 1fr 1fr 1fr 0.9fr 1.1fr 1fr 24px';   // 多一列当前排名指标

const val = (g: BenchGroup, m: Metric) => g[m.id] as number | null;
const unit = (m: Metric) => (m.kind === 'rate' ? '%' : m.kind === 'wan' ? '万元' : m.kind === 'm' ? '米' : '元/米');
function show(v: number | null | undefined, m: Metric) {
  if (v === null || v === undefined) return '—';
  return m.kind === 'rate' ? (v * 100).toFixed(1) + '%' : m.kind === 'wan' ? wan(v) : m.kind === 'm' ? fmt(v, 0) : yuan(v);
}
/** 基准值：合计类指标以全部井 / 所选范围为基准时取每组平均（合计 ÷ 分组数），标杆则直接取标杆的值。 */
function baseVal(g: BenchGroup, m: Metric, bench: boolean) {
  const v = val(g, m);
  return v !== null && m.sum && !bench ? v / (g.group_count || 1) : v;
}
/** 与基准的差：比率用百分点，金额用相对百分比；按“越高 / 越低越好”判断好坏。 */
function diff(v: number | null, base: number | null, m: Metric) {
  if (v === null || base === null) return { text: '—', good: null as boolean | null };
  const d = v - base;
  const text = m.kind === 'rate' ? `${d >= 0 ? '+' : ''}${(d * 100).toFixed(1)} pp`
    : base !== 0 ? `${d >= 0 ? '+' : ''}${((d / Math.abs(base)) * 100).toFixed(1)}%` : '—';
  return { text, good: Math.abs(d) < 1e-9 ? null : (d > 0) === (m.better === 'high') };
}
/** 排名以后端为准（data.rank 为分组下标），缺省时按指标方向在前端排序。 */
const rank = (d: Benchmark, m: Metric) => d.rank?.[m.id as string]?.map((i) => d.groups[i])
  ?? d.groups.filter((g) => val(g, m) !== null)
    .sort((a, b) => (m.better === 'high' ? (val(b, m) as number) - (val(a, m) as number) : (val(a, m) as number) - (val(b, m) as number)));

export default function BenchmarkPage({ setRoute }: PageProps) {
  const [periodSel, setPeriodSel] = useState<string[]>([]);   // 空 = 全部期间，可多选月份
  const [dim, setDim] = useState<Dim>('team');
  const [filters, setFilters] = useState<Filters>(NO_FILTER);
  const [showFilter, setShowFilter] = useState(false);
  const [baseSel, setBaseSel] = useState('total');   // total / scope / g:<对象名>
  const [metricId, setMetricId] = useState<keyof BenchGroup>('net_rate');

  const query = useMemo(() => {
    const q = new URLSearchParams({ dims: dim });
    (periodSel.length ? [...periodSel].sort() : ['all']).forEach((p) => q.append('period', p));
    ORDER.forEach((d) => filters[d.id].forEach((v) => q.append(d.id, v)));
    return q.toString();
  }, [periodSel, dim, filters]);
  const { data, error } = useLoad(() => api.get<Benchmark>(`/api/benchmark?${query}`), [query]);
  const m = ALL_METRICS.find((x) => x.id === metricId)!;
  const extraCol = m.id !== 'net_rate' && m.id !== 'cost_per_m';   // 表里已有利润率与单位进尺成本列
  const dimLabel = ORDER.find((d) => d.id === dim)!.label;
  const nameOf = (g: BenchGroup) => g.keys[data?.dims[0] ?? dim] ?? '';   // 切换维度的加载间隙仍按旧数据的维度取名
  const sorted = useMemo(() => (data ? rank(data, m) : []), [data, m]);
  const noValue = useMemo(() => (data ? data.groups.filter((g) => val(g, m) === null) : []), [data, m]);
  const rows = useMemo(() => [...sorted, ...noValue], [sorted, noValue]);

  const benchGroup = baseSel.startsWith('g:') ? rows.find((g) => nameOf(g) === baseSel.slice(2)) : undefined;
  useEffect(() => {
    if (!data) return;
    if (baseSel === 'scope' && !data.filtered) setBaseSel('total');
    if (baseSel.startsWith('g:') && !benchGroup) setBaseSel(data.filtered ? 'scope' : 'total');
  }, [data, baseSel, benchGroup]);
  const baseGroup = benchGroup ?? (baseSel === 'scope' && data?.filtered ? data.scope : data?.total);
  const inScope = baseSel === 'scope' && !!data?.filtered;
  const baseName = benchGroup ? `标杆 ${nameOf(benchGroup)}`
    : m.sum ? `${inScope ? '所选' : '各'}${dimLabel}平均` : inScope ? '所选范围' : '全部井';
  const base = baseGroup ? baseVal(baseGroup, m, !!benchGroup) : null;
  const stale = !!data && (data.dims[0] !== dim || data.period !== (periodSel.length ? [...periodSel].sort().join(',') : 'all'));

  // ---- 下钻与面包屑：已下钻的维度（只选了一个对象）依次排在对比维度之前 ----
  const dimIdx = ORDER.findIndex((d) => d.id === dim);
  const crumbs = ORDER.slice(0, dimIdx).filter((d) => filters[d.id].length === 1);
  const drill = (g: BenchGroup) => {
    const key = nameOf(g);
    if (dim === 'well') {
      const it = g.items[g.items.length - 1];
      if (it) setRoute({ screen: 'overview', well: it.well_id, period: it.period });
      return;
    }
    setFilters((f) => ({ ...f, [dim]: [key] }));
    setDim(ORDER[dimIdx + 1].id);
  };
  const backTo = (i: number) => {
    setFilters((f) => ({ ...f, ...Object.fromEntries(ORDER.slice(i).map((d) => [d.id, []])) }));
    setDim(ORDER[i].id);
  };
  const activeFilters = FILTER_DIMS.reduce((n, d) => n + (filters[d.id].length ? 1 : 0), 0);
  const insight = useMemo(() => {
    if (sorted.length < 2) return '';
    const b = sorted[0], w = sorted[sorted.length - 1];
    return `${m.label}：${nameOf(b)} 最好（较${baseName} ${diff(val(b, m), base, m).text}），${nameOf(w)} 最差（较${baseName} ${diff(val(w, m), base, m).text}）。点击任一${dimLabel}${dim === 'well' ? '查看该井经营' : '下钻'}。`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sorted, m, base, baseName]);

  return (
    <>
      <PageHead title="对标分析" sub="选一个对比维度，看谁好谁差、差多少；点击对象逐级下钻" />
      <div className="filters bm-bar" role="group" aria-label="对标条件">
        <MultiSelect label="期间" options={[...(data?.period_options || [])].reverse()} value={periodSel} onChange={setPeriodSel}
          all="全部期间" unit="个月" fmtValue={periodLabel} icon quick={years(data?.periods || [])} />
        <div className="seg" role="group" aria-label="对比维度">
          {ORDER.map((d) => (
            <button key={d.id} type="button" className={dim === d.id ? 'on' : ''} aria-pressed={dim === d.id} onClick={() => setDim(d.id)}>{d.label}</button>
          ))}
        </div>
        <label className="pill">基准
          <select aria-label="对标基准" value={benchGroup ? baseSel : baseSel === 'scope' && data?.filtered ? 'scope' : 'total'} onChange={(e) => setBaseSel(e.target.value)}>
            <option value="total">全部井</option>
            {data?.filtered && <option value="scope">所选范围</option>}
            {rows.length > 1 && (
              <optgroup label="指定标杆">
                {rows.map((g) => <option key={nameOf(g)} value={'g:' + nameOf(g)}>{nameOf(g)}</option>)}
              </optgroup>
            )}
          </select>
        </label>
        <button type="button" className={'pill ms-btn' + (activeFilters ? ' on' : '')} aria-expanded={showFilter} onClick={() => setShowFilter(!showFilter)}>
          <span className="muted">筛选</span>{activeFilters > 0 && <b>{activeFilters}</b>}{Icon.down(12)}
        </button>
      </div>
      {showFilter && (
        <div className="filters" role="group" aria-label="筛选对象">
          {FILTER_DIMS.map((d) => (
            <MultiSelect key={d.id} label={d.label} options={data?.options[d.id] || []} value={filters[d.id]}
              onChange={(v) => setFilters((f) => ({ ...f, [d.id]: v }))} />
          ))}
          {activeFilters > 0 && <button type="button" className="btn sm link" style={{ minHeight: 34 }} onClick={() => { setFilters(NO_FILTER); setDim('market'); }}>清空筛选</button>}
        </div>
      )}
      {(crumbs.length > 0 || dimIdx > 0) && (
        <nav className="crumbs" aria-label="下钻路径">
          <button type="button" onClick={() => backTo(0)}>全部</button>
          {crumbs.map((d) => (
            <span key={d.id} className="bm-crumb"><span className="sep">›</span>
              <button type="button" onClick={() => backTo(ORDER.findIndex((x) => x.id === d.id) + 1)} title={`${d.label} ${filters[d.id][0]}，点击回到下一级`}>{filters[d.id][0]}</button>
            </span>
          ))}
          <span className="bm-crumb"><span className="sep">›</span><b>{dimLabel}</b></span>
        </nav>
      )}

      {error && <div className="notice">{error}</div>}
      {data && baseGroup && (
        <div style={{ opacity: stale ? 0.6 : 1, transition: 'opacity .15s', display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="bm-cards" role="group" aria-label="对标指标">
            {METRICS.map((x) => {
              const r = rank(data, x);
              const b = baseGroup ? val(baseGroup, x) : null;
              return (
                <button key={x.id} type="button" className={'bm-card' + (x.id === metricId ? ' on' : '')} aria-pressed={x.id === metricId} onClick={() => setMetricId(x.id)}>
                  <span className="k">{x.label}</span>
                  <b className="num">{show(b, x)}{x.kind !== 'rate' && <small>{unit(x)}</small>}</b>
                  <span className="s">{r.length > 1 ? `${nameOf(r[0])} 最优 · ${nameOf(r[r.length - 1])} 最差` : `${baseName}基准`}</span>
                </button>
              );
            })}
          </div>
          {insight && <div className="notice info bm-insight">{insight}</div>}

          <section className="card pad">
            <div className="sec-head">
              <h2 className="bm-rank-head">
                <label className="pill bm-rank-pick">
                  <select aria-label="排名指标" value={m.id as string} onChange={(e) => setMetricId(e.target.value as keyof BenchGroup)}>
                    {TOTALS.map((x) => <option key={x.id} value={x.id as string}>{x.label}排名</option>)}
                    <optgroup label="单井 / 单位指标">
                      {METRICS.slice(2).map((x) => <option key={x.id} value={x.id as string}>{x.label}排名</option>)}
                    </optgroup>
                  </select>
                </label>
                <span className="hint" style={{ fontWeight: 400 }}>{m.better === 'high' ? '越高越好' : '越低越好'} · {unit(m)}</span>
              </h2>
              <span className="small muted bm-ellipsis">竖线为{baseName} {show(base, m)}{sorted.length > CHART_MAX ? `；显示前 ${CHART_MAX} 个，完整数据见下表` : ''}</span>
            </div>
            {sorted.length < 2
              ? <div className="empty" style={{ padding: 20 }}>只有一个{dimLabel}，无法排名。换一个对比维度，或在“筛选”里放宽范围</div>
              : <BarChart groups={sorted.slice(0, CHART_MAX)} metric={m} base={base} baseLabel={benchGroup ? '标杆' : '基准'}
                  benchName={benchGroup ? nameOf(benchGroup) : null} nameOf={nameOf} onPick={drill} />}
          </section>

          <section className="card list">
            <div className="card-head">
              <h2>对标明细</h2>
              <div className="hint">按{m.label}排序；“较基准”为{m.label}与{baseName}的差</div>
            </div>
            <div className="scroll-x">
              <div className="nowrap" style={{ minWidth: extraCol ? 960 : 860 }}>
                <div className="grid-row grid-head" style={{ gridTemplateColumns: extraCol ? COLS_X : COLS }}>
                  <span>{dimLabel}</span><span className="r">井数</span><span className="r">形象收入（万元）</span><span className="r">总成本（万元）</span>
                  {extraCol && <span className="r">{m.label}（{unit(m)}）</span>}
                  <span className="r">利润率</span><span className="r">单位进尺成本（元/米）</span><span className="r">较基准（{m.label}）</span><span />
                </div>
                {rows.length === 0 && <div className="empty">当前条件下没有经营数据</div>}
                {rows.map((g) => <Row key={nameOf(g)} g={g} name={nameOf(g)} m={m} base={base} bench={benchGroup === g} extra={extraCol} onClick={() => drill(g)} />)}
                {data.filtered && <Total g={data.scope} label="所选范围合计" m={m} base={base} extra={extraCol} />}
                <Total g={data.total} label="全部井合计" m={m} base={base} extra={extraCol} />
              </div>
            </div>
          </section>
        </div>
      )}
    </>
  );
}

/** 按年快选：把一整年的月份一次选中或取消。 */
function years(periods: string[]) {
  return [...new Set(periods.map((p) => p.slice(0, 4)))].sort().reverse()
    .map((y) => ({ label: `${y} 年`, values: periods.filter((p) => p.startsWith(y)) }));
}

function rate(v: number | null) { return v === null ? '—' : (v * 100).toFixed(1) + '%'; }

function Row({ g, name, m, base, bench, extra, onClick }: { g: BenchGroup; name: string; m: Metric; base: number | null; bench: boolean; extra: boolean; onClick: () => void }) {
  const d = diff(val(g, m), base, m);
  return (
    <button type="button" className="grid-row line" style={{ gridTemplateColumns: extra ? COLS_X : COLS }} onClick={onClick} aria-label={`${name}，下钻`}>
      <span className="bold" title={name}>{name}{bench && <> <span className="tag info">标杆</span></>}</span>
      <span className="r num">{g.well_count}</span>
      <span className="r num">{wan(g.revenue)}</span><span className="r num">{wan(g.total_cost)}</span>
      {extra && <MetricCell g={g} m={m} />}
      <span className={'r num' + (g.net_profit < 0 ? ' neg' : '')}>{rate(g.net_rate)}</span>
      <span className="r num">{g.cost_per_m === null ? '—' : yuan(g.cost_per_m)}</span>
      <span className="r">{bench ? '标杆' : <span className={'tag ' + (d.good === null ? '' : d.good ? 'good' : 'bad')}>{d.text}</span>}</span>
      {Icon.chevron()}
    </button>
  );
}

function MetricCell({ g, m }: { g: BenchGroup; m: Metric }) {
  const v = val(g, m);
  return <span className={'r num' + (v !== null && v < 0 ? ' neg' : '')}>{m.kind === 'rate' ? rate(v) : show(v, m)}</span>;
}

function Total({ g, label, m, base, extra }: { g: BenchGroup; label: string; m: Metric; base: number | null; extra: boolean }) {
  // 合计类指标的合计行与“每组平均”基准不可比，较基准一栏留空
  const d = m.sum ? { text: '—' } : diff(val(g, m), base, m);
  return (
    <div className="grid-row line bold" style={{ gridTemplateColumns: extra ? COLS_X : COLS, background: 'var(--fill)', fontSize: 14 }}>
      <span>{label}</span><span className="r num">{g.well_count}</span>
      <span className="r num">{wan(g.revenue)}</span><span className="r num">{wan(g.total_cost)}</span>
      {extra && <MetricCell g={g} m={m} />}
      <span className={'r num' + (g.net_profit < 0 ? ' neg' : '')}>{rate(g.net_rate)}</span>
      <span className="r num">{g.cost_per_m === null ? '—' : yuan(g.cost_per_m)}</span>
      <span className="r muted">{d.text === '+0.0 pp' || d.text === '—' ? '—' : d.text}</span><span />
    </div>
  );
}

/** 多选下拉：不选表示全部；选项带井数，按其他维度已选条件联动收窄。 */
function MultiSelect({ label, options, value, onChange, all = '全部', unit = '个', fmtValue = (v: string) => v, icon, quick }: {
  label: string; options: { value: string; wells: number }[]; value: string[]; onChange: (v: string[]) => void;
  all?: string; unit?: string; fmtValue?: (v: string) => string; icon?: boolean; quick?: { label: string; values: string[] }[];
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
  const shown = options.filter((o) => !q || fmtValue(o.value).includes(q.trim()) || o.value.includes(q.trim()));
  const ordered = [...value].sort();
  const summary = value.length === 0 ? all : value.length === 1 ? fmtValue(value[0]) : `${fmtValue(ordered[0])} 等 ${value.length} ${unit}`;
  const toggle = (v: string) => onChange(value.includes(v) ? value.filter((x) => x !== v) : [...value, v]);
  return (
    <div className="ms" ref={ref}>
      <button type="button" className={'pill ms-btn' + (value.length ? ' on' : '')} aria-haspopup="listbox" aria-expanded={open}
        aria-label={`${label}：${value.length ? value.map(fmtValue).join('、') : all}`} onClick={() => setOpen(!open)}>
        {icon && Icon.calendar()}<span className="muted">{label}</span><b>{summary}</b>{Icon.down(12)}
      </button>
      {open && (
        <div className="ms-pop" role="listbox" aria-multiselectable="true" aria-label={'选择' + label}>
          {options.length > 8 && <input className="input" style={{ height: 34, fontSize: 13, marginBottom: 6 }} placeholder={'搜索' + label} value={q} onChange={(e) => setQ(e.target.value)} autoFocus />}
          <div className="ms-actions">
            <button type="button" onClick={() => onChange(options.map((o) => o.value))}>全选</button>
            <button type="button" onClick={() => onChange([])}>清空（{all}）</button>
          </div>
          {quick && quick.length > 0 && (
            <div className="ms-actions" aria-label="按年选择">
              {quick.map((y) => {
                const on = y.values.every((v) => value.includes(v));
                return <button key={y.label} type="button" className={on ? 'on' : ''} aria-pressed={on}
                  onClick={() => onChange(on ? value.filter((v) => !y.values.includes(v)) : [...new Set([...value, ...y.values])])}>{y.label}</button>;
              })}
            </div>
          )}
          {shown.length === 0 && <div className="hint" style={{ padding: 8 }}>没有可选的{label}</div>}
          {shown.map((o) => (
            <label key={o.value} className="ms-opt" role="option" aria-selected={value.includes(o.value)}>
              <input type="checkbox" checked={value.includes(o.value)} onChange={() => toggle(o.value)} />
              <span style={{ flex: 1 }}>{fmtValue(o.value)}</span><span className="small muted">{o.wells} 口井</span>
            </label>
          ))}
        </div>
      )}
    </div>
  );
}

/** 单系列横向条形图：优于基准用强调蓝、劣于基准用红，零线与基准为细线；悬停看数值，点击下钻。 */
function BarChart({ groups, metric, base, baseLabel, benchName, nameOf, onPick }: {
  groups: BenchGroup[]; metric: Metric; base: number | null; baseLabel: string; benchName: string | null;
  nameOf: (g: BenchGroup) => string; onPick: (g: BenchGroup) => void;
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
          <div key={name} className={'bar-row pick' + (hover === i ? ' on' : '')} tabIndex={0} role="button"
            aria-label={`${name}：${metric.label} ${show(v, metric)}${isBench ? '（标杆）' : `，较${baseLabel} ${d.text}`}，点击下钻`}
            onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} onBlur={() => setHover(null)}
            onClick={() => onPick(g)} onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onPick(g))}>
            <span className="bar-name" title={name}>{isBench ? '★ ' : ''}{name}</span>
            <span className="bar-plot">
              <span className="bar-zero" style={{ left: `${zero}%` }} />
              {base !== null && <span className="bar-base" style={{ left: `${pos(base)}%` }}>{i === 0 && <em>{baseLabel}</em>}</span>}
              {v !== null && (
                <>
                  <span className={'bar' + (neg ? ' neg' : '')} style={{
                    left: `${Math.min(zero, pos(v))}%`, width: `${Math.abs(pos(v) - zero)}%`,
                    background: d.good === false ? 'var(--bad)' : undefined,
                  }} />
                  <span className="bar-val num" style={neg ? { right: `calc(${100 - pos(v)}% + 6px)` } : { left: `calc(${pos(v)}% + 6px)` }}>{show(v, metric)}</span>
                </>
              )}
              {hover === i && (
                <span className="bar-tip" role="tooltip" style={{ left: `clamp(0px, calc(${pos(v ?? 0)}% - 90px), calc(100% - 200px))` }}>
                  <b>{show(v, metric)}</b>
                  <span>{name}{isBench ? '（标杆）' : ''}</span>
                  <span>{isBench ? '' : `较${baseLabel} ${d.text} · `}{g.well_count} 口井 / {fmt(g.well_months, 0)} 井·月</span>
                </span>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}
