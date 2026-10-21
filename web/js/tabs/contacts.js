// Contacts: filterable, paged table of every contact.
import { html, useState, useEffect, useApp, useFetch, StateBox, fmt, STATUS_COLOR, STEP_NAMES } from '../ui.js';

const STATUSES = [['new', 'New'], ['first_sent', 'First sent'], ['follow_up_1_sent', 'Follow-up 1 sent'],
  ['follow_up_2_sent', 'Follow-up 2 sent'], ['follow_up_3_sent', 'Follow-up 3 sent'], ['replied', 'Replied'],
  ['bounced', 'Bounced'], ['unsubscribed', 'Unsubscribed'], ['deactivated', 'Deactivated']];
const SEGMENTS = ['SaaS', 'Fintech', 'Retail', 'Healthcare', 'Logistics'];
const ZONES = ['Asia/Kolkata', 'America/New_York', 'America/Los_Angeles', 'Europe/London', 'Europe/Berlin',
  'Asia/Singapore', 'Australia/Sydney'];
const COLS = '1.2fr 1.7fr 1fr 1.1fr 0.8fr 1.6fr 1.4fr 1fr';

function pager(page, pages) {
  const nums = [...new Set([0, page - 1, page, page + 1, pages - 1])].filter(p => p >= 0 && p < pages).sort((a, b) => a - b);
  const out = [];
  nums.forEach((p, i) => { if (i && p - nums[i - 1] > 1) out.push(null); out.push(p); });
  return out;
}

export function Contacts() {
  const { openContact, contactFilters } = useApp();
  const mailboxes = useFetch('mailboxes');
  const [f, setF] = useState({});
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(25);
  useEffect(() => { setF(contactFilters || {}); setPage(0); }, [contactFilters]);
  const q = useFetch('contacts', { ...f, page, size });
  const set = (k, v) => { setF({ ...f, [k]: v || undefined }); setPage(0); };

  const filters = [
    ['Status', 'status', STATUSES],
    ['Step', 'step', STEP_NAMES.map((n, i) => [String(i + 1), n])],
    ['Segment', 'segment', SEGMENTS.map(s => [s, s])],
    ['Timezone', 'timezone', ZONES.map(z => [z, z])],
    ['Mailbox', 'mailbox_id', (mailboxes.data || []).map(m => [String(m.id), m.address])],
    ['On hold', 'on_hold', [['yes', 'Yes'], ['no', 'No']]],
  ];
  const total = q.data?.total || 0;
  const pages = Math.max(1, Math.ceil(total / size));
  return html`<div class="stack" style=${{ gap: 16 }}>
    <div class="row" style=${{ alignItems: 'baseline' }}><h1>Contacts</h1>
      <span class="mute" style=${{ fontSize: 13 }}>${total ? `${fmt(page * size + 1)}–${fmt(Math.min((page + 1) * size, total))} of ${fmt(total)}` : '0 contacts'}</span></div>
    <div style=${{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
      ${filters.map(([label, key, opts]) => html`<label class="f">${label}
        <select class="sel" value=${f[key] ?? ''} onChange=${e => set(key, e.target.value)}>
          <option value="">All</option>${opts.map(([v, l]) => html`<option value=${v}>${l}</option>`)}
        </select></label>`)}
      <label class="f">Search<input class="input" style=${{ padding: '5px 10px', fontSize: 12, minWidth: 180 }} value=${f.q || ''}
        placeholder="email, name or company" onInput=${e => set('q', e.target.value)} /></label>
    </div>
    <section class="card flush"><div class="table" style=${{ minWidth: 1000 }}>
      <div class="thead" style=${{ gridTemplateColumns: COLS }}><span>Name</span><span>Email</span><span>Company</span><span>Status</span>
        <span>Step</span><span>Next action (IST)</span><span>Mailbox</span><span>Timezone</span></div>
      <${StateBox} q=${q} empty=${d => !d.rows.length}>${d => d.rows.map(c => html`
        <div class="trow click" style=${{ gridTemplateColumns: COLS }} onClick=${() => openContact(c.id)}>
          <span class="ellipsis">${c.name}</span><span class="mono ellipsis" style=${{ fontSize: 11 }}>${c.email}</span>
          <span class="ink2 ellipsis">${c.company}</span>
          <span style=${{ display: 'flex', alignItems: 'center', gap: 6 }}><span class="dot sm" style=${{ background: STATUS_COLOR[c.status] }}></span>${c.status_label}</span>
          <span class="mute">${c.step_label || '—'}</span>
          <span class="ink2">${c.hold_label ? `On hold: ${c.hold_label.toLowerCase()}` : c.next_action_label ? `${c.next_action_label}, ${c.next_action_ist}` : '—'}</span>
          <span class="mono mute ellipsis" style=${{ fontSize: 11 }}>${c.mailbox || '—'}</span><span class="mute ellipsis">${c.timezone}</span>
        </div>`)}<//>
    </div></section>
    ${total > 0 && html`<div class="row" style=${{ alignItems: 'center', flexWrap: 'wrap', gap: 12 }}>
      <div style=${{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12 }} class="mute">Rows
        <div class="seg">${[25, 50, 100].map(n => html`<button class=${n === size ? 'on' : ''} onClick=${() => { setSize(n); setPage(0); }}>${n}</button>`)}</div></div>
      <div style=${{ display: 'flex', alignItems: 'center', gap: 4 }}>
        <button class="btn" disabled=${page === 0} onClick=${() => setPage(page - 1)}>‹ Prev</button>
        ${pager(page, pages).map(p => p === null ? html`<span class="mute2" style=${{ padding: '0 6px' }}>…</span>`
          : html`<button class=${'btn' + (p === page ? ' primary' : '')} style=${{ minWidth: 32, justifyContent: 'center',
            background: p === page ? 'var(--ink)' : undefined }} onClick=${() => setPage(p)}>${p + 1}</button>`)}
        <button class="btn" disabled=${page >= pages - 1} onClick=${() => setPage(page + 1)}>Next ›</button>
      </div></div>`}
  </div>`;
}
