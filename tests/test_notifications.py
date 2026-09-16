from types import SimpleNamespace

import httpx

def test_disabled_webhook_does_not_send(monkeypatch):
    from app import notifications
    monkeypatch.setattr(notifications, "settings", SimpleNamespace(discord_moderation_webhook_url=""))
    monkeypatch.setattr(notifications.httpx, "post", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Unexpected request")))
    notifications.notify_moderation(1, "Runner", "Category", 1000)

def test_webhook_payload_and_failure_handling(monkeypatch, caplog):
    from app import notifications
    secret_url = "https://discord.com/api/webhooks/test/secret"
    monkeypatch.setattr(notifications, "settings", SimpleNamespace(
        discord_moderation_webhook_url=secret_url, base_url="https://example.com",
    ))
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(204, request=httpx.Request("POST", url))

    monkeypatch.setattr(notifications.httpx, "post", post)
    notifications.notify_moderation(12, "@everyone", "Build · Category", 1000, True)
    payload = calls[0][1]["json"]
    assert payload["content"] == notifications.MODERATION_MESSAGE
    assert payload["allowed_mentions"] == {"parse": [], "roles": ["1548412791944122469"]}
    assert payload["embeds"][0]["title"] == "Pending run edited"
    assert payload["embeds"][0]["url"] == "https://example.com/runs/12"

    def failing_post(*args, **kwargs):
        raise httpx.ConnectError(secret_url)

    monkeypatch.setattr(notifications.httpx, "post", failing_post)
    notifications.notify_moderation(12, "Runner", "Category", 1000)
    assert "notification failed" in caplog.text
    assert secret_url not in caplog.text

def test_unexpected_notification_failure_does_not_escape(monkeypatch, caplog):
    from app import notifications

    def fail(*args, **kwargs):
        raise ValueError("unexpected error containing a secret")

    monkeypatch.setattr(notifications, "_send_moderation_notification", fail)
    notifications.notify_moderation(1, "Runner", "Category", 1000)
    assert "notification failed" in caplog.text
    assert "containing a secret" not in caplog.text
