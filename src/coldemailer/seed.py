"""Deterministic seed: ~10k contacts with a random place in the sequence and matching history.

Seeded history is treated as data imported from the old spreadsheets (source='import'):
it follows the sequence waits and prospect windows, but not warm-up / pacing / domain caps.
History is built with the same lifecycle.schedule_next the engine uses, so a seeded contact
on follow-up 2 has a first email and follow-up 1 exactly where the engine would have put them.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, timedelta

from faker import Faker

from . import clock, db, lifecycle, timeutil
from .ai import templates
from .config import SEGMENTS
from .sim_corpus import reply_body

ROLES = ["VP Sales", "Head of Growth", "Founder", "Marketing Lead", "Ops Director", "CTO",
         "Head of Customer Success", "Finance Director"]
TIMEZONES = [("Asia/Kolkata", 30), ("America/New_York", 20), ("America/Los_Angeles", 10),
             ("Europe/London", 15), ("Europe/Berlin", 10), ("Asia/Singapore", 8), ("Australia/Sydney", 7)]
STATUS_MIX = [("new", 40), ("first_sent", 15), ("follow_up_1_sent", 10), ("follow_up_2_sent", 8),
              ("follow_up_3_sent", 7), ("replied", 8), ("bounced", 4), ("unsubscribed", 3), ("deactivated", 5)]
TLDS = [".com", ".io", ".co", ".net"]
BAD_COMPANY_NAMES = ["Oldmail Corp", "Defunct Labs", "Parked Site"]


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())[:20] or "company"


def _key(*parts) -> str:
    return hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()


def _status_list(n: int, r) -> list[str]:
    out: list[str] = []
    for status, pct in STATUS_MIX[1:]:
        out += [status] * round(n * pct / 100)
    out += ["new"] * (n - len(out))
    r.shuffle(out)
    return out


def _mailboxes(settings) -> list[dict]:
    start_day = timeutil.ist_day(settings.clock_start)
    rows = []
    for i in range(1, settings.mailbox_count + 1):
        stagger = (i * 7) % 21  # 0-20 days of warm-up already done
        rows.append({
            "id": i,
            "address": f"sales{i}@mail-{'abcde'[(i - 1) % 5]}.test",
            "warmup_started_on": timeutil.shift_day(start_day, -stagger),
            "start_limit": settings.warmup_start,
            "daily_increment": settings.warmup_increment,
            "max_limit": settings.warmup_max,
        })
    return rows


class _History:
    """Builds the send chain for one contact so that it ends where we want relative to the clock."""

    def __init__(self, settings):
        self.s = settings
        self.waits = list(settings.waits_days)
        self.C = settings.clock_start

    def chain(self, first_day: date, k: int, tz: str, cid: int, minute: int):
        times = [timeutil.window_time_on(first_day, tz, minute, self.s.window_start, self.s.window_end)]
        for step in range(1, k):
            times.append(lifecycle.schedule_next(times[-1], step, tz, self.waits, f"{cid}:{step}",
                                                 self.s.window_start, self.s.window_end))
        nxt = lifecycle.schedule_next(times[-1], k, tz, self.waits, f"{cid}:{k}",
                                      self.s.window_start, self.s.window_end)
        return times, nxt

    def build(self, k: int, tz: str, cid: int, offset_days: int, minute: int):
        """Chain of k sends whose next action lands `offset_days` from the clock start."""
        c_local = timeutil.local(self.C, tz).date()
        first = c_local - timedelta(days=60)
        times, nxt = self.chain(first, k, tz, cid, minute)
        target = c_local + timedelta(days=offset_days)
        first += target - timeutil.local(nxt, tz).date()
        times, nxt = self.chain(first, k, tz, cid, minute)
        while times[-1] >= self.C:  # last send must be in the past
            first -= timedelta(days=1)
            times, nxt = self.chain(first, k, tz, cid, minute)
        return times, nxt


def run(settings, n: int = 10000, seed: int = 42) -> dict:
    t0 = time.perf_counter()
    db.delete_db_files(settings.db_path)
    conn = db.connect(settings.db_path)
    db.init_db(conn)

    C = settings.clock_start
    camp = settings.campaign_id
    fake = Faker("en_US")
    fake.seed_instance(seed)
    R = lifecycle.rng(seed, "seed")
    hist = _History(settings)
    mailboxes = _mailboxes(settings)

    # Companies: several contacts per domain so the per-domain cap matters.
    companies, seen_domains = [], set(settings.bad_domains)
    for _ in range(max(1, n // 4)):
        name = fake.company()
        domain = _slug(name) + R.choice(TLDS)
        while domain in seen_domains:
            domain = _slug(name) + str(R.randrange(10, 99)) + R.choice(TLDS)
        seen_domains.add(domain)
        companies.append((name, domain, R.choice(SEGMENTS)))
    bad_companies = [(nm, d, R.choice(SEGMENTS)) for nm, d in zip(BAD_COMPANY_NAMES, settings.bad_domains)]

    statuses = _status_list(n, R)
    tz_names, tz_weights = zip(*TIMEZONES)

    contacts, sends, events, replies, suppression = [], [], [], [], []
    emails: set[str] = set()
    send_id = 0

    with db.tx(conn):
        clock.init(conn, C)
        conn.execute(
            "INSERT INTO campaign(id, name, waits_days, window_start, window_end, domain_daily_cap, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (camp, "Q4 outbound", json.dumps(list(settings.waits_days)), settings.window_start,
             settings.window_end, settings.domain_daily_cap, C))
        conn.executemany(
            "INSERT INTO mailboxes(id, address, warmup_started_on, start_limit, daily_increment, max_limit) "
            "VALUES (:id,:address,:warmup_started_on,:start_limit,:daily_increment,:max_limit)", mailboxes)
        templates.ensure_static(conn, C)
        templates.load_seed_file(conn)
        tpl_rows = {(r["segment"], r["step"], r["variant"]): r for r in conn.execute("SELECT * FROM templates")}

        def tpl_for(segment, step, variant):
            return tpl_rows.get((segment, step, variant)) or tpl_rows[("*", step, variant)]

        for i, status in enumerate(statuses):
            cid = i + 1
            cr = lifecycle.rng(seed, "contact", cid)
            company, domain, industry = (cr.choice(bad_companies) if cr.random() < 0.01
                                         else companies[cr.randrange(len(companies))])
            first, last = fake.first_name(), fake.last_name()
            base = f"{re.sub(r'[^a-z]', '', first.lower())}.{re.sub(r'[^a-z]', '', last.lower())}"
            email, k2 = f"{base}@{domain}", 2
            while email in emails:
                email, k2 = f"{base}{k2}@{domain}", k2 + 1
            emails.add(email)
            tz = cr.choices(tz_names, tz_weights)[0]
            mailbox_id = (cid - 1) % len(mailboxes) + 1
            mb = mailboxes[mailbox_id - 1]
            minute = cr.randrange(timeutil.window_minutes(settings.window_start, settings.window_end))
            contact = {
                "id": cid, "email": email, "first_name": first, "last_name": last, "company": company,
                "domain": domain, "role": cr.choice(ROLES), "industry": industry, "segment": industry,
                "variant": "A" if cr.random() < 0.5 else "B", "timezone": tz, "status": status, "step": 0,
                "next_action": None, "next_action_at": None, "mailbox_id": None, "source": "import",
                "created_at": None, "updated_at": C,
            }

            if status == "new":
                d = timeutil.local(C, tz).date() + timedelta(days=cr.randint(-1, 10))
                contact.update(next_action="send_1", created_at=timeutil.add(C, days=-cr.randint(1, 20)),
                               next_action_at=timeutil.window_time_on(d, tz, minute, settings.window_start,
                                                                      settings.window_end))
                contacts.append(contact)
                continue

            if status in lifecycle.STATUS_BY_STEP:
                k = lifecycle.STATUS_BY_STEP.index(status)
                offset = cr.randint(-2, min(7, settings.waits_days[k - 1]) - 1)
            elif status == "deactivated":
                k, offset = 4, cr.randint(-25, -1)
            elif status == "bounced":
                k, offset = (1 if cr.random() < 0.85 else 2), cr.randint(-30, -1)
            else:  # replied / unsubscribed
                k, offset = cr.choices([1, 2, 3, 4], [0.4, 0.3, 0.2, 0.1])[0], cr.randint(-30, -1)

            times, nxt = hist.build(k, tz, cid, offset, minute)
            contact.update(step=k, mailbox_id=mailbox_id, created_at=timeutil.add(times[0], days=-1))
            last_send_id = None
            for step, at in enumerate(times, start=1):
                send_id += 1
                last_send_id = send_id
                subject, body, headers = templates.render(tpl_for(industry, step, contact["variant"]),
                                                          contact, mb["address"], mailbox_id, camp)
                bounced = status == "bounced" and step == k
                sends.append((send_id, cid, step, mailbox_id, subject, body, json.dumps(headers),
                              _key(cid, step, camp, "import"), "bounced" if bounced else "sent", "import", at,
                              None if bounced else at))
                if not bounced:
                    events.append((cid, send_id, mailbox_id, "sent",
                                   json.dumps({"step": step, "mailbox": mb["address"], "source": "import"}), at))

            last = times[-1]
            if status in lifecycle.STATUS_BY_STEP:
                contact.update(next_action=lifecycle.next_action_after(k), next_action_at=nxt)
            elif status == "deactivated":
                events.append((cid, None, None, "deactivated", json.dumps({"after_step": 4}), nxt))
                contact["updated_at"] = nxt
            elif status == "bounced":
                contact["step"] = k - 1  # the bounced email never counted as a step sent
                at = timeutil.add(last, minutes=cr.randint(1, 10))
                events.append((cid, last_send_id, mailbox_id, "hard_bounced",
                               json.dumps({"smtp_code": "550 5.1.1", "message": "User unknown"}), at))
                suppression.append((email, "hard_bounce", at))
                contact["updated_at"] = at
            else:
                span = timeutil.seconds_between(last, min(nxt, C))
                at = timeutil.add(last, seconds=int(span * cr.uniform(0.05, 0.8)))
                if status == "replied":
                    label = "interested" if cr.random() < 0.5 else "not_interested"
                    replies.append((cid, last_send_id, reply_body(label, cr, first), at, label, "rule", at))
                    events.append((cid, last_send_id, mailbox_id, "replied", json.dumps({"label": label}), at))
                    if label == "not_interested":
                        suppression.append((email, "not_interested", at))
                else:
                    via_reply = cr.random() < 0.3
                    if via_reply:
                        replies.append((cid, last_send_id, reply_body("unsubscribe", cr, first), at,
                                        "unsubscribe", "rule", at))
                    events.append((cid, last_send_id, mailbox_id, "unsubscribed",
                                   json.dumps({"via": "reply" if via_reply else "link"}), at))
                    suppression.append((email, "unsubscribe_reply" if via_reply else "unsubscribe_link", at))
                contact["updated_at"] = at
            contacts.append(contact)

        conn.executemany(
            "INSERT INTO contacts(id, email, first_name, last_name, company, domain, role, industry, segment, "
            "variant, timezone, status, step, next_action, next_action_at, mailbox_id, source, created_at, updated_at) "
            "VALUES (:id,:email,:first_name,:last_name,:company,:domain,:role,:industry,:segment,:variant,"
            ":timezone,:status,:step,:next_action,:next_action_at,:mailbox_id,:source,:created_at,:updated_at)",
            contacts)
        conn.executemany(
            "INSERT INTO sends(id, contact_id, step, mailbox_id, subject, body, headers, idempotency_key, state, "
            "source, claimed_at, sent_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", sends)
        events.sort(key=lambda e: e[5])
        conn.executemany(
            "INSERT INTO events(contact_id, send_id, mailbox_id, type, detail, occurred_at) VALUES (?,?,?,?,?,?)",
            events)
        conn.executemany(
            "INSERT INTO replies(contact_id, send_id, body, received_at, classification, classified_by, "
            "classified_at) VALUES (?,?,?,?,?,?,?)", replies)
        conn.executemany("INSERT INTO suppression(email, reason, added_at) VALUES (?,?,?)", suppression)

    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) n FROM contacts GROUP BY status")}
    conn.close()
    return {"counts": counts, "contacts": n, "sends": len(sends), "events": len(events),
            "seconds": round(time.perf_counter() - t0, 2)}
