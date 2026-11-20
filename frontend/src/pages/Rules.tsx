import { useEffect, useMemo, useRef, useState } from 'react';
import { api, type Condition, type RuleDetail, type RuleImpact, type RuleRow } from '../api';
import type { PageProps } from '../App';
import { fmt, Icon, Modal, PageHead, Seg, today, useLoad, useToast, yuan } from '../ui';
import AiImport from './AiImport';

const STEPS = ['草稿', '待确认', '回算中', '待发布', '已发布'];
type Filter = 'todo' | 'published' | 'all';
const TODO = ['草稿', '待确认', '回算中', '待发布'];

export default function Rules({ me }: PageProps) {
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>(me.role === 'dept' ? 'all' : 'todo');
  const [publishing, setPublishing] = useState(false);
  const [rolling, setRolling] = useState(false);
  const [importing, setImporting] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [creating, setCreating] = useState<ManualSeed | null>(null);
  const list = useLoad(() => api.get<RuleRow[]>('/api/rules'), []);
  const shown = useMemo(() => (list.data || []).filter((r) =>
    filter === 'all' ? true : filter === 'todo' ? TODO.includes(r.status) : r.status === '已发布'), [list.data, filter]);
  const [sel, setSel] = useState<number | null>(null);
  const [d, setD] = useState<RuleDetail | null>(null);
  const [edit, setEdit] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!shown.length) return;
    if (sel === null) {
      setSel(shown[0].rule_id);
      return;
    }
    if (shown.some((r) => r.rule_id === sel)) return;
    // 刚新建的规则还没回到列表里时先留着，避免被旧列表弹回；已经在库里但不在当前筛选中则改选可见的第一条
    if ((list.data || []).some((r) => r.rule_id === sel)) setSel(shown[0].rule_id);
  }, [shown, sel, list.data]);
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
  const submit = () => act(() => api.post(`/api/rules/${id}/submit`), () => '已提交，等待费用管理部室确认');
  const confirm = () => act(() => api.post(`/api/rules/${id}/confirm`), (r) => `回算${r.backtest?.result}：${r.backtest?.message}`);
  const reject = () => {
    const c = window.prompt('退回原因（提交人可见）');
    if (c === null) return;
    if (!c.trim()) {
      toast('退回必须填写原因', true);
      return;
    }
    act(() => api.post(`/api/rules/${id}/reject`, { comment: c.trim() }), () => '已退回，提交人可修改后重新提交');
  };
  const publish = (basis: string) => act(() => api.post(`/api/rules/${id}/publish`, { change_basis: basis }), (r) => {
    setPublishing(false);
    return r.batch ? `已发布；重算 ${r.batch.created} 笔新增、${r.batch.reversed} 笔冲销，金额变动 ${yuan(r.batch.amount_change)} 元` : '已发布';
  });
  const rollback = (reason: string) => act(() => api.post(`/api/rules/${id}/rollback`, { comment: reason }), (r) => {
    setRolling(false);
    return (r.restored ? `已回滚，恢复 v${r.restored}` : '已回滚，规则停用') + (r.batch ? `；重算金额变动 ${yuan(r.batch.amount_change)} 元` : '');
  });
  const newVersion = () => act(() => api.post(`/api/rules/${id}/new-version`, {}), (r) => `已复制为 v${r.rule.version} 草稿，只改变化的参数`);

  const cur = d ? STEPS.indexOf(d.rule.status) : -1;
  return (
    <>
      <PageHead title={me.role === 'dept' ? '归口规则' : '规则库'}
        sub={me.role === 'dept'
          ? '只显示本部室归口规则，只读。确认、发布和金额计算不在部室；变化依据请到「变化依据」提交。日报不经部室审核。'
          : '三级入库：普通账户起草或修改草稿并提交 → 费用管理部室确认或退回（冲突检查 + 回算）→ 系统管理员登记依据并发布；发布后有误由系统管理员回滚'}
        right={me.role === 'finance' ? (
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" className="btn sm" onClick={() => { setCreating(null); setCreateOpen(true); }}>手工新建</button>
            <button type="button" className="btn primary sm" onClick={() => setImporting(true)}>导入依据文件 · AI 解析</button>
          </div>
        ) : undefined} />
      <AiImport open={importing} onClose={() => setImporting(false)} onDone={() => { list.reload(); setFilter('todo'); }} onPick={(rid) => { setFilter('todo'); setSel(rid); }}
        onManual={(m) => { setImporting(false); setCreating(m); setCreateOpen(true); }} />
      <ManualRule open={createOpen} seed={creating} onClose={() => setCreateOpen(false)} onCreated={(r) => {
        setCreateOpen(false); setFilter('todo'); setSel(r.rule.rule_id); setD(r); list.reload(); toast('已存为草稿，请对照原文核对后提交');
      }} />
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
                <div className="kvline"><span>命中</span><span>{d.hits.count} 笔有效事项 · {yuan(d.hits.amount)} 元</span></div>
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
                  <div className="field-row"><span className="muted" style={{ fontSize: 13 }}>依据提供</span><span>{d.owner_dept ? `${d.owner_dept}（归口部室）` : '—'}</span><span /></div>
                  {d.change_basis && <div className="field-row"><span className="muted" style={{ fontSize: 13 }}>变化依据</span><span>{d.change_basis}</span><span /></div>}
                  {d.tests.length > 0 && (
                    <div style={{ paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 6 }}>
                      <div className="sec-label">回算测试</div>
                      {d.tests.map((t, i) => t.result === '豁免' ? <div key={i} className="msg">无可回算的历史井，走豁免审批</div> : (
                        <div key={i} className="kvline" style={{ gridTemplateColumns: '90px 1fr' }}>
                          <span>{t.well_id} · {t.period}</span>
                          <span>对照 {yuan(t.expected_amount)} · 回算 {yuan(t.calc_amount)} · 偏差 {((t.deviation_rate || 0) * 100).toFixed(1)}% <span className={'tag ' + (t.result === '通过' ? 'good' : 'bad')}>{t.result}</span></span>
                        </div>
                      ))}
                    </div>
                  )}
                  {d.rule.status === '草稿' && !d.can_submit && (
                    <div className="hint" style={{ whiteSpace: 'normal', paddingTop: 8 }}>草稿待普通账户核对并提交后，费用管理部室才能确认。</div>
                  )}
                  <div className="btn-row" style={{ paddingTop: 14 }}>
                    {d.can_reject && <button type="button" className="btn danger" disabled={busy} onClick={reject}>退回</button>}
                    {d.can_edit && <button type="button" className="btn" disabled={busy} onClick={() => setEdit(true)}>修改</button>}
                    {d.can_submit && <button type="button" className="btn primary" disabled={busy} onClick={submit}>提交</button>}
                    {d.can_confirm && <button type="button" className="btn primary" disabled={busy} onClick={confirm}>确认，进入回算</button>}
                    {d.can_publish && <button type="button" className="btn primary" disabled={busy} onClick={() => setPublishing(true)}>影响预览并发布</button>}
                    {d.can_new_version && <button type="button" className="btn" disabled={busy} onClick={newVersion}>调价 · 新建版本</button>}
                    {d.can_rollback && <button type="button" className="btn danger" disabled={busy} onClick={() => setRolling(true)}>回滚</button>}
                  </div>
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
      {d && <PublishModal open={publishing} ruleId={d.rule.rule_id} busy={busy} onClose={() => setPublishing(false)} onPublish={publish} />}
      {d && <RollbackModal open={rolling} d={d} busy={busy} onClose={() => setRolling(false)} onRollback={rollback} />}
    </>
  );
}

