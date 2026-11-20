import { useEffect, useState } from 'react';
import { api, type AccountRow, type RevealedPassword } from '../api';
import { Modal, useLoad, useToast } from '../ui';

interface Admin { users: AccountRow[]; teams: { team_no: string; name: string }[]; depts: string[] }
const COLS = '1fr 1fr 0.9fr 1.3fr 0.8fr 1fr 1.9fr';
const ROLES = [
  { id: 'finance', label: '管理端（普通账户，提交规则）' },
  { id: 'dept', label: '归口部室（本部室规则确认，提交变化依据）' },
  { id: 'admin', label: '系统管理员' },
  { id: 'team', label: '填报端（钻井队）' },
];

/** 账号管理：管理端新建、重置密码、停用 / 启用。系统管理员可查看当前密码，列表本身不带密码。 */
export default function Accounts({ meId, canManage = true, canReveal = false }: { meId: string; canManage?: boolean; canReveal?: boolean }) {
  const toast = useToast();
  const { data, reload } = useLoad(() => api.get<Admin>('/api/admin/users'), []);
  const [creating, setCreating] = useState(false);
  const [resetting, setResetting] = useState<AccountRow | null>(null);
  const [revealing, setRevealing] = useState<AccountRow | null>(null);
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
        <h2>账号管理<span className="hint" style={{ fontWeight: 400, marginLeft: 8 }}>{users.length} 个账号{users.some((u) => u.is_debug && u.enabled) ? ' · 调试账号 test 正式上线前请停用' : ''}{canReveal && !canManage ? ' · 可查看当前密码（写入操作日志，不含密码）。新建、重置和停用仍由管理端操作' : ''}</span></h2>
        {canManage && <button type="button" className="btn primary sm" onClick={() => setCreating(true)}>新建账号</button>}
      </div>
      <div className="scroll-x">
        <div className="nowrap" style={{ minWidth: 980 }}>
          <div className="grid-row grid-head" style={{ gridTemplateColumns: COLS }}>
            <span>登录名</span><span>姓名</span><span>角色</span><span>数据范围</span><span>状态</span><span>最近登录</span><span>操作</span>
          </div>
          {users.map((u) => (
            <div key={u.user_id} className="grid-row line" style={{ gridTemplateColumns: COLS, opacity: u.enabled ? 1 : 0.6 }}>
              <span className="bold">{u.username}{u.is_debug ? <> <span className="tag warn">调试</span></> : null}</span><span>{u.name}</span>
              <span>{u.is_debug ? '可切换四个演示身份' : u.role_name}</span><span className="text3">{u.scope_label}</span>
              <span>{!u.enabled ? <span className="tag bad">已停用</span> : u.must_change ? <span className="tag warn">待改初始密码</span> : <span className="tag good">正常</span>}</span>
              <span className="muted num small">{u.last_login ? u.last_login.slice(0, 16) : '从未登录'}</span>
              <span className="btn-row" style={{ flexWrap: 'nowrap' }}>
                {canReveal && <button type="button" className="btn sm" onClick={() => setRevealing(u)}>查看当前密码</button>}
                {canManage && <button type="button" className="btn sm" onClick={() => setResetting(u)}>重置密码</button>}
                {canManage && u.user_id !== meId && <button type="button" className={'btn sm' + (u.enabled ? ' danger' : '')} onClick={() => toggle(u)}>{u.enabled ? '停用' : '启用'}</button>}
              </span>
            </div>
          ))}
        </div>
      </div>
      {data && canManage && <CreateModal open={creating} admin={data} onClose={() => setCreating(false)} onDone={() => { setCreating(false); reload(); }} />}
      {canManage && <ResetModal user={resetting} onClose={() => setResetting(null)} onDone={() => { setResetting(null); reload(); }} />}
      {canReveal && <RevealModal user={revealing} onClose={() => setRevealing(null)} />}
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
        {f.role === 'dept' && (
          <label className="field">归口部室
            <select className="input" value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })}>
              <option value="">请选择</option>
              {admin.depts.map((d) => <option key={d} value={d}>{d === '技术' ? '工程（技术）' : d === '人事' ? '人力（人事）' : d}</option>)}
            </select>
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

function RevealModal({ user, onClose }: { user: AccountRow | null; onClose: () => void }) {
  const toast = useToast();
  const [state, setState] = useState<RevealedPassword | null>(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    if (!user) {
      setState(null);
      setErr('');
      return;
    }
    let alive = true;
    setState(null);
    setErr('');
    api.post<RevealedPassword>(`/api/admin/users/${encodeURIComponent(user.user_id)}/reveal-password`)
      .then((r) => { if (alive) setState(r); })
      .catch((e: Error) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [user?.user_id]);
  const copy = async () => {
    if (!state?.password) return;
    try {
      await navigator.clipboard.writeText(state.password);
      toast('已复制到剪贴板');
    } catch {
      toast('复制失败，请手动选择密码', true);
    }
  };
  return (
    <Modal open={!!user} onClose={onClose} title={`查看当前密码 · ${user?.username ?? ''}`}>
      {!state && !err && <div className="hint">正在读取…</div>}
      {err && <div className="notice">{err}</div>}
      {state?.recoverable && state.password && (
        <>
          <label className="field">当前密码
            <input className="input" readOnly value={state.password} autoComplete="off" spellCheck={false} aria-label="当前密码" />
          </label>
          <div className="hint">本次查看已记入操作日志，日志里只有查看人和账号，没有密码本身。</div>
          <div className="btn-row">
            <button type="button" className="btn" onClick={onClose}>关闭</button>
            <button type="button" className="btn primary" onClick={copy}>复制</button>
          </div>
        </>
      )}
      {state && !state.recoverable && (
        <>
          <div className="notice">{state.reason || '无法查看当前密码。'}</div>
          <div className="hint">不会显示编造的密码。</div>
          <div className="btn-row"><button type="button" className="btn" onClick={onClose}>关闭</button></div>
        </>
      )}
    </Modal>
  );
}
