"""Read models for the CLI report, the API and the dashboard. Pure reads; IST days throughout."""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import clock, db, lifecycle, policies, provider_mock, timeutil
from .ai.classify import LABELS
from .engine import REVIEW_REASONS

STATUS_LABELS = {
    "new": "New", "first_sent": "First sent", "follow_up_1_sent": "Follow-up 1 sent",
    "follow_up_2_sent": "Follow-up 2 sent", "follow_up_3_sent": "Follow-up 3 sent", "replied": "Replied",
    "bounced": "Bounced", "unsubscribed": "Unsubscribed", "deactivated": "Deactivated",
}
ACTION_LABELS = {"send_1": "First email", "send_2": "Follow-up 1", "send_3": "Follow-up 2",
                 "send_4": "Follow-up 3", "deactivate": "Deactivate"}
DEFER_LABELS = {"outside_window": "Outside prospect sending window", "domain_cap": "Domain cap reached",
                "mailbox_capacity": "Mailbox at capacity", "pacing": "Pacing gap", "mailbox_paused": "Mailbox paused"}
SUPPRESSION_LABELS = {"unsubscribe_link": "Unsubscribed via link", "unsubscribe_reply": "Unsubscribed via reply",
                      "hard_bounce": "Hard bounced", "complaint": "Complaints", "not_interested": "Not interested",
                      "manual": "Manual"}
EVENT_LABELS = {
    "sent": "Sent", "replied": "Reply", "ooo": "Out of office", "hard_bounced": "Hard bounce",
    "soft_bounced": "Soft bounce", "deferred": "Deferred", "content_blocked": "Content blocked",
    "needs_review": "Needs review", "unsubscribed": "Unsubscribe", "complaint": "Complaint",
    "deactivated": "Deactivated", "mailbox_paused": "Mailbox paused", "mailbox_resumed": "Mailbox resumed",
    "ai_call": "AI call", "ai_fallback": "AI fallback", "template_generated": "Template generated",
    "classified": "Classified", "error": "Error", "human_action": "Human action",
}
EVENT_GROUPS = {
    "Email": ["sent", "replied", "ooo", "hard_bounced", "soft_bounced", "deferred", "content_blocked"],
    "Contact": ["needs_review", "unsubscribed", "complaint", "deactivated", "classified"],
    "Mailbox": ["mailbox_paused", "mailbox_resumed"],
    "AI": ["ai_call", "ai_fallback", "template_generated"],
    "System": ["error"],
    "Human": ["human_action"],
}
LABEL_TEXT = {"interested": "Interested", "not_interested": "Not interested", "out_of_office": "Out of office",
              "unsubscribe": "Unsubscribe"}


