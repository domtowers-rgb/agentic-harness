from datetime import datetime
from zoneinfo import ZoneInfo

from agentic_harness import clock


def get_current_time(timezone: str = None):
    """Local time (AGENTIC_TIMEZONE) by default - the same zone as today's
    date in the system prompt and reminder times, so they all agree."""
    if not timezone:
        return {"iso": datetime.now(clock.local_tz()).isoformat(), "timezone": clock.zone_name()}
    try:
        now = datetime.now(ZoneInfo(timezone))
    except Exception as exc:
        return {"error": f"unknown timezone '{timezone}': {exc}"}
    return {"iso": now.isoformat(), "timezone": timezone}


def register(registry):
    registry.register("get_current_time", get_current_time, {
        "name": "get_current_time",
        "description": "Get the current date and time - local time unless a timezone is given.",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "e.g. 'Europe/London'"},
            },
            "required": [],
        },
    })
