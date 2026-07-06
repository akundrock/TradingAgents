from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import cli.main as cli_main
from tradingagents.dataflows import schwab


def test_parse_auth_code_from_redirect_url():
    code = schwab.parse_auth_code_from_redirect(
        "https://127.0.0.1/?code=abc%2F123%3D%3D&state=xyz"
    )
    assert code == "abc/123=="


def test_parse_auth_code_accepts_raw_code():
    assert schwab.parse_auth_code_from_redirect("raw-code-value") == "raw-code-value"


def test_get_authorization_url_contains_params():
    url = schwab.get_authorization_url("cid-123", redirect_uri="https://127.0.0.1")
    assert "response_type=code" in url
    assert "client_id=cid-123" in url
    assert "redirect_uri=https%3A%2F%2F127.0.0.1" in url


@pytest.mark.unit
def test_bootstrap_tokens_from_redirect_saves_tokens(monkeypatch, tmp_path):
    token_path = tmp_path / "tokens.json"
    monkeypatch.setenv("TRADINGAGENTS_SCHWAB_TOKENS_PATH", str(token_path))

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": "access-1",
                "refresh_token": "refresh-1",
                "expires_in": 1800,
            }

    monkeypatch.setattr(schwab.requests, "post", lambda *a, **k: FakeResponse())

    out = schwab.bootstrap_tokens_from_redirect(
        "https://127.0.0.1/?code=good-code",
        client_id="cid",
        client_secret="secret",
        redirect_uri="https://127.0.0.1",
    )
    assert out["access_token"] == "access-1"
    assert token_path.exists()
    saved = json.loads(token_path.read_text(encoding="utf-8"))
    assert saved["refresh_token"] == "refresh-1"


@pytest.mark.unit
def test_cli_schwab_auth_command(monkeypatch):
    runner = CliRunner()

    monkeypatch.setattr(cli_main, "get_schwab_credentials", lambda: ("cid", "secret"))
    monkeypatch.setattr(cli_main, "get_schwab_redirect_uri", lambda: "https://127.0.0.1")
    monkeypatch.setattr(cli_main, "get_authorization_url", lambda *a, **k: "https://auth.example")
    monkeypatch.setattr(
        cli_main,
        "bootstrap_tokens_from_redirect",
        lambda *a, **k: {"access_token": "a", "refresh_token": "r", "expires_in": 1800},
    )
    monkeypatch.setattr(cli_main, "_tokens_path", lambda: "/tmp/schwab_tokens.json")

    result = runner.invoke(
        cli_main.app,
        ["schwab-auth", "--redirect-url", "https://127.0.0.1/?code=abc"],
    )
    assert result.exit_code == 0
    assert "Saved Schwab tokens" in result.output
