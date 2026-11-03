import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { Empty, Loading, Problem, RegRef, STATUS_LABEL, Status, countryName, useLoad, when } from '../lib';

export const FACTS = {
  address_conspicuously_published: 'Email address is clearly published on the school’s site',
  no_refusal_statement_near_address: 'No “no marketing emails” notice near the address',
  message_relevant_to_business_role: 'Our message is relevant to this person’s role',
  recipient_is_corporate_subscriber: 'Address belongs to a company, not an individual',
  legitimate_interests_assessment_done: 'Legitimate-interests assessment done',
  refusal_statement_present: 'Site says it refuses marketing emails',
  recipient_is_sole_trader_or_non_scottish_partnership: 'School is a sole trader or (non-Scottish) partnership',
};

const SECTIONS = [
  ['eligible_verified', 'Eligible', 'Approved by the official register or confirmed by a person.'],
  ['eligible_claimed', 'Check needed', 'The school’s website claims the approval – confirm it on the regulator’s site.'],
  ['unqualified', 'Unqualified', 'No approval found for this country’s standard, or not a pilot-training school.'],
  ['unknown', 'Not checked yet', 'Website not read yet. Automation will get to these.'],
];
export const ELIG_LABEL = { eligible_verified: 'Eligible', eligible_claimed: 'Check needed', unqualified: 'Unqualified', unknown: 'Not checked' };

