import { useEffect, useState } from 'react';
import { Link, NavLink, useLocation, useSearchParams } from 'react-router-dom';
import { api } from './api';
import { useAuth } from './auth';
import { countryName } from './lib';

const OPEN_KEY = 'fb_nav_open';
const readOpen = () => { try { return JSON.parse(localStorage.getItem(OPEN_KEY) || '{}'); } catch { return {}; } };
const saveOpen = (v) => { try { localStorage.setItem(OPEN_KEY, JSON.stringify(v)); } catch { /* ignore */ } };
const SCHOOLS_SHOWN = 8;

export function useNavData() {
  const loc = useLocation();
  const [data, setData] = useState({ regions: [], summary: null, automation: null });
  useEffect(() => {
    let alive = true;
    const load = () => Promise.allSettled([api.regions(), api.summary(), api.automation()]).then(([r, s, a]) => {
      if (alive) setData({ regions: r.value || [], summary: s.value || null, automation: a.value || null });
    });
    load();
    const t = setInterval(load, 60000);
    return () => { alive = false; clearInterval(t); };
  }, [loc.pathname]);
  return data;
}

function Chevron({ open }) {
  return <svg className={`chev ${open ? 'chev-open' : ''}`} viewBox="0 0 10 10" width="10" height="10" aria-hidden="true"><path d="M3 1.5 6.5 5 3 8.5" fill="none" stroke="currentColor" strokeWidth="1.6" /></svg>;
}

function RegionSchools({ country, region }) {
  const [list, setList] = useState(null);
  useEffect(() => {
    api.schools({ country, region: region ?? '__none__', limit: SCHOOLS_SHOWN + 1 }).then(setList, () => setList([]));
  }, [country, region]);
  const loc = useLocation();
  if (!list) return <li className="tree-note">Loading…</li>;
  return (
    <>
      {list.slice(0, SCHOOLS_SHOWN).map((s) => (
        <li key={s.id}>
          <Link className={`tree-school ${loc.pathname === `/schools/${s.id}` ? 'active' : ''}`}
            to={`/schools/${s.id}?country=${country}${region ? `&region=${encodeURIComponent(region)}` : ''}`} title={s.name}>
            {s.name.replace(/\s+T\/A\s+/i, ' – ')}
          </Link>
        </li>
      ))}
      <li>
        <Link className="tree-all" to={`/schools?country=${country}&region=${encodeURIComponent(region ?? '__none__')}`}>View all flying schools</Link>
      </li>
    </>
  );
}

function SchoolTree({ regions }) {
  const [open, setOpen] = useState(readOpen);
  const [sp] = useSearchParams();
  const loc = useLocation();
  const toggle = (k) => setOpen((o) => { const n = { ...o, [k]: !o[k] }; saveOpen(n); return n; });
  const onSchools = loc.pathname.startsWith('/schools');
  const curC = onSchools ? sp.get('country') : null;
  const curR = onSchools ? sp.get('region') : null;
  const total = regions.reduce((a, c) => a + c.total, 0);

  return (
    <div className="tree">
      <Link to="/schools" className={`tree-row tree-root ${onSchools && !curC ? 'active' : ''}`}>
        <span>All flying schools</span><b>{total || ''}</b>
      </Link>
      <ul>
        {regions.map((c) => {
          const ck = c.country;
          return (
            <li key={ck}>
              <div className={`tree-row ${curC === ck && !curR ? 'active' : ''}`}>
                <button className="tree-toggle" onClick={() => toggle(ck)} aria-expanded={!!open[ck]} aria-label={`Show regions in ${countryName(ck)}`}><Chevron open={open[ck]} /></button>
                <Link to={`/schools?country=${ck}`}>{countryName(ck)}</Link>
                <b>{c.total}</b>
              </div>
              {open[ck] && (
                <ul>
                  {c.regions.map((r) => {
                    const rk = `${ck}/${r.region ?? '__none__'}`;
                    return (
                      <li key={rk}>
                        <div className={`tree-row tree-l2 ${curC === ck && curR === (r.region ?? '__none__') ? 'active' : ''}`}>
                          <button className="tree-toggle" onClick={() => toggle(rk)} aria-expanded={!!open[rk]} aria-label={`Show schools in ${r.region || 'unspecified region'}`}><Chevron open={open[rk]} /></button>
                          <Link to={`/schools?country=${ck}&region=${encodeURIComponent(r.region ?? '__none__')}`}>{r.region || 'Region not stated'}</Link>
                          <b>{r.total}</b>
                        </div>
                        {open[rk] && <ul className="tree-l3"><RegionSchools country={ck} region={r.region} /></ul>}
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ago(iso) {
  if (!iso) return null;
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  return m < 1 ? 'just now' : m < 60 ? `${m} min ago` : m < 1440 ? `${Math.round(m / 60)} h ago` : `${Math.round(m / 1440)} d ago`;
}

export function workerState(automation) {
  const last = automation?.runs?.[0];
  if (!last) return { tone: 'idle', label: 'Automation not running yet', detail: 'No worker has checked in.' };
  const mins = (Date.now() - new Date(last.started_at).getTime()) / 60000;
  if (last.status === 'running') return { tone: 'live', label: 'Working now', detail: `Started ${ago(last.started_at)} on ${last.runner.split('@')[0]}` };
  if (mins > 45) return { tone: 'warn', label: 'Automation is behind', detail: `Last cycle ${ago(last.started_at)}` };
  return { tone: last.status === 'ok' ? 'live' : 'warn', label: last.status === 'ok' ? 'Automation running' : 'Last cycle had problems', detail: `Last cycle ${ago(last.started_at)}` };
}
export { ago };

export function Sidebar({ data }) {
  const { isAdmin } = useAuth();
  const s = data.summary;
  const openApprovals = s?.approvals?.open || 0;
  const ws = workerState(data.automation);
  return (
    <nav className="nav">
        <div className="nav-group">
          <NavLink to="/" end>Today</NavLink>
          <NavLink to="/automation" className="nav-auto">
            <span>Automation</span><i className={`dot dot-${ws.tone}`} title={ws.label} />
          </NavLink>
        </div>
        <div className="nav-group">
          <p className="nav-title">Flying schools</p>
          <SchoolTree regions={data.regions} />
        </div>
        <div className="nav-group">
          <p className="nav-title">Outreach</p>
          <NavLink to="/approvals"><span>Approvals</span>{openApprovals > 0 && <b className="badge">{openApprovals}</b>}</NavLink>
          <NavLink to="/inbox">Replies</NavLink>
          <NavLink to="/templates">Email templates</NavLink>
          <NavLink to="/sent">Sent & checks</NavLink>
        </div>
        <div className="nav-group">
          <p className="nav-title">Control</p>
          <NavLink to="/settings">Sending & safety</NavLink>
          {isAdmin && <NavLink to="/team">Team</NavLink>}
        </div>
    </nav>
  );
}

export function SendingBox({ summary }) {
  if (!summary) return null;
  const off = summary.kill_switches?.outbound_kill_switch !== false;
  return (
    <div className={`sending ${off ? 'sending-off' : 'sending-armed'}`}>
      <span className="sending-dot" aria-hidden="true" />
      <div>
        <strong>{off ? 'Sending is paused' : 'Kill switch released'}</strong>
        <span>{off ? 'No email can leave the system.' : 'Server dry-run settings still apply.'}</span>
      </div>
    </div>
  );
}
