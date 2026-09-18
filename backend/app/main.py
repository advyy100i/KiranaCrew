import logging
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import dashboard, dev, internal, telegram
from app.db import db_ok
from app.services import jobs

logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx logs full URLs = the bot token
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
FRONTEND = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "build" / "web"


def create_app(runtime=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if runtime is None:
            from app.runtime import get_runtime
            app.state.runtime = get_runtime()
        else:
            app.state.runtime = runtime
        yield

    app = FastAPI(title="KiranaCrew", lifespan=lifespan)
    origin = (runtime.settings.dashboard_origin if runtime else None) or "*"
    app.add_middleware(CORSMiddleware, allow_origins=[origin], allow_methods=["GET"], allow_headers=["Authorization"])
    app.include_router(telegram.router)
    app.include_router(dev.router)
    app.include_router(internal.router)
    app.include_router(dashboard.router)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/readyz")
    def readyz():
        rt = app.state.runtime
        out = {"db": db_ok(rt.engine)}
        if out["db"]:
            with rt.engine.connect() as c:
                out["worker"] = jobs.worker_alive(c) or rt.settings.run_worker_inline
        stt = rt.stt.providers[0] if getattr(rt.stt, "providers", None) else None
        out["stt"] = {"provider": getattr(stt, "name", None), "loaded": getattr(stt, "loaded", True)}
        llm = rt.llm_providers[0] if rt.llm_providers else None
        out["llm"] = {"provider": getattr(llm, "name", None), "model": getattr(llm, "model", None),
                      "reachable": llm.reachable() if hasattr(llm, "reachable") else (llm is not None)}
        out["parser_mode"] = rt.settings.parser_mode
        out["ok"] = bool(out["db"] and out.get("worker"))
        return out

    if FRONTEND.exists():
        app.mount("/dashboard", StaticFiles(directory=str(FRONTEND), html=True), name="dashboard")
    return app


app = create_app()
