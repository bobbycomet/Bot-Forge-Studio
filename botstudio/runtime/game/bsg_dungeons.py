"""Dungeons & Raids — solo, team and AFK runs with monsters, loot tables and thread-based combat (Bot Forge Studio game module).

/dungeon crawl | team | afk  open a thread (or run in the background).  Inside the thread, every message a
player sends is an attack: damage = attacks × the player's attack stat from Gear & Rarity.  Monsters strike back
on a timer; players answer with Block / Cast Spell buttons if their gear allows.  Team runs can be limited to
members of the leader's team (set with the Team nodes).  Everything — dungeons, monster stats, loot, difficulty —
is edited in /dungeonadmin, so no code changes are needed to add content.
"""

from __future__ import annotations
import asyncio
import random
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

import discord
from discord import Embed
from discord.ui import View, Button, Modal, TextInput, Select

import bsg_gear as gear
import bs_runtime as bs
from bs_game import host, track

# ══════════════════════════════════════════════════════════════════════════
C_OK, C_ERR, C_INFO, C_COMBAT = 0x10b981, 0xef4444, 0x3B82F6, 0xDC2626


def _ok(msg: str) -> Embed: return Embed(description=f"✅  {msg}", color=C_OK)
def _err(msg: str) -> Embed: return Embed(description=f"❌  {msg}", color=C_ERR)
def _info(title: str, body: str = "", color: int = C_INFO) -> Embed:
    return Embed(title=title, description=body or None, color=color)


def _hpbar(cur: float, mx: float, width: int = 14) -> str:
    cur = max(0, min(cur, mx))
    filled = int(width * (cur / mx)) if mx else 0
    return "🟥" * filled + "⬛" * (width - filled)


_db_exec = host.db_exec
_db_fetch = host.db_fetch
_db_fetch_one = host.db_fetch_one
_db_transaction = host.db_transaction
_add_gold = host.add_gold
_is_admin = host.is_admin
_get_alignment = host.get_team          # optional teams: None means "no team"
_client = None                          # set by the cog when it loads


def _cur() -> str:
    return host.currency_emoji


# In-memory routing / task handles — rebuilt from DB is NOT attempted across
# a bot restart (a restart ends any in-progress interactive/AFK runs; their
# DB rows are left status='active' and are swept to 'stopped' by
# sweep_orphaned_sessions() below, which you should call once on startup).
_thread_sessions: dict[str, str] = {}     # channel_id(str) -> session_id
_combat_tasks: dict[str, asyncio.Task] = {}
_spell_charges: dict[str, dict[str, int]] = {}   # session_id -> {user_id: charges_left}


def _now():
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# SCHEMA
# ══════════════════════════════════════════════════════════════════════════
async def init_dungeon_tables():
    await _db_exec(
        """CREATE TABLE IF NOT EXISTS dungeon_defs (
            guild_id       TEXT NOT NULL,
            dungeon_id     TEXT NOT NULL,
            display_name   TEXT NOT NULL,
            difficulty     TEXT NOT NULL DEFAULT 'normal',
            description    TEXT NOT NULL DEFAULT '',
            allow_solo     INTEGER NOT NULL DEFAULT 1,
            allow_team     INTEGER NOT NULL DEFAULT 1,
            allow_afk      INTEGER NOT NULL DEFAULT 0,
            min_players    INTEGER NOT NULL DEFAULT 1,
            max_players    INTEGER NOT NULL DEFAULT 4,
            atk_interval_s INTEGER NOT NULL DEFAULT 30,
            react_window_s INTEGER NOT NULL DEFAULT 12,
            afk_tick_s     INTEGER NOT NULL DEFAULT 20,
            enabled        INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (guild_id, dungeon_id)
        )""", ())
    await _db_exec(
        """CREATE TABLE IF NOT EXISTS dungeon_monsters (
            id            TEXT PRIMARY KEY,
            guild_id      TEXT NOT NULL,
            dungeon_id    TEXT NOT NULL,
            order_index   INTEGER NOT NULL DEFAULT 0,
            monster_name  TEXT NOT NULL,
            emoji         TEXT NOT NULL DEFAULT '👹',
            hp            REAL NOT NULL DEFAULT 100,
            power         REAL NOT NULL DEFAULT 10
        )""", ())
    await _db_exec(
        """CREATE TABLE IF NOT EXISTS dungeon_loot (
            id            TEXT PRIMARY KEY,
            guild_id      TEXT NOT NULL,
            dungeon_id    TEXT NOT NULL,
            drop_type     TEXT NOT NULL DEFAULT 'gear' CHECK(drop_type IN ('gear','gold')),
            template_id   TEXT,
            rarity        TEXT NOT NULL DEFAULT 'normal',
            gold_min      INTEGER NOT NULL DEFAULT 0,
            gold_max      INTEGER NOT NULL DEFAULT 0,
            drop_chance   REAL NOT NULL DEFAULT 0.5,
            drop_trigger  TEXT NOT NULL DEFAULT 'clear' CHECK(drop_trigger IN ('stage','clear'))
        )""", ())
    await _db_exec(
        """CREATE TABLE IF NOT EXISTS dungeon_sessions (
            session_id    TEXT PRIMARY KEY,
            guild_id      TEXT NOT NULL,
            dungeon_id    TEXT NOT NULL,
            mode          TEXT NOT NULL CHECK(mode IN ('solo','team','afk')),
            status        TEXT NOT NULL DEFAULT 'lobby' CHECK(status IN ('lobby','active','won','lost','stopped')),
            thread_id     TEXT,
            leader_id     TEXT NOT NULL,
            stage_index   INTEGER NOT NULL DEFAULT 0,
            monster_hp    REAL NOT NULL DEFAULT 0,
            monster_max_hp REAL NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        )""", ())
    await _db_exec(
        """CREATE TABLE IF NOT EXISTS dungeon_session_members (
            session_id   TEXT NOT NULL,
            user_id      TEXT NOT NULL,
            hp           REAL NOT NULL DEFAULT 100,
            max_hp       REAL NOT NULL DEFAULT 100,
            damage_dealt REAL NOT NULL DEFAULT 0,
            alive        INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (session_id, user_id)
        )""", ())
    await _db_exec(
        """CREATE TABLE IF NOT EXISTS dungeon_config (
            guild_id   TEXT PRIMARY KEY,
            channel_id TEXT NOT NULL
        )""", ())


