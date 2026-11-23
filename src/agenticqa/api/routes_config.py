"""Runtime model configuration.

GET  /api/v1/config/llm  - the active provider/model + the vendor presets.
POST /api/v1/config/llm  - switch the LLM provider without a restart.

All vendors are driven over the OpenAI wire protocol: OpenAI and Cloudera AI
Inference speak it natively; Anthropic / Bedrock are reached through an
OpenAI-compatible endpoint (a gateway URL). API keys are held in memory only -
never persisted to disk, returned, or logged. For durable config across
restarts, set the AGENTICQA_* env vars (this is a live override on top).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ..llm.openai_client import OpenAIClient

log = logging.getLogger("agenticqa.config")

router = APIRouter(prefix="/api/v1/config", tags=["config"])

VENDORS = [
    {"id": "openai", "label": "OpenAI", "endpoint_hint": "blank = api.openai.com", "model_hint": "gpt-4o", "endpoint_required": False},
    {"id": "cloudera", "label": "Cloudera AI", "endpoint_hint": "https://<inference-endpoint>/v1", "model_hint": "model name", "endpoint_required": True},
    {"id": "anthropic", "label": "Anthropic", "endpoint_hint": "OpenAI-compatible endpoint URL", "model_hint": "claude-…", "endpoint_required": True},
    {"id": "bedrock", "label": "Amazon Bedrock", "endpoint_hint": "OpenAI-compatible gateway URL", "model_hint": "model id", "endpoint_required": True},
    {"id": "stub", "label": "Heuristic (no model)", "endpoint_hint": "", "model_hint": "", "endpoint_required": False},
]
LABELS = {v["id"]: v["label"] for v in VENDORS}


class LLMConfig(BaseModel):
    provider: str
    model: str = ""
    base_url: str = ""
    api_key: str = ""


@router.get("/llm")
async def get_llm(request: Request) -> dict:
    meta = getattr(request.app.state, "llm_meta", None) or {
        "provider": "stub", "label": "Heuristic", "model": None, "base_url": "", "mode": "heuristic"
    }
    return {"current": meta, "vendors": VENDORS}


@router.post("/llm")
async def set_llm(cfg: LLMConfig, request: Request) -> dict:
    provider = (cfg.provider or "stub").lower()
    st = request.app.state
    if provider == "stub":
        client = None
    else:
        if not cfg.api_key:
            raise HTTPException(status_code=400, detail="An API key is required for this provider.")
        if provider != "openai" and not cfg.base_url:
            raise HTTPException(status_code=400, detail="This provider needs an OpenAI-compatible endpoint URL.")
        client = OpenAIClient(
            api_key=cfg.api_key,
            model=cfg.model or "gpt-4o",
            base_url=cfg.base_url,
            timeout=st.settings.llm_timeout_seconds,
            max_retries=st.settings.llm_max_retries,
        )

    # atomic hot-swap of the pipeline's touch-points
    st.engine.set_touchpoints(*st.touchpoints_from_client(client))
    st.settings.llm_provider = provider
    st.llm_meta = {
        "provider": provider,
        "label": LABELS.get(provider, provider),
        "model": (cfg.model or "gpt-4o") if client else None,
        "base_url": cfg.base_url,
        "mode": "model" if client else "heuristic",
    }
    log.info("LLM provider switched to %s (mode=%s) via config API.", provider, st.llm_meta["mode"])
    return {"ok": True, "current": st.llm_meta}
