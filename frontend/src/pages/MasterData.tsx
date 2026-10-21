import { useState, type ReactNode } from 'react';
import { api } from '../api';
import type { PageProps } from '../App';
import { Combo, Modal, PageHead, Seg, useLoad, useToast } from '../ui';

type Tab = 'items' | 'report_lines' | 'teams' | 'materials' | 'units';
const SEC: Record<string, string> = { energy: '能耗与材料', equip: '设备与工具在用', service: '外包技术服务', logistics: '运输与环保' };
type Row = Record<string, string | number | null>;
const ITEM_COLS = '1.1fr 2fr 0.6fr 0.6fr 1.1fr 0.8fr 0.6fr 0.9fr';

/** 编辑弹窗的字段：text 文本（带 list 时为可新增的下拉，取值来自已有数据）、select 固定下拉、bool 勾选、number 数字。 */
interface Field { k: string; label: string; kind?: 'text' | 'select' | 'bool' | 'number'; options?: { v: string; label: string }[]; list?: string[]; required?: boolean; hint?: string }
interface Edit { title: string; method: 'post' | 'put'; url: string; fields: Field[]; init: Row; note?: string }

const enc = encodeURIComponent;
const uniq = (xs: (string | number | null | undefined)[]) => [...new Set(xs.filter((x): x is string => typeof x === 'string' && x !== ''))].sort();

