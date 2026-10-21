// Right-hand drawer: a list (status / KPI) or one contact's facts, actions and timeline.
import { get, post } from './api.js';
import { html, useState, useEffect, useApp, useFetch, fmt, ACTION_LABEL, EVENT_COLOR } from './ui.js';

const LISTS = {
  new: ['New', 'Loaded, not yet emailed. They enter the sequence when a mailbox has room and their window is open.', { status: 'new' }],
  first_sent: ['First sent', 'First email sent. Follow-up 1 after a 3 day wait.', { status: 'first_sent' }],
  follow_up_1_sent: ['Follow-up 1 sent', 'Follow-up 2 after a 5 day wait.', { status: 'follow_up_1_sent' }],
  follow_up_2_sent: ['Follow-up 2 sent', 'Follow-up 3 after a 7 day wait.', { status: 'follow_up_2_sent' }],
  follow_up_3_sent: ['Follow-up 3 sent', 'Deactivated if no reply arrives within 7 days.', { status: 'follow_up_3_sent' }],
  replied: ['Replied', 'Prospects who replied. The sequence stopped at once; the reply was then classified.', { status: 'replied' }],
  bounced: ['Bounced', 'Hard bounces. Suppressed and never retried.', { status: 'bounced' }],
  unsubscribed: ['Unsubscribed', 'Asked not to be contacted (link, reply, complaint or operator). All suppressed.', { status: 'unsubscribed' }],
  deactivated: ['Deactivated', 'All follow-ups sent and the last wait passed with no reply.', { status: 'deactivated' }],
  due_today: ['Due today', "Next step scheduled for today (including overdue), waiting for the prospect's window and mailbox capacity.", { due: 'today' }],
  due_tomorrow: ['Due tomorrow', 'Next step scheduled for tomorrow.', { due: 'tomorrow' }],
  ooo: ['Paused for out of office', 'Replied with an out-of-office message. The sequence continues after the return date.', { ooo: 'yes' }],
  sent: ['Sent', 'Emails accepted by the mock provider in the selected range, newest first.', null],
};

function ContactList({ listKey }) {
  const { openContact, range, rev } = useApp();
  const [title, desc, params] = LISTS[listKey];
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(null);
  const [cursor, setCursor] = useState(0);
  const load = (reset) => {
    if (params) {
      const page = reset ? 0 : cursor;
      get('contacts', { ...params, size: 25, page }).then(d => {
        setRows(r => (reset ? d.rows : [...r, ...d.rows])); setTotal(d.total); setCursor(page + 1);
      });
    } else {
      get('events', { type: 'sent', day_from: range.from, day_to: range.to, limit: 25, before: reset ? undefined : cursor })
        .then(d => {
          const mapped = d.rows.map(e => ({ id: e.contact_id, name: e.email, company: e.text, note: e.at_ist }));
          setRows(r => (reset ? mapped : [...r, ...mapped])); setTotal(d.total); setCursor(d.next);
        });
    }
  };
  useEffect(() => load(true), [listKey, rev]);
  const more = params ? rows.length < (total || 0) : !!cursor;
  return html`
    <div class="row" style=${{ alignItems: 'start', gap: 12 }}>
      <div><div style=${{ fontSize: 18, fontWeight: 600 }}>${title}</div>
        <div class="mute" style=${{ marginTop: 6, lineHeight: 1.45 }}>${desc}</div></div>
      <${Close} /></div>
    <div class="mono" style=${{ fontSize: 34, fontWeight: 500, letterSpacing: '-.02em' }}>${total === null ? '…' : fmt(total)}</div>
    <div>
      <h2 style=${{ marginBottom: 6 }}>${params ? 'Contacts' : 'Emails'} <span class="mute2" style=${{ fontWeight: 400 }}>· showing ${fmt(rows.length)} of ${fmt(total || 0)}</span></h2>
      ${rows.map(r => html`<div class="click" onClick=${() => openContact(r.id)} style=${{ display: 'grid', gridTemplateColumns: '1fr auto',
        gap: 10, padding: '10px 8px', margin: '0 -8px', borderBottom: '1px solid var(--row)', cursor: 'pointer', borderRadius: 6 }}>
        <div style=${{ minWidth: 0 }}><div class="ellipsis">${r.name}</div>
          <div class="mute ellipsis" style=${{ fontSize: 12 }}>${r.company}${r.role ? ` · ${r.role}` : ''}</div></div>
        <span class="mute" style=${{ fontSize: 12, textAlign: 'right', maxWidth: 170 }}>${r.note ?? noteFor(r)}</span>
      </div>`)}
      ${more && html`<button class="btn" style=${{ marginTop: 12 }} onClick=${() => load(false)}>Load more</button>`}
    </div>`;
}