def _one(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()[0]


def _range(conn, day_from: str | None, day_to: str | None) -> tuple[str, str, str, str]:
    today = timeutil.ist_day(clock.now(conn))
    day_to = min(day_to or today, today)
    day_from = day_from or timeutil.shift_day(day_to, -6)
    return day_from, day_to, timeutil.ist_day_bounds(day_from)[0], timeutil.ist_day_bounds(day_to)[1]


def _days(day_from: str, day_to: str) -> list[str]:
    a, b = date.fromisoformat(day_from), date.fromisoformat(day_to)
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def clock_info(conn) -> dict:
    if not db.is_initialised(conn):
        return {"initialised": False}
    now, start = clock.now(conn), clock.started_at(conn)
    return {"initialised": True, "now": now, "now_ist": timeutil.fmt_ist(now), "today": timeutil.ist_day(now),
            "started_at": start, "advanced_hours": round(timeutil.seconds_between(start, now) / 3600, 2),
            "running": clock.lock_holder(conn)}


def campaign(conn, settings) -> dict:
    row = conn.execute("SELECT * FROM campaign LIMIT 1").fetchone()
    return {
        "name": row["name"], "waits_days": json.loads(row["waits_days"]),
        "window": f"{row['window_start']}–{row['window_end']} prospect local time",
        "domain_daily_cap": row["domain_daily_cap"], "mailboxes": _one(conn, "SELECT COUNT(*) FROM mailboxes"),
        "warmup": f"{settings.warmup_start}/day, +{settings.warmup_increment}/day, max {settings.warmup_max}",
        "mock_rates": {"hard_bounce": settings.hard_bounce_rate, "soft_bounce": settings.soft_bounce_rate,
                       "complaint": settings.complaint_rate, "unsubscribe_click": settings.unsub_click_rate,
                       "reply_by_step": list(settings.reply_rates)},
        "autopause": f"bounce > {settings.autopause_bounce:.0%} over last {settings.autopause_window} sends, "
                     f"complaints > {settings.autopause_complaint:.1%}",
    }


def overview(conn, settings, day_from: str | None = None, day_to: str | None = None) -> dict:
    if not db.is_initialised(conn):
        return {"initialised": False}
    now = clock.now(conn)
    today = timeutil.ist_day(now)
    t0, t1 = timeutil.ist_day_bounds(today)
    m1 = timeutil.ist_day_bounds(timeutil.shift_day(today, 1))[1]
    day_from, day_to, r0, r1 = _range(conn, day_from, day_to)

    counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM contacts GROUP BY status")}
    total = sum(counts.values())
    statuses = [{"key": k, "label": v, "count": counts.get(k, 0)} for k, v in STATUS_LABELS.items()]

    sent_range = _one(conn, "SELECT COUNT(*) FROM events WHERE type='sent' AND occurred_at>=? AND occurred_at<?", (r0, r1))
    sent_today = _one(conn, "SELECT COUNT(*) FROM events WHERE type='sent' AND occurred_at>=? AND occurred_at<?", (t0, t1))
    hard_range = _one(conn, "SELECT COUNT(*) FROM events WHERE type='hard_bounced' AND occurred_at>=? AND occurred_at<?",
                      (r0, r1))
    mbs = conn.execute("SELECT * FROM mailboxes").fetchall()
    capacity = sum(policies.warmup_limit(m, d) for d in _days(day_from, day_to) for m in mbs)
    contacted = _one(conn, "SELECT COUNT(DISTINCT contact_id) FROM sends WHERE state='sent'")
    genuine = counts.get("replied", 0)  # the same set the "Replied" drawer lists
    due = conn.execute(
        "SELECT next_action a, SUM(next_action_at < ?) today, SUM(next_action_at >= ? AND next_action_at < ?) tmrw "
        "FROM contacts WHERE next_action IS NOT NULL AND hold_reason IS NULL GROUP BY next_action", (t1, t1, m1))
    due = {r["a"]: (r["today"] or 0, r["tmrw"] or 0) for r in due}
    open_reviews = _one(conn, "SELECT COUNT(*) FROM review_items WHERE resolved_at IS NULL")

    kpis = {
        "sent_today": sent_today, "sent_range": sent_range, "capacity_range": capacity, "range_days": len(_days(day_from, day_to)),
        "due_today": sum(v[0] for v in due.values()), "due_tomorrow": sum(v[1] for v in due.values()),
        "replied": genuine, "contacted": contacted, "reply_rate": genuine / contacted if contacted else 0,
        "hard_bounces": hard_range, "sends_range": sent_range + hard_range,
        "bounce_rate": hard_range / (sent_range + hard_range) if sent_range + hard_range else 0,
        "deactivated": counts.get("deactivated", 0), "unsubscribed": counts.get("unsubscribed", 0),
        "needs_review": open_reviews, "total": total,
    }

    cls = {r["k"]: r["n"] for r in conn.execute(
        "SELECT COALESCE(classification, 'pending') k, COUNT(*) n FROM replies GROUP BY 1")}
    by = {r["k"]: r["n"] for r in conn.execute(
        "SELECT CASE WHEN classified_by LIKE 'llm:%' THEN 'llm' ELSE COALESCE(classified_by, 'pending') END k, "
        "COUNT(*) n FROM replies GROUP BY 1")}
    acc = conn.execute(
        "SELECT COUNT(*) n, SUM(r.classification = s.true_label) ok FROM replies r "
        "JOIN sim_events s ON s.id=r.sim_event_id WHERE r.classification IS NOT NULL").fetchone()

    hours = [{"hour": h, "steps": [0, 0, 0, 0]} for h in range(24)]
    for r in conn.execute("SELECT sent_at, step FROM sends WHERE state='sent' AND sent_at>=? AND sent_at<?", (t0, t1)):
        hours[timeutil.from_ts(r["sent_at"]).astimezone(timeutil.IST).hour]["steps"][r["step"] - 1] += 1
    deferred = {r["k"]: r["n"] for r in conn.execute(
        "SELECT json_extract(detail, '$.reason') k, COUNT(*) n FROM events "
        "WHERE type='deferred' AND occurred_at>=? AND occurred_at<? GROUP BY 1", (t0, t1))}
    sup = {r["reason"]: r["n"] for r in conn.execute("SELECT reason, COUNT(*) n FROM suppression GROUP BY reason")}
    engine_sends = _one(conn, "SELECT COUNT(*) FROM sends WHERE source='engine' AND state='sent'")
    with_header = _one(conn, "SELECT COUNT(*) FROM sends WHERE source='engine' AND state='sent' "
                             "AND headers LIKE '%List-Unsubscribe%'")
    vr = conn.execute("SELECT * FROM verify_runs ORDER BY id DESC LIMIT 1").fetchone()
    cy = conn.execute("SELECT * FROM cycles ORDER BY id DESC LIMIT 1").fetchone()

    return {
        "initialised": True, "clock": clock_info(conn), "range": {"from": day_from, "to": day_to},
        "campaign": campaign(conn, settings), "kpis": kpis, "statuses": statuses,
        "markers": {"on_hold": _one(conn, "SELECT COUNT(*) FROM contacts WHERE hold_reason IS NOT NULL"),
                    "paused_ooo": _one(conn, "SELECT COUNT(*) FROM contacts WHERE ooo_until > ? "
                                             "AND next_action IS NOT NULL", (now,))},
        "replies": [{"key": k, "label": LABEL_TEXT[k], "count": cls.get(k, 0)} for k in LABELS],
        "classifiers": {k: by.get(k, 0) for k in ("rule", "llm", "fallback", "human", "pending")},
        "classifier_accuracy": {"checked": acc["n"], "correct": acc["ok"] or 0,
                                "rate": (acc["ok"] or 0) / acc["n"] if acc["n"] else None},
        "hours": hours,
        "deferred": [{"key": k, "label": v, "count": deferred.get(k, 0)} for k, v in DEFER_LABELS.items()],
        "due": [{"key": k, "label": v, "today": due.get(k, (0, 0))[0], "tomorrow": due.get(k, (0, 0))[1]}
                for k, v in ACTION_LABELS.items()],
        "suppression": {
            "total": sum(sup.values()),
            "by_reason": [{"key": k, "label": v, "count": sup.get(k, 0)} for k, v in SUPPRESSION_LABELS.items()],
            "added_today": _one(conn, "SELECT COUNT(*) FROM suppression WHERE added_at>=? AND added_at<?", (t0, t1)),
            "list_unsubscribe_coverage": with_header / engine_sends if engine_sends else 1.0,
        },
        "health": {
            "verify": None if vr is None else {"ok": bool(vr["ok"]), "at": vr["sim_at"],
                                               "checks": json.loads(vr["checks_json"])},
            "content_blocks": _one(conn, "SELECT COUNT(*) FROM events WHERE type='content_blocked'"),
        },
        "cycle": None if cy is None else {**dict(cy), "deferred": json.loads(cy["deferred_json"]),
                                          "at_ist": timeutil.fmt_ist(cy["sim_to"])},
    }


# ------------------------------------------------------------------ contacts

def _contact_row(r, mb_addr: dict) -> dict:
    return {
        "id": r["id"], "name": f"{r['first_name']} {r['last_name']}", "email": r["email"], "company": r["company"],
        "role": r["role"], "segment": r["segment"], "timezone": r["timezone"], "status": r["status"],
        "status_label": STATUS_LABELS[r["status"]], "step": r["step"],
        "step_label": lifecycle.STEP_NAMES[r["step"] - 1] if r["step"] else None,
        "next_action": r["next_action"], "next_action_label": ACTION_LABELS.get(r["next_action"]),
        "next_action_at": r["next_action_at"], "next_action_ist": timeutil.fmt_ist(r["next_action_at"]),
        "mailbox": mb_addr.get(r["mailbox_id"]), "hold_reason": r["hold_reason"],
        "hold_label": REVIEW_REASONS.get(r["hold_reason"]) if r["hold_reason"] else None,
    }


def contacts(conn, filters: dict, page: int = 0, size: int = 25) -> dict:
    if not db.is_initialised(conn):
        return {"total": 0, "rows": [], "initialised": False}
    where, args = [], []
    for key in ("status", "segment", "timezone"):
        if filters.get(key):
            where.append(f"{key}=?")
            args.append(filters[key])
    if filters.get("step") not in (None, ""):
        where.append("step=?")
        args.append(int(filters["step"]))
    if filters.get("mailbox_id"):
        where.append("mailbox_id=?")
        args.append(int(filters["mailbox_id"]))
    if filters.get("on_hold") in ("yes", "no"):
        where.append("hold_reason IS NOT NULL" if filters["on_hold"] == "yes" else "hold_reason IS NULL")
    if filters.get("due") in ("today", "tomorrow"):
        today = timeutil.ist_day(clock.now(conn))
        t1 = timeutil.ist_day_bounds(today)[1]
        where.append("next_action IS NOT NULL AND hold_reason IS NULL AND next_action_at < ?"
                     + (" AND next_action_at >= ?" if filters["due"] == "tomorrow" else ""))
        args += [timeutil.ist_day_bounds(timeutil.shift_day(today, 1))[1], t1] if filters["due"] == "tomorrow" else [t1]
    if filters.get("ooo") == "yes":
        where.append("ooo_until > ? AND next_action IS NOT NULL")
        args.append(clock.now(conn))
    if filters.get("q"):
        where.append("(email LIKE ? OR company LIKE ? OR first_name || ' ' || last_name LIKE ?)")
        args += [f"%{filters['q']}%"] * 3
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    size = max(1, min(int(size), 200))
    total = _one(conn, f"SELECT COUNT(*) FROM contacts {clause}", args)
    rows = conn.execute(f"SELECT * FROM contacts {clause} ORDER BY id LIMIT ? OFFSET ?",
                        (*args, size, max(0, int(page)) * size)).fetchall()
    mb = {m["id"]: m["address"] for m in conn.execute("SELECT id, address FROM mailboxes")}
    return {"total": total, "page": page, "size": size, "rows": [_contact_row(r, mb) for r in rows]}


def search(conn, q: str, limit: int = 6) -> list[dict]:
    if not db.is_initialised(conn) or len(q.strip()) < 2:
        return []
    q = q.strip()
    rows = conn.execute("SELECT * FROM contacts WHERE email LIKE ? ORDER BY email LIMIT ?", (f"{q}%", limit)).fetchall()
    if len(rows) < limit:
        seen = {r["id"] for r in rows}
        more = conn.execute(
            "SELECT * FROM contacts WHERE email LIKE ? OR company LIKE ? OR first_name || ' ' || last_name LIKE ? "
            "LIMIT ?", (f"%{q}%", f"%{q}%", f"%{q}%", limit * 2)).fetchall()
        rows += [r for r in more if r["id"] not in seen][: limit - len(rows)]
    return [{"id": r["id"], "name": f"{r['first_name']} {r['last_name']}", "email": r["email"],
             "company": r["company"]} for r in rows]


def describe(type_: str, d: dict) -> str:
    """One readable line per event, shared by the logs table and the contact timeline."""
    step = lifecycle.STEP_NAMES[d["step"] - 1] if d.get("step") else ""
    if type_ == "sent":
        return f"{step} sent via {d.get('mailbox', '')}".strip()
    if type_ == "replied":
        return f"Reply, classified {LABEL_TEXT.get(d.get('label'), 'later')}" if d.get("label") else "Reply received"
    if type_ == "ooo":
        when = d.get("return_date") or "no date given"
        return f"Out of office (return: {when}); next step moved to {timeutil.fmt_ist(d.get('resumes_at'))} IST"
    if type_ == "hard_bounced":
        return f"{d.get('smtp_code', '550')} {d.get('message', 'hard bounce')}, marked bounced" if d.get("via") != "manual" \
            else "Marked bounced by operator"
    if type_ == "soft_bounced":
        return f"{d.get('smtp_code')} {d.get('message')} (attempt {d.get('attempt')})"
    if type_ == "deferred":
        return f"{step + ': ' if step else ''}{DEFER_LABELS.get(d.get('reason'), d.get('reason'))}"
    if type_ == "content_blocked":
        return "Blocked by content check: " + ", ".join(d.get("reasons", []))
    if type_ == "needs_review":
        return f"Placed in review: {d.get('reason', '')}"
    if type_ == "unsubscribed":
        return {"link": "Unsubscribed via link", "reply": "Unsubscribed via reply",
                "manual": "Suppressed by operator"}.get(d.get("via"), "Unsubscribed")
    if type_ == "complaint":
        return "Spam complaint received, suppressed"
    if type_ == "deactivated":
        return "No reply after follow-up 3, deactivated"
    if type_ in ("mailbox_paused", "mailbox_resumed"):
        return f"{d.get('mailbox')} {'paused: ' + d.get('reason', '') if type_ == 'mailbox_paused' else 'resumed by operator'}"
    if type_ == "ai_call":
        return f"{d.get('purpose', '').title()} · {d.get('model')} · {d.get('status')}" + \
               (f" · {d.get('latency_ms') / 1000:.1f}s" if d.get("latency_ms") else "")
    if type_ == "ai_fallback":
        return f"All models failed or rate limited ({d.get('purpose')}); used fallback"
    if type_ == "template_generated":
        return f"Template generated: {d.get('segment')} · {lifecycle.STEP_NAMES[d.get('step', 1) - 1]} · " \
               f"variant {d.get('variant')} ({d.get('model')})"
    if type_ == "classified":
        by = d.get("by", "")
        return f"Classified {LABEL_TEXT.get(d.get('label'), d.get('label'))} by " + \
               ("AI" if by.startswith("llm:") else by)
    if type_ == "human_action":
        return d.get("action", "Operator action")
    return json.dumps(d)


def contact_detail(conn, settings, contact_id: int) -> dict | None:
    c = conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if c is None:
        return None
    now = clock.now(conn)
    mb = {m["id"]: m["address"] for m in conn.execute("SELECT id, address FROM mailboxes")}
    sup = conn.execute("SELECT reason FROM suppression WHERE email=?", (c["email"],)).fetchone()
    local_now = timeutil.local(now, c["timezone"]).strftime("%H:%M")
    nxt_local = timeutil.local(c["next_action_at"], c["timezone"]).strftime("%b %d, %H:%M") \
        if c["next_action_at"] else None
    sends = {s["id"]: s for s in conn.execute(
        "SELECT s.*, t.segment t_segment, t.step t_step, t.variant t_variant, t.source t_source FROM sends s "
        "LEFT JOIN templates t ON t.id=s.template_id WHERE s.contact_id=?", (contact_id,))}
    replies = {r["received_at"]: r for r in conn.execute("SELECT * FROM replies WHERE contact_id=?", (contact_id,))}
    timeline = []
    for e in conn.execute("SELECT * FROM events WHERE contact_id=? ORDER BY occurred_at, id", (contact_id,)):
        d = json.loads(e["detail"]) if e["detail"] else {}
        item = {"id": e["id"], "type": e["type"], "label": EVENT_LABELS.get(e["type"], e["type"]),
                "at": e["occurred_at"], "at_ist": timeutil.fmt_ist(e["occurred_at"]),
                "text": describe(e["type"], d), "fields": [], "body": None}
        s = sends.get(e["send_id"])
        if e["type"] == "sent" and s:
            model = (s["t_source"] or "").removeprefix("llm:") if s["t_source"] else None
            headers = json.loads(s["headers"]) if s["headers"] else {}
            item["fields"] = [{"k": "Subject", "v": s["subject"]},
                              {"k": "Template", "v": f"{s['t_segment']} · {lifecycle.STEP_NAMES[s['step'] - 1]} · "
                                                     f"variant {s['t_variant']}" if s["t_segment"] else "imported"},
                              {"k": "Model", "v": model or ("static template" if s["t_source"] else "imported")},
                              {"k": "List-Unsubscribe", "v": headers.get("List-Unsubscribe", "—")}]
            item["body"] = s["body"]
        elif e["type"] in ("replied", "ooo") and e["occurred_at"] in replies:
            r = replies[e["occurred_at"]]
            by = r["classified_by"] or "pending"
            item["fields"] = [{"k": "Classification", "v": LABEL_TEXT.get(r["classification"], "Pending")},
                              {"k": "Classified by", "v": "AI · " + by[4:] if by.startswith("llm:") else by}]
            item["body"] = r["body"]
        elif e["type"] == "soft_bounced" and s:
            item["fields"] = [{"k": f"Attempt {a['attempt']}", "v": f"{a['smtp_code'] or ''} · {a['result']}"}
                              for a in conn.execute("SELECT * FROM send_attempts WHERE send_id=? ORDER BY attempt",
                                                    (s["id"],))]
        timeline.append(item)
    reviews = [_review_row(conn, r, now) for r in conn.execute(
        "SELECT r.*, c.email, c.first_name, c.last_name, c.company FROM review_items r "
        "JOIN contacts c ON c.id=r.contact_id WHERE r.contact_id=? AND r.resolved_at IS NULL", (contact_id,))]
    facts = [
        {"k": "Company", "v": c["company"]}, {"k": "Role", "v": c["role"]}, {"k": "Segment", "v": c["segment"]},
        {"k": "Timezone", "v": f"{c['timezone']} · local {local_now}"},
        {"k": "Status", "v": STATUS_LABELS[c["status"]]},
        {"k": "Step", "v": lifecycle.STEP_NAMES[c["step"] - 1] if c["step"] else "Not started"},
        {"k": "Mailbox", "v": mb.get(c["mailbox_id"], "—")},
        {"k": "Emails sent", "v": str(_one(conn, "SELECT COUNT(*) FROM sends WHERE contact_id=? AND state='sent'",
                                           (contact_id,)))},
        {"k": "Next action", "v": f"{ACTION_LABELS[c['next_action']]}\n{timeutil.fmt_ist(c['next_action_at'])} IST\n"
                                  f"{nxt_local} local" if c["next_action"] else "—"},
        {"k": "Suppressed", "v": f"Yes · {SUPPRESSION_LABELS.get(sup['reason'], sup['reason'])}" if sup else "No"},
        {"k": "On hold", "v": f"Yes · {REVIEW_REASONS.get(c['hold_reason'])}" if c["hold_reason"] else "No"},
    ]
    return {"contact": _contact_row(c, mb), "facts": facts, "timeline": timeline, "reviews": reviews,
            "suppressed": bool(sup)}


# ------------------------------------------------------------------ logs, reviews, mailboxes, AI, templates

def events(conn, group: str | None = None, type_: str | None = None, q: str | None = None,
           day_from: str | None = None, day_to: str | None = None, limit: int = 40,
           before: str | None = None) -> dict:
    if not db.is_initialised(conn):
        return {"rows": [], "next": None, "total": 0}
    day_from, day_to, r0, r1 = _range(conn, day_from, day_to)
    where, args = ["e.occurred_at >= ?", "e.occurred_at < ?"], [r0, r1]
    if type_:
        where.append("e.type = ?")
        args.append(type_)
    elif group and group in EVENT_GROUPS:
        where.append(f"e.type IN ({','.join('?' * len(EVENT_GROUPS[group]))})")
        args += EVENT_GROUPS[group]
    if q:
        where.append("(c.email LIKE ? OR c.company LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    clause = " AND ".join(where)
    total = _one(conn, f"SELECT COUNT(*) FROM events e LEFT JOIN contacts c ON c.id=e.contact_id WHERE {clause}", args)
    if before:
        at, eid = before.split("|")
        where.append("(e.occurred_at, e.id) < (?, ?)")
        args += [at, int(eid)]
    rows = conn.execute(
        f"SELECT e.*, c.email, c.company FROM events e LEFT JOIN contacts c ON c.id=e.contact_id "
        f"WHERE {' AND '.join(where)} ORDER BY e.occurred_at DESC, e.id DESC LIMIT ?", (*args, limit + 1)).fetchall()
    more = len(rows) > limit
    rows = rows[:limit]
    out = [{"id": r["id"], "type": r["type"], "label": EVENT_LABELS.get(r["type"], r["type"]),
            "at": r["occurred_at"], "at_ist": timeutil.fmt_ist(r["occurred_at"]), "contact_id": r["contact_id"],
            "email": r["email"] or ("system" if r["type"] != "human_action" else "operator"),
            "company": r["company"], "text": describe(r["type"], json.loads(r["detail"]) if r["detail"] else {})}
           for r in rows]
    return {"rows": out, "total": total, "next": f"{rows[-1]['occurred_at']}|{rows[-1]['id']}" if more else None,
            "range": {"from": day_from, "to": day_to}}


DEFER_HELP = {
    "outside_window": "It was outside 09:00-18:00 in the prospect's timezone. The send moves to their next window.",
    "domain_cap": "This company's domain already got its daily cap of emails, so the send waits for tomorrow.",
    "mailbox_capacity": "No mailbox this send could use had warm-up room left today. Follow-ups stay on the mailbox "
                        "that sent the first email, so a young mailbox that is full holds its follow-ups until "
                        "tomorrow's higher limit, even when other mailboxes have room.",
    "pacing": "The mailbox sent recently and must leave a gap between emails, so the send waits for the next slot.",
    "mailbox_paused": "Every mailbox that could send was paused. Resume one from the Mailboxes tab.",
}


def _source_text(source: str | None) -> str | None:
    if not source:
        return None
    if source.startswith("llm:"):
        return f"AI ({source[4:]})"
    return {"rule": "Rules", "fallback": "Fallback (AI unavailable)", "human": "Operator",
            "static": "Static template"}.get(source, source)


def event_detail(conn, settings, event_id: int) -> dict | None:
    """Everything behind one log row: the email that was sent, its attempts, any reply and how it
    was classified, and for deferrals the mailbox capacity that day."""
    e = conn.execute("SELECT e.*, c.email, c.company, c.first_name, c.last_name FROM events e "
                     "LEFT JOIN contacts c ON c.id=e.contact_id WHERE e.id=?", (event_id,)).fetchone()
    if e is None:
        return None
    d = json.loads(e["detail"]) if e["detail"] else {}
    out = {"id": e["id"], "type": e["type"], "label": EVENT_LABELS.get(e["type"], e["type"]),
           "at_ist": timeutil.fmt_ist(e["occurred_at"]), "text": describe(e["type"], d), "detail": d,
           "contact": {"id": e["contact_id"], "email": e["email"], "company": e["company"],
                       "name": f"{e['first_name'] or ''} {e['last_name'] or ''}".strip()} if e["contact_id"] else None,
           "email": None, "attempts": [], "replies": [], "capacity": None,
           "explain": DEFER_HELP.get(d.get("reason")) if e["type"] == "deferred" else None}
    if e["send_id"]:
        s = conn.execute("SELECT s.*, m.address mailbox, t.source template_source, t.segment, t.variant "
                         "FROM sends s LEFT JOIN mailboxes m ON m.id=s.mailbox_id "
                         "LEFT JOIN templates t ON t.id=s.template_id WHERE s.id=?", (e["send_id"],)).fetchone()
        if s:
            out["email"] = {"step": lifecycle.STEP_NAMES[s["step"] - 1], "subject": s["subject"], "body": s["body"],
                            "mailbox": s["mailbox"], "state": s["state"], "sent_at_ist": timeutil.fmt_ist(s["sent_at"]),
                            "imported": s["source"] == "import",
                            "template": _source_text(s["template_source"]),
                            "template_ref": f"{s['segment']} · variant {s['variant']}" if s["segment"] else None}
            out["attempts"] = [{"attempt": a["attempt"], "result": a["result"], "smtp_code": a["smtp_code"],
                                "at_ist": timeutil.fmt_ist(a["at"])} for a in conn.execute(
                "SELECT * FROM send_attempts WHERE send_id=? ORDER BY attempt", (e["send_id"],))]
            out["replies"] = [{"body": r["body"], "received_at_ist": timeutil.fmt_ist(r["received_at"]),
                               "label": LABEL_TEXT.get(r["classification"], r["classification"]),
                               "by": _source_text(r["classified_by"]),
                               "ai": bool(r["classified_by"] and r["classified_by"].startswith("llm:"))}
                              for r in conn.execute("SELECT * FROM replies WHERE send_id=? ORDER BY received_at",
                                                    (e["send_id"],))]
    if e["type"] == "deferred" and d.get("reason") in ("mailbox_capacity", "mailbox_paused"):
        # Deferrals are stamped at the end of their cycle, so the day is the one just before that instant.
        # Usage is as of now (end of that day if it's past); paused state is the current one.
        day = timeutil.ist_day(timeutil.add(e["occurred_at"], seconds=-1))
        used = {r["mailbox_id"]: r["sent"] for r in conn.execute(
            "SELECT mailbox_id, sent FROM mailbox_daily WHERE day=?", (day,))}
        mbs = conn.execute("SELECT * FROM mailboxes ORDER BY id").fetchall()
        active = [m for m in mbs if not m["paused"]]
        own = conn.execute("SELECT m.* FROM contacts c JOIN mailboxes m ON m.id=c.mailbox_id WHERE c.id=?",
                           (e["contact_id"],)).fetchone() if (d.get("step") or 1) > 1 else None
        out["capacity"] = {"day": day, "sent": sum(used.get(m["id"], 0) for m in active),
                           "limit": sum(policies.warmup_limit(m, day) for m in active),
                           "mailboxes": len(mbs), "paused_now": len(mbs) - len(active),
                           "own": {"address": own["address"], "sent": used.get(own["id"], 0),
                                   "limit": policies.warmup_limit(own, day), "paused": bool(own["paused"])}
                           if own else None}
    return out


def _ago(a: str, b: str) -> str:
    secs = max(0, int(timeutil.seconds_between(a, b)))
    d, h = secs // 86400, secs % 86400 // 3600
    return f"{d}d {h}h" if d else f"{h}h {secs % 3600 // 60}m"


def _review_row(conn, r, now: str) -> dict:
    d = json.loads(r["detail"]) if r["detail"] else {}
    if r["kind"] == "classification":
        text, actions = d.get("body", ""), [f"label:{k}" for k in LABELS]
    elif r["kind"] == "content":
        text, actions = "Content check: " + ", ".join(d.get("reasons", [])), ["retry_now", "retry_later", "suppress"]
    else:
        text = f"{d.get('smtp_code', '')} {d.get('message', '')}".strip()
        actions = ["retry_now", "retry_later", "mark_bounced", "suppress"]
    return {
        "id": r["id"], "contact_id": r["contact_id"], "name": f"{r['first_name']} {r['last_name']}",
        "email": r["email"], "company": r["company"], "kind": r["kind"], "reason": REVIEW_REASONS.get(r["reason"], r["reason"]),
        "attempts": len(d.get("attempts", [])) or (1 if r["kind"] in ("policy", "soft_bounce") else 0),
        "detail": text, "created_at": r["created_at"], "waiting": _ago(r["created_at"], r["resolved_at"] or now),
        "resolved": bool(r["resolved_at"]), "resolution": r["resolution"], "actions": [] if r["resolved_at"] else actions,
    }


def reviews(conn, state: str = "open") -> list[dict]:
    if not db.is_initialised(conn):
        return []
    cond = {"open": "r.resolved_at IS NULL", "resolved": "r.resolved_at IS NOT NULL"}.get(state, "1=1")
    now = clock.now(conn)
    return [_review_row(conn, r, now) for r in conn.execute(
        f"SELECT r.*, c.email, c.first_name, c.last_name, c.company FROM review_items r "
        f"JOIN contacts c ON c.id=r.contact_id WHERE {cond} ORDER BY r.resolved_at IS NOT NULL, r.id DESC LIMIT 500")]


def mailboxes(conn, settings) -> list[dict]:
    if not db.is_initialised(conn):
        return []
    today = timeutil.ist_day(clock.now(conn))
    out = []
    for m in conn.execute("SELECT * FROM mailboxes ORDER BY id"):
        start = m["warmup_started_on"]
        day_n = (date.fromisoformat(today) - date.fromisoformat(start)).days + 1
        used = {r["day"]: r for r in conn.execute("SELECT * FROM mailbox_daily WHERE mailbox_id=?", (m["id"],))}
        att = conn.execute(
            "SELECT a.result, a.smtp_code FROM send_attempts a JOIN sends s ON s.id=a.send_id WHERE s.mailbox_id=? "
            "AND a.result != 'pending' ORDER BY a.at DESC LIMIT ?", (m["id"], settings.autopause_window)).fetchall()
        bad = sum(1 for a in att if a["result"] == "hard_bounce"
                  or (a["result"] == "soft_bounce" and provider_mock.is_policy_code(a["smtp_code"])))
        complaints = sum(r["complaints"] for r in used.values())
        sent_total = sum(r["sent"] for r in used.values())
        series_days = [timeutil.shift_day(start, i) for i in range(28)]
        out.append({
            "id": m["id"], "address": m["address"], "warmup_day": day_n,
            "limit_today": policies.warmup_limit(m, today), "limit_tomorrow": policies.warmup_limit(m, timeutil.shift_day(today, 1)),
            "max": m["max_limit"], "used_today": used.get(today)["sent"] if today in used else 0,
            "bounce_rate": bad / len(att) if att else 0, "complaint_rate": complaints / sent_total if sent_total else 0,
            "paused": bool(m["paused"]), "paused_reason": m["paused_reason"],
            "series": [{"day": d, "limit": policies.warmup_limit(m, d),
                        "sent": used[d]["sent"] if d in used else 0, "future": d > today} for d in series_days],
        })
    return out


def ai_usage(conn, settings) -> dict:
    if not db.is_initialised(conn):
        return {"totals": {}, "models": []}
    first = settings.openrouter_models[0] if settings.openrouter_models else None
    rows = conn.execute(
        "SELECT model, COUNT(*) calls, SUM(status='ok') ok, SUM(status='rate_limited') rl, "
        "SUM(retry_of IS NOT NULL) retries, SUM(status='error') errors, "
        "AVG(CASE WHEN status='ok' THEN latency_ms END) lat FROM llm_calls GROUP BY model ORDER BY calls DESC").fetchall()
    engine_sends = _one(conn, "SELECT COUNT(*) FROM sends WHERE source='engine' AND template_id IS NOT NULL")
    llm_sends = _one(conn, "SELECT COUNT(*) FROM sends s JOIN templates t ON t.id=s.template_id "
                           "WHERE s.source='engine' AND t.source LIKE 'llm:%'")
    ok_total = sum(r["ok"] or 0 for r in rows)
    lat = [r["lat"] * (r["ok"] or 0) for r in rows if r["lat"]]
    return {
        "configured": bool(settings.openrouter_api_key), "chain": list(settings.openrouter_models),
        "totals": {
            "calls": sum(r["calls"] for r in rows), "successes": ok_total,
            "rate_limited": sum(r["rl"] or 0 for r in rows), "retries": sum(r["retries"] or 0 for r in rows),
            "fallback_next_model": _one(conn, "SELECT COUNT(*) FROM llm_calls WHERE status='ok' AND model != ?",
                                        (first or "",)),
            "fallback_static": _one(conn, "SELECT COUNT(*) FROM events WHERE type='ai_fallback'")
                               + _one(conn, "SELECT COUNT(*) FROM replies WHERE classified_by='fallback'"),
            "template_cache_hit_rate": llm_sends / engine_sends if engine_sends else None,
            "ai_sends": llm_sends,
            "ai_labels": _one(conn, "SELECT COUNT(*) FROM replies WHERE classified_by LIKE 'llm:%'"),
            "avg_latency_ms": sum(lat) / ok_total if ok_total else None,
        },
        "models": [{"model": r["model"], "calls": r["calls"], "ok": r["ok"] or 0, "rate_limited": r["rl"] or 0,
                    "retries": r["retries"] or 0, "errors": r["errors"] or 0,
                    "avg_latency_ms": round(r["lat"]) if r["lat"] else None} for r in rows],
    }


AI_LISTS = {
    "calls": "Every OpenRouter request, newest first.",
    "successes": "Requests that returned a usable answer.",
    "rate_limited": "Requests refused with 429. The model went on cool-down and the next one in the chain was tried.",
    "retries": "Requests that repeated an earlier failed one (batch jobs only; cycles never wait).",
    "fallback_next_model": "Successful answers from a model after the first in the chain.",
    "fallback_static": "Where AI wasn't available: the static template or the keyword classifier was used instead.",
    "ai_sends": "Emails sent with an AI-written template, newest first.",
    "ai_labels": "Replies the LLM labelled because the rules couldn't settle them.",
    "latency": "Successful requests, slowest first.",
}


def _call_row(r) -> dict:
    status = {"ok": "OK", "rate_limited": "429 rate limited", "error": f"error {r['http_status'] or ''}".strip()}
    return {"at_ist": timeutil.fmt_ist(r["at"]), "title": r["model"],
            "sub": f"{r['purpose'].title()} · {status.get(r['status'], r['status'])}"
                   + (" · retry" if r["retry_of"] else ""),
            "note": f"{r['latency_ms'] / 1000:.1f}s" if r["latency_ms"] else None, "contact_id": None}


def ai_list(conn, settings, kind: str, offset: int = 0, limit: int = 50) -> dict | None:
    """The rows behind one AI KPI card: where AI was (or couldn't be) used."""
    if kind not in AI_LISTS:
        return None
    calls = {"calls": "1", "successes": "status='ok'", "rate_limited": "status='rate_limited'",
             "retries": "retry_of IS NOT NULL", "latency": "status='ok' AND latency_ms IS NOT NULL"}
    first = settings.openrouter_models[0] if settings.openrouter_models else ""
    if kind in calls or kind == "fallback_next_model":
        where, args = (calls[kind], ()) if kind in calls else ("status='ok' AND model != ?", (first,))
        order = "latency_ms DESC" if kind == "latency" else "at DESC, id DESC"
        total = _one(conn, f"SELECT COUNT(*) FROM llm_calls WHERE {where}", args)
        rows = [_call_row(r) for r in conn.execute(
            f"SELECT * FROM llm_calls WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?", (*args, limit, offset))]
    elif kind == "ai_sends":
        q = ("FROM sends s JOIN templates t ON t.id=s.template_id JOIN contacts c ON c.id=s.contact_id "
             "WHERE s.source='engine' AND t.source LIKE 'llm:%'")
        total = _one(conn, f"SELECT COUNT(*) {q}")
        rows = [{"at_ist": timeutil.fmt_ist(r["sent_at"] or r["claimed_at"]), "title": r["email"],
                 "sub": f"{lifecycle.STEP_NAMES[r['step'] - 1]} · {r['segment']} variant {r['variant']} · {r['subject']}",
                 "note": r["tsource"][4:], "contact_id": r["contact_id"]} for r in conn.execute(
            f"SELECT s.*, c.email, t.segment, t.variant, t.source tsource {q} "
            "ORDER BY COALESCE(s.sent_at, s.claimed_at) DESC, s.id DESC LIMIT ? OFFSET ?", (limit, offset))]
    elif kind == "ai_labels":
        q = "FROM replies r JOIN contacts c ON c.id=r.contact_id WHERE r.classified_by LIKE 'llm:%'"
        total = _one(conn, f"SELECT COUNT(*) {q}")
        rows = [{"at_ist": timeutil.fmt_ist(r["classified_at"] or r["received_at"]), "title": r["email"],
                 "sub": f"{LABEL_TEXT.get(r['classification'], r['classification'])}: “{r['body'][:90]}”",
                 "note": r["classified_by"][4:], "contact_id": r["contact_id"]} for r in conn.execute(
            f"SELECT r.*, c.email {q} ORDER BY r.classified_at DESC, r.id DESC LIMIT ? OFFSET ?", (limit, offset))]
    else:  # fallback_static: whole-chain failures plus replies the keyword fallback labelled
        merged = [{"at": e["occurred_at"], "title": "All models failed",
                   "sub": f"{(json.loads(e['detail'] or '{}').get('purpose') or '').title()} · "
                          f"{json.loads(e['detail'] or '{}').get('reason', '')}", "note": None, "contact_id": None}
                  for e in conn.execute("SELECT * FROM events WHERE type='ai_fallback'")]
        merged += [{"at": r["classified_at"] or r["received_at"], "title": r["email"],
                    "sub": f"Keyword label {LABEL_TEXT.get(r['classification'], r['classification'])}: “{r['body'][:90]}”",
                    "note": "fallback", "contact_id": r["contact_id"]} for r in conn.execute(
            "SELECT r.*, c.email FROM replies r JOIN contacts c ON c.id=r.contact_id WHERE r.classified_by='fallback'")]
        merged.sort(key=lambda x: x["at"], reverse=True)
        total = len(merged)
        rows = [{**{k: v for k, v in m.items() if k != "at"}, "at_ist": timeutil.fmt_ist(m["at"])}
                for m in merged[offset:offset + limit]]
    return {"kind": kind, "description": AI_LISTS[kind], "total": total, "rows": rows,
            "next": offset + limit if offset + limit < total else None}


def templates_grid(conn) -> dict:
    from .config import SEGMENTS
    if not db.is_initialised(conn):
        return {"segments": [], "steps": [], "cells": []}
    rows = {(r["segment"], r["step"], r["variant"]): r for r in conn.execute("SELECT * FROM templates")}
    cells = []
    for seg in SEGMENTS:
        for step in (1, 2, 3, 4):
            for v in ("A", "B"):
                r = rows.get((seg, step, v)) or rows.get(("*", step, v))
                cells.append({"segment": seg, "step": step, "variant": v, "subject": r["subject"], "body": r["body"],
                              "source": "static" if r["source"] == "static" else "ai",
                              "model": r["source"].removeprefix("llm:") if r["source"] != "static" else None,
                              "created_at": r["created_at"], "created_ist": timeutil.fmt_ist(r["created_at"])})
    return {"segments": list(SEGMENTS), "steps": lifecycle.STEP_NAMES, "cells": cells}
