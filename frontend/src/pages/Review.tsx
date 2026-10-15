import { useState } from 'react';
import { api, type Report, type ReviewRow } from '../api';
import type { PageProps } from '../App';
import { Drawer, fmt, Icon, PageHead, Seg, useLoad, useToast, yuan } from '../ui';

const COLS = '0.8fr 0.9fr 1.1fr 0.6fr 0.8fr 1.6fr 24px';
type Tab = '已提交' | '已审核' | '驳回';

export default function Review({ me }: PageProps) {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('已提交');
  const list = useLoad(() => api.get<ReviewRow[]>(`/api/review/reports?status=${encodeURIComponent(tab)}`), [tab]);
  const [open, setOpen] = useState<number | null>(null);
  const detail = useLoad(() => (open === null ? Promise.resolve(null) : api.get<Report>(`/api/reports/${open}`)), [open]);
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const canReview = me.role === 'dept' || me.role === 'finance';

  const act = async (kind: 'approve' | 'reject') => {
    if (kind === 'reject' && !comment.trim()) {
      toast('驳回必须填写意见', true);
      return;
    }
    setBusy(true);
    try {
      const r = await api.post<Report>(`/api/reports/${open}/${kind}`, { comment: comment || null });
      toast(kind === 'approve'
        ? `审核通过，规则引擎生成 ${r.batch?.created ?? 0} 笔、冲销 ${r.batch?.reversed ?? 0} 笔事项，金额变动 ${yuan(r.batch?.amount_change ?? 0)} 元`
        : '已驳回，退回基层队修改');
      setOpen(null);
      setComment('');
      list.reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };

  const d = detail.data && detail.data.report_id === open ? detail.data : null;
  return (
    <>
      <PageHead title="日报审核" sub="按整张日报审核，通过后其中的事实一起生效，并触发规则引擎计算金额" />
      <Seg value={tab} onChange={setTab} label="审核状态" options={[{ id: '已提交', label: '待审核' }, { id: '已审核', label: '已通过' }, { id: '驳回', label: '已驳回' }]} />
      <section className="card list">
        <div className="scroll-x">
          <div style={{ minWidth: 760 }}>
            <div className="grid-row grid-head" style={{ gridTemplateColumns: COLS }}>
              <span>井号</span><span>日期</span><span>队伍</span><span>版本</span><span className="r">进尺（米）</span><span>校验提示</span><span />
            </div>
            {list.data?.length === 0 && <div className="empty">没有{tab === '已提交' ? '待审核' : tab}的日报</div>}
            {list.data?.map((r) => (
              <button key={r.report_id} type="button" className="grid-row line" style={{ gridTemplateColumns: COLS }} onClick={() => setOpen(r.report_id)}>
                <span className="bold">{r.well_id}</span><span className="num">{r.report_date}</span><span>{r.team}</span><span>v{r.version}</span>
                <span className="r num">{fmt(r.depth_end - r.depth_start, 0)}</span>
                <span className="text3 small">{r.alerts.length ? r.alerts.map((a) => a.level + '：' + a.message).join('；') : (r.review_comment || '无')}</span>
                {Icon.chevron()}
              </button>
            ))}
          </div>
        </div>
      </section>
      <Drawer open={open !== null} onClose={() => setOpen(null)} label="日报详情"
        head={<>
          <div className="hint">成本日报 · {d?.header.team}</div>
          <div style={{ fontSize: 24, fontWeight: 700 }}>{d ? `${d.well_id} · ${d.report_date}` : '加载中…'}</div>
          {d && <div className="small muted">版本 {d.version} · {d.status} · {d.header.phase}{d.supersedes_report_id ? ' · 更正版本' : ''}</div>}
        </>}
        foot={canReview && d?.status === '已提交' ? (
          <>
            <textarea className="input" style={{ height: 64, padding: 10, flexBasis: '100%' }} placeholder="审核意见（驳回时必填）" value={comment} onChange={(e) => setComment(e.target.value)} />
            <button type="button" className="btn danger" style={{ flex: 1 }} disabled={busy} onClick={() => act('reject')}>驳回</button>
            <button type="button" className="btn primary" style={{ flex: 2 }} disabled={busy} onClick={() => act('approve')}>审核通过</button>
          </>
        ) : undefined}>
        {d && (
          <>
            {d.supersedes_report_id && <div className="notice">本张为更正版本：通过后原版本事实冲销，金额按新事实重算。</div>}
            <section className="sec">
              <div className="sec-label">进尺与井深</div>
              <div className="kvline"><span>井深</span><span className="num">{fmt(d.depth_start, 0)} → {fmt(d.depth_end, 0)} 米</span></div>
              <div className="kvline"><span>进尺</span><span className="num bold">{fmt(d.depth_end - d.depth_start, 0)} 米</span></div>
            </section>
            <section className="sec">
              <div className="sec-label">工况时间</div>
              {Object.entries(d.hours).map(([k, v]) => <div key={k} className="kvline"><span>{k}</span><span className="num">{v} 小时</span></div>)}
            </section>
            <section className="sec">
              <div className="sec-label">报量行</div>
              {Object.entries(d.lines).length === 0 && <div className="hint">无</div>}
              {Object.entries(d.lines).map(([k, v]) => {
                const ld = d.line_defs.find((x) => x.key === k);
                const note = d.notes?.[k];
                return <div key={k} className="kvline" style={{ gridTemplateColumns: '150px 1fr' }}><span>{ld ? `${ld.group} · ${ld.label}` : k}</span><span className="num">{v} {ld?.unit ?? ''}{note ? ` · ${ld?.needs_doc ? '单据' : '设备'} ${note}` : ''}</span></div>;
              })}
            </section>
            <section className="sec">
              <div className="sec-label">校验结果</div>
              {d.checks.length === 0 && <div className="msg">全部校验通过</div>}
              {d.checks.map((c, i) => <div key={i} className={'msg' + (c.level === '阻断' ? ' block' : '')}>· {c.level}：{c.message}</div>)}
            </section>
            <section className="sec">
              <div className="sec-label">处理记录</div>
              {d.history.map((h, i) => <div key={i} className="msg">{h.operated_at.slice(5, 16)} · {h.action} · {h.operator}{h.comment ? '：' + h.comment : ''}</div>)}
            </section>
          </>
        )}
      </Drawer>
    </>
  );
}