/** 发布：先按发布试算一遍看影响（不落库），登记部室提供的变化依据后才能发布。 */
function PublishModal({ open, ruleId, busy, onClose, onPublish }: { open: boolean; ruleId: number; busy: boolean; onClose: () => void; onPublish: (basis: string) => void }) {
  const [imp, setImp] = useState<RuleImpact | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [basis, setBasis] = useState('');
  const [tips, setTips] = useState<{ basis_id: number; dept: string; title: string }[]>([]);
  useEffect(() => {
    if (!open) return;
    setImp(null);
    setErr(null);
    setBasis('');
    setTips([]);
    api.post<RuleImpact>(`/api/rules/${ruleId}/impact`).then(setImp).catch((e: Error) => setErr(e.message));
    api.get<{ basis_id: number; dept: string; title: string }[]>('/api/basis').then(setTips).catch(() => setTips([]));
  }, [open, ruleId]);
  return (
    <Modal open={open} onClose={onClose} title="影响预览并发布" wide>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {err && <div className="notice">{err}</div>}
        {!imp && !err && <div className="loading">试算中…</div>}
        {imp && (
          <>
            <div className="stat-pair" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
              <div className="stat"><span>新增事项</span><b>{imp.created}</b></div>
              <div className="stat"><span>冲销事项</span><b>{imp.reversed}</b></div>
              <div className="stat"><span>金额变动（元）</span><b className={imp.amount_change < 0 ? 'neg' : ''}>{yuan(imp.amount_change)}</b></div>
            </div>
            {imp.wells.length === 0 ? <div className="msg">对已有井的金额没有影响（新规则只作用于今后的事实）</div> : (
              <div style={{ maxHeight: 220, overflowY: 'auto' }}>
                <div className="sec-label">受影响的井 · {imp.well_count} 个井月{imp.well_count > imp.wells.length ? `（列出变化最大的 ${imp.wells.length} 个）` : ''}</div>
                {imp.wells.map((w) => (
                  <div key={w.well_id + w.period} className="kvline" style={{ gridTemplateColumns: '1fr 1fr 1fr' }}>
                    <span className="bold">{w.well_id}</span><span className="muted num">{w.period}</span>
                    <span className={'num r' + (w.delta < 0 ? ' neg' : '')}>{w.delta > 0 ? '+' : ''}{yuan(w.delta)} 元</span>
                  </div>
                ))}
              </div>
            )}
            <div className="hint" style={{ whiteSpace: 'normal' }}>以上为试算结果，尚未写入；发布后按此冲销重算，发现有误可在规则上“回滚”。</div>
          </>
        )}
        {tips.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div className="sec-label">部室已提交的变化依据（点选填入；发布仍由系统管理员确认）</div>
            <div className="btn-row">
              {tips.slice(0, 12).map((b) => (
                <button key={b.basis_id} type="button" className="btn sm" onClick={() => setBasis(b.title)}>{b.dept} · {b.title}</button>
              ))}
            </div>
          </div>
        )}
        <label className="field">变化依据 *（部室提供的文件、通知或会议纪要：文号 + 名称）
          <input className="input" value={basis} onChange={(e) => setBasis(e.target.value)} placeholder="如：工程技术部〔2026〕12 号 定向服务价格调整通知" />
        </label>
        <div className="btn-row">
          <button type="button" className="btn" onClick={onClose}>取消</button>
          <button type="button" className="btn primary" disabled={busy || !imp || basis.trim().length < 4} onClick={() => onPublish(basis.trim())}>确认发布</button>
        </div>
      </div>
    </Modal>
  );
}

