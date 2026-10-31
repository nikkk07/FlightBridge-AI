import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { Empty, Loading, Problem, RegRef, Risk, countryName, useLoad, when } from '../lib';
import { FACTS } from './Schools';

const TABS = [['open', 'Waiting'], ['approved', 'Approved'], ['edited_approved', 'Edited & approved'], ['more_research', 'Sent back'], ['rejected', 'Rejected']];
const KIND = { initial: 'First email', followup: 'Follow-up', reply: 'Reply' };

export default function Approvals() {
  const { id } = useParams();
  const [sp, setSp] = useSearchParams();
  const tab = sp.get('tab') || 'open';
  const list = useLoad(() => api.approvals(tab), [tab]);

  return (
    <div className={`page page-wide ${id ? 'has-detail' : ''}`}>
      <div className="master">
        <header className="page-head">
          <h1>Approvals</h1>
          <p className="muted">Nothing is emailed until it’s approved here, and even then every email passes the legal checks before it can go out.</p>
        </header>
        <div className="tabs" role="tablist">
          {TABS.map(([k, v]) => (
            <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'on' : ''} onClick={() => setSp({ tab: k })}>{v}</button>
          ))}
        </div>
        {list.loading && <Loading />}
        <Problem error={list.error} onRetry={list.reload} />
        {list.data && list.data.length === 0 && (
          <Empty title={tab === 'open' ? 'Nothing waiting for approval' : 'Nothing here yet'}>
            {tab === 'open' ? 'Drafts appear here once a school is a good fit and an email template is approved.' : null}
          </Empty>
        )}
        {list.data && list.data.length > 0 && (
          <ul className="queue">
            {list.data.map((a) => (
              <li key={a.id} className={a.id === id ? 'sel' : ''}>
                <Link to={`/approvals/${a.id}?tab=${tab}`}>
                  <span className="queue-top"><b>{a.org_name}</b><Risk value={a.risk} /></span>
                  <span className="muted">{KIND[a.kind] || a.kind} to {a.email}, {countryName(a.country)}</span>
                  <span className="queue-subject">{a.subject}</span>
                  <small className="muted">{a.escalation_reason} – {when(a.created_at, true)}</small>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
      {id && <ApprovalDetail id={id} onChanged={list.reload} back={`/approvals?tab=${tab}`} />}
    </div>
  );
}

function ApprovalDetail({ id, onChanged, back }) {
  const { isAdmin } = useAuth();
  const nav = useNavigate();
  const d = useLoad(() => api.approval(id), [id]);
  const [editing, setEditing] = useState(false);
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  useEffect(() => { setEditing(false); setMsg(null); setNote(''); }, [id]);
  useEffect(() => { if (d.data) { setSubject(d.data.draft_subject || ''); setBody(d.data.draft_body || ''); } }, [d.data]);

  const decide = async (action, extra = {}) => {
    setBusy(true); setMsg(null);
    try {
      const r = await api.decide(id, { action, note: note || undefined, ...extra });
      setMsg({ ok: true, text: r.message || 'Decision saved.' });
      setEditing(false); await d.reload(); onChanged();
    } catch (e) { setMsg({ ok: false, text: e.message }); }
    finally { setBusy(false); }
  };

  const t = d.data;
  const open = t?.status === 'open';
  const blocked = t?.draft_body?.includes('[[HUMAN RESPONSE REQUIRED]]');

  return (
    <aside className="detail" aria-label="Approval details">
      <button className="link detail-close" onClick={() => nav(back)}>Close</button>
      {d.loading && !t && <Loading />}
      <Problem error={d.error} onRetry={d.reload} />
      {t && (
        <>
          <h2>{t.organisation?.name}</h2>
          <p className="muted">{KIND[t.kind] || t.kind} to {t.contact?.email} – {countryName(t.organisation?.country)}</p>
          <p><Risk value={t.risk} /> <span className="muted">{t.escalation_reason}</span></p>

          {t.thread?.length > 0 && (
            <>
              <h3>Conversation so far</h3>
              <ol className="thread">
                {t.thread.map((m, i) => (
                  <li key={i} className={m.direction}>
                    <small className="muted">{m.direction === 'inbound' ? m.from_address : 'We One Aviation'} – {when(m.occurred_at, true)}</small>
                    <p>{m.body_text}</p>
                  </li>
                ))}
              </ol>
            </>
          )}

          <h3>{open ? 'Draft' : 'Email'}</h3>
          {editing ? (
            <div className="stack">
              <label>Subject<input value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={150} /></label>
              <label>Message<textarea rows={14} value={body} onChange={(e) => setBody(e.target.value)} /></label>
            </div>
          ) : (
            <div className="letter">
              <p className="letter-subject">{t.final_subject || t.draft_subject}</p>
              <pre>{t.final_body || t.draft_body}</pre>
              <p className="muted small">The legal footer (our address, how to opt out) is added automatically when the email is prepared.</p>
            </div>
          )}
          {blocked && open && <p className="note">This reply needs a person to write it. Edit the message before approving.</p>}

          <h3>Why this school</h3>
          {t.regulatory.map((r, i) => (
            <p key={i}><RegRef authority={r.authority_code} value={r.approval_ref} /> <span className="muted">{r.status === 'verified_by_human' ? 'confirmed by a person' : 'listed in register'}, checked {when(r.checked_at)}</span></p>
          ))}
          {t.qualification && <p className="muted">Fit score {Math.round(t.qualification.score)} / 100</p>}
          {t.contact && <p className="muted small">Address found on <a href={t.contact.source_url} target="_blank" rel="noreferrer noopener">{t.contact.source_url}</a></p>}
          {t.contact_evidence?.length > 0 && (
            <ul className="evidence">
              {t.contact_evidence.map((e, i) => <li key={i}>{FACTS[e.fact] || e.fact}<small className="muted">valid until {when(e.expires_at)}</small></li>)}
            </ul>
          )}

          {open && isAdmin && (
            <>
              <h3>Decision</h3>
              <label className="stack">Note for the record (optional)
                <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
              </label>
              <div className="btns">
                {editing ? (
                  <>
                    <button className="btn" disabled={busy || !subject.trim() || !body.trim()} onClick={() => decide('edit_approve', { subject, body })}>Approve edited email</button>
                    <button className="btn btn-quiet" onClick={() => setEditing(false)}>Cancel edit</button>
                  </>
                ) : (
                  <>
                    <button className="btn" disabled={busy || blocked} onClick={() => decide('approve')}>Approve</button>
                    <button className="btn btn-quiet" disabled={busy} onClick={() => setEditing(true)}>Edit</button>
                    <button className="btn btn-quiet" disabled={busy} onClick={() => decide('more_research')}>Send back for research</button>
                    <button className="btn btn-quiet btn-danger-text" disabled={busy} onClick={() => decide('reject')}>Reject</button>
                    {t.kind === 'reply' && <button className="btn btn-quiet" disabled={busy} onClick={() => decide('resolve')}>Handled outside FlightBridge</button>}
                  </>
                )}
              </div>
            </>
          )}
          {open && !isAdmin && <p className="note">Only an admin can approve or reject emails. You can still add evidence on the school’s page.</p>}
          {!open && <p className="muted">Decided {when(t.decided_at, true)}{t.decision_note ? ` – “${t.decision_note}”` : ''}.</p>}
          {msg && <p className={msg.ok ? 'form-ok' : 'form-error'} role="status">{msg.text}</p>}
        </>
      )}
    </aside>
  );
}