export default function MasterData(_: PageProps) {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('items');
  const [edit, setEdit] = useState<Edit | null>(null);
  const { data, reload } = useLoad(() => api.get<Row[]>(`/api/master/${tab}`), [tab]);
  const refs = useLoad(() => Promise.all([api.get<Row[]>('/api/master/units'), api.get<Row[]>('/api/master/items')]), []);
  const unitRows = refs.data?.[0] ?? [];
  const units = unitRows.map((u) => String(u.unit));
  const items = (refs.data?.[1] ?? []).filter((i) => i.is_leaf);
  const leafOptions = [{ v: '', label: '未关联' }, ...items.map((i) => ({ v: String(i.item_code), label: `${i.item_code} ${i.item_name}` }))];

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      toast(ok);
      reload();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  const editItem = (r: Row) => setEdit({
    title: `费用科目 · ${r.item_code} ${r.item_name}`, method: 'put', url: `/api/master/items/${enc(String(r.item_code))}`, init: r,
    note: '编码、层级、方向、性态关系到计算口径和导出模板，不在此修改',
    fields: [
      { k: 'owner_dept', label: '归口部室', list: uniq(items.map((i) => i.owner_dept)) },
      { k: 'calc_type', label: '计价类型', list: uniq(items.map((i) => i.calc_type)) },
      { k: 'report_col', label: '总表列', list: uniq(items.map((i) => i.report_col)) },
      { k: 'finance_account', label: '会计科目', list: uniq(items.map((i) => i.finance_account)) },
    ],
  });
  const editLine = (r: Row) => setEdit({
    title: `报量项目 · ${r.line_key}`, method: 'put', url: `/api/master/report_lines/${enc(String(r.line_key))}`, init: r,
    note: '停用后日报不再显示，已填数据和业务事实保留',
    fields: [
      { k: 'label', label: '名称' }, { k: 'unit', label: '单位', list: units, required: true }, { k: 'sort_no', label: '排序', kind: 'number', hint: '段内按数字从小到大' },
      { k: 'needs_doc', label: '须填签证单', kind: 'bool' }, { k: 'enabled', label: '启用', kind: 'bool' },
    ],
  });
  const editTeam = (r: Row) => setEdit({
    title: `队伍 · ${r.team_no}`, method: 'put', url: `/api/master/teams/${enc(String(r.team_id))}`, init: r,
    note: '队号不可改；改队名后该队账号的数据范围同步更新',
    fields: [{ k: 'name', label: '队伍名称' }, { k: 'rig_type', label: '钻机型号', list: uniq(data?.map((t) => t.rig_type) ?? []) },
      { k: 'unit', label: '所属单位', list: uniq(data?.map((t) => t.unit) ?? []) }, { k: 'captain', label: '队长' }],
  });
  const materialFields: Field[] = [
    { k: 'name', label: '名称' }, { k: 'unit', label: '计量单位', list: units, required: true },
    { k: 'item_code', label: '对应费用科目', kind: 'select', options: leafOptions },
  ];
  const editMaterial = (r: Row | null) => setEdit(r
    ? { title: `物资 · ${r.material_code}`, method: 'put', url: `/api/master/materials/${enc(String(r.material_code))}`, init: r, fields: materialFields }
    : { title: '新增物资', method: 'post', url: '/api/master/materials', init: { material_code: '', name: '', unit: '', item_code: '' },
        note: '物资编码引用物资系统编码，新增后不可修改', fields: [{ k: 'material_code', label: '物资编码' }, ...materialFields] });
  const editUnit = (r: Row | null) => setEdit(r
    ? { title: `计量单位 · ${r.unit}`, method: 'put', url: `/api/master/units/${enc(String(r.unit))}`, init: r, fields: [{ k: 'usage', label: '用于', list: uniq(unitRows.map((u) => u.usage)), required: true }],
        note: '单位名称被物资、报量项目、规则引用，只能改用途说明' }
    : { title: '新增计量单位', method: 'post', url: '/api/master/units', init: { unit: '', usage: '' }, fields: [{ k: 'unit', label: '计量单位' }, { k: 'usage', label: '用于', list: uniq(unitRows.map((u) => u.usage)), required: true }] });

  const toggleLine = (r: Row) => act(() => api.put(`/api/master/report_lines/${enc(String(r.line_key))}`, { enabled: !r.enabled }), r.enabled ? `已停用 ${r.label}` : `已启用 ${r.label}`);
  const deleteLine = (r: Row) => {
    if (!window.confirm(`删除报量项目“${r.label}”？已有日报或业务事实的项目不能删除，只能停用。`)) return;
    act(() => api.del(`/api/master/report_lines/${enc(String(r.line_key))}`), `已删除 ${r.label}`);
  };
  const toggleMaterial = (r: Row) => act(() => api.put(`/api/master/materials/${enc(String(r.material_code))}`, { enabled: !r.enabled }), r.enabled ? `已停用 ${r.name}` : `已启用 ${r.name}`);

  const addBtn = tab === 'materials' ? <button type="button" className="btn primary sm" onClick={() => editMaterial(null)}>新增物资</button>
    : tab === 'units' ? <button type="button" className="btn primary sm" onClick={() => editUnit(null)}>新增计量单位</button> : null;

  return (
    <>
      <PageHead title="主数据维护" sub="费用科目树、队伍、物资、计量单位；点击行编辑，每次修改留痕" />
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <Seg value={tab} onChange={setTab} label="主数据类别" options={[{ id: 'items', label: '费用科目树' }, { id: 'report_lines', label: '日报报量项目' }, { id: 'teams', label: '队伍' }, { id: 'materials', label: '物资' }, { id: 'units', label: '计量单位' }]} />
        {addBtn}
      </div>
      <section className="card list">
        {tab === 'items' && (
          <div className="scroll-x">
            <div className="nowrap" style={{ minWidth: 920 }}>
              <div className="grid-row grid-head" style={{ gridTemplateColumns: ITEM_COLS }}>
                <span>编码</span><span>名称</span><span>方向</span><span>性态</span><span>计价类型</span><span>归口部室</span><span>总表列</span><span>会计科目</span>
              </div>
              {data?.map((r) => {
                const group = !r.is_leaf;
                return (
                  <div key={r.item_code as string} className={'grid-row' + (group ? '' : ' line')} {...(group ? {} : {
                    role: 'button', tabIndex: 0, title: '编辑说明字段', onClick: () => editItem(r), onKeyDown: (e: { key: string }) => e.key === 'Enter' && editItem(r) })}
                    style={{ gridTemplateColumns: ITEM_COLS, minHeight: group ? 44 : 40, background: group && Number(r.level) <= 2 ? 'var(--fill)' : undefined, fontWeight: group ? 600 : 400 }}>
                    <span className="muted num" style={{ fontWeight: 400 }}>{r.item_code}</span>
                    <span style={{ paddingLeft: (Number(r.level) - 1) * 16 }} title={String(r.item_name)}>{r.item_name}</span>
                    <span>{r.direction}</span><span>{r.cost_behavior || '—'}</span>
                    {group ? <><span /><span /><span /><span /></> : <>   {/* 归属只在末级科目上显示和维护 */}
                      <span className="text3">{r.calc_type || ''}</span><span>{r.owner_dept || ''}</span>
                      <span className="muted">{r.report_col || ''}</span><span className="muted">{r.finance_account || ''}</span>
                    </>}
                  </div>
                );
              })}
            </div>
          </div>
        )}
        {tab === 'report_lines' && (
          <>
            <Hint text="成本日报只收需要基层采集、参与成本计算的量，以末级费用项目编码为键；“默认显示”的项目在日报中常驻。已有日报或业务事实的项目只能停用，不能删除。" />
            <Simple cols={['段', '分组', '报量项目', '单位', '事实类型', '费用项目', '须填单据', '默认显示', '排序']}
              keys={['section', 'group_name', 'label', 'unit', 'fact_type', 'item', 'doc', 'core', 'sort_no']} minWidth={1200}
              data={data?.map((r) => ({ ...r, section: SEC[String(r.section)] ?? r.section, item: `${r.item_code} ${r.item_name ?? ''}`,
                doc: r.needs_doc ? '是' : '', core: r.core ? '是' : '' })) ?? null}
              onEdit={editLine} off={(r) => !r.enabled}
              actions={(r) => (
                <>
                  <button type="button" className={'btn sm' + (r.enabled ? ' danger' : '')} onClick={() => toggleLine(r)}>{r.enabled ? '停用' : '启用'}</button>
                  <button type="button" className="btn sm" onClick={() => deleteLine(r)}>删除</button>
                </>
              )} />
          </>
        )}
        {tab === 'teams' && <Simple cols={['队号', '队伍', '钻机型号', '所属单位', '队长']} keys={['team_no', 'name', 'rig_type', 'unit', 'captain']} data={data} onEdit={editTeam} />}
        {tab === 'materials' && (
          <>
            <Hint text="初期直接引用物资系统编码，此处只维护与费用科目的对应关系；停用后不再选用，历史引用保留。" />
            <Simple cols={['物资编码', '名称', '计量单位', '对应费用科目']} keys={['material_code', 'name', 'unit', 'item_name']} data={data}
              onEdit={editMaterial} off={(r) => !r.enabled}
              actions={(r) => <button type="button" className={'btn sm' + (r.enabled ? ' danger' : '')} onClick={() => toggleMaterial(r)}>{r.enabled ? '停用' : '启用'}</button>} />
          </>
        )}
        {tab === 'units' && <Simple cols={['计量单位', '用于']} keys={['unit', 'usage']} data={data} onEdit={editUnit} />}
      </section>
      <EditModal edit={edit} onClose={() => setEdit(null)} onDone={() => { setEdit(null); reload(); refs.reload(); }} />
    </>
  );
}

