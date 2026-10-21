# coldemailer — design

Date: 2026-10-21 · Status: draft for review

## 1. Intent

A take-home for MnemioAI (AI Automation Engineer): a cold-email engine that runs a
4-step sequence (first email + 3 follow-ups) over 10,000+ prospects and **never loses
track of a contact**. At any moment it must answer: who was emailed, who replied,
who bounced, who unsubscribed, what is due next, and who must never be contacted again.
Email sending is mocked; AI (OpenRouter `:free` models) writes emails and classifies replies.

Graders clone the repo, follow the README on a clean machine, seed 10k contacts, run the
workflow, and check: lifecycle correctness, **re-runs never double-send**, complete tracking,
README clarity, engineering judgement (scale, rate limits, failures, data model), and bonus
items (warm-up, spam management, dashboard, tests). The README matters as much as the code.

## 2. Goals, non-goals, anti-goals

**Goals**
- Correct lifecycle: `new → first_sent → follow_up_1_sent → follow_up_2_sent → follow_up_3_sent → deactivated`, with `replied` / `bounced` / `unsubscribed` as terminal exits from any step.
- Exactly-once per (contact, step), provable from the schema and demonstrated by tests.
- A controllable simulated clock: weeks of campaign in minutes.
- Works at 10k and visibly beyond (README reports a 10k run and a 100k sanity run).
- AI used where it adds value, designed around free-tier rate limits; system fully works with **no API key** (fallback templates + rule classifier).
- All bonus items: mailbox warm-up, spreading sends, auto-pause on bounce/complaint rate, suppression list, unsubscribe link + `List-Unsubscribe` header, content checks, prospect-timezone sending windows, per-recipient-domain limits.
- A FastAPI layer the user's own HTML frontend (supplied later) will call.

**Non-goals**
- Real SMTP/IMAP, real deliverability, multi-tenant, auth.
- Multiple concurrent campaigns (one campaign row; schema leaves room).
- Distributed workers. Single writer process is the documented design.

**Anti-goals** (failure even if it "works")
- Any contact receiving the same step twice, or any send to a suppressed/terminal contact.
- AI on the critical send path (a 429 must never stall sending).
- A hand-rolled storage engine. SQLite's B-trees + well-chosen indexes are the "traversal".
- State that can drift silently between the contact row and its history.

## 3. Constraints

- Python 3.12, venv at `coldemailer/.venv` (created with `py -3.12`; `python` on the dev machine is the Windows Store alias). Windows dev machine; must also run on macOS/Linux.
- SQLite (stdlib `sqlite3`, 3.45), WAL mode, single file `data/coldemailer.db`.
- Secrets in `.env`; commit only `.env.example`. `.venv`, `data/*.db*`, `.env` gitignored (`data/templates.seed.json` is committed).
- Deterministic: fixed random seed for seed data **and** for the mock provider, so the same commands produce the same final counts.
- User has asked: no commits until they say so.

## 4. Decisions made with the user

- **Stack:** Python + SQLite + FastAPI. No n8n/CRM. Reason for README: correctness guarantees (unique constraints, transactions) live in one place we control; zero infra for graders.
- **Traversal = indexes.** Due work found by an index range scan on `next_action_at`; no custom B+tree.
- **Source of truth:** contact row holds current state (indexable); append-only `events` log holds history; both written in the same transaction. Current state must be re-derivable from events via SQL, and a `verify` command checks this.
- **Double-send protection:** `UNIQUE(contact_id, step)` on sends + per-contact claim lease + idempotency key to the mock provider + re-check of status/suppression inside the send transaction.
- **Locking:** every cycle/advance runs under `BEGIN IMMEDIATE`-guarded write sections; contacts claimed via `UPDATE … RETURNING` with `claimed_by`/`claim_expires_at` lease.
- **Clock:** virtual clock in DB; monotonic (backwards rejected); one advance at a time; nothing reads wall-clock time.
- **Time zones:** store UTC; display and report in **IST** ("today" = IST calendar day). Prospect sending windows computed per-contact with `zoneinfo`.
- **AI strategy:** segment templates (cached), rules-first reply classification with batched LLM for the ambiguous rest, retries on 429 with backoff → model fallback chain → static fallback.
- **Spreading sends:** (a) jitter at scheduling time — `next_action_at` = due date + random time inside the prospect's local window; (b) per-mailbox pacing at send time — min gap ≈ window length ÷ today's warm-up limit.
- **Seed data:** Faker with fixed seed; history consistent with status and configured waits.
- **All bonus items in scope.**

