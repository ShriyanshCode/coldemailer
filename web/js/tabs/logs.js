// Logs: every event, filterable by group/type/search, paged with "Load more", optional live tail.
import { get } from '../api.js';
import { html, useState, useEffect, useRef, useApp, fmt, EVENT_COLOR } from '../ui.js';

const GROUPS = {
  Email: [['sent', 'Sent'], ['replied', 'Reply'], ['ooo', 'Out of office'], ['hard_bounced', 'Hard bounce'],
    ['soft_bounced', 'Soft bounce'], ['deferred', 'Deferred'], ['content_blocked', 'Content blocked']],
  Contact: [['needs_review', 'Needs review'], ['unsubscribed', 'Unsubscribe'], ['complaint', 'Complaint'],
    ['deactivated', 'Deactivated'], ['classified', 'Classified']],
  Mailbox: [['mailbox_paused', 'Mailbox paused'], ['mailbox_resumed', 'Mailbox resumed']],
  AI: [['ai_call', 'AI call'], ['ai_fallback', 'AI fallback'], ['template_generated', 'Template generated']],
  System: [['error', 'Error']],
  Human: [['human_action', 'Human action']],
};
const COLS = '120px 140px 1.4fr 2.6fr';

export function Logs() {
  const { range, rev, openContact } = useApp();
  const [group, setGroup] = useState('All');
  const [type, setType] = useState('');
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  const [data, setData] = useState({ rows: [], next: null, total: 0 });
  const [error, setError] = useState(null);
  const [live, setLive] = useState(false);
  const [pending, setPending] = useState(0);
  const top = useRef(null);

  useEffect(() => { const t = setTimeout(() => setQuery(q), 250); return () => clearTimeout(t); }, [q]);
  const params = { group: group === 'All' ? undefined : group, type: type || undefined, q: query || undefined,
    day_from: live ? undefined : range.from, day_to: live ? undefined : range.to };
  const load = () => get('events', { ...params, limit: 40 })
    .then(d => { setData(d); setError(null); setPending(0); top.current = d.rows[0]?.id ?? null; })
    .catch(e => setError(e.message));
  useEffect(() => { if (range.from || live) load(); }, [group, type, query, range.from, range.to, rev, live]);
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => get('events', { ...params, limit: 40 }).then(d => {
      const i = d.rows.findIndex(r => r.id === top.current);
      setPending(i === -1 ? d.rows.length : i);
    }).catch(() => {}), 3000);
    return () => clearInterval(t);
  }, [live, group, type, query]);
  const more = () => get('events', { ...params, limit: 40, before: data.next })
    .then(d => setData({ ...d, rows: [...data.rows, ...d.rows] }));

  const types = group === 'All' ? Object.values(GROUPS).flat() : GROUPS[group];
  return html`<div class="stack" style=${{ gap: 14 }}>
    <div class="row" style=${{ alignItems: 'baseline' }}><h1>Logs</h1>
      <span class="mute" style=${{ fontSize: 13 }}>${fmt(data.total)} events · ${live ? 'live' : `sim ${range.from} – ${range.to}`}</span></div>
    <div style=${{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
      <input class="input" style=${{ width: 240, background: 'var(--card)' }} value=${q} onInput=${e => setQ(e.target.value)} placeholder="Search by email or company" />
      <div style=${{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>${['All', ...Object.keys(GROUPS)].map(g => html`
        <button class=${'pill' + (g === group ? ' on' : '')} onClick=${() => { setGroup(g); setType(''); }}>${g}</button>`)}</div>
      <select class="sel" value=${type} onChange=${e => setType(e.target.value)}>
        <option value="">All types</option>${types.map(([k, l]) => html`<option value=${k}>${l}</option>`)}</select>
      <button class="btn" onClick=${() => setLive(!live)}><span class="dot sm" style=${{ background: live ? '#3aa35a' : 'var(--mute2)' }}></span>Live tail</button>
    </div>
    ${live && pending > 0 && html`<button class="btn primary" style=${{ alignSelf: 'flex-start', borderRadius: 999, padding: '6px 14px' }}
      onClick=${load}>${pending}${pending >= 40 ? '+' : ''} new events · Show</button>`}
    <section class="card flush"><div class="table">
      <div class="thead" style=${{ gridTemplateColumns: COLS }}><span>Time (IST)</span><span>Type</span><span>Contact</span><span>Detail</span></div>
      ${error && html`<div class="state">${error}<button class="btn" onClick=${load}>Retry</button></div>`}
      ${data.rows.map(l => html`<div class=${'trow' + (l.contact_id ? ' click' : '')} style=${{ gridTemplateColumns: COLS }}
        onClick=${() => l.contact_id && openContact(l.contact_id)}>
        <span class="mono mute" style=${{ fontSize: 12 }}>${l.at_ist}</span>
        <span style=${{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 500 }}>
          <span class="dot sm" style=${{ background: EVENT_COLOR[l.type] || 'var(--mute)' }}></span>${l.label}</span>
        <span class="mono ellipsis" style=${{ fontSize: 12 }}>${l.email}</span><span class="ink2">${l.text}</span></div>`)}
      ${!error && data.rows.length === 0 && html`<div class="state mute2">No matching events in this range</div>`}
    </div></section>
    ${data.next && html`<button class="btn" style=${{ alignSelf: 'center', padding: '8px 20px', fontSize: 13 }} onClick=${more}>Load more</button>`}
  </div>`;
}
