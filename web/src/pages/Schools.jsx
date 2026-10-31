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

export default function Schools() {
  const { id } = useParams();
  const [sp, setSp] = useSearchParams();
  const [q, setQ] = useState(sp.get('q') || '');
  const country = sp.get('country') || '';
  const status = sp.get('status') || '';
  const list = useLoad(() => api.schools({ country, status, q: sp.get('q') || '' }), [country, status, sp.get('q')]);
  const countries = useMemo(() => [...new Set((list.data || []).map((s) => s.country))].sort(), [list.data]);

  useEffect(() => {
    const t = setTimeout(() => {
      const next = new URLSearchParams(sp);
      q ? next.set('q', q) : next.delete('q');
      if (next.toString() !== sp.toString()) setSp(next, { replace: true });
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  const setParam = (k, v) => { const n = new URLSearchParams(sp); v ? n.set(k, v) : n.delete(k); setSp(n); };

  return (
    <div className={`page page-wide ${id ? 'has-detail' : ''}`}>
      <div className="master">
        <header className="page-head">
          <h1>Schools</h1>
          <p className="muted">Each school is listed by its regulator. Check the entry, then mark it as a good fit or not.</p>
        </header>
        <div className="filters">
          <input type="search" placeholder="Search name, website or approval number" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search schools" />
          <select value={country} onChange={(e) => setParam('country', e.target.value)} aria-label="Country">
            <option value="">All countries</option>
            {[...new Set([...countries, country].filter(Boolean))].map((c) => <option key={c} value={c}>{countryName(c)}</option>)}
          </select>
          <select value={status} onChange={(e) => setParam('status', e.target.value)} aria-label="Status">
            <option value="">Any status</option>
            {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        {list.loading && <Loading />}
        <Problem error={list.error} onRetry={list.reload} />
        {list.data && list.data.length === 0 && (
          <Empty title={country || status || sp.get('q') ? 'No schools match these filters' : 'No schools yet'}>
            {country || status || sp.get('q') ? 'Clear a filter to see more.' : 'Import a regulator register to start. The UK list is ready in the FlightBridge repository.'}
          </Empty>
        )}
        {list.data && list.data.length > 0 && (
          <table className="list">
            <thead>
              <tr><th>School</th><th>Approval</th><th>Status</th><th className="num">Contacts</th></tr>
            </thead>
            <tbody>
              {list.data.map((s) => (
                <tr key={s.id} className={s.id === id ? 'sel' : ''}>
                  <td>
                    <Link to={`/schools/${s.id}?${sp}`} className="row-link">{s.name}</Link>
                    <small className="muted">{[s.city, countryName(s.country)].filter(Boolean).join(', ')}{s.website_domain ? ` – ${s.website_domain}` : ''}</small>
                  </td>
                  <td><RegRef authority={s.authority_code} value={s.approval_ref} /></td>
                  <td><Status value={s.status} /></td>
                  <td className="num">{s.contacts}<span className="m-label"> {Number(s.contacts) === 1 ? 'contact' : 'contacts'}</span></td>
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
          <p className="muted">{[s.city, countryName(s.country)].filter(Boolean).join(', ')}</p>
          <p>
            <Status value={s.status} />
            {s.website_domain && <> <a href={`https://${s.website_domain}`} target="_blank" rel="noreferrer noopener">{s.website_domain}</a></>}
          </p>
          {s.rejection_reason && <p className="note">Not a fit: {s.rejection_reason}</p>}

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
          {s.contacts.length === 0 && <p className="muted">No public email found yet. Contacts come only from the school’s own website.</p>}
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
