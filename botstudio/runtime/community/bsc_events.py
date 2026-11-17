"""Event Scheduler — spawn / drop / countdown reminders, a quest board, a trade board and guild events with signups
(Bot Forge Studio community module).

Every "add" command takes one argument — the target channel, picked from an autocomplete dropdown — and opens a form
(modal) for the details.  Edit commands open a pre-filled form; remove commands ask for confirmation.

  Spawn reminders   /addspawn /editspawn /removespawn /listspawns /nextspawn
  Item drops        /addrop /editdrop /removedrop /listdrops /nextdrop
  Countdowns        /countdown /editcountdown /removecountdown /listcountdowns /nextcountdown
  Quest board       /questpost /listquests
  Trade board       /tradepost /listtrades
  Guild events      /eventcreate /listevents      (sign-up buttons, roster, cancel)
  Utilities         /upcoming /listtargets /events-help

Discord modals hold at most 5 fields, so related values share a field (e.g. "14:30 America/New_York").
Times are scheduled with APScheduler in each entry's own timezone; data lives in data/events.json.
"""
import os
import json
import time
import uuid
import pytz
import asyncio
import logging
import tempfile
from threading import RLock
from typing import Optional, Tuple, Dict, Any, List
from datetime import datetime, timedelta

import discord
from discord.ext import commands
from discord import app_commands, ui, Interaction

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

# ───────────────────────────────
# Logging
# ───────────────────────────────
log = logging.getLogger("bot.events")

# ───────────────────────────────
# Globals & storage
# ───────────────────────────────
DATA_FILE = os.path.join(os.getenv("DATA_DIR", "data"), "events.json")
os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
DATA_LOCK = RLock()
DATA: Dict[str, Any] = {}
scheduler = AsyncIOScheduler()

bot = None   # the running commands.Bot; assigned by EventsCog.cog_load

_COMMANDS: list = []        # slash commands collected at import time, registered by the cog


def _cmd(**kw):
    """Stand-in for @bot.tree.command: build the command now, add it to the tree when the cog loads."""
    def deco(fn):
        c = app_commands.command(**kw)(fn)
        _COMMANDS.append(c)
        return c
    return deco

# ───────────────────────────────
# Time helpers
# ───────────────────────────────
TZ_ALIASES = {
    "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
    "MST": "America/Denver",      "MDT": "America/Denver",
    "CST": "America/Chicago",     "CDT": "America/Chicago",
    "EST": "America/New_York",    "EDT": "America/New_York",
    "GMT": "Etc/GMT",             "UTC": "UTC",
}

def normalize_tz(tz_name: str) -> Optional[str]:
    if not tz_name:
        return None
    tz_name = tz_name.strip()
    if tz_name in TZ_ALIASES:
        return TZ_ALIASES[tz_name]
    try:
        pytz.timezone(tz_name)
        return tz_name
    except Exception:
        return None

def validate_time(hour: int, minute: int) -> bool:
    return isinstance(hour, int) and isinstance(minute, int) and 0 <= hour <= 23 and 0 <= minute <= 59

def unix_for_today_local(hour: int, minute: int, tz_name: str) -> Optional[int]:
    tz_norm = normalize_tz(tz_name)
    if not tz_norm or not validate_time(hour, minute):
        return None
    try:
        tz = pytz.timezone(tz_norm)
        dt = datetime.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)
        return int(dt.timestamp())
    except Exception:
        return None

def next_event_unix_from_local(hour: int, minute: int, tz_name: str) -> Optional[int]:
    tz_norm = normalize_tz(tz_name)
    if not tz_norm or not validate_time(hour, minute):
        return None
    try:
        tz = pytz.timezone(tz_norm)
        now = datetime.now(tz)
        today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return int(today.timestamp()) if today > now else int((today + timedelta(days=1)).timestamp())
    except Exception:
        return None

def unix_from_mmddyyyy_and_hm(mmddyyyy: str, hour: int, minute: int, tz_name: str) -> Optional[int]:
    tz_norm = normalize_tz(tz_name)
    if not tz_norm or not validate_time(hour, minute):
        return None
    try:
        month, day, year = map(int, mmddyyyy.strip().split("-"))
        tz = pytz.timezone(tz_norm)
        return int(tz.localize(datetime(year, month, day, hour, minute)).timestamp())
    except Exception:
        return None

def compute_daily_reminder_hm(hour: int, minute: int, tz_name: str, offset_minutes: int) -> Optional[Tuple[int, int]]:
    tz_norm = normalize_tz(tz_name)
    if not tz_norm or not validate_time(hour, minute) or offset_minutes <= 0:
        return None
    try:
        tz = pytz.timezone(tz_norm)
        base = tz.localize(datetime(2000, 1, 1, hour, minute))
        remind_dt = base - timedelta(minutes=offset_minutes)
        return remind_dt.hour, remind_dt.minute
    except Exception:
        return None

def format_days_hhmm(seconds_left: int) -> Tuple[int, str]:
    seconds_left = max(0, seconds_left)
    days = seconds_left // 86400
    rem  = seconds_left % 86400
    return days, f"{rem // 3600:02d}:{(rem % 3600) // 60:02d}"

def make_id() -> str:
    return str(uuid.uuid4())[:8]

def parse_hhmm(raw: str) -> Optional[Tuple[int, int]]:
    """Parse 'HH:MM' into (hour, minute)."""
    raw = raw.strip()
    if ":" in raw:
        parts = raw.split(":", 1)
        try:
            h, m = int(parts[0]), int(parts[1])
            if validate_time(h, m):
                return h, m
        except ValueError:
            pass
    return None

# ───────────────────────────────
# JSON persistence (atomic)
# ───────────────────────────────
def load_data() -> None:
    global DATA
    with DATA_LOCK:
        if not os.path.exists(DATA_FILE):
            DATA = {}
            return
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            DATA = raw if isinstance(raw, dict) else {}
        except json.JSONDecodeError as e:
            log.error("Corrupted JSON (%s): %s", DATA_FILE, e)
            try:
                os.replace(DATA_FILE, DATA_FILE + ".corrupted")
            except Exception:
                pass
            DATA = {}
        except Exception:
            log.exception("Unexpected read error; starting fresh.")
            DATA = {}

def _atomic_write(path: str, payload: Any) -> None:
    dirpath = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix="data-", suffix=".tmp", dir=dirpath)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise

def save_data() -> None:
    with DATA_LOCK:
        try:
            _atomic_write(DATA_FILE, DATA)
        except Exception:
            log.exception("Failed to save DATA.")

def ensure_guild(gid: str) -> None:
    with DATA_LOCK:
        g = DATA.setdefault(gid, {})
        g.setdefault("spawns",     [])
        g.setdefault("itemdrops",  [])
        g.setdefault("countdowns", [])
        g.setdefault("quests",     [])
        g.setdefault("trades",     [])
        g.setdefault("events",     [])

# ───────────────────────────────
# Discord helpers
# ───────────────────────────────
def safe_mention(target_id: int) -> str:
    return f"<#{target_id}>"

async def _resolve_channel(target_id: int):
    for g in bot.guilds:
        obj = g.get_channel(target_id) or g.get_thread(target_id)
        if obj:
            return obj
    try:
        return await bot.fetch_channel(target_id)
    except Exception:
        return None

async def post_message(
    target_id: int,
    text: str = "",
    embed: discord.Embed = None,
    view: discord.ui.View = None,
) -> Optional[int]:
    try:
        obj = await _resolve_channel(target_id)
        if not obj:
            log.warning("Target %s not found.", target_id)
            return None
        kwargs: Dict[str, Any] = {}
        if text:
            kwargs["content"] = text
        if embed:
            kwargs["embed"] = embed
        if view:
            kwargs["view"] = view
        msg = await obj.send(**kwargs)
        return msg.id
    except discord.Forbidden:
        log.error("Forbidden posting to %s", target_id)
    except discord.NotFound:
        log.error("NotFound for %s", target_id)
    except Exception:
        log.exception("post_message error target=%s", target_id)
    return None

async def delete_message(target_id: int, message_id: int) -> None:
    try:
        obj = await _resolve_channel(target_id)
        if obj:
            msg = await obj.fetch_message(message_id)
            await msg.delete()
    except Exception:
        pass

# ───────────────────────────────
# Misc helpers
# ───────────────────────────────
def _gid(interaction: Interaction) -> Optional[str]:
    return str(interaction.guild_id) if interaction.guild_id else None

