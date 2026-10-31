import { useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { useAuth } from './auth';
import { DEMO } from './api';
import { SendingBox, Sidebar, useNavData } from './nav';
import Automation from './pages/Automation';
import Login from './pages/Login';
import Overview from './pages/Overview';
import Schools from './pages/Schools';
import Approvals from './pages/Approvals';
import Inbox from './pages/Inbox';
import Templates from './pages/Templates';
import Sent from './pages/Sent';
import Settings from './pages/Settings';
import Team from './pages/Team';

function Shell() {
  const { me, signOut, isAdmin } = useAuth();
  const [open, setOpen] = useState(false);
  const navData = useNavData();
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  return (
    <div className="shell">
      <aside className={`rail ${open ? 'rail-open' : ''}`}>
        <div className="rail-top">
          <div className="brand">
            <svg viewBox="0 0 32 32" width="26" height="26" aria-hidden="true"><rect width="32" height="32" rx="7" fill="currentColor" /><path d="M6 19.5 26 11l-4.5 11.5-4.6-4.1-3.4 3.6-.4-5.2z" fill="#fff" /></svg>
            <div><b>FlightBridge</b><small>We One Aviation</small></div>
          </div>
          <button className="menu-btn" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label="Menu">
            <span /><span />
          </button>
        </div>
        <div className="rail-scroll">
          <Sidebar data={navData} />
        </div>
        <div className="rail-foot">
          <SendingBox summary={navData.summary} />
          <div className="who">
            <div>
              <b>{me?.name}</b>
              <small>{isAdmin ? 'Admin' : 'Research assistant'}</small>
            </div>
            <button className="link" onClick={signOut}>Sign out</button>
          </div>
        </div>
      </aside>
      <main className="main">
        {DEMO && <p className="demo-strip">Preview with sample data. School names and approval numbers are the real UK CAA register; contacts, emails and replies are invented. Nothing here is saved or sent.</p>}
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/automation" element={<Automation />} />
          <Route path="/schools" element={<Schools />} />
          <Route path="/schools/:id" element={<Schools />} />
          <Route path="/approvals" element={<Approvals />} />
          <Route path="/approvals/:id" element={<Approvals />} />
          <Route path="/inbox" element={<Inbox />} />
          <Route path="/templates" element={<Templates />} />
          <Route path="/sent" element={<Sent />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/team" element={isAdmin ? <Team /> : <Navigate to="/" />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}

function Gate({ title, children }) {
  const { signOut } = useAuth();
  return (
    <div className="gate">
      <div className="gate-box">
        <h1>{title}</h1>
        {children}
        <button className="btn btn-quiet" onClick={signOut}>Sign in with another account</button>
      </div>
    </div>
  );
}

export default function App() {
  const { state, error, retry } = useAuth();
  if (state === 'checking') return <div className="gate"><p className="muted">Opening FlightBridge…</p></div>;
  if (state === 'signed_out') return <Login />;
  if (state === 'no_access') {
    return (
      <Gate title="This account doesn’t have FlightBridge access">
        <p>Ask an admin to add you on the Team page. CRM admins have access automatically.</p>
      </Gate>
    );
  }
  if (state === 'not_deployed') {
    return (
      <Gate title="The CRM server hasn’t been updated yet">
        <p>You signed in, but the server doesn’t have the FlightBridge module. Merge the <b>flightbridge-phase2</b> branch of the CRM repository and redeploy on Render.</p>
      </Gate>
    );
  }
  if (state === 'error') {
    return (
      <Gate title="FlightBridge couldn’t load">
        <p>{error?.message}</p>
        <button className="btn" onClick={retry}>Try again</button>
      </Gate>
    );
  }
  return <Shell />;
}
