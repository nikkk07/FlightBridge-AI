// Preview-only data. Schools are the real UK CAA SD31 register entries; every contact, email and reply is invented (example.test).
import schools from './demo-schools.json';

const now = Date.now();
const day = 864e5;
const iso = (d) => new Date(now - d * day).toISOString();
const SRC = 'https://www.caa.co.uk/publication/download/13488';

const status = (i) => (i === 3 ? 'qualified' : i === 7 ? 'contacted' : i === 10 || i === 11 ? 'needs_human_review' : i === 14 ? 'rejected' : 'research_pending');
const orgs = schools.map((s, i) => ({
  id: `org-${i}`, name: s.name, country: s.country, region: s.region || null, city: s.city, status: status(i),
  website_domain: i === 3 || i === 7 ? `school-${i}.example.test` : null,
  rejection_reason: i === 14 ? 'Helicopter training only' : null, updated_at: iso(1),
  authority_code: 'UK_CAA', classification: 'ATO_UK', approval_ref: s.ref, approval_status: i === 3 ? 'verified_by_human' : 'listed_in_register',
  checked_at: iso(0), score: i === 3 ? 82 : null, recommended_action: null, contacts: i === 3 || i === 7 ? 1 : 0,
}));

const approvals = [{
  id: 'apr-1', status: 'open', risk: 'AMBER', escalation_reason: 'First contact with this school', created_at: iso(0.1),
  kind: 'initial', subject: `Training partnership enquiry for ${orgs[3].name}`, org_name: orgs[3].name, country: 'GB', email: 'info@school-3.example.test',
}];
const inbox = [
  { id: 'm1', from_address: 'admissions@school-7.example.test', subject: 'Re: Training partnership enquiry', preview: 'Thank you for getting in touch. Could you tell us how many students you expect to refer each year, and whether they hold DGCA medicals?', occurred_at: iso(0.2), match_method: 'in_reply_to', intent: 'question', risk: 'AMBER', confidence: 0.86, org_name: orgs[7].name, country: 'GB' },
  { id: 'm2', from_address: 'noreply@school-7.example.test', subject: 'Automatic reply: Training partnership enquiry', preview: 'Our office is closed until Monday.', occurred_at: iso(1), match_method: 'in_reply_to', is_auto_reply: true, intent: 'auto_reply', risk: 'GREEN', confidence: 0.99, org_name: orgs[7].name, country: 'GB' },
];
const templates = [
  { id: 't2', kind: 'initial', version: 2, status: 'approved', subject: 'Training partnership enquiry for {{school_name}}', body: 'Dear {{school_name}} team,\n\n(Your own wording goes here — FlightBridge never writes it.)\n\nKind regards,\nWe One Aviation', validation: { ok: true, errors: [] }, created_at: iso(2), approved_at: iso(2) },
  { id: 't1', kind: 'initial', version: 1, status: 'archived', subject: 'Hello', body: 'Draft wording', validation: { ok: true, errors: [] }, created_at: iso(3) },
];
const team = [
  { id: 'e1', name: 'Nikhil Bisht', department: 'Admin', fb_role: '', effective_role: 'admin' },
  { id: 'e2', name: 'Research Assistant (sample)', department: 'Sales', fb_role: 'researcher', effective_role: 'researcher' },
  { id: 'e3', name: 'Counsellor (sample)', department: 'Sales', fb_role: '', effective_role: null },
];
let killSwitch = true;
let autopilotCfg = { find_websites: true, research: true, qualify: true, draft: true, batch_size: 8 };
const jobs = [];
const runs = [
  { id: 3, started_at: iso(0.004), finished_at: iso(0.0035), runner: 'github-actions', trigger: 'schedule', status: 'partial',
    stats: { research: { find_websites_error: 'Places API 403: Places API (New) has not been used in project 589531166732 before or it is disabled.', research: { researched: 2, emails_found: 2 }, qualify: { qualified: 1, needs_review: 1 }, draft: { drafts_created: 1 } } } },
  { id: 2, started_at: iso(0.014), finished_at: iso(0.0138), runner: 'github-actions', trigger: 'schedule', status: 'ok', stats: { research: {} } },
  { id: 1, started_at: iso(0.03), finished_at: iso(0.029), runner: 'mac', trigger: 'queue', status: 'ok', stats: { jobs: [{}], research: { research: { researched: 1, emails_found: 1 } } } },
];
let followups = [4, 9, 16];

