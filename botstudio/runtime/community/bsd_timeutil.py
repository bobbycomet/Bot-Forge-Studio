"""Time parsing and formatting helpers.

Display times use Discord's <t:UNIX:F> / <t:UNIX:R> markup, which every client
renders in the *viewer's own* timezone — that's how each person sees local time.
The per-user timezone we store is only used to interpret dates people TYPE IN.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

DEFAULT_REMINDER_HOUR = 9  # a date with no time fires at 9:00 AM in the user's timezone
MAX_TIMER_SECONDS = 366 * 24 * 3600

COMMON_ZONES = [
    "UTC", "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu",
    "America/Toronto", "America/Sao_Paulo", "Europe/London", "Europe/Paris",
    "Europe/Berlin", "Europe/Moscow", "Africa/Johannesburg", "Asia/Dubai",
    "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo", "Australia/Sydney",
    "Pacific/Auckland",
]

# Human-friendly interval presets for recurrence
INTERVAL_PRESETS = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
    "biweekly": 1209600,
    "monthly": 2592000,  # ~30 days
}


class TimeParseError(ValueError):
    """Raised with a user-friendly message when a time string can't be understood."""


@dataclass(frozen=True)
class ParsedWhen:
    kind: str          # "date" or "timer"
    due_at: int        # unix timestamp (for timers: computed from *now*, recomputed on save)
    seconds: int = 0   # timer length in seconds (0 for dates)


_TIMER_RE = re.compile(r"^(\d{1,4}):([0-5]\d)$")
_DATE_RE = re.compile(
    r"^(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):([0-5]\d)\s*([ap]m)?)?$", re.IGNORECASE
)
_INTERVAL_RE = re.compile(
    r"^(?:every\s+)?(\d+)\s*(h|hr|hrs|hour|hours|d|day|days|w|wk|week|weeks)$",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _zone_map() -> dict[str, str]:
    return {z.lower(): z for z in available_timezones()}


def resolve_zone(query: str) -> Optional[str]:
    """Return the canonical IANA name for a user-typed zone, or None."""
    return _zone_map().get(query.strip().lower().replace(" ", "_"))


def search_zones(query: str, limit: int = 25) -> list[str]:
    q = query.strip().lower().replace(" ", "_")
    if not q:
        return COMMON_ZONES[:limit]
    return sorted(z for low, z in _zone_map().items() if q in low)[:limit]


def get_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo("UTC")


def parse_when(raw: str, tz_name: str, now: Optional[int] = None) -> ParsedWhen:
    """Parse 'HH:MM' (timer), 'MM/DD/YYYY' or 'MM/DD/YYYY HH:MM[ am/pm]' (date)."""
    raw = raw.strip()
    now = int(time.time()) if now is None else now

    m = _TIMER_RE.match(raw)
    if m:
        seconds = int(m[1]) * 3600 + int(m[2]) * 60
        if seconds <= 0:
            raise TimeParseError("A timer has to be longer than 00:00.")
        if seconds > MAX_TIMER_SECONDS:
            raise TimeParseError("Timers can be at most one year long. Use a date instead.")
        return ParsedWhen("timer", now + seconds, seconds)

    m = _DATE_RE.match(raw)
    if m:
        month, day, year = int(m[1]), int(m[2]), int(m[3])
        if m[4] is None:
            hour, minute = DEFAULT_REMINDER_HOUR, 0
        else:
            hour, minute = int(m[4]), int(m[5])
            if m[6]:
                if not 1 <= hour <= 12:
                    raise TimeParseError("With AM/PM the hour must be between 1 and 12.")
                hour = hour % 12 + (12 if m[6].lower() == "pm" else 0)
            elif hour > 23:
                raise TimeParseError("The hour must be between 00 and 23.")
        try:
            local = datetime(year, month, day, hour, minute, tzinfo=get_zone(tz_name))
        except ValueError:
            raise TimeParseError("That date doesn't exist on the calendar.") from None
        due = int(local.timestamp())
        if due <= now:
            raise TimeParseError("That moment has already passed — pick a future date.")
        return ParsedWhen("date", due, 0)

    raise TimeParseError(
        "Use **MM/DD/YYYY** (or **MM/DD/YYYY HH:MM**) for a date, or **HH:MM** for a timer."
    )


def parse_interval(raw: str) -> Optional[int]:
    """Parse 'daily', 'weekly', 'every 2 days', '12h', etc. → seconds, or None."""
    raw = raw.strip().lower()
    if not raw or raw in ("none", "once", "one-shot", "oneshot"):
        return None
    if raw in INTERVAL_PRESETS:
        return INTERVAL_PRESETS[raw]
    m = _INTERVAL_RE.match(raw)
    if m:
        n = int(m[1])
        unit = m[2][0]  # h / d / w
        if unit == "h":
            return n * 3600
        if unit == "d":
            return n * 86400
        if unit == "w":
            return n * 604800
    raise TimeParseError(
        "Repeat as **daily**, **weekly**, **every 2 days**, **12h**, or leave blank for one-shot."
    )


def format_interval(seconds: Optional[int]) -> str:
    if not seconds:
        return "one-shot"
    for name, secs in INTERVAL_PRESETS.items():
        if seconds == secs:
            return name
    if seconds % 604800 == 0:
        n = seconds // 604800
        return f"every {n} week{'s' if n != 1 else ''}"
    if seconds % 86400 == 0:
        n = seconds // 86400
        return f"every {n} day{'s' if n != 1 else ''}"
    if seconds % 3600 == 0:
        n = seconds // 3600
        return f"every {n} hour{'s' if n != 1 else ''}"
    return f"every {seconds}s"


def prefill_when(
    kind: str, due_at: int, pending: bool, tz_name: str, now: Optional[int] = None
) -> str:
    """Text to pre-fill the 'when' field with when editing an existing task."""
    now = int(time.time()) if now is None else now
    if kind == "timer" and pending and due_at > now:
        minutes = max(1, -(-(due_at - now) // 60))  # ceil to whole minutes
        h, m = divmod(minutes, 60)
        return f"{h:02d}:{m:02d}"
    return datetime.fromtimestamp(due_at, get_zone(tz_name)).strftime("%m/%d/%Y %H:%M")


def when_text(unix: int) -> str:
    """Absolute + relative time that renders in each viewer's own timezone."""
    return f"<t:{unix}:F> (<t:{unix}:R>)"
