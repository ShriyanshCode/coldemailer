# coldemailer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A cold-email engine that runs a 4-step sequence over 10k+ contacts on a simulated clock, never double-sends, tracks every contact, and ships with a React dashboard.

**Architecture:** One SQLite file holds engine tables plus one `sim_events` table that simulates the outside world. A single worker (`run_cycle`) ingests simulated events, scans due contacts by index, gates them through sending policies, and claims → sends → finalizes each step. FastAPI exposes the same modules to a no-build React page.

**Tech Stack:** Python 3.12, stdlib `sqlite3` (WAL), FastAPI + uvicorn, httpx, Faker, python-dotenv, pytest; React 18.3.1 UMD + htm 3.1.1 (no Node needed).

**Spec:** `docs/superpowers/specs/2026-10-21-coldemailer-design.md`

**Phases (user's order):** 1 Backend (Tasks 1–12) → 2 Pytest suite (Tasks 13–16) → 3 React frontend (Tasks 17–21) → 4 Wiring, 10k run, README (Tasks 22–24).
Because tests come in Phase 2 (user's choice), every Phase 1 task ends with a CLI smoke check instead of a unit test.

## Global Constraints

- Python 3.12; venv at `.venv` (create with `py -3.12 -m venv .venv`; `python` on the dev box is the Store alias — use `.venv\Scripts\python.exe`). Must also run on macOS/Linux.
- Run everything from the repo root as `python -m coldemailer <cmd>`; package lives in `src/coldemailer`, installed with `pip install -e .`.
- Single DB file `data/coldemailer.db`; WAL; `foreign_keys=ON`; `busy_timeout=5000`.
- All stored timestamps are UTC text `YYYY-MM-DDTHH:MM:SSZ`. Reports and UI use IST (`Asia/Kolkata`); "today" = IST calendar day.
- Nothing reads wall-clock time for business logic — only `clock.now(conn)`.
- Deterministic: default seed `42`; every random draw comes from `random.Random(<stable string key>)` built via `hashlib.sha256`, never from global `random`.
- Secrets only in `.env`; commit `.env.example`. Gitignored: `.venv/`, `.env`, `data/*.db*`. `data/templates.seed.json` is committed.
- No real email ever leaves the process; the only "provider" is `provider_mock.py`.
- AI: OpenRouter model IDs ending in `:free` only; the system must fully work with no API key.
- Commits: one per task (user approved checkpoint commits). **Push only when the user asks.** Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Spec §3 "no commits" and §2 "user's own HTML" are superseded by later decisions (§15a commits, §12b mockup).

## Fixed values (from spec, used across tasks)

| Name | Value |
|---|---|
| waits_days | `[3, 5, 7, 7]` (after step 1,2,3,4; the 4th is the wait before deactivation) |
| sending window (prospect local) | `09:00`–`18:00` |
| clock start | `2026-10-01T03:30:00Z` (= 09:00 IST) |
| mailboxes | 20, warm-up start 20/day, +10/day, max 200/day; `warmup_started_on` staggered 0–20 days before clock start |
| domain_daily_cap | 50 sends per recipient domain per IST day |
| mock rates | hard bounce 4%, soft bounce 2% (of which ⅓ policy, ⅔ transient), complaint 0.1%, unsubscribe click 0.5%, reply per step `[0.06,0.05,0.04,0.03]`, reply mix interested 30 / not_interested 30 / out_of_office 25 / unsubscribe 15 |
| delays | bounce 1–10 min, reply 2 h–4 days, unsubscribe click 1 h–2 days, complaint 1 h–1 day |
| soft-bounce retry | 1 h → 4 h → 24 h; 3rd soft bounce on a step → review |
| auto-pause | over last 50 sends (min 20): bounce > 5% or complaint > 0.3% |
| claim lease | 300 s |
| OOO push | parsed return date, else +3 days; cap +14 days |
| seed status mix | new 40, first_sent 15, follow_up_1_sent 10, follow_up_2_sent 8, follow_up_3_sent 7, replied 8, bounced 4, unsubscribed 3, deactivated 5 (%) |
| segments (industries) | SaaS, Fintech, Retail, Healthcare, Logistics |
| LLM | 15 req/min token bucket; 3 tries per model; 429 honours `Retry-After`; per-model cool-down 60 s |

Step numbering used everywhere: **step 1 = first email, 2–4 = follow-ups 1–3**. `contacts.step` = number of steps sent (0–4). `next_action` ∈ `send_1`…`send_4`, `deactivate`, or NULL.
Status by step: 0 `new`, 1 `first_sent`, 2 `follow_up_1_sent`, 3 `follow_up_2_sent`, 4 `follow_up_3_sent`; terminal `replied`, `bounced`, `unsubscribed`, `deactivated`.

## Review Focus

1. **All mailboxes paused or at capacity** → due contacts stay due, a `deferred` event with reason `mailbox_paused`/`mailbox_capacity` is written at most once per contact per IST day, the cycle finishes normally. (Test in Task 14.)
2. **Two advances at once** (CLI while the API job runs, or a double-click) → the second is refused with "another run is in progress"; no interleaved cycles. (Test in Task 13.)
3. **Reply/unsubscribe arriving in the same cycle a follow-up is due** → ingest runs before the due scan, no send goes out. (Test in Task 13.)
4. **DST in a prospect's zone** (America/New_York, 2026-11-01) → window check uses `zoneinfo`; no send before 09:00 local on either side of the change. (Test in Task 14.)
5. **Running `run`/`advance`/`serve` before `seed`, or an empty DB** → CLI prints "database not initialised — run `python -m coldemailer seed`" and exits 1; API returns 200 with empty/zero payloads, not 500. (Tests in Tasks 13 and 16.)

---

# Phase 1 — Backend

### Task 1: Scaffold, config, schema, DB helpers

**Files:**
- Create: `pyproject.toml`, `requirements.txt`, `.env.example`, `src/coldemailer/__init__.py`, `src/coldemailer/__main__.py`, `src/coldemailer/config.py`, `src/coldemailer/db.py`, `src/coldemailer/schema.sql`, `src/coldemailer/timeutil.py`, `src/coldemailer/cli.py`
- Modify: `.gitignore` (already has `.venv/`, `.env`, `data/*.db*`)

**Interfaces:**
- Produces:
  - `config.Settings` (frozen dataclass, every field in the Fixed values table plus `db_path: str = "data/coldemailer.db"`, `seed: int = 42`, `batch_size: int = 500`, `openrouter_api_key: str | None`, `openrouter_models: list[str]`, `llm_rpm: int = 15`, `classify_calls_per_cycle: int = 3`, `campaign_id: int = 1`); `config.load_settings(**overrides) -> Settings` (reads `.env` via python-dotenv; overrides win — tests use this).
  - `db.connect(path: str) -> sqlite3.Connection` (autocommit `isolation_level=None`, `row_factory=sqlite3.Row`, pragmas above); `db.tx(conn, immediate: bool = True)` context manager (`BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK` on exception); `db.init_db(conn)` executes `schema.sql`; `db.is_initialised(conn) -> bool`; `db.NotInitialised(Exception)`.
  - `timeutil`: `IST`, `to_ts(dt) -> str`, `from_ts(s) -> datetime` (aware UTC), `add(ts, **timedelta_kwargs) -> str`, `ist_day(ts) -> str` (`YYYY-MM-DD`), `ist_day_bounds(day) -> tuple[str, str]` (UTC start/end), `local(ts, tz) -> datetime`, `in_window(ts, tz, start="09:00", end="18:00") -> bool`, `window_time_on(local_date, tz, minute_of_window: int, start, end) -> str` (UTC ts of a minute offset inside that local day's window).
  - `cli.main(argv=None)` using argparse subcommands; this task registers `init-db` only.

- [ ] **Step 1:** Write `pyproject.toml` (setuptools, `src` layout, name `coldemailer`, requires-python `>=3.12`) and `requirements.txt` pinning: `fastapi==0.115.*`, `uvicorn==0.32.*`, `httpx==0.27.*`, `faker==30.*`, `python-dotenv==1.0.*`, `pytest==8.*`, `tzdata` (Windows needs it for `zoneinfo`).
- [ ] **Step 2:** Write `.env.example`:
  ```
  OPENROUTER_API_KEY=
  OPENROUTER_MODELS=meta-llama/llama-3.3-70b-instruct:free,mistralai/mistral-small-3.1-24b-instruct:free,google/gemma-3-27b-it:free
  COLDEMAILER_DB=data/coldemailer.db
  ```
  Before committing, check the current `:free` list at `https://openrouter.ai/api/v1/models` and replace any ID that no longer exists.
- [ ] **Step 3:** Write `schema.sql` with every table in spec §6 plus these spec additions:
  - `contacts`: add `hold_reason TEXT`, `ooo_until TEXT`, `variant TEXT` (`A`/`B`), `source TEXT DEFAULT 'engine'`; `contact_id` columns everywhere are `INTEGER REFERENCES contacts(id)`.
  - `sends`: add `source TEXT DEFAULT 'engine'` (`import` for seeded), `state` adds `soft_bounced`, `held`.
  - `send_attempts(id, send_id, attempt, idempotency_key UNIQUE, result, smtp_code, at, UNIQUE(send_id, attempt))`.
  - `review_items(id, contact_id, send_id, kind, reason, detail JSON, created_at, resolved_at, resolution, note)`.
  - `events.contact_id` nullable (system events); `events.type` free text — the full list: `sent, deferred, soft_bounced, hard_bounced, needs_review, replied, ooo, unsubscribed, complaint, deactivated, content_blocked, classified, mailbox_paused, mailbox_resumed, human_action, ai_call, ai_fallback, template_generated, error`.
  - `cycles(id, started_at_wall, sim_from, sim_to, sent, deferred_json, replies, bounces, deactivated, held, duration_ms)`.
  - `jobs(id, kind, status, progress, total, result_json, error, created_at, finished_at)`.
  - `verify_runs(id, sim_at, ok, checks_json)`.
  - `run_lock(id=1 CHECK(id=1), owner, expires_at)`.
  - Indexes: spec §6 list, but replace the due index with **`CREATE INDEX ix_due ON contacts(next_action, next_action_at) WHERE next_action IS NOT NULL AND hold_reason IS NULL`** (per-action range scans, see Task 7); add `contacts(hold_reason) WHERE hold_reason IS NOT NULL`, `contacts(ooo_until) WHERE ooo_until IS NOT NULL`, `review_items(resolved_at)`, `sends(contact_id)`, `send_attempts(send_id)`.
- [ ] **Step 4:** Implement `config.py`, `db.py`, `timeutil.py`, `cli.py` (`init-db` creates `data/` and the schema) and `__main__.py` calling `cli.main()`.
- [ ] **Step 5:** Smoke check:
  ```
  py -3.12 -m venv .venv; .venv\Scripts\pip install -r requirements.txt -e .
  .venv\Scripts\python -m coldemailer init-db
  .venv\Scripts\python -c "import sqlite3;print(sorted(r[0] for r in sqlite3.connect('data/coldemailer.db').execute(\"select name from sqlite_master where type='table'\")))"
  ```
  Expected: list contains `campaign, clock, contacts, cycles, domain_daily, events, jobs, llm_calls, mailbox_daily, mailboxes, replies, review_items, run_lock, send_attempts, sends, sim_events, suppression, templates, verify_runs`.
- [ ] **Step 6:** Commit `feat: scaffold package, config, schema`.

### Task 2: Clock, run lock, lifecycle rules

**Files:**
- Create: `src/coldemailer/clock.py`, `src/coldemailer/lifecycle.py`

**Interfaces:**
- Consumes: Task 1 `db`, `timeutil`.
- Produces:
  - `clock.now(conn) -> str`; `clock.set_now(conn, ts: str) -> None` raises `clock.ClockError("clock cannot move backwards")` if `ts < now`; `clock.acquire_run_lock(conn, owner: str, ttl_s: int = 600) -> bool` (atomic upsert that succeeds only if no row or `expires_at` < wall-clock — the lock is the *one* place wall time is allowed, because it guards real processes); `clock.release_run_lock(conn, owner)`; `clock.RunLocked(Exception)`; context manager `clock.run_lock(conn, owner)` raising `RunLocked("another run is in progress")`.
  - `lifecycle.STATUS_BY_STEP = ["new","first_sent","follow_up_1_sent","follow_up_2_sent","follow_up_3_sent"]`, `TERMINAL = {"replied","bounced","unsubscribed","deactivated"}`, `STEP_NAMES = ["First email","Follow-up 1","Follow-up 2","Follow-up 3"]`.
  - `lifecycle.next_action_after(step_sent: int) -> str` (`send_{n+1}` for n<4, `deactivate` for 4).
  - `lifecycle.schedule_next(sent_at: str, step_sent: int, tz: str, waits: list[int], key: str, start="09:00", end="18:00") -> str` — local date of `sent_at` + `waits[step_sent-1]` days at a minute inside the window drawn from `rng(key)`; for `step_sent == 4` returns the end of that local window day (deactivation time).
  - `lifecycle.rng(*parts) -> random.Random` seeded from `sha256(":".join(parts))`.
  - `lifecycle.ooo_push(received_at: str, return_date: str | None, tz, key) -> str` (return date at a window minute, else +3 days; capped +14 days).

- [ ] **Step 1:** Implement both modules.
- [ ] **Step 2:** Smoke check: `python -c "from coldemailer import lifecycle as l; print(l.schedule_next('2026-10-01T05:00:00Z',1,'America/New_York',[3,5,7,7],'c1:1'))"` prints a `2026-10-04T1x:xx:00Z`/`2026-10-04T2x` timestamp that is between 13:00Z and 22:00Z (09:00–18:00 EDT). Running it twice prints the same value.
- [ ] **Step 3:** Commit `feat: virtual clock, run lock, lifecycle schedule`.

### Task 3: Static templates, rendering, content checks

**Files:**
- Create: `src/coldemailer/ai/__init__.py`, `src/coldemailer/ai/templates.py`, `src/coldemailer/content.py`

**Interfaces:**
- Produces:
  - `templates.STATIC: dict[int, tuple[str, str]]` — step → (subject, body) with merge fields `{first_name} {company} {role} {industry_topic} {sender_name} {unsubscribe_url}`; one per step, plain, no spam words.
  - `templates.ensure_static(conn)` inserts `segment='*'` rows (`source='static'`, variants A and B identical).
  - `templates.pick(conn, segment, step, variant) -> sqlite3.Row` (exact segment row, else static `*`).
  - `templates.render(tpl_row, contact_row, mailbox_address: str) -> tuple[str, str, dict]` → (subject, body, headers). Headers: `List-Unsubscribe: <mailto:unsubscribe@{mailbox_domain}?subject=unsub-{contact_id}>, <https://unsub.example.test/u/{token}>` and `List-Unsubscribe-Post: List-Unsubscribe=One-Click`; token = first 16 hex of `sha256(contact_id:campaign)`.
  - `templates.load_seed_file(conn, path="data/templates.seed.json")`, `templates.export_seed_file(conn, path)`; JSON list of `{segment, step, variant, subject, body, source, created_at}`.
  - `content.check(subject: str, body: str) -> list[str]` — reasons: `unrendered_placeholder` (regex `\{[a-z_]+\}`), `spam_words` (fixed list of ~20, e.g. "free money", "guarantee", "act now", "100%"), `too_many_links` (>2 URLs excluding unsubscribe), `too_long` (body > 1200 chars), `missing_unsubscribe`, `subject_all_caps`.
- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Smoke: `python -c` that renders STATIC step 1 for a dict-like contact and prints `content.check(...)` → `[]`; rendering with `company=None` yields `['unrendered_placeholder']` (render leaves unknown/None fields as `{company}` so the check catches it).
- [ ] **Step 3:** Commit `feat: static templates, rendering, content checks`.

### Task 4: Seed with consistent history

**Files:**
- Create: `src/coldemailer/seed.py`
- Modify: `src/coldemailer/cli.py` (add `seed`)

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: `seed.run(settings, n: int = 10000, seed: int = 42) -> dict` (counts by status + `seconds`); CLI `seed --n N --seed S` deletes the DB file (and `-wal`/`-shm`), inits schema, seeds, prints counts and time.

Rules (spec §10 + §15a):
- Faker(`en_US`) seeded with `seed`; ~2,500 companies so several contacts share a domain; 3 of those domains flagged "bad" (stored only in the mock provider config as `bad_domains`, hard-bounce rate 40%) so auto-pause is demonstrable.
- Role from a fixed list of 8; industry from the 5 segments; `segment = industry`; `variant` = `A`/`B` from `rng(contact_id)`.
- Timezone weights: Asia/Kolkata 30, America/New_York 20, America/Los_Angeles 10, Europe/London 15, Europe/Berlin 10, Asia/Singapore 8, Australia/Sydney 7.
- Status from the seed mix; build history **backwards** from the clock start: pick the last action time 0–(wait) days before clock start so active contacts' `next_action_at` (computed with `lifecycle.schedule_next`) spreads from ~2 days overdue to ~7 days ahead. Each prior step gets a `sends` row (`state='sent'`, `source='import'`, `sent_at` inside the prospect's window, spaced exactly by `schedule_next`) + a `sent` event. Terminal contacts get their terminating event 1 h–3 days after the last send (`hard_bounced`, `replied` + `replies` row with a corpus body and its classification, `unsubscribed` + suppression, `deactivated` at the `schedule_next(...,4,...)` time). Bounced/unsubscribed/not_interested replies go into `suppression` with the right reason.
- Mailboxes: 20 rows `sales{n}@mail-{a..e}.test`, warm-up as in Fixed values; seeded contacts that already have sends get a sticky `mailbox_id` round-robin.
- Campaign row with waits/window/cap; clock row = clock start; `templates.ensure_static`; `templates.load_seed_file` if the file exists.
- Everything inserted with `executemany` in one transaction.
- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Smoke: `python -m coldemailer seed --n 10000` prints counts within ±1% of the mix and a time (target < 15 s). Running it twice prints identical counts.
- [ ] **Step 3:** Commit `feat: deterministic seed with consistent history`.

### Task 5: Mock provider + reply corpus

**Files:**
- Create: `src/coldemailer/provider_mock.py`, `src/coldemailer/sim_corpus.py`

**Interfaces:**
- Produces:
  - `sim_corpus.reply_body(label: str, rng, first_name: str, return_date: str | None) -> str` — 6–10 phrasings per label; OOO bodies always contain an unambiguous marker ("out of office", "OOO", "automatic reply", "on leave") and ~70% include a return date like "back on November 3"; ~15% of interested/not_interested bodies are deliberately ambiguous (no rule keyword) to exercise the LLM path; unsubscribe bodies say "remove me"/"unsubscribe"/"stop emailing".
  - `provider_mock.SendResult(kind: Literal["accepted","hard_bounce","soft_bounce"], smtp_code: str | None, message: str | None)`.
  - `provider_mock.MockProvider(conn, settings)`:
    - `send(idempotency_key, send_id, contact_id, step: int, at: str, domain: str, first_name: str) -> SendResult` — if an `accepted` row for the key exists, return accepted without writing (idempotent). Otherwise roll `rng(idempotency_key)`: hard bounce (rate, or 40% for bad domains) → write `hard_bounce` row with `deliver_at = at + 1–10 min`, code `550 5.1.1 User unknown`; soft bounce → `soft_bounce` row, code from transient `421 4.4.2`/`450 4.2.1`/`452 4.2.2` or policy `550 5.7.1`/`554 5.7.0`; else write `accepted` row (`deliver_at = at`, `processed_at = at`) plus at most one future reaction: complaint, unsubscribe click, or reply with `true_label` and body. Returns the immediate result (bounces are *also* reported asynchronously via their rows, which ingest uses).
    - `was_accepted(idempotency_key) -> bool`.
- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Smoke: a `python -c` that sends 10,000 fake keys and prints kind frequencies → accepted ≈ 94%, hard ≈ 4%, soft ≈ 2%; resending the first key returns the same result and inserts nothing.
- [ ] **Step 3:** Commit `feat: mock provider writing sim_events`.

### Task 6: Sending policies

**Files:**
- Create: `src/coldemailer/policies.py`

**Interfaces:**
- Produces:
  - `warmup_limit(mailbox_row, ist_day: str) -> int` = `min(max_limit, start_limit + daily_increment * days_since_warmup_start)`; 0 if paused.
  - `pacing_gap_s(limit: int, window_minutes: int = 540) -> int` = `window_minutes*60 // max(limit,1)`.
  - `class Capacity` — per-cycle in-memory view loaded once per cycle: `Capacity.load(conn, settings, sim_from, sim_to)`; `next_slot(mailbox_id, earliest: str) -> str | None` (≥ `last_sent_at + gap`, ≥ `earliest`, ≤ `sim_to`; None ⇒ pacing/capacity), `remaining(mailbox_id, ist_day) -> int`, `domain_remaining(domain, ist_day) -> int`, `commit(mailbox_id, domain, at)` (updates in-memory counters; DB counters are written by the engine in finalize), `pick_mailbox(contact_row, ist_day) -> int | None` (sticky mailbox unless paused; first send or paused sticky ⇒ active mailbox with most remaining today, ties by id).
  - `gate(contact_row, slot: str, settings) -> str | None` — returns a defer reason or None. Reasons (exact strings, also used by report and UI): `outside_window`, `domain_cap`, `mailbox_capacity`, `pacing`, `mailbox_paused`.
  - `evaluate_autopause(conn, mailbox_id, settings) -> str | None` — over that mailbox's last 50 engine attempts (min 20): returns e.g. `"bounce rate 6.8% over 5% threshold"` or `"complaint rate 0.4% over 0.3% threshold"`.
- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Smoke: `python -c` printing `warmup_limit` for days 0, 5, 30 → 20, 70, 200; `pacing_gap_s(40)` → 810.
- [ ] **Step 3:** Commit `feat: warm-up, pacing, domain cap, window gates, auto-pause`.

### Task 7: The cycle (engine)

**Files:**
- Create: `src/coldemailer/engine.py`, `src/coldemailer/ai/classify.py` (rules part only)

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces:
  - `classify.rules(body: str) -> tuple[str | None, float]` — label or None, with confidence; OOO and unsubscribe markers ⇒ confidence 1.0; strong interested/not-interested keywords ⇒ 0.8; else `(None, 0.0)`.
  - `classify.parse_return_date(body: str, received_at: str) -> str | None` (handles "back on November 3", "returning 3 Nov", "until 2026-11-03"; picks the next such date after `received_at`).
  - `engine.CycleStats` dataclass: `sent:int, deferred:dict[str,int], replies:int, ooo:int, bounces:int, soft_bounces:int, deactivated:int, held:int, blocked:int, duration_ms:int`.
  - `engine.run_cycle(conn, settings, sim_from: str, sim_to: str) -> CycleStats` — writes a `cycles` row.
  - `engine._finalize(conn, ...)` is a module-level function (tests monkeypatch it to simulate a crash).

Order inside `run_cycle` (spec §7, §14a, §15a):
1. **Recover:** sends in `claimed` whose contact claim expired: `provider.was_accepted(key)` ⇒ `_finalize`; else delete the claimed `sends`/attempt row and clear the claim.
2. **Ingest** `sim_events` with `deliver_at <= sim_to AND processed_at IS NULL AND kind != 'accepted'`, ordered by `deliver_at, id`; mark `processed_at`. Ignore events for contacts already terminal except `unsubscribe_click`/`complaint` (still suppress). Mapping:
   - `hard_bounce` → send `bounced`, contact `bounced`, suppression `hard_bounce`, mailbox_daily.bounced++, `hard_bounced` event.
   - `soft_bounce` → attempt result recorded, send `soft_bounced`, `soft_bounced` event. Policy codes (`5.7.x`) or 3rd attempt ⇒ `hold_reason` (`policy_rejection`/`repeated_soft_bounce`) + `review_items` row + `needs_review` event. Else `next_action_at = slot of now + 1h/4h/24h` (keep `next_action` the same step).
   - `reply` → `replies` row; `classify.rules`: OOO ⇒ `ooo` event, `ooo_until`/`next_action_at = lifecycle.ooo_push(...)`, status unchanged; unsubscribe ⇒ `unsubscribed` + suppression `unsubscribe_reply`; otherwise contact `replied`, `next_action=NULL`, `replied` event, classification set if rules confident (`classified_by='rule'`), `not_interested` ⇒ suppression `not_interested`.
   - `unsubscribe_click` → `unsubscribed` + suppression `unsubscribe_link`. `complaint` → `unsubscribed` + suppression `complaint`, mailbox_daily.complaints++.
3. **Deactivate:** range scan `next_action='deactivate' AND next_action_at <= sim_to` ⇒ `deactivated`.
4. **Send**, scanning by action in priority order `send_4, send_3, send_2, send_1` (follow-ups before first emails), each `WHERE next_action=? AND next_action_at <= sim_to AND hold_reason IS NULL ORDER BY next_action_at, id LIMIT batch` with keyset continuation. For each candidate: `pick_mailbox` → `next_slot(mb, max(next_action_at, sim_from))` → `gate(...)`; deferred ⇒ count + `deferred` event (once per contact per IST day: skip if one exists for that day). If a mailbox has no slot left, stop asking it this cycle.
   - **Claim tx** (`BEGIN IMMEDIATE`): `UPDATE contacts SET claimed_by=:cycle, claim_expires_at=:now+300s WHERE id=? AND step=:n-1 AND status NOT IN terminal AND hold_reason IS NULL AND (claim_expires_at IS NULL OR claim_expires_at < :now) RETURNING *`; no row ⇒ skip. Check suppression by email ⇒ skip + clear next_action. Render + `content.check` — failures ⇒ `content_blocked` event, `review_items` kind `content`, hold. Insert or reuse the `sends` row (`UNIQUE(contact_id, step)`; existing `sent` row ⇒ abort, clear claim) and a new `send_attempts` row, `idempotency_key = sha256(f"{contact_id}:{step}:{campaign_id}:{attempt}")`.
   - **Provider:** `MockProvider.send(...)` at `slot`.
   - **`_finalize` tx:** accepted ⇒ send `sent`, `sent_at=slot`, contact `step=n`, `status=STATUS_BY_STEP[n]`, `next_action=next_action_after(n)`, `next_action_at=schedule_next(slot, n, tz, waits, f"{id}:{n}")`, `mailbox_id`, clear claim and `ooo_until`, `sent` event, `mailbox_daily.sent++`, `domain_daily.sent++`, `mailboxes.last_sent_at=slot`, `Capacity.commit`. Bounce results only clear the claim (ingest handles them when their row is due).
5. **Auto-pause:** for each mailbox that sent this cycle, `evaluate_autopause` ⇒ `paused=1`, reason, `mailbox_paused` event.
6. **Classify backlog** (Task 10 plugs the LLM in; here it is a no-op hook `classify.backlog(conn, settings, llm=None)` that returns 0).
- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Add CLI `run` (one cycle from the previous `cycles.sim_to`, or clock start, to `clock.now`) — inside `clock.run_lock`; prints the `CycleStats`.
- [ ] **Step 3:** Smoke: `seed --n 2000`, then `run` twice. First prints `sent > 0` (overdue contacts); second prints `sent: 0` (same clock — no double send). `sqlite3 data/coldemailer.db "select contact_id, step, count(*) from sends group by 1,2 having count(*)>1"` returns nothing.
- [ ] **Step 4:** Commit `feat: engine cycle with claim/send/finalize and ingest`.

### Task 8: Advance and background jobs

**Files:**
- Create: `src/coldemailer/runner.py`
- Modify: `src/coldemailer/cli.py` (add `advance`)

**Interfaces:**
- Produces:
  - `runner.advance(conn, settings, hours: int, step_hours: int = 1, on_progress: Callable[[int,int], None] | None = None, owner="cli") -> list[engine.CycleStats]` — under `clock.run_lock`: for each step, `set_now(now + step)`, `run_cycle(prev, now)`; `hours=0` runs one cycle at the current time ("Run cycle now").
  - `runner.start_job(db_path, settings, hours) -> int` — inserts a `jobs` row and runs `advance` in a `threading.Thread` with its **own** connection, updating `progress/total` per step, `status` `running → done | error`; refuses (raises `clock.RunLocked`) if the lock is held.
  - CLI `advance --days D | --hours H [--step 1]` prints one summary line per simulated day and totals.
- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Smoke: `seed --n 10000` then `advance --days 3` prints 3 day lines with sends, replies and deactivations; a second terminal running `advance --hours 1` at the same time prints "another run is in progress" and exits 1.
- [ ] **Step 3:** Commit `feat: advance clock and background jobs`.

### Task 9: Human actions

**Files:**
- Create: `src/coldemailer/actions.py`
- Modify: `src/coldemailer/cli.py` (add `review list`, `review resolve <id> <action> [--days N]`, `mailbox resume <id>`, `suppress <email>`)

**Interfaces:**
- Produces (each in one tx, each writes a `human_action` event with `detail.actor="operator"`):
  - `actions.resolve_review(conn, item_id: int, action: str, days: int = 3, note: str | None = None) -> None` with `action` ∈ `retry_now` (clear hold, `next_action_at = now`), `retry_later` (clear hold, `now + days`), `mark_bounced` (status `bounced`, suppression `hard_bounce`), `suppress` (status `unsubscribed`, suppression `manual`), `label:interested|not_interested|out_of_office|unsubscribe` (only for kind `classification`: sets the reply label, `classified_by='human'`; `unsubscribe`/`not_interested` add suppression; `unsubscribe` moves `replied → unsubscribed`). Raises `ValueError` for an action that doesn't fit the item kind or an already-resolved item.
  - `actions.resume_mailbox(conn, mailbox_id)` (`paused=0`, `mailbox_resumed` event).
  - `actions.suppress_contact(conn, contact_id, reason="manual")`.
- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Smoke: after `advance --days 5`, `review list` shows ≥ 1 item; `review resolve <id> retry_now` then `run` sends that contact's held step exactly once.
- [ ] **Step 3:** Commit `feat: review queue actions, mailbox resume, manual suppress`.

### Task 10: OpenRouter client, template warm-up, LLM classification

**Files:**
- Create: `src/coldemailer/ai/openrouter.py`
- Modify: `src/coldemailer/ai/templates.py` (add `warm`), `src/coldemailer/ai/classify.py` (add `backlog`), `src/coldemailer/engine.py` (call `classify.backlog` with an `LLMClient` when a key is set), `src/coldemailer/cli.py` (`templates warm`, `templates export`)

**Interfaces:**
- Produces:
  - `openrouter.LLMClient(conn, settings, transport: httpx.BaseTransport | None = None, sleep=time.sleep)`; `chat(messages: list[dict], purpose: str, json_mode=False) -> tuple[str, str] | None` → (text, model) or None after every model failed. Token bucket at `llm_rpm`; per model up to 3 tries: 429 ⇒ wait `Retry-After` (else `2**try + jitter`), 5xx/timeout ⇒ backoff; after 3 failures the model gets a 60 s cool-down (wall time) and the next model is tried. Every HTTP attempt ⇒ `llm_calls` row (`status` ok/rate_limited/error/fallback, `http_status`, `latency_ms`, `retry_of`); a full fallback writes an `ai_fallback` event. `sleep` is injected so tests don't wait.
  - `templates.warm(conn, client) -> dict` — for each segment × step 1–4 × variant A/B missing a non-static row: prompt for JSON `{subject, body}` using only the allowed merge fields, ≤ 120 words, includes `{unsubscribe_url}`; validated by `content.check` after a test render; stored with `source=f"llm:{model}"` and a `template_generated` event; invalid ⇒ skipped (static stays). Resumable.
  - `classify.backlog(conn, settings, llm: LLMClient | None) -> int` — up to `classify_calls_per_cycle` calls, each classifying 10 unclassified replies with a JSON array response `[{"id":..,"label":..}]`; labels outside the 4 ⇒ ignored. No LLM or failure ⇒ keyword fallback (`classified_by='fallback'`). If the LLM label conflicts with a confident rule label or the call returns nothing usable twice for a reply ⇒ `review_items` kind `classification`. Applies post-terminal effects (`unsubscribe` ⇒ `unsubscribed` + suppression; `not_interested` ⇒ suppression) and a `classified` event.
- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Smoke without a key: `advance --days 2` still works; `select classified_by, count(*) from replies group by 1` shows `rule` and `fallback` only.
- [ ] **Step 3:** Smoke with a key in `.env`: `templates warm` prints generated/skipped counts; `templates export` writes `data/templates.seed.json` (40 entries max). If the user hasn't provided a key yet, record that and move on — Task 23 needs it.
- [ ] **Step 4:** Commit `feat: OpenRouter client with rate limiting and fallbacks`.

### Task 11: Report and verify

**Files:**
- Create: `src/coldemailer/report.py`, `src/coldemailer/verify.py`
- Modify: `src/coldemailer/cli.py` (`report [--from DAY --to DAY] [--json]`, `verify`, `contact <email>`)

**Interfaces:**
- Produces (all pure reads; IST days; return plain dicts/lists — the API returns them as-is, the UI renders them):
  - `report.overview(conn, day_from: str, day_to: str) -> dict` with keys: `clock` (`now`, `now_ist`), `campaign` (settings card), `kpis` (`sent_range`, `capacity_range`, `due_today`, `due_tomorrow`, `reply_rate`, `replied`, `contacted`, `bounce_rate`, `hard_bounces`, `sends_range`, `deactivated`, `unsubscribed`, `needs_review`, `total`), `statuses` (9 rows: key, count), `markers` (`on_hold`, `paused_ooo` = contacts with `ooo_until > now`), `replies` (counts per label; OOO counted here but excluded from reply rate), `classifiers` (`rule`, `llm`, `fallback`, `human`, `pending`), `classifier_accuracy` (vs `sim_events.true_label`), `hours` (24 rows for the *current* IST day: count per step), `deferred` (reason → count, today), `due` (rows `send_1..send_4, deactivate` × today/tomorrow), `suppression` (total + per reason + added today + `list_unsubscribe_coverage`), `health` (latest `verify_runs` row), `cycle` (latest `cycles` row).
  - Rate definitions (spec §12b): reply rate = contacts with a `replies` row whose label ≠ `out_of_office` (or unlabelled and status `replied`) ÷ contacts with ≥ 1 `sent` send; bounce rate = hard-bounced sends ÷ sends with `sent_at` in range.
  - `report.contacts(conn, filters: dict, page: int, size: int) -> {"total": int, "rows": [...]}` — filters `status, step, segment, timezone, mailbox_id, on_hold, q`; ORDER BY id; LIMIT/OFFSET.
  - `report.search(conn, q: str, limit=6)` — prefix match on email, then `LIKE` on name/company.
  - `report.contact_detail(conn, contact_id) -> dict` — facts (incl. local time now, next action in IST and local, suppressed + reason, hold), timeline (events joined with sends/attempts/replies; send items carry subject, body, template segment/step/variant, source model, headers), open review items, available actions.
  - `report.events(conn, group: str | None, type: str | None, q: str | None, day_from, day_to, limit=40, before_id: int | None) -> {"rows": [...], "next_before_id": int | None}` with the group map from the mockup: Email (`sent, replied, ooo, hard_bounced, soft_bounced, deferred, content_blocked`), Contact (`needs_review, unsubscribed, complaint, deactivated`), Mailbox (`mailbox_paused, mailbox_resumed`), AI (`ai_call, ai_fallback, template_generated, classified`), System (`error`), Human (`human_action`). `ai_call` rows come from `llm_calls` (UNION ALL), so no duplicate event writes.
  - `report.reviews(conn, state: "open"|"resolved"|"all")`, `report.mailboxes(conn)` (row + `warmup_day`, today/tomorrow limit, used today, bounce & complaint rate over last 50, and a 28-day `series` of `{day, limit, sent}`), `report.ai_usage(conn)` (totals + per model), `report.templates(conn)` (grid segment × step × variant).
  - `verify.run(conn, settings) -> {"ok": bool, "checks": [{"name","ok","detail"}]}` and stores a `verify_runs` row. Checks: (1) status matches events-derived status; (2) `step` == count of `sent` sends; (3) no duplicate (contact, step) and ≤ 1 accepted attempt per send; (4) no send to an email after its suppression time; (5) terminal ⇒ `next_action IS NULL`; (6) consecutive sends spaced ≥ waits (OOO pushes only lengthen); (7) no engine send outside prospect window; (8) engine sends per mailbox per day ≤ warm-up limit; (9) engine sends per domain per day ≤ cap; (10) no send while held; (11) every `sim_events` reaction older than now is processed; (12) every non-terminal, non-held contact has a `next_action`.
- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Smoke: after `seed` → `verify` prints 12 passing checks; after `advance --days 10` → `verify` still all pass; `report` prints status counts summing to 10,000, due today/tomorrow, rates.
- [ ] **Step 3:** Commit `feat: report and verify`.

### Task 12: FastAPI

**Files:**
- Create: `src/coldemailer/api.py`
- Modify: `src/coldemailer/cli.py` (`serve [--port 8000]`)

**Interfaces:**
- Consumes: Tasks 8, 9, 11.
- Produces (JSON; per-request connection via a dependency; empty/uninitialised DB ⇒ 200 with zero/empty payloads, flag `initialised: false`):
  - `GET /api/overview?from=&to=` → `report.overview`
  - `GET /api/contacts?status=&step=&segment=&timezone=&mailbox_id=&on_hold=&q=&page=&size=` → `report.contacts`
  - `GET /api/search?q=` → `report.search`
  - `GET /api/contacts/{id}` → `report.contact_detail` (404 if missing)
  - `GET /api/events?group=&type=&q=&from=&to=&before_id=&limit=` → `report.events`
  - `GET /api/reviews?state=` → `report.reviews`
  - `GET /api/mailboxes`, `GET /api/ai`, `GET /api/templates`
  - `GET /api/clock` → `{now, now_ist, running_job}`
  - `POST /api/clock/advance {"hours": int}` → `{"job_id"}` (409 `{"detail":"another run is in progress"}` when locked); `hours: 0` = run cycle now.
  - `GET /api/jobs/{id}` → `{status, progress, total, result, error}`
  - `POST /api/reviews/{id}/resolve {"action","days"?,"note"?}`, `POST /api/mailboxes/{id}/resume`, `POST /api/contacts/{id}/suppress` (400 on `ValueError`)
  - `POST /api/verify` → `verify.run`
  - `/` serves `web/index.html`; `/web/*` static.
- [ ] **Step 1:** Implement.
- [ ] **Step 2:** Smoke: `serve`, then `curl localhost:8000/api/overview` returns JSON with `kpis.total == 10000`; `curl -X POST localhost:8000/api/clock/advance -d "{\"hours\":24}" -H "content-type: application/json"` returns a job id and `/api/jobs/<id>` reaches `done`.
- [ ] **Step 3:** Commit `feat: FastAPI endpoints`.

---

# Phase 2 — Pytest suite

Shared fixtures go in `tests/conftest.py`: `settings` (`load_settings(db_path=tmp_path/"t.db", openrouter_api_key=None)`), `conn` (initialised empty DB with campaign, clock, 2 mailboxes at max 200/day), `seeded` (`seed.run(settings, n=300)`), and helpers `make_contact(conn, **fields) -> int`, `due_now(conn, contact_id)` (sets `next_action_at = clock.now`), `tick(conn, settings, hours=1)` (= `runner.advance(conn, settings, hours)`), and `force_provider(monkeypatch, kind, code=None, reaction=None)` (patches `MockProvider` dice for one send). Make contacts' timezone `Asia/Kolkata` and the clock `2026-10-01T04:30:00Z` (10:00 IST) unless a test says otherwise.

### Task 13: Lifecycle and exactly-once tests

**Files:** Create `tests/conftest.py`, `tests/test_lifecycle.py`, `tests/test_idempotency.py`

- [ ] **Step 1:** Write tests (names and assertions):
  - `test_new_contact_gets_first_email`: `make_contact` + `due_now` + `tick` ⇒ one `sends` row step 1 state `sent`, contact `first_sent`, `next_action == "send_2"`, `next_action_at` on the IST date 3 days later inside 09:00–18:00 local.
  - `test_full_sequence_then_deactivated`: forced always-accepted, no reactions; `tick(hours=24*25)` ⇒ steps 1–4 sent with gaps ≥ 3/5/7 days, then status `deactivated` ≥ 7 days after step 4, `next_action is None`.
  - `test_reply_stops_sequence`: reaction `reply` label `interested` 1 h after step 1 ⇒ after 10 days: status `replied`, only 1 send, `replies.classification == "interested"`.
  - `test_ooo_pushes_not_stops`: OOO reply with "back on October 12" ⇒ status still `first_sent`, `ooo_until` and `next_action_at` on 2026-10-12 local window; step 2 sends on/after it.
  - `test_hard_bounce_suppresses_forever`: hard bounce ⇒ status `bounced`, email in `suppression`, `next_action is None`; another 30 days ⇒ still 1 send; setting that contact's status back to `first_sent` with `next_action` due via raw SQL still sends nothing (the claim-time suppression check by email holds).
  - `test_unsubscribe_click_and_complaint_suppress`: both ⇒ `unsubscribed`, suppression reasons `unsubscribe_link` / `complaint`.
  - `test_soft_bounce_retries_then_review`: transient soft bounce ×3 ⇒ attempts at +1 h, +4 h, +24 h (± pacing), then `hold_reason == "repeated_soft_bounce"`, one open `review_items`; `resolve_review(retry_now)` + tick ⇒ step sent once, `send_attempts` count 4, accepted 1.
  - `test_policy_rejection_goes_straight_to_review`: `550 5.7.1` ⇒ hold after 1 attempt.
  - `test_run_twice_same_clock_sends_nothing_new` (`runner.advance(hours=0)` twice ⇒ second `sent == 0`).
  - `test_crash_after_provider_accept_recovers_without_resend`: monkeypatch `engine._finalize` to raise once ⇒ cycle raises; restore; set clock +10 min (past lease) and run ⇒ exactly one `sim_events` `accepted` row for the key, send `sent`, contact `first_sent`.
  - `test_reply_in_same_cycle_as_due_followup_blocks_send` (Review Focus 3): contact at `first_sent` due now and a `reply` sim_event delivered now ⇒ tick ⇒ no step-2 send, status `replied`.
  - `test_concurrent_advance_refused` (Review Focus 2): hold `clock.run_lock(conn,"a")`, then `runner.advance(conn2, ...)` raises `RunLocked`.
  - `test_clock_rejects_backwards`.
  - `test_cli_before_seed_exits_1` (Review Focus 5): `cli.main(["run"])` on a missing DB ⇒ `SystemExit(1)` and message contains "run `python -m coldemailer seed`".
- [ ] **Step 2:** Run `pytest tests/test_lifecycle.py tests/test_idempotency.py -v`. Expected: all pass. Fix any engine bug found (bugs go in the engine, not the test).
- [ ] **Step 3:** Commit `test: lifecycle and exactly-once`.

### Task 14: Policy, seed, verify tests

**Files:** Create `tests/test_policies.py`, `tests/test_seed_verify.py`

- [ ] **Step 1:** Write tests:
  - `test_window_respects_timezone`: contact in `America/Los_Angeles` due at 10:00 IST ⇒ deferred `outside_window`; advancing to 09:00 PT sends at ≥ 09:00 local.
  - `test_window_across_dst` (Review Focus 4): NY contact due 2026-10-31 and 2026-11-02 ⇒ both sends between 09:00 and 18:00 local per `zoneinfo`.
  - `test_warmup_limit_caps_daily_sends`: 1 mailbox, warm-up day 0 (limit 20), 100 due contacts ⇒ after one IST day exactly 20 sent, 80 deferred `mailbox_capacity`/`pacing`.
  - `test_pacing_spreads_sends`: same setup ⇒ consecutive `sent_at` gaps ≥ `pacing_gap_s(20)`.
  - `test_domain_cap`: 60 contacts at one domain, cap 50 ⇒ 50 sent that day, 10 deferred `domain_cap`.
  - `test_all_mailboxes_paused_defers_once_per_day` (Review Focus 1): pause both ⇒ tick 24 h ⇒ 0 sent, exactly one `deferred` event per contact for that IST day with reason `mailbox_paused`.
  - `test_autopause_on_bounce_rate`: force 5 hard bounces in 30 sends ⇒ mailbox `paused`, `mailbox_paused` event; `resume_mailbox` ⇒ active.
  - `test_followups_before_first_emails`: capacity 5, 5 due follow-ups + 5 due new ⇒ the 5 follow-ups are sent.
  - `test_content_block_holds_contact`: template with `{company}` and contact `company=None` ⇒ `content_blocked` event, hold, no send.
  - `test_seed_deterministic`: two `seed.run(n=500, seed=42)` into two DBs ⇒ identical `sha256` of `contacts`, `sends`, `events` rows ordered by id.
  - `test_seed_counts_match_mix`: n=2000 ⇒ each status within ±2 pp of the mix.
  - `test_seed_history_consistent`: every `follow_up_2_sent` contact has sends for steps 1–3 with gaps ≥ 3 and 5 days.
  - `test_verify_passes_after_seed_and_advance`: `seeded` + `tick(24*14)` ⇒ `verify.run(...)["ok"] is True`.
  - `test_verify_catches_drift`: raw SQL sets a `first_sent` contact's `step` to 3 ⇒ `verify.run` returns `ok False` with check 2 failing and naming that contact id.
- [ ] **Step 2:** `pytest tests/test_policies.py tests/test_seed_verify.py -v` — all pass.
- [ ] **Step 3:** Commit `test: policies, seed determinism, verify`.

### Task 15: AI tests (mocked HTTP)

**Files:** Create `tests/test_ai.py`

- [ ] **Step 1:** Write tests using `httpx.MockTransport` and an injected no-op `sleep`:
  - `test_429_retries_then_succeeds`: responses 429 (`Retry-After: 2`), 200 ⇒ result text; `llm_calls` statuses `["rate_limited","ok"]`; sleep called with 2.
  - `test_model_fallback_chain`: model 1 always 429, model 2 returns 200 ⇒ result model == model 2; model 1 has 3 `rate_limited` rows.
  - `test_all_models_fail_returns_none_and_logs_fallback`: ⇒ `None`, one `ai_fallback` event.
  - `test_token_bucket_limits_rate`: 20 calls with a fake clock ⇒ sleep total ≥ the time 15 rpm requires.
  - `test_rules_classifier`: table-driven ≥ 12 bodies from `sim_corpus` (OOO, unsubscribe, interested, not interested, ambiguous ⇒ None).
  - `test_parse_return_date`: "back on November 3" with received `2026-10-20T..` ⇒ `2026-11-03`; "until 3 Nov" same; nothing ⇒ None.
  - `test_backlog_batch_and_conflict_to_review`: LLM returns JSON for 10 ids, one conflicting with a confident rule ⇒ 9 labelled `llm:<model>`, 1 `review_items` kind `classification`.
  - `test_no_key_uses_fallback`: `llm=None` ⇒ all labelled `fallback`/`rule`.
  - `test_warm_rejects_invalid_template`: LLM returns a body with `{ceo_name}` ⇒ not stored, static used by `templates.pick`.
- [ ] **Step 2:** `pytest tests/test_ai.py -v` — all pass.
- [ ] **Step 3:** Commit `test: OpenRouter client, classifier, templates`.

### Task 16: API tests

**Files:** Create `tests/test_api.py`

- [ ] **Step 1:** Write tests with `fastapi.testclient.TestClient` against `seeded`:
  - `test_overview_shape`: keys `kpis, statuses, markers, replies, classifiers, hours, deferred, due, suppression, health, cycle, campaign`; `sum(statuses.count) == 300`; `len(hours) == 24`; `len(due) == 5`.
  - `test_contacts_filter_and_paging`: `status=first_sent&size=25&page=0` ⇒ all rows `first_sent`, `total` equals the DB count.
  - `test_contact_detail_timeline`: a `follow_up_1_sent` contact ⇒ timeline has 2 `sent` items each with `subject` and `body`.
  - `test_advance_job_and_lock`: POST `hours: 2` ⇒ job id; polling reaches `done`; a POST while running ⇒ 409.
  - `test_review_resolve_endpoint`: 400 for a wrong action, 200 for `retry_now`.
  - `test_empty_db_returns_200` (Review Focus 5): app on a missing DB ⇒ `/api/overview` 200 with `initialised: false`.
- [ ] **Step 2:** `pytest -q` (whole suite) — all pass; note the runtime.
- [ ] **Step 3:** Commit `test: API`.

---

# Phase 3 — React frontend

Visual spec: `web/mockup/Outreach_clean.html` (unpacked template = layout, inline styles, colour tokens, copy). Port its markup into components; replace every sample-data expression with API data per spec §12b semantics. **No build step:** React 18.3.1 + ReactDOM 18.3.1 UMD from `https://unpkg.com/react@18.3.1/umd/react.production.min.js` / `react-dom@18.3.1`, and `htm@3.1.1` (`https://unpkg.com/htm@3.1.1/dist/htm.module.js`) for JSX-like templates; fonts from Google Fonts (Instrument Sans 400/500/600, JetBrains Mono 400/500). Graders need only Python.

Frontend files (one responsibility each):
- `web/index.html` — fonts, CSS tokens (light/dark exactly as the mockup's `V` objects), mounts `app.js` as a module.
- `web/js/api.js` — `get(path, params)`, `post(path, body)`, error → `{error}`; `poll(jobId, onProgress) -> Promise`.
- `web/js/ui.js` — shared bits: `Card`, `Kpi`, `Bar`, `Pill`, `StateBox` (loading/empty/error + Retry), `fmt`, `fmtIst`, `useFetch(path, params, deps)`, `useWidth()`.
- `web/js/app.js` — header (clock popover, search, sim-day range, theme), tabs, drawer host, global refresh counter bumped after any write.
- `web/js/tabs/dashboard.js`, `contacts.js`, `review.js`, `mailboxes.js`, `ai.js`, `logs.js`; `web/js/drawer.js` (status/KPI list + contact detail).

Each Phase 3 task is verified by serving the app on a seeded DB (`python -m coldemailer serve`) and checking in a browser (Playwright MCP screenshot in light and dark, at 1280 px and 390 px), comparing against the mockup.

### Task 17: Shell — header, tabs, clock, search, range, theme, states

**Files:** Create `web/index.html`, `web/js/api.js`, `web/js/ui.js`, `web/js/app.js`

- [ ] **Step 1:** Implement the header exactly as the mockup: clock chip shows `HH:MM IST` from `/api/clock`; popover shows sim time, "Advanced Nd Nh" since clock start, buttons +1h/+6h/+1d/+7d (`POST /api/clock/advance {hours}`), "Run cycle now" (`hours: 0`), busy dot + "day k of n" progress from `/api/jobs/{id}`, last-cycle line from `overview.cycle`; disable buttons while a job runs; 409 ⇒ toast "another run is in progress". Search input ⇒ `/api/search` (debounce 200 ms, ≥ 2 chars) ⇒ opens contact drawer. Sim-day range picker anchored to the sim clock's IST date (future days disabled), presets Today/7/14/30/90. Theme toggle. Tabs with Review badge = `kpis.needs_review`.
- [ ] **Step 2:** Verify: page loads with no console errors; +1h advances the clock chip; double-click shows the 409 toast.
- [ ] **Step 3:** Commit `feat(web): app shell`.

### Task 18: Dashboard tab

**Files:** Create `web/js/tabs/dashboard.js`

- [ ] **Step 1:** Port: 8 KPI cards (Sent in range "of N mailbox capacity", Due today, Due tomorrow, Reply rate "N of M contacted", Bounce rate "N hard bounces of M sends", Deactivated, Unsubscribed, Needs review → Review tab); funnel (9 statuses, bars scaled to max) + markers On hold / Paused for OOO; reply classification (OOO note "Sequence continues after return date", Unsubscribe "Moved to Unsubscribed") + Classified by (Rules/AI/Fallback/Human/Waiting) + classifier accuracy line; sends per hour (stacked by step, IST); deferred by reason (labels: Outside prospect sending window, Domain cap reached, Mailbox at capacity, Pacing gap, Mailbox paused); next actions due (First email, Follow-up 1–3, Deactivate); suppression list; system health (verify result + "Run verify" button → `POST /api/verify`, last cycle duration, content-check blocks); **Campaign settings** card (spec §12b addition). KPI/funnel clicks open the drawer list (Task 19).
- [ ] **Step 2:** Verify against the mockup screenshot; numbers equal `python -m coldemailer report` for the same DB.
- [ ] **Step 3:** Commit `feat(web): dashboard tab`.

### Task 19: Contacts tab and drawer

**Files:** Create `web/js/tabs/contacts.js`, `web/js/drawer.js`

- [ ] **Step 1:** Contacts: filters (Status, Step, Segment, Timezone, Mailbox, On hold), table columns as mockup, page numbers + rows 25/50/100 via `/api/contacts`. Drawer list mode: title/description per status or KPI, count, contacts from `/api/contacts?status=…` with "Load more"; the Sent KPI lists sends in range (`/api/events?type=sent&from&to`). Drawer contact mode from `/api/contacts/{id}`: facts grid (Company, Role, Segment, Timezone + local time, Status, Step, Mailbox, Emails sent, Next action IST + local, Suppressed, On hold), action buttons (review actions when held, Suppress when not suppressed), expandable timeline (sent: subject/template/model/List-Unsubscribe + body; reply: text + classification + by; soft bounce: attempts with SMTP codes; human actions).
- [ ] **Step 2:** Verify: filter `Follow-up 2 sent` ⇒ every row matches; open a contact ⇒ timeline shows 3 sends with bodies; Suppress ⇒ timeline gains a Human action and the fact flips to "Yes · manual".
- [ ] **Step 3:** Commit `feat(web): contacts and drawer`.

### Task 20: Review and Mailboxes tabs

**Files:** Create `web/js/tabs/review.js`, `web/js/tabs/mailboxes.js`

- [ ] **Step 1:** Review: Unresolved/Resolved/All, cards as mockup (reason labels "Repeated soft bounce", "Policy rejection", "Unsure classification", "Content blocked"; attempts; waiting time in sim time; SMTP code or reply text); actions call `/api/reviews/{id}/resolve` then refresh. Mailboxes: table (Mailbox, Warm-up day, Used today/limit bar, Tomorrow, Max, Bounce, Complaint, State + reason + Resume), 28-day warm-up charts (dashed limit line, sent bars) from `series`.
- [ ] **Step 2:** Verify: resolving an item moves it to Resolved and decrements the tab badge; Resume flips state to Active.
- [ ] **Step 3:** Commit `feat(web): review and mailboxes`.

### Task 21: AI & Templates and Logs tabs

**Files:** Create `web/js/tabs/ai.js`, `web/js/tabs/logs.js`

- [ ] **Step 1:** AI: 8 KPIs (Calls, Successes, 429 errors, Retries, Fallback to next model, Fallback to static, Template cache hits, Avg latency), per-model table, template grid 5 segments × 4 steps with AI/Static dot, A/B variant toggle, preview pane (title, model, generated at IST, subject, body with merge fields visible). Logs: search, group chips, type select, table (Time IST, Type with colour dot, Contact, Detail), "Load more" via `before_id`, live tail polling `/api/events` every 3 s showing "N new events · Show" without shifting rows; rows open the contact drawer.
- [ ] **Step 2:** Verify: with no key, Fallback to static > 0 or template cells show Static; live tail during a running +1d job shows the new-events pill.
- [ ] **Step 3:** Commit `feat(web): AI and logs tabs`.

---

# Phase 4 — Wiring, full run, README

### Task 22: End-to-end wiring pass

**Files:** Modify whatever Tasks 17–21 touched; `src/coldemailer/api.py` if shapes need adjusting.

- [ ] **Step 1:** Fresh clone simulation: delete `data/coldemailer.db`, `seed`, `serve`, then in the browser: +7d (job runs, progress shows, all tabs refresh when done), resolve one review, resume a paused mailbox, suppress a contact, run verify — every action reflected without a page reload; no console errors; `python -m coldemailer verify` passes afterwards.
- [ ] **Step 2:** Fix issues found; add an API test to `tests/test_api.py` for any backend bug fixed.
- [ ] **Step 3:** `pytest -q` passes. Commit `fix: end-to-end wiring`.

### Task 23: 10k and 100k runs, capacity tuning, template seed file

**Files:** Create `scripts/full_run.py`; Modify `src/coldemailer/config.py` (only if tuning needed); Create `data/templates.seed.json` (needs the user's OpenRouter key)

- [ ] **Step 1:** `scripts/full_run.py --n 10000 --days 60` — seeds, advances day by day, prints per-phase wall times and final `report` + `verify`; writes `docs/results/run-10k.md` (machine info: CPU, OS, Python; times; final counts by status; reply/bounce rates; sends total; deferred totals; verify result).
- [ ] **Step 2:** Check the campaign finishes: after 60 days, `new` + active statuses ≈ 0 (only OOO stragglers). If not, tune mailbox count/ramp in `config.py` (spec §15a target ≤ 60 days) and rerun.
- [ ] **Step 3:** `--n 100000 --days 60` → `docs/results/run-100k.md`. Investigate any phase > 10 min with `EXPLAIN QUERY PLAN` on the due scan and report queries.
- [ ] **Step 4:** With the user's key: `templates warm` then `templates export`; reseed and confirm the AI & Templates grid shows AI cells. Without a key, stop and ask the user for one.
- [ ] **Step 5:** Commit `chore: full-scale runs and template seed`.

### Task 24: README

**Files:** Modify `README.md`

- [ ] **Step 1:** Write for a person (spec §1 and the brief's list), in this order: what it does (one paragraph); quickstart in ≤ 6 commands (venv, install, `cp .env.example .env`, `seed`, `advance --days 14`, `report`, `serve` → `http://localhost:8000`), Windows and macOS/Linux variants; CLI reference; architecture diagram (one SVG or ASCII) + data model table + why SQLite/FastAPI/no-build React; lifecycle rules incl. OOO deviation, soft-bounce review, suppression; how double sends are prevented (UNIQUE, claim lease, idempotency key, re-check, crash recovery, run lock); the simulation (`sim_events`, clock); where AI is used, models, rate-limit design and fallbacks, classifier accuracy from the run; bonus features (warm-up, pacing, windows, domain caps, auto-pause, content checks, List-Unsubscribe); results from `docs/results/*.md`; tests (`pytest -q`, count, runtime); trade-offs, known limitations (single writer, OFFSET paging, wall-time lock TTL, mocked deliverability), what I'd build next; link to the design doc and plan ("how we got here").
- [ ] **Step 2:** Follow the README literally in a fresh clone directory (`git clone . ../coldemailer-check`) and confirm every command works as written.
- [ ] **Step 3:** Commit `docs: README`. Ask the user before pushing.