function school(id) {
  const o = orgs.find((x) => x.id === id);
  const i = orgs.indexOf(o);
  return {
    ...o,
    regulatory: [{ id: i, authority_code: 'UK_CAA', classification: 'ATO_UK', approval_ref: o.approval_ref, status: o.approval_status, source_url: SRC,
      checked_at: iso(0), next_check_due: iso(-30), courses: ['Commercial Pilot Licence (Aeroplanes) Modular', 'Instrument Rating (Aeroplanes)', 'Private Pilot Licence (Aeroplanes)'] }],
    contacts: o.contacts ? [{ id: `c-${i}`, email: `info@school-${i}.example.test`, is_role_address: true, role: null, source_url: `https://school-${i}.example.test/contact`,
      evidence: [{ fact: 'address_conspicuously_published', method: 'human_verified', expires_at: iso(-80) }] }] : [],
    qualification: o.score ? { score: o.score, confidence: 'medium', missing: [], risks: [] } : null,
    profile: o.website_domain ? { summary: 'Sample summary: offers modular CPL and PPL training at its airfield and says it accepts international students; fleet published.',
      signals: { has_cpl: true, intl_students: true, fleet_published: true, accommodation: false, india_mention: false },
      evidence: [{ signal: 'has_cpl', url: `https://${o.website_domain}/courses`, excerpt: 'Modular CPL and PPL courses run all year from our airfield.' },
        { signal: 'intl_students', url: `https://${o.website_domain}/international`, excerpt: 'We welcome international students and can provide visa support letters.' },
        { signal: 'fleet_published', url: `https://${o.website_domain}/fleet`, excerpt: 'Our fleet: four Piper PA-28 Warriors and a Diamond DA42.' }],
      generator: 'groq:llama-3.1-8b-instant', updated_at: iso(0.05) } : null,
    jobs: [],
    history: [{ at: iso(0), action: 'organisation created', payload: {} }],
  };
}

const wait = (v) => new Promise((r) => setTimeout(() => r(structuredClone(v)), 150));
const ok = (v = { ok: true }) => wait(v);

