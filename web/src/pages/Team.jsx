import { useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { Loading, Problem, useLoad } from '../lib';

export default function Team() {
  const { me } = useAuth();
  const t = useLoad(() => api.team(), []);
  const [q, setQ] = useState('');
  const [msg, setMsg] = useState(null);

  const set = async (emp, role) => {
    setMsg(null);
    try { await api.setRole(emp.id, role || null); setMsg({ ok: true, text: `${emp.name}: ${role ? (role === 'admin' ? 'admin' : 'research assistant') : 'access removed'}.` }); t.reload(); }
    catch (e) { setMsg({ ok: false, text: e.message }); }
  };

  const rows = (t.data || []).filter((e) => !q || e.name.toLowerCase().includes(q.toLowerCase()));
  const members = rows.filter((e) => e.effective_role);
  const others = rows.filter((e) => !e.effective_role);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Team</h1>
        <p className="muted">People sign in with their CRM account. Research assistants can check schools and record evidence; only admins approve emails, templates and sending.</p>
      </header>
      {msg && <p className={msg.ok ? 'form-ok' : 'form-error'}>{msg.text}</p>}
      {t.loading && <Loading />}
      <Problem error={t.error} onRetry={t.reload} />
      {t.data && (
        <>
          <div className="filters"><input type="search" placeholder="Find a person" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Find a person" /></div>
          <section className="panel">
            <h2>Has access</h2>
            <table className="plain">
              <tbody>
                {members.map((e) => (
                  <tr key={e.id}>
                    <td><b>{e.name}</b><small className="muted">{e.department}</small></td>
                    <td>
                      {e.department === 'Admin' ? <span className="muted">Admin (CRM admin)</span> : (
                        <select value={e.fb_role} onChange={(x) => set(e, x.target.value)} disabled={e.id === me?.id} aria-label={`Role for ${e.name}`}>
                          <option value="researcher">Research assistant</option>
                          <option value="admin">Admin</option>
                          <option value="">No access</option>
                        </select>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section className="panel">
            <h2>Everyone else in the CRM</h2>
            {others.length === 0 ? <p className="muted">Nobody else.</p> : (
              <table className="plain">
                <tbody>
                  {others.map((e) => (
                    <tr key={e.id}>
                      <td><b>{e.name}</b><small className="muted">{e.department}</small></td>
                      <td><button className="btn btn-small btn-quiet" onClick={() => set(e, 'researcher')}>Add as research assistant</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </div>
  );
}