const ELLIPSIS = { overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 } as const;

/** 说明行单行显示，宽度不够时省略，悬停看全文。 */
function Hint({ text }: { text: string }) {
  return <div className="hint" style={{ padding: '14px 24px', ...ELLIPSIS }} title={text}>{text}</div>;
}

function Simple({ cols, keys, data, onEdit, actions, off, minWidth }: {
  cols: string[]; keys: string[]; data: Row[] | null; onEdit: (r: Row) => void; actions?: (r: Row) => ReactNode; off?: (r: Row) => boolean; minWidth?: number;
}) {
  const tpl = `repeat(${cols.length}, minmax(0, 1fr))${actions ? ' 200px' : ''}`;
  return (
    <div className="scroll-x">
      <div className="nowrap" style={{ minWidth: minWidth ?? 0 }}>
        <div className="grid-row grid-head" style={{ gridTemplateColumns: tpl }}>{cols.map((c) => <span key={c}>{c}</span>)}{actions && <span>操作</span>}</div>
        {data?.map((r, i) => {
          const dim = off?.(r);
          return (
            <div key={i} className="grid-row line" role="button" tabIndex={0} title="编辑"
              onClick={() => onEdit(r)} onKeyDown={(e) => e.target === e.currentTarget && e.key === 'Enter' && onEdit(r)}
              style={{ gridTemplateColumns: tpl, minHeight: 48, opacity: dim ? 0.6 : 1 }}>
              {keys.map((k, j) => (
                <span key={k} style={j === 0 ? { fontWeight: 500 } : undefined}>
                  {r[k] === null || r[k] === '' ? '—' : r[k]}
                </span>
              ))}
              {actions && <span className="btn-row" style={{ flexWrap: 'nowrap' }} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>{dim && <span className="tag bad" style={{ alignSelf: 'center' }}>已停用</span>}{actions(r)}</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const asForm = (v: string | number | null | undefined) => (v === null || v === undefined ? '' : String(v));

function EditModal({ edit, onClose, onDone }: { edit: Edit | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [f, setF] = useState<Record<string, string | boolean>>({});
  const [key, setKey] = useState<Edit | null>(null);
  if (edit !== key) {   // 打开新的编辑对象时按其当前值重置表单
    setKey(edit);
    setF(Object.fromEntries((edit?.fields ?? []).map((d) => [d.k, d.kind === 'bool' ? !!edit!.init[d.k] : asForm(edit!.init[d.k])])));
  }
  if (!edit) return null;
  const value = (d: Field) => (d.kind === 'bool' ? !!f[d.k] : d.kind === 'number' ? Number(f[d.k]) : String(f[d.k] ?? '').trim());
  const original = (d: Field) => (d.kind === 'bool' ? !!edit.init[d.k] : d.kind === 'number' ? Number(edit.init[d.k]) : asForm(edit.init[d.k]).trim());
  const body = Object.fromEntries(edit.fields.filter((d) => edit.method === 'post' || value(d) !== original(d)).map((d) => [d.k, value(d)]));
  const dirty = Object.keys(body).length > 0;
  const submit = async () => {
    try {
      await (edit.method === 'post' ? api.post(edit.url, body) : api.put(edit.url, body));
      toast(edit.method === 'post' ? '已新增' : '已保存，修改已留痕');
      onDone();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const texts = edit.fields.filter((d) => d.kind !== 'bool');
  const bools = edit.fields.filter((d) => d.kind === 'bool');
  return (
    <Modal open onClose={onClose} title={edit.title}>
      <div className="form-grid" style={{ gridTemplateColumns: texts.length > 1 ? '1fr 1fr' : '1fr' }}>
        {texts.map((d) => (
          <label key={d.k} className="field" style={d.kind === 'select' && texts.length > 1 ? { gridColumn: '1 / -1' } : undefined}>{d.label}
            {d.kind === 'select'
              ? <select className="input" value={String(f[d.k] ?? '')} onChange={(e) => setF({ ...f, [d.k]: e.target.value })}>
                  {d.options?.map((o) => <option key={o.v} value={o.v}>{o.label}</option>)}
                </select>
              : d.list
                ? <Combo value={String(f[d.k] ?? '')} options={d.list} allowEmpty={!d.required} onChange={(v) => setF({ ...f, [d.k]: v })} onEnter={() => dirty && submit()} />
                : <input className="input" type={d.kind === 'number' ? 'number' : 'text'} value={String(f[d.k] ?? '')}
                    placeholder={d.hint} onChange={(e) => setF({ ...f, [d.k]: e.target.value })} onKeyDown={(e) => e.key === 'Enter' && dirty && submit()} />}
          </label>
        ))}
      </div>
      {bools.length > 0 && (
        <div style={{ display: 'flex', gap: 20, fontSize: 14 }}>
          {bools.map((d) => <label key={d.k} style={{ display: 'flex', gap: 6, alignItems: 'center' }}><input type="checkbox" checked={!!f[d.k]} onChange={(e) => setF({ ...f, [d.k]: e.target.checked })} />{d.label}</label>)}
        </div>
      )}
      {edit.note && <div className="hint" style={ELLIPSIS} title={edit.note}>{edit.note}</div>}
      <div className="btn-row"><button type="button" className="btn" onClick={onClose}>取消</button><button type="button" className="btn primary" disabled={!dirty} onClick={submit}>{edit.method === 'post' ? '新增' : '保存'}</button></div>
    </Modal>
  );
}
