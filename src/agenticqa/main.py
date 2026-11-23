"""FastAPI application factory.

Wires the process singletons (repository, event bus, executor, engine) into
``app.state`` during the lifespan, mounts the API routers and the build-free
static dashboard, and reconciles interrupted runs on startup. Runs single-worker
(``uvicorn --workers 1``) - the bus and engine are in-process singletons.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import (
    routes_approval,
    routes_config,
    routes_github,
    routes_health,
    routes_runs,
    routes_stream,
    routes_trigger,
)
import logging

from .config import get_settings
from .events.bus import EventBus
from .execution.subprocess_venv import SubprocessVenvExecutor
from .github import GitHubAppAuth, GitHubClient, GitHubService
from .llm.factory import build_client
from .orchestrator.engine import Engine
from .orchestrator.llm_touchpoints import LLMClassifier, LLMRouter, LLMTriage
from .orchestrator.seed import sample_runs
from .orchestrator.stubs import HeuristicClassifier, HeuristicRouter, HeuristicTriage
from .store.db import RunRepository

log = logging.getLogger("agenticqa")

_STATIC_DIR = Path(__file__).parent / "web" / "static"


def _touchpoints_from_client(client):
    """LLM-backed touch-points when a client is present (each falling back to
    its heuristic on error), else the deterministic heuristics."""
    if client is None:
        return HeuristicRouter(), HeuristicClassifier(), HeuristicTriage()
    return LLMRouter(client), LLMClassifier(client), LLMTriage(client)


def _advisor_for(client):
    """The test advisor that goes with a client: LLM-backed when there is one, else a name-matching rule."""
    from .orchestrator.llm_touchpoints import LLMTestAdvisor
    from .orchestrator.stubs import HeuristicAdvisor

    return HeuristicAdvisor() if client is None else LLMTestAdvisor(client)


def _build_llm_touchpoints(settings):
    return _touchpoints_from_client(build_client(settings))


def llm_meta(settings, *, active: bool) -> dict:
    """The provider/model summary the UI shows and the config route updates."""
    provider = (settings.llm_provider or "stub").lower()
    presets = {
        "openai": ("OpenAI", settings.openai_model, settings.openai_base_url),
        "cloudera": ("Cloudera AI", settings.cloudera_model or settings.openai_model, settings.cloudera_ai_endpoint),
        "anthropic": ("Anthropic", settings.anthropic_model, ""),
        "bedrock": ("Amazon Bedrock", "", ""),
    }
    label, model, base = presets.get(provider, ("Heuristic", "", ""))
    return {
        "provider": provider, "label": label,
        "model": model if active else None,
        "base_url": base, "mode": "model" if active else "heuristic",
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    repo = RunRepository(settings.db_path)
    await repo.connect()
    bus = EventBus(repo)
    executor = SubprocessVenvExecutor(base_dir=settings.workspace_dir)
    llm_client = build_client(settings)
    router, classifier, triage = _touchpoints_from_client(llm_client)
    if llm_client is not None:
        log.info("LLM touch-points enabled (provider=%s).", settings.llm_provider)

    github_app_auth = GitHubAppAuth(settings)
    github_client = GitHubClient(
        settings.github_token, app_auth=github_app_auth, api_url=settings.github_api_url
    )
    github = GitHubService(github_client, settings)
    if github_client.enabled:
        mode = "app" if github_app_auth.enabled else "pat"
        log.info("GitHub integration enabled (mode=%s, status context=%s).",
                 mode, settings.github_status_context)
    else:
        log.info("GitHub integration disabled (no App or token) - statuses/comments skipped.")

    engine = Engine(
        repo, bus, executor, router, classifier, triage,
        max_concurrent_runs=settings.max_concurrent_runs,
        github=github,
        selection_mode=settings.selection,
        trust_dynamic_loading=settings.selection_trust_dynamic,
        coverage_map_path=settings.selection_coverage_map,
        advisor=_advisor_for(llm_client),
    )

    app.state.settings = settings
    app.state.repo = repo
    app.state.bus = bus
    app.state.engine = engine
    app.state.github = github
    # for the runtime model-config (routes_config): swap the LLM at runtime.
    app.state.touchpoints_from_client = _touchpoints_from_client
    app.state.llm_meta = llm_meta(settings, active=llm_client is not None)

    await engine.reconcile_on_startup()
    if settings.seed_samples and not await repo.list_runs(limit=1):
        for run in sample_runs():
            await repo.save_run(run)
    try:
        yield
    finally:
        await repo.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Agentic QA", version="0.1.0", lifespan=lifespan)

    app.include_router(routes_health.router)
    app.include_router(routes_trigger.router)
    app.include_router(routes_stream.router)
    app.include_router(routes_runs.router)
    app.include_router(routes_config.router)
    app.include_router(routes_approval.router)
    app.include_router(routes_github.router)

    # Always revalidate the SPA shell and its assets so a browser can never run
    # a stale build of the dashboard (ETag/Last-Modified still yield cheap 304s).
    @app.middleware("http")
    async def _revalidate_ui(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/runs/") or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    @app.get("/runs/{run_id}", include_in_schema=False)
    async def run_page(run_id: str) -> FileResponse:
        # Same single-page dashboard; the client reads the run id from the URL.
        return FileResponse(_STATIC_DIR / "index.html")

    return app


app = create_app()