async def sweep_orphaned_sessions():
    """Call once at startup (after a restart) to close out any sessions left
    'active'/'lobby' from before the bot went down."""
    await _db_exec(
        "UPDATE dungeon_sessions SET status='stopped' WHERE status IN ('lobby','active')", ())


# ══════════════════════════════════════════════════════════════════════════
# CONFIG / DEFS HELPERS
# ══════════════════════════════════════════════════════════════════════════
async def _get_dungeon(guild_id: str, dungeon_id: str) -> Optional[dict]:
    row = await _db_fetch_one(
        "SELECT * FROM dungeon_defs WHERE guild_id=? AND dungeon_id=? AND enabled=1",
        (guild_id, dungeon_id))
    return dict(row) if row else None


async def _get_monsters(guild_id: str, dungeon_id: str) -> list[dict]:
    rows = await _db_fetch(
        "SELECT * FROM dungeon_monsters WHERE guild_id=? AND dungeon_id=? ORDER BY order_index",
        (guild_id, dungeon_id))
    return [dict(r) for r in rows]


async def _get_loot(guild_id: str, dungeon_id: str) -> list[dict]:
    rows = await _db_fetch("SELECT * FROM dungeon_loot WHERE guild_id=? AND dungeon_id=?", (guild_id, dungeon_id))
    return [dict(r) for r in rows]


async def _get_channel(guild_id: str) -> Optional[int]:
    row = await _db_fetch_one("SELECT channel_id FROM dungeon_config WHERE guild_id=?", (guild_id,))
    return int(row["channel_id"]) if row else None


# ══════════════════════════════════════════════════════════════════════════
# SESSION HELPERS
# ══════════════════════════════════════════════════════════════════════════
async def _get_session(session_id: str) -> Optional[dict]:
    row = await _db_fetch_one("SELECT * FROM dungeon_sessions WHERE session_id=?", (session_id,))
    return dict(row) if row else None


async def _get_members(session_id: str) -> list[dict]:
    rows = await _db_fetch("SELECT * FROM dungeon_session_members WHERE session_id=?", (session_id,))
    return [dict(r) for r in rows]


async def _set_stage(session_id: str, guild_id: str, dungeon_id: str, stage_index: int) -> Optional[dict]:
    monsters = await _get_monsters(guild_id, dungeon_id)
    if stage_index >= len(monsters):
        return None
    m = monsters[stage_index]
    await _db_exec(
        "UPDATE dungeon_sessions SET stage_index=?, monster_hp=?, monster_max_hp=?, updated_at=? WHERE session_id=?",
        (stage_index, m["hp"], m["hp"], _now().isoformat(), session_id))
    return m


async def _roll_loot(guild_id: str, dungeon_id: str, user_id: str, trigger: str) -> list[str]:
    """Rolls this dungeon's loot table for one user; grants gear/gold; returns display lines."""
    lines = []
    for entry in await _get_loot(guild_id, dungeon_id):
        if entry["drop_trigger"] != trigger:
            continue
        if random.random() > float(entry["drop_chance"]):
            continue
        if entry["drop_type"] == "gold":
            amt = random.randint(int(entry["gold_min"]), max(int(entry["gold_min"]), int(entry["gold_max"])))
            if amt and _add_gold:
                await _add_gold(guild_id, user_id, amt)
                lines.append(f"{_cur()} +{amt} {host.currency_name}")
        elif entry["drop_type"] == "gear" and entry["template_id"]:
            iid = await gear.grant_gear(guild_id, user_id, entry["template_id"], entry["rarity"])
            if iid:
                tmpl = await _db_fetch_one("SELECT display_name, emoji FROM guild_gear WHERE guild_id=? AND gear_id=?",
                                            (guild_id, entry["template_id"]))
                name = tmpl["display_name"] if tmpl else entry["template_id"]
                emoji = tmpl["emoji"] if tmpl else "🎁"
                lines.append(f"{emoji} {gear.RARITY_EMOJI.get(entry['rarity'],'')} **{name}** ({entry['rarity']})")
    return lines


# ══════════════════════════════════════════════════════════════════════════
# COMBAT HOOK — the cog's on_message listener calls this for every attack message in a run thread.
# ══════════════════════════════════════════════════════════════════════════
async def on_combat_hit(guild_id: str, user_id: str, channel_id: str, system: str, hits: float, now=None):
    if hits <= 0:
        return
    session_id = _thread_sessions.get(str(channel_id))
    if not session_id:
        return
    session = await _get_session(session_id)
    if not session or session["status"] != "active" or session["mode"] not in ("solo", "team"):
        return
    members = await _get_members(session_id)
    member = next((m for m in members if m["user_id"] == user_id and m["alive"]), None)
    if not member:
        return
    stats = await gear.get_equipped_stats(guild_id, user_id)
    dmg = round(hits * stats["atk"] * 0.5, 1)   # 0.5 = base combat scaling, tune in code if too fast/slow
    await _db_exec(
        "UPDATE dungeon_session_members SET damage_dealt = damage_dealt + ? WHERE session_id=? AND user_id=?",
        (dmg, session_id, user_id))
    await track(guild_id, user_id, "boss_damage", max(1, int(dmg)))
    new_hp = max(0.0, float(session["monster_hp"]) - dmg)
    await _db_exec("UPDATE dungeon_sessions SET monster_hp=?, updated_at=? WHERE session_id=?", (new_hp, _now().isoformat(), session_id))

    thread = _client.get_channel(int(channel_id)) if _client else None
    if thread:
        try:
            await thread.send(f"⚔️ <@{user_id}> hits for **{dmg:g}**! ({max(0,new_hp):g}/{session['monster_max_hp']:g} HP)")
        except discord.HTTPException:
            pass

    if new_hp <= 0:
        await _advance_stage(session_id)


