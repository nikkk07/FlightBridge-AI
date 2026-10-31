import { useState } from 'react';
import { api } from '../api';
import { Empty, Loading, Problem, Risk, countryName, useLoad, when } from '../lib';

const FILTERS = [['', 'All'], ['RED', 'Needs you'], ['AMBER', 'Check'], ['GREEN', 'Routine'], ['unmatched', 'Unknown sender']];
const INTENT = {
  interested: 'Interested', question: 'Asked a question', pricing: 'Asked about fees or terms', not_interested: 'Not interested',
  unsubscribe: 'Asked to stop', bounce: 'Bounced', auto_reply: 'Auto-reply', legal: 'Legal or complaint', other: 'Other',
};

export default function Inbox() {
  const [f, setF] = useState('');
  const [open, setOpen] = useState(null);
  const list = useLoad(() => api.inbox(f === 'unmatched' ? { unmatched: true } : { risk: f }), [f]);

  return (
    <div className="page">
      <header className="page-head">
        <h1>Replies</h1>
        <p className="muted">Replies from schools, sorted by how much attention they need. Opt-outs and bounces are applied automatically.</p>
      </header>
      <div className="tabs" role="tablist">
        {FILTERS.map(([k, v]) => <button key={k} role="tab" aria-selected={f === k} className={f === k ? 'on' : ''} onClick={() => setF(k)}>{v}</button>)}
      </div>
      {list.loading && <Loading />}
      <Problem error={list.error} onRetry={list.reload} />
      {list.data && list.data.length === 0 && <Empty title="No replies here">Replies show up within a few minutes of arriving in the mailbox.</Empty>}
      {list.data && list.data.length > 0 && (
        <ul className="mail">
          {list.data.map((m) => (
            <li key={m.id} className={open === m.id ? 'open' : ''}>
              <button className="mail-head" onClick={() => setOpen(open === m.id ? null : m.id)} aria-expanded={open === m.id}>
                <Risk value={m.risk} />
                <span className="grow">
                  <b>{m.org_name || m.from_address}</b>
                  {m.org_name && <span className="muted"> {countryName(m.country)}</span>}
                  <span className="mail-subject">{m.subject}</span>
                </span>
                <span className="muted nowrap">{when(m.occurred_at, true)}</span>
              </button>
              {open === m.id && (
                <div className="mail-body">
                  <p className="muted small">
                    From {m.from_address}. {INTENT[m.intent] || m.intent || 'Not classified'}
                    {m.confidence != null && `, ${Math.round(m.confidence * 100)}% sure`}.
                    {m.match_method === 'unmatched' && ' We couldn’t link this to a school we wrote to.'}
                  </p>
                  {(m.is_unsubscribe || m.is_bounce || m.is_auto_reply || m.injection) && (
                    <p className="flags">
                      {m.is_unsubscribe && <span>Opt-out recorded</span>}
                      {m.is_bounce && <span>Bounce</span>}
                      {m.is_auto_reply && <span>Auto-reply</span>}
                      {m.injection && <span className="warn">Contains instructions aimed at software – treat with care</span>}
                    </p>
                  )}
                  <pre>{m.preview}</pre>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
