import { useCallback, useEffect, useState } from 'react';
import { api, hasToken, setToken, setUnauthorizedHandler, type Me } from './api';
import { LoginPage, PasswordModal } from './auth';
import { Icon, NAV_ICON, Seg, ToastHost } from './ui';
import Overview from './pages/Overview';
import DailyReport from './pages/DailyReport';
import Review from './pages/Review';
import Rules from './pages/Rules';
import WellReg from './pages/WellReg';
import MasterData from './pages/MasterData';
import Perm from './pages/Perm';
import Batches from './pages/Batches';
import BenchmarkPage from './pages/Benchmark';
import CostDetail from './pages/CostDetail';
import Allocation from './pages/Allocation';
import WellLibraryPage from './pages/WellLibrary';

export interface Route { screen: string; well?: string; item?: string; period?: string }
export interface PageProps { me: Me; route: Route; setRoute: (r: Route) => void }

function readHash(): Route {
  const [path, query] = window.location.hash.replace(/^#\/?/, '').split('?');
  const q = new URLSearchParams(query || '');
  return { screen: path || '', well: q.get('well') || undefined, item: q.get('item') || undefined, period: q.get('period') || undefined };
}

function writeHash(r: Route) {
  const q = new URLSearchParams();
  if (r.well) q.set('well', r.well);
  if (r.item) q.set('item', r.item);
  if (r.period) q.set('period', r.period);
  const s = q.toString();
  const h = '#/' + r.screen + (s ? '?' + s : '');
  if (window.location.hash !== h) window.history.pushState(null, '', h);
}

const store = {
  get: (k: string) => {
    try {
      return localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set: (k: string, v: string) => {
    try {
      localStorage.setItem(k, v);
    } catch {
      /* 隐私模式下忽略 */
    }
  },
};

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [checking, setChecking] = useState(hasToken());
  const [pwd, setPwd] = useState(false);
  const [theme, setTheme] = useState(() => store.get('tfos.theme') || (window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  const [route, setRouteState] = useState<Route>(readHash);
  const [err, setErr] = useState<string | null>(null);

  const setRoute = useCallback((r: Route) => {
    setRouteState(r);
    writeHash(r);
  }, []);

  const enter = useCallback((m: Me) => {
    setMe(m);
    setErr(null);
    const screens = m.nav.flatMap((g) => g.items.map((i) => i.id));
    const r = readHash();
    if (!screens.includes(r.screen)) setRoute({ screen: screens[0] });
    else setRouteState(r);
  }, [setRoute]);

  const switchIdentity = useCallback(async (userId: string) => {
    try {
      const m = await api.post<Me>('/api/auth/switch', { user_id: userId });
      setMe(m);
      const screens = m.nav.flatMap((g) => g.items.map((i) => i.id));
      setRoute({ screen: screens[0] });
    } catch (e) {
      setErr((e as Error).message);
    }
  }, [setRoute]);

  const logout = useCallback(async () => {
    try {
      await api.post('/api/auth/logout');
    } catch {
      /* 令牌已失效也照常退出 */
    }
    setToken(null);
    setMe(null);
    setPwd(false);
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => setMe(null));
    if (hasToken()) {
      api.get<Me>('/api/me').then(enter).catch((e: Error) => { if (hasToken()) setErr('无法连接后端服务：' + e.message); })
        .finally(() => setChecking(false));
    }
    const onPop = () => setRouteState(readHash());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, [enter]);

  useEffect(() => {
    window.scrollTo(0, 0);
  }, [route.screen, route.well, route.item]);

  useEffect(() => {
    store.set('tfos.theme', theme);
    document.body.style.background = theme === 'dark' ? '#000' : '#F5F5F7';
  }, [theme]);

  let page = null;
  if (me) {
    const p = { me, route, setRoute };
    page = {
      overview: <Overview {...p} />, report: <DailyReport {...p} />, review: <Review {...p} />, rules: <Rules {...p} />,
      wellreg: <WellReg {...p} />, masterdata: <MasterData {...p} />, perm: <Perm {...p} />, batches: <Batches {...p} />, benchmark: <BenchmarkPage {...p} />, costdetail: <CostDetail {...p} />, allocation: <Allocation {...p} />, wellinfo: <WellLibraryPage {...p} />,
    }[route.screen] ?? null;
  }

  const themeBtn = (
    <button type="button" className="icon-btn theme-btn" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
      aria-label={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'} title={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}>
      {theme === 'dark' ? Icon.sun() : Icon.moon()}
    </button>
  );

  if (!me) {
    return (
      <ToastHost>
        <div className={'app theme-' + theme}>
          <div style={{ position: 'absolute', top: 16, right: 24 }}>{themeBtn}</div>
          {checking ? <div className="loading">加载中…</div> : <LoginPage onLogin={enter} />}
          {err && <div className="notice" style={{ margin: '0 auto', maxWidth: 400 }}>{err}</div>}
        </div>
      </ToastHost>
    );
  }

  return (
    <ToastHost>
      <div className={'app theme-' + theme}>
        <header className="topbar">
          <div className="brand">
            <div className="brand-mark">{Icon.brand()}</div>
            <div>
              <div className="brand-name">全要素经营管理系统 v3.0</div>
              <div className="brand-sub">钻井单井经营管控</div>
            </div>
          </div>
          {me.debug && me.identities && (
            <div className="role-switch">
              <span className="tag warn">调试</span>
              <Seg large label="调试身份" value={me.user_id} onChange={switchIdentity}
                options={me.identities.map((i) => ({ id: i.user_id, label: i.role_name }))} />
            </div>
          )}
          <div className="user-box">
            <span className="user-chip" title={me.who}>
              <span className="avatar" aria-hidden="true">{(me.login_username || me.name || me.username).slice(0, 1).toUpperCase()}</span>
              {me.debug
                ? <span><b>{me.login_username}</b><span className="small muted"> · 调试账号</span></span>
                : <span><b>{me.name}</b><span className="small muted"> · {me.role_name}</span></span>}
            </span>
            <button type="button" className="btn sm" onClick={() => setPwd(true)}>修改密码</button>
            <button type="button" className="btn sm" onClick={logout}>退出</button>
            {themeBtn}
          </div>
        </header>
        <PasswordModal open={pwd || !!me.must_change} forced={!!me.must_change} onClose={() => setPwd(false)} onLogout={logout}
          onDone={() => { setPwd(false); setMe({ ...me, must_change: 0 }); }} />
        <div className="body">
          {me && (
            <aside className="side">
              <nav className="nav" aria-label="主导航">
                {me.nav.map((g) => (
                  <div key={g.label} className="nav-group">
                    <div className="nav-label">{g.label}</div>
                    {g.items.map((it) => (
                      <button key={it.id} type="button" className={'nav-item' + (route.screen === it.id ? ' on' : '')}
                        aria-current={route.screen === it.id ? 'page' : undefined} onClick={() => setRoute({ screen: it.id })}>
                        <span className="ic">{NAV_ICON[it.id]?.()}</span>
                        <span className="lbl">{it.label}</span>
                      </button>
                    ))}
                  </div>
                ))}
              </nav>
              <div className="who">
                <b>{me.who}</b>
                <span>数据范围：{me.scope_label}</span>
                <span>登录名：{me.debug ? `${me.login_username}（调试，当前身份：${me.role_name}）` : me.username}</span>
              </div>
            </aside>
          )}
          <main className="main">
            <div className="wrap">
              {err && <div className="notice">{err}</div>}
              <div key={me?.user_id} style={{ display: 'contents' }}>{page}</div>
            </div>
          </main>
        </div>
      </div>
    </ToastHost>
  );
}
