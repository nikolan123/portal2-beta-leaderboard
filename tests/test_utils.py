from datetime import datetime, timedelta, timezone

import pytest

from app.utils import (
    format_time,
    is_direct_video_url,
    parse_time_to_ms,
    timeago,
    validate_video_url,
    video_embed_url,
    youtube_embed_url,
)

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("42", 42_000),
        ("42.5", 42_500),
        ("1:02.345", 62_345),
        ("1:02:03.004", 3_723_004),
        ("0:59.999", 59_999),
    ],
)

def test_parse_time(raw, expected):
    assert parse_time_to_ms(raw) == expected

@pytest.mark.parametrize("raw", ["", "0", "1:60", "1:2:60", "1:60:00", "1:02.1234", "wat"])
def test_parse_time_rejects_invalid(raw):
    with pytest.raises(ValueError):
        parse_time_to_ms(raw)

def test_format_time():
    assert format_time(62_345) == "1:02.345"
    assert format_time(3_723_004) == "1:02:03.004"

def test_video_url_validation():
    assert validate_video_url("https://youtu.be/example") == "https://youtu.be/example"
    with pytest.raises(ValueError):
        validate_video_url("javascript:alert(1)")

@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=30), "just now"),
        (timedelta(minutes=1), "1 minute ago"),
        (timedelta(minutes=45), "45 minutes ago"),
        (timedelta(hours=1), "1 hour ago"),
        (timedelta(hours=20), "20 hours ago"),
        (timedelta(days=1), "1 day ago"),
        (timedelta(days=6), "6 days ago"),
        (timedelta(days=14), "2 weeks ago"),
        (timedelta(days=60), "2 months ago"),
        (timedelta(days=400), "1 year ago"),
    ],
)
def test_timeago(delta, expected):
    moment = datetime.now(timezone.utc) - delta
    assert timeago(moment) == expected
    assert timeago(moment.replace(tzinfo=None)) == expected

def test_timeago_future_is_just_now():
    moment = datetime.now(timezone.utc) + timedelta(minutes=5)
    assert timeago(moment) == "just now"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://youtu.be/dQw4w9WgXcQ", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"),
        ("https://youtube.com/watch?v=dQw4w9WgXcQ&t=42", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"),
    ],
)
def test_youtube_embed_url(raw, expected):
    assert youtube_embed_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "not a url", "https://vimeo.com/123456", "https://youtu.be/", "javascript:alert(1)"],
)
def test_youtube_embed_url_rejects_non_youtube(raw):
    assert youtube_embed_url(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (
            "https://www.twitch.tv/videos/1234567890",
            "https://player.twitch.tv/?video=1234567890&parent=example.com",
        ),
        (
            "https://twitch.tv/videos/1234567890?t=1h2m3s",
            "https://player.twitch.tv/?video=1234567890&parent=example.com",
        ),
        (
            "https://www.twitch.tv/somechannel/v/1234567890",
            "https://player.twitch.tv/?video=1234567890&parent=example.com",
        ),
    ],
)
def test_twitch_vod_embed_url(raw, expected):
    assert video_embed_url(raw, parent="example.com") == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "https://www.twitch.tv/somechannel",
        "https://www.twitch.tv/videos/notanid",
        "https://clips.twitch.tv/AmusedCautiousPuppyThatsLife",
        "https://vimeo.com/123456",
    ],
)
def test_video_embed_url_rejects_unsupported(raw):
    assert video_embed_url(raw, parent="example.com") is None


def test_video_embed_url_prefers_youtube():
    assert video_embed_url("https://youtu.be/dQw4w9WgXcQ", parent="example.com") == (
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"
    )


@pytest.mark.parametrize(
    "raw",
    [
        "https://example.com/proof.mp4",
        "https://example.com/proof.MP4",
        "https://example.com/videos/run.webm?token=abc",
        "https://example.com/run.ogg",
        "https://example.com/run.mov",
    ],
)
def test_is_direct_video_url(raw):
    assert is_direct_video_url(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not a url",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.twitch.tv/videos/1234567890",
        "https://example.com/watch",
        "https://example.com/proof.mp4/extra",
        "https://example.com/video.webm.html",
    ],
)
def test_is_direct_video_url_rejects_non_files(raw):
    assert not is_direct_video_url(raw)