async def _advance_stage(session_id: str):
    session = await _get_session(session_id)
    if not session:
        return
    guild_id, dungeon_id = session["guild_id"], session["dungeon_id"]
    thread = _client.get_channel(int(session["thread_id"])) if (_client and session["thread_id"]) else None

    members = await _get_members(session_id)
    total_dmg = sum(m["damage_dealt"] for m in members) or 1.0
    loot_summary: dict[str, list[str]] = {}
    for m in members:
        share_lines = await _roll_loot(guild_id, dungeon_id, m["user_id"], "stage")
        if share_lines:
            loot_summary[m["user_id"]] = share_lines

    if thread and loot_summary:
        text = "\n".join(f"<@{uid}>: {', '.join(lines)}" for uid, lines in loot_summary.items())
        await thread.send(embed=_ok("Stage cleared! Loot:"), )
        await thread.send(text)

    next_monster = await _set_stage(session_id, guild_id, dungeon_id, session["stage_index"] + 1)
    if next_monster is None:
        await _finish_session(session_id, "won")
        return
    _spell_charges.setdefault(session_id, {})
    for m in members:
        _spell_charges[session_id][m["user_id"]] = 1
    if thread:
        await thread.send(embed=_info(
            f"{next_monster['emoji']} {next_monster['monster_name']} appears!",
            f"`{_hpbar(next_monster['hp'], next_monster['hp'])}` {next_monster['hp']:g}/{next_monster['hp']:g} HP\n"
            f"Keep typing in this thread to attack!"))


async def _finish_session(session_id: str, result: str):
    session = await _get_session(session_id)
    if not session:
        return
    await _db_exec("UPDATE dungeon_sessions SET status=?, updated_at=? WHERE session_id=?", (result, _now().isoformat(), session_id))
    guild_id, dungeon_id = session["guild_id"], session["dungeon_id"]
    thread = _client.get_channel(int(session["thread_id"])) if (_client and session["thread_id"]) else None
    members = await _get_members(session_id)

    for m in members:
        await track(guild_id, m["user_id"], "match_played", 1)
    if result == "won":
        loot_summary: dict[str, list[str]] = {}
        for m in members:
            lines = await _roll_loot(guild_id, dungeon_id, m["user_id"], "clear")
            await track(guild_id, m["user_id"], "dungeon_clear", 1)
            await track(guild_id, m["user_id"], "match_won", 1)
            if lines:
                loot_summary[m["user_id"]] = lines
        if thread:
            body = "\n".join(f"<@{uid}>: {', '.join(l)}" for uid, l in loot_summary.items()) or "No bonus loot this time."
            await thread.send(embed=_ok("🏆 Dungeon cleared!"))
            await thread.send(body)
    elif thread:
        await thread.send(embed=_err("💀 The party has fallen. Better luck next time."))

    if session["thread_id"]:
        _thread_sessions.pop(str(session["thread_id"]), None)
        if thread and isinstance(thread, discord.Thread):
            try:
                await thread.edit(archived=True, locked=True)
            except discord.HTTPException:
                pass
    task = _combat_tasks.pop(session_id, None)
    if task:
        task.cancel()
    _spell_charges.pop(session_id, None)


# ══════════════════════════════════════════════════════════════════════════
# MONSTER ATTACK LOOP (interactive solo/team)
# ══════════════════════════════════════════════════════════════════════════
class _DefendView(View):
    def __init__(self, session_id: str, target_id: str, has_shield: bool, has_spell: bool, window: int):
        super().__init__(timeout=window)
        self.session_id, self.target_id = session_id, target_id
        self.resolved = "none"
        if has_shield:
            self.add_item(self._btn("🛡️ Block", discord.ButtonStyle.primary, "block"))
        if has_spell:
            self.add_item(self._btn("✨ Cast Spell", discord.ButtonStyle.success, "spell"))

    def _btn(self, label, style, action):
        btn = Button(label=label, style=style)

        async def cb(interaction: discord.Interaction):
            if str(interaction.user.id) != self.target_id:
                return await interaction.response.send_message("This attack isn't aimed at you!", ephemeral=True)
            self.resolved = action
            await interaction.response.send_message(f"You chose **{label}**!", ephemeral=True)
            self.stop()
        btn.callback = cb
        return btn


async def _monster_attack(session_id: str):
    session = await _get_session(session_id)
    if not session or session["status"] != "active":
        return
    guild_id = session["guild_id"]
    members = [m for m in await _get_members(session_id) if m["alive"]]
    if not members:
        return
    target = random.choice(members)
    monsters = await _get_monsters(guild_id, session["dungeon_id"])
    monster = monsters[session["stage_index"]] if session["stage_index"] < len(monsters) else None
    if not monster:
        return
    power = float(monster["power"])
    stats = await gear.get_equipped_stats(guild_id, target["user_id"])
    has_shield = stats["block"] > 0
    charges = _spell_charges.setdefault(session_id, {})
    has_spell = charges.get(target["user_id"], 0) > 0

    thread = _client.get_channel(int(session["thread_id"])) if _client else None
    dungeon = await _get_dungeon(guild_id, session["dungeon_id"])
    window = dungeon["react_window_s"] if dungeon else 12
    view = _DefendView(session_id, target["user_id"], has_shield, has_spell, window)
    if thread:
        try:
            await thread.send(
                content=f"💥 **{monster['monster_name']}** winds up an attack on <@{target['user_id']}>! ({window}s to react)",
                view=view)
        except discord.HTTPException:
            pass
    await view.wait()

    dmg = power
    note = "took the full hit"
    if view.resolved == "block":
        dmg = power * (1 - stats["block"] / 100.0)
        note = f"blocked {stats['block']:.0f}% of the damage"
    elif view.resolved == "spell":
        dmg = 0
        charges[target["user_id"]] = 0
        note = "negated the attack with a spell!"

    new_hp = max(0.0, float(target["hp"]) - dmg)
    alive = 1 if new_hp > 0 else 0
    await _db_exec(
        "UPDATE dungeon_session_members SET hp=?, alive=? WHERE session_id=? AND user_id=?",
        (new_hp, alive, session_id, target["user_id"]))
    if thread:
        msg = f"<@{target['user_id']}> {note} — **{dmg:g} dmg** ({new_hp:g}/{target['max_hp']:g} HP)"
        if not alive:
            msg += "\n☠️ **They're down!**"
        await thread.send(msg)

    remaining = [m for m in await _get_members(session_id) if m["alive"]]
    if not remaining:
        await _finish_session(session_id, "lost")


