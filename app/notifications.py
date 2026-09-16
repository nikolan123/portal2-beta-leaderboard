import logging
import re

import httpx

from .config import settings
from .utils import format_time

# Discord role syntax: <@&ROLE_ID>
MODERATION_MESSAGE = "<@&1548412791944122469> A run needs review."

logger = logging.getLogger(__name__)

def notify_moderation(run_id: int, runner_name: str, category_name: str, time_ms: int, edited: bool = False) -> None:
    try:
        _send_moderation_notification(run_id, runner_name, category_name, time_ms, edited)
    except Exception:
        logger.warning("Discord moderation notification failed for run %s", run_id)

def _send_moderation_notification(run_id: int, runner_name: str, category_name: str, time_ms: int, edited: bool) -> None:
    if not settings.discord_moderation_webhook_url:
        return
    
    payload = {
        "content": MODERATION_MESSAGE,
        "allowed_mentions": {
            "parse": [],
            "roles": list(dict.fromkeys(re.findall(r"<@&(\d+)>", MODERATION_MESSAGE))),
        },
        "embeds": [{
            "title": "Pending run edited" if edited else "New run awaiting review",
            "url": f"{settings.base_url}/runs/{run_id}",
            "color": 0x9ECBFF,
            "fields": [
                {"name": "Runner", "value": runner_name[:1024], "inline": True},
                {"name": "Category", "value": category_name[:1024]},
                {"name": "Time", "value": format_time(time_ms), "inline": True},
                {"name": "Review", "value": f"[Open moderation]({settings.base_url}/moderation)"},
            ],
        }],
    }
    
    response = httpx.post(settings.discord_moderation_webhook_url, json=payload, timeout=5)
    response.raise_for_status()
