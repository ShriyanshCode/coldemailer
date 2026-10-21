// App shell: header (clock, search, range, theme), tabs, drawer host, toasts.
import { get, post, pollJob } from './api.js';
import { html, useState, useEffect, useRef, AppContext, useApp, useFetch, fmt, timeOf } from './ui.js';
import { Drawer } from './drawer.js';
import { Dashboard } from './tabs/dashboard.js';
import { Contacts } from './tabs/contacts.js';
import { Review } from './tabs/review.js';
import { Mailboxes } from './tabs/mailboxes.js';
import { AiTab } from './tabs/ai.js';
import { Logs } from './tabs/logs.js';

const TABS = [['dash', 'Dashboard'], ['contacts', 'Contacts'], ['review', 'Review'], ['mail', 'Mailboxes'],
  ['ai', 'AI & Templates'], ['logs', 'Logs']];
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const shiftDay = (day, n) => {
  const d = new Date(`${day}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};
const dayLabel = day => { const d = new Date(`${day}T00:00:00Z`); return `${MON[d.getUTCMonth()]} ${d.getUTCDate()}`; };

function ClockMenu({ clock, overview }) {
  const { job, advance } = useApp();
  const [open, setOpen] = useState(false);
  const cy = overview?.cycle;
  const deferred = cy ? Object.values(cy.deferred || {}).reduce((a, b) => a + b, 0) : 0;
  const adv = clock?.advanced_hours || 0;
  const d = Math.floor(adv / 24), h = Math.round(adv % 24);
  const progress = job && (job.hours >= 24
    ? `day ${Math.max(1, Math.ceil(job.progress / 24))} of ${Math.ceil(job.total / 24)}`
    : `cycle ${job.progress} of ${job.total}`);
  return html`<div class="pop-wrap">
    <button class="btn" title="Simulated clock" onClick=${() => setOpen(!open)}
      style=${{ borderColor: adv ? 'var(--accent)' : 'var(--line2)' }}>
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
        <circle cx="8" cy="8" r="6.25"></circle><path d="M8 4.5V8l2.25 1.5"></path></svg>
      <span class="mono mute">${clock?.now_ist ? `${timeOf(clock.now_ist)} IST` : '--:--'}</span>
      ${job && html`<span class="dot sm pulse"></span>`}
    </button>
    ${open && html`<div class="scrim" onClick=${() => setOpen(false)}></div>
      <div class="pop" style=${{ left: 0, width: 300 }}>
        <div><div class="mute" style=${{ fontSize: 12 }}>Simulated time</div>
          <div class="mono" style=${{ fontSize: 20, marginTop: 4 }}>${clock?.now_ist} IST</div>
          <div class="mute2" style=${{ fontSize: 12, marginTop: 2 }}>
            ${adv ? `Advanced ${d ? `${d} day${d > 1 ? 's' : ''} ` : ''}${h ? `${h} h` : ''}` : 'Start of simulation'}</div></div>
        <div><div class="mute" style=${{ fontSize: 12, marginBottom: 6 }}>Move forward</div>
          <div class="grid" style=${{ gridTemplateColumns: 'repeat(4,1fr)', gap: 6 }}>
            ${[['+1h', 1], ['+6h', 6], ['+1d', 24], ['+7d', 168]].map(([l, n]) => html`
              <button class="btn" style=${{ justifyContent: 'center' }} disabled=${!!job} onClick=${() => advance(n)}>${l}</button>`)}
          </div></div>
        <button class="btn primary" style=${{ padding: '9px 0', fontSize: 13 }} disabled=${!!job} onClick=${() => advance(0)}>
          ${job ? `Running… ${progress}` : 'Run cycle now'}</button>
        <div style=${{ fontSize: 12, borderTop: '1px solid var(--row)', paddingTop: 12 }}>
          <div class="mute2">Last cycle · ${cy?.at_ist || '—'}</div>
          <div class="mono ink2" style=${{ marginTop: 4 }}>
            ${cy ? `sent ${fmt(cy.sent)} · deferred ${fmt(deferred)} · replies ${fmt(cy.replies)}` : 'no cycle run yet'}</div>
        </div>
      </div>`}
  </div>`;
}

function Search() {
  const { openContact } = useApp();
  const [q, setQ] = useState('');
  const [res, setRes] = useState(null);
  useEffect(() => {
    if (q.trim().length < 2) { setRes(null); return; }
    const t = setTimeout(() => get('search', { q }).then(setRes).catch(() => setRes([])), 200);
    return () => clearTimeout(t);
  }, [q]);
  return html`<div class="search">
    <input value=${q} onInput=${e => setQ(e.target.value)} placeholder="Search email, name or company" />
    ${res && html`<div class="scrim" onClick=${() => setQ('')}></div>
      <div class="results">
        ${res.map(r => html`<div class="r" onClick=${() => { openContact(r.id); setQ(''); }}>
          <div>${r.name} <span class="mute2" style=${{ fontSize: 12 }}>· ${r.company}</span></div>
          <div class="mono mute" style=${{ fontSize: 11 }}>${r.email}</div></div>`)}
        ${!res.length && html`<div class="mute2" style=${{ padding: 14, textAlign: 'center' }}>No matches</div>`}
      </div>`}
  </div>`;
}

function RangePicker({ today }) {
  const { range, setRange } = useApp();
  const [open, setOpen] = useState(false);
  const [month, setMonth] = useState(0);
  const [picking, setPicking] = useState(null);
  if (!today) return null;
  const label = range.from === range.to ? dayLabel(range.from) : `${dayLabel(range.from)} – ${dayLabel(range.to)}`;
  const base = new Date(`${today}T00:00:00Z`);
  const first = new Date(Date.UTC(base.getUTCFullYear(), base.getUTCMonth() + month, 1));
  const days = new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth() + 1, 0)).getUTCDate();
  const cells = [...Array(first.getUTCDay()).fill(null), ...Array.from({ length: days }, (_, i) =>
    new Date(Date.UTC(first.getUTCFullYear(), first.getUTCMonth(), i + 1)).toISOString().slice(0, 10))];
  const pick = day => {
    if (day > today) return;
    if (!picking) { setPicking(day); setRange({ from: day, to: day }); }
    else { setRange({ from: day < picking ? day : picking, to: day < picking ? picking : day }); setPicking(null); setOpen(false); }
  };
  const preset = n => ({ from: shiftDay(today, -(n - 1)), to: today, preset: n });
  return html`<div class="pop-wrap">
    <button class="btn" onClick=${() => { setOpen(!open); setPicking(null); }}>
      <span class="mute">Sim days</span><span class="mono">${label}</span><span class="mute">▾</span></button>
    ${open && html`<div class="scrim" onClick=${() => setOpen(false)}></div>
      <div class="pop" style=${{ right: 0, width: 300, gap: 12, padding: 14 }}>
        <div class="mute" style=${{ fontSize: 12 }}>Simulated days, counted from the engine clock</div>
        <div style=${{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          ${[['Today', 1], ['Past 7 days', 7], ['Past 14 days', 14], ['Past 30 days', 30], ['Past 90 days', 90]].map(([l, n]) => html`
            <button class=${'pill' + (range.preset === n ? ' on' : '')} onClick=${() => { setRange(preset(n)); setOpen(false); }}>${l}</button>`)}
        </div>
        <div class="row" style=${{ alignItems: 'center' }}>
          <button class="x" style=${{ fontSize: 16 }} onClick=${() => setMonth(month - 1)}>‹</button>
          <span style=${{ fontWeight: 600 }}>${MON[first.getUTCMonth()]} ${first.getUTCFullYear()}</span>
          <button class="x" style=${{ fontSize: 16 }} onClick=${() => setMonth(Math.min(0, month + 1))}>›</button>
        </div>
        <div style=${{ display: 'grid', gridTemplateColumns: 'repeat(7,1fr)', gap: 2, textAlign: 'center' }}>
          ${['S', 'M', 'T', 'W', 'T', 'F', 'S'].map(w => html`<span class="mute2" style=${{ fontSize: 11, padding: '4px 0' }}>${w}</span>`)}
          ${cells.map(day => {
            if (!day) return html`<span></span>`;
            const end = day === range.from || day === range.to, inr = day >= range.from && day <= range.to, fut = day > today;
            return html`<button onClick=${() => pick(day)} style=${{ border: 0, fontSize: 12, height: 32, borderRadius: 6,
              cursor: fut ? 'default' : 'pointer', background: end ? 'var(--accent)' : inr ? 'var(--track)' : 'transparent',
              color: fut ? 'var(--line2)' : end ? 'var(--card)' : 'var(--ink)' }}>${Number(day.slice(8))}</button>`;
          })}
        </div>
        <div class="mute" style=${{ fontSize: 12 }}>${picking ? 'Select the end day' : 'Select a start day, then an end day'}</div>
      </div>`}
  </div>`;
}

function Shell() {
  const app = useApp();
  const clockQ = useFetch('clock');
  const clock = clockQ.data;
  const overview = useFetch('overview', { day_from: app.range.from, day_to: app.range.to }, !!app.range.from);
  const reviewCount = overview.data?.kpis?.needs_review || 0;

  useEffect(() => { // keep preset ranges anchored to the simulated "today"
    if (!clock?.today) return;
    const r = app.range;
    if (!r.from || (r.preset && r.to !== clock.today)) {
      const n = r.preset || 7;
      app.setRange({ from: shiftDay(clock.today, -(n - 1)), to: clock.today, preset: n });
    }
  }, [clock?.today]);
  useEffect(() => { if (app.job) clockQ.reload(); }, [app.job?.progress]);

  const T = { dash: Dashboard, contacts: Contacts, review: Review, mail: Mailboxes, ai: AiTab, logs: Logs }[app.tab];
  return html`
    <header class="top">
      <div class="bar">
        <div class="brand"><span class="dot"></span>Outreach Engine<${ClockMenu} clock=${clock} overview=${overview.data} /></div>
        <${Search} />
        <div style=${{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <${RangePicker} today=${clock?.today} />
          <button class="btn" onClick=${app.toggleTheme}>${app.theme === 'dark' ? 'Light' : 'Dark'}</button>
        </div>
      </div>
      <nav class="tabs">${TABS.map(([k, l]) => html`
        <button class=${app.tab === k ? 'on' : ''} onClick=${() => app.setTab(k)}>${l}
          ${k === 'review' && reviewCount > 0 && html`<span class="badge">${fmt(reviewCount)}</span>`}</button>`)}
      </nav>
    </header>
    <main>${clock?.initialised === false
      ? html`<div class="card state">No data yet. Run <span class="mono">python -m coldemailer seed</span>, then reload.</div>`
      : html`<${T} overview=${overview} clock=${clock} />`}</main>
    ${app.drawer && html`<${Drawer} />`}
    ${app.toastMsg && html`<div class="toast">${app.toastMsg}</div>`}`;
}

function App() {
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem('theme') || 'light'; } catch { return 'light'; } });
  const [tab, setTab] = useState('dash');
  const [rev, setRev] = useState(0);
  const [range, setRange] = useState({ from: null, to: null, preset: 7 });
  const [drawer, setDrawer] = useState(null);
  const [contactFilters, setContactFilters] = useState({});
  const [toastMsg, setToast] = useState(null);
  const [job, setJob] = useState(null);
  const toastTimer = useRef();

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('theme', theme); } catch { /* private mode */ }
  }, [theme]);

  const toast = msg => { setToast(msg); clearTimeout(toastTimer.current); toastTimer.current = setTimeout(() => setToast(null), 3500); };
  const bump = () => setRev(r => r + 1);
  const advance = async hours => {
    if (job) return;
    try {
      const { job_id } = await post('clock/advance', { hours });
      setJob({ progress: 0, total: Math.max(1, Math.ceil(hours)), hours });
      await pollJob(job_id, (progress, total) => setJob({ progress, total, hours }));
    } catch (e) {
      toast(e.status === 409 ? 'Another run is in progress' : e.message);
    } finally { setJob(null); bump(); }
  };
  const act = async (fn, okMsg) => {
    try { await fn(); if (okMsg) toast(okMsg); } catch (e) { toast(e.message); } finally { bump(); }
  };

  const value = {
    theme, toggleTheme: () => setTheme(t => (t === 'dark' ? 'light' : 'dark')), tab, setTab, rev, bump, range, setRange,
    drawer, job, advance, act, toastMsg, toast, contactFilters,
    openContact: id => setDrawer(d => ({ contact: id, list: d?.list || null })),
    openList: key => setDrawer({ list: key, contact: null }),
    backToList: () => setDrawer(d => ({ ...d, contact: null })),
    closeDrawer: () => setDrawer(null),
    showContacts: filters => { setContactFilters(filters); setTab('contacts'); },
  };
  return html`<${AppContext.Provider} value=${value}><${Shell} /><//>`;
}

ReactDOM.createRoot(document.getElementById('root')).render(html`<${App} />`);
