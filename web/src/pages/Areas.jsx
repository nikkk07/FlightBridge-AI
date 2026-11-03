import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from '../api';
import { useAuth } from '../auth';
import { Loading, Problem, countryName, useLoad, when } from '../lib';

const COUNTRIES = ['US', 'CA', 'AU', 'NZ', 'GB', 'IN'];

export default function Areas() {
  const { isAdmin } = useAuth();
  const list = useLoad(() => api.areas(), []);
  const [msg, setMsg] = useState(null);
  const grouped = useMemo(() => {
    const g = {};
    (list.data || []).forEach((a) => { ((g[a.country] ||= {})[a.region] ||= []).push(a); });
    return g;
  }, [list.data]);

  const act = async (fn, ok) => {
    setMsg(null);
    try { const r = await fn(); setMsg({ ok: true, text: r?.message || ok }); list.reload(); }
    catch (e) { setMsg({ ok: false, text: e.message }); }
  };

  return (
    <div className="page">
      <header className="page-head">
        <h1>Areas</h1>
        <p className="muted">Each area is searched for schools, by country, then state, then area. <b>Flying-school hubs</b> are the airfields where schools cluster. <b>Student-demand hubs</b> are places like Ramphal Chowk, where students and aviation institutes are. Areas are searched again every 30 days to pick up new schools.</p>
      </header>
      {msg && <p className={msg.ok ? 'form-ok' : 'form-error'}>{msg.text}</p>}
      {isAdmin && <AddArea onAdded={(t) => { setMsg({ ok: true, text: t }); list.reload(); }} />}
      {list.loading && !list.data && <Loading />}
      <Problem error={list.error} onRetry={list.reload} />
      {Object.entries(grouped).map(([country, regions]) => (
        <section key={country} className="panel">
          <h2>{countryName(country)}</h2>
          <table className="plain areas-table">
            <thead><tr><th>Area</th><th>Kind</th><th className="num">Schools</th><th>Last searched</th><th /></tr></thead>
            <tbody>
              {Object.entries(regions).map(([region, areas]) => areas.map((a, i) => (
                <tr key={a.id} className={a.enabled ? '' : 'row-off'}>
                  <td>{i === 0 && <small className="muted">{region}</small>}<Link to={`/schools?country=${country}&area=${a.id}`}><b>{a.name}</b></Link> <span className="muted small">{a.radius_km} km</span></td>
                  <td>{a.kind === 'demand_hub' ? <span className="hub-tag">demand</span> : <span className="muted small">flying schools</span>}</td>
                  <td className="num">{a.schools}</td>
                  <td className="muted small">{a.last_searched_at ? when(a.last_searched_at, true) : 'not yet'}{a.last_result?.new != null ? ` – ${a.last_result.new} new` : ''}</td>
                  <td className="area-actions">
                    <button className="btn btn-small btn-quiet" onClick={() => act(() => api.runNow('search_area', null, a.id), 'Queued')}>Search now</button>
                    {isAdmin && <button className="btn btn-small btn-quiet" onClick={() => act(() => api.patchArea(a.id, { enabled: !a.enabled }), a.enabled ? 'Paused' : 'Resumed')}>{a.enabled ? 'Pause' : 'Resume'}</button>}
                  </td>
                </tr>
              )))}
            </tbody>
          </table>
        </section>
      ))}
    </div>
  );
}

function AddArea({ onAdded }) {
  const [v, setV] = useState({ country: 'US', region: '', name: '', kind: 'training_hub', radius_km: 40 });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const submit = async (e) => {
    e.preventDefault(); setBusy(true); setErr('');
    try {
      await api.addArea({ ...v, radius_km: Number(v.radius_km), region: v.region.trim(), name: v.name.trim() });
      onAdded(`Added ${v.name}. It will be searched on the next cycle.`);
      setV({ ...v, name: '' });
    } catch (x) { setErr(x.message); } finally { setBusy(false); }
  };
  return (
    <form className="panel add-area" onSubmit={submit}>
      <h2>Add an area</h2>
      <div className="add-area-grid">
        <label>Country<select value={v.country} onChange={(e) => setV({ ...v, country: e.target.value })}>{COUNTRIES.map((c) => <option key={c} value={c}>{countryName(c)}</option>)}</select></label>
        <label>State / province<input value={v.region} onChange={(e) => setV({ ...v, region: e.target.value })} placeholder="Florida" required /></label>
        <label>Area<input value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} placeholder="Lakeland" required /></label>
        <label>Kind<select value={v.kind} onChange={(e) => setV({ ...v, kind: e.target.value })}><option value="training_hub">Flying-school hub</option><option value="demand_hub">Student-demand hub</option></select></label>
        <label>Radius (km)<input type="number" min={2} max={50} value={v.radius_km} onChange={(e) => setV({ ...v, radius_km: e.target.value })} /></label>
      </div>
      <p className="muted small">Google Places finds the location from the name, so the Google Places key must work (Sending & safety, then Connections).</p>
      {err && <p className="form-error">{err}</p>}
      <div className="btns"><button className="btn" disabled={busy || !v.region.trim() || !v.name.trim()}>{busy ? 'Finding it…' : 'Add area'}</button></div>
    </form>
  );
}