## 5. Architecture

```
coldemailer/
  pyproject.toml / requirements.txt
  .env.example
  src/coldemailer/
    config.py        # settings from .env + defaults (waits, rates, limits)
    schema.sql       # DDL + indexes
    db.py            # connection (WAL, foreign_keys, busy_timeout), tx helpers
    clock.py         # virtual clock read/advance (monotonic, locked)
    seed.py          # Faker-based deterministic seed with consistent history
    lifecycle.py     # state machine: allowed transitions, next step, schedule fn
    policies.py      # sending window, warm-up limit, pacing, domain cap, content checks, auto-pause
    engine.py        # the cycle (§7)
    provider_mock.py # mock ESP: accept/bounce, idempotency, schedules inbound events
    ai/openrouter.py # client, rate limiter, retries, model chain, call log
    ai/templates.py  # segment template generation + cache + static fallbacks
    ai/classify.py   # rules + batched LLM classification
    report.py        # report queries (shared by CLI and API)
    verify.py        # invariant checks
    api.py           # FastAPI app; serves web/ statically
    cli.py           # entry point: python -m coldemailer <cmd>
  web/               # user's HTML frontend (later)
  tests/
  docs/
```

Single process does all writes. The API server and CLI share the same modules; API write
endpoints (advance, run cycle) take the same lock as the CLI.

## 6. Data model (SQLite)

All timestamps are UTC ISO-8601 text (`YYYY-MM-DDTHH:MM:SSZ`) so lexical order = time order.

- **clock**(`id=1`, `now`, `updated_at`)
- **campaign**(`id`, `name`, `waits_days` JSON e.g. `[3,5,7,7]`, `window_start` `09:00`, `window_end` `18:00`, `domain_daily_cap`) — one row.
  `waits_days[i]` = wait after step *i* (0 = first email). The 4th wait is the post-FU3 wait before deactivation.
