from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")
TIME_RE = re.compile(r"^\s*(?:(\d{1,2}):)?(?:(\d{1,2}):)?(\d{1,2})(?:[\.,](\d{1,3}))?\s*$")

def parse_time_to_ms(raw: str) -> int:
    """Parse SS(.mmm), MM:SS(.mmm), or HH:MM:SS(.mmm) into milliseconds."""
    value = raw.strip()
    if not value:
        raise ValueError("Time is required.")

    parts = value.replace(",", ".").split(":")
    if len(parts) > 3:
        raise ValueError("Use SS.mmm, MM:SS.mmm, or HH:MM:SS.mmm.")

    try:
        if len(parts) == 1:
            hours = 0
            minutes = 0
            seconds_part = parts[0]
        elif len(parts) == 2:
            hours = 0
            minutes = int(parts[0])
            seconds_part = parts[1]
        else:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds_part = parts[2]

        if "." in seconds_part:
            seconds_text, millis_text = seconds_part.split(".", 1)
        else:
            seconds_text, millis_text = seconds_part, ""

        seconds = int(seconds_text)
        if not seconds_text or (millis_text and (not millis_text.isdigit() or len(millis_text) > 3)):
            raise ValueError

        millis = int(millis_text.ljust(3, "0")) if millis_text else 0
    except ValueError as exc:
        raise ValueError("Use SS.mmm, MM:SS.mmm, or HH:MM:SS.mmm.") from exc

    if hours < 0 or minutes < 0 or seconds < 0:
        raise ValueError("Time cannot be negative.")
    if len(parts) >= 2 and seconds >= 60:
        raise ValueError("Seconds must be below 60 when using colons.")
    if len(parts) == 3 and minutes >= 60:
        raise ValueError("Minutes must be below 60 when using hours.")

    total_ms = ((hours * 3600 + minutes * 60 + seconds) * 1000) + millis
    if total_ms <= 0:
        raise ValueError("Time must be greater than zero.")
    if total_ms > 24 * 60 * 60 * 1000:
        raise ValueError("Time must be 24 hours or less.")

    return total_ms

def format_time(time_ms: int) -> str:
    total_seconds, millis = divmod(time_ms, 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}.{millis:03d}"

    return f"{minutes}:{seconds:02d}.{millis:03d}"

def validate_video_url(raw: str) -> str:
    value = raw.strip()
    if len(value) > 500:
        raise ValueError("Video URL is too long.")

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Enter a valid http:// or https:// video URL.")

    return value

def validate_optional_url(raw: str, label: str) -> str:
    """Validate an optional URL field; blank stays blank."""
    value = raw.strip()
    if not value:
        return ""

    if len(value) > 500:
        raise ValueError(f"{label} is too long.")

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Enter a valid http:// or https:// {label.lower()}.")

    return value

def youtube_embed_url(raw: str) -> str | None:
    """Return a youtube-nocookie embed URL for YouTube links, else None."""
    try:
        parsed = urlparse(raw.strip())
    except ValueError:
        return None

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    video_id = ""
    if host == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0]
    elif host in {"youtube.com", "m.youtube.com", "youtube-nocookie.com"}:
        if parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/embed/", "/shorts/", "/live/")):
            video_id = parsed.path.rsplit("/", 1)[-1]

    if not YOUTUBE_ID_RE.match(video_id):
        return None
    return f"https://www.youtube-nocookie.com/embed/{video_id}"

def twitch_vod_embed_url(raw: str, parent: str) -> str | None:
    """Return a Twitch player embed URL for VOD links, else None.

    Twitch requires the embedding site's hostname as ``parent``.
    """
    try:
        parsed = urlparse(raw.strip())
    except ValueError:
        return None

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    if host != "twitch.tv":
        return None

    parts = [part for part in parsed.path.split("/") if part]
    video_id = ""
    if len(parts) == 2 and parts[0] == "videos":
        video_id = parts[1]
    elif len(parts) == 3 and parts[1] == "v":
        video_id = parts[2]
    if not video_id.isdigit():
        return None
    return f"https://player.twitch.tv/?video={video_id}&parent={parent}"

def video_embed_url(raw: str, parent: str = "localhost") -> str | None:
    """Return an embeddable player URL for supported video links, else None."""
    return youtube_embed_url(raw) or twitch_vod_embed_url(raw, parent)

DIRECT_VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".ogg", ".ogv", ".mov", ".m4v"})

def is_direct_video_url(raw: str) -> bool:
    """Whether the URL points directly at a playable video file."""
    try:
        path = urlparse(raw.strip()).path.lower()
    except ValueError:
        return False
    return any(path.endswith(extension) for extension in DIRECT_VIDEO_EXTENSIONS)

def _ago(count: int, unit: str) -> str:
    return f"1 {unit} ago" if count == 1 else f"{count} {unit}s ago"

def timeago(value: datetime) -> str:
    """Human-relative time like '3 days ago'. Naive datetimes are UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    seconds = int((datetime.now(timezone.utc) - value).total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "just now"

    minutes = seconds // 60
    if minutes < 60:
        return _ago(minutes, "minute")

    hours = minutes // 60
    if hours < 24:
        return _ago(hours, "hour")

    days = hours // 24
    if days < 7:
        return _ago(days, "day")

    weeks = days // 7
    if days < 30:
        return _ago(weeks, "week")

    months = days // 30
    if months < 12:
        return _ago(months, "month")

    return _ago(days // 365, "year")

def ordinal(value: int) -> str:
    if 10 <= value % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"
