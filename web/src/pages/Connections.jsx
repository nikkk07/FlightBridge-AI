import { useState } from 'react';
import { api } from '../api';
import { Loading, Problem, useLoad, when } from '../lib';

const HELP = {
  google_places_api_key: ['Finds each school’s official website.', 'Google Cloud → APIs & Services → Credentials. Enable “Places API (New)” in the same project.'],
  groq_api_key: ['Short school summaries and a second check on replies.', 'console.groq.com → API Keys (free tier).'],
  gemini_api_key: ['Same as Groq – you need only one.', 'aistudio.google.com → Get API key (free tier).'],
  hostinger_mail_api_token: ['Reads replies from schools.', 'hPanel → Emails → your mailbox → API access.'],
  hostinger_mailbox_resource_id: ['Which mailbox to read.', 'Shown next to the API token in hPanel.'],
};

function Key({ k, onChanged }) {
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const act = async (fn) => {
    setBusy(true); setMsg(null);
    try { const r = await fn(); if (r?.message) setMsg({ ok: r.test_ok, text: r.message }); setValue(''); onChanged(); }
    catch (e) { setMsg({ ok: false, text: e.message }); } finally { setBusy(false); }
  };
  const state = k.from_server_env ? ['Set on the server', 'set']
    : !k.saved ? ['Not set', 'none']
    : k.last_test_ok === true ? [`Working – …${k.last4}`, 'ok']
    : k.last_test_ok === false ? [`Problem – …${k.last4}`, 'bad'] : [`Saved – …${k.last4}`, 'set'];
  const [used, where] = HELP[k.name] || [];
  return (
    <div className="conn">
      <div className="conn-head">
        <div><b>{k.label}</b><small className="muted">{used}</small></div>
        <span className={`conn-state conn-${state[1]}`}>{state[0]}</span>
      </div>
      {k.last_test_ok === false && k.last_test_message && !msg && <p className="form-error small">{k.last_test_message}</p>}
      <div className="conn-row">
        <input type="password" autoComplete="off" spellCheck={false} value={value} onChange={(e) => setValue(e.target.value)}
          placeholder={k.saved ? 'Paste a new key to replace it' : 'Paste the key here'} aria-label={k.label} />
        <button className="btn" disabled={busy || !value.trim()} onClick={() => act(() => api.saveKey(k.name, value))}>{busy ? 'Checking…' : 'Save & test'}</button>
        {k.saved && <button className="btn btn-quiet" disabled={busy} onClick={() => act(() => api.testKey(k.name))}>Test</button>}
        {k.saved && <button className="btn btn-quiet btn-danger-text" disabled={busy} onClick={() => act(() => api.removeKey(k.name))}>Remove</button>}
      </div>
      {msg && <p className={msg.ok ? 'form-ok small' : 'form-error small'}>{msg.text}</p>}
      <small className="muted">Where to get it: {where}{k.updated_at ? ` – saved ${when(k.updated_at, true)}` : ''}</small>
    </div>
  );
}

export default function Connections() {
  const d = useLoad(() => api.integrations(), []);
  const [msg, setMsg] = useState(null);
  const save = async (patch) => {
    setMsg(null);
    try { await api.saveIntegrationSettings({ ...d.data.settings, ...patch }); d.reload(); }
    catch (e) { setMsg(e.message); }
  };
  if (d.loading && !d.data) return <Loading />;
  if (d.error) return <Problem error={d.error} onRetry={d.reload} />;
  const keys = Object.fromEntries(d.data.keys.map((k) => [k.name, k]));
  const s = d.data.settings;
  return (
    <section className="panel">
      <h2>Connections</h2>
      <p className="muted">Paste API keys here. They’re checked straight away, encrypted before they’re stored, and never shown again – only the last 4 characters.</p>
      {msg && <p className="form-error">{msg}</p>}
      <label className="check-row conn-toggle"><input type="checkbox" checked={!!s.engine_in_crm} onChange={(e) => save({ engine_in_crm: e.target.checked })} /> Run automation on the CRM server (every 15 minutes)</label>
      <h3>Website search</h3>
      <Key k={keys.google_places_api_key} onChanged={d.reload} />
      <h3>Free AI (optional)</h3>
      <label className="conn-inline">Use
        <select value={s.llm_provider} onChange={(e) => save({ llm_provider: e.target.value })}>
          <option value="none">No AI (keywords only)</option><option value="groq">Groq</option><option value="gemini">Gemini</option>
        </select>
      </label>
      <Key k={keys.groq_api_key} onChanged={d.reload} />
      <Key k={keys.gemini_api_key} onChanged={d.reload} />
      <h3>Mailbox (replies)</h3>
      <label className="check-row conn-toggle"><input type="checkbox" checked={s.inbound_source === 'hostinger'} onChange={(e) => save({ inbound_source: e.target.checked ? 'hostinger' : 'none' })} /> Read replies from this mailbox</label>
      <Key k={keys.hostinger_mail_api_token} onChanged={d.reload} />
      <Key k={keys.hostinger_mailbox_resource_id} onChanged={d.reload} />
    </section>
  );
}
