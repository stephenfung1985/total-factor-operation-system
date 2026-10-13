import { useState } from 'react';
import { api } from '../api';
import type { PageProps } from '../App';
import { PageHead, Seg, useLoad } from '../ui';

type Tab = 'items' | 'report_lines' | 'teams' | 'materials' | 'units';
const SEC: Record<string, string> = { staff: '人员出勤', energy: '能耗与材料', equip: '设备与工具在用', service: '外包技术服务', logistics: '运输与环保' };
type Row = Record<string, string | number | null>;
const ITEM_COLS = '1.1fr 2fr 0.6fr 0.6fr 1.1fr 0.8fr 0.6fr 0.9fr';

export default function MasterData(_: PageProps) {
  const [tab, setTab] = useState<Tab>('items');
  const { data } = useLoad(() => api.get<Row[]>(`/api/master/${tab}`), [tab]);
  return (
    <>
      <PageHead title="主数据维护" sub="费用科目树、队伍、物资、计量单位；物资初期引用物资系统编码" />
      <div style={{ alignSelf: 'flex-start' }}>
        <Seg value={tab} onChange={setTab} label="主数据类别" options={[{ id: 'items', label: '费用科目树' }, { id: 'report_lines', label: '日报报量项目' }, { id: 'teams', label: '队伍' }, { id: 'materials', label: '物资' }, { id: 'units', label: '计量单位' }]} />
      </div>
      <section className="card list">
        {tab === 'items' && (
          <div className="scroll-x">
            <div style={{ minWidth: 920 }}>
              <div className="grid-row grid-head" style={{ gridTemplateColumns: ITEM_COLS }}>
                <span>编码</span><span>名称</span><span>方向</span><span>性态</span><span>计价类型</span><span>归口部室</span><span>总表列</span><span>会计科目</span>
              </div>
              {data?.map((r) => {
                const group = !r.is_leaf;
                return (
                  <div key={r.item_code as string} className="grid-row" style={{ gridTemplateColumns: ITEM_COLS, minHeight: group ? 44 : 40, fontSize: 14, borderBottom: '1px solid var(--sep2)', background: group && Number(r.level) <= 2 ? 'var(--fill)' : undefined, fontWeight: group ? 600 : 400 }}>
                    <span className="muted num" style={{ fontWeight: 400 }}>{r.item_code}</span>
                    <span style={{ paddingLeft: (Number(r.level) - 1) * 16 }}>{r.item_name}</span>
                    <span>{r.direction}</span><span>{r.cost_behavior || '—'}</span>
                    <span className="text3" style={{ fontWeight: 400 }}>{r.calc_type || ''}</span><span style={{ fontWeight: 400 }}>{r.owner_dept || ''}</span>
                    <span className="muted" style={{ fontWeight: 400 }}>{r.report_col || ''}</span><span className="muted" style={{ fontWeight: 400 }}>{r.finance_account || ''}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        {tab === 'report_lines' && (
          <>
            <div style={{ padding: '14px 24px' }} className="hint">成本日报只收这些需要基层采集、参与成本计算的量；以末级费用项目编码为键，规则按同一编码取量。“默认显示”的项目在日报中常驻，其余按需添加。</div>
            <Simple cols={['段', '分组', '报量项目', '单位', '事实类型', '费用项目', '须填单据', '默认显示']}
              keys={['section', 'group_name', 'label', 'unit', 'fact_type', 'item', 'doc', 'core']}
              data={data?.map((r) => ({ ...r, section: SEC[String(r.section)] ?? r.section, item: `${r.item_code} ${r.item_name ?? ''}`,
                doc: r.needs_doc ? '是' : '', core: r.core ? '是' : '' })) ?? null} />
          </>
        )}
        {tab === 'teams' && <Simple cols={['队号', '队伍', '钻机型号', '所属单位', '队长']} keys={['team_no', 'name', 'rig_type', 'unit', 'captain']} data={data} />}
        {tab === 'materials' && (
          <>
            <div style={{ padding: '14px 24px' }} className="hint">初期直接引用物资系统编码，不重新建设；此处只维护与费用科目的对应关系。</div>
            <Simple cols={['物资编码', '名称', '计量单位', '对应费用科目']} keys={['material_code', 'name', 'unit', 'item_name']} data={data} />
          </>
        )}
        {tab === 'units' && <Simple cols={['计量单位', '用于']} keys={['unit', 'usage']} data={data} />}
      </section>
    </>
  );
}

function Simple({ cols, keys, data }: { cols: string[]; keys: string[]; data: Row[] | null }) {
  const tpl = `repeat(${cols.length}, minmax(0, 1fr))`;
  return (
    <>
      <div className="grid-row grid-head" style={{ gridTemplateColumns: tpl }}>{cols.map((c) => <span key={c}>{c}</span>)}</div>
      {data?.map((r, i) => (
        <div key={i} className="grid-row" style={{ gridTemplateColumns: tpl, minHeight: 48, fontSize: 14, borderBottom: '1px solid var(--sep2)' }}>
          {keys.map((k, j) => <span key={k} style={j === 0 ? { fontWeight: 500 } : undefined}>{r[k] ?? '—'}</span>)}
        </div>
      ))}
    </>
  );
}
