// Dashboard: KPIs, funnel, replies, sends per hour, deferrals, due, suppression, health, campaign settings.
import { post } from '../api.js';
import { html, useApp, StateBox, Card, Bar, fmt, pct, STATUS_COLOR, STEP_COLORS, STEP_NAMES } from '../ui.js';

function Kpis({ k, range }) {
  const { openList, setTab } = useApp();
  const one = range.from === range.to;
  const cards = [
    [one ? 'Sent today' : 'Sent in range', fmt(k.sent_range), `of ${fmt(k.capacity_range)} mailbox capacity`, () => openList('sent')],
    ['Due today', fmt(k.due_today), 'across all steps, incl. overdue', () => openList('due_today')],
    ['Due tomorrow', fmt(k.due_tomorrow), 'across all steps', () => openList('due_tomorrow')],
    ['Reply rate', pct(k.reply_rate), `${fmt(k.replied)} replied of ${fmt(k.contacted)} contacted`, () => openList('replied')],
    ['Bounce rate', pct(k.bounce_rate), `${fmt(k.hard_bounces)} hard bounces of ${fmt(k.sends_range)} in range`, () => openList('bounced_range')],
    ['Deactivated', fmt(k.deactivated), `${pct(k.deactivated / Math.max(k.total, 1))} of contacts`, () => openList('deactivated')],
    ['Unsubscribed', fmt(k.unsubscribed), 'all on suppression list', () => openList('unsubscribed')],
    ['Needs review', fmt(k.needs_review), 'open items, human action', () => setTab('review')],
  ];
  return html`<div class="grid g-kpi">${cards.map(([label, value, note, go]) => html`
    <div class="card kpi" onClick=${go}>
      <div class="lbl"><span>${label}</span><span>›</span></div>
      <div class="val">${value}</div><div class="note">${note}</div>
    </div>`)}</div>`;
}

