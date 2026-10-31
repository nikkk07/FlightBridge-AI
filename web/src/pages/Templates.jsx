import { useMemo, useState } from 'react';
import { api } from '../api';
import { useAuth } from '../auth';
import { Loading, Problem, useLoad, when } from '../lib';

const KINDS = [['initial', 'First email'], ['followup_1', 'Follow-up 1'], ['followup_2', 'Follow-up 2'], ['followup_3', 'Follow-up 3']];
const VARS = ['school_name', 'city', 'country'];

export default function Templates() {
  const { isAdmin } = useAuth();
  const list = useLoad(() => api.templates(), []);
  const [kind, setKind] = useState('initial');
  const byKind = useMemo(() => {
    const m = {};
    (list.data || []).forEach((t) => { (m[t.kind] ||= []).push(t); });
    return m;
  }, [list.data]);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Email templates</h1>
        <p className="muted">Write the emails in your own words. FlightBridge only fills in the school’s details and adds the legal footer – it never writes outreach text itself.</p>
      </header>
      <div className="tabs" role="tablist">
        {KINDS.map(([k, v]) => (
          <button key={k} role="tab" aria-selected={kind === k} className={kind === k ? 'on' : ''} onClick={() => setKind(k)}>
            {v}{byKind[k]?.some((t) => t.status === 'approved') ? ' ✓' : ''}
          </button>
        ))}
      </div>
      {list.loading && <Loading />}
      <Problem error={list.error} onRetry={list.reload} />
      {list.data && (
        <div className="split-2 split-wide-left">
          {isAdmin ? <Editor kind={kind} current={byKind[kind]?.[0]} onSaved={list.reload} /> : (
            <section className="panel"><p className="muted">Only an admin can write or approve templates.</p></section>
          )}
          <section className="panel">
            <h2>Versions</h2>
            {!(byKind[kind]?.length) && <p className="muted">No versions yet.</p>}
            <ul className="versions">
              {(byKind[kind] || []).map((t) => <Version key={t.id} t={t} isAdmin={isAdmin} onChanged={list.reload} />)}
            </ul>
          </section>
        </div>
      )}
    </div>
  );
}

function Editor({ kind, current, onSaved }) {
  const [subject, setSubject] = useState(current?.subject || '');
  const [body, setBody] = useState(current?.body || '');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [lastKind, setLastKind] = useState(kind);
  if (kind !== lastKind) { setLastKind(kind); setSubject(current?.subject || ''); setBody(current?.body || ''); setResult(null); }

  const unknown = [...(subject + body).matchAll(/\{\{\s*([a-zA-Z_]+)\s*\}\}/g)].map((m) => m[1]).filter((v) => !VARS.includes(v));

  const save = async (e) => {
    e.preventDefault(); setBusy(true); setResult(null);
    try {
      const r = await api.saveTemplate({ kind, subject, body });
      setResult({ ok: r.validation.ok, version: r.version, errors: r.validation.errors || [], warnings: r.validation.warnings || [] });
      onSaved();
    } catch (x) { setResult({ ok: false, errors: [x.message] }); } finally { setBusy(false); }
  };

  return (
    <form className="panel stack" onSubmit={save}>
      <h2>Write a new version</h2>
      <label>Subject<input value={subject} onChange={(e) => setSubject(e.target.value)} maxLength={150} required /></label>
      <label>Message<textarea rows={16} value={body} onChange={(e) => setBody(e.target.value)} required /></label>
      <p className="muted small">
        You can use {VARS.map((v, i) => <span key={v}>{i ? ', ' : ''}<code>{`{{${v}}}`}</code></span>)}.
        Don’t add your address or an unsubscribe line – they’re added for you. Avoid claims like placement guarantees or student numbers.
      </p>
      {unknown.length > 0 && <p className="form-error">Unknown placeholder: {unknown.map((u) => `{{${u}}}`).join(', ')}</p>}
      <div className="btns"><button className="btn" disabled={busy || !subject.trim() || body.trim().length < 20}>Save as draft</button></div>
      {result && (
        <div className={result.ok ? 'form-ok' : 'form-error'} role="status">
          {result.ok ? `Saved as version ${result.version}. Approve it on the right when you’re happy.` : 'Saved, but it can’t be approved yet:'}
          {result.errors.length > 0 && <ul>{result.errors.map((e) => <li key={e}>{e}</li>)}</ul>}
          {result.warnings.length > 0 && <ul className="muted">{result.warnings.map((e) => <li key={e}>{e}</li>)}</ul>}
        </div>
      )}
    </form>
  );
}

function Version({ t, isAdmin, onChanged }) {
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const errors = t.validation?.errors || [];
  const approve = async () => {
    setBusy(true); setErr('');
    try { await api.approveTemplate(t.id); onChanged(); } catch (x) { setErr(x.message); } finally { setBusy(false); }
  };
  return (
    <li className={`version version-${t.status}`}>
      <div className="version-head">
        <b>Version {t.version}</b>
        <span className={`status status-tpl-${t.status}`}>{{ draft: 'Draft', approved: 'In use', archived: 'Replaced' }[t.status]}</span>
      </div>
      <p className="letter-subject">{t.subject}</p>
      <details><summary>Show message</summary><pre>{t.body}</pre></details>
      <small className="muted">Saved {when(t.created_at, true)}{t.approved_at ? `, approved ${when(t.approved_at, true)}` : ''}</small>
      {errors.length > 0 && t.status === 'draft' && <ul className="form-error small">{errors.map((e) => <li key={e}>{e}</li>)}</ul>}
      {isAdmin && t.status === 'draft' && errors.length === 0 && (
        <div className="btns"><button className="btn btn-small" disabled={busy} onClick={approve}>Use this version</button></div>
      )}
      {err && <p className="form-error small">{err}</p>}
    </li>
  );
}
