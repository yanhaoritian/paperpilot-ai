from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.api import auth, conversations, documents, health, libraries, query
from app.config import get_settings
from app.db import init_db
from app.services.client_ip import client_ip

settings = get_settings()
ROOT = Path(__file__).resolve().parents[2]


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        for key in ("request_id", "user_id", "path", "method", "status", "latency_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, ensure_ascii=False)


handler = logging.StreamHandler()
handler.setFormatter(JsonLogFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
logger = logging.getLogger("paperpilot")


def _assert_startup_security(cfg) -> None:  # noqa: ANN001
    if cfg.jwt_require_strong and cfg.jwt_secret_is_weak():
        raise RuntimeError(
            "JWT_SECRET 过弱或仍为占位符。请设置至少 24 位随机密钥，"
            "或仅在本地开发将 JWT_REQUIRE_STRONG=0。"
        )
    if cfg.auth_expose_code:
        logger.warning("AUTH_EXPOSE_CODE=1：验证码会返回给客户端，仅用于本地联调")
    if not cfg.smtp_configured and not cfg.auth_expose_code:
        logger.warning("未配置 SMTP 且 AUTH_EXPOSE_CODE=0：邮箱注册发码将失败，直到配置 SMTP_*")
    origins = [o.strip() for o in (cfg.cors_origins or "").split(",") if o.strip()]
    if not origins or origins == ["*"]:
        logger.warning(
            "CORS_ORIGINS 为 * 或空：公网部署请改为具体域名，例如 https://your.domain"
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_settings.cache_clear()
    cfg = get_settings()
    _assert_startup_security(cfg)
    Path(cfg.pdf_storage_dir).mkdir(parents=True, exist_ok=True)
    init_db()
    logger.info(
        "startup complete provider=%s db=%s expose_code=%s",
        cfg.embedding_provider,
        "sqlite" if cfg.is_sqlite else "postgres",
        cfg.auth_expose_code,
    )
    if cfg.index_recover_on_startup and not cfg.index_external_worker:
        from app.services.index_recovery import reclaim_and_retry_indexing

        threading.Thread(
            target=reclaim_and_retry_indexing,
            name="index-recovery",
            daemon=True,
        ).start()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    allow_all = (not origins) or origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if allow_all else origins,
        allow_credentials=not allow_all,  # browsers forbid * + credentials
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Simple in-memory sliding-window rate limit for lab / public-beta scale
    windows: dict[str, deque[float]] = defaultdict(deque)

    def check_rate(key: str, limit: int, window_s: int) -> bool:
        now = time.time()
        q = windows[key]
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True

    app.state.check_rate = check_rate

    @app.middleware("http")
    async def request_middleware(request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        start = time.perf_counter()
        path = request.url.path
        client = client_ip(request, settings.trusted_proxy_cidrs)

        if path.startswith("/api/"):
            window = settings.rate_limit_window_seconds
            if path == "/api/auth/send-code" and request.method == "POST":
                ok = check_rate(
                    f"send-code-ip:{client}",
                    settings.rate_limit_send_code_ip_max,
                    settings.rate_limit_send_code_ip_window_seconds,
                )
            elif path.startswith("/api/auth/"):
                ok = check_rate(f"auth:{client}", settings.rate_limit_auth_max, window)
            elif "/documents" in path and request.method == "POST" and path.endswith("/documents"):
                ok = check_rate(f"upload:{client}", settings.rate_limit_upload_max, window)
            elif (
                (path == "/api/query" and request.method == "POST")
                or (path.endswith("/messages") and request.method == "POST" and "/conversations/" in path)
            ):
                ok = check_rate(f"query:{client}", settings.rate_limit_query_max, window)
            else:
                ok = True
            if not ok:
                return JSONResponse(status_code=429, content={"detail": "请求过于频繁，请稍后再试"})

        response = await call_next(request)
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Request-Id"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data: blob: https://fastapi.tiangolo.com; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'; form-action 'self'",
        )
        logger.info(
            "request",
            extra={
                "request_id": request_id,
                "path": path,
                "method": request.method,
                "status": response.status_code,
                "latency_ms": latency_ms,
            },
        )
        return response

    app.include_router(auth.router)
    app.include_router(libraries.router)
    app.include_router(documents.router)
    app.include_router(query.router)
    app.include_router(conversations.router)
    app.include_router(health.router)

    # Serve frontend assets only (do not expose .env / backend source)
    @app.get("/")
    def serve_index() -> FileResponse:
        return FileResponse(ROOT / "index.html")

    @app.get("/login.html")
    def serve_login() -> FileResponse:
        return FileResponse(ROOT / "login.html")

    @app.get("/app.html")
    def serve_app() -> FileResponse:
        return FileResponse(ROOT / "app.html")

    @app.get("/app.js")
    def serve_app_js() -> FileResponse:
        return FileResponse(
            ROOT / "app.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/auth.js")
    def serve_auth_js() -> FileResponse:
        return FileResponse(
            ROOT / "auth.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/styles.css")
    def serve_styles() -> FileResponse:
        return FileResponse(
            ROOT / "styles.css",
            media_type="text/css",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    return app


app = create_app()
