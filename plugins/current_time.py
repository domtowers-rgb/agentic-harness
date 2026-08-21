from datetime import datetime, timezone as dt_timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None


def get_current_time(timezone: str = "UTC"):
    if not timezone or timezone.upper() == "UTC":
        now = datetime.now(dt_timezone.utc)
        return {"iso": now.isoformat(), "timezone": "UTC"}

    if ZoneInfo is None:
        return {"error": "timezone lookup is unavailable on this Python version"}

    try:
        now = datetime.now(ZoneInfo(timezone))
    except Exception as exc:
        return {"error": f"unknown timezone '{timezone}': {exc}"}
    return {"iso": now.isoformat(), "timezone": timezone}


def register(registry):
    registry.register("get_current_time", get_current_time, {
        "name": "get_current_time",
        "description": "Get the current date and time, optionally in a specific IANA timezone (e.g. 'America/New_York'). Defaults to UTC.",
        "parameters": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "description": "IANA timezone name, e.g. 'Europe/London'. Defaults to UTC."},
            },
            "required": [],
        },
    })
