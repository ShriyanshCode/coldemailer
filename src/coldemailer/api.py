"""HTTP API for the dashboard. Same modules as the CLI. Advancing the clock takes the run lock; operator actions
(resolve, resume, suppress) are single transactions, and the engine never revives a contact they stopped."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import actions, clock, db, report, runner, verify
from .config import ROOT, Settings, load_settings

WEB_DIR = ROOT / "web"


class AdvanceBody(BaseModel):
    hours: float = 1


class ResolveBody(BaseModel):
    action: str
    days: int = 3
    note: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    app = FastAPI(title="coldemailer", version="0.1.0")

    def conn():
        c = db.connect(settings.db_path)
        try:
            yield c
        finally:
            c.close()

    def initialised(c) -> None:
        if not db.is_initialised(c):
            raise HTTPException(409, "database not initialised — run `python -m coldemailer seed`")

    @app.exception_handler(ValueError)
    async def value_error(_: Request, e: ValueError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(clock.RunLocked)
    async def locked(_: Request, e: clock.RunLocked):
        return JSONResponse({"detail": str(e)}, status_code=409)

    # ---------------------------------------------------------------- reads
    @app.get("/api/clock")
    def get_clock(c=Depends(conn)):
        info = report.clock_info(c)
        if info.get("initialised"):
            job = c.execute("SELECT id FROM jobs WHERE status='running' ORDER BY id DESC LIMIT 1").fetchone()
            info["running_job"] = job["id"] if job else None
        return info

    @app.get("/api/overview")
    def overview(c=Depends(conn), day_from: str | None = None, day_to: str | None = None):
        return report.overview(c, settings, day_from, day_to)

    @app.get("/api/contacts")
    def contacts(c=Depends(conn), status: str | None = None, step: int | None = None, segment: str | None = None,
                 timezone: str | None = None, mailbox_id: int | None = None, on_hold: str | None = None,
                 q: str | None = None, due: str | None = None, ooo: str | None = None, page: int = 0, size: int = 25):
        filters = {"status": status, "step": step, "segment": segment, "timezone": timezone,
                   "mailbox_id": mailbox_id, "on_hold": on_hold, "q": q, "due": due, "ooo": ooo}
        return report.contacts(c, filters, page, size)

    @app.get("/api/contacts/{contact_id}")
    def contact(contact_id: int, c=Depends(conn)):
        initialised(c)
        d = report.contact_detail(c, settings, contact_id)
        if d is None:
            raise HTTPException(404, "contact not found")
        return d

    @app.get("/api/search")
    def search(q: str = "", c=Depends(conn)):
        return report.search(c, q)

    @app.get("/api/events")
    def events(c=Depends(conn), group: str | None = None, type: str | None = None, q: str | None = None,
               day_from: str | None = None, day_to: str | None = None, before: str | None = None, limit: int = 40):
        return report.events(c, group, type, q, day_from, day_to, max(1, min(limit, 200)), before)

    @app.get("/api/events/{event_id}")
    def event(event_id: int, c=Depends(conn)):
        initialised(c)
        d = report.event_detail(c, settings, event_id)
        if d is None:
            raise HTTPException(404, "event not found")
        return d

    @app.get("/api/reviews")
    def reviews(state: str = "open", c=Depends(conn)):
        return report.reviews(c, state)

    @app.get("/api/mailboxes")
    def mailboxes(c=Depends(conn)):
        return report.mailboxes(c, settings)

    @app.get("/api/ai")
    def ai(c=Depends(conn)):
        return report.ai_usage(c, settings)

    @app.get("/api/ai/list/{kind}")
    def ai_list(kind: str, offset: int = 0, limit: int = 50, c=Depends(conn)):
        initialised(c)
        d = report.ai_list(c, settings, kind, max(0, offset), max(1, min(limit, 200)))
        if d is None:
            raise HTTPException(404, "unknown list")
        return d

    @app.get("/api/templates")
    def templates(c=Depends(conn)):
        return report.templates_grid(c)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: int, c=Depends(conn)):
        j = runner.job(c, job_id)
        if j is None:
            raise HTTPException(404, "job not found")
        return j

    # ---------------------------------------------------------------- writes
    @app.post("/api/clock/advance")
    def advance(body: AdvanceBody, c=Depends(conn)):
        initialised(c)
        if body.hours < 0 or body.hours > 24 * 90:
            raise ValueError("hours must be between 0 and 2160")
        return {"job_id": runner.start_job(settings, body.hours)}

    @app.post("/api/reviews/{item_id}/resolve")
    def resolve(item_id: int, body: ResolveBody, c=Depends(conn)):
        initialised(c)
        actions.resolve_review(c, item_id, body.action, body.days, body.note, settings)
        return {"ok": True}

    @app.post("/api/mailboxes/{mailbox_id}/resume")
    def resume(mailbox_id: int, c=Depends(conn)):
        initialised(c)
        actions.resume_mailbox(c, mailbox_id)
        return {"ok": True}

    @app.post("/api/contacts/{contact_id}/suppress")
    def suppress(contact_id: int, c=Depends(conn)):
        initialised(c)
        actions.suppress_contact(c, contact_id)
        return {"ok": True}

    @app.post("/api/verify")
    def run_verify(c=Depends(conn)):
        initialised(c)
        return verify.run(c, settings)

    # ---------------------------------------------------------------- the dashboard
    if WEB_DIR.exists():
        app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")

    @app.get("/", include_in_schema=False)
    def index():
        page = WEB_DIR / "index.html"
        if not page.exists():
            return JSONResponse({"detail": "dashboard not built yet; the API is under /api and /docs"})
        return FileResponse(page)

    return app
