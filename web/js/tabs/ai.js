// AI & Templates: OpenRouter usage, per-model table and the template cache grid.
// Each KPI card opens the rows behind it, so you can see exactly where AI was (or wasn't) used.
import { get } from '../api.js';
import { html, useState, useEffect, useApp, useFetch, StateBox, fmt, pct, STEP_NAMES } from '../ui.js';

function UsageList({ kind, label, onClose }) {
  const { openContact, rev } = useApp();
  const [d, setD] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { setD(null); get(`ai/list/${kind}`).then(setD).catch(e => setError(e.message)); }, [kind, rev]);
  const more = () => get(`ai/list/${kind}`, { offset: d.next }).then(n => setD({ ...n, rows: [...d.rows, ...n.rows] }));
  return html`<section class="card flush">
    <div class="row" style=${{ padding: '14px 18px', alignItems: 'start', gap: 12 }}>
      <div><h2 style=${{ margin: 0 }}>${label} <span class="mute2" style=${{ fontWeight: 400 }}>· ${d ? fmt(d.total) : '…'}</span></h2>
        <div class="mute" style=${{ fontSize: 12, marginTop: 4 }}>${d?.description}</div></div>
      <button class="x" onClick=${onClose}>×</button></div>
    ${error && html`<div class="state">${error}</div>`}
    ${d && d.rows.length === 0 && html`<div class="state mute2">Nothing here yet</div>`}
    ${d && d.rows.map(r => html`<div class=${'trow' + (r.contact_id ? ' click' : '')} onClick=${() => r.contact_id && openContact(r.contact_id)}
      style=${{ gridTemplateColumns: '110px 1.2fr 2.6fr auto' }}>
      <span class="mono mute" style=${{ fontSize: 12 }}>${r.at_ist}</span>
      <span class="mono ellipsis" style=${{ fontSize: 12 }}>${r.title}</span>
      <span class="ink2 ellipsis">${r.sub}</span>
      <span class="mono mute2" style=${{ fontSize: 11 }}>${r.note}</span></div>`)}
    ${d?.next && html`<div style=${{ padding: 12, textAlign: 'center' }}><button class="btn" onClick=${more}>Load more</button></div>`}
  </section>`;
}

function Usage({ d }) {
  const t = d.totals;
  const [open, setOpen] = useState(null);
  const kpis = [['calls', 'Calls', fmt(t.calls)], ['successes', 'Successes', fmt(t.successes)],
    ['rate_limited', '429 errors', fmt(t.rate_limited)], ['retries', 'Retries', fmt(t.retries)],
    ['fallback_next_model', 'Fallback to next model', fmt(t.fallback_next_model)],
    ['fallback_static', 'Fallback to static / rules', fmt(t.fallback_static)],
    ['ai_sends', 'Emails from AI templates', fmt(t.ai_sends)], ['ai_labels', 'Replies labelled by AI', fmt(t.ai_labels)],
    ['ai_sends', 'Template cache hits', pct(t.template_cache_hit_rate, 0)],
    ['latency', 'Avg latency', t.avg_latency_ms ? `${(t.avg_latency_ms / 1000).toFixed(1)}s` : '—']];
  const cols = '2.4fr repeat(6, 1fr)';
  return html`
    ${!d.configured && html`<div class="card note" style=${{ padding: '12px 16px', marginTop: 0 }}>No OpenRouter key configured: emails use
      the committed templates and replies use the rule classifier. Add OPENROUTER_API_KEY to .env to enable live calls.</div>`}
    <div class="grid g-kpi five">${kpis.map(([k, l, v]) => html`<button class=${'card kpi-btn' + (open?.label === l ? ' on' : '')}
      onClick=${() => setOpen(open?.label === l ? null : { kind: k, label: l })}>
      <div class="mute" style=${{ fontSize: 12 }}>${l}</div><div class="mono" style=${{ fontSize: 22, fontWeight: 500, marginTop: 4 }}>${v}</div></button>`)}</div>
    ${open && html`<${UsageList} kind=${open.kind} label=${open.label} onClose=${() => setOpen(null)} />`}
    <section class="card flush"><div class="table" style=${{ minWidth: 760, padding: '6px 20px 8px' }}>
      <div class="thead" style=${{ gridTemplateColumns: cols, padding: '12px 0 8px', background: 'none' }}>
        <span>Model</span><span>Calls</span><span>Success</span><span>429s</span><span>Retries</span><span>Errors</span><span>Avg latency</span></div>
      ${d.models.length === 0 && html`<div class="mute" style=${{ padding: '14px 0' }}>No model calls yet. Fallback chain: ${d.chain.join(' → ') || 'none configured'}</div>`}
      ${d.models.map(m => html`<div class="trow mono" style=${{ gridTemplateColumns: cols, padding: '11px 0', fontSize: 12 }}>
        <span style=${{ fontFamily: 'Instrument Sans', fontSize: 13 }}>${m.model}</span><span>${fmt(m.calls)}</span><span>${fmt(m.ok)}</span>
        <span>${fmt(m.rate_limited)}</span><span>${fmt(m.retries)}</span><span>${fmt(m.errors)}</span>
        <span>${m.avg_latency_ms ? `${(m.avg_latency_ms / 1000).toFixed(1)}s` : '—'}</span></div>`)}
    </div></section>`;
}