const noteFor = r => (r.hold_label ? `On hold: ${r.hold_label}` : r.next_action_label ? `${r.next_action_label}, ${r.next_action_ist}` : r.status_label);

const Close = () => { const { closeDrawer } = useApp(); return html`<button class="x" onClick=${closeDrawer}>×</button>`; };

function ContactView({ id, fromList }) {
  const { backToList, act } = useApp();
  const q = useFetch(`contacts/${id}`);
  const [open, setOpen] = useState({});
  if (q.error) return html`<div class="state">${q.error}<${Close} /></div>`;
  if (!q.data) return html`<div class="state">Loading…</div>`;
  const d = q.data;
  const actions = [];
  for (const r of d.reviews) for (const a of r.actions)
    actions.push([ACTION_LABEL[a] || a, () => act(() => post(`reviews/${r.id}/resolve`, { action: a }), ACTION_LABEL[a])]);
  if (!d.suppressed) actions.push(['Suppress', () => act(() => post(`contacts/${id}/suppress`), 'Suppressed')]);
  return html`
    <div class="row" style=${{ alignItems: 'start' }}>
      <div>${fromList && html`<button class="x" style=${{ fontSize: 12, padding: '0 0 8px' }} onClick=${backToList}>‹ Back to list</button>`}
        <div style=${{ fontSize: 18, fontWeight: 600 }}>${d.contact.name}</div>
        <div class="mono mute" style=${{ fontSize: 12, marginTop: 4 }}>${d.contact.email}</div></div>
      <${Close} /></div>
    <div class="facts">${d.facts.map(f => html`<div><div class="k">${f.k}</div><div class="v">${f.v}</div></div>`)}</div>
    ${actions.length > 0 && html`<div style=${{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
      ${actions.map(([label, go]) => html`<button class="btn" onClick=${go}>${label}</button>`)}</div>`}
    <div><h2>Timeline</h2>
      <div class="rows">${d.timeline.length === 0 && html`<div class="mute">Not contacted yet.</div>`}
        ${d.timeline.map(e => {
          const has = e.fields.length || e.body;
          const isOpen = !!open[e.id];
          return html`<div class="tl">
            <div class="tl-head click" style=${{ cursor: has ? 'pointer' : 'default' }} onClick=${() => has && setOpen({ ...open, [e.id]: !isOpen })}>
              <span class="mono mute" style=${{ fontSize: 11, paddingTop: 2 }}>${e.at_ist}</span>
              <div><div style=${{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span class="dot sm" style=${{ background: EVENT_COLOR[e.type] || 'var(--mute)' }}></span>
                  <span style=${{ fontWeight: 500 }}>${e.label}</span></div>
                <div class="mute" style=${{ fontSize: 12, marginTop: 2, lineHeight: 1.4 }}>${e.text}</div></div>
              <span class="mute2">${has ? (isOpen ? '▾' : '▸') : ''}</span>
            </div>
            ${isOpen && html`<div class="tl-body">
              ${e.fields.map(f => html`<div class="kv"><span class="mute2">${f.k}</span><span class="ink2" style=${{ wordBreak: 'break-word' }}>${f.v}</span></div>`)}
              ${e.body && html`<div class="prose">${e.body}</div>`}
            </div>`}
          </div>`;
        })}</div></div>`;
}

export function Drawer() {
  const { drawer, closeDrawer } = useApp();
  return html`<div class="overlay" onClick=${closeDrawer}></div>
    <aside class="drawer">
      ${drawer.contact ? html`<${ContactView} id=${drawer.contact} fromList=${!!drawer.list} />` : html`<${ContactList} listKey=${drawer.list} />`}
    </aside>`;
}
