import { useState, type FormEvent } from 'react';
import { api, setToken, type Me } from './api';
import { Icon, Modal, useToast } from './ui';

export function LoginPage({ onLogin }: { onLogin: (me: Me) => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) {
      setErr('请输入登录名和密码');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post<{ token: string; user: Me }>('/api/auth/login', { username: username.trim(), password });
      setToken(r.token);
      onLogin(r.user);
    } catch (e2) {
      setErr((e2 as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login-wrap">
      <form className="login card" onSubmit={submit} aria-label="登录">
        <div className="brand">
          <div className="brand-mark">{Icon.brand()}</div>
          <div>
            <div className="brand-name">全要素经营管理系统 v3.0</div>
            <div className="brand-sub">钻井单井经营管控</div>
          </div>
        </div>
        <label className="field">登录名
          <input className="input" autoComplete="username" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} />
        </label>
        <label className="field">密码
          <input className="input" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {err && <div className="notice" role="alert">{err}</div>}
        <button type="submit" className="btn primary" disabled={busy}>{busy ? '登录中…' : '登录'}</button>
        <div className="small muted" style={{ textAlign: 'center' }}>账号由管理端分配；初始密码首次登录后须修改</div>
      </form>
    </div>
  );
}

/** 修改密码。forced 为首次登录（初始密码）时强制修改，不能关闭，只能退出。 */
export function PasswordModal({ open, forced, onClose, onDone, onLogout }: {
  open: boolean; forced: boolean; onClose: () => void; onDone: () => void; onLogout: () => void;
}) {
  const toast = useToast();
  const [f, setF] = useState({ old: '', next: '', again: '' });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (f.next !== f.again) {
      setErr('两次输入的新密码不一致');
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await api.post('/api/auth/password', { old_password: f.old, new_password: f.next });
      toast('密码已修改');
      setF({ old: '', next: '', again: '' });
      onDone();
    } catch (e2) {
      setErr((e2 as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} onClose={forced ? () => {} : onClose} closable={!forced} title={forced ? '首次登录，请修改初始密码' : '修改密码'}>
      <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <label className="field">原密码<input className="input" type="password" autoComplete="current-password" value={f.old} onChange={(e) => setF({ ...f, old: e.target.value })} /></label>
        <label className="field">新密码（至少 8 位，含字母和数字）<input className="input" type="password" autoComplete="new-password" value={f.next} onChange={(e) => setF({ ...f, next: e.target.value })} /></label>
        <label className="field">再次输入新密码<input className="input" type="password" autoComplete="new-password" value={f.again} onChange={(e) => setF({ ...f, again: e.target.value })} /></label>
        {err && <div className="notice" role="alert">{err}</div>}
        <div className="btn-row">
          {forced ? <button type="button" className="btn" onClick={onLogout}>退出登录</button> : <button type="button" className="btn" onClick={onClose}>取消</button>}
          <button type="submit" className="btn primary" disabled={busy}>{busy ? '保存中…' : '确认修改'}</button>
        </div>
      </form>
    </Modal>
  );
}