function Templates({ d }) {
  const [sel, setSel] = useState([0, 0]);
  const [variant, setVariant] = useState('A');
  const cell = (seg, step) => d.cells.find(c => c.segment === seg && c.step === step && c.variant === variant);
  const t = cell(d.segments[sel[0]], sel[1] + 1);
  return html`<section class="card">
    <div class="row" style=${{ alignItems: 'center', flexWrap: 'wrap', gap: 10 }}>
      <div><h2 style=${{ margin: '0 0 4px' }}>Template cache</h2>
        <div class="mute" style=${{ fontSize: 12 }}>Segment by step. Select a cell to read the copy; merge fields stay visible.</div></div>
      <div class="seg">${['A', 'B'].map(v => html`<button class=${v === variant ? 'on' : ''} onClick=${() => setVariant(v)}>Variant ${v}</button>`)}</div>
    </div>
    <div style=${{ overflowX: 'auto', marginTop: 14 }}><div style=${{ minWidth: 560, display: 'grid', gridTemplateColumns: '130px repeat(4, 1fr)', gap: 6 }}>
      <span></span>${STEP_NAMES.map(n => html`<span class="mute2" style=${{ fontSize: 12, padding: '4px 2px' }}>${n}</span>`)}
      ${d.segments.map((seg, si) => html`<span class="ink2" style=${{ fontSize: 13, padding: '10px 2px' }}>${seg}</span>
        ${STEP_NAMES.map((_, ti) => {
          const c = cell(seg, ti + 1), on = sel[0] === si && sel[1] === ti;
          return html`<button onClick=${() => setSel([si, ti])} style=${{ fontSize: 12, padding: 10, borderRadius: 8, cursor: 'pointer', textAlign: 'left',
            border: `1px solid ${on ? 'var(--accent)' : 'var(--line2)'}`, background: on ? 'var(--track)' : 'var(--card)' }}>
            <span style=${{ display: 'inline-block', width: 7, height: 7, borderRadius: '50%', marginRight: 7,
              background: c.source === 'ai' ? 'var(--violet)' : 'var(--mute2)' }}></span>${c.source === 'ai' ? 'AI' : 'Static'}</button>`;
        })}`)}
    </div></div>
    ${t && html`<div style=${{ marginTop: 16, border: '1px solid var(--line)', borderRadius: 8, padding: 16, background: 'var(--bg)',
      display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div class="mute" style=${{ display: 'flex', gap: 18, flexWrap: 'wrap', fontSize: 12 }}>
        <span>${t.segment} · ${STEP_NAMES[t.step - 1]} · variant ${t.variant}</span>
        <span>Model: <span class="mono ink2">${t.model || 'static template (no model)'}</span></span>
        <span>Generated ${t.created_ist} IST</span></div>
      <div style=${{ fontWeight: 500 }}>${t.subject}</div>
      <div class="prose">${t.body}</div></div>`}
  </section>`;
}

export function AiTab() {
  const usage = useFetch('ai');
  const tpl = useFetch('templates');
  return html`<div class="stack">
    <h1>AI & Templates</h1>
    <${StateBox} q=${usage}>${d => html`<${Usage} d=${d} />`}<//>
    <${StateBox} q=${tpl}>${d => html`<${Templates} d=${d} />`}<//>
  </div>`;
}