def is_admin_or_mod(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild.owner_id == member.id:
        return True
    mod_names = {"mod", "moderator", "admin", "administrator", "officer"}
    return any(r.name.lower() in mod_names for r in member.roles)

async def target_autocomplete(interaction: Interaction, current: str) -> List[app_commands.Choice[str]]:
    guild = interaction.guild
    if not guild:
        return []
    current_l = current.lower()
    choices: List[app_commands.Choice[str]] = []
    for ch in guild.text_channels:
        if not current_l or current_l in ch.name.lower():
            choices.append(app_commands.Choice(name=f"# {ch.name}", value=str(ch.id)))
        if len(choices) >= 25:
            return choices
    for th in guild.threads:
        if not current_l or current_l in th.name.lower():
            parent = f" @ #{th.parent.name}" if th.parent else ""
            choices.append(app_commands.Choice(name=f"💬 {th.name}{parent}", value=str(th.id)))
        if len(choices) >= 25:
            return choices
    return choices

# ───────────────────────────────
# Scheduling
# ───────────────────────────────
def schedule_all() -> None:
    scheduler.remove_all_jobs()
    with DATA_LOCK:
        for gid, conf in DATA.items():
            ensure_guild(gid)

            for s in conf.get("spawns", []):
                tz_norm = normalize_tz(s.get("tz", ""))
                if not tz_norm:
                    continue
                try:
                    tz_obj = pytz.timezone(tz_norm)
                except Exception:
                    continue

                async def spawn_job(sp=s):
                    ts = unix_for_today_local(sp["hour"], sp["minute"], sp["tz"])
                    if ts:
                        await post_message(sp["target_id"],
                            f"🪄 **{sp['name']}** may spawn now at {sp['location']} → <t:{ts}:t>")

                scheduler.add_job(spawn_job, CronTrigger(hour=s["hour"], minute=s["minute"], timezone=tz_obj))
                rim = s.get("remind_in_minutes")
                if isinstance(rim, int) and rim > 0:
                    hm = compute_daily_reminder_hm(s["hour"], s["minute"], s["tz"], rim)
                    if hm:
                        async def spawn_pre(sp=s):
                            ts = next_event_unix_from_local(sp["hour"], sp["minute"], sp["tz"])
                            if ts:
                                await post_message(sp["target_id"],
                                    f"🔔 **Reminder**: `{sp['name']}` at {sp['location']} in <t:{ts}:R> → <t:{ts}:t>")
                        scheduler.add_job(spawn_pre, CronTrigger(hour=hm[0], minute=hm[1], timezone=tz_obj))

            for d in conf.get("itemdrops", []):
                tz_norm = normalize_tz(d.get("tz", ""))
                if not tz_norm:
                    continue
                try:
                    tz_obj = pytz.timezone(tz_norm)
                except Exception:
                    continue

                async def drop_job(dr=d):
                    ts = unix_for_today_local(dr["hour"], dr["minute"], dr["tz"])
                    if ts:
                        await post_message(dr["target_id"],
                            f"💎 **{dr['item']}** is available now → <t:{ts}:t>")

                scheduler.add_job(drop_job, CronTrigger(hour=d["hour"], minute=d["minute"], timezone=tz_obj))
                rim = d.get("remind_in_minutes")
                if isinstance(rim, int) and rim > 0:
                    hm = compute_daily_reminder_hm(d["hour"], d["minute"], d["tz"], rim)
                    if hm:
                        async def drop_pre(dr=d):
                            ts = next_event_unix_from_local(dr["hour"], dr["minute"], dr["tz"])
                            if ts:
                                await post_message(dr["target_id"],
                                    f"🔔 **Reminder**: `{dr['item']}` in <t:{ts}:R> → <t:{ts}:t>")
                        scheduler.add_job(drop_pre, CronTrigger(hour=hm[0], minute=hm[1], timezone=tz_obj))

            for c in conf.get("countdowns", []):
                tz_norm = normalize_tz(c.get("tz", ""))
                if not tz_norm:
                    continue
                try:
                    tz_obj = pytz.timezone(tz_norm)
                except Exception:
                    continue
                cd_name = c["name"]

                async def cd_job(guild_id=gid, cd=c, _name=cd_name):
                    target_ts = unix_from_mmddyyyy_and_hm(cd["date"], cd["hour"], cd["minute"], cd["tz"])
                    if not target_ts:
                        return
                    now = int(time.time())
                    if now < target_ts:
                        left = target_ts - now
                        days, hhmm = format_days_hhmm(left)
                        dlabel = "day" if days == 1 else "days"
                        msg_id = await post_message(cd["target_id"],
                            f"⏳ **{cd['name']}**\n**{days} {dlabel} and {hhmm}** left "
                            f"until <t:{target_ts}:F> (**<t:{target_ts}:R>**)")
                        if msg_id:
                            with DATA_LOCK:
                                for x in DATA[guild_id]["countdowns"]:
                                    if x["name"] == _name:
                                        x["last_message_id"] = msg_id
                                        break
                                save_data()
                    else:
                        final = cd.get("final_message") or f"🎉 **{cd['name']}** is happening now! <t:{target_ts}:F>"
                        await post_message(cd["target_id"], final)
                        last_id = cd.get("last_message_id")
                        if isinstance(last_id, int):
                            await delete_message(cd["target_id"], last_id)
                        with DATA_LOCK:
                            DATA[guild_id]["countdowns"] = [
                                x for x in DATA[guild_id]["countdowns"] if x["name"] != _name
                            ]
                            save_data()
                        asyncio.get_event_loop().call_soon(schedule_all)

                scheduler.add_job(cd_job, CronTrigger(hour=c["hour"], minute=c["minute"], timezone=tz_obj))

# ═══════════════════════════════════════════════════════════════
# SHARED UI
# ═══════════════════════════════════════════════════════════════

class ConfirmView(ui.View):
    def __init__(self, on_confirm):
        super().__init__(timeout=30)
        self.on_confirm = on_confirm

    @ui.button(label="✅ Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: Interaction, button: ui.Button):
        for child in self.children:
            child.disabled = True
        try:
            await self.on_confirm(interaction)
        finally:
            self.stop()

    @ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction: Interaction, button: ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.send_message("Cancelled.", ephemeral=True)
        self.stop()

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True

# ═══════════════════════════════════════════════════════════════
# SPAWN MODAL
# Fields:  Name | Location | Game | Time+TZ ("HH:MM TZ") | Reminder mins
# ═══════════════════════════════════════════════════════════════
class SpawnModal(ui.Modal, title="🪄 Spawn Reminder"):
    spawn_name = ui.TextInput(label="Spawn Name", placeholder="e.g. World Boss", max_length=80)
    location   = ui.TextInput(label="Location", placeholder="e.g. Northern Forest, Grid C4", max_length=120)
    game       = ui.TextInput(label="Game", placeholder="e.g. New World", max_length=60)
    time_tz    = ui.TextInput(
        label="Time + Timezone  (HH:MM TIMEZONE)",
        placeholder="14:30 UTC   or   18:00 America/New_York",
        max_length=60,
    )
    remind     = ui.TextInput(
        label="Pre-reminder minutes (0 or blank = none)",
        placeholder="30",
        required=False,
        max_length=4,
    )

    def __init__(self, target_id: int, gid: str, editing_name: Optional[str] = None):
        super().__init__()
        self.target_id    = target_id
        self.gid          = gid
        self.editing_name = editing_name

    async def on_submit(self, interaction: Interaction):
        name     = self.spawn_name.value.strip()
        loc      = self.location.value.strip()
        game_val = self.game.value.strip()
        raw_rem  = (self.remind.value or "").strip()

        parts = self.time_tz.value.strip().split(None, 1)
        if len(parts) != 2:
            await interaction.response.send_message(
                "❌ **Time + Timezone** must be `HH:MM TIMEZONE`, e.g. `14:30 UTC`.", ephemeral=True)
            return
        hm = parse_hhmm(parts[0])
        if hm is None:
            await interaction.response.send_message("❌ Invalid time. Use `HH:MM` (24-hour).", ephemeral=True)
            return
        hour, minute = hm
        tz_norm = normalize_tz(parts[1])
        if not tz_norm:
            await interaction.response.send_message(f"❌ Unknown timezone `{parts[1]}`.", ephemeral=True)
            return

        remind_mins: Optional[int] = None
        if raw_rem and raw_rem != "0":
            try:
                remind_mins = int(raw_rem)
                if not (1 <= remind_mins <= 1439):
                    raise ValueError
            except ValueError:
                await interaction.response.send_message("❌ Pre-reminder must be 1–1439 minutes.", ephemeral=True)
                return

        ensure_guild(self.gid)

        if self.editing_name:
            updated = False
            with DATA_LOCK:
                for s in DATA[self.gid]["spawns"]:
                    if s["name"].lower() == self.editing_name.lower():
                        s.update(name=name, location=loc, game=game_val,
                                 hour=hour, minute=minute, tz=tz_norm,
                                 remind_in_minutes=remind_mins)
                        updated = True
                        break
                if updated:
                    save_data()
            if not updated:
                await interaction.response.send_message("❌ Spawn not found.", ephemeral=True)
                return
            schedule_all()
            await interaction.response.send_message(f"✏️ Spawn **{name}** updated.", ephemeral=True)
        else:
            with DATA_LOCK:
                if any(s["name"].lower() == name.lower() for s in DATA[self.gid]["spawns"]):
                    await interaction.response.send_message(
                        f"❌ A spawn named **{name}** already exists.", ephemeral=True)
                    return
                DATA[self.gid]["spawns"].append(dict(
                    name=name, location=loc, game=game_val,
                    hour=hour, minute=minute, tz=tz_norm,
                    target_id=self.target_id, remind_in_minutes=remind_mins,
                ))
                save_data()
            schedule_all()
            ts    = unix_for_today_local(hour, minute, tz_norm)
            msg   = f"🪄 Added spawn **{name}** [{game_val}] at {loc} in {safe_mention(self.target_id)} → <t:{ts}:t>"
            if remind_mins:
                msg += f"\n🔔 Pre-reminder: **{remind_mins} min** before."
            await interaction.response.send_message(msg)

# ═══════════════════════════════════════════════════════════════
# ITEM DROP MODAL
# Fields:  Item name | Game | Time+TZ | Reminder mins
# ═══════════════════════════════════════════════════════════════
class ItemDropModal(ui.Modal, title="💎 Item Drop Reminder"):
    item_name = ui.TextInput(label="Item Name", placeholder="e.g. Dragon Scale", max_length=80)
    game      = ui.TextInput(label="Game", placeholder="e.g. Lost Ark", max_length=60)
    time_tz   = ui.TextInput(
        label="Time + Timezone  (HH:MM TIMEZONE)",
        placeholder="18:00 UTC   or   09:30 America/Chicago",
        max_length=60,
    )
    remind    = ui.TextInput(
        label="Pre-reminder minutes (0 or blank = none)",
        placeholder="15",
        required=False,
        max_length=4,
    )

    def __init__(self, target_id: int, gid: str, editing_item: Optional[str] = None):
        super().__init__()
        self.target_id    = target_id
        self.gid          = gid
        self.editing_item = editing_item

    async def on_submit(self, interaction: Interaction):
        item     = self.item_name.value.strip()
        game_val = self.game.value.strip()
        raw_rem  = (self.remind.value or "").strip()

        parts = self.time_tz.value.strip().split(None, 1)
        if len(parts) != 2:
            await interaction.response.send_message(
                "❌ **Time + Timezone** must be `HH:MM TIMEZONE`, e.g. `18:00 UTC`.", ephemeral=True)
            return
        hm = parse_hhmm(parts[0])
        if hm is None:
            await interaction.response.send_message("❌ Invalid time. Use `HH:MM` (24-hour).", ephemeral=True)
            return
        hour, minute = hm
        tz_norm = normalize_tz(parts[1])
        if not tz_norm:
            await interaction.response.send_message(f"❌ Unknown timezone `{parts[1]}`.", ephemeral=True)
            return

        remind_mins: Optional[int] = None
        if raw_rem and raw_rem != "0":
            try:
                remind_mins = int(raw_rem)
                if not (1 <= remind_mins <= 1439):
                    raise ValueError
            except ValueError:
                await interaction.response.send_message("❌ Pre-reminder must be 1–1439 minutes.", ephemeral=True)
                return

        ensure_guild(self.gid)

        if self.editing_item:
            updated = False
            with DATA_LOCK:
                for d in DATA[self.gid]["itemdrops"]:
                    if d["item"].lower() == self.editing_item.lower():
                        d.update(item=item, game=game_val, hour=hour, minute=minute,
                                 tz=tz_norm, remind_in_minutes=remind_mins)
                        updated = True
                        break
                if updated:
                    save_data()
            if not updated:
                await interaction.response.send_message("❌ Drop not found.", ephemeral=True)
                return
            schedule_all()
            await interaction.response.send_message(f"✏️ Drop **{item}** updated.", ephemeral=True)
        else:
            with DATA_LOCK:
                if any(d["item"].lower() == item.lower() for d in DATA[self.gid]["itemdrops"]):
                    await interaction.response.send_message(
                        f"❌ A drop for **{item}** already exists.", ephemeral=True)
                    return
                DATA[self.gid]["itemdrops"].append(dict(
                    item=item, game=game_val, hour=hour, minute=minute, tz=tz_norm,
                    target_id=self.target_id, remind_in_minutes=remind_mins,
                ))
                save_data()
            schedule_all()
            ts  = unix_for_today_local(hour, minute, tz_norm)
            msg = f"💎 Added drop **{item}** [{game_val}] in {safe_mention(self.target_id)} → <t:{ts}:t>"
            if remind_mins:
                msg += f"\n🔔 Pre-reminder: **{remind_mins} min** before."
            await interaction.response.send_message(msg)

# ═══════════════════════════════════════════════════════════════
# COUNTDOWN MODAL
# Fields:  Name | Target date+time+tz | Daily ping time+tz (or "same") | Final message
# ═══════════════════════════════════════════════════════════════
class CountdownModal(ui.Modal, title="⏳ Countdown"):
    cd_name    = ui.TextInput(label="Countdown Name", placeholder="e.g. DLC Launch", max_length=80)
    target_dt  = ui.TextInput(
        label="Target date + time + timezone",
        placeholder="MM-DD-YYYY HH:MM TIMEZONE   e.g. 12-31-2025 20:00 UTC",
        max_length=60,
    )
    ping_time  = ui.TextInput(
        label='Daily ping time + timezone  (or "same")',
        placeholder='same   or   09:00 America/New_York',
        max_length=60,
    )
    final_msg  = ui.TextInput(
        label="Final message when it fires (optional)",
        placeholder="🎉 The DLC is live!",
        required=False,
        max_length=200,
    )

    def __init__(self, target_id: int, gid: str, editing_name: Optional[str] = None):
        super().__init__()
        self.target_id    = target_id
        self.gid          = gid
        self.editing_name = editing_name

    async def on_submit(self, interaction: Interaction):
        name     = self.cd_name.value.strip()
        raw_dt   = self.target_dt.value.strip()
        raw_ping = self.ping_time.value.strip()
        final    = self.final_msg.value.strip() or None

        # Parse "MM-DD-YYYY HH:MM TIMEZONE"
        dt_parts = raw_dt.split()
        if len(dt_parts) < 3:
            await interaction.response.send_message(
                "❌ Target date must be `MM-DD-YYYY HH:MM TIMEZONE`, e.g. `12-31-2025 20:00 UTC`.",
                ephemeral=True)
            return
        date_str  = dt_parts[0]
        hm        = parse_hhmm(dt_parts[1])
        target_tz = normalize_tz(dt_parts[2])
        if hm is None:
            await interaction.response.send_message("❌ Invalid time in target date field.", ephemeral=True)
            return
        if not target_tz:
            await interaction.response.send_message(f"❌ Unknown timezone `{dt_parts[2]}`.", ephemeral=True)
            return
        target_hour, target_min = hm
        target_unix = unix_from_mmddyyyy_and_hm(date_str, target_hour, target_min, target_tz)
        if target_unix is None:
            await interaction.response.send_message("❌ Invalid date. Use MM-DD-YYYY.", ephemeral=True)
            return

        # Parse daily ping time
        if raw_ping.lower() == "same":
            ping_hour, ping_min, ping_tz = target_hour, target_min, target_tz
        else:
            ping_parts = raw_ping.split()
            if len(ping_parts) < 2:
                await interaction.response.send_message(
                    '❌ Daily ping must be `HH:MM TIMEZONE` or the word `same`.', ephemeral=True)
                return
            p_hm    = parse_hhmm(ping_parts[0])
            ping_tz = normalize_tz(ping_parts[1])
            if p_hm is None:
                await interaction.response.send_message("❌ Invalid daily ping time.", ephemeral=True)
                return
            if not ping_tz:
                await interaction.response.send_message(f"❌ Unknown timezone `{ping_parts[1]}`.", ephemeral=True)
                return
            ping_hour, ping_min = p_hm

        ensure_guild(self.gid)

        if self.editing_name:
            updated = False
            with DATA_LOCK:
                for c in DATA[self.gid]["countdowns"]:
                    if c["name"].lower() == self.editing_name.lower():
                        c.update(name=name, date=date_str, hour=ping_hour, minute=ping_min,
                                 tz=ping_tz, final_message=final)
                        updated = True
                        break
                if updated:
                    save_data()
            if not updated:
                await interaction.response.send_message("❌ Countdown not found.", ephemeral=True)
                return
            schedule_all()
            await interaction.response.send_message(f"✏️ Countdown **{name}** updated.", ephemeral=True)
        else:
            with DATA_LOCK:
                if any(c["name"].lower() == name.lower() for c in DATA[self.gid]["countdowns"]):
                    await interaction.response.send_message(
                        f"❌ A countdown named **{name}** already exists.", ephemeral=True)
                    return
                DATA[self.gid]["countdowns"].append(dict(
                    name=name, date=date_str, hour=ping_hour, minute=ping_min,
                    tz=ping_tz, target_id=self.target_id,
                    final_message=final, last_message_id=None,
                ))
                save_data()
            schedule_all()
            where = safe_mention(self.target_id)
            await interaction.response.send_message(
                f"⏳ Countdown **{name}** added → fires <t:{target_unix}:F> (**<t:{target_unix}:R>**) in {where}")

# ═══════════════════════════════════════════════════════════════
# QUEST MODAL + BOARD
# Fields:  Title | Game | Description
# ═══════════════════════════════════════════════════════════════
class QuestModal(ui.Modal, title="⚔️ Post a Quest / Bounty"):
    quest_title = ui.TextInput(label="Quest Title", placeholder="e.g. Need help clearing Dragon Lair", max_length=100)
    game        = ui.TextInput(label="Game", placeholder="e.g. Final Fantasy XIV", max_length=60)
    description = ui.TextInput(
        label="Description / Details",
        style=discord.TextStyle.paragraph,
        placeholder="What do you need help with? Any requirements, rewards, or time preferences?",
        max_length=800,
    )

    def __init__(self, channel_id: int, gid: str):
        super().__init__()
        self.channel_id = channel_id
        self.gid        = gid

    async def on_submit(self, interaction: Interaction):
        qid   = make_id()
        entry = dict(
            id=qid, poster_id=interaction.user.id, poster_name=str(interaction.user),
            game=self.game.value.strip(), title=self.quest_title.value.strip(),
            description=self.description.value.strip(),
            status="open", accepted_by=None, accepted_name=None,
            channel_id=self.channel_id, message_id=None,
        )
        ensure_guild(self.gid)
        with DATA_LOCK:
            DATA[self.gid]["quests"].append(entry)
            save_data()
        await interaction.response.send_message("✅ Quest posted!", ephemeral=True)
        msg_id = await post_message(self.channel_id, embed=build_quest_embed(entry), view=QuestBoardView(qid, self.gid))
        if msg_id:
            with DATA_LOCK:
                for q in DATA[self.gid]["quests"]:
                    if q["id"] == qid:
                        q["message_id"] = msg_id
                        break
                save_data()

def build_quest_embed(e: dict) -> discord.Embed:
    emoji = {"open": "🟢", "accepted": "🟡", "closed": "🔴"}.get(e["status"], "⚪")
    color = {"open": 0x57F287, "accepted": 0xFEE75C, "closed": 0xED4245}.get(e["status"], 0x95A5A6)
    embed = discord.Embed(title=f"⚔️ {e['title']}", description=e["description"], color=color)
    embed.add_field(name="🎮 Game",      value=e["game"],                            inline=True)
    embed.add_field(name="📋 Status",    value=f"{emoji} {e['status'].capitalize()}", inline=True)
    embed.add_field(name="👤 Posted by", value=e["poster_name"],                     inline=True)
    if e.get("accepted_name"):
        embed.add_field(name="🤝 Accepted by", value=e["accepted_name"], inline=True)
    embed.set_footer(text=f"Quest ID: {e['id']}")
    return embed

class QuestBoardView(ui.View):
    def __init__(self, quest_id: str, gid: str):
        super().__init__(timeout=None)
        self.quest_id = quest_id
        self.gid      = gid

    @ui.button(label="✅ Accept Quest", style=discord.ButtonStyle.success, custom_id="quest_accept")
    async def accept(self, interaction: Interaction, button: ui.Button):
        gid = str(interaction.guild_id)
        ensure_guild(gid)
        with DATA_LOCK:
            quest = next((q for q in DATA[gid]["quests"] if q["id"] == self.quest_id), None)
            if not quest:
                await interaction.response.send_message("❌ Quest not found.", ephemeral=True)
                return
            if quest["status"] != "open":
                await interaction.response.send_message("❌ Already taken or closed.", ephemeral=True)
                return
            if quest["poster_id"] == interaction.user.id:
                await interaction.response.send_message("❌ Can't accept your own quest.", ephemeral=True)
                return
            quest.update(status="accepted", accepted_by=interaction.user.id, accepted_name=str(interaction.user))
            save_data()
        for child in self.children:
            if getattr(child, "custom_id", None) == "quest_accept":
                child.disabled = True
        await interaction.message.edit(embed=build_quest_embed(quest), view=self)
        await interaction.response.send_message(
            f"🤝 You accepted **{quest['title']}**! Reach out to <@{quest['poster_id']}> to coordinate.",
            ephemeral=True)

    @ui.button(label="🔴 Close", style=discord.ButtonStyle.danger, custom_id="quest_close")
    async def close(self, interaction: Interaction, button: ui.Button):
        gid = str(interaction.guild_id)
        ensure_guild(gid)
        with DATA_LOCK:
            quest = next((q for q in DATA[gid]["quests"] if q["id"] == self.quest_id), None)
            if not quest:
                await interaction.response.send_message("❌ Quest not found.", ephemeral=True)
                return
            if interaction.user.id != quest["poster_id"] and not is_admin_or_mod(interaction.user):
                await interaction.response.send_message("❌ Only the poster or a mod can close this.", ephemeral=True)
                return
            quest["status"] = "closed"
            save_data()
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(embed=build_quest_embed(quest), view=self)
        await interaction.response.send_message("🔴 Quest closed.", ephemeral=True)

# ═══════════════════════════════════════════════════════════════
# TRADE MODAL + BOARD
# Fields:  Game | Offering | Wanting (optional) | Currency (optional)
# ═══════════════════════════════════════════════════════════════
class TradeModal(ui.Modal, title="🔄 Post a Trade Offer"):
    game     = ui.TextInput(label="Game", placeholder="e.g. Path of Exile", max_length=60)
    offering = ui.TextInput(label="What you're offering", placeholder="e.g. 3× Iron Ore, legendary sword", max_length=200)
    wanting  = ui.TextInput(
        label="What you want (blank = open to offers)",
        placeholder="e.g. 500 gold  /  any rare gem  /  leave blank for open",
        required=False,
        max_length=200,
    )
    currency = ui.TextInput(
        label="In-game currency price (leave blank if N/A)",
        placeholder="e.g. 500 Gold  /  200 Coins",
        required=False,
        max_length=100,
    )

    def __init__(self, channel_id: int, gid: str):
        super().__init__()
        self.channel_id = channel_id
        self.gid        = gid

    async def on_submit(self, interaction: Interaction):
        tid   = make_id()
        entry = dict(
            id=tid, poster_id=interaction.user.id, poster_name=str(interaction.user),
            game=self.game.value.strip(), offering=self.offering.value.strip(),
            wanting=self.wanting.value.strip() or "Open to offers",
            currency=self.currency.value.strip() or None,
            status="open", accepted_by=None, accepted_name=None,
            channel_id=self.channel_id, message_id=None,
        )
        ensure_guild(self.gid)
        with DATA_LOCK:
            DATA[self.gid]["trades"].append(entry)
            save_data()
        await interaction.response.send_message("✅ Trade posted!", ephemeral=True)
        msg_id = await post_message(self.channel_id, embed=build_trade_embed(entry), view=TradeBoardView(tid, self.gid))
        if msg_id:
            with DATA_LOCK:
                for t in DATA[self.gid]["trades"]:
                    if t["id"] == tid:
                        t["message_id"] = msg_id
                        break
                save_data()

def build_trade_embed(e: dict) -> discord.Embed:
    emoji = {"open": "🟢", "accepted": "🟡", "closed": "🔴"}.get(e["status"], "⚪")
    color = {"open": 0x3498DB, "accepted": 0xFEE75C, "closed": 0xED4245}.get(e["status"], 0x95A5A6)
    embed = discord.Embed(title="🔄 Trade Offer", color=color)
    embed.add_field(name="🎮 Game",     value=e["game"],                             inline=True)
    embed.add_field(name="📋 Status",   value=f"{emoji} {e['status'].capitalize()}", inline=True)
    embed.add_field(name="👤 Trader",   value=e["poster_name"],                      inline=True)
    embed.add_field(name="📦 Offering", value=e["offering"],                         inline=False)
    embed.add_field(name="🤲 Wanting",  value=e["wanting"],                          inline=False)
    if e.get("currency"):
        embed.add_field(name="💰 Currency Price", value=e["currency"], inline=True)
    if e.get("accepted_name"):
        embed.add_field(name="🤝 Accepted by",    value=e["accepted_name"], inline=True)
    embed.set_footer(text=f"Trade ID: {e['id']}")
    return embed

class TradeBoardView(ui.View):
    def __init__(self, trade_id: str, gid: str):
        super().__init__(timeout=None)
        self.trade_id = trade_id
        self.gid      = gid

    @ui.button(label="🤝 Accept Trade", style=discord.ButtonStyle.success, custom_id="trade_accept")
    async def accept(self, interaction: Interaction, button: ui.Button):
        gid = str(interaction.guild_id)
        ensure_guild(gid)
        with DATA_LOCK:
            trade = next((t for t in DATA[gid]["trades"] if t["id"] == self.trade_id), None)
            if not trade:
                await interaction.response.send_message("❌ Trade not found.", ephemeral=True)
                return
            if trade["status"] != "open":
                await interaction.response.send_message("❌ This trade is no longer open.", ephemeral=True)
                return
            if trade["poster_id"] == interaction.user.id:
                await interaction.response.send_message("❌ Can't accept your own trade.", ephemeral=True)
                return
            trade.update(status="accepted", accepted_by=interaction.user.id, accepted_name=str(interaction.user))
            save_data()
        for child in self.children:
            if getattr(child, "custom_id", None) == "trade_accept":
                child.disabled = True
        await interaction.message.edit(embed=build_trade_embed(trade), view=self)
        await interaction.response.send_message(
            f"🤝 Trade accepted! Reach out to <@{trade['poster_id']}> to complete the exchange.", ephemeral=True)

    @ui.button(label="🔴 Close", style=discord.ButtonStyle.danger, custom_id="trade_close")
    async def close(self, interaction: Interaction, button: ui.Button):
        gid = str(interaction.guild_id)
        ensure_guild(gid)
        with DATA_LOCK:
            trade = next((t for t in DATA[gid]["trades"] if t["id"] == self.trade_id), None)
            if not trade:
                await interaction.response.send_message("❌ Trade not found.", ephemeral=True)
                return
            if interaction.user.id != trade["poster_id"] and not is_admin_or_mod(interaction.user):
                await interaction.response.send_message("❌ Only the poster or a mod can close this.", ephemeral=True)
                return
            trade["status"] = "closed"
            save_data()
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(embed=build_trade_embed(trade), view=self)
        await interaction.response.send_message("🔴 Trade closed.", ephemeral=True)

# ═══════════════════════════════════════════════════════════════
# EVENT MODAL + SIGNUP
# Fields:  Event name | Game | Type + Description (type on line 1) | Date+time+tz | Max slots
# ═══════════════════════════════════════════════════════════════
class EventModal(ui.Modal, title="🏰 Create Guild Event / Raid"):
    event_name    = ui.TextInput(label="Event Name", placeholder="e.g. Saturday Night Raid", max_length=100)
    game          = ui.TextInput(label="Game", placeholder="e.g. World of Warcraft", max_length=60)
    type_and_desc = ui.TextInput(
        label="Type on line 1, description below",
        style=discord.TextStyle.paragraph,
        placeholder="raid\nWe're going for the final boss. Bring consumables. Min ilvl 400.",
        max_length=820,
    )
    event_dt      = ui.TextInput(
        label="Date + time + timezone",
        placeholder="MM-DD-YYYY HH:MM TIMEZONE   e.g. 06-15-2025 20:00 EST",
        max_length=60,
    )
    slots         = ui.TextInput(label="Max slots (0 = unlimited)", placeholder="10", max_length=3)

    def __init__(self, channel_id: int, gid: str):
        super().__init__()
        self.channel_id = channel_id
        self.gid        = gid

    async def on_submit(self, interaction: Interaction):
        title    = self.event_name.value.strip()
        game_val = self.game.value.strip()
        raw_td   = self.type_and_desc.value.strip()
        raw_dt   = self.event_dt.value.strip()

        try:
            max_slots = int(self.slots.value.strip())
            if max_slots < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message("❌ Max slots must be a non-negative integer.", ephemeral=True)
            return

        td_lines = raw_td.split("\n", 1)
        etype    = td_lines[0].strip().lower() or "other"
        desc     = td_lines[1].strip() if len(td_lines) > 1 else ""

        dt_parts = raw_dt.split()
        if len(dt_parts) < 3:
            await interaction.response.send_message(
                "❌ Date field must be `MM-DD-YYYY HH:MM TIMEZONE`.", ephemeral=True)
            return
        date_str  = dt_parts[0]
        hm        = parse_hhmm(dt_parts[1])
        tz_norm   = normalize_tz(dt_parts[2])
        if hm is None:
            await interaction.response.send_message("❌ Invalid time in event date field.", ephemeral=True)
            return
        if not tz_norm:
            await interaction.response.send_message(f"❌ Unknown timezone `{dt_parts[2]}`.", ephemeral=True)
            return
        hour, minute = hm
        target_ts = unix_from_mmddyyyy_and_hm(date_str, hour, minute, tz_norm)
        if target_ts is None:
            await interaction.response.send_message("❌ Invalid date. Use MM-DD-YYYY.", ephemeral=True)
            return

        eid   = make_id()
        entry = dict(
            id=eid, creator_id=interaction.user.id, creator_name=str(interaction.user),
            game=game_val, title=title, description=desc, event_type=etype,
            date=date_str, hour=hour, minute=minute, tz=tz_norm,
            target_ts=target_ts, max_slots=max_slots, signups=[],
            channel_id=self.channel_id, message_id=None, status="open",
        )
        ensure_guild(self.gid)
        with DATA_LOCK:
            DATA[self.gid]["events"].append(entry)
            save_data()
        await interaction.response.send_message("✅ Event created!", ephemeral=True)
        msg_id = await post_message(self.channel_id, embed=build_event_embed(entry), view=EventSignupView(eid, self.gid))
        if msg_id:
            with DATA_LOCK:
                for ev in DATA[self.gid]["events"]:
                    if ev["id"] == eid:
                        ev["message_id"] = msg_id
                        break
                save_data()

def build_event_embed(e: dict) -> discord.Embed:
    type_emoji = {"raid":"⚔️","dungeon":"🏰","pvp":"🛡️","event":"🎉","other":"📌"}.get(e.get("event_type","other"),"📌")
    color      = 0xE91E63 if e["status"] == "open" else 0x95A5A6
    ts         = e.get("target_ts")
    time_str   = f"<t:{ts}:F> (<t:{ts}:R>)" if ts else "Unknown time"
    signups    = e.get("signups", [])
    max_s      = e.get("max_slots", 0)
    slot_str   = f"{len(signups)}/{max_s}" if max_s else f"{len(signups)} (unlimited)"
    roster     = "\n".join(f"• {s['name']}" for s in signups) if signups else "*No signups yet — be the first!*"
    status_str = "🟢 Open" if e["status"] == "open" else "🔴 Closed"
    embed = discord.Embed(title=f"{type_emoji} {e['title']}", description=e.get("description",""), color=color)
    embed.add_field(name="🎮 Game",         value=e["game"],       inline=True)
    embed.add_field(name="📋 Status",       value=status_str,      inline=True)
    embed.add_field(name="🗓️ When",         value=time_str,        inline=False)
    embed.add_field(name="👥 Signups",      value=slot_str,        inline=True)
    embed.add_field(name="🧙 Organized by", value=e["creator_name"],inline=True)
    embed.add_field(name="📝 Roster",       value=roster[:1024],   inline=False)
    embed.set_footer(text=f"Event ID: {e['id']}")
    return embed

class EventSignupView(ui.View):
    def __init__(self, event_id: str, gid: str):
        super().__init__(timeout=None)
        self.event_id = event_id
        self.gid      = gid

    @ui.button(label="✅ Sign Up", style=discord.ButtonStyle.success, custom_id="event_signup")
    async def signup(self, interaction: Interaction, button: ui.Button):
        gid = str(interaction.guild_id)
        ensure_guild(gid)
        with DATA_LOCK:
            event = next((ev for ev in DATA[gid]["events"] if ev["id"] == self.event_id), None)
            if not event:
                await interaction.response.send_message("❌ Event not found.", ephemeral=True)
                return
            if event["status"] != "open":
                await interaction.response.send_message("❌ This event is closed.", ephemeral=True)
                return
            if any(s["uid"] == interaction.user.id for s in event["signups"]):
                await interaction.response.send_message("✅ You're already signed up!", ephemeral=True)
                return
            max_s = event.get("max_slots", 0)
            if max_s > 0 and len(event["signups"]) >= max_s:
                await interaction.response.send_message("❌ Event is full.", ephemeral=True)
                return
            event["signups"].append({"uid": interaction.user.id, "name": str(interaction.user)})
            save_data()
        await interaction.message.edit(embed=build_event_embed(event), view=self)
        await interaction.response.send_message(f"✅ Signed up for **{event['title']}**!", ephemeral=True)

    @ui.button(label="🚪 Withdraw", style=discord.ButtonStyle.secondary, custom_id="event_withdraw")
    async def withdraw(self, interaction: Interaction, button: ui.Button):
        gid = str(interaction.guild_id)
        ensure_guild(gid)
        with DATA_LOCK:
            event = next((ev for ev in DATA[gid]["events"] if ev["id"] == self.event_id), None)
            if not event:
                await interaction.response.send_message("❌ Event not found.", ephemeral=True)
                return
            before = len(event["signups"])
            event["signups"] = [s for s in event["signups"] if s["uid"] != interaction.user.id]
            if len(event["signups"]) == before:
                await interaction.response.send_message("❌ You weren't signed up.", ephemeral=True)
                return
            save_data()
        await interaction.message.edit(embed=build_event_embed(event), view=self)
        await interaction.response.send_message("🚪 You've withdrawn from the event.", ephemeral=True)

    @ui.button(label="🔴 Cancel Event", style=discord.ButtonStyle.danger, custom_id="event_cancel")
    async def cancel(self, interaction: Interaction, button: ui.Button):
        gid = str(interaction.guild_id)
        ensure_guild(gid)
        with DATA_LOCK:
            event = next((ev for ev in DATA[gid]["events"] if ev["id"] == self.event_id), None)
            if not event:
                await interaction.response.send_message("❌ Event not found.", ephemeral=True)
                return
            if interaction.user.id != event["creator_id"] and not is_admin_or_mod(interaction.user):
                await interaction.response.send_message("❌ Only the organizer or a mod can cancel.", ephemeral=True)
                return
            event["status"] = "closed"
            save_data()
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(embed=build_event_embed(event), view=self)
        await interaction.response.send_message("🔴 Event cancelled.", ephemeral=True)

# ═══════════════════════════════════════════════════════════════
# BOT EVENTS
# ═══════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════
# SLASH COMMANDS
# ═══════════════════════════════════════════════════════════════

# ─── /help ───
@_cmd(name="events-help", description="How to use the event scheduler")
async def help_command(interaction: Interaction):
    msg = (
        "## 🧙‍♂️ Event Scheduler — Help\n\n"
        "**Every add/create command takes just one step: pick a channel from the dropdown.**\n"
        "A pop-up form instantly appears to fill in the rest — no long slash arguments to type.\n\n"

        "### 🪄 Spawn Reminders\n"
        "`/addspawn #channel` → form  |  `/editspawn name`  |  `/removespawn name`\n"
        "`/listspawns`  |  `/nextspawn`\n\n"

        "### 💎 Item Drop Reminders\n"
        "`/addrop #channel` → form  |  `/editdrop item`  |  `/removedrop item`\n"
        "`/listdrops`  |  `/nextdrop`\n\n"

        "### ⏳ Countdowns\n"
        "`/countdown #channel` → form  |  `/editcountdown name`  |  `/removecountdown name`\n"
        "`/listcountdowns`  |  `/nextcountdown`\n\n"

        "### ⚔️ Quest / Bounty Board\n"
        "`/questpost #channel` → form  |  Others click **✅ Accept** or **🔴 Close**\n"
        "`/listquests`\n\n"

        "### 🔄 Trade Board\n"
        "`/tradepost #channel` → form  |  Others click **🤝 Accept Trade** or **🔴 Close**\n"
        "`/listtrades`\n\n"

        "### 🏰 Guild Events & Raids *(admin / mod / owner only to create)*\n"
        "`/eventcreate #channel` → form  |  Members click **✅ Sign Up** / **🚪 Withdraw**\n"
        "`/listevents`\n\n"

        "### 📅 Utilities\n"
        "`/upcoming hours`  |  `/listtargets`\n\n"

        "### 🕐 Form time formats\n"
        "**Time + Timezone** field:  `HH:MM TIMEZONE`  e.g. `14:30 UTC` or `09:00 America/Chicago`\n"
        "**Date + Time + TZ** field:  `MM-DD-YYYY HH:MM TIMEZONE`  e.g. `12-31-2025 20:00 EST`\n"
        "Short codes: `UTC  EST  CST  MST  PST  EDT  CDT  MDT  PDT  GMT`\n"
        "IANA names:  `America/New_York  Europe/London  Asia/Tokyo  …`"
    )
    await interaction.response.send_message(msg, ephemeral=True)

# ─── SPAWNS ───
@_cmd(name="addspawn", description="🪄 Add a spawn reminder — pick a channel, form opens instantly")
@app_commands.describe(target="Channel or thread to post spawn alerts in")
@app_commands.autocomplete(target=target_autocomplete)
async def addspawn(interaction: Interaction, target: str):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    try:
        target_id = int(target)
    except Exception:
        await interaction.response.send_message("❌ Invalid channel selection.", ephemeral=True)
        return
    await interaction.response.send_modal(SpawnModal(target_id, gid))

@_cmd(name="editspawn", description="🛠️ Edit a spawn — opens a pre-filled form")
@app_commands.describe(name="Exact name of the spawn to edit")
async def editspawn(interaction: Interaction, name: str):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        spawn = next((s for s in DATA[gid]["spawns"] if s["name"].lower() == name.lower()), None)
    if not spawn:
        await interaction.response.send_message("❌ Spawn not found.", ephemeral=True)
        return
    modal = SpawnModal(spawn["target_id"], gid, editing_name=spawn["name"])
    modal.spawn_name.default = spawn["name"]
    modal.location.default   = spawn.get("location", "")
    modal.game.default       = spawn.get("game", "")
    modal.time_tz.default    = f"{spawn['hour']:02d}:{spawn['minute']:02d} {spawn.get('tz','UTC')}"
    modal.remind.default     = str(spawn.get("remind_in_minutes") or "")
    await interaction.response.send_modal(modal)

@_cmd(name="removespawn", description="🗑️ Remove a spawn — shows a confirm button")
@app_commands.describe(name="Exact name of the spawn to remove")
async def removespawn(interaction: Interaction, name: str):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        exists = any(s["name"].lower() == name.lower() for s in DATA[gid]["spawns"])
    if not exists:
        await interaction.response.send_message("❌ Spawn not found.", ephemeral=True)
        return

    async def do_remove(i: Interaction):
        with DATA_LOCK:
            DATA[gid]["spawns"] = [s for s in DATA[gid]["spawns"] if s["name"].lower() != name.lower()]
            save_data()
        schedule_all()
        await i.response.send_message(f"✅ Spawn **{name}** removed.")

    await interaction.response.send_message(
        f"⚠️ Remove spawn **{name}**?", view=ConfirmView(do_remove), ephemeral=True)

@_cmd(name="listspawns", description="📜 List all spawns")
async def listspawns(interaction: Interaction):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        spawns = list(DATA[gid]["spawns"])
    if not spawns:
        await interaction.response.send_message("No spawns configured.")
        return
    lines = ["🪄 **Spawns:**"]
    for s in spawns:
        ts    = unix_for_today_local(s["hour"], s["minute"], s["tz"])
        stamp = f"<t:{ts}:t>" if ts else "(invalid time)"
        game  = f" [{s.get('game','')}]" if s.get("game") else ""
        rem   = f" • reminder {s['remind_in_minutes']}m before" if s.get("remind_in_minutes") else ""
        lines.append(f"- **{s['name']}**{game} @ {s.get('location','')} {stamp} → {safe_mention(s['target_id'])}{rem}")
    await interaction.response.send_message("\n".join(lines))

@_cmd(name="nextspawn", description="🪄 Show the next upcoming spawn")
async def nextspawn(interaction: Interaction):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        spawns = list(DATA[gid]["spawns"])
    best, best_ts = None, None
    for s in spawns:
        ts = next_event_unix_from_local(s["hour"], s["minute"], s["tz"])
        if ts and (best_ts is None or ts < best_ts):
            best_ts, best = ts, s
    if not best:
        await interaction.response.send_message("No spawns configured.")
        return
    await interaction.response.send_message(
        f"🪄 Next: **{best['name']}** @ {best.get('location','')} → <t:{best_ts}:t> (<t:{best_ts}:R>) in {safe_mention(best['target_id'])}")

# ─── ITEM DROPS ───
@_cmd(name="addrop", description="💎 Add an item drop reminder — pick a channel, form opens instantly")
@app_commands.describe(target="Channel or thread to post drop alerts in")
@app_commands.autocomplete(target=target_autocomplete)
async def addrop(interaction: Interaction, target: str):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    try:
        target_id = int(target)
    except Exception:
        await interaction.response.send_message("❌ Invalid channel selection.", ephemeral=True)
        return
    await interaction.response.send_modal(ItemDropModal(target_id, gid))

@_cmd(name="editdrop", description="🛠️ Edit an item drop — opens a pre-filled form")
@app_commands.describe(item="Exact item name to edit")
async def editdrop(interaction: Interaction, item: str):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        drop = next((d for d in DATA[gid]["itemdrops"] if d["item"].lower() == item.lower()), None)
    if not drop:
        await interaction.response.send_message("❌ Drop not found.", ephemeral=True)
        return
    modal = ItemDropModal(drop["target_id"], gid, editing_item=drop["item"])
    modal.item_name.default = drop["item"]
    modal.game.default      = drop.get("game", "")
    modal.time_tz.default   = f"{drop['hour']:02d}:{drop['minute']:02d} {drop.get('tz','UTC')}"
    modal.remind.default    = str(drop.get("remind_in_minutes") or "")
    await interaction.response.send_modal(modal)

@_cmd(name="removedrop", description="🗑️ Remove an item drop — shows a confirm button")
@app_commands.describe(item="Exact item name to remove")
async def removedrop(interaction: Interaction, item: str):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        exists = any(d["item"].lower() == item.lower() for d in DATA[gid]["itemdrops"])
    if not exists:
        await interaction.response.send_message("❌ Drop not found.", ephemeral=True)
        return

    async def do_remove(i: Interaction):
        with DATA_LOCK:
            DATA[gid]["itemdrops"] = [d for d in DATA[gid]["itemdrops"] if d["item"].lower() != item.lower()]
            save_data()
        schedule_all()
        await i.response.send_message(f"✅ Drop **{item}** removed.")

    await interaction.response.send_message(
        f"⚠️ Remove drop **{item}**?", view=ConfirmView(do_remove), ephemeral=True)

@_cmd(name="listdrops", description="📜 List all item drops")
async def listdrops(interaction: Interaction):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        drops = list(DATA[gid]["itemdrops"])
    if not drops:
        await interaction.response.send_message("No drops configured.")
        return
    lines = ["💎 **Item Drops:**"]
    for d in drops:
        ts    = unix_for_today_local(d["hour"], d["minute"], d["tz"])
        stamp = f"<t:{ts}:t>" if ts else "(invalid time)"
        game  = f" [{d.get('game','')}]" if d.get("game") else ""
        rem   = f" • reminder {d['remind_in_minutes']}m before" if d.get("remind_in_minutes") else ""
        lines.append(f"- **{d['item']}**{game} {stamp} → {safe_mention(d['target_id'])}{rem}")
    await interaction.response.send_message("\n".join(lines))

@_cmd(name="nextdrop", description="💎 Show the next upcoming item drop")
async def nextdrop(interaction: Interaction):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        drops = list(DATA[gid]["itemdrops"])
    best, best_ts = None, None
    for d in drops:
        ts = next_event_unix_from_local(d["hour"], d["minute"], d["tz"])
        if ts and (best_ts is None or ts < best_ts):
            best_ts, best = ts, d
    if not best:
        await interaction.response.send_message("No drops configured.")
        return
    await interaction.response.send_message(
        f"💎 Next: **{best['item']}** → <t:{best_ts}:t> (<t:{best_ts}:R>) in {safe_mention(best['target_id'])}")

# ─── COUNTDOWNS ───
@_cmd(name="countdown", description="⏳ Add a countdown — pick a channel, form opens instantly")
@app_commands.describe(target="Channel or thread for daily countdown posts")
@app_commands.autocomplete(target=target_autocomplete)
async def countdown_cmd(interaction: Interaction, target: str):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    try:
        target_id = int(target)
    except Exception:
        await interaction.response.send_message("❌ Invalid channel selection.", ephemeral=True)
        return
    await interaction.response.send_modal(CountdownModal(target_id, gid))

@_cmd(name="editcountdown", description="🛠️ Edit a countdown — opens a pre-filled form")
@app_commands.describe(name="Exact countdown name to edit")
async def editcountdown(interaction: Interaction, name: str):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        cd = next((c for c in DATA[gid]["countdowns"] if c["name"].lower() == name.lower()), None)
    if not cd:
        await interaction.response.send_message("❌ Countdown not found.", ephemeral=True)
        return
    modal = CountdownModal(cd["target_id"], gid, editing_name=cd["name"])
    modal.cd_name.default   = cd["name"]
    modal.target_dt.default = f"{cd.get('date','')} {cd['hour']:02d}:{cd['minute']:02d} {cd.get('tz','UTC')}"
    modal.ping_time.default = "same"
    modal.final_msg.default = cd.get("final_message") or ""
    await interaction.response.send_modal(modal)

@_cmd(name="removecountdown", description="🗑️ Remove a countdown — shows a confirm button")
@app_commands.describe(name="Exact countdown name to remove")
async def removecountdown(interaction: Interaction, name: str):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        match = next((c for c in DATA[gid]["countdowns"] if c["name"].lower() == name.lower()), None)
    if not match:
        await interaction.response.send_message("❌ Countdown not found.", ephemeral=True)
        return
    match_name = match["name"]

    async def do_remove(i: Interaction):
        last_id = match.get("last_message_id")
        if isinstance(last_id, int):
            await delete_message(match["target_id"], last_id)
        with DATA_LOCK:
            DATA[gid]["countdowns"] = [c for c in DATA[gid]["countdowns"] if c["name"] != match_name]
            save_data()
        schedule_all()
        await i.response.send_message(f"✅ Countdown **{name}** removed.")

    await interaction.response.send_message(
        f"⚠️ Remove countdown **{name}**?", view=ConfirmView(do_remove), ephemeral=True)

@_cmd(name="listcountdowns", description="📜 List all countdowns")
async def listcountdowns(interaction: Interaction):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        cds = list(DATA[gid]["countdowns"])
    if not cds:
        await interaction.response.send_message("No countdowns configured.")
        return
    lines = ["⏳ **Countdowns:**"]
    for c in cds:
        ts    = unix_from_mmddyyyy_and_hm(c["date"], c["hour"], c["minute"], c["tz"])
        stamp = f"<t:{ts}:F> (<t:{ts}:R>)" if ts else "(invalid)"
        lines.append(f"- **{c['name']}** → {stamp} in {safe_mention(c['target_id'])}")
    await interaction.response.send_message("\n".join(lines))

@_cmd(name="nextcountdown", description="⏳ Show the soonest countdown")
async def nextcountdown(interaction: Interaction):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        cds = list(DATA[gid]["countdowns"])
    best, best_ts = None, None
    for c in cds:
        ts = unix_from_mmddyyyy_and_hm(c["date"], c["hour"], c["minute"], c["tz"])
        if ts and (best_ts is None or ts < best_ts):
            best_ts, best = ts, c
    if not best:
        await interaction.response.send_message("No countdowns configured.")
        return
    left = max(0, best_ts - int(time.time()))
    days, hhmm = format_days_hhmm(left)
    dlabel = "day" if days == 1 else "days"
    await interaction.response.send_message(
        f"⏳ **{best['name']}** → <t:{best_ts}:F> (<t:{best_ts}:R>)\n"
        f"**{days} {dlabel} and {hhmm}** remaining — in {safe_mention(best['target_id'])}")

# ─── QUEST BOARD ───
@_cmd(name="questpost", description="⚔️ Post a quest or bounty — pick a channel, form opens instantly")
@app_commands.describe(target="Channel to post the quest board message in")
@app_commands.autocomplete(target=target_autocomplete)
async def questpost(interaction: Interaction, target: str):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    try:
        channel_id = int(target)
    except Exception:
        await interaction.response.send_message("❌ Invalid channel selection.", ephemeral=True)
        return
    await interaction.response.send_modal(QuestModal(channel_id, gid))

@_cmd(name="listquests", description="⚔️ List open quests")
async def listquests(interaction: Interaction):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        quests = [q for q in DATA[gid]["quests"] if q["status"] == "open"]
    if not quests:
        await interaction.response.send_message("No open quests right now.")
        return
    lines = ["⚔️ **Open Quests:**"]
    for q in quests:
        lines.append(f"- **{q['title']}** [{q['game']}] — {q['poster_name']} (ID: `{q['id']}`)")
    await interaction.response.send_message("\n".join(lines))

# ─── TRADE BOARD ───
@_cmd(name="tradepost", description="🔄 Post a trade offer — pick a channel, form opens instantly")
@app_commands.describe(target="Channel to post the trade board message in")
@app_commands.autocomplete(target=target_autocomplete)
async def tradepost(interaction: Interaction, target: str):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    try:
        channel_id = int(target)
    except Exception:
        await interaction.response.send_message("❌ Invalid channel selection.", ephemeral=True)
        return
    await interaction.response.send_modal(TradeModal(channel_id, gid))

@_cmd(name="listtrades", description="🔄 List open trades")
async def listtrades(interaction: Interaction):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        trades = [t for t in DATA[gid]["trades"] if t["status"] == "open"]
    if not trades:
        await interaction.response.send_message("No open trades right now.")
        return
    lines = ["🔄 **Open Trades:**"]
    for t in trades:
        cur = f" | 💰 {t['currency']}" if t.get("currency") else ""
        lines.append(f"- **{t['offering']}** for **{t['wanting']}**{cur} [{t['game']}] — {t['poster_name']} (ID: `{t['id']}`)")
    await interaction.response.send_message("\n".join(lines))

# ─── EVENTS ───
@_cmd(name="eventcreate", description="🏰 Create a guild event or raid — pick a channel, form opens instantly (admin/mod/owner only)")
@app_commands.describe(target="Channel to post the event signup in")
@app_commands.autocomplete(target=target_autocomplete)
async def eventcreate(interaction: Interaction, target: str):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    if not is_admin_or_mod(interaction.user):
        await interaction.response.send_message(
            "❌ Only admins, moderators, or the server owner can create events.", ephemeral=True)
        return
    try:
        channel_id = int(target)
    except Exception:
        await interaction.response.send_message("❌ Invalid channel selection.", ephemeral=True)
        return
    await interaction.response.send_modal(EventModal(channel_id, gid))

@_cmd(name="listevents", description="🏰 List upcoming guild events and their rosters")
async def listevents(interaction: Interaction):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    ensure_guild(gid)
    with DATA_LOCK:
        events = sorted(
            [e for e in DATA[gid]["events"] if e["status"] == "open"],
            key=lambda e: e.get("target_ts") or 0,
        )
    if not events:
        await interaction.response.send_message("No upcoming events.")
        return
    lines = ["🏰 **Upcoming Events:**"]
    for e in events:
        ts       = e.get("target_ts")
        t_str    = f"<t:{ts}:F>" if ts else "Unknown time"
        signups  = e.get("signups", [])
        max_s    = e.get("max_slots", 0)
        slot_str = f"{len(signups)}/{max_s}" if max_s else str(len(signups))
        names    = ", ".join(s["name"] for s in signups) or "None yet"
        lines.append(
            f"\n**{e['title']}** [{e['game']}] — {e.get('event_type','').upper()}\n"
            f"  📅 {t_str} | 👥 {slot_str} signed up\n"
            f"  📝 {names}"
        )
    await interaction.response.send_message("\n".join(lines))

# ─── UTILITIES ───
@_cmd(name="upcoming", description="📅 Everything happening in the next X hours")
@app_commands.describe(hours="Look-ahead window (1–168 hours)")
async def upcoming(interaction: Interaction, hours: int):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    if not (1 <= hours <= 168):
        await interaction.response.send_message("❌ hours must be 1–168.", ephemeral=True)
        return
    ensure_guild(gid)
    now, cutoff = int(time.time()), int(time.time()) + hours * 3600
    items: List[Tuple[int, str]] = []
    with DATA_LOCK:
        for s in DATA[gid]["spawns"]:
            ts = next_event_unix_from_local(s["hour"], s["minute"], s["tz"])
            if ts and now <= ts <= cutoff:
                items.append((ts, f"🪄 **{s['name']}** @ {s.get('location','')} → <t:{ts}:t> in {safe_mention(s['target_id'])}"))
        for d in DATA[gid]["itemdrops"]:
            ts = next_event_unix_from_local(d["hour"], d["minute"], d["tz"])
            if ts and now <= ts <= cutoff:
                items.append((ts, f"💎 **{d['item']}** → <t:{ts}:t> in {safe_mention(d['target_id'])}"))
        for c in DATA[gid]["countdowns"]:
            ts = unix_from_mmddyyyy_and_hm(c["date"], c["hour"], c["minute"], c["tz"])
            if ts and now <= ts <= cutoff:
                items.append((ts, f"⏳ **{c['name']}** ends → <t:{ts}:F> in {safe_mention(c['target_id'])}"))
        for e in DATA[gid]["events"]:
            ts = e.get("target_ts")
            if ts and now <= ts <= cutoff and e["status"] == "open":
                items.append((ts, f"🏰 **{e['title']}** [{e['game']}] → <t:{ts}:F> ({len(e.get('signups',[]))} signed up)"))
    if not items:
        await interaction.response.send_message(f"Nothing scheduled in the next **{hours}h**.")
        return
    items.sort(key=lambda x: x[0])
    await interaction.response.send_message(
        f"📅 **Upcoming in the next {hours}h:**\n" + "\n".join(d for _, d in items))

@_cmd(name="listtargets", description="📋 Show channels and active threads the bot can post in")
async def listtargets(interaction: Interaction):
    gid = _gid(interaction)
    if not gid:
        await interaction.response.send_message("❌ Server only.", ephemeral=True)
        return
    guild = interaction.guild
    lines = ["📋 **Available targets:**\n**Text Channels:**"]
    for ch in guild.text_channels:
        if ch.permissions_for(guild.me).send_messages:
            lines.append(f"  # {ch.name}")
    thread_lines = [
        f"  💬 {th.name}" + (f" @ #{th.parent.name}" if th.parent else "")
        for th in guild.threads if not th.archived
    ]
    if thread_lines:
        lines.append("**Active Threads:**")
        lines.extend(thread_lines)
    if len(lines) == 1:
        await interaction.response.send_message("No accessible channels found.", ephemeral=True)
        return
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

# ═══════════════════════════════════════════════════════════════
# COG ENTRY POINT (Bot Forge Studio loads this file as an extension)
# ═══════════════════════════════════════════════════════════════
class EventsCog(commands.Cog):
    def __init__(self, b: commands.Bot):
        self.b = b
        self._names: list[tuple[str, discord.AppCommandType]] = []

    async def cog_load(self):
        global bot
        bot = self.b
        load_data()
        schedule_all()
        if not scheduler.running:
            scheduler.start()
        for c in _COMMANDS:
            self.b.tree.add_command(c)
            self._names.append((c.name, discord.AppCommandType.chat_input))

    @commands.Cog.listener()
    async def on_ready(self):
        for g in self.b.guilds:
            ensure_guild(str(g.id))
        schedule_all()

    async def cog_unload(self):
        try:
            scheduler.remove_all_jobs()
            scheduler.shutdown(wait=False)
        except Exception:
            pass
        for name, kind in self._names:
            self.b.tree.remove_command(name, type=kind)


async def setup(b: commands.Bot):
    await b.add_cog(EventsCog(b))