async def _combat_loop(session_id: str):
    try:
        while True:
            session = await _get_session(session_id)
            if not session or session["status"] != "active":
                return
            dungeon = await _get_dungeon(session["guild_id"], session["dungeon_id"])
            interval = dungeon["atk_interval_s"] if dungeon else 30
            await asyncio.sleep(interval)
            session = await _get_session(session_id)
            if not session or session["status"] != "active":
                return
            await _monster_attack(session_id)
    except asyncio.CancelledError:
        return


# ══════════════════════════════════════════════════════════════════════════
# STARTING A RUN — solo / team
# ══════════════════════════════════════════════════════════════════════════
async def _open_thread(interaction: discord.Interaction, dungeon: dict, suffix: str) -> Optional[discord.Thread]:
    guild_id = str(interaction.guild.id)
    channel_id = await _get_channel(guild_id)
    parent = interaction.guild.get_channel(channel_id) if channel_id else interaction.channel
    if parent is None:
        parent = interaction.channel
    try:
        thread = await parent.create_thread(
            name=f"🗡️ {dungeon['display_name']} — {suffix}"[:100],
            type=discord.ChannelType.public_thread,
        )
        return thread
    except discord.HTTPException:
        return None


async def _begin_combat(session_id: str, thread: discord.Thread, guild_id: str, dungeon_id: str):
    m0 = await _set_stage(session_id, guild_id, dungeon_id, 0)
    await _db_exec("UPDATE dungeon_sessions SET status='active', updated_at=? WHERE session_id=?", (_now().isoformat(), session_id))
    _thread_sessions[str(thread.id)] = session_id
    members = await _get_members(session_id)
    _spell_charges[session_id] = {m["user_id"]: 1 for m in members}
    await thread.send(embed=_info(
        f"{m0['emoji']} {m0['monster_name']} blocks your path!",
        f"`{_hpbar(m0['hp'], m0['hp'])}` {m0['hp']:g}/{m0['hp']:g} HP\n\n"
        f"Type in this thread to attack. "
        f"When the monster strikes back, you'll get Block/Spell buttons if you have the gear for it."))
    _combat_tasks[session_id] = asyncio.create_task(_combat_loop(session_id))


class _TeamJoinView(View):
    def __init__(self, session_id: str, guild_id: str, dungeon: dict, alignment: Optional[str], thread: discord.Thread):
        super().__init__(timeout=90)
        self.session_id, self.guild_id, self.dungeon, self.alignment, self.thread = session_id, guild_id, dungeon, alignment, thread
        self.started = False

    @discord.ui.button(label="🤝 Join", style=discord.ButtonStyle.success)
    async def join(self, interaction: discord.Interaction, _btn):
        uid = str(interaction.user.id)
        members = await _get_members(self.session_id)
        if any(m["user_id"] == uid for m in members):
            return await interaction.response.send_message("You're already in!", ephemeral=True)
        if len(members) >= self.dungeon["max_players"]:
            return await interaction.response.send_message("Party is full.", ephemeral=True)
        if self.alignment:
            align = await _get_alignment(self.guild_id, uid)
            if align != self.alignment:
                return await interaction.response.send_message(
                    f"This party is recruiting members of **{self.alignment}** only.", ephemeral=True)
        stats = await gear.get_equipped_stats(self.guild_id, uid)
        await _db_exec(
            "INSERT INTO dungeon_session_members (session_id, user_id, hp, max_hp) VALUES (?,?,?,?)",
            (self.session_id, uid, stats["hp"], stats["hp"]))
        await interaction.response.send_message(f"<@{uid}> joined the party! ({len(members)+1}/{self.dungeon['max_players']})")

    @discord.ui.button(label="▶️ Start Now", style=discord.ButtonStyle.primary)
    async def start_now(self, interaction: discord.Interaction, _btn):
        session = await _get_session(self.session_id)
        if str(interaction.user.id) != session["leader_id"]:
            return await interaction.response.send_message("Only the party leader can start early.", ephemeral=True)
        self.started = True
        self.stop()
        await interaction.response.send_message("Starting the dungeon now!")


async def _run_team_lobby(session_id: str, guild_id: str, dungeon: dict, alignment: str, thread: discord.Thread):
    view = _TeamJoinView(session_id, guild_id, dungeon, alignment, thread)
    await thread.send(embed=_info(
        "🤝 Recruiting party members",
        f"Difficulty: **{dungeon['difficulty']}** · Needs {dungeon['min_players']}-{dungeon['max_players']} players\n"
        f"{('Only **' + alignment + '** members may join. ') if alignment else 'Anyone may join. '}Party leader can Start Now, "
        f"or it auto-starts in {view.timeout}s."), view=view)
    await view.wait()
    members = await _get_members(session_id)
    dungeon_id = dungeon["dungeon_id"]
    if len(members) < dungeon["min_players"]:
        await thread.send(embed=_err(f"Not enough players joined (needed {dungeon['min_players']}). Run cancelled."))
        await _db_exec("UPDATE dungeon_sessions SET status='stopped' WHERE session_id=?", (session_id,))
        try:
            await thread.edit(archived=True, locked=True)
        except discord.HTTPException:
            pass
        return
    await _begin_combat(session_id, thread, guild_id, dungeon_id)


