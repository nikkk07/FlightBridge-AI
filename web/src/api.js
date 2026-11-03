const API_URL = (import.meta.env.VITE_API_URL || 'https://crm-p8j8.onrender.com').replace(/\/$/, '');
const TOKEN_KEY = 'fb_token';

export class ApiError extends Error {
  constructor(message, status, kind) {
    super(message);
    this.status = status;
    this.kind = kind; // 'network' | 'auth' | 'forbidden' | 'not_installed' | 'not_deployed' | 'http'
  }
}

export const getToken = () => { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } };
export const setToken = (t) => { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ } };

async function request(path, { method = 'GET', body, auth = true } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  const token = getToken();
  if (auth && token) headers.Authorization = `Bearer ${token}`;
  let res;
  try {
    res = await fetch(`${API_URL}${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError('The CRM server could not be reached. It may be waking up — try again in a minute.', 0, 'network');
  }
  let data = null;
  const text = await res.text();
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (res.ok) return data;
  const detail = (data && data.detail) || res.statusText;
  const msg = typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d) => d.msg).join('; ') : 'Request failed';
  if (res.status === 401) { setToken(null); throw new ApiError('Your session has ended. Sign in again.', 401, 'auth'); }
  if (res.status === 403) throw new ApiError(msg, 403, 'forbidden');
  if (res.status === 503 && /schema/i.test(msg)) throw new ApiError(msg, 503, 'not_installed');
  if (res.status === 404 && path.startsWith('/api/flightbridge/me')) throw new ApiError('not deployed', 404, 'not_deployed');
  throw new ApiError(msg, res.status, 'http');
}

import { demoApi } from './demo';

const realApi = {
  login: (login_id, password) => request('/api/auth/login', { method: 'POST', body: { login_id, password }, auth: false }),
  me: () => request('/api/flightbridge/me'),
  summary: () => request('/api/flightbridge/summary'),
  metrics: () => request('/api/flightbridge/metrics'),
  schools: (params = {}) => request('/api/flightbridge/schools?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v))),
  school: (id) => request(`/api/flightbridge/schools/${id}`),
  reviewSchool: (id, decision, reason) => request(`/api/flightbridge/schools/${id}/review`, { method: 'POST', body: { decision, reason } }),
  verifyRegulatory: (id, approvalId) => request(`/api/flightbridge/schools/${id}/regulatory/${approvalId}/verify`, { method: 'POST' }),
  addEvidence: (body) => request('/api/flightbridge/evidence', { method: 'POST', body }),
  approvals: (status = 'open') => request(`/api/flightbridge/approvals?status=${status}`),
  approval: (id) => request(`/api/flightbridge/approvals/${id}`),
  decide: (id, body) => request(`/api/flightbridge/approvals/${id}/decision`, { method: 'POST', body }),
  inbox: (params = {}) => request('/api/flightbridge/inbox?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v))),
  outbound: () => request('/api/flightbridge/outbound'),
  recheck: (id) => request(`/api/flightbridge/outbound/${id}/recheck`, { method: 'POST' }),
  templates: () => request('/api/flightbridge/templates'),
  saveTemplate: (body) => request('/api/flightbridge/templates', { method: 'POST', body }),
  approveTemplate: (id) => request(`/api/flightbridge/templates/${id}/approve`, { method: 'POST' }),
  settings: () => request('/api/flightbridge/settings'),
  setFollowups: (days) => request('/api/flightbridge/settings/followups', { method: 'POST', body: { days } }),
  setKillSwitch: (engaged) => request('/api/flightbridge/kill-switch', { method: 'POST', body: { engaged } }),
  suppressions: () => request('/api/flightbridge/suppressions'),
  addSuppression: (body) => request('/api/flightbridge/suppressions', { method: 'POST', body }),
  regions: () => request('/api/flightbridge/regions'),
  automation: () => request('/api/flightbridge/automation'),
  runNow: (kind, org_id, area_id) => request('/api/flightbridge/automation/run', { method: 'POST', body: { kind, org_id, area_id } }),
  areas: () => request('/api/flightbridge/areas'),
  addArea: (body) => request('/api/flightbridge/areas', { method: 'POST', body }),
  patchArea: (id, body) => request(`/api/flightbridge/areas/${id}`, { method: 'PATCH', body }),
  eligibilityDecision: (id, body) => request(`/api/flightbridge/schools/${id}/eligibility`, { method: 'POST', body }),
  setAutopilot: (body) => request('/api/flightbridge/automation/settings', { method: 'POST', body }),
  integrations: () => request('/api/flightbridge/integrations'),
  saveKey: (name, value) => request(`/api/flightbridge/integrations/${name}`, { method: 'POST', body: { value } }),
  testKey: (name) => request(`/api/flightbridge/integrations/${name}/test`, { method: 'POST' }),
  removeKey: (name) => request(`/api/flightbridge/integrations/${name}`, { method: 'DELETE' }),
  saveIntegrationSettings: (body) => request('/api/flightbridge/integrations-settings', { method: 'POST', body }),
  team: () => request('/api/flightbridge/team'),
  setRole: (id, role) => request(`/api/flightbridge/team/${id}/role`, { method: 'POST', body: { role } }),
};

export const DEMO = import.meta.env.VITE_DEMO === '1';
export const api = DEMO ? demoApi : realApi;