/** 回滚：本版本废止，上一版本恢复生效（没有则停用），受影响月份冲销重算。 */
function RollbackModal({ open, d, busy, onClose, onRollback }: { open: boolean; d: RuleDetail; busy: boolean; onClose: () => void; onRollback: (reason: string) => void }) {
  const [reason, setReason] = useState('');
  useEffect(() => { if (open) setReason(''); }, [open]);
  const prev = d.versions.filter((v) => v.version < d.rule.version && v.status === '已被替代').pop();
  return (
    <Modal open={open} onClose={onClose} title={`回滚 ${d.rule.rule_code} v${d.rule.version}`}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div className="notice">{prev ? `本版本将废止，v${prev.version}（单价 ${fmt(prev.unit_price, 4)}）恢复生效` : '没有上一版本，回滚后该规则停用'}；
          自 {d.rule.valid_from.slice(0, 7)} 起已有事实按回滚后的规则冲销重算。</div>
        <label className="field">回滚原因 *<input className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="如：调价通知尚未生效" /></label>
        <div className="btn-row">
          <button type="button" className="btn" onClick={onClose}>取消</button>
          <button type="button" className="btn danger" disabled={busy || !reason.trim()} onClick={() => onRollback(reason.trim())}>确认回滚</button>
        </div>
      </div>
    </Modal>
  );
}