export const demoApi = {
  login: () => ok({ access_token: 'demo' }),
  me: () => ok({ id: 'e1', name: 'Nikhil Bisht', department: 'Admin', role: 'admin' }),
  summary: () => {
    const c = {}; orgs.forEach((o) => { c[o.status] = (c[o.status] || 0) + 1; });
    return ok({ organisations: c, approvals: { open: 1 }, outbound: { sent: 1 }, kill_switches: { outbound_kill_switch: killSwitch }, suppressions: 0 });
  },
  metrics: () => ok({ schools_contacted: 1, schools_replied: 1, response_rate: 1, schools_by_country: orgs.reduce((a, o) => ({ ...a, [o.country]: (a[o.country] || 0) + 1 }), {}) }),
  schools: (p = {}) => ok(orgs.filter((o) => (!p.country || o.country === p.country) && (!p.status || o.status === p.status)
    && (!p.region || (p.region === '__none__' ? !o.region : o.region === p.region))
    && (!p.q || (o.name + o.approval_ref).toLowerCase().includes(p.q.toLowerCase())))),
  school: (id) => wait({ ...school(id), jobs: jobs.filter((j) => j.org_id === id) }),
  reviewSchool: (id, decision, reason) => { const o = orgs.find((x) => x.id === id); o.status = decision; o.rejection_reason = reason || null; return ok(); },
  verifyRegulatory: (id) => { orgs.find((x) => x.id === id).approval_status = 'verified_by_human'; return ok(); },
  addEvidence: () => ok(),
  approvals: (s = 'open') => ok(approvals.filter((a) => a.status === s)),
  approval: (id) => {
    const a = approvals.find((x) => x.id === id); const s = school('org-3');
    return wait({ ...a, draft_subject: a.subject, draft_body: templates[0].body.replaceAll('{{school_name}}', s.name), organisation: s, regulatory: s.regulatory,
      qualification: s.qualification, contact: s.contacts[0], contact_evidence: s.contacts[0].evidence, thread: [] });
  },
  decide: (id, b) => { const a = approvals.find((x) => x.id === id); a.status = { approve: 'approved', edit_approve: 'edited_approved', reject: 'rejected', more_research: 'more_research' }[b.action] || a.status; a.decided_at = new Date().toISOString(); return ok({ message: 'Preview only — nothing is queued or sent.' }); },
  inbox: (p = {}) => ok(inbox.filter((m) => (!p.risk || m.risk === p.risk) && (!p.unmatched || m.match_method === 'unmatched'))),
  outbound: () => ok([{ id: 'o1', status: 'dry_run_rendered', kind: 'initial', subject: 'Training partnership enquiry', created_at: iso(3), ref_token: 'Ref: FB-7Q2KD9XM4TPA',
    email: 'admissions@school-7.example.test', org_name: orgs[7].name, country: 'GB', jurisdiction: 'ALLOW', jurisdiction_reasons: [], content: 'PASS', content_reasons: [] }]),
  recheck: () => ok(),
  templates: () => ok(templates),
  saveTemplate: (b) => { const v = { id: `t${templates.length + 1}`, ...b, version: templates.filter((t) => t.kind === b.kind).length + 1, status: 'draft', validation: { ok: true, errors: [] }, created_at: new Date().toISOString() }; templates.unshift(v); return ok({ version: v.version, validation: { ok: true, errors: [], warnings: [] } }); },
  approveTemplate: (id) => { const t = templates.find((x) => x.id === id); templates.forEach((x) => { if (x.kind === t.kind && x.status === 'approved') x.status = 'archived'; }); t.status = 'approved'; t.approved_at = new Date().toISOString(); return ok(); },
  settings: () => ok({ outbound_kill_switch: killSwitch, followup_schedule_days: followups, followup_business_hours: { start: 9, end: 17 } }),
  setFollowups: (d) => { followups = d; return ok(); },
  setKillSwitch: (v) => { killSwitch = v; return ok({ note: 'Preview only. In the real app, server dry-run settings still block sending.' }); },
  suppressions: () => ok([]),
  addSuppression: () => ok(),
  regions: () => {
    const m = {};
    orgs.forEach((o) => {
      const c = (m[o.country] ||= { country: o.country, total: 0, needs_look: 0, regions: {} });
      c.total += 1; if (o.status === 'needs_human_review') c.needs_look += 1;
      const r = (c.regions[o.region] ||= { region: o.region, total: 0, needs_look: 0, good_fit: 0, in_contact: 0 });
      r.total += 1;
    });
    return ok(Object.values(m).map((c) => ({ ...c, regions: Object.values(c.regions) })).sort((a, b) => b.total - a.total));
  },
  automation: () => ok({
    runs, jobs, runners: [{ runner: 'github-actions', last_seen: iso(0.004) }, { runner: 'mac', last_seen: iso(0.03) }], autopilot: autopilotCfg,
    backlog: { need_website: orgs.filter((o) => !o.website_domain && o.status === 'research_pending').length, need_research: 0, need_scoring: 0 },
    activity: [
      { at: iso(0.0036), actor_type: 'system', action: 'draft_created', entity: 'ai_draft', payload: {} },
      { at: iso(0.0037), actor_type: 'system', action: 'qualified', entity: 'organisation', entity_id: 'org-3', org_name: orgs[3].name, payload: { score: 82, recommended_action: 'draft_outreach_for_approval' } },
      { at: iso(0.0038), actor_type: 'system', action: 'research_stored', entity: 'organisation', entity_id: 'org-3', org_name: orgs[3].name, payload: { pages: 6, contacts: 1 } },
      { at: iso(0.0039), actor_type: 'system', action: 'research_stored', entity: 'organisation', entity_id: 'org-7', org_name: orgs[7].name, payload: { pages: 4, contacts: 1 } },
      ...orgs.map((o) => ({ at: iso(0.9), actor_type: 'system', action: 'organisation_created', entity: 'organisation', entity_id: o.id, org_name: o.name, payload: {} })),
    ],
  }),
  runNow: (kind, org_id) => {
    if (jobs.some((j) => j.kind === kind && j.status === 'queued' && (j.org_id || null) === (org_id || null))) return ok({ queued: false, already_queued: true, message: 'Already queued.' });
    jobs.unshift({ id: jobs.length + 1, kind, org_id, status: 'queued', requested_at: new Date().toISOString(), org_name: orgs.find((o) => o.id === org_id)?.name, requested_by_name: 'Nikhil Bisht' });
    return ok({ queued: true, message: 'Queued (preview only). In the live app it runs on the next worker cycle.' });
  },
  setAutopilot: (b) => { autopilotCfg = b; return ok(b); },
  team: () => ok(team),
  setRole: (id, role) => { const e = team.find((x) => x.id === id); e.fb_role = role || ''; e.effective_role = e.department === 'Admin' ? 'admin' : role || null; return ok(); },
};
