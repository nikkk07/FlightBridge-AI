import { useState } from 'react';
import { useAuth } from '../auth';

export default function Login() {
  const { signIn } = useAuth();
  const [loginId, setLoginId] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr('');
    try {
      await signIn(loginId.trim(), password);
    } catch (x) {
      setErr(x.status === 401 ? 'That login ID and password don’t match. Use the same details as the CRM.' : x.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <section className="login-side">
        <div className="brand brand-lg">
          <svg viewBox="0 0 32 32" width="34" height="34" aria-hidden="true"><rect width="32" height="32" rx="7" fill="currentColor" /><path d="M6 19.5 26 11l-4.5 11.5-4.6-4.1-3.4 3.6-.4-5.2z" fill="#fff" /></svg>
          <div><b>FlightBridge</b><small>We One Aviation</small></div>
        </div>
        <div className="login-copy">
          <h1>Partnerships with approved flying schools abroad.</h1>
          <p>Every school here comes from a regulator’s own register. Nothing is emailed until a person on the team approves it.</p>
        </div>
        <svg className="chartline" viewBox="0 0 400 160" aria-hidden="true">
          <path d="M10 130 C 90 120, 120 40, 200 50 S 330 110, 390 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="5 6" />
          <circle cx="10" cy="130" r="5" fill="currentColor" />
          <circle cx="390" cy="20" r="5" fill="none" stroke="currentColor" strokeWidth="2" />
          <text x="20" y="150">DEL</text>
        </svg>
      </section>
      <section className="login-form">
        <form onSubmit={submit} noValidate>
          <h2>Sign in</h2>
          <p className="muted">Use your CRM login ID and password.</p>
          <label>
            Login ID
            <input value={loginId} onChange={(e) => setLoginId(e.target.value)} autoComplete="username" autoFocus required />
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
          </label>
          {err && <p className="form-error" role="alert">{err}</p>}
          <button className="btn btn-wide" disabled={busy || !loginId || !password}>{busy ? 'Signing in…' : 'Sign in'}</button>
        </form>
      </section>
    </div>
  );
}
