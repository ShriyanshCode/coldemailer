// Shared bits: htm binding, hooks, formatting, small components.
import htm from 'https://unpkg.com/htm@3.1.1/dist/htm.module.js';
import { get } from './api.js';

export const { useState, useEffect, useCallback, useRef, useContext, createContext, useMemo } = React;
export const html = htm.bind(React.createElement);
export const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

export const fmt = n => (n ?? 0).toLocaleString('en-US');
export const pct = (x, d = 1) => (x === null || x === undefined ? '—' : `${(x * 100).toFixed(d)}%`);
export const timeOf = s => (s ? s.split(', ')[1] : '');

export const STATUS_COLOR = {
  new: 'var(--mute2)', first_sent: 'var(--accent)', follow_up_1_sent: 'var(--accent)', follow_up_2_sent: 'var(--accent)',
  follow_up_3_sent: 'var(--accent)', replied: 'var(--blue)', bounced: 'var(--red)', unsubscribed: 'var(--amber)',
  deactivated: 'var(--mute)',
};
export const EVENT_COLOR = {
  sent: 'var(--accent)', replied: 'var(--blue)', ooo: 'var(--blue)', hard_bounced: 'var(--red)', soft_bounced: 'var(--amber)',
  deferred: 'var(--mute2)', content_blocked: 'var(--red)', needs_review: 'var(--amber)', unsubscribed: 'var(--amber)',
  complaint: 'var(--red)', deactivated: 'var(--mute)', mailbox_paused: 'var(--red)', mailbox_resumed: 'var(--accent)',
  ai_call: 'var(--violet)', ai_fallback: 'var(--violet)', template_generated: 'var(--violet)', classified: 'var(--blue)',
  error: 'var(--red)', human_action: 'var(--ink)',
};
export const STEP_COLORS = ['var(--accent)', 'var(--blue)', 'var(--amber)', 'var(--violet)'];
export const STEP_NAMES = ['First email', 'Follow-up 1', 'Follow-up 2', 'Follow-up 3'];
export const ACTION_LABEL = {
  retry_now: 'Retry now', retry_later: 'Retry later (+3 days)', mark_bounced: 'Mark bounced', suppress: 'Suppress',
  'label:interested': 'Interested', 'label:not_interested': 'Not interested', 'label:out_of_office': 'Out of office',
  'label:unsubscribe': 'Unsubscribe',
};

// Fetch `path` with `params`; refetches when params change or the app-wide revision bumps.
export function useFetch(path, params = {}, enabled = true) {
  const { rev } = useApp();
  const key = JSON.stringify(params);
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    setState(s => ({ ...s, loading: true, error: null }));
    get(path, params)
      .then(data => live && setState({ data, error: null, loading: false }))
      .catch(e => live && setState({ data: null, error: e.message, loading: false }));
    return () => { live = false; };
  }, [path, key, rev, nonce, enabled]);
  return { ...state, reload: () => setNonce(n => n + 1) };
}

// Loading / error / empty states with a Retry button, as in the mockup.
export function StateBox({ q, empty, children }) {
  if (q.error) return html`<div class="state"><span>Could not load data: ${q.error}</span>
    <button class="btn" onClick=${q.reload}>Retry</button></div>`;
  if (!q.data) return html`<div class="state">Loading…</div>`;
  if (q.data.initialised === false) return html`<div class="state">No data yet. Run <span class="mono">python -m coldemailer seed</span>.</div>`;
  if (empty && empty(q.data)) return html`<div class="state">Nothing here yet.</div>`;
  return children(q.data);
}

export function Card({ title, extra, children, flush }) {
  return html`<section class=${'card' + (flush ? ' flush' : '')}>
    ${title && html`<div class="row" style=${{ alignItems: 'center', flexWrap: 'wrap', marginBottom: 14 }}>
      <h2 style=${{ margin: 0 }}>${title}</h2>${extra}</div>`}
    ${children}</section>`;
}

export const Bar = ({ value, max, color = 'var(--accent)' }) => html`<div class="track">
  <div style=${{ width: `${max ? Math.min(100, (value / max) * 100) : 0}%`, background: color }}></div></div>`;
