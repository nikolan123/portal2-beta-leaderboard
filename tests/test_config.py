from app.config import load_settings

def test_owner_ids_parse_comma_separated_list(monkeypatch):
    monkeypatch.setenv("OWNER_DISCORD_ID", "111, 222 ,, 333 ")
    assert load_settings().owner_discord_ids == ("111", "222", "333")

def test_owner_ids_default_to_empty_tuple(monkeypatch):
    monkeypatch.setenv("OWNER_DISCORD_ID", "")
    assert load_settings().owner_discord_ids == ()
