import { useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { Empty, Loading, Problem, countryName, useLoad, when } from '../lib';

const STATUS = {
  pending_checks: 'Waiting for checks', needs_review: 'Held – needs evidence', blocked: 'Blocked by checks', authorized: 'Cleared to send',
  sending: 'Sending', sent: 'Sent', send_unknown: 'Unconfirmed – being reconciled', failed: 'Failed', dry_run_rendered: 'Dry run (not sent)', cancelled: 'Cancelled',
};
const DECISION = { ALLOW: 'Passed', PASS: 'Passed', BLOCK: 'Blocked', FAIL: 'Failed', HUMAN_REVIEW: 'Needs a person' };

export default function Sent() {
  const { isAdmin } = useAuth();
  const list = useLoad(() => api.outbound(), []);
  const [msg, setMsg] = useState(null);

  const recheck = async (id) => {
    setMsg(null);
    try { await api.recheck(id); setMsg({ ok: true, text: 'Sent back through the checks.' }); list.reload(); }
    catch (e) { setMsg({ ok: false, text: e.message }); }
  };

  return (
    <div className="page">
      <header className="page-head">
        <h1>Sent & checks</h1>
        <p className="muted">Every approved email is checked against the rules for the school’s country and against its exact wording before it can leave.</p>
      </header>
      {msg && <p className={msg.ok ? 'form-ok' : 'form-error'}>{msg.text}</p>}
      {list.loading && <Loading />}
      <Problem error={list.error} onRetry={list.reload} />
      {list.data && list.data.length === 0 && <Empty title="Nothing has been sent">Approved emails will be listed here with the result of each check.</Empty>}
      {list.data && list.data.length > 0 && (
        <table className="list">
          <thead><tr><th>School</th><th>Status</th><th>Country rules</th><th>Wording</th><th /></tr></thead>
          <tbody>
            {list.data.map((m) => (
              <tr key={m.id}>
                <td>
                  <b>{m.org_name}</b>
                  <small className="muted">{m.email}, {countryName(m.country)} – {when(m.sent_at || m.created_at, true)}{m.ref_token ? ` – ${m.ref_token}` : ''}</small>
                </td>
                <td><span className={`status status-out-${m.status}`}>{STATUS[m.status] || m.status}</span></td>
                <td><Check decision={m.jurisdiction} reasons={m.jurisdiction_reasons} /></td>
                <td><Check decision={m.content} reasons={m.content_reasons} /></td>
                <td>{isAdmin && m.status === 'needs_review' && <button className="btn btn-small btn-quiet" onClick={() => recheck(m.id)}>Check again</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Check({ decision, reasons }) {
  if (!decision) return <span className="muted">Not run</span>;
  const list = Array.isArray(reasons) ? reasons : [];
  return (
    <span className={`check check-${decision.toLowerCase()}`}>
      {DECISION[decision] || decision}
      {list.length > 0 && <small className="muted">{list.map((r) => (typeof r === 'string' ? r : r.message || r.code)).join('; ')}</small>}
    </span>
  );
}