# ══════════════════════════════════════════════════════════════════════════
# AFK DUNGEONS
# ══════════════════════════════════════════════════════════════════════════
async def _afk_loop(session_id: str):
    try:
        while True:
            session = await _get_session(session_id)
            if not session or session["status"] != "active":
                return
            guild_id, dungeon_id = session["guild_id"], session["dungeon_id"]
            dungeon = await _get_dungeon(guild_id, dungeon_id)
            tick = dungeon["afk_tick_s"] if dungeon else 20
            await asyncio.sleep(tick)
            session = await _get_session(session_id)
            if not session or session["status"] != "active":
                return
            members = await _get_members(session_id)
            member = members[0] if members else None
            if not member or not member["alive"]:
                return
            uid = member["user_id"]
            stats = await gear.get_equipped_stats(guild_id, uid)

            # Auto-attack
            dmg = round(stats["atk"] * 0.8, 1)
            await _db_exec("UPDATE dungeon_session_members SET damage_dealt = damage_dealt + ? WHERE session_id=? AND user_id=?",
                            (dmg, session_id, uid))
            new_hp = max(0.0, float(session["monster_hp"]) - dmg)
            await _db_exec("UPDATE dungeon_sessions SET monster_hp=?, updated_at=? WHERE session_id=?", (new_hp, _now().isoformat(), session_id))
            if new_hp <= 0:
                loot = await _roll_loot(guild_id, dungeon_id, uid, "stage")
                next_monster = await _set_stage(session_id, guild_id, dungeon_id, session["stage_index"] + 1)
                if next_monster is None:
                    await _finish_session(session_id, "won")
                    return
                continue

            # Auto-defend: monster retaliates every tick at reduced (AFK) power,
            # automatically mitigated by block% if the player has a shield.
            monsters = await _get_monsters(guild_id, dungeon_id)
            monster = monsters[session["stage_index"]] if session["stage_index"] < len(monsters) else None
            if monster:
                mdmg = float(monster["power"]) * (1 - stats["block"] / 100.0) * 0.6
                phhp = max(0.0, float(member["hp"]) - mdmg)
                alive = 1 if phhp > 0 else 0
                await _db_exec("UPDATE dungeon_session_members SET hp=?, alive=? WHERE session_id=? AND user_id=?",
                                (phhp, alive, session_id, uid))
                if not alive:
                    await _finish_session(session_id, "lost")
                    return
    except asyncio.CancelledError:
        return


async def _start_afk(guild_id: str, user_id: str, dungeon: dict) -> tuple[bool, str]:
    existing = await _db_fetch_one(
        "SELECT session_id FROM dungeon_sessions WHERE guild_id=? AND leader_id=? AND mode='afk' AND status='active'",
        (guild_id, user_id))
    if existing:
        return False, "You already have an AFK dungeon running — use `/dungeon afk_stop` first."
    session_id = "ds_" + uuid.uuid4().hex[:12]
    stats = await gear.get_equipped_stats(guild_id, user_id)
    ts = _now().isoformat()
    await _db_exec(
        """INSERT INTO dungeon_sessions (session_id, guild_id, dungeon_id, mode, status, leader_id, monster_hp, monster_max_hp, created_at, updated_at)
           VALUES (?,?,?,?,'active',?,0,0,?,?)""",
        (session_id, guild_id, dungeon["dungeon_id"], "afk", user_id, ts, ts))
    await _db_exec(
        "INSERT INTO dungeon_session_members (session_id, user_id, hp, max_hp) VALUES (?,?,?,?)",
        (session_id, user_id, stats["hp"], stats["hp"]))
    await _set_stage(session_id, guild_id, dungeon["dungeon_id"], 0)
    _combat_tasks[session_id] = asyncio.create_task(_afk_loop(session_id))
    return True, f"Started AFK run in **{dungeon['display_name']}**. Use `/dungeon afk_stop` to collect and end early."


async def stop_afk(guild_id: str, user_id: str) -> tuple[bool, str]:
    row = await _db_fetch_one(
        "SELECT session_id FROM dungeon_sessions WHERE guild_id=? AND leader_id=? AND mode='afk' AND status='active'",
        (guild_id, user_id))
    if not row:
        return False, "You don't have an AFK dungeon running."
    session_id = row["session_id"]
    task = _combat_tasks.pop(session_id, None)
    if task:
        task.cancel()
    await _db_exec("UPDATE dungeon_sessions SET status='stopped', updated_at=? WHERE session_id=?", (_now().isoformat(), session_id))
    return True, "AFK run stopped. Loot earned along the way has already been credited."


# ══════════════════════════════════════════════════════════════════════════
# PLAYER COMMANDS
# ══════════════════════════════════════════════════════════════════════════
async def _dungeon_autocomplete(interaction: discord.Interaction, current: str):
    if not interaction.guild:
        return []
    rows = await _db_fetch("SELECT dungeon_id, display_name FROM dungeon_defs WHERE guild_id=? AND enabled=1", (str(interaction.guild.id),))
    return [discord.app_commands.Choice(name=r["display_name"], value=r["dungeon_id"])
            for r in rows if current.lower() in r["display_name"].lower()][:25]


