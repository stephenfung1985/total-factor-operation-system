import { useEffect, useRef, useState } from 'react';
import { api, download, type ImportResult, type InfoSummaryRow, type InfoWellDetail, type PendingReturn } from '../api';
import { Drawer, fmt, Kpis, Modal, useLoad, useToast } from '../ui';

const SECTIONS = ['一开', '二开', '三开', '四开', '五开'];
const WELL_TAG: Record<string, string> = { 计划: '', 正钻: 'info', 完钻: 'warn', 完井: 'good' };
const t16 = (s: string | null | undefined) => (s ? s.slice(0, 16) : '');
const RET_COLS = '0.8fr 1fr 0.8fr 3fr 1fr';
const lagCls = (s: string | null | undefined) => (s?.startsWith('滞后') ? 'tag bad' : s === '未开钻' ? 'tag' : s ? 'tag good' : '');

/** 管理层经营总览中的基础信息：登记统计、待队伍修改、Excel 导入导出；单井详情与退回在 InfoDetail 中。 */
export function InfoPanel() {
  const toast = useToast();
  const { data, error, reload } = useLoad(() => api.get<InfoSummaryRow[]>('/api/wellinfo/summary'), []);
  const returns = useLoad(() => api.get<PendingReturn[]>('/api/wellinfo/returns'), []);
  const [open, setOpen] = useState<string | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const refresh = () => {
    reload();
    returns.reload();
  };
  const count = (f: (r: InfoSummaryRow) => boolean) => String((data || []).filter(f).length);

  const exportXlsx = async () => {
    try {
      await download('/api/wellinfo/summary.xlsx', '单井基础信息表.xlsx');
      toast('已按《单井基础信息表》列格式导出');
    } catch (e) {
      toast((e as Error).message, true);
    }
  };

  return (
    <>
      <section className="card list">
        <div className="card-head center">
          <h2>基础信息</h2>
          <div className="head-actions">
            <div className="hint">由基层钻井队填报，提交后自动汇总；在单井页查看详情或退回</div>
            <button type="button" className="btn sm link" onClick={() => fileRef.current?.click()}>导入 Excel</button>
            <button type="button" className="btn primary sm" onClick={exportXlsx}>导出 Excel</button>
            <input ref={fileRef} type="file" accept=".xlsx" hidden onChange={(e) => { if (e.target.files?.[0]) setImportFile(e.target.files[0]); e.target.value = ''; }} />
          </div>
        </div>
        {error && <div className="notice">{error}</div>}
        {data && (
          <Kpis items={[
            { label: '登记井数', value: count(() => true), unit: '口' },
            { label: '正钻', value: count((r) => r.derived.status === '正钻'), unit: '口' },
            { label: '完钻 / 完井', value: `${count((r) => r.derived.status === '完钻')} / ${count((r) => r.derived.status === '完井')}`, unit: '口' },
            { label: '登记滞后', value: count((r) => r.derived.reg_lag.startsWith('滞后')), unit: '口' },
            { label: '完井填报滞后', value: count((r) => !!r.derived.comp_lag?.startsWith('滞后')), unit: '口' },
          ]} />
        )}
        {returns.data && returns.data.length > 0 && (
          <div className="scroll-x">
            <div style={{ minWidth: 760 }}>
              <div className="hint" style={{ padding: '8px 24px' }}>待队伍修改 · {returns.data.length}（退回队伍或导入校验未通过；队伍重新提交前仍按当前已提交版本）</div>
              <div className="grid-row grid-head" style={{ gridTemplateColumns: RET_COLS }}>
                <span>井号</span><span>队伍</span><span>来源</span><span>原因</span><span>退回时间</span>
              </div>
              {returns.data.map((r) => (
                <div key={r.report_id} className="grid-row line" style={{ gridTemplateColumns: RET_COLS, minHeight: 48, fontSize: 13, cursor: 'pointer' }}
                  onClick={() => setOpen(r.well_id)}>
                  <span className="bold">{r.well_id}</span><span>{r.team}</span>
                  <span><span className={'tag ' + (r.source === '导入' ? 'warn' : 'info')}>{r.source === '导入' ? '导入未通过' : '人工退回'}</span></span>
                  <span className="text3" style={{ whiteSpace: 'normal', padding: '8px 0' }}>{r.return_comment}</span>
                  <span className="muted num">{t16(r.returned_at)}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>
      <InfoDetail id={open} onClose={() => setOpen(null)} onReturned={refresh} />
      <ImportModal file={importFile} onClose={() => setImportFile(null)} onDone={refresh} />
    </>
  );
}

export function InfoDetail({ id, onClose, onReturned }: { id: string | null; onClose: () => void; onReturned: () => void }) {
  const toast = useToast();
  const { data, reload } = useLoad(() => (id ? api.get<InfoWellDetail>(`/api/wellinfo/wells/${encodeURIComponent(id)}`) : Promise.resolve(null)), [id]);
  const d = data && data.well_id === id ? data : null;
  const [returning, setReturning] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setReturning(false);
    setReason('');
  }, [id]);
  const line = (k: string, v: React.ReactNode, label = k) => (
    <div key={k} className="kvline" style={{ gridTemplateColumns: '110px 1fr' }}><span>{label}</span><span>{v || '—'}</span></div>
  );
  const doReturn = async () => {
    if (!reason.trim()) {
      toast('请填写退回原因，队伍会在填报页看到', true);
      return;
    }
    setBusy(true);
    try {
      await api.post(`/api/wellinfo/wells/${encodeURIComponent(id!)}/return`, { comment: reason.trim() });
      toast(`已退回 ${d?.team_no} 修改；重新提交前汇总仍按当前版本`);
      setReturning(false);
      reload();
      onReturned();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  let foot;
  if (d && !d.pending_return) {
    foot = returning ? (
      <>
        <textarea className="input" style={{ height: 72, padding: 10, flexBasis: '100%' }} autoFocus
          placeholder="退回原因（必填），如：区块应为 A1区块；二开完钻时间有误" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button type="button" className="btn" style={{ flex: 1 }} onClick={() => setReturning(false)}>取消</button>
        <button type="button" className="btn primary" style={{ flex: 2 }} disabled={busy} onClick={doReturn}>确认退回</button>
      </>
    ) : <button type="button" className="btn danger" style={{ flex: 1 }} onClick={() => setReturning(true)}>退回队伍修改</button>;
  }
  return (
    <Drawer open={id !== null} onClose={onClose} label="单井基础信息" wide foot={foot}
      head={<>
        <div className="hint">单井基础信息 · {d ? `${d.team_no} ${d.source === '导入' ? '导入' : '填报'} · 版本 ${d.version}` : '加载中…'}</div>
        <div style={{ fontSize: 26, fontWeight: 700 }}>{id}</div>
        {d && <div><span className={'tag ' + (WELL_TAG[d.derived.status] || '')}>{d.derived.status}</span></div>}
      </>}>
      {d && (
        <>
          {d.pending_return && (
            <div className="notice">
              已退回 {d.team_no} 修改（{d.pending_return.returned_by} · {t16(d.pending_return.returned_at)}）：{d.pending_return.return_comment}。
              队伍重新提交前，下列内容仍为当前已提交版本。
            </div>
          )}
          <section className="sec">
            <div className="sec-label">一、基本信息</div>
            {line('归属年月', d.period)}{line('市场分布', d.market)}{line('队号 / 队长', `${d.team_no} · ${d.captain}`)}
            {line('钻机类型', d.rig_type)}{line('甲方单位', d.client)}{line('区块', d.block)}
            {line('井别 / 井型', `${d.well_category} · ${d.well_type}`)}{line('开次', `${d.spud_sections} 开`)}
          </section>
          <section className="sec">
            <div className="sec-label">二、井深与周期</div>
            {line('设计井深', fmt(d.design_depth, 2) + ' 米')}
            {line('钻前 / 钻井 / 完井', `${fmt(d.derived.pre_days)} / ${fmt(d.derived.drill_days)} / ${fmt(d.derived.completion_days)} 天`)}
          </section>
          <section className="sec">
            <div className="sec-label">三、施工节点</div>
            {line('搬安（平移）', t16(d.milestones['搬安']))}{line('砸导管', t16(d.milestones['砸导管']))}
            {SECTIONS.slice(0, d.spud_sections).map((s, i) => line(s, `${t16(d.milestones[`开钻${i + 1}`]) || '—'} → ${t16(d.milestones[`完钻${i + 1}`]) || '—'}`))}
            {line('完钻', t16(d.milestones['完钻']))}{line('完井', t16(d.milestones['完井']))}
          </section>
          <section className="sec">
            <div className="sec-label">施工时段（由节点推导，供规则匹配与日报表头使用）</div>
            {d.segments.map((s, i) => line(`seg${i}`, `${s.team} · ${s.start_at} 至 ${s.end_at ?? '今'}`, s.phase))}
          </section>
          <section className="sec">
            <div className="sec-label">四、人员</div>
            {line('合同化 / 市场化 / 第三方', `${d.staff['合同化'] ?? 0} / ${d.staff['市场化'] ?? 0} / ${d.staff['第三方'] ?? 0} 人（合计 ${d.derived.staff_total}）`)}
          </section>
          <section className="sec">
            <div className="sec-label">五、填报</div>
            {line('填报时间', t16(d.registered_at))}{line('登记滞后', <span className={lagCls(d.derived.reg_lag)}>{d.derived.reg_lag}</span>)}
            {line('完井填报日期', t16(d.completion_reported_at))}
            {line('完井填报滞后', d.derived.comp_lag && <span className={lagCls(d.derived.comp_lag)}>{d.derived.comp_lag}</span>)}
            {line('创建人', d.creator)}
          </section>
          <section className="sec">
            <div className="sec-label">版本记录</div>
            {d.versions.map((v) => line(`v${v.version}`, `${v.status} · ${t16(v.submitted_at)} · ${v.team} · ${v.creator}`
              + (v.source === '导入' ? ' · 导入' : '') + (v.return_comment ? ` · 退回原因：${v.return_comment}` : '')))}
          </section>
        </>
      )}
    </Drawer>
  );
}

const ACTION_TAG: Record<string, string> = { 新增: 'good', 更新: 'info', 无变化: '', 退回队伍: 'warn', 跳过: 'bad' };
const IMP_COLS = '56px 1.1fr 0.9fr 0.9fr 0.6fr 3fr';

function ImportModal({ file, onClose, onDone }: { file: File | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [res, setRes] = useState<ImportResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [only, setOnly] = useState(false);
  const post = (commit: boolean) => {
    const fd = new FormData();
    fd.append('file', file!);
    return api.post<ImportResult>(`/api/wellinfo/import?commit=${commit}`, fd);
  };
  useEffect(() => {
    setRes(null);
    setErr(null);
    if (file) post(false).then(setRes).catch((e: Error) => setErr(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file]);
  const writes = res ? (res.count['新增'] || 0) + (res.count['更新'] || 0) + (res.count['退回队伍'] || 0) : 0;
  const commit = async () => {
    setBusy(true);
    try {
      const r = await post(true);
      const c = (k: string) => r.count[k] || 0;
      toast(`导入完成：新增 ${c('新增')}、更新 ${c('更新')}、退回队伍 ${c('退回队伍')}、跳过 ${c('跳过')}`);
      onDone();
      onClose();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const rows = (res?.rows || []).filter((x) => !only || x.action === '退回队伍' || x.action === '跳过' || x.checks.length > 0);
  return (
    <Modal open={!!file} onClose={onClose} title="导入单井基础信息" wide>
      <div className="hint">文件：{file?.name}。按《单井基础信息表》格式逐行校验（与队伍线上填报相同）；周期、状态、滞后由系统重算，不取表中的值。</div>
      {err && <div className="notice">{err}</div>}
      {!res && !err && <div className="loading">正在校验…</div>}
      {res && (
        <>
          <div className="chips" style={{ padding: 0 }}>
            {(['新增', '更新', '无变化', '退回队伍', '跳过'] as const).map((a) => (
              <span key={a} className="chip"><span>{a}</span> {res.count[a] || 0}</span>
            ))}
          </div>
          <div className="msg">
            通过校验的行直接汇总；有阻断项的行生成草稿，<b>退回对应队伍修改</b>；井号或队号为空、文件内重复、他队已登记的行跳过。
            {res.new_teams.length > 0 && <> 将新建 {res.new_teams.length} 支队伍：{res.new_teams.slice(0, 12).join('、')}{res.new_teams.length > 12 ? ' 等' : ''}。</>}
          </div>
          <label className="small muted" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <input type="checkbox" checked={only} onChange={(e) => setOnly(e.target.checked)} />只看有问题的行
          </label>
          <div className="scroll-x" style={{ maxHeight: 380, overflowY: 'auto', border: '1px solid var(--sep)', borderRadius: 12 }}>
            <div style={{ minWidth: 720 }}>
              <div className="grid-row grid-head" style={{ gridTemplateColumns: IMP_COLS, position: 'sticky', top: 0, background: 'var(--card)', zIndex: 1 }}>
                <span>行</span><span>井号</span><span>队号</span><span>处理</span><span>状态</span><span>说明</span>
              </div>
              {rows.map((x) => (
                <div key={x.row} className="grid-row" style={{ gridTemplateColumns: IMP_COLS, minHeight: 40, fontSize: 13, borderBottom: '1px solid var(--sep2)', padding: '6px 24px' }}>
                  <span className="muted num">{x.row}</span><span className="bold">{x.well_id || '—'}</span>
                  <span>{x.team_no}{x.new_team && <> <span className="tag">新队伍</span></>}</span>
                  <span><span className={'tag ' + ACTION_TAG[x.action]}>{x.action}</span></span>
                  <span>{x.status}</span>
                  <span className="text3" style={{ lineHeight: '18px' }}>{x.skip || x.checks.map((c) => `${c.level}：${c.message}`).join('；') || '—'}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="btn-row">
            <button type="button" className="btn" onClick={onClose}>取消</button>
            <button type="button" className="btn primary" disabled={busy || writes === 0} onClick={commit}>
              {busy ? '导入中…' : writes ? `确认导入（写入 ${writes} 行）` : '没有需要写入的行'}
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}
