"""Factions & Phrase Scoring — teams that earn points when members use their trigger phrases (Bot Forge Studio game module).

Admins define *factions* (any number; two are seeded) and *trigger phrases* with weights per faction.  Every message in the
tracked channels is scanned once with a combined pattern; weighted hits are limited per message, per phrase (cooldown) and
per day (a soft cap after which hits are worth a fraction), then added to the member's score for that faction.

Each member has, per faction: a lifetime score (levels come from it), a *current* score that slowly decays ("forgiveness"),
a streak of consecutive scoring messages, and daily / weekly / monthly history for leaderboards.  Joining a faction (or the
dominant one) is the member's team: Dungeons, Arena and Cards read it.

Other modules listen to the event bus (``bs.track`` events "hits" and "hits:<faction>") and call the services
``score.scan``, ``score.add`` and ``score.get``, so Boss Fights and Arena turn scoring hits into damage.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import bs_runtime as bs
from bs_game import host

log = logging.getLogger("bot.scoring")

SIDE_NAMES = %%side_names_list%%
DEFAULT_CFG = {
    "max_hits_msg": 10,        # weighted hits one message can score
    "saturation": 100,         # current score that counts as 100 % ("extreme")
    "cooldown_secs": 60,       # the same phrase only scores again after this long, per member
    "decay_per_day": 0.0,      # fraction of the *current* score forgiven per day (0 = never)
    "daily_softcap": 150,      # hits per day at full value, per faction
    "softcap_rate": 0.2,       # value of hits above the cap
    "react": True,             # react with the faction emoji when a message scores
    "announce_levels": True,
}
PALETTE = ["#5865F2", "#ED4245", "#57F287", "#FEE75C", "#EB459E", "#FAA61A"]
EMOJIS = ["🔵", "🔴", "🟢", "🟡", "🟣", "🟠"]
TIERS = [(0, "Neutral"), (25, "Leaning"), (50, "Committed"), (75, "Devoted"), (100, "Extreme")]
PERIODS = ("daily", "weekly", "monthly", "alltime")


# ----------------------------------------------------------------------------- schema & config
async def init_scoring_tables():
    for sql in (
        """CREATE TABLE IF NOT EXISTS sc_factions (guild_id TEXT NOT NULL, key TEXT NOT NULL, name TEXT NOT NULL,
            emoji TEXT NOT NULL DEFAULT '🔵', color TEXT NOT NULL DEFAULT '#5865F2', role_id TEXT, PRIMARY KEY (guild_id, key))""",
        """CREATE TABLE IF NOT EXISTS sc_triggers (guild_id TEXT NOT NULL, faction TEXT NOT NULL, phrase TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 1, PRIMARY KEY (guild_id, faction, phrase))""",
        """CREATE TABLE IF NOT EXISTS sc_scores (guild_id TEXT NOT NULL, user_id TEXT NOT NULL, faction TEXT NOT NULL,
            lifetime REAL NOT NULL DEFAULT 0, current REAL NOT NULL DEFAULT 0, streak INTEGER NOT NULL DEFAULT 0,
            best_streak INTEGER NOT NULL DEFAULT 0, last_hit TEXT, PRIMARY KEY (guild_id, user_id, faction))""",
        """CREATE TABLE IF NOT EXISTS sc_history (guild_id TEXT NOT NULL, user_id TEXT NOT NULL, faction TEXT NOT NULL,
            period TEXT NOT NULL, period_id TEXT NOT NULL, hits REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id, faction, period, period_id))""",
        """CREATE TABLE IF NOT EXISTS sc_channels (guild_id TEXT NOT NULL, channel_id TEXT NOT NULL, PRIMARY KEY (guild_id, channel_id))""",
    ):
        await host.db_exec(sql, ())


_cfg_cache: dict[str, dict] = {}


async def get_cfg(guild_id: str) -> dict:
    if guild_id not in _cfg_cache:
        stored = await bs.kv_get("scoring_cfg", guild_id, {}) or {}
        _cfg_cache[guild_id] = {**DEFAULT_CFG, **stored}
    return _cfg_cache[guild_id]


async def set_cfg(guild_id: str, key: str, value) -> None:
    cfg = dict(await get_cfg(guild_id))
    cfg[key] = value
    _cfg_cache[guild_id] = cfg
    await bs.kv_set("scoring_cfg", guild_id, {k: cfg[k] for k in DEFAULT_CFG})


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24] or "faction"


async def ensure_factions(guild_id: str) -> list[dict]:
    rows = await host.db_fetch("SELECT * FROM sc_factions WHERE guild_id=? ORDER BY rowid", (guild_id,))
    if not rows:
        for i, name in enumerate(SIDE_NAMES[:2] or ["Team A", "Team B"]):
            await host.db_exec("INSERT OR IGNORE INTO sc_factions (guild_id,key,name,emoji,color) VALUES (?,?,?,?,?)",
                               (guild_id, _key(name), name, EMOJIS[i % len(EMOJIS)], PALETTE[i % len(PALETTE)]))
        rows = await host.db_fetch("SELECT * FROM sc_factions WHERE guild_id=? ORDER BY rowid", (guild_id,))
    return [dict(r) for r in rows]


async def find_faction(guild_id: str, text: str) -> Optional[dict]:
    t = (text or "").strip().lower()
    for f in await ensure_factions(guild_id):
        if t in (f["key"], f["name"].lower()):
            return f
    return None


# ----------------------------------------------------------------------------- patterns
_patterns: dict[tuple, tuple] = {}      # (guild, faction) -> (compiled | None, {group: (phrase, weight)})
_cooldowns: dict[tuple, float] = {}
_streaks: dict[tuple, int] = {}


def bust_patterns(guild_id: str) -> None:
    for k in [k for k in _patterns if k[0] == guild_id]:
        _patterns.pop(k, None)


async def _pattern(guild_id: str, faction: str):
    k = (guild_id, faction)
    if k not in _patterns:
        rows = await host.db_fetch("SELECT phrase, weight FROM sc_triggers WHERE guild_id=? AND faction=?", (guild_id, faction))
        groups, parts = {}, []
        for i, r in enumerate(sorted(rows, key=lambda r: -len(r["phrase"]))):
            g = f"p{i}"
            groups[g] = (r["phrase"].lower(), float(r["weight"]))
            parts.append(f"(?P<{g}>(?<!\\w){re.escape(r['phrase'].lower())}(?!\\w))")
        _patterns[k] = (re.compile("|".join(parts)) if parts else None, groups)
    return _patterns[k]


async def scan_text(guild_id: str, user_id: str, text: str, *, cooldown: bool = True, faction: Optional[str] = None) -> dict[str, float]:
    """Weighted hits per faction for a piece of text (phrase cooldowns and the per-message cap applied)."""
    cfg = await get_cfg(guild_id)
    low = (text or "").lower()
    now = time.monotonic()
    out: dict[str, float] = {}
    for f in await ensure_factions(guild_id):
        if faction and f["key"] != faction:
            continue
        rx, groups = await _pattern(guild_id, f["key"])
        if rx is None:
            continue
        counts: dict[str, int] = {}
        for m in rx.finditer(low):
            g = next((k for k, v in m.groupdict().items() if v is not None), None)
            if g:
                counts[g] = counts.get(g, 0) + 1
        raw = 0.0
        for g, n in counts.items():
            phrase, weight = groups[g]
            ck = (guild_id, user_id, f["key"], phrase)
            if cooldown and now - _cooldowns.get(ck, -1e9) < cfg["cooldown_secs"]:
                continue
            if cooldown:
                _cooldowns[ck] = now
            raw += n * weight
        if raw > 0:
            out[f["key"]] = min(raw, float(cfg["max_hits_msg"]))
    return out


# ----------------------------------------------------------------------------- scoring
def _periods(now: datetime) -> list[tuple[str, str]]:
    iso = now.isocalendar()
    return [("daily", now.strftime("%Y-%m-%d")), ("weekly", f"{iso[0]}-W{iso[1]:02d}"), ("monthly", now.strftime("%Y-%m"))]


def level_for(xp: float) -> int:
    lvl = 0
    while xp >= 100 * (lvl + 1) ** 2 // 2:
        lvl += 1
    return lvl


async def _softcap(guild_id: str, user_id: str, faction: str, raw: float, now: datetime) -> float:
    cfg = await get_cfg(guild_id)
    row = await host.db_fetch_one(
        "SELECT hits FROM sc_history WHERE guild_id=? AND user_id=? AND faction=? AND period='daily' AND period_id=?",
        (guild_id, user_id, faction, now.strftime("%Y-%m-%d")))
    already = float(row["hits"]) if row else 0.0
    cap, rate = float(cfg["daily_softcap"]), float(cfg["softcap_rate"])
    if already >= cap:
        return raw * rate
    if already + raw <= cap:
        return raw
    below = cap - already
    return below + (raw - below) * rate


async def add_score(guild_id: str, user_id: str, faction: str, hits: float, *, softcap: bool = True,
                    channel: Optional[discord.abc.Messageable] = None) -> float:
    """Credit hits (after the daily soft cap), update streak / history, emit events.  Returns the hits actually credited."""
    now = datetime.now(timezone.utc)
    credited = await _softcap(guild_id, user_id, faction, hits, now) if softcap else float(hits)
    if credited <= 0:
        return 0.0
    before = await host.db_fetch_one("SELECT lifetime FROM sc_scores WHERE guild_id=? AND user_id=? AND faction=?",
                                     (guild_id, user_id, faction))
    old_life = float(before["lifetime"]) if before else 0.0
    sk = (guild_id, user_id, faction)
    _streaks[sk] = _streaks.get(sk, 0) + 1
    await host.db_exec(
        """INSERT INTO sc_scores (guild_id,user_id,faction,lifetime,current,streak,best_streak,last_hit) VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(guild_id,user_id,faction) DO UPDATE SET lifetime=lifetime+excluded.lifetime, current=current+excluded.current,
             streak=excluded.streak, best_streak=MAX(best_streak, excluded.streak), last_hit=excluded.last_hit""",
        (guild_id, user_id, faction, credited, credited, _streaks[sk], _streaks[sk], now.isoformat()))
    for period, pid in _periods(now):
        await host.db_exec(
            """INSERT INTO sc_history (guild_id,user_id,faction,period,period_id,hits) VALUES (?,?,?,?,?,?)
               ON CONFLICT(guild_id,user_id,faction,period,period_id) DO UPDATE SET hits=hits+excluded.hits""",
            (guild_id, user_id, faction, period, pid, credited))
    await bs.track(guild_id, user_id, "hits", credited)
    await bs.track(guild_id, user_id, f"hits:{faction}", credited)
    new_level = level_for(old_life + credited)
    if new_level > level_for(old_life):
        await bs.track(guild_id, user_id, "level_up", new_level)
        cfg = await get_cfg(guild_id)
        if channel is not None and cfg["announce_levels"]:
            f = await find_faction(guild_id, faction)
            try:
                await channel.send(f"{f['emoji'] if f else '⭐'} <@{user_id}> reached **level {new_level}** with **{f['name'] if f else faction}**!",
                                   allowed_mentions=discord.AllowedMentions(users=True))
            except discord.HTTPException:
                pass
    return credited


async def member_scores(guild_id: str, user_id: str) -> list[dict]:
    rows = await host.db_fetch("SELECT * FROM sc_scores WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    have = {r["faction"]: dict(r) for r in rows}
    cfg = await get_cfg(guild_id)
    out = []
    for f in await ensure_factions(guild_id):
        r = have.get(f["key"], {"lifetime": 0.0, "current": 0.0, "streak": 0, "best_streak": 0})
        pct = min(100.0, 100.0 * float(r["current"]) / max(1.0, float(cfg["saturation"])))
        out.append({"key": f["key"], "name": f["name"], "emoji": f["emoji"], "lifetime": float(r["lifetime"]),
                    "current": float(r["current"]), "pct": pct, "level": level_for(float(r["lifetime"])),
                    "streak": int(r["streak"]), "best_streak": int(r["best_streak"])})
    return out


def tier_name(pct: float) -> str:
    name = TIERS[0][1]
    for floor, n in TIERS:
        if pct >= floor:
            name = n
    return name


async def dominant_faction(guild_id: str, user_id: str) -> Optional[dict]:
    scores = await member_scores(guild_id, user_id)
    best = max(scores, key=lambda s: s["lifetime"], default=None)
    return best if best and best["lifetime"] > 0 else None


# ----------------------------------------------------------------------------- services for other modules
async def _svc_scan(guild, user, text, faction=None):
    return await scan_text(bs._id_of(guild), bs._id_of(user), text, cooldown=False, faction=faction)


async def _svc_add(guild, user, faction, hits):
    f = await find_faction(bs._id_of(guild), faction)
    if f is None:
        return 0.0
    return await add_score(bs._id_of(guild), bs._id_of(user), f["key"], float(hits), softcap=False)


async def _svc_get(guild, user):
    return await member_scores(bs._id_of(guild), bs._id_of(user))


# ----------------------------------------------------------------------------- UI helpers
def _color(hex_: str) -> discord.Colour:
    try:
        return discord.Colour(int(hex_.lstrip("#"), 16))
    except ValueError:
        return discord.Colour.blurple()


def _bar(pct: float, n: int = 10) -> str:
    filled = round(pct / 100 * n)
    return "█" * filled + "░" * (n - filled)


async def _admin_only(interaction: discord.Interaction) -> bool:
    if host.is_admin(interaction):
        return True
    await interaction.response.send_message("You need **Manage Server** permission.", ephemeral=True)
    return False


async def _faction_choices(interaction: discord.Interaction, current: str):
    if interaction.guild is None:
        return []
    return [app_commands.Choice(name=f"{f['emoji']} {f['name']}", value=f["key"]) for f in await ensure_factions(str(interaction.guild.id))
            if current.lower() in f["name"].lower()][:25]


class _BulkTriggerModal(discord.ui.Modal, title="Add trigger phrases"):
    lines = discord.ui.TextInput(label="One per line:  phrase | weight", style=discord.TextStyle.paragraph,
                                 placeholder="good morning | 1\nlet's go | 2", max_length=3000)

    def __init__(self, guild_id: str, faction: dict):
        super().__init__()
        self.guild_id, self.faction = guild_id, faction

    async def on_submit(self, interaction: discord.Interaction):
        n = 0
        for ln in self.lines.value.splitlines():
            parts = [p.strip() for p in ln.split("|")]
            if not parts[0]:
                continue
            try:
                w = float(parts[1]) if len(parts) > 1 and parts[1] else 1.0
            except ValueError:
                w = 1.0
            await host.db_exec("INSERT INTO sc_triggers (guild_id,faction,phrase,weight) VALUES (?,?,?,?) "
                               "ON CONFLICT(guild_id,faction,phrase) DO UPDATE SET weight=excluded.weight",
                               (self.guild_id, self.faction["key"], parts[0].lower(), max(0.1, min(w, 50))))
            n += 1
        bust_patterns(self.guild_id)
        await interaction.response.send_message(f"Saved **{n}** phrase(s) for {self.faction['emoji']} {self.faction['name']}.", ephemeral=True)


# ----------------------------------------------------------------------------- cog
class ScoringCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._names: list[str] = []

    async def cog_load(self):
        await init_scoring_tables()
        bs.register_service("score.scan", _svc_scan)
        bs.register_service("score.add", _svc_add)
        bs.register_service("score.get", _svc_get)
        for c in self._build_commands():
            self.bot.tree.add_command(c)
            self._names.append(c.name)
        self.decay.start()

    async def cog_unload(self):
        self.decay.cancel()
        for n in self._names:
            self.bot.tree.remove_command(n)
        for s in ("score.scan", "score.add", "score.get"):
            bs.unregister_service(s)
        await host.close()

    # -- decay -------------------------------------------------------------------------
    @tasks.loop(hours=1)
    async def decay(self):
        for g in list(self.bot.guilds):
            cfg = await get_cfg(str(g.id))
            rate = float(cfg["decay_per_day"])
            if rate > 0:
                await host.db_exec("UPDATE sc_scores SET current = MAX(0, current * ?) WHERE guild_id=?",
                                   ((1 - rate) ** (1 / 24), str(g.id)))

    @decay.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    # -- messages ----------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or not message.content:
            return
        gid, uid = str(message.guild.id), str(message.author.id)
        tracked = await host.db_fetch("SELECT channel_id FROM sc_channels WHERE guild_id=?", (gid,))
        if tracked and str(message.channel.id) not in {r["channel_id"] for r in tracked} \
                and str(getattr(message.channel, "parent_id", "")) not in {r["channel_id"] for r in tracked}:
            return
        hits = await scan_text(gid, uid, message.content)
        if not hits:
            for k in [k for k in _streaks if k[0] == gid and k[1] == uid]:
                _streaks[k] = 0
            return
        cfg = await get_cfg(gid)
        react_emoji = None
        for faction, raw in hits.items():
            credited = await add_score(gid, uid, faction, raw, channel=message.channel)
            if credited > 0 and react_emoji is None:
                react_emoji = (await find_faction(gid, faction) or {}).get("emoji")
        if react_emoji and cfg["react"]:
            try:
                await message.add_reaction(react_emoji)
            except discord.HTTPException:
                pass

    # -- commands ------------------------------------------------------------------------
    def _build_commands(self):
        faction = app_commands.Group(name="faction", description="Factions (teams) members can join", guild_only=True)

        @faction.command(name="list", description="Show this server's factions and how many members each has")
        async def f_list(interaction: discord.Interaction):
            gid = str(interaction.guild_id)
            rows = await ensure_factions(gid)
            teams = await bs.kv_top_any("team", 100000)
            counts = {}
            for k, v in teams:
                if k.startswith(f"{gid}:"):
                    counts[v] = counts.get(v, 0) + 1
            e = discord.Embed(title="Factions", colour=discord.Colour.blurple(),
                              description="\n".join(f"{f['emoji']} **{f['name']}** — {counts.get(f['name'], 0)} member(s)" for f in rows))
            await interaction.response.send_message(embed=e, ephemeral=True)

        @faction.command(name="join", description="Join a faction (this is your team)")
        @app_commands.autocomplete(name=_faction_choices)
        async def f_join(interaction: discord.Interaction, name: str):
            f = await find_faction(str(interaction.guild_id), name)
            if f is None:
                return await interaction.response.send_message("No such faction.", ephemeral=True)
            await host.set_team(interaction.guild_id, interaction.user.id, f["name"])
            if f["role_id"]:
                role = interaction.guild.get_role(int(f["role_id"]))
                if role:
                    try:
                        await interaction.user.add_roles(role, reason="Joined faction")
                    except discord.HTTPException:
                        pass
            await bs.track(interaction.guild_id, interaction.user.id, "faction_join", 1)
            await interaction.response.send_message(f"{f['emoji']} You joined **{f['name']}**.", ephemeral=True)

        @faction.command(name="leave", description="Leave your faction")
        async def f_leave(interaction: discord.Interaction):
            await host.set_team(interaction.guild_id, interaction.user.id, None)
            await interaction.response.send_message("You left your faction.", ephemeral=True)

        @faction.command(name="create", description="Create a faction (Manage Server)")
        @app_commands.describe(name="Faction name", emoji="Emoji shown on reactions and boards", color="Hex colour, e.g. #ED4245",
                               role="Role members receive when they join")
        async def f_create(interaction: discord.Interaction, name: str, emoji: str = "🔵", color: str = "#5865F2",
                           role: Optional[discord.Role] = None):
            if not await _admin_only(interaction):
                return
            await host.db_exec("INSERT INTO sc_factions (guild_id,key,name,emoji,color,role_id) VALUES (?,?,?,?,?,?) "
                               "ON CONFLICT(guild_id,key) DO UPDATE SET name=excluded.name, emoji=excluded.emoji, color=excluded.color, role_id=excluded.role_id",
                               (str(interaction.guild_id), _key(name), name[:40], emoji[:16], color, str(role.id) if role else None))
            await interaction.response.send_message(f"Saved faction **{name}**.", ephemeral=True)

        @faction.command(name="delete", description="Delete a faction and its triggers (Manage Server)")
        @app_commands.autocomplete(name=_faction_choices)
        async def f_delete(interaction: discord.Interaction, name: str):
            if not await _admin_only(interaction):
                return
            f = await find_faction(str(interaction.guild_id), name)
            if f is None:
                return await interaction.response.send_message("No such faction.", ephemeral=True)
            gid = str(interaction.guild_id)
            for t in ("sc_factions", "sc_triggers", "sc_scores", "sc_history"):
                col = "key" if t == "sc_factions" else "faction"
                await host.db_exec(f"DELETE FROM {t} WHERE guild_id=? AND {col}=?", (gid, f["key"]))
            bust_patterns(gid)
            await interaction.response.send_message(f"Deleted **{f['name']}**.", ephemeral=True)

        trig = app_commands.Group(name="trigger", description="Phrases that score for a faction", guild_only=True)

        @trig.command(name="add", description="Add a trigger phrase (Manage Server)")
        @app_commands.describe(faction="Which faction it scores for", phrase="The phrase", weight="Points per match (default 1)")
        @app_commands.autocomplete(faction=_faction_choices)
        async def t_add(interaction: discord.Interaction, faction: str, phrase: str, weight: float = 1.0):
            if not await _admin_only(interaction):
                return
            f = await find_faction(str(interaction.guild_id), faction)
            if f is None:
                return await interaction.response.send_message("No such faction.", ephemeral=True)
            await host.db_exec("INSERT INTO sc_triggers (guild_id,faction,phrase,weight) VALUES (?,?,?,?) "
                               "ON CONFLICT(guild_id,faction,phrase) DO UPDATE SET weight=excluded.weight",
                               (str(interaction.guild_id), f["key"], phrase.strip().lower(), max(0.1, min(weight, 50))))
            bust_patterns(str(interaction.guild_id))
            await interaction.response.send_message(f"{f['emoji']} `{phrase}` now scores **{weight:g}** for {f['name']}.", ephemeral=True)

        @trig.command(name="bulk", description="Add many phrases at once (Manage Server)")
        @app_commands.autocomplete(faction=_faction_choices)
        async def t_bulk(interaction: discord.Interaction, faction: str):
            if not await _admin_only(interaction):
                return
            f = await find_faction(str(interaction.guild_id), faction)
            if f is None:
                return await interaction.response.send_message("No such faction.", ephemeral=True)
            await interaction.response.send_modal(_BulkTriggerModal(str(interaction.guild_id), f))

        @trig.command(name="remove", description="Remove a trigger phrase (Manage Server)")
        @app_commands.autocomplete(faction=_faction_choices)
        async def t_remove(interaction: discord.Interaction, faction: str, phrase: str):
            if not await _admin_only(interaction):
                return
            f = await find_faction(str(interaction.guild_id), faction)
            if f is None:
                return await interaction.response.send_message("No such faction.", ephemeral=True)
            await host.db_exec("DELETE FROM sc_triggers WHERE guild_id=? AND faction=? AND phrase=?",
                               (str(interaction.guild_id), f["key"], phrase.strip().lower()))
            bust_patterns(str(interaction.guild_id))
            await interaction.response.send_message("Removed.", ephemeral=True)

        @trig.command(name="list", description="Show trigger phrases")
        @app_commands.autocomplete(faction=_faction_choices)
        async def t_list(interaction: discord.Interaction, faction: Optional[str] = None):
            gid = str(interaction.guild_id)
            e = discord.Embed(title="Trigger phrases", colour=discord.Colour.blurple())
            for f in await ensure_factions(gid):
                if faction and f["key"] != faction:
                    continue
                rows = await host.db_fetch("SELECT phrase, weight FROM sc_triggers WHERE guild_id=? AND faction=? ORDER BY phrase", (gid, f["key"]))
                e.add_field(name=f"{f['emoji']} {f['name']}", inline=False,
                            value=(", ".join(f"`{r['phrase']}`×{r['weight']:g}" for r in rows) or "—")[:1000])
            await interaction.response.send_message(embed=e, ephemeral=True)

        @trig.command(name="test", description="See what a message would score (Manage Server)")
        async def t_test(interaction: discord.Interaction, text: str):
            if not await _admin_only(interaction):
                return
            hits = await scan_text(str(interaction.guild_id), str(interaction.user.id), text, cooldown=False)
            if not hits:
                return await interaction.response.send_message("No trigger phrase matched.", ephemeral=True)
            out = []
            for k, v in hits.items():
                f = await find_faction(str(interaction.guild_id), k)
                out.append(f"{f['emoji']} **{f['name']}**: {v:g}")
            await interaction.response.send_message("\n".join(out), ephemeral=True)

        @app_commands.command(name="score", description="Show a member's faction scores")
        @app_commands.guild_only()
        async def score(interaction: discord.Interaction, member: Optional[discord.Member] = None):
            m = member or interaction.user
            gid = str(interaction.guild_id)
            scores = await member_scores(gid, str(m.id))
            team = await host.get_team(gid, m.id)
            dom = max(scores, key=lambda s: s["current"], default=None)
            e = discord.Embed(title=f"{m.display_name}", colour=discord.Colour.blurple(),
                              description=f"Team: **{team or '—'}**" + (f"  ·  Leaning: **{dom['name']}** ({tier_name(dom['pct'])})" if dom and dom["current"] > 0 else ""))
            for s in scores:
                e.add_field(name=f"{s['emoji']} {s['name']} — level {s['level']}", inline=False,
                            value=f"`{_bar(s['pct'])}` {s['pct']:.0f}%  ·  current {s['current']:.1f}  ·  lifetime {s['lifetime']:.0f}  ·  streak {s['streak']} (best {s['best_streak']})")
            await interaction.response.send_message(embed=e, ephemeral=True)

        @app_commands.command(name="scoreboard", description="Leaderboard for a faction")
        @app_commands.describe(faction="Which faction", period="daily, weekly, monthly or alltime")
        @app_commands.choices(period=[app_commands.Choice(name=p, value=p) for p in PERIODS])
        @app_commands.autocomplete(faction=_faction_choices)
        @app_commands.guild_only()
        async def scoreboard(interaction: discord.Interaction, faction: Optional[str] = None, period: str = "alltime"):
            gid = str(interaction.guild_id)
            facs = await ensure_factions(gid)
            f = await find_faction(gid, faction) if faction else facs[0]
            if f is None:
                return await interaction.response.send_message("No such faction.", ephemeral=True)
            if period == "alltime":
                rows = await host.db_fetch("SELECT user_id, lifetime AS v FROM sc_scores WHERE guild_id=? AND faction=? ORDER BY lifetime DESC LIMIT 10", (gid, f["key"]))
            else:
                pid = dict(_periods(datetime.now(timezone.utc)))[period]
                rows = await host.db_fetch("SELECT user_id, hits AS v FROM sc_history WHERE guild_id=? AND faction=? AND period=? AND period_id=? ORDER BY hits DESC LIMIT 10",
                                           (gid, f["key"], period, pid))
            medals = {1: "🥇", 2: "🥈", 3: "🥉"}
            text = "\n".join(f"{medals.get(i, f'`{i}.`')} <@{r['user_id']}> — {float(r['v']):.0f}" for i, r in enumerate(rows, 1)) or "Nobody yet."
            await interaction.response.send_message(embed=discord.Embed(title=f"{f['emoji']} {f['name']} — {period}", description=text, colour=_color(f["color"])), ephemeral=True)

        cfgg = app_commands.Group(name="scoreconfig", description="Scoring settings (Manage Server)", guild_only=True)

        @cfgg.command(name="show", description="Show the scoring settings")
        async def c_show(interaction: discord.Interaction):
            if not await _admin_only(interaction):
                return
            gid = str(interaction.guild_id)
            cfg = await get_cfg(gid)
            ch = await host.db_fetch("SELECT channel_id FROM sc_channels WHERE guild_id=?", (gid,))
            lines = [f"`{k}` = **{v}**" for k, v in cfg.items()]
            lines.append("tracked channels: " + (", ".join(f"<#{r['channel_id']}>" for r in ch) or "all"))
            await interaction.response.send_message("\n".join(lines), ephemeral=True)

        @cfgg.command(name="set", description="Change a setting")
        @app_commands.choices(key=[app_commands.Choice(name=k, value=k) for k in DEFAULT_CFG])
        async def c_set(interaction: discord.Interaction, key: str, value: str):
            if not await _admin_only(interaction):
                return
            cur = DEFAULT_CFG[key]
            try:
                new = (value.strip().lower() in ("1", "true", "yes", "on")) if isinstance(cur, bool) else type(cur)(float(value) if isinstance(cur, (int, float)) else value)
            except ValueError:
                return await interaction.response.send_message("That value doesn't fit this setting.", ephemeral=True)
            await set_cfg(str(interaction.guild_id), key, new)
            await interaction.response.send_message(f"`{key}` = **{new}**", ephemeral=True)

        @cfgg.command(name="track", description="Only score messages in this channel (add it; run again to remove)")
        async def c_track(interaction: discord.Interaction, channel: discord.TextChannel):
            if not await _admin_only(interaction):
                return
            gid = str(interaction.guild_id)
            have = await host.db_fetch_one("SELECT 1 FROM sc_channels WHERE guild_id=? AND channel_id=?", (gid, str(channel.id)))
            if have:
                await host.db_exec("DELETE FROM sc_channels WHERE guild_id=? AND channel_id=?", (gid, str(channel.id)))
                msg = f"{channel.mention} is no longer tracked."
            else:
                await host.db_exec("INSERT INTO sc_channels (guild_id,channel_id) VALUES (?,?)", (gid, str(channel.id)))
                msg = f"Now scoring only tracked channels — added {channel.mention}."
            await interaction.response.send_message(msg, ephemeral=True)

        @cfgg.command(name="reset", description="Reset one member's scores")
        async def c_reset(interaction: discord.Interaction, member: discord.Member):
            if not await _admin_only(interaction):
                return
            for t in ("sc_scores", "sc_history"):
                await host.db_exec(f"DELETE FROM {t} WHERE guild_id=? AND user_id=?", (str(interaction.guild_id), str(member.id)))
            await interaction.response.send_message(f"Reset {member.display_name}.", ephemeral=True)

        return [faction, trig, score, scoreboard, cfgg]


async def setup(bot: commands.Bot):
    await bot.add_cog(ScoringCog(bot))