function Funnel({ d }) {
  const { openList, showContacts } = useApp();
  const max = Math.max(...d.statuses.map(s => s.count), 1);
  const total = Math.max(d.kpis.total, 1);
  return html`<${Card} title="Lifecycle funnel" extra=${html`<div style=${{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      <span class="chip" onClick=${() => showContacts({ on_hold: 'yes' })}>On hold (review) <b class="mono">${fmt(d.markers.on_hold)}</b></span>
      <span class="chip" onClick=${() => openList('ooo')}>Paused for OOO <b class="mono">${fmt(d.markers.paused_ooo)}</b></span>
    </div>`}>
    <div style=${{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      ${d.statuses.map(s => html`<div class="funnel-row" onClick=${() => openList(s.key)}>
        <span class="ink2">${s.label}</span>
        <${Bar} value=${s.count} max=${max} color=${STATUS_COLOR[s.key]} />
        <span class="n" style=${{ textAlign: 'right' }}>${fmt(s.count)} <span class="mute2">${pct(s.count / total)}</span></span>
        <span class="mute2">›</span></div>`)}
    </div><//>`;
}

function Replies({ d }) {
  const notes = { interested: 'Sequence stops', not_interested: 'Sequence stops, suppressed',
    out_of_office: 'Sequence continues after return date', unsubscribe: 'Moved to Unsubscribed' };
  const c = d.classifiers, acc = d.classifier_accuracy;
  return html`<${Card} title="Reply classification">
    <div class="rows" style=${{ gap: 12 }}>${d.replies.map(r => html`<div>
      <div class="row"><span>${r.label}</span><span class="n">${fmt(r.count)}</span></div>
      <div class="note" style=${{ marginTop: 2 }}>${notes[r.key]}</div></div>`)}</div>
    <div class="rows" style=${{ borderTop: '1px solid var(--row)', marginTop: 16, paddingTop: 14, gap: 8 }}>
      <div class="mute" style=${{ fontSize: 12 }}>Classified by</div>
      ${[['Rules', c.rule], ['AI', c.llm], ['Fallback', c.fallback], ['Human', c.human], ['Waiting to be classified', c.pending]]
        .map(([l, n]) => html`<div class="row"><span class="ink2">${l}</span><span class="n">${fmt(n)}</span></div>`)}
      ${acc.checked > 0 && html`<div class="note">Accuracy vs simulated ground truth: ${pct(acc.rate)} of ${fmt(acc.checked)}</div>`}
    </div><//>`;
}

function Hours({ d }) {
  const max = Math.max(...d.hours.map(h => h.steps.reduce((a, b) => a + b, 0)), 1);
  return html`<${Card} title=${html`Sends per hour today <span class="mute2" style=${{ fontWeight: 400 }}>· IST</span>`}
    extra=${html`<div style=${{ display: 'flex', gap: 10, fontSize: 11 }} class="mute">${STEP_NAMES.map((n, i) => html`
      <span style=${{ display: 'flex', alignItems: 'center', gap: 5 }}><span style=${{ width: 8, height: 8, borderRadius: 2, background: STEP_COLORS[i] }}></span>${n}</span>`)}</div>`}>
    <div style=${{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 140 }}>
      ${d.hours.map(h => {
        const total = h.steps.reduce((a, b) => a + b, 0);
        return html`<div title=${`${String(h.hour).padStart(2, '0')}:00 · ${total} sent`} style=${{ flex: 1, minWidth: 0, display: 'flex',
          flexDirection: 'column-reverse', height: total ? `${Math.max(3, (total / max) * 100)}%` : '2px', borderRadius: 2, overflow: 'hidden',
          background: total ? 'transparent' : 'var(--track)' }}>
          ${h.steps.map((n, i) => html`<div style=${{ height: `${total ? (n / total) * 100 : 0}%`, background: STEP_COLORS[i] }}></div>`)}
        </div>`;
      })}
    </div>
    <div style=${{ display: 'flex', gap: 3, marginTop: 6 }}>${d.hours.map(h => html`
      <span class="mono mute2" style=${{ flex: 1, textAlign: 'center', fontSize: 10 }}>${h.hour % 3 === 0 ? String(h.hour).padStart(2, '0') : ''}</span>`)}</div>
  <//>`;
}

function Deferred({ d }) {
  const max = Math.max(...d.deferred.map(x => x.count), 1);
  return html`<${Card} title=${html`Deferred by reason <span class="mute2" style=${{ fontWeight: 400 }}>· today</span>`}>
    <div class="rows" style=${{ gap: 12 }}>${d.deferred.map(x => html`<div>
      <div class="row"><span class="ink2">${x.label}</span><span class="n">${fmt(x.count)}</span></div>
      <div class="track" style=${{ height: 5, marginTop: 4 }}><div style=${{ width: `${(x.count / max) * 100}%`, background: 'var(--mute2)' }}></div></div>
    </div>`)}</div>
    <div class="note" style=${{ marginTop: 12 }}>Counted once per contact, day and reason. Deferred contacts keep their place.</div>
  <//>`;
}

function Health({ d }) {
  const { act } = useApp();
  const v = d.health.verify;
  const failed = v ? v.checks.filter(c => !c.ok) : [];
  return html`<${Card} title="System health">
    <div class="rows" style=${{ gap: 12 }}>
      <div><div style=${{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span class="dot" style=${{ width: 8, height: 8, background: !v ? 'var(--mute2)' : v.ok ? 'var(--accent)' : 'var(--red)' }}></span>
          <span style=${{ fontWeight: 500 }}>Last verify run: ${!v ? 'not run yet' : v.ok ? 'Pass' : 'Fail'}</span></div>
        <div class="mute" style=${{ fontSize: 12, marginTop: 4, lineHeight: 1.4 }}>
          ${!v ? 'Checks every invariant: status vs history, no double sends, suppression, waits, limits.'
            : v.ok ? `${v.checks.length} of ${v.checks.length} checks passed`
            : `Failed: ${failed.map(c => c.name).join('; ')}. ${v.checks.length - failed.length} of ${v.checks.length} passed.`}</div></div>
      <button class="btn" style=${{ alignSelf: 'flex-start' }} onClick=${() => act(() => post('verify'))}>Run verify</button>
      <div class="row"><span class="ink2">Last cycle duration</span><span class="n">${d.cycle ? `${(d.cycle.duration_ms / 1000).toFixed(2)}s` : '—'}</span></div>
      <div class="row"><span class="ink2">Content-check blocks</span><span class="n">${fmt(d.health.content_blocks)}</span></div>
      <div class="row"><span class="ink2">Last cycle</span><span class="n">${d.cycle?.at_ist || '—'}</span></div>
    </div><//>`;
}

function Campaign({ c }) {
  const rates = c.mock_rates;
  const rows = [
    ['Waits', `${c.waits_days.slice(0, 3).join(' / ')} days, then ${c.waits_days[3]} days to deactivation`],
    ['Sending window', c.window], ['Recipient domain cap', `${c.domain_daily_cap} per day`],
    ['Mailboxes', `${c.mailboxes} · warm-up ${c.warmup}`], ['Auto-pause', c.autopause],
    ['Mock provider', `hard ${pct(rates.hard_bounce)} · soft ${pct(rates.soft_bounce)} · complaint ${pct(rates.complaint)} · ` +
      `reply ${rates.reply_by_step.map(r => pct(r, 0)).join('/')}`],
  ];
  return html`<${Card} title=${`Campaign settings · ${c.name}`}>
    <div class="rows" style=${{ gap: 8 }}>${rows.map(([k, v]) => html`<div class="kv"><span class="mute2">${k}</span><span class="ink2">${v}</span></div>`)}</div>
  <//>`;
}

export function Dashboard({ overview }) {
  const { range } = useApp();
  return html`<div class="stack">
    <h1>Campaign overview</h1>
    <${StateBox} q=${overview}>${d => html`
      <${Kpis} k=${d.kpis} range=${d.range} />
      <div class="grid g-32"><${Funnel} d=${d} /><${Replies} d=${d} /></div>
      <div class="grid g-32"><${Hours} d=${d} /><${Deferred} d=${d} /></div>
      <div class="grid g-3">
        <${Card} title="Next actions due"><div class="rows">${d.due.map(x => html`<div class="row"><span>${x.label}</span>
          <span class="n">${fmt(x.today)} today · ${fmt(x.tomorrow)} tmrw</span></div>`)}</div><//>
        <${Card} title="Suppression list"><div class="rows" style=${{ gap: 9 }}>
          <div class="row"><span>Total suppressed</span><span class="n">${fmt(d.suppression.total)}</span></div>
          ${d.suppression.by_reason.map(x => html`<div class="row"><span class="ink2">${x.label}</span><span class="n">${fmt(x.count)}</span></div>`)}
          <div class="row"><span class="ink2">Added today</span><span class="n">${fmt(d.suppression.added_today)}</span></div>
          <div class="row"><span class="ink2">List-Unsubscribe header coverage</span><span class="n">${pct(d.suppression.list_unsubscribe_coverage, 0)}</span></div>
        </div><//>
        <${Health} d=${d} />
      </div>
      <${Campaign} c=${d.campaign} />`}<//>
  </div>`;
}
