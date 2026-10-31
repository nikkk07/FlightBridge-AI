import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { ago, workerState } from '../nav';
import { Loading, Problem, useLoad, when } from '../lib';

const RUNNER = { 'github-actions': 'GitHub Actions (cloud, every 15 min)', mac: 'Office Mac' };
const runnerLabel = (r) => RUNNER[r] || (r?.startsWith('manual') ? 'Manual run' : r);
const KIND = { cycle: 'Full cycle', research_school: 'Research one school', find_websites: 'Find websites', research_batch: 'Read websites and score' };
const ACTION = {
  website_search: (p) => (p.status === 'found' ? `Found the official website ${p.domain}` : 'Searched for a website – no confirmed match'),
  research_stored: (p) => `Read ${p.pages} page${p.pages === 1 ? '' : 's'}, found ${p.contacts} public email${p.contacts === 1 ? '' : 's'}`,
  qualified: (p) => `Scored ${Math.round(p.score)}/100 – ${p.recommended_action === 'draft_outreach_for_approval' ? 'good fit' : 'needs a person'}`,
  draft_created: () => 'Drafted a first email for approval',
  draft_skipped: (p) => (p.reason === 'no_approved_initial_template' ? 'Draft waiting – no approved first-email template yet' : `Draft skipped – ${p.reason}`),
  organisation_created: () => 'Added from the regulator register',
  school_qualified: () => 'Marked as a good fit by a person',
  school_rejected: (p) => `Marked as not a fit${p.reason ? ` – ${p.reason}` : ''}`,
  regulatory_verified_by_human: () => 'Approval confirmed on the regulator’s site',
  approval_approved: () => 'Email approved',
  approval_edited_approved: () => 'Email edited and approved',
  template_approved: () => 'Email template approved',
  kill_switch_set: (p) => (p.engaged ? 'Sending paused' : 'Kill switch released'),
};

// Collapse runs of the same automatic event (e.g. 35 schools imported at once) into one line.
function groupActivity(items, max = 14) {
  const out = [];
  for (const e of items) {
    const last = out[out.length - 1];
    if (last && last.action === e.action && last.actor_type === e.actor_type && e.actor_type === 'system'
        && ['organisation_created', 'website_search', 'research_stored', 'qualified', 'draft_created'].includes(e.action)) {
      last.group.push(e);
    } else out.push({ ...e, group: [e] });
  }
  return out.slice(0, max);
}
const GROUP_LABEL = {
  organisation_created: (n) => `${n} schools added from the regulator register`,
  website_search: (n) => `Searched for ${n} school websites`,
  research_stored: (n) => `Read the websites of ${n} schools`,
  qualified: (n) => `Scored ${n} schools`,
  draft_created: (n) => `Drafted ${n} first emails for approval`,
};

function summarise(stats = {}) {
  const r = stats.research || {};
  const bits = [];
  if (r.find_websites?.found) bits.push(`${r.find_websites.found} website${r.find_websites.found === 1 ? '' : 's'} found`);
  if (r.research?.researched) bits.push(`${r.research.researched} site${r.research.researched === 1 ? '' : 's'} read`);
  if (r.research?.emails_found) bits.push(`${r.research.emails_found} email${r.research.emails_found === 1 ? '' : 's'}`);
  if (r.qualify?.qualified) bits.push(`${r.qualify.qualified} good fit`);
  if (r.draft?.drafts_created) bits.push(`${r.draft.drafts_created} draft${r.draft.drafts_created === 1 ? '' : 's'}`);
  if (stats.inbound?.stored) bits.push(`${stats.inbound.stored} repl${stats.inbound.stored === 1 ? 'y' : 'ies'}`);
  if (stats.jobs?.length) bits.push(`${stats.jobs.length} run-now request${stats.jobs.length === 1 ? '' : 's'}`);
  return bits.length ? bits.join(', ') : 'Nothing new to do';
}

function errorsOf(run) {
  const st = run.stats || {};
  const errs = Object.entries({ ...st, ...(st.research || {}) }).filter(([k]) => k.endsWith('_error')).map(([, v]) => v);
  if (run.error) errs.unshift(run.error);
  return [...new Set(errs)];
}

