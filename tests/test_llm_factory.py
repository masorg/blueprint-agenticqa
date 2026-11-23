"""`llm.factory` provider resolution: which credentials/endpoint/model each
provider name maps to, and that a missing key never silently yields a client.

Settings are built with ``_env_file=None`` so a developer's real ``.env`` can
never leak keys into (or change the outcome of) these tests.
"""

from __future__ import annotations

from agenticqa.config import Settings
from agenticqa.llm.factory import build_client, resolve_target
from agenticqa.llm.openai_client import OpenAIClient


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **kw)


def test_mistral_resolves_to_its_own_endpoint_and_model():
    t = resolve_target(_settings(llm_provider="mistral", mistral_api_key="mk"))
    assert t is not None
    assert (t.provider, t.api_key, t.base_url, t.model) == (
        "mistral", "mk", "https://api.mistral.ai/v1", "mistral-large-latest",
    )


def test_mistral_does_not_borrow_openai_credentials():
    t = resolve_target(_settings(llm_provider="mistral", openai_api_key="sk-openai"))
    assert t is not None and t.api_key == ""


def test_openai_resolves_to_openai_settings():
    t = resolve_target(_settings(llm_provider="openai", openai_api_key="sk", openai_model="gpt-4o-mini"))
    assert t is not None
    assert (t.provider, t.api_key, t.model, t.base_url) == ("openai", "sk", "gpt-4o-mini", "")


def test_cloudera_prefers_its_own_endpoint_falls_back_to_openai_fields():
    own = resolve_target(_settings(
        llm_provider="cloudera", cloudera_ai_api_key="ck", cloudera_ai_endpoint="https://x/v1",
        cloudera_model="m", openai_api_key="sk",
    ))
    assert own is not None and (own.api_key, own.base_url, own.model) == ("ck", "https://x/v1", "m")

    fallback = resolve_target(_settings(llm_provider="cloudera", openai_api_key="sk", openai_model="gpt-4o"))
    assert fallback is not None and (fallback.api_key, fallback.model) == ("sk", "gpt-4o")


def test_stub_and_unknown_providers_resolve_to_nothing():
    assert resolve_target(_settings(llm_provider="stub")) is None
    assert resolve_target(_settings(llm_provider="no-such-provider")) is None


def test_missing_key_yields_no_client():
    assert build_client(_settings(llm_provider="mistral")) is None
    assert build_client(_settings(llm_provider="openai")) is None


def test_build_client_returns_an_openai_client_for_mistral():
    client = build_client(_settings(llm_provider="mistral", mistral_api_key="mk"))
    assert isinstance(client, OpenAIClient)
