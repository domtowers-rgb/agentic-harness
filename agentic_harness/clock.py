"""The time zone everything local is in: today's date in the system prompt,
get_current_time's default, and reminder times. This machine may well run
on UTC while its users don't."""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

TIMEZONE = os.environ.get("AGENTIC_TIMEZONE", "")


def local_tz():
    """AGENTIC_TIMEZONE, or this machine's own zone if unset or unknown."""
    try:
        return ZoneInfo(TIMEZONE) if TIMEZONE else datetime.now().astimezone().tzinfo
    except Exception:
        return datetime.now().astimezone().tzinfo


def zone_name() -> str:
    """Its name, e.g. "Europe/London" (or the machine's abbreviation)."""
    return TIMEZONE or datetime.now().astimezone().tzname()
