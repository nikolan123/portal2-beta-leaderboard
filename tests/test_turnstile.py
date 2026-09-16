from types import SimpleNamespace

import httpx

from app import turnstile

class FakeResponse:
    def __init__(self, result):
        self.result = result

    def raise_for_status(self):
        return None

    def json(self):
        return self.result

def configure_turnstile(monkeypatch):
    monkeypatch.setattr(
        turnstile,
        "settings",
        SimpleNamespace(
            turnstile_site_key="site-key",
            turnstile_secret_key="secret-key",
            base_url="https://runs.example.com",
        ),
    )

def test_turnstile_accepts_matching_action_and_hostname(monkeypatch):
    configure_turnstile(monkeypatch)
    monkeypatch.setattr(
        turnstile.httpx,
        "post",
        lambda *args, **kwargs: FakeResponse(
            {
                "success": True,
                "action": "submit_run",
                "hostname": "runs.example.com",
            }
        ),
    )
    assert turnstile.verify_turnstile("valid-token", "submit_run")

def test_turnstile_rejects_wrong_context_or_network_failure(monkeypatch):
    configure_turnstile(monkeypatch)
    monkeypatch.setattr(
        turnstile.httpx,
        "post",
        lambda *args, **kwargs: FakeResponse(
            {
                "success": True,
                "action": "discord_signup",
                "hostname": "wrong.example.com",
            }
        ),
    )
    assert not turnstile.verify_turnstile("valid-token", "submit_run")

    def fail(*args, **kwargs):
        raise httpx.ConnectError("unavailable")

    monkeypatch.setattr(turnstile.httpx, "post", fail)
    assert not turnstile.verify_turnstile("valid-token", "submit_run")
