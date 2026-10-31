import { useCallback, useEffect, useState } from 'react';

const COUNTRY = {
  GB: 'United Kingdom', US: 'United States', CA: 'Canada', AU: 'Australia', NZ: 'New Zealand', IE: 'Ireland',
  DE: 'Germany', FR: 'France', ES: 'Spain', PT: 'Portugal', IT: 'Italy', NL: 'Netherlands', BE: 'Belgium',
  PL: 'Poland', CZ: 'Czech Republic', HU: 'Hungary', GR: 'Greece', CY: 'Cyprus', MT: 'Malta', AT: 'Austria',
  CH: 'Switzerland', SE: 'Sweden', DK: 'Denmark', NO: 'Norway', FI: 'Finland', LT: 'Lithuania', LV: 'Latvia',
  EE: 'Estonia', ZA: 'South Africa', AE: 'United Arab Emirates', IN: 'India', SG: 'Singapore', PH: 'Philippines',
  JE: 'Jersey', GG: 'Guernsey', IM: 'Isle of Man',
};
export const countryName = (c) => COUNTRY[c] || c || '—';

export const STATUS_LABEL = {
  discovered: 'Found in register', research_pending: 'Researching', research_completed: 'Researched',
  authority_verified: 'Approval confirmed', contact_verified: 'Contact confirmed', qualified: 'Good fit',
  rejected: 'Not a fit', needs_human_review: 'Needs a look', ready_for_outreach: 'Ready to contact',
  contacted: 'Contacted', interested: 'Interested', negotiation: 'In discussion', partner: 'Partner',
};

export function when(v, withTime = false) {
  if (!v) return '—';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return '—';
  const opts = withTime
    ? { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }
    : { day: 'numeric', month: 'short', year: 'numeric' };
  return d.toLocaleString('en-IN', opts);
}

export function useLoad(fn, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const run = useCallback(() => {
    setState((s) => ({ ...s, loading: true, error: null }));
    return fn().then(
      (data) => setState({ data, error: null, loading: false }),
      (error) => setState({ data: null, error, loading: false }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { run(); }, [run]);
  return { ...state, reload: run };
}

export function Risk({ value }) {
  if (!value) return <span className="muted">Not classified</span>;
  const label = { GREEN: 'Routine', AMBER: 'Check', RED: 'Needs you' }[value] || value;
  return <span className={`risk risk-${value.toLowerCase()}`}><i aria-hidden="true" />{label}</span>;
}

export function Status({ value }) {
  return <span className={`status status-${value}`}>{STATUS_LABEL[value] || value}</span>;
}

export function RegRef({ authority, value }) {
  if (!value) return <span className="muted">No reference</span>;
  return <span className="regref" title={`${authority} approval reference`}><b>{String(authority || '').replace(/_/g, ' ')}</b>{value}</span>;
}

export function Problem({ error, onRetry }) {
  if (!error) return null;
  if (error.kind === 'not_installed') {
    return (
      <div className="notice">
        <h3>The FlightBridge tables aren’t in the database yet</h3>
        <p>Open the Supabase SQL editor and run migrations <b>037</b> and <b>038</b> from the CRM repository, then reload this page.</p>
      </div>
    );
  }
  return (
    <div className="notice notice-error" role="alert">
      <p>{error.message}</p>
      {onRetry && <button className="btn btn-quiet" onClick={onRetry}>Try again</button>}
    </div>
  );
}

export function Empty({ title, children }) {
  return (
    <div className="empty">
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}

export function Loading() {
  return <div className="loading" aria-live="polite">Loading…</div>;
}
