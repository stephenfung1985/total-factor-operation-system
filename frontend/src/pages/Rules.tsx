import { useEffect, useMemo, useState } from 'react';
import { api, type Condition, type RuleDetail, type RuleRow } from '../api';
import type { PageProps } from '../App';
import { fmt, Icon, PageHead, Seg, useLoad, useToast } from '../ui';

const STEPS = ['草稿', '待确认', '回算中', '待发布', '已发布'];
type Filter = 'todo' | 'published' | 'all';
const TODO = ['草稿', '待确认', '回算中', '待发布'];

export default function Rules({ me }: PageProps) {
  const toast = useToast();
  const isDept = me.role === 'dept';
  const [filter, setFilter] = useState<Filter>(isDept ? 'todo' : 'all');
  const list = useLoad(() => api.get<RuleRow[]>('/api/rules'), []);
  const shown = useMemo(() => (list.data || []).filter((r) =>
    filter === 'all' ? true : filter === 'todo' ? TODO.includes(r.status) : r.status === '已发布'), [list.data, filter]);
  const [sel, setSel] = useState<number | null>(null);
  const [d, setD] = useState<RuleDetail | null>(null);
  const [edit, setEdit] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (shown.length && (sel === null || !shown.some((r) => r.rule_id === sel))) setSel(shown[0].rule_id);
  }, [shown, sel]);
  useEffect(() => {
    setEdit(false);
    if (sel !== null) api.get<RuleDetail>(`/api/rules/${sel}`).then(setD).catch((e: Error) => toast(e.message, true));
  }, [sel, toast]);

  const act = async (fn: () => Promise<RuleDetail>, ok: (r: RuleDetail) => string) => {
    setBusy(true);
    try {
      const r = await fn();
      setD(r);
      setSel(r.rule.rule_id);
      toast(ok(r), r.backtest?.result === '不通过');
      list.reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const id = d?.rule.rule_id;
  const confirm = () => act(() => api.post(`/api/rules/${id}/confirm`), (r) => `回算${r.backtest?.result}：${r.backtest?.message}`);
  const reject = () => {
    const c = window.prompt('驳回意见');
    if (c !== null) act(() => api.post(`/api/rules/${id}/reject`, { comment: c }), () => '已驳回，退回草稿');
  };
  const publish = () => act(() => api.post(`/api/rules/${id}/publish`), (r) =>
    r.batch ? `已发布；重算批次 ${r.batch.created} 笔新增、${r.batch.reversed} 笔冲销` : '已发布');
  const newVersion = () => act(() => api.post(`/api/rules/${id}/new-version`, {}), (r) => `已复制为 v${r.rule.version} 草稿，只改变化的参数`);

  const cur = d ? STEPS.indexOf(d.rule.status) : -1;
  return (
    <>
      <PageHead title={isDept ? '规则确认' : '规则库'} sub={isDept ? '对照原文逐条确认 AI 提取的规则草稿' : '查看各部室规则的提取与确认状态'} />
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, justifyContent: 'space-between', alignItems: 'center' }}>
        <div className="steps">
          {STEPS.map((s, i) => (
            <span key={s} style={{ display: 'contents' }}>
              {i > 0 && <span aria-hidden="true" style={{ color: 'var(--mute)' }}>›</span>}
              <span className={'step' + (i === cur ? ' on' : '')}>{s}</span>
            </span>
          ))}
          {d && cur < 0 && <span className="step on">{d.rule.status}</span>}
        </div>
        <Seg value={filter} onChange={setFilter} label="规则筛选" options={[{ id: 'todo', label: '待处理' }, { id: 'published', label: '已发布' }, { id: 'all', label: '全部' }]} />
      </div>
      <div className="split" style={{ gap: 20 }}>
        <section className="card queue">
          <div style={{ padding: '8px 10px' }} className="hint">{filter === 'todo' ? '待处理' : filter === 'published' ? '已发布' : '全部'} · {shown.length} 条</div>
          {shown.map((r) => (
            <button key={r.rule_id} type="button" className={'queue-item' + (r.rule_id === sel ? ' on' : '')} onClick={() => setSel(r.rule_id)}>
              <b>{r.name} <span className="tag">v{r.version}</span></b>
              <span>{r.doc} · {r.clause_no}</span>
              <span>{r.status} · {r.conf}</span>
            </button>
          ))}
          {shown.length === 0 && <div className="empty">没有规则</div>}
        </section>
        {d && (
          <div style={{ flex: '3 1 560px', minWidth: 0, display: 'flex', flexWrap: 'wrap', gap: 20, alignItems: 'flex-start' }}>
            <section className="card pad" style={{ flex: '1 1 300px', minWidth: 0, gap: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
                <h3>{d.clause?.doc_type === '公司制度' || d.clause?.doc_type === '内部结算价' ? '制度原文' : '合同原文'}</h3>
                <span className="small muted">{d.clause?.no}{d.clause?.page ? ' · ' + d.clause.page : ''}</span>
              </div>
              {d.clause ? (
                <>
                  <div className="hint">{d.clause.doc}</div>
                  <p className="quote">{d.clause.pre}<mark>{d.clause.hit}</mark>{d.clause.post}</p>
                  <div className="small muted">高亮部分为提取依据；原件指纹 {d.clause.hash} 已校验</div>
                </>
              ) : <div className="hint">无来源条款</div>}
              <div style={{ borderTop: '1px solid var(--sep2)', paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
                <div className="sec-label">版本</div>
                {d.versions.map((v) => (
                  <button key={v.rule_id} type="button" className="kvline" style={{ border: 'none', background: 'none', padding: 0, textAlign: 'left', color: 'var(--text)', fontWeight: v.rule_id === id ? 600 : 400 }} onClick={() => setSel(v.rule_id)}>
                    <span>v{v.version}</span><span>{v.status} · {fmt(v.unit_price, 4)} · {v.valid_from} 起{v.valid_to ? ` 至 ${v.valid_to}` : ''}</span>
                  </button>
                ))}
                <div className="kvline"><span>命中</span><span>{d.hits.count} 笔有效事项 · {fmt(d.hits.amount)} 元</span></div>
              </div>
            </section>

            <section className="card pad" style={{ flex: '1 1 300px', minWidth: 0, gap: 4 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap', alignItems: 'baseline', paddingBottom: 8 }}>
                <h3>{d.rule.status === '已发布' ? '已发布规则' : '规则草稿'} <span className="tag">{d.rule.rule_code} · v{d.rule.version}</span></h3>
                <span className="small muted">{d.rule.origin}{d.rule.ai_confidence !== null ? ' · 黄色为需逐字核对的字段' : ''}</span>
              </div>
              {edit ? <RuleEditor d={d} onCancel={() => setEdit(false)} onSaved={(r) => { setD(r); setEdit(false); toast('已修改，修改前后差异已记录'); list.reload(); }} /> : (
                <>
                  {d.fields.map((f) => (
                    <div key={f.label} className={'field-row' + (f.level !== '高' ? ' flag' : '')}>
                      <span className="muted" style={{ fontSize: 13 }}>{f.label}</span><span>{f.value}</span><span className="lv">{f.level}</span>
                    </div>
                  ))}
                  <div className="field-row"><span className="muted" style={{ fontSize: 13 }}>生效日期</span><span>{d.rule.valid_from}{d.rule.valid_to ? ' 至 ' + d.rule.valid_to : ' 起'}</span><span /></div>
                  {d.tests.length > 0 && (
                    <div style={{ paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
                      <div className="sec-label">回算测试</div>
                      {d.tests.map((t, i) => t.result === '豁免' ? <div key={i} className="msg">无可回算的历史井，走豁免审批</div> : (
                        <div key={i} className="kvline" style={{ gridTemplateColumns: '90px 1fr' }}>
                          <span>{t.well_id} · {t.period}</span>
                          <span>对照 {fmt(t.expected_amount)} · 回算 {fmt(t.calc_amount)} · 偏差 {((t.deviation_rate || 0) * 100).toFixed(1)}% <span className={'tag ' + (t.result === '通过' ? 'good' : 'bad')}>{t.result}</span></span>
                        </div>
                      ))}
                    </div>
                  )}
                  {isDept ? (
                    <div className="btn-row" style={{ paddingTop: 14 }}>
                      {d.can_reject && <button type="button" className="btn danger" disabled={busy} onClick={reject}>驳回</button>}
                      {d.can_edit && <button type="button" className="btn" disabled={busy} onClick={() => setEdit(true)}>修改</button>}
                      {d.can_confirm && <button type="button" className="btn primary" disabled={busy} onClick={confirm}>确认，进入回算</button>}
                      {d.can_publish && <button type="button" className="btn primary" disabled={busy} onClick={publish}>审批发布</button>}
                      {d.can_new_version && <button type="button" className="btn" disabled={busy} onClick={newVersion}>调价 · 新建版本</button>}
                    </div>
                  ) : (
                    <div style={{ paddingTop: 14 }} className="hint">当前角色只读：规则由归口部室确认，部室负责人审批发布</div>
                  )}
                  {d.logs.length > 0 && (
                    <div style={{ paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 4 }}>
                      <div className="sec-label">处理记录</div>
                      {d.logs.slice(-6).map((l, i) => <div key={i} className="msg small">{l.operated_at.slice(0, 16)} · {l.action} · {l.operator}{l.comment ? '：' + l.comment : ''}</div>)}
                    </div>
                  )}
                </>
              )}
            </section>
          </div>
        )}
      </div>
    </>
  );
}

function RuleEditor({ d, onCancel, onSaved }: { d: RuleDetail; onCancel: () => void; onSaved: (r: RuleDetail) => void }) {
  const toast = useToast();
  const ratio = d.rule.pricing_method === '比例';
  const [price, setPrice] = useState(String(ratio ? d.rule.unit_price * 100 : d.rule.unit_price));
  const [conds, setConds] = useState<Condition[]>(d.conditions);
  const [combine, setCombine] = useState(d.rule.combine_mode);
  const [gran, setGran] = useState(d.rule.granularity);
  const [from, setFrom] = useState(d.rule.valid_from);
  const [factors, setFactors] = useState(d.params.factor_rules || []);
  const save = async () => {
    try {
      const p = parseFloat(price);
      onSaved(await api.put<RuleDetail>(`/api/rules/${d.rule.rule_id}`, {
        unit_price: ratio ? p / 100 : p, conditions: conds, combine_mode: combine, granularity: gran, valid_from: from, factor_rules: factors,
      }));
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const { dimensions, operators } = d.options;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div className="form-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <label className="field">{ratio ? '比例（%）' : `单价（元/${d.rule.unit}）`}<input className="input" type="number" value={price} onChange={(e) => setPrice(e.target.value)} /></label>
        <label className="field">生效日期<input className="input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="field">组合方式<select className="input" value={combine} onChange={(e) => setCombine(e.target.value)}><option>互斥</option><option>叠加</option></select></label>
        <label className="field">计算粒度<select className="input" value={gran} onChange={(e) => setGran(e.target.value)}>{['逐日', '按井', '按阶段', '按期间'].map((g) => <option key={g}>{g}</option>)}</select></label>
      </div>
      <div className="sec">
        <div className="sec-label">适用条件（匹配单井基础信息）</div>
        {conds.map((c, i) => (
          <div key={i} className="cond-row">
            <select value={c.dimension} onChange={(e) => setConds(conds.map((x, j) => (j === i ? { ...x, dimension: e.target.value } : x)))}>{dimensions.map((o) => <option key={o}>{o}</option>)}</select>
            <select value={c.operator} onChange={(e) => setConds(conds.map((x, j) => (j === i ? { ...x, operator: e.target.value } : x)))}>{operators.map((o) => <option key={o}>{o}</option>)}</select>
            <input value={c.value} placeholder="值，多个用逗号分隔" onChange={(e) => setConds(conds.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
            <button type="button" className="close" aria-label="删除条件" onClick={() => setConds(conds.filter((_, j) => j !== i))}>{Icon.close()}</button>
          </div>
        ))}
        <button type="button" className="btn sm link" style={{ alignSelf: 'flex-start' }} onClick={() => setConds([...conds, { dimension: '市场', operator: '等于', value: '' }])}>添加条件</button>
      </div>
      {!ratio && (
        <div className="sec">
          <div className="sec-label">系数（满足条件时乘以系数，如井深超过 5000 米上浮 20%）</div>
          {factors.map((f, i) => (
            <div key={i} className="cond-row" style={{ gridTemplateColumns: '90px 80px minmax(0,1fr) 70px 36px' }}>
              <select value={f.dimension} onChange={(e) => setFactors(factors.map((x, j) => (j === i ? { ...x, dimension: e.target.value } : x)))}>{dimensions.map((o) => <option key={o}>{o}</option>)}</select>
              <select value={f.operator} onChange={(e) => setFactors(factors.map((x, j) => (j === i ? { ...x, operator: e.target.value } : x)))}>{operators.map((o) => <option key={o}>{o}</option>)}</select>
              <input value={f.value} onChange={(e) => setFactors(factors.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)))} />
              <input type="number" step="0.01" value={f.factor} onChange={(e) => setFactors(factors.map((x, j) => (j === i ? { ...x, factor: parseFloat(e.target.value) || 1 } : x)))} />
              <button type="button" className="close" aria-label="删除系数" onClick={() => setFactors(factors.filter((_, j) => j !== i))}>{Icon.close()}</button>
            </div>
          ))}
          <button type="button" className="btn sm link" style={{ alignSelf: 'flex-start' }} onClick={() => setFactors([...factors, { dimension: '井深', operator: '大于', value: '5000', factor: 1.2 }])}>添加系数</button>
        </div>
      )}
      <div className="btn-row">
        <button type="button" className="btn" onClick={onCancel}>取消</button>
        <button type="button" className="btn primary" onClick={save}>保存修改</button>
      </div>
    </div>
  );
}
