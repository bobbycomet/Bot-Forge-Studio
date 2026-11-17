"""Boss Fights — timed community boss events with HP, phases, periodic attacks, a live damage feed and rewards (Bot Forge Studio game module).

Admins build bosses with /boss create, /boss phases and /boss rewards, then /boss start (or queue / recurring).  Damage comes
from the event bus — every scoring hit from Factions & Phrase Scoring ("hits"), the Deal Boss Damage node, or optionally every
chat message — multiplied by the current phase and the member's gear attack (Gear & Rarity).  The boss strikes back on a
timer (score penalty, silence, or a damage-multiplier drop), changes phase at HP thresholds, and ends when it is defeated or
time runs out.  Rewards go to everyone, a winning faction, the top damage dealer or the top N: currency, a role, or a message.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import bs_runtime as bs
from bs_game import host

log = logging.getLogger("bot.bosses")
host.wallet = %%wallet%%
host.currency_name = %%currency_name%%
host.currency_emoji = %%currency_emoji%%
MESSAGE_DAMAGE = float(%%message_damage%%)
FEED_DEBOUNCE = 3.0
BASE_ATK = 5.0          # attack every member has with no gear (Gear & Rarity's baseline) — only the part above it adds damage
EFFECTS = ("score_penalty", "silence", "damage_drop")
SCOPES = ("all", "team", "individual", "topN")

_feed: dict[tuple, dict] = {}        # (guild, boss) -> {"msg": Message, "last": monotonic}
_msg_cd: dict[tuple, float] = {}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(s: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s) if s else None
    except ValueError:
        return None


def _bid(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30] or "boss"


def hp_bar(cur: float, mx: float, n: int = 12) -> str:
    pct = max(0.0, min(1.0, cur / max(1.0, mx)))
    block = "🟩" if pct > 0.6 else "🟨" if pct > 0.3 else "🟥"
    f = round(pct * n)
    return block * f + "⬛" * (n - f)


# ----------------------------------------------------------------------------- schema
async def init_boss_tables():
    for sql in (
        """CREATE TABLE IF NOT EXISTS bf_bosses (guild_id TEXT NOT NULL, boss_id TEXT NOT NULL, name TEXT NOT NULL, emoji TEXT NOT NULL DEFAULT '👹',
            max_hp REAL NOT NULL DEFAULT 1000, hp REAL NOT NULL DEFAULT 1000, status TEXT NOT NULL DEFAULT 'ready',
            duration_mins INTEGER NOT NULL DEFAULT 60, started_at TEXT, ends_at TEXT, phases TEXT NOT NULL DEFAULT '[]',
            attack_effect TEXT NOT NULL DEFAULT 'score_penalty', attack_value REAL NOT NULL DEFAULT 5, attack_interval INTEGER NOT NULL DEFAULT 300,
            last_attack TEXT, phase INTEGER NOT NULL DEFAULT 1, dmg_mult REAL NOT NULL DEFAULT 1, silenced_until TEXT,
            rewards TEXT NOT NULL DEFAULT '{}', channel_id TEXT, recurring_mins INTEGER NOT NULL DEFAULT 0, next_spawn TEXT,
            PRIMARY KEY (guild_id, boss_id))""",
        """CREATE TABLE IF NOT EXISTS bf_participants (guild_id TEXT NOT NULL, boss_id TEXT NOT NULL, user_id TEXT NOT NULL,
            faction TEXT NOT NULL DEFAULT '', hits REAL NOT NULL DEFAULT 0, damage REAL NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, boss_id, user_id))""",
        """CREATE TABLE IF NOT EXISTS bf_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL, boss_id TEXT NOT NULL,
            queued_by TEXT NOT NULL, queued_at TEXT NOT NULL, launched INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS bf_reward_log (guild_id TEXT NOT NULL, boss_id TEXT NOT NULL, user_id TEXT NOT NULL,
            kind TEXT NOT NULL, value TEXT NOT NULL, at TEXT NOT NULL)""",
    ):
        await host.db_exec(sql, ())


async def get_boss(guild_id: str, boss_id: str) -> Optional[dict]:
    r = await host.db_fetch_one("SELECT * FROM bf_bosses WHERE guild_id=? AND boss_id=?", (guild_id, boss_id))
    return dict(r) if r else None


async def active_boss(guild_id: str) -> Optional[dict]:
    r = await host.db_fetch_one("SELECT * FROM bf_bosses WHERE guild_id=? AND status='active' LIMIT 1", (guild_id,))
    return dict(r) if r else None


# ----------------------------------------------------------------------------- embeds
async def feed_embed(b: dict) -> discord.Embed:
    top = await host.db_fetch("SELECT user_id, damage FROM bf_participants WHERE guild_id=? AND boss_id=? ORDER BY damage DESC LIMIT 5",
                              (b["guild_id"], b["boss_id"]))
    ends = _parse(b["ends_at"])
    e = discord.Embed(title=f"{b['emoji']} {b['name']} — phase {b['phase']}", colour=discord.Colour.red(),
                      description=f"{hp_bar(b['hp'], b['max_hp'])}\n**{max(0, int(b['hp'])):,} / {int(b['max_hp']):,} HP**"
                                  + (f"   ·   ends <t:{int(ends.timestamp())}:R>" if ends else ""))
    if b["dmg_mult"] != 1:
        e.add_field(name="Damage multiplier", value=f"×{b['dmg_mult']:g}")
    if top:
        e.add_field(name="Top damage", inline=False,
                    value="\n".join(f"`{i}.` <@{r['user_id']}> — {float(r['damage']):,.0f}" for i, r in enumerate(top, 1)))
    return e


async def _channel(bot, b: dict):
    cid = b.get("channel_id")
    if not cid:
        return None
    return bot.get_channel(int(cid)) or await bot.fetch_channel(int(cid))


async def update_feed(bot, b: dict, force: bool = False):
    key = (b["guild_id"], b["boss_id"])
    slot = _feed.setdefault(key, {"msg": None, "last": 0.0})
    if not force and time.monotonic() - slot["last"] < FEED_DEBOUNCE:
        return
    slot["last"] = time.monotonic()
    emb = await feed_embed(b)
    try:
        if slot["msg"] is None:
            ch = await _channel(bot, b)
            if ch is not None:
                slot["msg"] = await ch.send(embed=emb)
        else:
            await slot["msg"].edit(embed=emb)
    except discord.HTTPException:
        slot["msg"] = None


async def say(bot, b: dict, text: str = "", embed: Optional[discord.Embed] = None):
    try:
        ch = await _channel(bot, b)
        if ch is not None:
            await ch.send(text or None, embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.HTTPException:
        pass


# ----------------------------------------------------------------------------- lifecycle
async def start_boss(bot, guild_id: str, boss_id: str, channel_id: Optional[str] = None) -> tuple[bool, str]:
    b = await get_boss(guild_id, boss_id)
    if b is None:
        return False, "No such boss."
    if await active_boss(guild_id):
        return False, "A boss is already active — use /boss queue to line this one up."
    now = now_utc()
    await host.db_exec("DELETE FROM bf_participants WHERE guild_id=? AND boss_id=?", (guild_id, boss_id))
    await host.db_exec(
        "UPDATE bf_bosses SET status='active', hp=max_hp, phase=1, dmg_mult=1, silenced_until=NULL, started_at=?, ends_at=?, last_attack=?, "
        "channel_id=COALESCE(?, channel_id), next_spawn=NULL WHERE guild_id=? AND boss_id=?",
        (_iso(now), _iso(now + timedelta(minutes=int(b["duration_mins"]))), _iso(now), channel_id, guild_id, boss_id))
    b = await get_boss(guild_id, boss_id)
    _feed.pop((guild_id, boss_id), None)
    await say(bot, b, embed=discord.Embed(title=f"{b['emoji']} {b['name']} APPEARS!", colour=discord.Colour.red(),
                                            description=f"**{int(b['max_hp']):,} HP** · {b['duration_mins']} minutes\nUse your trigger phrases to deal damage!"))
    await update_feed(bot, b, force=True)
    await bs.track(guild_id, 0, "boss_started", 1)
    return True, f"{b['name']} has started."


async def _phase_check(bot, b: dict) -> dict:
    try:
        phases = json.loads(b["phases"] or "[]")
    except ValueError:
        return b
    if not phases:
        return b
    frac = b["hp"] / max(1.0, b["max_hp"])
    ordered = sorted(phases, key=lambda p: -float(p.get("threshold", 0)))     # highest HP threshold first
    target = 1
    for i, ph in enumerate(ordered):
        if frac <= float(ph.get("threshold", 0)):
            target = i + 2            # phase 1 is the opening; each crossed threshold starts the next one
    target = min(target, len(ordered) + 1)
    if target <= int(b["phase"]):
        return b
    ph = ordered[target - 2]
    sets, args = ["phase=?"], [target]
    if ph.get("multiplier"):
        sets.append("dmg_mult=?"); args.append(float(ph["multiplier"]))
    if ph.get("attack_interval_secs"):
        sets.append("attack_interval=?"); args.append(int(ph["attack_interval_secs"]))
    await host.db_exec(f"UPDATE bf_bosses SET {', '.join(sets)} WHERE guild_id=? AND boss_id=?", (*args, b["guild_id"], b["boss_id"]))
    b = await get_boss(b["guild_id"], b["boss_id"])
    await say(bot, b, embed=discord.Embed(title=f"⚡ PHASE {target}: {str(ph.get('name', 'Enraged')).upper()}", colour=discord.Colour.orange(),
                                            description=f"{ph.get('emoji') or b['emoji']} **{b['name']}** grows stronger!  HP {int(b['hp']):,}/{int(b['max_hp']):,}"))
    return b


async def deal_damage(bot, guild_id: str, user_id: str, amount: float) -> Optional[dict]:
    b = await active_boss(guild_id)
    if b is None or amount <= 0:
        return None
    sil = _parse(b["silenced_until"])
    if sil and sil > now_utc():
        return None
    stats = await bs.service("gear.stats", guild_id, user_id) or {}
    dmg = float(amount) * float(b["dmg_mult"]) * (1 + max(0.0, float(stats.get("atk", 0)) - BASE_ATK) / 100.0)
    team = await host.get_team(guild_id, user_id) or ""
    await host.db_exec(
        """INSERT INTO bf_participants (guild_id,boss_id,user_id,faction,hits,damage) VALUES (?,?,?,?,?,?)
           ON CONFLICT(guild_id,boss_id,user_id) DO UPDATE SET hits=hits+excluded.hits, damage=damage+excluded.damage, faction=excluded.faction""",
        (guild_id, b["boss_id"], user_id, team, float(amount), dmg))
    await host.db_exec("UPDATE bf_bosses SET hp=MAX(0, hp-?) WHERE guild_id=? AND boss_id=?", (dmg, guild_id, b["boss_id"]))
    b = await get_boss(guild_id, b["boss_id"])
    await bs.track(guild_id, user_id, "boss_damage", max(1, int(dmg)))
    if b["hp"] <= 0:
        await end_boss(bot, b, "won")
        return b
    b = await _phase_check(bot, b)
    await update_feed(bot, b)
    return b


async def _attack(bot, b: dict):
    effect, value = b["attack_effect"], float(b["attack_value"])
    gid, bid = b["guild_id"], b["boss_id"]
    if effect == "score_penalty":
        users = await host.db_fetch("SELECT user_id FROM bf_participants WHERE guild_id=? AND boss_id=?", (gid, bid))
        for u in users:
            try:
                await host.db_exec("UPDATE sc_scores SET current=MAX(0, current-?) WHERE guild_id=? AND user_id=?", (value, gid, u["user_id"]))
            except Exception:
                break                      # scoring module not installed: nothing to penalise
        text = f"{b['emoji']} **{b['name']} ATTACKS!** Everyone who fought loses **{value:g}** points."
    elif effect == "silence":
        until = now_utc() + timedelta(seconds=value or 30)
        await host.db_exec("UPDATE bf_bosses SET silenced_until=? WHERE guild_id=? AND boss_id=?", (_iso(until), gid, bid))
        text = f"🔇 **{b['name']} SILENCES THE CROWD!** No damage for **{int(value or 30)}s**."
    else:
        new = max(1.0, float(b["dmg_mult"]) / 2.0)
        await host.db_exec("UPDATE bf_bosses SET dmg_mult=? WHERE guild_id=? AND boss_id=?", (new, gid, bid))
        text = f"{b['emoji']} **{b['name']} WEAKENS YOUR RESOLVE!** Damage multiplier is now **×{new:g}**."
    await host.db_exec("UPDATE bf_bosses SET last_attack=? WHERE guild_id=? AND boss_id=?", (_iso(now_utc()), gid, bid))
    await say(bot, b, embed=discord.Embed(title="⚠️ BOSS ATTACK!", description=text, colour=discord.Colour.dark_red())
              .set_footer(text=f"HP remaining: {max(0, int(b['hp'])):,}"))


async def _winners(b: dict, scope: str, n: int) -> list[str]:
    gid, bid = b["guild_id"], b["boss_id"]
    rows = await host.db_fetch("SELECT user_id, faction, damage FROM bf_participants WHERE guild_id=? AND boss_id=? ORDER BY damage DESC", (gid, bid))
    if scope == "all":
        return [r["user_id"] for r in rows]
    if scope == "individual":
        return [rows[0]["user_id"]] if rows else []
    if scope == "topN":
        return [r["user_id"] for r in rows[:n]]
    totals: dict[str, float] = {}
    for r in rows:
        totals[r["faction"]] = totals.get(r["faction"], 0) + float(r["damage"])
    if not totals:
        return []
    top = max(totals, key=totals.get)
    return [r["user_id"] for r in rows if r["faction"] == top]


async def _rewards(bot, b: dict):
    try:
        cfg = json.loads(b["rewards"] or "{}")
    except ValueError:
        return
    if not cfg:
        return
    gid = b["guild_id"]
    winners = await _winners(b, cfg.get("scope", "all"), int(cfg.get("N", 3)))
    guild = bot.get_guild(int(gid))
    for g in cfg.get("grants", []):
        kind, val = g.get("type"), g.get("value", "")
        for uid in winners:
            if kind == "currency":
                await host.add_gold(gid, uid, float(val or 0))
            elif kind == "role" and guild:
                role, m = guild.get_role(int(val)) if str(val).isdigit() else None, guild.get_member(int(uid))
                if role and m:
                    try:
                        await m.add_roles(role, reason="Boss reward")
                    except discord.HTTPException:
                        pass
            await host.db_exec("INSERT INTO bf_reward_log (guild_id,boss_id,user_id,kind,value,at) VALUES (?,?,?,?,?,?)",
                               (gid, b["boss_id"], uid, str(kind), str(val), _iso(now_utc())))
        if kind == "message" and val:
            await say(bot, b, str(val).replace("{boss}", b["name"]).replace("{winners}", ", ".join(f"<@{u}>" for u in winners[:10])))


async def end_boss(bot, b: dict, result: str):
    gid, bid = b["guild_id"], b["boss_id"]
    nxt = _iso(now_utc() + timedelta(minutes=int(b["recurring_mins"]))) if int(b["recurring_mins"]) > 0 else None
    await host.db_exec("UPDATE bf_bosses SET status=?, next_spawn=? WHERE guild_id=? AND boss_id=?", (result, nxt, gid, bid))
    b = await get_boss(gid, bid)
    await update_feed(bot, b, force=True)
    _feed.pop((gid, bid), None)
    parts = await host.db_fetch("SELECT user_id, damage FROM bf_participants WHERE guild_id=? AND boss_id=? ORDER BY damage DESC", (gid, bid))
    top = "\n".join(f"`{i}.` <@{r['user_id']}> — {float(r['damage']):,.0f}" for i, r in enumerate(parts[:5], 1)) or "Nobody fought."
    if result == "won":
        await say(bot, b, embed=discord.Embed(title=f"🏆 {b['name']} DEFEATED!", colour=discord.Colour.green(), description=top))
        await _rewards(bot, b)
        for r in parts:
            await bs.track(gid, r["user_id"], "boss_defeated", 1)
            await bs.track(gid, r["user_id"], "match_won", 1)
    else:
        await say(bot, b, embed=discord.Embed(title=f"💀 {b['name']} survived", colour=discord.Colour.dark_grey(),
                                                description=f"Time ran out with **{int(b['hp']):,} HP** left.\n{top}"))
    for r in parts:
        await bs.track(gid, r["user_id"], "match_played", 1)
    await _next_in_queue(bot, gid)


async def _next_in_queue(bot, gid: str):
    while True:
        q = await host.db_fetch_one("SELECT * FROM bf_queue WHERE guild_id=? AND launched=0 ORDER BY id LIMIT 1", (gid,))
        if not q:
            return
        await host.db_exec("UPDATE bf_queue SET launched=1 WHERE id=?", (q["id"],))
        ok, _ = await start_boss(bot, gid, q["boss_id"])
        if ok:
            return


async def tick(bot):
    """Called every few seconds: expiry, boss attacks, recurring spawns."""
    now = now_utc()
    for b in await host.db_fetch("SELECT * FROM bf_bosses WHERE status='active'", ()):
        b = dict(b)
        end = _parse(b["ends_at"])
        if end and now >= end:
            await end_boss(bot, b, "lost")
            continue
        last = _parse(b["last_attack"]) or now
        if int(b["attack_interval"]) > 0 and (now - last).total_seconds() >= int(b["attack_interval"]):
            await _attack(bot, b)
        await update_feed(bot, b)
    for b in await host.db_fetch("SELECT * FROM bf_bosses WHERE status IN ('won','lost','ready') AND recurring_mins>0 AND next_spawn IS NOT NULL", ()):
        nxt = _parse(b["next_spawn"])
        if nxt and now >= nxt and not await active_boss(b["guild_id"]):
            await start_boss(bot, b["guild_id"], b["boss_id"])


# ----------------------------------------------------------------------------- services / events
async def _svc_start(guild, boss_id, channel=None):
    ok, msg = await start_boss(_bot, bs._id_of(guild), str(boss_id), bs._id_of(channel) if channel else None)
    return ok


async def _svc_damage(guild, user, amount):
    b = await deal_damage(_bot, bs._id_of(guild), bs._id_of(user), float(amount))
    return b["hp"] if b else None


async def _svc_status(guild):
    b = await active_boss(bs._id_of(guild))
    return b


_bot: Optional[commands.Bot] = None


async def _on_hits(guild_id: str, user_id: str, event: str, amount: float):
    if event == "hits" and _bot is not None:
        await deal_damage(_bot, guild_id, user_id, float(amount))


# ----------------------------------------------------------------------------- commands
import functools  # noqa: E402


def _need_admin(f):
    @functools.wraps(f)
    async def wrapper(interaction: discord.Interaction, *a, **k):
        if not host.is_admin(interaction):
            return await interaction.response.send_message("You need **Manage Server** permission.", ephemeral=True)
        return await f(interaction, *a, **k)
    return wrapper


async def _boss_choices(interaction: discord.Interaction, current: str):
    if interaction.guild is None:
        return []
    rows = await host.db_fetch("SELECT boss_id, name, emoji FROM bf_bosses WHERE guild_id=?", (str(interaction.guild_id),))
    return [app_commands.Choice(name=f"{r['emoji']} {r['name']}", value=r["boss_id"]) for r in rows if current.lower() in r["name"].lower()][:25]


class _CreateModal(discord.ui.Modal, title="Create a boss"):
    name = discord.ui.TextInput(label="Name", max_length=60, placeholder="The Hollow King")
    hp = discord.ui.TextInput(label="Max HP | duration (minutes)", placeholder="5000 | 60", max_length=30)
    emoji = discord.ui.TextInput(label="Emoji", required=False, max_length=8, placeholder="👹")
    attack = discord.ui.TextInput(label="Attack: effect | value | every N seconds", max_length=60,
                                  placeholder="score_penalty | 5 | 300   (effects: score_penalty, silence, damage_drop)")

    def __init__(self, guild_id: str):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            a = [x.strip() for x in self.hp.value.split("|")]
            hp, dur = float(a[0]), int(a[1]) if len(a) > 1 and a[1] else 60
            e = [x.strip() for x in self.attack.value.split("|")]
            effect = e[0] if e and e[0] in EFFECTS else "score_penalty"
            val = float(e[1]) if len(e) > 1 and e[1] else 5.0
            every = int(e[2]) if len(e) > 2 and e[2] else 300
        except (ValueError, IndexError):
            return await interaction.response.send_message("Use `max hp | minutes` and `effect | value | seconds`.", ephemeral=True)
        bid = _bid(self.name.value)
        await host.db_exec(
            """INSERT INTO bf_bosses (guild_id,boss_id,name,emoji,max_hp,hp,duration_mins,attack_effect,attack_value,attack_interval)
               VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(guild_id,boss_id) DO UPDATE SET name=excluded.name, emoji=excluded.emoji,
               max_hp=excluded.max_hp, duration_mins=excluded.duration_mins, attack_effect=excluded.attack_effect,
               attack_value=excluded.attack_value, attack_interval=excluded.attack_interval""",
            (self.guild_id, bid, self.name.value.strip(), self.emoji.value.strip() or "👹", max(10, hp), max(10, hp), max(1, dur), effect, val, max(0, every)))
        await interaction.response.send_message(f"Boss **{self.name.value}** saved as `{bid}`. Add `/boss phases` and `/boss rewards`, then `/boss start`.", ephemeral=True)


class _PhaseModal(discord.ui.Modal, title="Boss phases"):
    lines = discord.ui.TextInput(label="One per line: name | HP % | damage ×  | attack every s", style=discord.TextStyle.paragraph,
                                 placeholder="Enraged | 60 | 1.5 | 180\nDesperate | 25 | 2 | 90", max_length=1500)

    def __init__(self, guild_id: str, boss_id: str):
        super().__init__()
        self.guild_id, self.boss_id = guild_id, boss_id

    async def on_submit(self, interaction: discord.Interaction):
        phases = []
        for ln in self.lines.value.splitlines():
            p = [x.strip() for x in ln.split("|")]
            if not p or not p[0]:
                continue
            try:
                ph = {"name": p[0], "threshold": float(p[1]) / 100.0 if len(p) > 1 and p[1] else 0.5}
                if len(p) > 2 and p[2]:
                    ph["multiplier"] = float(p[2])
                if len(p) > 3 and p[3]:
                    ph["attack_interval_secs"] = int(p[3])
            except ValueError:
                continue
            phases.append(ph)
        await host.db_exec("UPDATE bf_bosses SET phases=? WHERE guild_id=? AND boss_id=?", (json.dumps(phases), self.guild_id, self.boss_id))
        await interaction.response.send_message(f"Saved **{len(phases)}** phase(s).", ephemeral=True)


class _RewardModal(discord.ui.Modal, title="Boss rewards"):
    scope = discord.ui.TextInput(label="Who: all, team, individual or top N", placeholder="top 3", max_length=20)
    grants = discord.ui.TextInput(label="Rewards, one per line", style=discord.TextStyle.paragraph, max_length=1000,
                                  placeholder="currency 250\nrole 123456789012345678\nmessage {boss} has fallen! Thanks {winners}")

    def __init__(self, guild_id: str, boss_id: str):
        super().__init__()
        self.guild_id, self.boss_id = guild_id, boss_id

    async def on_submit(self, interaction: discord.Interaction):
        s = self.scope.value.strip().lower()
        n = int(re.sub(r"\D", "", s) or 3)
        scope = "topN" if s.startswith("top") else s if s in ("all", "team", "individual") else "all"
        grants = []
        for ln in self.grants.value.splitlines():
            kind, _, val = ln.strip().partition(" ")
            if kind.lower() in ("currency", "role", "message") and val.strip():
                grants.append({"type": kind.lower(), "value": val.strip()})
        await host.db_exec("UPDATE bf_bosses SET rewards=? WHERE guild_id=? AND boss_id=?",
                           (json.dumps({"scope": scope, "N": n, "grants": grants}), self.guild_id, self.boss_id))
        await interaction.response.send_message(f"Saved **{len(grants)}** reward(s) for scope `{scope}`.", ephemeral=True)


def build_commands(bot: commands.Bot):
    g = app_commands.Group(name="boss", description="Community boss fights", guild_only=True)

    @g.command(name="create", description="Create or edit a boss (Manage Server)")
    @_need_admin
    async def create(interaction: discord.Interaction):
        await interaction.response.send_modal(_CreateModal(str(interaction.guild_id)))

    @g.command(name="phases", description="Set a boss's phases (Manage Server)")
    @app_commands.autocomplete(boss=_boss_choices)
    @_need_admin
    async def phases(interaction: discord.Interaction, boss: str):
        if await get_boss(str(interaction.guild_id), boss) is None:
            return await interaction.response.send_message("No such boss.", ephemeral=True)
        await interaction.response.send_modal(_PhaseModal(str(interaction.guild_id), boss))

    @g.command(name="rewards", description="Set a boss's rewards (Manage Server)")
    @app_commands.autocomplete(boss=_boss_choices)
    @_need_admin
    async def rewards(interaction: discord.Interaction, boss: str):
        if await get_boss(str(interaction.guild_id), boss) is None:
            return await interaction.response.send_message("No such boss.", ephemeral=True)
        await interaction.response.send_modal(_RewardModal(str(interaction.guild_id), boss))

    @g.command(name="start", description="Start a boss now (Manage Server)")
    @app_commands.autocomplete(boss=_boss_choices)
    @_need_admin
    async def start(interaction: discord.Interaction, boss: str, channel: Optional[discord.TextChannel] = None):
        ok, msg = await start_boss(bot, str(interaction.guild_id), boss, str((channel or interaction.channel).id))
        await interaction.response.send_message(msg, ephemeral=True)

    @g.command(name="queue", description="Start this boss when the current one ends (Manage Server)")
    @app_commands.autocomplete(boss=_boss_choices)
    @_need_admin
    async def queue(interaction: discord.Interaction, boss: str):
        if await get_boss(str(interaction.guild_id), boss) is None:
            return await interaction.response.send_message("No such boss.", ephemeral=True)
        await host.db_exec("INSERT INTO bf_queue (guild_id,boss_id,queued_by,queued_at) VALUES (?,?,?,?)",
                           (str(interaction.guild_id), boss, str(interaction.user.id), _iso(now_utc())))
        await interaction.response.send_message("Queued.", ephemeral=True)

    @g.command(name="stop", description="End the active boss early (Manage Server)")
    @_need_admin
    async def stop(interaction: discord.Interaction):
        b = await active_boss(str(interaction.guild_id))
        if b is None:
            return await interaction.response.send_message("No boss is active.", ephemeral=True)
        await interaction.response.send_message("Ending it.", ephemeral=True)
        await end_boss(bot, b, "lost")

    @g.command(name="status", description="Show the active boss")
    async def status(interaction: discord.Interaction):
        b = await active_boss(str(interaction.guild_id))
        if b is None:
            return await interaction.response.send_message("No boss is active right now.", ephemeral=True)
        await interaction.response.send_message(embed=await feed_embed(b), ephemeral=True)

    @g.command(name="list", description="List this server's bosses")
    async def list_(interaction: discord.Interaction):
        rows = await host.db_fetch("SELECT * FROM bf_bosses WHERE guild_id=? ORDER BY name", (str(interaction.guild_id),))
        text = "\n".join(f"{r['emoji']} **{r['name']}** (`{r['boss_id']}`) — {int(r['max_hp']):,} HP · {r['status']}"
                         + (f" · repeats every {r['recurring_mins']} min" if r["recurring_mins"] else "") for r in rows) or "No bosses yet — `/boss create`."
        await interaction.response.send_message(text, ephemeral=True)

    @g.command(name="leaderboard", description="Damage leaderboard of the active (or a given) boss")
    @app_commands.autocomplete(boss=_boss_choices)
    async def leaderboard(interaction: discord.Interaction, boss: Optional[str] = None):
        gid = str(interaction.guild_id)
        b = await get_boss(gid, boss) if boss else await active_boss(gid)
        if b is None:
            return await interaction.response.send_message("No boss selected.", ephemeral=True)
        rows = await host.db_fetch("SELECT user_id, damage FROM bf_participants WHERE guild_id=? AND boss_id=? ORDER BY damage DESC LIMIT 10", (gid, b["boss_id"]))
        text = "\n".join(f"`{i}.` <@{r['user_id']}> — {float(r['damage']):,.0f}" for i, r in enumerate(rows, 1)) or "Nobody has fought yet."
        await interaction.response.send_message(embed=discord.Embed(title=f"{b['emoji']} {b['name']}", description=text, colour=discord.Colour.red()), ephemeral=True)

    @g.command(name="recurring", description="Respawn this boss automatically every N minutes after it ends (0 = off)")
    @app_commands.autocomplete(boss=_boss_choices)
    @_need_admin
    async def recurring(interaction: discord.Interaction, boss: str, minutes: int):
        await host.db_exec("UPDATE bf_bosses SET recurring_mins=?, next_spawn=? WHERE guild_id=? AND boss_id=?",
                           (max(0, minutes), _iso(now_utc() + timedelta(minutes=max(0, minutes))) if minutes > 0 else None, str(interaction.guild_id), boss))
        await interaction.response.send_message("Saved.", ephemeral=True)

    @g.command(name="delete", description="Delete a boss (Manage Server)")
    @app_commands.autocomplete(boss=_boss_choices)
    @_need_admin
    async def delete(interaction: discord.Interaction, boss: str):
        gid = str(interaction.guild_id)
        for t in ("bf_bosses", "bf_participants", "bf_queue"):
            await host.db_exec(f"DELETE FROM {t} WHERE guild_id=? AND boss_id=?", (gid, boss))
        await interaction.response.send_message("Deleted.", ephemeral=True)

    return [g]


class BossesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._names: list[str] = []

    async def cog_load(self):
        global _bot
        _bot = self.bot
        await init_boss_tables()
        bs.on_event(_on_hits)
        bs.register_service("boss.start", _svc_start)
        bs.register_service("boss.damage", _svc_damage)
        bs.register_service("boss.status", _svc_status)
        for c in build_commands(self.bot):
            self.bot.tree.add_command(c)
            self._names.append(c.name)
        self.watch.start()

    async def cog_unload(self):
        self.watch.cancel()
        bs.off_event(_on_hits)
        for s in ("boss.start", "boss.damage", "boss.status"):
            bs.unregister_service(s)
        for n in self._names:
            self.bot.tree.remove_command(n)
        await host.close()

    @tasks.loop(seconds=5)
    async def watch(self):
        try:
            await tick(self.bot)
        except Exception:
            log.exception("boss watchdog failed")

    @watch.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if MESSAGE_DAMAGE <= 0 or message.author.bot or message.guild is None:
            return
        k = (message.guild.id, message.author.id)
        if time.monotonic() - _msg_cd.get(k, 0) < 2.0:
            return
        _msg_cd[k] = time.monotonic()
        await deal_damage(self.bot, str(message.guild.id), str(message.author.id), MESSAGE_DAMAGE)


async def setup(bot: commands.Bot):
    await bot.add_cog(BossesCog(bot))