export default function Schools() {
  const { id } = useParams();
  const [sp, setSp] = useSearchParams();
  const [q, setQ] = useState(sp.get('q') || '');
  const country = sp.get('country') || '';
  const status = sp.get('status') || '';
  const region = sp.get('region') || '';
  const area = sp.get('area') || '';
  const section = sp.get('section') || '';
  const list = useLoad(() => api.schools({ country, status, region, area_id: area, q: sp.get('q') || '', limit: 1000 }),
    [country, status, region, area, sp.get('q')]);
  const areas = useLoad(() => api.areas().catch(() => []), []);
  const areaRow = (areas.data || []).find((a) => String(a.id) === area);
  const countries = useMemo(() => [...new Set((list.data || []).map((s) => s.country))].sort(), [list.data]);
  const isDemand = areaRow?.kind === 'demand_hub';
  const counts = useMemo(() => {
    const c = {}; (list.data || []).forEach((s) => { c[s.eligibility || 'unknown'] = (c[s.eligibility || 'unknown'] || 0) + 1; }); return c;
  }, [list.data]);
  const activeSection = isDemand ? '' : (section || SECTIONS.find(([k]) => counts[k])?.[0] || 'eligible_verified');
  const rows = (list.data || []).filter((s) => !activeSection || (s.eligibility || 'unknown') === activeSection);
  const [runMsg, setRunMsg] = useState(null);

  useEffect(() => {
    const t = setTimeout(() => {
      const next = new URLSearchParams(sp);
      q ? next.set('q', q) : next.delete('q');
      if (next.toString() !== sp.toString()) setSp(next, { replace: true });
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);
  useEffect(() => setRunMsg(null), [area]);

  const setParam = (k, v) => { const n = new URLSearchParams(sp); v ? n.set(k, v) : n.delete(k); setSp(n); };
  const searchArea = async () => {
    setRunMsg(null);
    try { const r = await api.runNow('search_area', null, Number(area)); setRunMsg({ ok: true, text: r.message }); }
    catch (e) { setRunMsg({ ok: false, text: e.message }); }
  };
  const title = areaRow ? areaRow.name : area === '0' ? `Elsewhere in ${region && region !== '__none__' ? region : countryName(country)}`
    : !country ? 'All flying schools' : region ? (region === '__none__' ? 'Region not stated' : region) : countryName(country);

  return (
    <div className={`page page-wide ${id ? 'has-detail' : ''}`}>
      <div className="master">
        <header className="page-head">
          {country && (
            <nav className="crumbs" aria-label="Location">
              <Link to="/schools">All flying schools</Link>
              {(region || area) && <> <span aria-hidden="true">/</span> <Link to={`/schools?country=${country}`}>{countryName(country)}</Link></>}
              {areaRow && <> <span aria-hidden="true">/</span> <Link to={`/schools?country=${country}&region=${encodeURIComponent(areaRow.region)}`}>{areaRow.region}</Link></>}
            </nav>
          )}
          <div className="head-row">
            <h1>{title}{isDemand && <span className="hub-tag hub-tag-lg">student-demand hub</span>}</h1>
            {areaRow && <button className="btn btn-quiet btn-small" onClick={searchArea}>Search this area now</button>}
          </div>
          <p className="muted">
            {areaRow
              ? <>{isDemand ? 'Aviation institutes and ground schools within' : 'Flying schools within'} {areaRow.radius_km} km. {areaRow.last_searched_at ? `Last searched ${when(areaRow.last_searched_at, true)}.` : 'Not searched yet – it’s in the queue.'}</>
              : 'Schools come from official registers and from searching each area. FlightBridge checks each one against its own country’s standard.'}
          </p>
          {runMsg && <p className={runMsg.ok ? 'form-ok' : 'form-error'}>{runMsg.text}</p>}
        </header>
        {!isDemand && (
          <div className="tabs" role="tablist">
            {SECTIONS.map(([k, label, help]) => (
              <button key={k} role="tab" title={help} aria-selected={activeSection === k} className={activeSection === k ? 'on' : ''}
                onClick={() => setParam('section', k)}>{label} <span className="tab-n">{counts[k] || 0}</span></button>
            ))}
          </div>
        )}
        <div className="filters">
          <input type="search" placeholder="Search name, website or approval number" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search schools" />
          {!area && (
            <select value={country} onChange={(e) => { const n = new URLSearchParams(sp); e.target.value ? n.set('country', e.target.value) : n.delete('country'); n.delete('region'); n.delete('area'); setSp(n); }} aria-label="Country">
              <option value="">All countries</option>
              {[...new Set([...countries, country].filter(Boolean))].map((c) => <option key={c} value={c}>{countryName(c)}</option>)}
            </select>
          )}
          <select value={status} onChange={(e) => setParam('status', e.target.value)} aria-label="Status">
            <option value="">Any status</option>
            {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        {activeSection && <p className="muted small section-help">{SECTIONS.find(([k]) => k === activeSection)?.[2]}</p>}
        {list.loading && <Loading />}
        <Problem error={list.error} onRetry={list.reload} />
        {list.data && rows.length === 0 && (
          <Empty title={list.data.length ? 'Nothing in this section' : areaRow ? 'No schools found here yet' : 'No schools match'}>
            {list.data.length ? 'Try another section above.' : areaRow ? 'Click “Search this area now”, or wait for the next automatic cycle.' : 'Clear a filter to see more.'}
          </Empty>
        )}
        {rows.length > 0 && (
          <table className="list">
            <thead>
              <tr><th>School</th><th>Approval</th><th>Status</th><th className="num">Contacts</th></tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.id} className={s.id === id ? 'sel' : ''}>
                  <td>
                    <Link to={`/schools/${s.id}?${sp}`} className="row-link">{s.name}</Link>
                    <small className="muted">{[s.city, s.region, countryName(s.country)].filter((x, i, a) => x && a.indexOf(x) === i).join(', ')}{s.website_domain ? ` – ${s.website_domain}` : ''}</small>
                  </td>
                  <td>{s.approval_ref ? <RegRef authority={s.authority_code} value={s.approval_ref} /> : <span className={`elig elig-${s.eligibility}`}>{ELIG_LABEL[s.eligibility] || 'Not checked'}</span>}</td>
                  <td><Status value={s.status} /></td>
                  <td className="num">{Number(s.contacts) + Number(s.phones || 0)}<span className="m-label"> contact details</span>
                    <small className="muted">{s.contacts} email{Number(s.contacts) === 1 ? '' : 's'}, {s.phones || 0} phone{Number(s.phones) === 1 ? '' : 's'}</small>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {id && <SchoolDetail id={id} onChanged={list.reload} back={`/schools?${sp}`} />}
    </div>
  );
}

function SchoolDetail({ id, onChanged, back }) {
  const d = useLoad(() => api.school(id), [id]);
  const nav = useNavigate();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');

  useEffect(() => { setMsg(null); setRejecting(false); setReason(''); }, [id]);

  const act = async (fn, ok) => {
    setBusy(true); setMsg(null);
    try { await fn(); setMsg({ ok: true, text: ok }); await d.reload(); onChanged(); }
    catch (e) { setMsg({ ok: false, text: e.message }); }
    finally { setBusy(false); }
  };

  const s = d.data;
  const locked = s && ['contacted', 'interested', 'negotiation', 'partner'].includes(s.status);

  return (
    <aside className="detail" aria-label="School details">
      <button className="link detail-close" onClick={() => nav(back)}>Close</button>
      {d.loading && !s && <Loading />}
      <Problem error={d.error} onRetry={d.reload} />
      {s && (
        <>
          <h2>{s.name}</h2>
          <p className="muted">{[s.city, s.region, countryName(s.country)].filter((x, i, a) => x && a.indexOf(x) === i).join(', ')}</p>
          <p>
            <Status value={s.status} />
            {s.website_domain && <> <a href={`https://${s.website_domain}`} target="_blank" rel="noreferrer noopener">{s.website_domain}</a></>}
          </p>
          {s.rejection_reason && <p className="note">Not a fit: {s.rejection_reason}</p>}

          <ResearchNow id={id} jobs={s.jobs} hasSite={!!s.website_domain} onQueued={() => d.reload()} />

          <Eligibility s={s} onChanged={() => { d.reload(); onChanged(); }} />

          <h3>What their website says</h3>
          {!s.profile ? (
            <p className="muted">{s.website_domain ? 'Not read yet. It’s in the queue for the next cycle.' : 'No official website confirmed yet.'}</p>
          ) : (
            <div className="profile">
              {s.profile.summary && <p>{s.profile.summary}</p>}
              <ul className="signals">
                {Object.entries(SIGNAL_LABEL).map(([k, label]) => {
                  const ev = (s.profile.evidence || []).find((e) => e.signal === k);
                  const yes = s.profile.signals?.[k];
                  return (
                    <li key={k} className={yes ? 'yes' : 'no'}>
                      <span>{yes ? label.yes : label.no}</span>
                      {ev && <small className="muted">“{ev.excerpt}” – <a href={ev.url} target="_blank" rel="noreferrer noopener">source</a></small>}
                    </li>
                  );
                })}
              </ul>
              <small className="muted">Read {when(s.profile.updated_at, true)}{s.profile.generator !== 'keywords-v1' ? `, summary by ${s.profile.generator.split(':')[0]} (free tier)` : ''}</small>
            </div>
          )}

          <h3>Regulator entry</h3>
          {s.regulatory.length === 0 && <p className="muted">No regulator record linked.</p>}
          {s.regulatory.map((r) => (
            <div key={r.id} className="reg">
              <RegRef authority={r.authority_code} value={r.approval_ref} />
              <p>
                {r.status === 'verified_by_human' ? 'Confirmed by a person' : r.status === 'listed_in_register' ? 'Listed in the published register' : r.status}
                {' – '}checked {when(r.checked_at)}, next check {when(r.next_check_due)}.{' '}
                <a href={r.source_url} target="_blank" rel="noreferrer noopener">Open source</a>
              </p>
              {Array.isArray(r.courses) && r.courses.length > 0 && (
                <details>
                  <summary>{r.courses.length} approved courses</summary>
                  <ul className="courses">{r.courses.map((c) => <li key={c}>{c}</li>)}</ul>
                </details>
              )}
              {r.status !== 'verified_by_human' && (
                <button className="btn btn-quiet" disabled={busy}
                  onClick={() => act(() => api.verifyRegulatory(id, r.id), 'Marked as confirmed on the regulator’s site.')}>
                  I checked this on the regulator’s site
                </button>
              )}
            </div>
          ))}

          <h3>Contacts</h3>
          {(s.phones || []).length > 0 && (
            <ul className="phones">
              {s.phones.map((ph) => (
                <li key={ph.phone}>
                  <a href={`tel:${ph.phone}`}>{ph.display}</a>
                  <small className="muted">{PHONE_SOURCE[ph.source] || ph.source}{ph.source_url && ph.source !== 'google_places' ? <> – <a href={ph.source_url} target="_blank" rel="noreferrer noopener">page</a></> : null}</small>
                </li>
              ))}
            </ul>
          )}
          {s.contacts.length === 0 && <p className="muted">No public email found yet. Emails come only from the school’s own website – never guessed.</p>}
          {s.contacts.map((c) => <Contact key={c.id} c={c} onAdded={() => d.reload()} />)}

          {s.qualification && (
            <>
              <h3>Fit score</h3>
              <p><b className="score">{Math.round(s.qualification.score)}</b> <span className="muted">out of 100, {s.qualification.confidence} confidence</span></p>
              {s.qualification.missing?.length > 0 && <p className="muted">Missing: {s.qualification.missing.join(', ')}</p>}
              {s.qualification.risks?.length > 0 && <p className="muted">Risks: {s.qualification.risks.join(', ')}</p>}
            </>
          )}

          <h3>Your decision</h3>
          {locked ? (
            <p className="muted">This school is already in conversation, so its status follows the email thread.</p>
          ) : rejecting ? (
            <div className="stack">
              <label>
                Why isn’t it a fit?
                <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: helicopter training only" maxLength={500} autoFocus />
              </label>
              <div className="btns">
                <button className="btn btn-danger" disabled={busy || !reason.trim()}
                  onClick={() => act(() => api.reviewSchool(id, 'rejected', reason), 'Marked as not a fit.').then(() => setRejecting(false))}>
                  Mark as not a fit
                </button>
                <button className="btn btn-quiet" onClick={() => setRejecting(false)}>Cancel</button>
              </div>
            </div>
          ) : (
            <div className="btns">
              <button className="btn" disabled={busy || s.status === 'qualified'} onClick={() => act(() => api.reviewSchool(id, 'qualified'), 'Marked as a good fit.')}>Good fit</button>
              <button className="btn btn-quiet" disabled={busy || s.status === 'needs_human_review'} onClick={() => act(() => api.reviewSchool(id, 'needs_human_review'), 'Flagged for another look.')}>Needs a look</button>
              <button className="btn btn-quiet" disabled={busy} onClick={() => setRejecting(true)}>Not a fit</button>
            </div>
          )}
          {msg && <p className={msg.ok ? 'form-ok' : 'form-error'} role="status">{msg.text}</p>}

          {s.history.length > 0 && (
            <>
              <h3>History</h3>
              <ul className="history">
                {s.history.map((h, i) => (
                  <li key={i}><span className="muted">{when(h.at, true)}</span> {h.action.replace(/_/g, ' ')}{h.payload?.reason ? ` – ${h.payload.reason}` : ''}</li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </aside>
  );
}

const PHONE_SOURCE = { website: 'From their website', regulator: 'From the regulator’s register', google_places: 'From Google', human: 'Added by a person' };

function Eligibility({ s, onChanged }) {
  const [mode, setMode] = useState(null);
  const [ref, setRef] = useState('');
  const [url, setUrl] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  useEffect(() => { setMode(null); setMsg(null); setRef(''); setUrl(''); setNote(''); }, [s.id]);
  if (s.org_kind === 'ground_school') {
    return <><h3>Eligibility</h3><p className="muted">Aviation institute in a student-demand hub – not a flying school, so it isn’t checked against flying-school rules.</p></>;
  }
  const rule = s.eligibility_rule;
  const act = async (body) => {
    setBusy(true); setMsg(null);
    try { const r = await api.eligibilityDecision(s.id, body); setMsg({ ok: true, text: `Now: ${ELIG_LABEL[r.eligibility]}` }); setMode(null); onChanged(); }
    catch (e) { setMsg({ ok: false, text: e.message }); } finally { setBusy(false); }
  };
  return (
    <>
      <h3>Eligibility</h3>
      <p><span className={`elig elig-${s.eligibility}`}>{ELIG_LABEL[s.eligibility] || 'Not checked'}</span>
        {rule && <span className="muted small"> – standard: {rule.credential}</span>}</p>
      <ul className="reasons">
        {(s.eligibility_reasons || []).map((r, i) => (
          <li key={i}>{r.text}
            {r.excerpt && <small className="muted">“{r.excerpt}”</small>}
            {r.source_url && <small><a href={r.source_url} target="_blank" rel="noreferrer noopener">Source</a></small>}
          </li>
        ))}
      </ul>
      {rule && <p className="small muted">Check on: {rule.lookup.map(([label, href], i) => <span key={href}>{i ? ' · ' : ''}<a href={href} target="_blank" rel="noreferrer noopener">{label}</a></span>)}</p>}
      {mode === 'confirm' ? (
        <div className="stack">
          <label>Approval or certificate number (if shown)<input value={ref} onChange={(e) => setRef(e.target.value)} maxLength={80} /></label>
          <label>Page where you checked it<input type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder={rule?.lookup?.[0]?.[1]} /></label>
          <div className="btns">
            <button className="btn" disabled={busy} onClick={() => act({ decision: 'confirm', reference: ref || null, source_url: url || null })}>Confirm eligible</button>
            <button className="btn btn-quiet" onClick={() => setMode(null)}>Cancel</button>
          </div>
        </div>
      ) : mode === 'unqualified' ? (
        <div className="stack">
          <label>Why is it unqualified?<input value={note} onChange={(e) => setNote(e.target.value)} placeholder="For example: Part 61 only, no student visas" maxLength={500} autoFocus /></label>
          <div className="btns">
            <button className="btn btn-danger" disabled={busy || !note.trim()} onClick={() => act({ decision: 'unqualified', note })}>Mark unqualified</button>
            <button className="btn btn-quiet" onClick={() => setMode(null)}>Cancel</button>
          </div>
        </div>
      ) : (
        <div className="btns">
          {s.eligibility !== 'eligible_verified' && rule && <button className="btn btn-small" onClick={() => setMode('confirm')}>I checked – it’s approved</button>}
          {s.eligibility !== 'unqualified' && <button className="btn btn-small btn-quiet" onClick={() => setMode('unqualified')}>Mark unqualified</button>}
          <button className="btn btn-small btn-quiet" disabled={busy} onClick={() => act({ decision: 'recheck' })}>Re-check</button>
        </div>
      )}
      {msg && <p className={msg.ok ? 'form-ok small' : 'form-error small'}>{msg.text}</p>}
    </>
  );
}

const SIGNAL_LABEL = {
  has_cpl: { yes: 'Mentions CPL training', no: 'No CPL mention found' },
  intl_students: { yes: 'Takes international students', no: 'No mention of international students' },
  fleet_published: { yes: 'Publishes its fleet', no: 'Fleet not published' },
  accommodation: { yes: 'Offers accommodation', no: 'No accommodation mentioned' },
  india_mention: { yes: 'Mentions India or DGCA', no: 'No India/DGCA mention' },
};

function ResearchNow({ id, jobs, hasSite, onQueued }) {
  const [msg, setMsg] = useState(null);
  const pending = (jobs || []).find((j) => j.kind === 'research_school' && (j.status === 'queued' || j.status === 'running'));
  const go = async () => {
    setMsg(null);
    try { const r = await api.runNow('research_school', id); setMsg({ ok: true, text: r.message }); onQueued(); }
    catch (e) { setMsg({ ok: false, text: e.message }); }
  };
  return (
    <div className="research-now">
      {pending ? <p className="muted small">Research {pending.status === 'running' ? 'is running now' : 'is queued'} – requested {when(pending.requested_at, true)}.</p>
        : <button className="btn btn-quiet btn-small" onClick={go}>{hasSite ? 'Read the website again now' : 'Find the website and research now'}</button>}
      {msg && <p className={msg.ok ? 'form-ok small' : 'form-error small'}>{msg.text}</p>}
    </div>
  );
}

function Contact({ c, onAdded }) {
  const [open, setOpen] = useState(false);
  const [fact, setFact] = useState(Object.keys(FACTS)[0]);
  const [url, setUrl] = useState(c.source_url || '');
  const [excerpt, setExcerpt] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  const save = async (e) => {
    e.preventDefault(); setBusy(true); setErr('');
    try {
      await api.addEvidence({ contact_id: c.id, fact, source_url: url, excerpt });
      setOpen(false); setExcerpt(''); onAdded();
    } catch (x) { setErr(x.message); } finally { setBusy(false); }
  };

  return (
    <div className="contact">
      <div className="contact-line">
        <b>{c.email}</b>
        <span className="muted">{c.is_role_address ? 'Shared inbox' : 'Named person'}{c.role ? `, ${c.role}` : ''}</span>
      </div>
      <p className="muted small">Found on <a href={c.source_url} target="_blank" rel="noreferrer noopener">{c.source_url}</a></p>
      {c.evidence?.length > 0 && (
        <ul className="evidence">
          {c.evidence.map((e, i) => (
            <li key={i} className={new Date(e.expires_at) < new Date() ? 'expired' : ''}>
              {FACTS[e.fact] || e.fact}
              <small className="muted">{e.method === 'human_verified' ? 'checked by a person' : 'found automatically'}, valid until {when(e.expires_at)}</small>
            </li>
          ))}
        </ul>
      )}
      {!open ? (
        <button className="link" onClick={() => setOpen(true)}>Record something you checked</button>
      ) : (
        <form className="stack" onSubmit={save}>
          <label>What did you confirm?
            <select value={fact} onChange={(e) => setFact(e.target.value)}>
              {Object.entries(FACTS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
          <label>Page where you saw it
            <input type="url" value={url} onChange={(e) => setUrl(e.target.value)} required />
          </label>
          <label>Exact text from the page
            <textarea rows={3} value={excerpt} onChange={(e) => setExcerpt(e.target.value)} required minLength={3} />
          </label>
          {err && <p className="form-error">{err}</p>}
          <div className="btns">
            <button className="btn" disabled={busy || excerpt.trim().length < 3}>Save</button>
            <button type="button" className="btn btn-quiet" onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </form>
      )}
    </div>
  );
}
