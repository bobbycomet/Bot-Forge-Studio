"""Community modules: ready-made, self-contained features for servers (generic — nothing is tied to one bot).

* Emote Library — emotes as image files, posted as attachments, visual browser, bulk import
* Birthdays & Anniversaries — announcements, temporary roles, GIFs, custom messages, per-server timezone/time
* Post Router — watch a hub channel (follows, forwards, webhooks) and route posts to channels / forums / threads
* Role Brackets — level-bracket roles with member lists and optional per-game UIDs
* Game Profiles & UIDs — scope brackets and player UIDs per game on a multi-game server
* Task Lists & Reminders — checklists with timers and live progress embeds, one-off and recurring reminders
* Event Scheduler — spawn / drop / countdown reminders, quest board, trade board, guild events with sign-ups
"""
from __future__ import annotations

import re
from pathlib import Path

from .model import NodeSpec, Prop, register
from .modules import fill

COMMUNITY_DIR = Path(__file__).resolve().parent.parent / "runtime" / "community"


def _read(name: str) -> str:
    return (COMMUNITY_DIR / name).read_text(encoding="utf-8")


def _module(node, slug, main: str, support=()):
    out = {name: _read(name) for name in support}
    out[f"cogs/{slug}.py"] = fill(_read(main), node, node.spec)
    return out


def _names_in(main: str) -> list[str]:
    s = _read(main)
    names = re.findall(r'(?:Group|command|context_menu)\(\s*name\s*=\s*"([^"]+)"', s)
    return list(dict.fromkeys(names))


def _top_level(main: str, extra=()) -> list[str]:
    return list(extra)


# ----------------------------------------------------------------------------------------- Emote Library
register(NodeSpec(
    "module.emotes", "Emote Library", "Community", "module",
    props=[Prop("allowed_role_ids", "Extra role IDs allowed to add / remove emotes", "text", "",
                help="Owner, Administrators and Manage Expressions can always manage. Comma or space separated."),
           Prop("max_mb", "Largest emote (MB)", "float", 8)],
    files=lambda n, s: _module(n, s, "bsc_emotes.py"),
    commands=lambda p: ["emote", "emotes", "Browse emotes", "Import emotes from message"],
    requires=("Pillow>=10.1",),
    description="Emotes stored as image files and posted as full-size attachments (no 256 KB / 200-emoji limits): "
                "/emote add|remove|list|send|browse|panel|bulk, /emotes, and right-click menus. Includes a visual picker "
                "and a permanent 'Browse emotes' button."))

# ----------------------------------------------------------------------------------------- Birthdays
register(NodeSpec(
    "module.birthdays", "Birthdays & Anniversaries", "Community", "module",
    files=lambda n, s: _module(n, s, "bsc_birthdays.py"),
    commands=lambda p: ["birthday", "anniversary", "birthday-config", "birthday-help"],
    intents=("members",),
    description="Members set birthdays and community join dates; the bot announces them with embeds, GIFs, a temporary role and "
                "custom messages. Admins set channels, roles, announcement time, timezone and default messages with "
                "/birthday-config. Data stays per server."))

# ----------------------------------------------------------------------------------------- Post Router
ROUTER_SUPPORT = ("bsp_database.py", "bsp_utils.py", "bsp_router.py")
register(NodeSpec(
    "module.post_router", "Post Router", "Community", "module",
    files=lambda n, s: _module(n, s, "bsc_post_router.py", ROUTER_SUPPORT),
    commands=lambda p: ["router", "router-test"],
    intents=("message_content", "members"), requires=("aiosqlite>=0.20",),
    description="Watches a hub channel (channel follows, forwards, webhooks) and routes matching posts to text channels, forums "
                "or specific threads. Match by author, source server/channel and keywords; the most specific mapping wins. "
                "Set up with /router sethub and /router addmapping; /router scan shows the IDs that really arrive."))

# ----------------------------------------------------------------------------------------- Role Brackets
BRACKET_SUPPORT = ("bsp_database.py", "bsp_utils.py")
register(NodeSpec(
    "module.role_brackets", "Role Brackets", "Community", "module",
    files=lambda n, s: _module(n, s, "bsc_role_brackets.py", BRACKET_SUPPORT),
    commands=lambda p: ["bracket"],
    intents=("members",), requires=("aiosqlite>=0.20",),
    description="Level-bracket roles (e.g. 1-10, 11-20…) per game with auto-updated member lists, optional UIDs next to names, "
                "and thread-pinned lists. Pair it with Game Profiles & UIDs."))

# ----------------------------------------------------------------------------------------- Game Profiles & UIDs
GAME_SUPPORT = ("bsp_database.py", "bsp_utils.py", "bsp_uid.py")
register(NodeSpec(
    "module.game_profiles", "Game Profiles & UIDs", "Community", "module",
    files=lambda n, s: _module(n, s, "bsc_game_profiles.py", GAME_SUPPORT),
    commands=lambda p: ["game", "gameuid"],
    requires=("aiosqlite>=0.20",),
    description="Named game profiles (/game add|edit|remove|list|migrate) that keep brackets and player UIDs separate per game, "
                "plus /gameuid set|remove|me|lookup for members' in-game IDs. A richer alternative to UID Registry — use one or the other."))

# ----------------------------------------------------------------------------------------- Task Lists & Reminders
TASK_SUPPORT = ("bsd_common.py", "bsd_db.py", "bsd_timeutil.py", "bsd_ui.py", "bsd_remind.py", "bsd_delivery.py")
register(NodeSpec(
    "module.task_lists", "Task Lists & Reminders", "Community", "module",
    files=lambda n, s: _module(n, s, "bsc_task_lists.py", TASK_SUPPORT),
    commands=lambda p: ["tasklist", "reminder"],
    requires=("aiosqlite>=0.20",),
    description="/tasklist: checklists you can complete, with optional timers, imports, archives and a live public progress embed. "
                "/reminder: one-off and recurring reminders posted in a channel with user/role pings and a daily digest. "
                "Dates are typed in each member's own timezone (/tasklist timezone)."))

# ----------------------------------------------------------------------------------------- Event Scheduler
register(NodeSpec(
    "module.event_scheduler", "Event Scheduler", "Community", "module",
    files=lambda n, s: _module(n, s, "bsc_events.py"),
    commands=lambda p: _names_in("bsc_events.py"),
    requires=("apscheduler>=3.10,<4", "pytz"),
    description="Spawn, item-drop and countdown reminders (with pre-alerts, per-entry timezone), a quest board, a trade board and "
                "guild events/raids with sign-up buttons and rosters. Every 'add' command picks a channel, then opens a form."))
