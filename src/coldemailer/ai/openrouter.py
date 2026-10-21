"""OpenRouter client for free models, built to survive their rate limits.

- token bucket at settings.llm_rpm requests/minute (in-process)
- per model: up to N tries; 429 honours Retry-After, else exponential backoff with jitter
- a model that keeps failing gets a cool-down and the next model in the chain is tried
- every HTTP attempt is logged to llm_calls; giving up writes an ai_fallback event
Callers always have a non-AI fallback, so None is a normal answer.
"""
from __future__ import annotations

import json
import random
import time

import httpx

from .. import clock

BASE_URL = "https://openrouter.ai/api/v1"


class TokenBucket:
    def __init__(self, rpm: int, sleep=time.sleep, now=time.monotonic):
        self.rate = rpm / 60.0
        self.capacity = float(max(1, rpm))
        self.tokens = self.capacity
        self.sleep, self.now = sleep, now
        self.updated = now()

    def acquire(self) -> None:
        t = self.now()
        self.tokens = min(self.capacity, self.tokens + (t - self.updated) * self.rate)
        self.updated = t
        if self.tokens < 1:
            wait = (1 - self.tokens) / self.rate
            self.sleep(wait)
            self.tokens, self.updated = 1.0, self.updated + wait
        self.tokens -= 1


class LLMClient:
    def __init__(self, conn, settings, transport: httpx.BaseTransport | None = None,
                 sleep=time.sleep, now=time.monotonic):
        self.conn, self.s = conn, settings
        self.models = list(settings.openrouter_models)
        self.sleep, self.now = sleep, now
        self.bucket = TokenBucket(settings.llm_rpm, sleep, now)
        self.cooldown: dict[str, float] = {}
        self.jitter = random.Random(0)
        self.http = httpx.Client(
            base_url=BASE_URL, transport=transport, timeout=30,
            headers={"Authorization": f"Bearer {settings.openrouter_api_key or ''}",
                     "HTTP-Referer": "https://github.com/ShriyanshCode/coldemailer",
                     "X-Title": "coldemailer"})

    def _log(self, model: str, purpose: str, status: str, http_status, latency_ms: int, retry_of) -> int:
        try:
            at = clock.now(self.conn)
        except Exception:
            at = "1970-01-01T00:00:00Z"
        call_id = self.conn.execute(
            "INSERT INTO llm_calls(model, purpose, status, http_status, latency_ms, retry_of, at) "
            "VALUES (?,?,?,?,?,?,?)", (model, purpose, status, http_status, latency_ms, retry_of, at)).lastrowid
        self.conn.execute(
            "INSERT INTO events(type, detail, occurred_at) VALUES ('ai_call', ?, ?)",
            (json.dumps({"model": model, "purpose": purpose, "status": status, "http_status": http_status,
                         "latency_ms": latency_ms, "retry": retry_of is not None}), at))
        return call_id

    def chat(self, messages: list[dict], purpose: str, max_tokens: int = 2500,
             patient: bool = False) -> tuple[str, str] | None:
        """patient=True (offline batch jobs) waits out model cool-downs once instead of giving up."""
        answer = self._chat_once(messages, purpose, max_tokens, patient)
        if answer is None and patient and self.models:
            wait = min(self.cooldown.get(m, 0) for m in self.models) - self.now()
            if wait > 0:
                self.sleep(min(wait, 120))
            answer = self._chat_once(messages, purpose, max_tokens, patient)
        if answer is None:
            self._log_fallback(purpose)
        return answer

    def _post(self, payload: dict, deadline_s: float):
        """POST with an overall deadline. OpenRouter keeps slow requests alive by sending whitespace,
        which resets httpx's per-read timeout, so a call could otherwise hang for many minutes."""
        start = self.now()
        with self.http.stream("POST", "/chat/completions", json=payload) as resp:
            body = bytearray()
            for chunk in resp.iter_bytes():
                body += chunk
                if self.now() - start > deadline_s:
                    raise httpx.ReadTimeout(f"no answer within {deadline_s:.0f}s")
            return resp.status_code, resp.headers, bytes(body)

    def _chat_once(self, messages, purpose, max_tokens, patient: bool) -> tuple[str, str] | None:
        """Inside a cycle (not patient) never sleep: a 429 puts the model on cool-down for Retry-After
        and the next model is tried at once. Patient batch jobs retry with back-off."""
        for model in self.models:
            if self.cooldown.get(model, 0) > self.now():
                continue
            retry_of = None
            for attempt in range(self.s.llm_tries_per_model):
                self.bucket.acquire()
                t0 = time.perf_counter()
                try:
                    status, headers, body = self._post(
                        {"model": model, "messages": messages, "temperature": 0.4, "max_tokens": max_tokens},
                        self.s.llm_patient_deadline_s if patient else self.s.llm_call_deadline_s)
                except httpx.HTTPError:
                    retry_of = self._log(model, purpose, "error", None, int((time.perf_counter() - t0) * 1000),
                                         retry_of)
                    if not patient:
                        break
                    self.sleep(2 ** attempt + self.jitter.random())
                    continue
                latency = int((time.perf_counter() - t0) * 1000)
                if status == 200:
                    try:
                        text = json.loads(body)["choices"][0]["message"]["content"] or ""
                    except (ValueError, KeyError, IndexError, TypeError):
                        text = ""
                    if text.strip():
                        self._log(model, purpose, "ok", 200, latency, retry_of)
                        return text, model
                    retry_of = self._log(model, purpose, "error", 200, latency, retry_of)
                    continue
                if status == 429:
                    retry_of = self._log(model, purpose, "rate_limited", 429, latency, retry_of)
                    try:
                        wait = float(headers.get("Retry-After", ""))
                    except ValueError:
                        wait = 2 ** attempt + self.jitter.random()
                    if not patient:
                        self.cooldown[model] = self.now() + max(wait, 1)
                        break
                    self.sleep(min(wait, 60))
                    continue
                retry_of = self._log(model, purpose, "error", status, latency, retry_of)
                if 400 <= status < 500 or not patient:
                    break  # bad request / unknown model, or no time to wait inside a cycle
                self.sleep(2 ** attempt + self.jitter.random())
            self.cooldown[model] = max(self.cooldown.get(model, 0), self.now() + self.s.llm_cooldown_s)
        return None

    def _log_fallback(self, purpose: str) -> None:
        try:
            at = clock.now(self.conn)
            self.conn.execute("INSERT INTO events(type, detail, occurred_at) VALUES ('ai_fallback', ?, ?)",
                              (json.dumps({"purpose": purpose, "reason": "all models failed or rate limited"}), at))
        except Exception:
            pass
