// Review queue: soft bounces, policy rejections, content blocks and unsure replies waiting for a human.
import { post } from '../api.js';
import { html, useState, useApp, useFetch, StateBox, ACTION_LABEL } from '../ui.js';

const STATES = [['open', 'Unresolved'], ['resolved', 'Resolved'], ['all', 'All']];

export function Review() {
  const { act, openContact } = useApp();
  const [state, setState] = useState('open');
  const q = useFetch('reviews', { state });
  return html`<div class="stack" style=${{ gap: 16 }}>
    <div class="row" style=${{ alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
      <h1>Review queue</h1>
      <div class="seg">${STATES.map(([k, l]) => html`<button class=${k === state ? 'on' : ''} onClick=${() => setState(k)}>${l}</button>`)}</div>
    </div>
    <div class="note" style=${{ marginTop: -6 }}>Contacts here are on hold: nothing is sent to them until someone decides.
      Unsure replies are already stopped; a human only sets the label.</div>
    <${StateBox} q=${q} empty=${d => !d.length}>${items => html`<div class="rows" style=${{ gap: 12 }}>
      ${items.map(r => html`<div class="card" style=${{ padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 10, opacity: r.resolved ? 0.6 : 1 }}>
        <div class="row" style=${{ flexWrap: 'wrap', gap: 12 }}>
          <div class="contact-chip" title="Open contact" onClick=${() => openContact(r.contact_id)}>
            <div style=${{ fontWeight: 500 }}>${r.name} <span class="mute2" style=${{ fontWeight: 400, fontSize: 12 }}>· ${r.company}</span></div>
            <div class="mono mute" style=${{ fontSize: 11 }}>${r.email}</div></div>
          <div style=${{ display: 'flex', gap: 14, fontSize: 12, alignItems: 'center' }} class="mute">
            <span style=${{ fontWeight: 500, color: 'var(--amber)' }}>${r.reason}</span>
            ${r.attempts > 0 && html`<span>${r.attempts} attempt${r.attempts > 1 ? 's' : ''}</span>`}
            <span>${r.resolved ? 'waited' : 'waiting'} ${r.waiting}</span></div>
        </div>
        <div class="mono ink2" style=${{ fontSize: 12, background: 'var(--bg)', borderRadius: 6, padding: '8px 10px', lineHeight: 1.45 }}>${r.detail || '—'}</div>
        ${r.resolved ? html`<div style=${{ fontSize: 12, color: 'var(--accent)' }}>Resolved: ${r.resolution}</div>`
          : html`<div style=${{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>${r.actions.map(a => html`
            <button class="btn" onClick=${() => act(() => post(`reviews/${r.id}/resolve`, { action: a }), `${r.name}: ${ACTION_LABEL[a]}`)}>
              ${ACTION_LABEL[a] || a}</button>`)}</div>`}
      </div>`)}</div>`}<//>
  </div>`;
}
