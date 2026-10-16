import { useState } from 'react';
import { api, type AccountRow } from '../api';
import { Modal, useLoad, useToast } from '../ui';

interface Admin { users: AccountRow[]; teams: { team_no: string; name: string }[] }
const COLS = '1fr 1fr 0.9fr 1.4fr 0.8fr 1.1fr 1.6fr';
const ROLES = [{ id: 'finance', label: '管理层' }, { id: 'team', label: '基层钻井队' }];

/** 账号管理：新建、重置密码、停用 / 启用。密码只设置不显示，重置后用户须在首次登录时修改。 */
export default function Accounts({ meId }: { meId: string }) {
  const toast = useToast();
  const { data, reload } = useLoad(() => api.get<Admin>('/api/admin/users'), []);
  const [creating, setCreating] = useState(false);
  const [resetting, setResetting] = useState<AccountRow | null>(null);
  const toggle = async (u: AccountRow) => {
    try {
      await api.post(`/api/admin/users/${encodeURIComponent(u.user_id)}/enable`, { enabled: !u.enabled });
      toast(u.enabled ? `已停用 ${u.username}，其登录会话已失效` : `已启用 ${u.username}`);
      reload();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  const users = (data?.users || []).filter((u) => u.username);
  return (
    <section className="card list">
      <div className="card-head center">
        <h2>账号管理<span className="hint" style={{ fontWeight: 400, marginLeft: 8 }}>{users.length} 个账号{users.some((u) => u.is_debug && u.enabled) ? ' · 调试账号 test 正式上线前请停用' : ''}</span></h2>
        <button type="button" className="btn primary sm" onClick={() => setCreating(true)}>新建账号</button>
      </div>
      <div className="scroll-x">
        <div className="nowrap" style={{ minWidth: 900 }}>
          <div className="grid-row grid-head" style={{ gridTemplateColumns: COLS }}>
            <span>登录名</span><span>姓名</span><span>角色</span><span>数据范围</span><span>状态</span><span>最近登录</span><span>操作</span>
          </div>
          {users.map((u) => (
            <div key={u.user_id} className="grid-row line" style={{ gridTemplateColumns: COLS, opacity: u.enabled ? 1 : 0.6 }}>
              <span className="bold">{u.username}{u.is_debug ? <> <span className="tag warn">调试</span></> : null}</span><span>{u.name}</span>
              <span>{u.is_debug ? '可切换三个演示身份' : u.role_name}</span><span className="text3">{u.scope_label}</span>
              <span>{!u.enabled ? <span className="tag bad">已停用</span> : u.must_change ? <span className="tag warn">待改初始密码</span> : <span className="tag good">正常</span>}</span>
              <span className="muted num small">{u.last_login ? u.last_login.slice(0, 16) : '从未登录'}</span>
              <span className="btn-row" style={{ flexWrap: 'nowrap' }}>
                <button type="button" className="btn sm" onClick={() => setResetting(u)}>重置密码</button>
                {u.user_id !== meId && <button type="button" className={'btn sm' + (u.enabled ? ' danger' : '')} onClick={() => toggle(u)}>{u.enabled ? '停用' : '启用'}</button>}
              </span>
            </div>
          ))}
        </div>
      </div>
      {data && <CreateModal open={creating} admin={data} onClose={() => setCreating(false)} onDone={() => { setCreating(false); reload(); }} />}
      <ResetModal user={resetting} onClose={() => setResetting(null)} onDone={() => { setResetting(null); reload(); }} />
    </section>
  );
}

function CreateModal({ open, admin, onClose, onDone }: { open: boolean; admin: Admin; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [f, setF] = useState({ username: '', name: '', role: 'finance', scope: '', password: '' });
  const submit = async () => {
    try {
      const u = await api.post<AccountRow>('/api/admin/users', f);
      toast(`已新建 ${u.username}（${u.scope_label}），首次登录须修改初始密码`);
      setF({ username: '', name: '', role: 'finance', scope: '', password: '' });
      onDone();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  return (
    <Modal open={open} onClose={onClose} title="新建账号">
      <div className="form-grid" style={{ gridTemplateColumns: '1fr 1fr' }}>
        <label className="field">登录名<input className="input" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} placeholder="字母、数字" /></label>
        <label className="field">姓名（选填）<input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
        <label className="field">角色
          <select className="input" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value, scope: '' })}>
            {ROLES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
          </select>
        </label>
        {f.role === 'team' && (
          <label className="field">队号（不存在则新建队伍）
            <input className="input" list="dl-teams" value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })} />
            <datalist id="dl-teams">{admin.teams.map((t) => <option key={t.team_no} value={t.team_no}>{t.name}</option>)}</datalist>
          </label>
        )}
        <label className="field">初始密码<input className="input" type="password" autoComplete="new-password" value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} /></label>
      </div>
      <div className="btn-row"><button type="button" className="btn" onClick={onClose}>取消</button><button type="button" className="btn primary" onClick={submit}>新建</button></div>
    </Modal>
  );
}

function ResetModal({ user, onClose, onDone }: { user: AccountRow | null; onClose: () => void; onDone: () => void }) {
  const toast = useToast();
  const [pw, setPw] = useState('');
  const submit = async () => {
    try {
      await api.post(`/api/admin/users/${encodeURIComponent(user!.user_id)}/reset`, { password: pw });
      toast(`已重置 ${user!.username} 的密码，其登录会话已失效，下次登录须修改`);
      setPw('');
      onDone();
    } catch (e) {
      toast((e as Error).message, true);
    }
  };
  return (
    <Modal open={!!user} onClose={onClose} title={`重置密码 · ${user?.username ?? ''}`}>
      <label className="field">新的初始密码<input className="input" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} /></label>
      <div className="hint">重置后该账号的登录会话立即失效，下次登录须修改密码；连续输错导致的锁定同时解除。</div>
      <div className="btn-row"><button type="button" className="btn" onClick={onClose}>取消</button><button type="button" className="btn primary" onClick={submit}>重置</button></div>
    </Modal>
  );
}
