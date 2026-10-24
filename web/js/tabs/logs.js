// Logs: every event, filterable by group/type/search, paged with "Load more", optional live tail.
// Click a row to expand it: the email that was sent, its attempts, any reply and who classified it.
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
const COLS = '16px 120px 140px 1.4fr 2.6fr';

const Kv = ({ k, v }) => v && html`<div class="kv"><span class="mute2">${k}</span><span class="ink2" style=${{ wordBreak: 'break-word' }}>${v}</span></div>`;

function EventDetail({ id }) {
  const { openContact } = useApp();
  const [d, setD] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { get(`events/${id}`).then(setD).catch(e => setError(e.message)); }, [id]);
  if (error) return html`<div class="tl-body mute">${error}</div>`;
  if (!d) return html`<div class="tl-body mute2">Loading…</div>`;
  const e = d.email, cap = d.capacity;
  const extra = Object.entries(d.detail).filter(([k]) => !['step', 'mailbox', 'reason', 'label'].includes(k));
  return html`<div class="tl-body log-detail">
    ${d.contact && html`<div class="row" style=${{ alignItems: 'center' }}>
      <span><b>${d.contact.name}</b> <span class="mute">· ${d.contact.company} · ${d.contact.email}</span></span>
      <button class="chip" onClick=${() => openContact(d.contact.id)}>Open contact</button></div>`}
    ${d.explain && html`<div class="ink2">${d.explain}</div>`}
    ${cap?.own && html`<${Kv} k="Its mailbox" v=${`${cap.own.address}: ${fmt(cap.own.sent)} of ${fmt(cap.own.limit)} sent on ${cap.day}`
      + (cap.own.paused ? ' · paused now' : '')} />`}
    ${cap && html`<${Kv} k="All active" v=${`${fmt(cap.sent)} of ${fmt(cap.limit)} sent on ${cap.day} across ${cap.mailboxes - cap.paused_now} active mailboxes`
      + (cap.paused_now ? ` · ${cap.paused_now} paused now` : '')} />`}
    ${e && html`<div class="log-section">
      <div class="mute2 log-h">Email sent${e.imported ? ' (imported history)' : ''}</div>
      <${Kv} k="Step" v=${e.step} /><${Kv} k="From" v=${e.mailbox} /><${Kv} k="Sent at" v=${e.sent_at_ist && `${e.sent_at_ist} IST`} />
      <${Kv} k="Written by" v=${e.template && (e.template_ref ? `${e.template} · ${e.template_ref}` : e.template)} />
      <${Kv} k="State" v=${e.state} /><${Kv} k="Subject" v=${e.subject} />
      ${e.body && html`<div class="prose log-mail">${e.body}</div>`}</div>`}
    ${d.attempts.length > 1 && html`<div class="log-section"><div class="mute2 log-h">Delivery attempts</div>
      ${d.attempts.map(a => html`<${Kv} k=${`Attempt ${a.attempt}`} v=${`${a.result}${a.smtp_code ? ` (${a.smtp_code})` : ''} · ${a.at_ist} IST`} />`)}</div>`}
    ${d.replies.map(r => html`<div class="log-section">
      <div class="mute2 log-h">Reply received · ${r.received_at_ist} IST</div>
      <div class="prose log-mail">${r.body}</div>
      <${Kv} k="Classified" v=${r.label ? `${r.label} · by ${r.by}` : 'Not yet classified'} /></div>`)}
    ${!e && extra.length > 0 && html`<div class="log-section">${extra.map(([k, v]) =>
      html`<${Kv} k=${k.replace(/_/g, ' ')} v=${typeof v === 'object' ? JSON.stringify(v) : String(v)} />`)}</div>`}
  </div>`;
}

export function Logs() {
  const { range, rev } = useApp();
  const [group, setGroup] = useState('All');
  const [type, setType] = useState('');
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  const [data, setData] = useState({ rows: [], next: null, total: 0 });
  const [error, setError] = useState(null);
  const [live, setLive] = useState(false);
  const [pending, setPending] = useState(0);
  const [open, setOpen] = useState(null);
  const top = useRef(null);

  useEffect(() => { const t = setTimeout(() => setQuery(q), 250); return () => clearTimeout(t); }, [q]);
  const params = { group: group === 'All' ? undefined : group, type: type || undefined, q: query || undefined,
    day_from: live ? undefined : range.from, day_to: live ? undefined : range.to };
  const seq = useRef(0);  // a slow response for an old filter must not overwrite a newer one
  const load = () => {
    const n = ++seq.current;
    return get('events', { ...params, limit: 40 })
      .then(d => { if (n !== seq.current) return; setData(d); setError(null); setPending(0); top.current = d.rows[0]?.id ?? null; })
      .catch(e => { if (n === seq.current) setError(e.message); });
  };
  useEffect(() => { setOpen(null); }, [group, type, query, range.from, range.to, live]);
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
      <div class="thead" style=${{ gridTemplateColumns: COLS }}><span></span><span>Time (IST)</span><span>Type</span><span>Contact</span><span>Detail</span></div>
      ${error && html`<div class="state">${error}<button class="btn" onClick=${load}>Retry</button></div>`}
      ${data.rows.map(l => html`<div key=${l.id}><div class="trow click" style=${{ gridTemplateColumns: COLS }}
        onClick=${() => setOpen(open === l.id ? null : l.id)}>
        <span class="mute2" style=${{ fontSize: 10 }}>${open === l.id ? '▼' : '▶'}</span>
        <span class="mono mute" style=${{ fontSize: 12 }}>${l.at_ist}</span>
        <span style=${{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12, fontWeight: 500 }}>
          <span class="dot sm" style=${{ background: EVENT_COLOR[l.type] || 'var(--mute)' }}></span>${l.label}</span>
        <span class="mono ellipsis" style=${{ fontSize: 12 }}>${l.email}</span><span class="ink2">${l.text}</span></div>
        ${open === l.id && html`<${EventDetail} id=${l.id} />`}</div>`)}
      ${!error && data.rows.length === 0 && html`<div class="state mute2">No matching events in this range</div>`}
    </div></section>
    ${data.next && html`<button class="btn" style=${{ alignSelf: 'center', padding: '8px 20px', fontSize: 13 }} onClick=${more}>Load more</button>`}
  </div>`;
}
