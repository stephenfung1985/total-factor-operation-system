import { useState } from 'react';
import { api, type Report, type ReviewRow } from '../api';
import type { PageProps } from '../App';
import { Drawer, fmt, Icon, PageHead, Seg, useLoad, useToast, yuan } from '../ui';

const COLS = '0.8fr 0.9fr 1.1fr 0.6fr 0.8fr 1.8fr 24px';
type Tab = 'todo' | 'effective' | 'returned';
type Summary = Record<Tab, number>;

/** 日报监控：日报提交即生效（系统校验通过），管理端事后抽查有警告的日报，发现问题退回更正或冲销退回。 */
export default function Review(_: PageProps) {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('todo');
  const sum = useLoad(() => api.get<Summary>('/api/monitor/summary'), [tab]);
  const list = useLoad(() => api.get<ReviewRow[]>(`/api/monitor/reports?tab=${tab}`), [tab]);
  const [open, setOpen] = useState<number | null>(null);
  const detail = useLoad(() => (open === null ? Promise.resolve(null) : api.get<Report>(`/api/reports/${open}`)), [open]);
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);

  const act = async (path: string, payload: object, ok: (r: Report) => string) => {
    setBusy(true);
    try {
      const r = await api.post<Report>(`/api/reports/${open}/${path}`, payload);
      toast(ok(r));
      setOpen(null);
      setComment('');
      list.reload();
      sum.reload();
    } catch (e) {
      toast((e as Error).message, true);
    } finally {
      setBusy(false);
    }
  };
  const needReason = () => {
    if (comment.trim()) return true;
    toast('退回必须填写原因', true);
    return false;
  };
  const amt = (r: Report) => (r.batch ? `，金额变动 ${yuan(r.batch.amount_change)} 元` : '');

  const d = detail.data && detail.data.report_id === open ? detail.data : null;
  const n = sum.data;
  return (
    <>
      <PageHead title="日报监控" sub="日报提交时系统校验，阻断项改完即生效；有警告的进入待处理，由管理端抽查，发现问题退回更正或冲销退回" />
      <Seg value={tab} onChange={setTab} label="分类" options={[
        { id: 'todo', label: `待处理${n ? ` ${n.todo}` : ''}` },
        { id: 'effective', label: '已生效' },
        { id: 'returned', label: `已退回${n ? ` ${n.returned}` : ''}` },
      ]} />
      <section className="card list">
        <div className="scroll-x">
          <div style={{ minWidth: 760 }}>
            <div className="grid-row grid-head" style={{ gridTemplateColumns: COLS }}>
              <span>井号</span><span>日期</span><span>队伍</span><span>版本</span><span className="r">进尺（米）</span><span>{tab === 'returned' ? '退回原因' : '校验警告'}</span><span />
            </div>
            {list.data?.length === 0 && <div className="empty">{tab === 'todo' ? '没有待处理的日报' : '没有日报'}</div>}
            {list.data?.map((r) => (
              <button key={r.report_id} type="button" className="grid-row line" style={{ gridTemplateColumns: COLS }} onClick={() => setOpen(r.report_id)}>
                <span className="bold">{r.well_id}</span><span className="num">{r.report_date}</span><span>{r.team}</span>
                <span>v{r.version}{r.status === '已提交' ? <span className="tag warn" style={{ marginLeft: 6 }}>未生效</span> : r.checked_at ? <span className="tag good" style={{ marginLeft: 6 }}>已抽查</span> : null}</span>
                <span className="r num">{fmt(r.depth_end - r.depth_start, 0)}</span>
                <span className="text3 small">{tab === 'returned' ? (r.review_comment || '').replace(/^管理[层端]退回：/, '') : r.alerts.length ? r.alerts.map((a) => a.message).join('；') : '无'}</span>
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
          {d && <div className="small muted">版本 {d.version} · {d.status === '已审核' ? '已生效' : d.status} · {d.header.phase}{d.supersedes_report_id ? ' · 更正版本' : ''}</div>}
        </>}
        foot={d && (d.status === '已审核' || d.status === '已提交') ? (
          <>
            <textarea className="input" style={{ height: 64, padding: 10, flexBasis: '100%' }} placeholder="说明（退回时必填，队伍可见）" value={comment} onChange={(e) => setComment(e.target.value)} />
            {d.status === '已审核' && (
              <button type="button" className="btn" style={{ flex: 1 }} disabled={busy}
                onClick={() => needReason() && act('return', { comment, reverse: false }, () => '已退回更正：原日报保持生效，队伍提交更正版本后替代')}>退回更正</button>
            )}
            <button type="button" className="btn danger" style={{ flex: 1 }} disabled={busy}
              onClick={() => needReason() && act('return', { comment, reverse: true }, (r) => `已冲销退回${amt(r)}；队伍可修改后重新提交`)}>冲销退回</button>
            {d.status === '已提交'
              ? <button type="button" className="btn primary" style={{ flex: 1 }} disabled={busy} onClick={() => act('confirm', { comment }, (r) => `已确认生效${amt(r)}`)}>确认生效</button>
              : !d.checked_at && <button type="button" className="btn primary" style={{ flex: 1 }} disabled={busy} onClick={() => act('check', { comment }, () => '已抽查确认')}>抽查无误</button>}
          </>
        ) : undefined}>
        {d && (
          <>
            {d.supersedes_report_id && <div className="notice">本张为更正版本：生效时原版本事实冲销，金额按新事实重算。</div>}
            {d.review_comment && <div className="notice">{d.review_comment}</div>}
            {d.checked_at && <div className="notice info">已抽查：{d.checked_by} · {d.checked_at.slice(0, 16)}</div>}
            <section className="sec">
              <div className="sec-label">进尺与井深</div>
              <div className="kvline"><span>井深</span><span className="num">{fmt(d.depth_start, 0)} → {fmt(d.depth_end, 0)} 米</span></div>
              <div className="kvline"><span>进尺</span><span className="num bold">{fmt(d.depth_end - d.depth_start, 0)} 米</span></div>
            </section>
            <section className="sec">
              <div className="sec-label">工况时间</div>
              {Object.entries(d.hours).filter(([, v]) => Number(v) > 0).map(([k, v]) => <div key={k} className="kvline"><span>{k}</span><span className="num">{v} 小时</span></div>)}
            </section>
            <section className="sec">
              <div className="sec-label">报量</div>
              {Object.entries(d.lines).length === 0 && <div className="hint">无</div>}
              {Object.entries(d.lines).map(([k, v]) => {
                const ld = d.line_defs.find((x) => x.key === k);
                const note = d.notes?.[k];
                return <div key={k} className="kvline" style={{ gridTemplateColumns: '150px 1fr' }}><span>{ld ? ld.label : k}</span><span className="num">{v} {ld?.unit ?? ''}{note ? ` · ${note}` : ''}</span></div>;
              })}
            </section>
            <section className="sec">
              <div className="sec-label">提交时的校验结果</div>
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
