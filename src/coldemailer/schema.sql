-- coldemailer schema. All timestamps are UTC text 'YYYY-MM-DDTHH:MM:SSZ' (lexical order = time order).
-- IST calendar days are stored as 'YYYY-MM-DD'.

CREATE TABLE IF NOT EXISTS clock (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    now         TEXT NOT NULL,
    started_at  TEXT NOT NULL,           -- simulation start, for "advanced N days"
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_lock (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    owner       TEXT,
    expires_at  REAL                     -- wall-clock epoch seconds; guards real processes only
);

CREATE TABLE IF NOT EXISTS campaign (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL,
    waits_days        TEXT NOT NULL,      -- JSON, e.g. [3,5,7,7]
    window_start      TEXT NOT NULL,      -- prospect local 'HH:MM'
    window_end        TEXT NOT NULL,
    domain_daily_cap  INTEGER NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mailboxes (
    id                 INTEGER PRIMARY KEY,
    address            TEXT NOT NULL UNIQUE,
    warmup_started_on  TEXT NOT NULL,     -- IST day
    start_limit        INTEGER NOT NULL,
    daily_increment    INTEGER NOT NULL,
    max_limit          INTEGER NOT NULL,
    paused             INTEGER NOT NULL DEFAULT 0,
    paused_reason      TEXT,
    paused_at          TEXT,
    last_sent_at       TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    id                INTEGER PRIMARY KEY,
    email             TEXT NOT NULL UNIQUE,
    first_name        TEXT NOT NULL,
    last_name         TEXT NOT NULL,
    company           TEXT,
    domain            TEXT NOT NULL,
    role              TEXT,
    industry          TEXT NOT NULL,
    segment           TEXT NOT NULL,
    variant           TEXT NOT NULL DEFAULT 'A',
    timezone          TEXT NOT NULL,
    status            TEXT NOT NULL,      -- new|first_sent|follow_up_N_sent|replied|bounced|unsubscribed|deactivated
    step              INTEGER NOT NULL DEFAULT 0,   -- number of steps sent (0-4)
    next_action       TEXT,               -- send_1..send_4 | deactivate | NULL
    next_action_at    TEXT,
    mailbox_id        INTEGER REFERENCES mailboxes(id),
    hold_reason       TEXT,               -- repeated_soft_bounce | policy_rejection | content_blocked
    ooo_until         TEXT,
    claimed_by        TEXT,
    claim_expires_at  TEXT,
    source            TEXT NOT NULL DEFAULT 'engine',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS templates (
    id          INTEGER PRIMARY KEY,
    segment     TEXT NOT NULL,            -- industry, or '*' for static fallbacks
    step        INTEGER NOT NULL,
    variant     TEXT NOT NULL,
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    source      TEXT NOT NULL,            -- static | llm:<model>
    created_at  TEXT NOT NULL,
    UNIQUE (segment, step, variant)
);

CREATE TABLE IF NOT EXISTS sends (
    id                   INTEGER PRIMARY KEY,
    contact_id           INTEGER NOT NULL REFERENCES contacts(id),
    step                 INTEGER NOT NULL,
    mailbox_id           INTEGER REFERENCES mailboxes(id),
    template_id          INTEGER REFERENCES templates(id),
    subject              TEXT,
    body                 TEXT,
    headers              TEXT,            -- JSON
    idempotency_key      TEXT UNIQUE,     -- key of the accepted (or latest) attempt
    state                TEXT NOT NULL,   -- claimed|sent|bounced|soft_bounced|held|blocked
    source               TEXT NOT NULL DEFAULT 'engine',   -- engine | import
    claimed_at           TEXT,
    sent_at              TEXT,
    provider_message_id  TEXT,
    UNIQUE (contact_id, step)
);

CREATE TABLE IF NOT EXISTS send_attempts (
    id               INTEGER PRIMARY KEY,
    send_id          INTEGER NOT NULL REFERENCES sends(id),
    attempt          INTEGER NOT NULL,
    idempotency_key  TEXT NOT NULL UNIQUE,
    result           TEXT,               -- pending|accepted|hard_bounce|soft_bounce
    smtp_code        TEXT,
    at               TEXT NOT NULL,
    UNIQUE (send_id, attempt)
);

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY,
    contact_id   INTEGER REFERENCES contacts(id),   -- NULL for system events
    send_id      INTEGER REFERENCES sends(id),
    mailbox_id   INTEGER REFERENCES mailboxes(id),
    type         TEXT NOT NULL,
    detail       TEXT,                    -- JSON
    occurred_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS replies (
    id              INTEGER PRIMARY KEY,
    contact_id      INTEGER NOT NULL REFERENCES contacts(id),
    send_id         INTEGER REFERENCES sends(id),
    sim_event_id    INTEGER,
    body            TEXT NOT NULL,
    received_at     TEXT NOT NULL,
    classification  TEXT,                 -- interested|not_interested|out_of_office|unsubscribe
    classified_by   TEXT,                 -- rule|llm:<model>|fallback|human
    classified_at   TEXT,
    llm_attempts    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS suppression (
    email            TEXT PRIMARY KEY,
    reason           TEXT NOT NULL,       -- hard_bounce|unsubscribe_link|unsubscribe_reply|complaint|not_interested|manual
    source_event_id  INTEGER,
    added_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_items (
    id           INTEGER PRIMARY KEY,
    contact_id   INTEGER NOT NULL REFERENCES contacts(id),
    send_id      INTEGER REFERENCES sends(id),
    reply_id     INTEGER REFERENCES replies(id),
    kind         TEXT NOT NULL,           -- soft_bounce|policy|content|classification
    reason       TEXT NOT NULL,
    detail       TEXT,                    -- JSON
    created_at   TEXT NOT NULL,
    resolved_at  TEXT,
    resolution   TEXT,
    note         TEXT
);

CREATE TABLE IF NOT EXISTS mailbox_daily (
    mailbox_id  INTEGER NOT NULL REFERENCES mailboxes(id),
    day         TEXT NOT NULL,
    sent        INTEGER NOT NULL DEFAULT 0,
    bounced     INTEGER NOT NULL DEFAULT 0,
    complaints  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (mailbox_id, day)
);

CREATE TABLE IF NOT EXISTS domain_daily (
    domain  TEXT NOT NULL,
    day     TEXT NOT NULL,
    sent    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (domain, day)
);

-- The one table that simulates the outside world (mock provider + prospects' inboxes).
CREATE TABLE IF NOT EXISTS sim_events (
    id               INTEGER PRIMARY KEY,
    idempotency_key  TEXT NOT NULL,
    send_id          INTEGER,
    contact_id       INTEGER,
    kind             TEXT NOT NULL,       -- accepted|hard_bounce|soft_bounce|reply|unsubscribe_click|complaint
    smtp_code        TEXT,
    body             TEXT,
    true_label       TEXT,                -- ground truth; only the report reads it
    deliver_at       TEXT NOT NULL,
    processed_at     TEXT
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id           INTEGER PRIMARY KEY,
    model        TEXT NOT NULL,
    purpose      TEXT NOT NULL,           -- template|classify
    status       TEXT NOT NULL,           -- ok|rate_limited|error
    http_status  INTEGER,
    latency_ms   INTEGER,
    retry_of     INTEGER,
    at           TEXT NOT NULL            -- simulated time
);

CREATE TABLE IF NOT EXISTS cycles (
    id               INTEGER PRIMARY KEY,
    started_at_wall  TEXT NOT NULL,
    sim_from         TEXT NOT NULL,
    sim_to           TEXT NOT NULL,
    sent             INTEGER NOT NULL,
    deferred_json    TEXT NOT NULL,
    replies          INTEGER NOT NULL,
    bounces          INTEGER NOT NULL,
    deactivated      INTEGER NOT NULL,
    held             INTEGER NOT NULL,
    duration_ms      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL,
    status       TEXT NOT NULL,           -- running|done|error
    progress     INTEGER NOT NULL DEFAULT 0,
    total        INTEGER NOT NULL DEFAULT 0,
    result_json  TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    finished_at  TEXT
);

CREATE TABLE IF NOT EXISTS verify_runs (
    id           INTEGER PRIMARY KEY,
    sim_at       TEXT NOT NULL,
    ok           INTEGER NOT NULL,
    checks_json  TEXT NOT NULL
);

-- Indexes. The due scan is a range scan per action: (next_action = ?, next_action_at <= now).
CREATE INDEX IF NOT EXISTS ix_due ON contacts(next_action, next_action_at)
    WHERE next_action IS NOT NULL AND hold_reason IS NULL;
CREATE INDEX IF NOT EXISTS ix_contacts_status ON contacts(status);
CREATE INDEX IF NOT EXISTS ix_contacts_domain ON contacts(domain);
CREATE INDEX IF NOT EXISTS ix_contacts_hold ON contacts(hold_reason) WHERE hold_reason IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_contacts_ooo ON contacts(ooo_until) WHERE ooo_until IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_sends_sent_at ON sends(sent_at);
CREATE INDEX IF NOT EXISTS ix_sends_claimed ON sends(state) WHERE state = 'claimed';
CREATE INDEX IF NOT EXISTS ix_sends_mailbox ON sends(mailbox_id, sent_at);
CREATE INDEX IF NOT EXISTS ix_attempts_send ON send_attempts(send_id);
CREATE INDEX IF NOT EXISTS ix_events_type_time ON events(type, occurred_at);
CREATE INDEX IF NOT EXISTS ix_events_contact ON events(contact_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_events_time ON events(occurred_at);
CREATE INDEX IF NOT EXISTS ix_sim_pending ON sim_events(deliver_at) WHERE processed_at IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_sim_accepted ON sim_events(idempotency_key) WHERE kind = 'accepted';
CREATE INDEX IF NOT EXISTS ix_sim_contact ON sim_events(contact_id);
CREATE INDEX IF NOT EXISTS ix_replies_backlog ON replies(id) WHERE classification IS NULL;
CREATE INDEX IF NOT EXISTS ix_replies_contact ON replies(contact_id);
CREATE INDEX IF NOT EXISTS ix_review_open ON review_items(resolved_at);
CREATE INDEX IF NOT EXISTS ix_llm_at ON llm_calls(at);