function explain(err) {
  if (/Places API \(New\) has not been used|SERVICE_DISABLED|not been used in project/i.test(err)) return 'Google Places API (New) is not enabled in the Google Cloud project yet. Enable it, and website search starts on the next cycle.';
  if (/GOOGLE_PLACES_API_KEY is not set/.test(err)) return 'The Google Places key is not set for the worker, so websites can’t be searched. Add it to the worker secrets.';
  return err;
}

export default function Automation() {
  const { isAdmin } = useAuth();
  const a = useLoad(() => api.automation(), []);
  const [msg, setMsg] = useState(null);
  useEffect(() => { const t = setInterval(() => a.reload(), 20000); return () => clearInterval(t); }, [a.reload]); // live refresh

  const run = async (kind, label) => {
    setMsg(null);
    try { const r = await api.runNow(kind); setMsg({ ok: true, text: `${label}: ${r.message}` }); a.reload(); }
    catch (e) { setMsg({ ok: false, text: e.message }); }
  };

  const d = a.data;
  const ws = workerState(d);
  const lastErrors = d?.runs?.[0] ? errorsOf(d.runs[0]) : [];

  return (
    <div className="page">
      <header className="page-head">
        <h1>Automation</h1>
        <p className="muted">FlightBridge works through the schools on its own: it finds each school’s official website, reads it for public contact details and facts, scores the fit, and drafts the first email from your template. Every email still waits for approval.</p>
      </header>
      {a.loading && !d && <Loading />}
      <Problem error={a.error} onRetry={a.reload} />
      {msg && <p className={msg.ok ? 'form-ok' : 'form-error'}>{msg.text}</p>}
      {d && (
        <>
          <section className={`panel wstatus wstatus-${ws.tone}`}>
            <div className="wstatus-main">
              <i className={`dot dot-${ws.tone} dot-lg`} aria-hidden="true" />
              <div>
                <h2>{ws.label}</h2>
                <p className="muted">{ws.detail}{d.runners?.length ? ` – runs on ${d.runners.map((r) => runnerLabel(r.runner.split('@')[0])).join(' and ')}` : ''}</p>
              </div>
            </div>
            <button className="btn" onClick={() => run('cycle', 'Full cycle')}>Run a cycle now</button>
            {lastErrors.map((e) => <p key={e} className="note wstatus-note">{explain(e)}</p>)}
            {!d.runs.length && (
              <div className="setup">
                <p>To switch it on, add these secrets to the FlightBridge-AI repository on GitHub (Settings, Secrets and variables, Actions):</p>
                <ul>
                  <li><code>FLIGHTBRIDGE_DATABASE_URL</code> – the Supabase connection string (transaction pooler)</li>
                  <li><code>GOOGLE_PLACES_API_KEY</code> – for finding school websites</li>
                  <li><code>GROQ_API_KEY</code> or <code>GEMINI_API_KEY</code>, with variable <code>FB_LLM_PROVIDER</code> – optional, free tier, for website summaries</li>
                </ul>
                <p className="muted small">The worker then runs every 15 minutes in GitHub’s cloud, whether or not the Mac is on.</p>
              </div>
            )}
          </section>

          <section className="pipeline" aria-label="Pipeline">
            <Stage n={d.backlog.need_website} title="Find the official website" text="Google Places suggests a site; it’s kept only if the site itself names the school."
              action={() => run('find_websites', 'Find websites')} actionLabel="Find websites now" on={d.autopilot?.find_websites} />
            <Stage n={d.backlog.need_research} title="Read the website" text="Contact and admissions pages, robots.txt respected. Only addresses published on the school’s own domain."
              action={() => run('research_batch', 'Read websites')} actionLabel="Read websites now" on={d.autopilot?.research} />
            <Stage n={d.backlog.need_scoring} title="Score the fit" text="Regulator approval, CPL courses, contact, international students, fleet. Unclear cases go to a person." on={d.autopilot?.qualify} />
            <Stage title="Draft for approval" text="Only from your approved template. Lands in Approvals – nothing is sent from here." link={<Link to="/approvals" className="link">Open approvals</Link>} on={d.autopilot?.draft} />
          </section>

          {isAdmin && d.autopilot && <AutopilotSettings cfg={d.autopilot} onSaved={a.reload} />}

          <div className="split-2">
            <section className="panel">
              <h2>Recent cycles</h2>
              {d.runs.length === 0 ? <p className="muted">No cycles yet.</p> : (
                <ul className="runs">
                  {d.runs.map((r) => {
                    const errs = errorsOf(r);
                    return (
                      <li key={r.id}>
                        <span className={`run-dot run-${r.status}`} aria-hidden="true" />
                        <div className="grow">
                          <b>{summarise(r.stats)}</b>
                          <small className="muted">{when(r.started_at, true)} – {runnerLabel(r.runner.split('@')[0])}{r.trigger !== 'schedule' ? `, ${r.trigger === 'queue' ? 'run now' : 'manual'}` : ''}{r.finished_at ? `, took ${Math.max(1, Math.round((new Date(r.finished_at) - new Date(r.started_at)) / 1000))} s` : ', running'}</small>
                          {errs.length > 0 && <small className="run-err">{explain(errs[0])}</small>}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              )}
            </section>
            <section className="panel">
              <h2>What happened</h2>
              {d.activity.length === 0 ? <p className="muted">Nothing yet.</p> : (
                <ul className="feed">
                  {groupActivity(d.activity).map((e, i) => (
                    <li key={i}>
                      <small className="muted">{ago(e.at)}{e.actor_type === 'employee' ? ', by a person' : ''}</small>
                      {e.group.length > 2 ? <span>{GROUP_LABEL[e.action](e.group.length)}</span> : <span>
                        {e.org_name && e.entity === 'organisation' ? <><Link to={`/schools/${e.entity_id}`}>{e.org_name.replace(/\s+T\/A\s+/i, ' – ')}</Link>: </> : null}
                        {(ACTION[e.action] || (() => e.action.replace(/_/g, ' ')))(e.payload || {})}
                      </span>}
                    </li>
                  ))}
                </ul>
              )}
              {d.jobs.length > 0 && (
                <>
                  <h3>Run-now requests</h3>
                  <ul className="feed">
                    {d.jobs.slice(0, 8).map((j) => (
                      <li key={j.id}>
                        <small className="muted">{ago(j.requested_at)}{j.requested_by_name ? `, ${j.requested_by_name}` : ''}</small>
                        <span>{KIND[j.kind]}{j.org_name ? ` – ${j.org_name}` : ''}: <b>{{ queued: 'waiting', running: 'running', done: 'done', failed: 'failed' }[j.status]}</b></span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </section>
          </div>
        </>
      )}
    </div>
  );
}

function Stage({ n, title, text, action, actionLabel, link, on }) {
  return (
    <div className={`stage ${on === false ? 'stage-off' : ''}`}>
      <div className="stage-top">
        <h3>{title}</h3>
        {n != null && <span className="stage-n" title="Waiting at this step">{n}</span>}
      </div>
      <p className="muted small">{text}</p>
      {on === false && <p className="small stage-paused">Paused in autopilot settings</p>}
      {action && <button className="btn btn-small btn-quiet" onClick={action}>{actionLabel}</button>}
      {link}
    </div>
  );
}

function AutopilotSettings({ cfg, onSaved }) {
  const [v, setV] = useState({ find_websites: true, research: true, qualify: true, draft: true, batch_size: 8, ...cfg });
  const [msg, setMsg] = useState(null);
  const save = async () => {
    setMsg(null);
    try { await api.setAutopilot({ ...v, batch_size: Number(v.batch_size) }); setMsg({ ok: true, text: 'Saved. Applies from the next cycle.' }); onSaved(); }
    catch (e) { setMsg({ ok: false, text: e.message }); }
  };
  const box = (k, label) => (
    <label className="check-row"><input type="checkbox" checked={!!v[k]} onChange={(e) => setV({ ...v, [k]: e.target.checked })} /> {label}</label>
  );
  return (
    <section className="panel">
      <h2>Autopilot</h2>
      <p className="muted">Which steps run by themselves on every cycle. Sending is never part of autopilot.</p>
      <div className="autopilot">
        {box('find_websites', 'Find websites')}
        {box('research', 'Read websites')}
        {box('qualify', 'Score the fit')}
        {box('draft', 'Draft first emails')}
        <label className="batch">Schools per step, per cycle
          <input type="number" min={1} max={50} value={v.batch_size} onChange={(e) => setV({ ...v, batch_size: e.target.value })} />
        </label>
        <button className="btn btn-quiet" onClick={save}>Save</button>
      </div>
      {msg && <p className={msg.ok ? 'form-ok' : 'form-error'}>{msg.text}</p>}
    </section>
  );
}