- **contacts**(`id`, `email` UNIQUE, `first_name`, `last_name`, `company`, `domain`, `role`, `industry`, `segment`, `timezone`, `status`, `step` 0–4 = number of steps sent, `next_action` (`send_step_N` | `deactivate` | NULL), `next_action_at`, `mailbox_id` (sticky: follow-ups come from the same mailbox), `claimed_by`, `claim_expires_at`, `created_at`, `updated_at`)
- **sends**(`id`, `contact_id`, `step`, `mailbox_id`, `template_id`, `subject`, `body`, `headers` JSON (incl. `List-Unsubscribe`), `idempotency_key` UNIQUE, `state` (`claimed` | `sent` | `bounced` | `blocked`), `claimed_at`, `sent_at`, `provider_message_id`; **UNIQUE(contact_id, step)**)
- **events**(`id`, `contact_id`, `send_id`, `type` (`sent`, `bounced`, `replied`, `unsubscribed`, `complaint`, `deactivated`, `blocked`, `deferred`), `detail` JSON, `occurred_at`) — append-only, never updated.
- **replies**(`id`, `contact_id`, `send_id`, `body`, `received_at`, `classification` (`interested` | `not_interested` | `out_of_office` | `unsubscribe` | NULL), `classified_by` (`rule` | `llm:<model>` | `fallback`), `classified_at`)
- **suppression**(`email` PK, `reason`, `source_event_id`, `added_at`)
- **mailboxes**(`id`, `address`, `warmup_started_on`, `start_limit`, `daily_increment`, `max_limit`, `paused`, `paused_reason`, `paused_at`, `last_sent_at`)
- **mailbox_daily**(`mailbox_id`, `day` (IST date), `sent`, `bounced`, `complaints`; PK(mailbox_id, day))
- **domain_daily**(`domain`, `day`, `sent`; PK)
- **templates**(`id`, `segment`, `step`, `variant`, `subject`, `body`, `source` (`llm:<model>` | `static`), `created_at`; UNIQUE(segment, step, variant))
- **provider_outbox** (mock ESP's own state): accepted messages keyed by `idempotency_key`, and **provider_inbound**(`id`, `send_id`, `kind`, `body`, `deliver_at`, `processed_at`) = simulated future bounces/replies/unsubscribes/complaints.
- **llm_calls**(`id`, `model`, `purpose`, `status`, `http_status`, `latency_ms`, `retry_of`, `at`) — for the README's rate-limit story and the report.

**Indexes**
- `contacts(next_action_at) WHERE next_action IS NOT NULL` — partial; terminal contacts are not in it. The due-work scan.
- `contacts(status)` — report counts.
- `contacts(domain)`, `contacts(email)` unique.
- `sends(sent_at)`, `sends(state) WHERE state='claimed'` (crash recovery).
- `events(type, occurred_at)`, `events(contact_id, occurred_at)` (timeline).
- `provider_inbound(deliver_at) WHERE processed_at IS NULL`.
- `replies(classification) WHERE classification IS NULL` (classification backlog).

## 7. The cycle

`run_cycle(window_start, window_end)` processes the simulated interval `(prev_now, now]`.
`advance --days D --step 1h` moves the clock in steps and runs a cycle per step.

1. **Recover.** `sends` stuck in `claimed` with expired lease: ask the mock provider by `idempotency_key` whether it accepted. Accepted → finalize as sent (step 4c below). Not accepted → release claim, contact stays due.
2. **Ingest inbound.** Apply `provider_inbound` rows with `deliver_at <= now` in time order:
   - bounce → send `bounced`, contact `bounced`, suppression add, `next_action=NULL`.
   - reply → `replies` row, contact `replied`, `next_action=NULL`. If rules detect unsubscribe intent → `unsubscribed` + suppression instead.
   - unsubscribe (link click) → `unsubscribed` + suppression.
   - complaint → counted on mailbox; contact suppressed.
   Each is idempotent (guarded by `processed_at` and by status transitions that only move forward).
3. **Select due.** Keyset scan: `WHERE next_action IS NOT NULL AND next_action_at <= :now ORDER BY next_action_at, id LIMIT :batch` (repeat with `(next_action_at,id) > last`).
   - `deactivate` actions → contact `deactivated`, event, `next_action=NULL`.
4. **For each `send_step_N` candidate, gate then send:**
   a. **Gates** (fail → leave due, write a `deferred` event at most once per contact per day with the reason): prospect local time inside window; domain under `domain_daily_cap` today; contact's mailbox not paused, under today's warm-up limit, and pacing gap elapsed. If the sticky mailbox is paused, reassign to another active mailbox (my call).
   b. **Claim** in a short `BEGIN IMMEDIATE` tx: `UPDATE contacts SET claimed_by, claim_expires_at WHERE id=? AND status=<expected> AND step=N-1 AND (claim_expires_at IS NULL OR claim_expires_at < now) RETURNING …`; re-check suppression; render email (template + merge fields + unsubscribe link/header); run content checks; insert `sends` row `state='claimed'` with `idempotency_key = sha256(contact_id:step:campaign_id)`. UNIQUE violation ⇒ already sent ⇒ skip.
   c. **Send** to mock provider with the key (outside the tx). Provider is idempotent on the key.
   d. **Finalize** tx: send `sent` with `sent_at` (a paced timestamp inside the interval, not "now"), contact `status`/`step` advanced, `next_action_at` computed by the shared schedule function, claim cleared, `sent` event, mailbox/domain counters incremented.
5. **Auto-pause check.** For each mailbox, rolling bounce rate over last N sends (default 50, min sample 20) > 5% or complaint rate > 0.3% ⇒ pause + event. Resume is manual (CLI/API) (my call).
6. **Classify backlog.** Unclassified replies → classifier (§9). Never blocks steps 1–5; a cycle-level time/call budget caps it.

**Schedule function** (shared by engine and seed, so seed and runtime agree):
`next_action_at = local_date(sent_at) + waits_days[step]` at a seeded-random time inside the prospect's local window, converted to UTC. For step 4 the action is `deactivate` (time of day irrelevant; use end of wait).

## 8. Mock provider

- Accept/bounce at configurable rates (defaults, my call): hard bounce 4%, complaint 0.1%.
- For accepted sends, may schedule inbound events with delays (seeded RNG keyed on idempotency key → deterministic regardless of run order): reply probability per step (default 6%/5%/4%/3%), reply kind mix interested 30% / not interested 30% / out of office 25% / unsubscribe 15%; link-click unsubscribe 0.5%. Bounces arrive within minutes; replies 2 hours–4 days later.
- Reply bodies drawn from a built-in corpus of realistic variants per kind (some deliberately ambiguous, so the LLM path is exercised).
- Mailboxes with high-bounce domains: seed includes a few "bad" domains with high bounce rates so auto-pause is demonstrable.

## 9. AI (OpenRouter free models)

- Client: `httpx`, OpenAI-compatible chat endpoint. Model chain from `.env` (`OPENROUTER_MODELS=a:free,b:free,…`); builder verifies currently available `:free` IDs.
- **Rate limiting:** in-process token bucket (default 15 req/min) + persisted daily counter; on 429 honour `Retry-After` else exponential backoff with jitter (max 3 tries) → next model in chain → per-model cool-down → static fallback. Every call logged in `llm_calls`.
- **Templates:** `python -m coldemailer templates warm` generates `segment × step × variants` (segments ≈ 6 role groups × 5 industries = 30; × 4 steps × 2 variants = 240 calls, ~16 min at 15 rpm; resumable, cached). Templates use merge fields (`{first_name}`, `{company}`, `{role}`…) validated before caching. Missing templates fall back to static per-step templates, so the cycle never waits on AI.
- **Classification:** rules first (unsubscribe/OOO patterns, strong keywords); remaining replies batched (≈10 per call) with a JSON-only response, schema-validated; invalid/failed → keyword fallback labelled `fallback`.
- No API key ⇒ everything runs on static templates + rules/keyword classifier; README says so.

## 10. Seed

`python -m coldemailer seed --n 10000 --seed 42` (wipes and recreates DB).
- Faker (fixed seed) for names/companies; role & industry from fixed lists → segment; timezone from a weighted list of IANA zones (India, US, UK, EU, APAC); email domain derived from company (several contacts per domain so domain caps matter).
- Clock starts at a fixed instant (e.g. 2026-10-01 09:00 IST, my call).
- Status distribution (my call, configurable): new 40%, first_sent 15%, fu1 10%, fu2 8%, fu3 7%, replied 8%, bounced 4%, unsubscribed 3%, deactivated 5%.
- History generated **backwards from the clock** using the same waits and schedule function: each prior step has a `sends` row + `sent` event; terminal statuses have their terminating event after the last send; deactivated contacts have all 4 sends and the final wait elapsed. `next_action_at` for active contacts computed by the shared function (some already overdue, so the first cycle has work).
- 10 mailboxes with staggered `warmup_started_on` so the warm-up view shows mixed ramps; seeded `mailbox_daily` consistent with seeded sends.
- Bulk insert in one transaction; `verify` must pass immediately after seeding.

## 11. Reporting & verify

`python -m coldemailer report` and `GET /api/report` (same function). IST day boundaries.
- counts by status; sent today; due today / due tomorrow (by `next_action_at`, split send vs deactivate); reply rate (replied contacts ÷ contacts with ≥1 delivered send); bounce rate (bounced sends ÷ sends); unsubscribed; deactivated; reply classification breakdown; per-mailbox warm-up limit / used today / paused; LLM calls, 429s, fallbacks.

`python -m coldemailer verify` — invariants: status ⇔ derived-from-events; step == count of sent sends; no contact has duplicate step; no send to suppressed email after suppression time; terminal contacts have `next_action IS NULL`; sends ordered and spaced ≥ configured waits.

## 12. API (FastAPI) for the frontend

Read: `GET /api/report`, `/api/contacts?status=&q=&after=` (keyset paging), `/api/contacts/{id}` (row + timeline), `/api/mailboxes`, `/api/clock`, `/api/templates`, `/api/suppression`.
Write: `POST /api/clock/advance {hours|days}`, `POST /api/cycle/run`, `POST /api/mailboxes/{id}/resume`, `POST /api/seed` (dev only).
Serves `web/` statically. The user's HTML will be wired to these later; endpoint shapes may adjust to it.

## 12a. Dashboard (user's mockup)

Source: `web/mockup/outreach-dashboard-standalone.html`, a self-unpacking bundle from a design tool (React 18.3.1 + a generated "dc-runtime" template engine, fonts inlined). Everything it shows is hardcoded sample data; it can fetch `data/dashboard.json` and, if present, replaces `statuses`, `kpis`, `replies`, `due`, `suppression`, `mailboxes`, `logs`.

**Screens:** header (sim-clock popover with +1h/+6h/+1d/+7d, date-range picker with presets, theme toggle); **Dashboard** tab (6 KPI cards, lifecycle funnel, reply classification, next actions due today/tomorrow per step, suppression list, mailbox warm-up table); **Logs** tab (event table with type filter chips, search, live tail); right **drawer** for a KPI/status (description, breakdown, contact list) and for a single contact (facts + history).

**Plan:** port to a page we own — `web/index.html` + `web/app.js` using React 18.3.1 from a pinned CDN (same as the mockup), no build step, same layout/tokens/fonts — served by FastAPI. Reason: the dc-runtime is generated, opaque code we can't explain in the interview, and the hardcoded parts (drawer, contact history, clock) need real API calls, not just a JSON swap. The mockup file stays in the repo as the design reference.

**Gaps to close while wiring (raised with the user):**
1. Sim clock is cosmetic and starts at a hardcoded date → read `/api/clock`, buttons call `POST /api/clock/advance` (runs cycles), then refetch. "Reset to real time" contradicts the monotonic clock → replace with "Run cycle now" (and a reseed action in dev).
2. Drawer contents (descriptions, breakdowns, contact lists) are hardcoded → `/api/report` breakdowns + `/api/contacts?status=…` with keyset "load more".
3. Contact history is synthesized (fake 4-day spacing, fixed mailbox) → `/api/contacts/{id}` timeline; add mailbox, next-action date/time (IST and prospect local), suppression flag/reason, reply text + classification + who classified it, and each email's subject/body (to show the AI-written content).
4. No way to find an arbitrary contact; Logs search filters only the 80 rows in memory → server-side contact search by email/company (header search or a Contacts view).
5. Logs filtered client-side; live tail is a fake counter → `/api/events?type=&q=&from=&to=&after=` with paging; live tail polls. Missing event types: Deactivated, Out of office, Deferred (window / domain cap / mailbox capacity), Complaint, Content blocked, Mailbox resumed, Classified.
6. Status semantics differ from our rules: "Replied" breakdown includes Out of office and Unsubscribe request (in our design OOO doesn't stop and unsubscribe replies become `unsubscribed`); "Soft bounce" exists (we only model hard bounces → drop it, or add soft bounce = retry later). Funnel bar scale hardcoded to 2,610.
7. Mailboxes: no resume action for a paused mailbox, no complaint-rate column, and no actual **warm-up schedule** (brief asks for "a clear view of each mailbox's warm-up schedule") → add ramp (today / tomorrow / max, small ramp chart) + resume button.
8. Missing panels that prove bonus/AI work: sends-per-hour histogram (proves "spread across the day"), AI usage (calls, 429s, retries, fallbacks, cache hits, models), templates viewer, deferred counts by reason (window / domain cap / capacity), content-check blocks, `verify` status.
9. Due panel has no "deactivate" row; report splits sends vs deactivations.
10. Date range: KPIs "in range" multiply sample numbers; range is relative to a fixed real date. Range must be in simulated IST days and passed to the API (status counts stay point-in-time; sent/replies/bounces are per range).
11. Practical: no loading/error states, no refresh after actions, `<title>` is "Bundled Page", fixed grids aren't responsive, `fetch` from `file://` fails (must be served by FastAPI).

## 13. CLI summary

`seed`, `templates warm`, `run` (one cycle at current clock), `advance --days N [--step 1h]`, `report`, `verify`, `contact <email>`, `mailbox resume <id>`, `serve`.
README quickstart = venv + install + `cp .env.example .env` + seed + advance + report.

## 14. Testing

pytest, on small seeds and in-memory/temp DBs:
- run cycle twice at same clock ⇒ zero new sends.
- crash after provider accept, before finalize ⇒ recovery finalizes, no resend.
- reply ingested between select and claim ⇒ no send.
- lifecycle transitions incl. deactivation timing; suppression is permanent.
- clock rejects backwards; seed is deterministic (same seed ⇒ same hash of tables); `verify` passes after seed and after a multi-week advance.
- policies: window across time zones, warm-up ramp, pacing, domain cap, auto-pause.
- LLM client: 429 → retry → fallback model → static (mocked HTTP).

## 15. Scale & results

README reports wall time for seed, a full-campaign advance (e.g. 45 simulated days) on 10k, final counts, and a 100k sanity run. Throughput is bounded by warm-up limits by design (10 mailboxes ramping to e.g. 300/day), which the README explains.

## 15a. Calls settled after builder review (my calls — check these)

- **Inbound → status mapping.** bounce → `bounced`; link-click unsubscribe → `unsubscribed`; complaint → `unsubscribed` with suppression reason `complaint` (no extra status); reply → `replied` at ingest (stops sequence immediately, classification later). If classification (rule or LLM) later says `unsubscribe`, contact moves `replied → unsubscribed` and is suppressed — the only allowed post-terminal transition. `not_interested` → suppressed (reason `not_interested`) but status stays `replied`.
- **Out-of-office (user decision):** an OOO auto-reply does **not** stop the sequence. It is recorded in `replies` (classification `out_of_office`) with an `ooo` event; status and step are unchanged; `next_action_at` is pushed back (parsed return date if the body has one, else +3 days, capped at +14; still inside the prospect's window). Because whether a reply stops the sequence must be decided at ingest, the **rules classifier runs synchronously at ingest**; only replies the rules can't settle go to the LLM backlog, and those are treated as `replied` (stopping is the safe side). If the LLM later labels such a reply OOO, it stays stopped — no backward transitions. Mock OOO bodies are written as clear auto-replies so the rules catch them. README calls out this deliberate deviation from the brief's literal "a reply stops the sequence".
- **Determinism with a key.** Classification never changes *whether* sends happen (the sequence already stopped at ingest); it can only add suppression / upgrade to `unsubscribed`. Mock reply kinds are recorded as ground truth, so the README can show classifier accuracy.
- **Capacity & priority.** Mailbox count and ramp are sized so the 10k seed **finishes its campaign** in the README's standard advance (target ≤ 60 simulated days; roughly 20 mailboxes ramping 20 → +10/day → 200/day — tune once measured). When capacity is short, due follow-ups go before first emails (oldest `next_action_at` first within each).
- **Seeded history = imported legacy data.** Seeded sends are marked `source='import'` (the brief's "today this lives in spreadsheets"). They obey lifecycle spacing and prospect windows but not warm-up/domain/pacing limits; `mailbox_daily` counts only engine sends. `verify` checks lifecycle invariants on everything and sending policies only on engine sends. Mailboxes start warm-up at the clock start (some staggered earlier).
- **Committed template cache.** Templates are generated once with a real key and committed as `data/templates.seed.json` (each tagged with its model); seed loads them. Graders without a key still see AI-written emails; `templates warm` regenerates.
- **Frontend.** Superseded by §12a — the user supplied a dashboard mockup.
- **Commits.** Checkpoint commits after each working piece, in order: plan + mockup → core engine → 10k run + report → bonus policies → dashboard wiring → README. If time runs short, bonus items are cut first. Deadline: 5 days from receipt of the brief (exact date to confirm).

## 16. Left to the builder

Module internals, exact default numbers marked "my call", CLI library (argparse vs typer), Docker Compose (nice-to-have, after core), exact content-check rules (spam word list, link count, unrendered placeholder detection), README prose.

## 17. Open

- Exact submission deadline date.
- User's call on dashboard gaps (§12a), especially #6 soft bounces and the extra panels in #8.
