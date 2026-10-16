import { api } from '../api';
import type { PageProps } from '../App';
import { PageHead, useLoad } from '../ui';
import Accounts from './Accounts';

interface Perms {
  roles: { id: string; name: string }[];
  matrix: { name: string; cells: Record<string, string> }[];
  users: { user_id: string; role: string; who: string; scope_label: string }[];
}
const ORDER = ['finance', 'team'];
const CLS: Record<string, string> = { 编辑: 'edit', 审核: 'review', 查看: 'view' };

export default function Perm({ me }: PageProps) {
  const { data, error } = useLoad(() => api.get<Perms>('/api/admin/permissions'), []);
  const name = (id: string) => data?.roles.find((r) => r.id === id)?.name ?? id;
  const tpl = '1.6fr repeat(3, minmax(0, 1fr))';
  return (
    <>
      <PageHead title="角色与权限" sub="账号由管理端分配；功能权限按角色分配，数据按行控制：填报端只看本队，管理端看全部" />
      <Accounts meId={me.user_id} />
      {error && <div className="notice">{error}</div>}
      {data && (
        <>
          <div className="kpis" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}>
            {ORDER.map((id) => {
              const u = data.users.find((x) => x.role === id);
              return (
                <div key={id} className="kpi" style={{ gap: 6, padding: '18px 20px' }}>
                  <div style={{ fontSize: 15, fontWeight: 600 }}>{name(id)}</div>
                  <div className="hint">行级数据范围</div>
                  <div style={{ fontSize: 14 }}>{u?.scope_label}</div>
                  <div className="small muted">{u?.who}</div>
                </div>
              );
            })}
          </div>
          <section className="card list">
            <div className="card-head"><h2>功能权限矩阵</h2><div className="hint">编辑 · 审核 · 查看 · 无</div></div>
            <div className="scroll-x">
              <div className="nowrap" style={{ minWidth: 760 }}>
                <div className="grid-row grid-head" style={{ gridTemplateColumns: tpl }}><span>功能</span>{ORDER.map((id) => <span key={id}>{name(id)}</span>)}</div>
                {data.matrix.map((m) => (
                  <div key={m.name} className="grid-row" style={{ gridTemplateColumns: tpl, minHeight: 50, fontSize: 14, borderBottom: '1px solid var(--sep2)' }}>
                    <span style={{ fontWeight: 500 }}>{m.name}</span>
                    {ORDER.map((id) => <span key={id}>{m.cells[id] ? <span className={'perm ' + CLS[m.cells[id]]}>{m.cells[id]}</span> : <span style={{ color: 'var(--mute)' }}>—</span>}</span>)}
                  </div>
                ))}
              </div>
            </div>
          </section>
          <section className="card pad" style={{ gap: 8 }}>
            <h3>硬约束</h3>
            <div style={{ fontSize: 14, lineHeight: '22px' }}>任何角色都不能直接修改经济事项，金额只由规则引擎写入；数据库触发器同样禁止改写和删除事项。</div>
            <div style={{ fontSize: 14, lineHeight: '22px' }}>AI 只生成草稿，不能写入已发布规则表；已发布规则的价格与条件不能修改，只能新建版本。</div>
            <div style={{ fontSize: 14, lineHeight: '22px' }}>账号与权限由管理端统一管理。</div>
            <div style={{ fontSize: 14, lineHeight: '22px' }}>密码加盐散列存储；初始密码首次登录须修改；连续输错 5 次锁定 15 分钟；登录 12 小时后需重新登录。</div>
          </section>
        </>
      )}
    </>
  );
}
