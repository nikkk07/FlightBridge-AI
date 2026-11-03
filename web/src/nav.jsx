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

function AreaSchools({ areaId, country }) {
  const [list, setList] = useState(null);
  useEffect(() => {
    api.schools({ area_id: areaId, limit: SCHOOLS_SHOWN + 1 }).then(setList, () => setList([]));
  }, [areaId]);
  const loc = useLocation();
  if (!list) return <li className="tree-note">Loading…</li>;
  if (!list.length) return <li className="tree-note">No schools found yet</li>;
  return (
    <>
      {list.slice(0, SCHOOLS_SHOWN).map((s) => (
        <li key={s.id}>
          <Link className={`tree-school ${loc.pathname === `/schools/${s.id}` ? 'active' : ''} te-${s.eligibility}`}
            to={`/schools/${s.id}?country=${country}&area=${areaId}`} title={s.name}>
            {s.name.replace(/\s+T\/A\s+/i, ' – ')}
          </Link>
        </li>
      ))}
      <li><Link className="tree-all" to={`/schools?country=${country}&area=${areaId}`}>View all flying schools</Link></li>
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
  const curA = onSchools ? sp.get('area') : null;
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
              <div className={`tree-row ${curC === ck && !curR && !curA ? 'active' : ''}`}>
                <button className="tree-toggle" onClick={() => toggle(ck)} aria-expanded={!!open[ck]} aria-label={`Show states in ${countryName(ck)}`}><Chevron open={open[ck]} /></button>
                <Link to={`/schools?country=${ck}`}>{countryName(ck)}</Link>
                <b>{c.total || ''}</b>
              </div>
              {open[ck] && (
                <ul>
                  {c.regions.map((r) => {
                    const rk = `${ck}/${r.region ?? '__none__'}`;
                    const regionParam = encodeURIComponent(r.region ?? '__none__');
                    return (
                      <li key={rk}>
                        <div className={`tree-row tree-l2 ${curC === ck && curR === (r.region ?? '__none__') && !curA ? 'active' : ''}`}>
                          <button className="tree-toggle" onClick={() => toggle(rk)} aria-expanded={!!open[rk]} aria-label={`Show areas in ${r.region || 'unspecified region'}`}><Chevron open={open[rk]} /></button>
                          <Link to={`/schools?country=${ck}&region=${regionParam}`}>{r.region || 'Region not stated'}</Link>
                          <b>{r.total || ''}</b>
                        </div>
                        {open[rk] && (
                          <ul className="tree-areas">
                            {(r.areas || []).map((a) => {
                              const ak = `area/${a.id}`;
                              return (
                                <li key={ak}>
                                  <div className={`tree-row tree-l3row ${String(a.id) === curA ? 'active' : ''} ${a.enabled ? '' : 'tree-off'}`}>
                                    <button className="tree-toggle" onClick={() => toggle(ak)} aria-expanded={!!open[ak]} aria-label={`Show schools in ${a.name}`}><Chevron open={open[ak]} /></button>
                                    <Link to={`/schools?country=${ck}&area=${a.id}`} title={a.kind === 'demand_hub' ? 'Student-demand hub' : 'Flying-school hub'}>
                                      {a.name}{a.kind === 'demand_hub' && <span className="hub-tag">demand</span>}
                                    </Link>
                                    <b>{a.total || ''}</b>
                                  </div>
                                  {open[ak] && <ul className="tree-l3"><AreaSchools areaId={a.id} country={ck} /></ul>}
                                </li>
                              );
                            })}
                            {r.unplaced > 0 && (
                              <li>
                                <Link className="tree-other" to={`/schools?country=${ck}&region=${regionParam}&area=0`}>Elsewhere in {r.region || 'this country'} <b>{r.unplaced}</b></Link>
                              </li>
                            )}
                          </ul>
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
      <Link to="/areas" className={`tree-manage ${loc.pathname === '/areas' ? 'active' : ''}`}>Manage areas</Link>
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