function RuleEditor({ d, onCancel, onSaved }: { d: RuleDetail; onCancel: () => void; onSaved: (r: RuleDetail) => void }) {
  const toast = useToast();
  const [name, setName] = useState(d.rule.name);
  const [item, setItem] = useState(d.rule.item_code);
  const [method, setMethod] = useState(d.rule.pricing_method);
  const [fact, setFact] = useState(d.rule.fact_type || '');
  const [base, setBase] = useState(d.rule.base_item_code || '');
  const [unit, setUnit] = useState(d.rule.unit);
  const ratio = method === '比例';
  const lump = method === '包干' && fact === '进尺';   // 单井总价 / 总包：按设计井深折算米价
  const [price, setPrice] = useState(String(d.rule.pricing_method === '比例' ? d.rule.unit_price * 100 : d.rule.unit_price));
  const [conds, setConds] = useState<Condition[]>(d.conditions);
  const [combine, setCombine] = useState(d.rule.combine_mode);
  const [gran, setGran] = useState(d.rule.granularity);
  const [from, setFrom] = useState(d.rule.valid_from);
  const [factors, setFactors] = useState(d.params.factor_rules || []);
  const save = async () => {
    try {
      const p = parseFloat(price);
      onSaved(await api.put<RuleDetail>(`/api/rules/${d.rule.rule_id}`, {
        name, item_code: item, pricing_method: method, fact_type: ratio ? null : fact, base_item_code: ratio ? base : null, unit,
        unit_price: ratio ? p / 100 : p, conditions: conds, combine_mode: combine, granularity: gran, valid_from: from, factor_rules: factors,
      }));
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const { dimensions, operators } = d.options;
  const grans = d.options.granularity.includes(gran) ? d.options.granularity : [gran, ...d.options.granularity];
  const facts = d.options.facts.includes(fact) || !fact ? d.options.facts : [fact, ...d.options.facts];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div className="form-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <label className="field" style={{ gridColumn: '1 / -1' }}>规则名称<input className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label className="field">科目
          <select className="input" value={item} onChange={(e) => setItem(e.target.value)}>
            {d.options.items.map((i) => <option key={i.item_code} value={i.item_code}>{i.item_code} {i.item_name}</option>)}
          </select>
        </label>
        <label className="field">计价方式
          <select className="input" value={method} onChange={(e) => setMethod(e.target.value)}>
            {d.options.pricing.map((p) => <option key={p}>{p}</option>)}
          </select>
        </label>
        {ratio ? (
          <label className="field">基数科目
            <select className="input" value={base} onChange={(e) => setBase(e.target.value)}>
              <option value="">请选择</option>
              {d.options.items.map((i) => <option key={i.item_code} value={i.item_code}>{i.item_code} {i.item_name}</option>)}
            </select>
          </label>
        ) : (
          <label className="field">触发事实
            <select className="input" value={fact} onChange={(e) => setFact(e.target.value)}>
              {facts.map((f) => <option key={f}>{f}</option>)}
            </select>
          </label>
        )}
        <label className="field">计价单位<input className="input" value={unit} onChange={(e) => setUnit(e.target.value)} /></label>
        <label className="field">{ratio ? '比例（%）' : lump ? '合同总价（元）' : `单价（元/${unit || '—'}）`}<input className="input" type="number" value={price} onChange={(e) => setPrice(e.target.value)} /></label>
        <label className="field">生效日期<input className="input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="field">组合方式
          <select className="input" value={combine} onChange={(e) => setCombine(e.target.value)}>{d.options.combine.map((o) => <option key={o}>{o}</option>)}</select>
        </label>
        <label className="field">计算粒度<select className="input" value={gran} onChange={(e) => setGran(e.target.value)}>{grans.map((g) => <option key={g}>{g}</option>)}</select></label>
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

interface ManualSeed {
  doc_id: number; clause_id: number; clause_no: string; text: string; reason: string; doc_name: string;
}
interface ManualClause { clause_id: number; clause_no: string; page: string; text: string; manual: boolean; rule_count: number }
interface ManualOptions {
  items: RuleDetail['options']['items']; facts: string[]; pricing: string[]; combine: string[]; granularity: string[];
  dimensions: string[]; operators: string[]; doc_types: string[];
  docs: { doc_id: number; doc_type: string; doc_no: string | null; doc_name: string; valid_from: string | null; clauses: ManualClause[] }[];
}

/** 普通账户手工新建草稿。必须挂到已上传依据的条款上；需人工计价的条款从解析结果带入，不另建无依据的规则。 */
function ManualRule({ open, seed, onClose, onCreated }: {
  open: boolean; seed: ManualSeed | null; onClose: () => void; onCreated: (r: RuleDetail) => void;
}) {
  const toast = useToast();
  const opt = useLoad(() => (open ? api.get<ManualOptions>('/api/rules/options') : Promise.resolve(null)), [open]);
  const [docId, setDocId] = useState<number | null>(null);
  const [clauseId, setClauseId] = useState<number | ''>('');
  const [clauseNo, setClauseNo] = useState('');
  const [page, setPage] = useState('');
  const [excerpt, setExcerpt] = useState('');
  const [name, setName] = useState('');
  const [item, setItem] = useState('');
  const [method, setMethod] = useState('单价');
  const [fact, setFact] = useState('');
  const [base, setBase] = useState('');
  const [price, setPrice] = useState('');
  const [unit, setUnit] = useState('');
  const [from, setFrom] = useState(today());
  const [combine, setCombine] = useState('互斥');
  const [gran, setGran] = useState('逐日');
  const [busy, setBusy] = useState(false);
  const [upType, setUpType] = useState('');
  const [upName, setUpName] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) return;
    setDocId(seed?.doc_id ?? null);
    setClauseId(seed?.clause_id ?? '');
    setClauseNo('');
    setPage('');
    setExcerpt('');
    setName('');
    setMethod('单价');
    setFact('');
    setBase('');
    setPrice('');
    setUnit('');
    setFrom(today());
    setCombine('互斥');
    setGran('逐日');
    setUpType('');
    setUpName('');
    setFile(null);
  }, [open, seed]);

  const o = opt.data;
  const doc = o?.docs.find((d) => d.doc_id === docId) || null;
  const clause = doc?.clauses.find((c) => c.clause_id === clauseId) || null;
  const fresh = !seed && clauseId === '';
  const ratio = method === '比例';

  const upload = async () => {
    if (!file || !upType) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append('file', file);
      fd.append('doc_type', upType);
      fd.append('doc_name', upName);
      const saved = await api.post<{ doc_id: number }>('/api/ai/docs', fd);
      opt.reload();
      setDocId(saved.doc_id);
      setClauseId('');
      setFile(null);
      toast('依据已上传，请从文件中摘录条款原文');
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!o) return;
    const p = parseFloat(price);
    if (!name.trim() || !item || Number.isNaN(p) || !from) {
      toast('请填写规则名称、科目、单价和生效日期', true);
      return;
    }
    if (ratio ? !base : !fact) {
      toast(ratio ? '请选择基数科目' : '请选择触发事实', true);
      return;
    }
    if (!ratio && !unit.trim()) {
      toast('请填写计价单位', true);
      return;
    }
    if (!seed && clauseId === '' && !docId) {
      toast('请选择依据文件。规则必须能追溯到原文', true);
      return;
    }
    const body: Record<string, unknown> = {
      name: name.trim(), item_code: item, pricing_method: method, fact_type: ratio ? null : fact,
      base_item_code: ratio ? base : null, unit: unit.trim(), unit_price: ratio ? p / 100 : p,
      combine_mode: combine, granularity: gran, valid_from: from,
    };
    if (seed || clauseId !== '') body.clause_id = seed ? seed.clause_id : clauseId;
    else {
      body.doc_id = docId;
      body.clause_no = clauseNo.trim();
      body.page_no = page.trim();
      body.clause_text = excerpt.trim();
    }
    setBusy(true);
    try {
      onCreated(await api.post<RuleDetail>('/api/rules', body));
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={seed ? '需人工计价 · 建成草稿' : '手工新建规则'} wide>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div className="hint" style={{ whiteSpace: 'normal' }}>
          {seed
            ? '这条条款写不成公式。草稿沿用已上传的依据文件和原文，补上计价方式后保存；核对无误再提交。不调用 AI。'
            : '保存为草稿，不调用 AI。请先选定依据文件，再选用已有条款，或从文件中原样摘录一句，保证以后每笔金额都能对上原文。'}
        </div>
        {!o && !opt.error && <div className="loading">加载依据…</div>}
        {opt.error && <div className="notice">{opt.error}</div>}
        {o && (
          <>
            {seed ? (
              <div>
                <div className="sec-label">{seed.doc_name} · {seed.clause_no}</div>
                <p className="quote">{seed.text}</p>
                {seed.reason && <div className="msg">AI 未生成公式：{seed.reason}</div>}
              </div>
            ) : (
              <>
                <div className="form-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
                  <label className="field">依据文件 *
                    <select className="input" value={docId ?? ''} onChange={(e) => { setDocId(e.target.value ? Number(e.target.value) : null); setClauseId(''); }}>
                      <option value="">请选择</option>
                      {o.docs.map((d) => <option key={d.doc_id} value={d.doc_id}>{d.doc_name} · {d.doc_type}</option>)}
                    </select>
                  </label>
                  <label className="field">条款
                    <select className="input" value={clauseId} onChange={(e) => setClauseId(e.target.value ? Number(e.target.value) : '')} disabled={!doc}>
                      <option value="">摘录新条款</option>
                      {doc?.clauses.map((c) => (
                        <option key={c.clause_id} value={c.clause_id}>
                          {c.clause_no}{c.manual ? ' · 需人工计价' : ''}{c.rule_count ? ` · 已有 ${c.rule_count} 条规则` : ''}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                {clause && <p className="quote">{clause.text}</p>}
                {fresh && (
                  <div className="form-grid" style={{ gridTemplateColumns: '140px 120px 1fr' }}>
                    <label className="field">条款号 *<input className="input" value={clauseNo} onChange={(e) => setClauseNo(e.target.value)} placeholder="如 第六条" /></label>
                    <label className="field">页码<input className="input" value={page} onChange={(e) => setPage(e.target.value)} /></label>
                    <label className="field" style={{ gridColumn: '1 / -1' }}>条款原文 *（从依据文件原样复制）
                      <textarea className="input" style={{ height: 88, padding: '10px 12px', resize: 'vertical' }} value={excerpt} onChange={(e) => setExcerpt(e.target.value)} />
                    </label>
                  </div>
                )}
                <details>
                  <summary className="small muted" style={{ cursor: 'pointer' }}>上传新的依据文件（不需要 AI）</summary>
                  <div className="form-grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', marginTop: 8 }}>
                    <div className="field"><span>文件 *</span>
                      <button type="button" className="btn" onClick={() => fileRef.current?.click()}>{file ? file.name : '选择文件'}</button>
                      <input ref={fileRef} type="file" accept=".pdf,.doc,.docx,.txt,.md" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) { setFile(f); setUpName((n) => n || f.name.replace(/\.[^.]+$/, '')); } e.target.value = ''; }} />
                    </div>
                    <label className="field">文件类型 *
                      <select className="input" value={upType} onChange={(e) => setUpType(e.target.value)}>
                        <option value="">请选择</option>
                        {o.doc_types.map((t) => <option key={t}>{t}</option>)}
                      </select>
                    </label>
                    <label className="field">文件名称<input className="input" value={upName} onChange={(e) => setUpName(e.target.value)} /></label>
                  </div>
                  <div className="btn-row" style={{ marginTop: 8 }}>
                    <button type="button" className="btn" disabled={busy || !file || !upType} onClick={upload}>{busy ? '上传中…' : '上传依据'}</button>
                  </div>
                </details>
              </>
            )}
            <div className="form-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
              <label className="field" style={{ gridColumn: '1 / -1' }}>规则名称 *<input className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
              <label className="field">科目 *
                <select className="input" value={item} onChange={(e) => setItem(e.target.value)}>
                  <option value="">请选择</option>
                  {o.items.map((i) => <option key={i.item_code} value={i.item_code}>{i.item_code} {i.item_name}</option>)}
                </select>
              </label>
              <label className="field">计价方式
                <select className="input" value={method} onChange={(e) => setMethod(e.target.value)}>{o.pricing.map((p) => <option key={p}>{p}</option>)}</select>
              </label>
              {ratio ? (
                <label className="field">基数科目 *
                  <select className="input" value={base} onChange={(e) => setBase(e.target.value)}>
                    <option value="">请选择</option>
                    {o.items.map((i) => <option key={i.item_code} value={i.item_code}>{i.item_code} {i.item_name}</option>)}
                  </select>
                </label>
              ) : (
                <label className="field">触发事实 *
                  <select className="input" value={fact} onChange={(e) => setFact(e.target.value)}>
                    <option value="">请选择</option>
                    {o.facts.map((f) => <option key={f}>{f}</option>)}
                  </select>
                </label>
              )}
              <label className="field">计价单位 *<input className="input" value={unit} onChange={(e) => setUnit(e.target.value)} placeholder={ratio ? '元' : '如 天、米'} /></label>
              <label className="field">{ratio ? '比例（%）*' : '单价（元）*'}<input className="input" type="number" value={price} onChange={(e) => setPrice(e.target.value)} /></label>
              <label className="field">生效日期 *<input className="input" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
              <label className="field">组合方式
                <select className="input" value={combine} onChange={(e) => setCombine(e.target.value)}>{o.combine.map((x) => <option key={x}>{x}</option>)}</select>
              </label>
              <label className="field">计算粒度
                <select className="input" value={gran} onChange={(e) => setGran(e.target.value)}>{o.granularity.map((g) => <option key={g}>{g}</option>)}</select>
              </label>
            </div>
            <div className="btn-row">
              <button type="button" className="btn" onClick={onClose}>取消</button>
              <button type="button" className="btn primary" disabled={busy || !o} onClick={save}>保存为草稿</button>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
