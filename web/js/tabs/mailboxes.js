// Mailboxes: warm-up limits, today's usage, bounce/complaint rates, pause state and the warm-up schedule.
import { post } from '../api.js';
import { html, useApp, useFetch, StateBox, fmt, pct } from '../ui.js';

const COLS = '1.7fr 0.8fr 1.8fr 0.9fr 0.6fr 0.8fr 0.8fr 1.2fr';

function Ramp({ m, scale }) {
  const pts = m.series.map((s, i) => `${((i + 0.5) / m.series.length) * 100},${100 - (s.limit / scale) * 100}`).join(' ');
  const today = m.warmup_day;
  return html`<section class="card" style=${{ padding: '18px 20px' }}>
    <div class="row" style=${{ alignItems: 'baseline' }}><span class="mono" style=${{ fontSize: 12 }}>${m.address}</span>
      <span class="mute2" style=${{ fontSize: 11 }}>Warm-up: daily limit vs sends</span></div>
    <div style=${{ position: 'relative', height: 100, marginTop: 14, borderBottom: '1px solid var(--line)' }}>
      <div style=${{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'flex-end', gap: 2 }}>
        ${m.series.map((s, i) => html`<div title=${`day ${i + 1} (${s.day}): ${s.sent} sent / limit ${s.limit}`} style=${{ flex: 1,
          height: `${(s.sent / scale) * 100}%`, borderRadius: '2px 2px 0 0',
          background: i + 1 === today ? 'var(--blue)' : m.paused ? 'var(--red)' : 'var(--accent)', opacity: s.future ? 0.25 : 1 }}></div>`)}
      </div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" style=${{ position: 'absolute', inset: 0, width: '100%', height: '100%', overflow: 'visible' }}>
        <polyline points=${pts} fill="none" stroke="var(--ink)" stroke-width="1.5" vector-effect="non-scaling-stroke" stroke-dasharray="4 3"></polyline>
      </svg>
    </div>
    <div class="mono mute2 row" style=${{ fontSize: 10, marginTop: 4 }}><span>day 1</span><span>day 14</span><span>day 28</span></div>
  </section>`;
}

export function Mailboxes() {
  const { act } = useApp();
  const q = useFetch('mailboxes');
  return html`<div class="stack" style=${{ gap: 16 }}>
    <h1>Mailboxes</h1>
    <${StateBox} q=${q} empty=${d => !d.length}>${list => {
      const scale = Math.max(...list.map(m => m.max), 1);
      return html`
      <section class="card flush"><div class="table" style=${{ minWidth: 980, padding: '6px 20px 12px' }}>
        <div class="thead" style=${{ gridTemplateColumns: COLS, padding: '12px 0 8px', background: 'none' }}>
          <span>Mailbox</span><span>Warm-up day</span><span>Used today / limit</span><span>Tomorrow</span><span>Max</span>
          <span>Bounce</span><span>Complaint</span><span>State</span></div>
        ${list.map(m => {
          const color = m.paused ? 'var(--red)' : 'var(--accent)';
          return html`<div class="trow" style=${{ gridTemplateColumns: COLS, padding: '12px 0' }}>
            <span class="mono" style=${{ fontSize: 12 }}>${m.address}</span><span>Day ${m.warmup_day}</span>
            <div style=${{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <div class="track" style=${{ flex: 1 }}><div style=${{ width: `${m.limit_today ? Math.min(100, (m.used_today / m.limit_today) * 100) : 0}%`, background: color }}></div></div>
              <span class="n" style=${{ width: 66, textAlign: 'right' }}>${fmt(m.used_today)}/${fmt(m.limit_today)}</span></div>
            <span class="n">${fmt(m.limit_tomorrow)}</span><span class="n mute">${fmt(m.max)}</span>
            <span class="n" style=${{ color: m.bounce_rate > 0.05 ? 'var(--red)' : 'var(--ink2)' }}>${pct(m.bounce_rate)}</span>
            <span class="n">${pct(m.complaint_rate, 2)}</span>
            <div style=${{ display: 'flex', flexDirection: 'column', gap: 4, alignItems: 'flex-start' }}>
              <span style=${{ fontSize: 12, fontWeight: 500, color }}>${m.paused ? 'Paused' : 'Active'}</span>
              ${m.paused && html`<span class="mute" style=${{ fontSize: 11, lineHeight: 1.3 }}>${m.paused_reason}</span>
                <button class="btn" style=${{ padding: '4px 10px' }} onClick=${() => act(() => post(`mailboxes/${m.id}/resume`), `${m.address} resumed`)}>Resume</button>`}
            </div></div>`;
        })}
      </div></section>
      <div class="grid g-2">${list.map(m => html`<${Ramp} m=${m} scale=${scale} />`)}</div>`;
    }}<//>
  </div>`;
}