def register_dungeon_commands(tree, name="dungeon"):
    group = discord.app_commands.Group(name=name, description="Dungeons & raids")

    @group.command(name="crawl", description="Solo-crawl a dungeon")
    @discord.app_commands.describe(name="Which dungeon")
    @discord.app_commands.autocomplete(name=_dungeon_autocomplete)
    async def crawl(interaction: discord.Interaction, name: str):
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        guild_id = str(interaction.guild.id)
        dungeon = await _get_dungeon(guild_id, name)
        if not dungeon:
            return await interaction.response.send_message(embed=_err("Unknown dungeon."), ephemeral=True)
        if not dungeon["allow_solo"]:
            return await interaction.response.send_message(embed=_err("This dungeon can't be soloed — try `/dungeon team`."), ephemeral=True)
        await interaction.response.defer(thinking=True)
        thread = await _open_thread(interaction, dungeon, interaction.user.display_name)
        if not thread:
            return await interaction.followup.send(embed=_err("Couldn't open a thread — ask an admin to set the dungeon channel with `/dungeonadmin`."))
        session_id = "ds_" + uuid.uuid4().hex[:12]
        stats = await gear.get_equipped_stats(guild_id, str(interaction.user.id))
        ts = _now().isoformat()
        await _db_exec(
            "INSERT INTO dungeon_sessions (session_id, guild_id, dungeon_id, mode, status, leader_id, thread_id, created_at, updated_at) VALUES (?,?,?,?,'lobby',?,?,?,?)",
            (session_id, guild_id, name, "solo", str(interaction.user.id), str(thread.id), ts, ts))
        await _db_exec("INSERT INTO dungeon_session_members (session_id, user_id, hp, max_hp) VALUES (?,?,?,?)",
                        (session_id, str(interaction.user.id), stats["hp"], stats["hp"]))
        await _begin_combat(session_id, thread, guild_id, name)
        await interaction.followup.send(f"Dungeon started: {thread.mention}")

    @group.command(name="team", description="Recruit a party and crawl a dungeon together")
    @discord.app_commands.describe(name="Which dungeon")
    @discord.app_commands.autocomplete(name=_dungeon_autocomplete)
    async def team(interaction: discord.Interaction, name: str):
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        guild_id, uid = str(interaction.guild.id), str(interaction.user.id)
        dungeon = await _get_dungeon(guild_id, name)
        if not dungeon:
            return await interaction.response.send_message(embed=_err("Unknown dungeon."), ephemeral=True)
        if not dungeon["allow_team"]:
            return await interaction.response.send_message(embed=_err("This dungeon doesn't support team runs."), ephemeral=True)
        alignment = await _get_alignment(guild_id, uid)     # None = no team: the party is open to everyone
        await interaction.response.defer(thinking=True)
        thread = await _open_thread(interaction, dungeon, f"{interaction.user.display_name}'s party")
        if not thread:
            return await interaction.followup.send(embed=_err("Couldn't open a thread — ask an admin to set the dungeon channel with `/dungeonadmin`."))
        session_id = "ds_" + uuid.uuid4().hex[:12]
        stats = await gear.get_equipped_stats(guild_id, uid)
        ts = _now().isoformat()
        await _db_exec(
            "INSERT INTO dungeon_sessions (session_id, guild_id, dungeon_id, mode, status, leader_id, thread_id, created_at, updated_at) VALUES (?,?,?,?,'lobby',?,?,?,?)",
            (session_id, guild_id, name, "team", uid, str(thread.id), ts, ts))
        await _db_exec("INSERT INTO dungeon_session_members (session_id, user_id, hp, max_hp) VALUES (?,?,?,?)",
                        (session_id, uid, stats["hp"], stats["hp"]))
        await interaction.followup.send(f"Party thread opened: {thread.mention}")
        asyncio.create_task(_run_team_lobby(session_id, guild_id, dungeon, alignment, thread))

    @group.command(name="afk", description="Start an AFK dungeon run")
    @discord.app_commands.describe(name="Which dungeon")
    @discord.app_commands.autocomplete(name=_dungeon_autocomplete)
    async def afk(interaction: discord.Interaction, name: str):
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        guild_id = str(interaction.guild.id)
        dungeon = await _get_dungeon(guild_id, name)
        if not dungeon:
            return await interaction.response.send_message(embed=_err("Unknown dungeon."), ephemeral=True)
        if not dungeon["allow_afk"]:
            return await interaction.response.send_message(embed=_err("This dungeon doesn't support AFK mode."), ephemeral=True)
        ok, msg = await _start_afk(guild_id, str(interaction.user.id), dungeon)
        await interaction.response.send_message(embed=(_ok(msg) if ok else _err(msg)), ephemeral=True)

    @group.command(name="afk_stop", description="Stop your AFK dungeon run and collect earnings")
    async def afk_stop(interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        ok, msg = await stop_afk(str(interaction.guild.id), str(interaction.user.id))
        await interaction.response.send_message(embed=(_ok(msg) if ok else _err(msg)), ephemeral=True)

    tree.add_command(group)
    return group


# ══════════════════════════════════════════════════════════════════════════
# ADMIN CRUD: /dungeonadmin
# ══════════════════════════════════════════════════════════════════════════
class _DungeonModal(Modal, title="Dungeon"):
    dungeon_id = TextInput(label="Dungeon ID (unique, no spaces)", max_length=40)
    display_name = TextInput(label="Display name", max_length=60)
    difficulty = TextInput(label="Difficulty label", placeholder="easy / normal / hard / nightmare", default="normal")
    players = TextInput(label="min_players | max_players", default="1 | 4")
    modes = TextInput(label="solo | team | afk  (1 or 0 each)", placeholder="1 | 1 | 0", default="1 | 1 | 0")

    def __init__(self, guild_id: str):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        did = "".join(c for c in self.dungeon_id.value.strip() if c.isalnum() or c == "_")
        if not did:
            return await interaction.response.send_message(embed=_err("Dungeon ID required."), ephemeral=True)
        try:
            mn, mx = [int(p.strip()) for p in self.players.value.split("|")]
            s, t, a = [int(p.strip()) for p in self.modes.value.split("|")]
        except (ValueError, IndexError):
            return await interaction.response.send_message(embed=_err("Check the players / modes format."), ephemeral=True)
        await _db_exec(
            """INSERT INTO dungeon_defs (guild_id, dungeon_id, display_name, difficulty, min_players, max_players, allow_solo, allow_team, allow_afk)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(guild_id, dungeon_id) DO UPDATE SET
                   display_name=excluded.display_name, difficulty=excluded.difficulty,
                   min_players=excluded.min_players, max_players=excluded.max_players,
                   allow_solo=excluded.allow_solo, allow_team=excluded.allow_team, allow_afk=excluded.allow_afk""",
            (self.guild_id, did, self.display_name.value.strip(), self.difficulty.value.strip(), mn, mx, s, t, a))
        await interaction.response.send_message(embed=_ok(f"Dungeon **{did}** saved. Now add monsters with 'Add Monster'."), ephemeral=True)


class _MonsterModal(Modal, title="Add Monster"):
    monster_name = TextInput(label="Monster name", max_length=60)
    order_index = TextInput(label="Stage order (0 = first)", default="0")
    hp_power = TextInput(label="hp | power", placeholder="150 | 20", default="150 | 20")

    def __init__(self, guild_id: str, dungeon_id: str):
        super().__init__()
        self.guild_id, self.dungeon_id = guild_id, dungeon_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            order = int(self.order_index.value.strip())
            hp, power = [float(p.strip()) for p in self.hp_power.value.split("|")]
        except (ValueError, IndexError):
            return await interaction.response.send_message(embed=_err("Check the order/hp/power format."), ephemeral=True)
        await _db_exec(
            "INSERT INTO dungeon_monsters (id, guild_id, dungeon_id, order_index, monster_name, hp, power) VALUES (?,?,?,?,?,?,?)",
            ("dm_" + uuid.uuid4().hex[:12], self.guild_id, self.dungeon_id, order, self.monster_name.value.strip(), hp, power))
        await interaction.response.send_message(embed=_ok(f"Monster **{self.monster_name.value}** added to `{self.dungeon_id}`."), ephemeral=True)


class _LootModal(Modal, title="Add Loot Entry"):
    template_or_gold = TextInput(label="Gear ID (from the gear catalog; blank = currency)", required=False)
    rarity = TextInput(label="Rarity (for gear drops)", default="normal", required=False)
    gold_range = TextInput(label="min | max  (for currency drops)", default="0 | 0", required=False)
    chance_trigger = TextInput(label="drop_chance (0-1) | trigger (stage/clear)", default="0.3 | clear")

    def __init__(self, guild_id: str, dungeon_id: str):
        super().__init__()
        self.guild_id, self.dungeon_id = guild_id, dungeon_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            chance, trigger = [p.strip() for p in self.chance_trigger.value.split("|")]
            chance = float(chance)
            if trigger not in ("stage", "clear"):
                raise ValueError
        except (ValueError, IndexError):
            return await interaction.response.send_message(embed=_err("Check the drop_chance/trigger format."), ephemeral=True)
        tid = self.template_or_gold.value.strip() or None
        gmin, gmax = 0, 0
        if not tid:
            try:
                gmin, gmax = [int(p.strip()) for p in self.gold_range.value.split("|")]
            except (ValueError, IndexError):
                return await interaction.response.send_message(embed=_err("Check the min | max format."), ephemeral=True)
        await _db_exec(
            """INSERT INTO dungeon_loot (id, guild_id, dungeon_id, drop_type, template_id, rarity, gold_min, gold_max, drop_chance, drop_trigger)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            ("dl_" + uuid.uuid4().hex[:12], self.guild_id, self.dungeon_id, "gold" if not tid else "gear", tid,
             self.rarity.value.strip() or "normal", gmin, gmax, chance, trigger))
        await interaction.response.send_message(embed=_ok("Loot entry added."), ephemeral=True)


class DungeonAdminView(View):
    def __init__(self, guild_id: str):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.button(label="➕ Add/Edit Dungeon", style=discord.ButtonStyle.primary, row=0)
    async def add_dungeon(self, interaction: discord.Interaction, _btn):
        await interaction.response.send_modal(_DungeonModal(self.guild_id))

    @discord.ui.button(label="📋 List Dungeons", style=discord.ButtonStyle.secondary, row=0)
    async def list_dungeons(self, interaction: discord.Interaction, _btn):
        rows = await _db_fetch("SELECT * FROM dungeon_defs WHERE guild_id=? ORDER BY display_name", (self.guild_id,))
        if not rows:
            return await interaction.response.send_message(embed=_info("No dungeons yet."), ephemeral=True)
        lines = []
        for r in rows:
            modes = "".join(["🧍" if r["allow_solo"] else "", "🤝" if r["allow_team"] else "", "💤" if r["allow_afk"] else ""])
            lines.append(f"{'🟢' if r['enabled'] else '⚪'} `{r['dungeon_id']}` **{r['display_name']}** [{r['difficulty']}] {modes} "
                         f"({r['min_players']}-{r['max_players']}p)")
        await interaction.response.send_message(embed=_info(f"📋 Dungeons ({len(rows)})", "\n".join(lines)[:4000]), ephemeral=True)

    @discord.ui.button(label="🗑️ Delete Dungeon", style=discord.ButtonStyle.danger, row=0)
    async def del_dungeon(self, interaction: discord.Interaction, _btn):
        rows = await _db_fetch("SELECT dungeon_id, display_name FROM dungeon_defs WHERE guild_id=? LIMIT 25", (self.guild_id,))
        if not rows:
            return await interaction.response.send_message(embed=_err("Nothing to delete."), ephemeral=True)
        opts = [discord.SelectOption(label=r["display_name"][:100], value=r["dungeon_id"]) for r in rows]
        view = View(timeout=60)
        sel = Select(placeholder="Choose dungeon to delete…", options=opts)

        async def cb(i2):
            did = i2.data["values"][0]
            await _db_transaction([
                ("DELETE FROM dungeon_defs WHERE guild_id=? AND dungeon_id=?", (self.guild_id, did)),
                ("DELETE FROM dungeon_monsters WHERE guild_id=? AND dungeon_id=?", (self.guild_id, did)),
                ("DELETE FROM dungeon_loot WHERE guild_id=? AND dungeon_id=?", (self.guild_id, did)),
            ])
            await i2.response.send_message(embed=_ok(f"Deleted `{did}` and its monsters/loot."), ephemeral=True)
        sel.callback = cb
        view.add_item(sel)
        await interaction.response.send_message(embed=_info("🗑️ Delete Dungeon"), view=view, ephemeral=True)

    @discord.ui.button(label="👹 Add Monster", style=discord.ButtonStyle.primary, row=1)
    async def add_monster(self, interaction: discord.Interaction, _btn):
        rows = await _db_fetch("SELECT dungeon_id, display_name FROM dungeon_defs WHERE guild_id=? LIMIT 25", (self.guild_id,))
        if not rows:
            return await interaction.response.send_message(embed=_err("Create a dungeon first."), ephemeral=True)
        opts = [discord.SelectOption(label=r["display_name"][:100], value=r["dungeon_id"]) for r in rows]
        view = View(timeout=60)
        sel = Select(placeholder="Choose dungeon…", options=opts)

        async def cb(i2: discord.Interaction):
            await i2.response.send_modal(_MonsterModal(self.guild_id, i2.data["values"][0]))
        sel.callback = cb
        view.add_item(sel)
        await interaction.response.send_message(embed=_info("👹 Add Monster", "Lower stage order = fights first."), view=view, ephemeral=True)

    @discord.ui.button(label="📋 List Monsters", style=discord.ButtonStyle.secondary, row=1)
    async def list_monsters(self, interaction: discord.Interaction, _btn):
        rows = await _db_fetch("SELECT dungeon_id, display_name FROM dungeon_defs WHERE guild_id=? LIMIT 25", (self.guild_id,))
        if not rows:
            return await interaction.response.send_message(embed=_err("No dungeons yet."), ephemeral=True)
        opts = [discord.SelectOption(label=r["display_name"][:100], value=r["dungeon_id"]) for r in rows]
        view = View(timeout=60)
        sel = Select(placeholder="Choose dungeon…", options=opts)

        async def cb(i2: discord.Interaction):
            did = i2.data["values"][0]
            monsters = await _get_monsters(self.guild_id, did)
            if not monsters:
                return await i2.response.send_message(embed=_info("No monsters in this dungeon yet."), ephemeral=True)
            lines = [f"{m['order_index']}. {m['emoji']} **{m['monster_name']}** — {m['hp']:g} HP / {m['power']:g} pwr" for m in monsters]
            await i2.response.send_message(embed=_info(f"👹 Monsters in {did}", "\n".join(lines)), ephemeral=True)
        sel.callback = cb
        view.add_item(sel)
        await interaction.response.send_message(embed=_info("📋 List Monsters"), view=view, ephemeral=True)

    @discord.ui.button(label="🎁 Add Loot", style=discord.ButtonStyle.primary, row=2)
    async def add_loot(self, interaction: discord.Interaction, _btn):
        rows = await _db_fetch("SELECT dungeon_id, display_name FROM dungeon_defs WHERE guild_id=? LIMIT 25", (self.guild_id,))
        if not rows:
            return await interaction.response.send_message(embed=_err("Create a dungeon first."), ephemeral=True)
        opts = [discord.SelectOption(label=r["display_name"][:100], value=r["dungeon_id"]) for r in rows]
        view = View(timeout=60)
        sel = Select(placeholder="Choose dungeon…", options=opts)

        async def cb(i2: discord.Interaction):
            await i2.response.send_modal(_LootModal(self.guild_id, i2.data["values"][0]))
        sel.callback = cb
        view.add_item(sel)
        await interaction.response.send_message(
            embed=_info("🎁 Add Loot Entry", "Leave the template field blank for a currency drop instead of gear."),
            view=view, ephemeral=True)

    @discord.ui.button(label="📺 Set Dungeon Channel", style=discord.ButtonStyle.secondary, row=2)
    async def set_channel(self, interaction: discord.Interaction, _btn):
        view = View(timeout=60)
        ch_select = discord.ui.ChannelSelect(placeholder="Choose the parent channel for dungeon threads…",
                                              channel_types=[discord.ChannelType.text])

        async def cb(i2: discord.Interaction):
            channel = ch_select.values[0]
            await _db_exec(
                "INSERT INTO dungeon_config (guild_id, channel_id) VALUES (?,?) ON CONFLICT(guild_id) DO UPDATE SET channel_id=excluded.channel_id",
                (self.guild_id, str(channel.id)))
            await i2.response.send_message(embed=_ok(f"Dungeon threads will open under {channel.mention}."), ephemeral=True)
        ch_select.callback = cb
        view.add_item(ch_select)
        await interaction.response.send_message(embed=_info("📺 Set Dungeon Channel"), view=view, ephemeral=True)


def register_dungeon_admin_commands(tree, name="dungeonadmin"):
    @tree.command(name=name, description="Manage dungeons, monsters and loot (Manage Server only)")
    async def dungeonadmin_cmd(interaction: discord.Interaction):
        if _is_admin and not _is_admin(interaction):
            return await interaction.response.send_message(embed=_err("You need **Manage Server** permission."), ephemeral=True)
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        view = DungeonAdminView(str(interaction.guild.id))
        await interaction.response.send_message(embed=_info("🗺️ Dungeon Admin", "Dungeons, monsters, loot tables, and the thread channel."),
                                                  view=view, ephemeral=True)
    return dungeonadmin_cmd


# ══════════════════════════════════════════════════════════════════════════
# COG ENTRY POINT (Bot Forge Studio loads this file as an extension)
# ══════════════════════════════════════════════════════════════════════════
import time as _time  # noqa: E402
from discord.ext import commands  # noqa: E402

DUNGEON_COMMAND = %%dungeon_command%%
ADMIN_COMMAND = %%admin_command%%
ATTACK_WORDS = [w.lower() for w in %%attack_words_list%%]      # empty = every message is an attack
ATTACK_COOLDOWN = %%attack_cooldown%%
host.wallet = %%wallet%%
host.currency_name = %%currency_name%%
host.currency_emoji = %%currency_emoji%%


class DungeonsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._names: list[str] = []
        self._last: dict[tuple[str, str], float] = {}

    async def cog_load(self):
        global _client
        _client = self.bot
        await gear.init_gear_tables()
        await init_dungeon_tables()
        await sweep_orphaned_sessions()
        grp = register_dungeon_commands(self.bot.tree, DUNGEON_COMMAND)
        adm = register_dungeon_admin_commands(self.bot.tree, ADMIN_COMMAND)
        self._names = [getattr(grp, "name", DUNGEON_COMMAND), getattr(adm, "name", ADMIN_COMMAND)]

    async def cog_unload(self):
        for n in self._names:
            self.bot.tree.remove_command(n)
        for t in list(_combat_tasks.values()):
            t.cancel()
        await host.close()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or str(message.channel.id) not in _thread_sessions:
            return
        text = message.content.lower()
        if ATTACK_WORDS and not any(w in text for w in ATTACK_WORDS):
            return
        key = (str(message.channel.id), str(message.author.id))
        now = _time.monotonic()
        if now - self._last.get(key, 0) < ATTACK_COOLDOWN:
            return
        self._last[key] = now
        await on_combat_hit(str(message.guild.id), str(message.author.id), str(message.channel.id), "message", 1)


async def setup(bot: commands.Bot):
    await bot.add_cog(DungeonsCog(bot))
