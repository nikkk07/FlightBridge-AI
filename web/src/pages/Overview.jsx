import { Link } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { Loading, Problem, Risk, countryName, useLoad, when } from '../lib';
import { ago, workerState } from '../nav';

const STAGES = [
  ['Found in a register', ['discovered', 'research_pending', 'research_completed', 'authority_verified', 'contact_verified']],
  ['Needs a look', ['needs_human_review']],
  ['Good fit', ['qualified', 'ready_for_outreach']],
  ['Contacted', ['contacted']],
  ['In conversation', ['interested', 'negotiation']],
  ['Partners', ['partner']],
];

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
}

export default function Overview() {
  const { me } = useAuth();
  const s = useLoad(() => Promise.all([api.summary(), api.approvals('open'), api.inbox({ limit: 8 }), api.metrics(), api.automation().catch(() => null)]), []);
  const first = (me?.name || '').split(' ')[0];

  return (
    <div className="page">
      <header className="page-head">
        <h1>{greeting()}{first ? `, ${first}` : ''}</h1>
        <p className="muted">{new Date().toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' })}</p>
      </header>
      {s.loading && <Loading />}
      <Problem error={s.error} onRetry={s.reload} />
      {s.data && <Body data={s.data} />}
    </div>
  );
}

function Body({ data: [summary, approvals, inbox, metrics, automation] }) {
  const orgs = summary.organisations || {};
  const total = Object.values(orgs).reduce((a, b) => a + b, 0);
  const needLook = orgs.needs_human_review || 0;
  const attention = inbox.filter((m) => m.risk === 'RED' || m.risk === 'AMBER');

  const ws = workerState(automation);
  const recent = (automation?.activity || []).filter((e) => e.actor_type === 'system').slice(0, 1)[0];
  return (
    <>
      <Link to="/automation" className={`autobar autobar-${ws.tone}`}>
        <i className={`dot dot-${ws.tone}`} aria-hidden="true" />
        <span><b>{ws.label}.</b> {ws.detail}{recent ? `. Latest: ${recent.org_name ? recent.org_name.replace(/\s+T\/A\s+/i, ' – ') + ' – ' : ''}${recent.action.replace(/_/g, ' ')} ${ago(recent.at)}` : ''}</span>
        {automation?.backlog && <span className="muted autobar-q">{automation.backlog.need_website + automation.backlog.need_research + automation.backlog.need_scoring} schools in the pipeline</span>}
      </Link>
      <section className="todo">
        <Link to="/approvals" className="todo-item">
          <span className="todo-n">{approvals.length}</span>
          <span>{approvals.length === 1 ? 'email waiting for approval' : 'emails waiting for approval'}</span>
        </Link>
        <Link to="/inbox" className="todo-item">
          <span className="todo-n">{attention.length}</span>
          <span>{attention.length === 1 ? 'reply that needs reading' : 'replies that need reading'}</span>
        </Link>
        <Link to="/schools?status=needs_human_review" className="todo-item">
          <span className="todo-n">{needLook}</span>
          <span>{needLook === 1 ? 'school to check by hand' : 'schools to check by hand'}</span>
        </Link>
      </section>

      <div className="split-2">
        <section className="panel">
          <div className="panel-head">
            <h2>Where the schools are</h2>
            <Link to="/schools" className="link">All {total} schools</Link>
          </div>
          {total === 0 ? (
            <p className="muted">No schools imported yet. The UK register (35 schools offering CPL training) is ready to import from the FlightBridge repository.</p>
          ) : (
            <ol className="stages">
              {STAGES.map(([label, keys]) => {
                const n = keys.reduce((a, k) => a + (orgs[k] || 0), 0);
                return (
                  <li key={label}>
                    <span>{label}</span>
                    <span className="bar" aria-hidden="true"><i style={{ width: `${total ? (n / total) * 100 : 0}%` }} /></span>
                    <b>{n}</b>
                  </li>
                );
              })}
              {orgs.rejected ? <li className="muted"><span>Not a fit</span><span /><b>{orgs.rejected}</b></li> : null}
            </ol>
          )}
        </section>

        <section className="panel">
          <div className="panel-head"><h2>By country</h2></div>
          {Object.keys(metrics.schools_by_country || {}).length === 0 ? (
            <p className="muted">Nothing yet.</p>
          ) : (
            <table className="plain">
              <tbody>
                {Object.entries(metrics.schools_by_country).map(([c, n]) => (
                  <tr key={c}><td>{countryName(c)}</td><td className="num">{n}</td></tr>
                ))}
              </tbody>
            </table>
          )}
          <dl className="facts">
            <div><dt>Schools contacted</dt><dd>{metrics.schools_contacted}</dd></div>
            <div><dt>Replied</dt><dd>{metrics.schools_replied}</dd></div>
            <div><dt>Reply rate</dt><dd>{metrics.response_rate == null ? '—' : `${Math.round(metrics.response_rate * 100)}%`}</dd></div>
          </dl>
        </section>
      </div>

      <section className="panel">
        <div className="panel-head">
          <h2>Latest replies</h2>
          <Link to="/inbox" className="link">Open replies</Link>
        </div>
        {inbox.length === 0 ? (
          <p className="muted">No replies yet. They’ll appear here once outreach starts.</p>
        ) : (
          <ul className="rows">
            {inbox.slice(0, 6).map((m) => (
              <li key={m.id}>
                <Risk value={m.risk} />
                <span className="grow"><b>{m.org_name || m.from_address}</b> <span className="muted">{m.subject}</span></span>
                <span className="muted">{when(m.occurred_at, true)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
