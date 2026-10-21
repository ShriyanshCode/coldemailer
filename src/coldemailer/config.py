"""Settings: defaults from the design doc, overridable from .env and by keyword (tests)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace

from dotenv import load_dotenv

SEGMENTS = ("SaaS", "Fintech", "Retail", "Healthcare", "Logistics")


@dataclass(frozen=True)
class Settings:
    db_path: str = "data/coldemailer.db"
    seed: int = 42
    campaign_id: int = 1
    batch_size: int = 500

    # Sequence
    waits_days: tuple[int, ...] = (3, 5, 7, 7)
    window_start: str = "09:00"
    window_end: str = "18:00"
    clock_start: str = "2026-10-01T03:30:00Z"  # 09:00 IST

    # Mailboxes / warm-up
    mailbox_count: int = 20
    warmup_start: int = 20
    warmup_increment: int = 10
    warmup_max: int = 200
    domain_daily_cap: int = 50

    # Mock provider
    hard_bounce_rate: float = 0.02
    soft_bounce_rate: float = 0.02
    policy_share_of_soft: float = 1 / 3
    complaint_rate: float = 0.001
    unsub_click_rate: float = 0.005
    reply_rates: tuple[float, ...] = (0.06, 0.05, 0.04, 0.03)
    reply_mix: tuple[tuple[str, float], ...] = (
        ("interested", 0.30),
        ("not_interested", 0.30),
        ("out_of_office", 0.25),
        ("unsubscribe", 0.15),
    )
    bad_domains: tuple[str, ...] = ("oldmail-corp.com", "defunct-labs.io", "parkedsite.net")
    bad_domain_bounce_rate: float = 0.40

    # Soft bounces / review
    soft_retry_hours: tuple[int, ...] = (1, 4, 24)
    max_soft_attempts: int = 3

    # Auto-pause
    autopause_window: int = 100
    autopause_min_sample: int = 50
    autopause_bounce: float = 0.05
    autopause_complaint_window: int = 1000
    autopause_complaint_min_sample: int = 300
    autopause_complaint: float = 0.003

    # Engine
    claim_lease_s: int = 300
    ooo_default_days: int = 3
    ooo_cap_days: int = 14

    # AI
    openrouter_api_key: str | None = None
    openrouter_models: tuple[str, ...] = field(default_factory=tuple)
    llm_rpm: int = 15
    llm_tries_per_model: int = 3
    llm_cooldown_s: int = 60
    classify_calls_per_cycle: int = 3
    classify_batch: int = 10


def load_settings(**overrides) -> Settings:
    load_dotenv()
    env: dict = {}
    if os.getenv("COLDEMAILER_DB"):
        env["db_path"] = os.environ["COLDEMAILER_DB"]
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    env["openrouter_api_key"] = key or None
    models = os.getenv("OPENROUTER_MODELS", "").strip()
    if models:
        env["openrouter_models"] = tuple(m.strip() for m in models.split(",") if m.strip())
    return replace(Settings(), **{**env, **overrides})
