"""Tests for LLAMA_CPP_BASE_URL env-var override across CLI and client paths."""

from __future__ import annotations

import importlib

import pytest


@pytest.fixture(scope="module", autouse=True)
def _resync_reloaded_modules():
    yield
    import cli.main
    import cli.utils
    importlib.reload(cli.utils)
    importlib.reload(cli.main)


def _reload_client():
    import tradingagents.llm_clients.openai_client as mod
    return importlib.reload(mod)


def _base_url(mod, provider, **kwargs):
    return str(mod.OpenAIClient(model="m", provider=provider, **kwargs).get_llm().openai_api_base)


def test_resolver_returns_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("LLAMA_CPP_BASE_URL", raising=False)
    mod = _reload_client()
    assert _base_url(mod, "llama_cpp") == "http://localhost:8080/v1"


def test_resolver_returns_env_when_set(monkeypatch):
    monkeypatch.setenv("LLAMA_CPP_BASE_URL", "http://remote-llama:8080/v1")
    mod = _reload_client()
    assert _base_url(mod, "llama_cpp") == "http://remote-llama:8080/v1"


def test_llama_cpp_uses_local_compatible_chat_class(monkeypatch):
    monkeypatch.delenv("LLAMA_CPP_BASE_URL", raising=False)
    mod = _reload_client()
    llm = mod.OpenAIClient(model="qwen2.5", provider="llama_cpp").get_llm()
    assert type(llm).__name__ == "LocalCompatibleChatOpenAI"


def test_provider_default_url_honors_env(monkeypatch):
    monkeypatch.setenv("LLAMA_CPP_BASE_URL", "http://host:9090/v1")
    import cli.utils
    importlib.reload(cli.utils)
    assert cli.utils.provider_default_url("llama_cpp") == "http://host:9090/v1"


def test_confirm_llama_cpp_endpoint_shows_env_origin(monkeypatch):
    monkeypatch.setenv("LLAMA_CPP_BASE_URL", "http://remote-host:8080/v1")
    import cli.utils
    importlib.reload(cli.utils)
    from rich.console import Console
    from io import StringIO

    out = StringIO()
    cli.utils.console = Console(file=out, width=120, force_terminal=True)
    cli.utils.confirm_llama_cpp_endpoint("http://remote-host:8080/v1")
    rendered = out.getvalue()
    assert "LLAMA_CPP_BASE_URL" in rendered
