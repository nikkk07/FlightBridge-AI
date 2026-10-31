import { useEffect, useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { Loading, Problem, useLoad, when } from '../lib';

const REASON = { unsubscribe: 'Asked to stop', rejection: 'Declined', complaint: 'Complaint', hard_bounce: 'Address doesn’t exist', legal: 'Legal', manual: 'Added by hand' };
const SCOPE = { contact: 'One address', organisation: 'Whole school', domain: 'Whole domain' };

export default function Settings() {
  const { isAdmin } = useAuth();
  const s = useLoad(() => Promise.all([api.settings(), api.suppressions()]), []);
  return (
    <div className="page">
      <header className="page-head">
        <h1>Sending & safety</h1>
        <p className="muted">Controls that decide whether anything can leave the system, and who must never be emailed.</p>
      </header>
      {s.loading && <Loading />}
      <Problem error={s.error} onRetry={s.reload} />
      {s.data && (
        <>
          <KillSwitch engaged={s.data[0].outbound_kill_switch !== false} isAdmin={isAdmin} onChanged={s.reload} />
          <Followups days={s.data[0].followup_schedule_days || [4, 9, 16]} hours={s.data[0].followup_business_hours} isAdmin={isAdmin} onChanged={s.reload} />
          <DoNotContact rows={s.data[1]} isAdmin={isAdmin} onChanged={s.reload} />
        </>
      )}
    </div>
  );
}

function KillSwitch({ engaged, isAdmin, onChanged }) {
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const set = async (v) => {
    setBusy(true); setMsg(null);
    try { const r = await api.setKillSwitch(v); setMsg({ ok: true, text: v ? 'Sending paused.' : r.note }); setConfirm(''); onChanged(); }
    catch (e) { setMsg({ ok: false, text: e.message }); } finally { setBusy(false); }
  };
  return (
    <section className={`panel switch ${engaged ? 'switch-off' : 'switch-on'}`}>
      <div>
        <h2>{engaged ? 'Sending is paused' : 'The kill switch is released'}</h2>
        <p className="muted">
          {engaged
            ? 'While paused, no email can be sent, whatever has been approved. This is the safe default.'
            : 'Emails still go out only if the server is out of dry-run mode and outbound sending is enabled there.'}
        </p>
      </div>
      {isAdmin && (engaged ? (
        <div className="stack switch-act">
          <label><span>Type <b>release</b> to allow sending</span>
            <input value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" />
          </label>
          <button className="btn btn-danger" disabled={busy || confirm.trim().toLowerCase() !== 'release'} onClick={() => set(false)}>Release kill switch</button>
        </div>
      ) : (
        <button className="btn" disabled={busy} onClick={() => set(true)}>Pause all sending</button>
      ))}
      {msg && <p className={msg.ok ? 'form-ok' : 'form-error'}>{msg.text}</p>}
    </section>
  );
}

function Followups({ days, hours, isAdmin, onChanged }) {
  const [val, setVal] = useState(days.join(', '));
  const [msg, setMsg] = useState(null);
  useEffect(() => setVal(days.join(', ')), [days.join(',')]); // eslint-disable-line react-hooks/exhaustive-deps
  const save = async (e) => {
    e.preventDefault(); setMsg(null);
    const parsed = val.split(/[,\s]+/).filter(Boolean).map(Number);
    try { await api.setFollowups(parsed); setMsg({ ok: true, text: 'Follow-up timing saved.' }); onChanged(); }
    catch (x) { setMsg({ ok: false, text: x.message }); }
  };
  return (
    <section className="panel">
      <h2>Follow-ups</h2>
      <p className="muted">
        If a school doesn’t reply, follow-ups are drafted after these numbers of days, on weekdays between
        {' '}{hours?.start ?? 9}:00 and {hours?.end ?? 17}:00 in the school’s time zone. Each one still needs approval. A reply, bounce or opt-out cancels the rest.
      </p>
      {isAdmin ? (
        <form className="inline-form" onSubmit={save}>
          <label>Days after the first email<input value={val} onChange={(e) => setVal(e.target.value)} inputMode="numeric" /></label>
          <button className="btn btn-quiet">Save</button>
        </form>
      ) : <p><b>{days.join(', ')}</b> days</p>}
      {msg && <p className={msg.ok ? 'form-ok' : 'form-error'}>{msg.text}</p>}
    </section>
  );
}

function DoNotContact({ rows, isAdmin, onChanged }) {
  const [scope, setScope] = useState('domain');
  const [value, setValue] = useState('');
  const [msg, setMsg] = useState(null);
  const add = async (e) => {
    e.preventDefault(); setMsg(null);
    const body = { scope, reason: 'manual', ...(scope === 'contact' ? { email: value } : { domain: value }) };
    try { await api.addSuppression(body); setValue(''); setMsg({ ok: true, text: 'Added. They won’t be emailed.' }); onChanged(); }
    catch (x) { setMsg({ ok: false, text: x.message }); }
  };
  return (
    <section className="panel">
      <h2>Do not contact</h2>
      <p className="muted">Anyone here is never emailed again. Entries can’t be removed, so the record stays complete.</p>
      {isAdmin && (
        <form className="inline-form" onSubmit={add}>
          <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="What to block">
            <option value="domain">Whole domain</option>
            <option value="contact">One email address</option>
          </select>
          <input value={value} onChange={(e) => setValue(e.target.value)} placeholder={scope === 'domain' ? 'example-aviation.com' : 'name@example-aviation.com'} aria-label="Domain or email" required />
          <button className="btn btn-quiet" disabled={!value.trim()}>Add</button>
        </form>
      )}
      {msg && <p className={msg.ok ? 'form-ok' : 'form-error'}>{msg.text}</p>}
      {rows.length === 0 ? <p className="muted">Nobody yet.</p> : (
        <table className="plain">
          <thead><tr><th>Who</th><th>Covers</th><th>Why</th><th>Added</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.domain || r.org_name || 'An email address (stored as a fingerprint)'}</td>
                <td>{SCOPE[r.scope]}</td><td>{REASON[r.reason] || r.reason}</td><td>{when(r.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
