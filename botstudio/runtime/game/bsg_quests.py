"""Quests — daily quests with streak-free progress bars and claim buttons (Bot Forge Studio game module).

Quests count *events*.  Anything can report one: the Track Progress node, the other game modules
(currency_earned, dungeon_clear, boss_damage, …), or your own code through ``bs.track(guild, user, event, amount)``.
Admins define quests per server with /questsadmin: pick a known event or type your own event name.
Rewards are paid in the shared wallet, so they show up for Economy nodes too.
"""

from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

import discord
from discord import app_commands, Embed
from discord.ui import View, Button, Modal, TextInput, Select

from bs_game import host, on_event

# ══════════════════════════════════════════════════════════════════════════
# EMBED HELPERS (same palette as the rest of the bot)
# ══════════════════════════════════════════════════════════════════════════
C_OK, C_ERR, C_INFO = 0x10b981, 0xef4444, 0xF5A623


def _ok(msg: str) -> Embed:
    return Embed(description=f"✅  {msg}", color=C_OK)


def _err(msg: str) -> Embed:
    return Embed(description=f"❌  {msg}", color=C_ERR)


def _info(title: str, body: str = "", color: int = C_INFO) -> Embed:
    return Embed(title=title, description=body or None, color=color)


def _today(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(timezone.utc)).date().isoformat()


def _bar(progress: int, target: int, width: int = 12) -> str:
    progress = max(0, min(progress, target))
    filled = int(width * (progress / target)) if target else width
    return "█" * filled + "░" * (width - filled)


_db_exec = host.db_exec
_db_fetch = host.db_fetch
_db_fetch_one = host.db_fetch_one
_db_transaction = host.db_transaction
_add_gold = host.add_gold
_is_admin = host.is_admin


def _cur() -> str:
    return host.currency_emoji


# ══════════════════════════════════════════════════════════════════════════
# QUEST TYPES + DEFAULTS
# ══════════════════════════════════════════════════════════════════════════
# type key         → (label,                                    emoji)
QUEST_TYPES = {
    "messages":        ("Send messages",                   "💬"),
    "currency_earned": ("Earn currency",                   "🪙"),
    "match_played":    ("Play a match or event",           "⚔️"),
    "match_won":       ("Win a match or event",            "🏆"),
    "boss_damage":     ("Deal damage to a boss",           "🐲"),
    "dungeon_clear":   ("Clear a dungeon",                 "🏰"),
    "item_crafted":    ("Craft, enchant or evolve gear",   "🛠️"),
    "daily_login":     ("Use the bot today",               "📅"),
}
CUSTOM_EVENT = "__custom__"

# (quest_id, event, target, reward, description) — seeded once per server, fully editable afterwards
DEFAULT_DAILY_QUESTS = [
    ("daily_messages", "messages",        20,  40, "Send 20 messages today"),
    ("daily_earn",     "currency_earned", 100, 25, "Earn 100 currency today"),
    ("daily_play",     "match_played",    1,   30, "Play 1 match today"),
]


# ══════════════════════════════════════════════════════════════════════════
# SCHEMA
# ══════════════════════════════════════════════════════════════════════════
async def init_quest_tables():
    """Call once from init_db(). Safe to call every startup (IF NOT EXISTS)."""
    await _db_exec(
        """CREATE TABLE IF NOT EXISTS quest_defs (
            guild_id     TEXT NOT NULL,
            quest_id     TEXT NOT NULL,
            quest_type   TEXT NOT NULL,
            target       INTEGER NOT NULL,
            reward_gold  INTEGER NOT NULL DEFAULT 0,
            description  TEXT NOT NULL DEFAULT '',
            enabled      INTEGER NOT NULL DEFAULT 1,
            is_default   INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, quest_id)
        )""", ())
    await _db_exec(
        """CREATE TABLE IF NOT EXISTS quest_progress (
            guild_id    TEXT NOT NULL,
            user_id     TEXT NOT NULL,
            quest_id    TEXT NOT NULL,
            date_key    TEXT NOT NULL,
            progress    INTEGER NOT NULL DEFAULT 0,
            claimed     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id, quest_id, date_key)
        )""", ())


async def _ensure_defaults(guild_id: str):
    row = await _db_fetch_one(
        "SELECT quest_id FROM quest_defs WHERE guild_id=? LIMIT 1", (guild_id,))
    if row:
        return
    ops = []
    for qid, qtype, target, reward, desc in DEFAULT_DAILY_QUESTS:
        ops.append((
            """INSERT OR IGNORE INTO quest_defs
               (guild_id, quest_id, quest_type, target, reward_gold, description, enabled, is_default)
               VALUES (?,?,?,?,?,?,1,1)""",
            (guild_id, qid, qtype, target, reward, desc),
        ))
    await _db_transaction(ops)


# ══════════════════════════════════════════════════════════════════════════
# PROGRESS TRACKING — events come from the bus (bs.track / Track Progress node)
# ══════════════════════════════════════════════════════════════════════════
async def _bump(guild_id: str, user_id: str, quest_type: str, amount: float, now: Optional[datetime] = None):
    """Add `amount` progress to every enabled quest of this type, today. No-ops
    safely if configure() hasn't been called or amount <= 0."""
    if _db_fetch is None or amount <= 0:
        return
    await _ensure_defaults(guild_id)
    date_key = _today(now)
    quests = await _db_fetch(
        "SELECT quest_id, target FROM quest_defs WHERE guild_id=? AND quest_type=? AND enabled=1",
        (guild_id, quest_type))
    if not quests:
        return
    ops = []
    for q in quests:
        qid, target = q["quest_id"], int(q["target"])
        amt = int(amount)
        # Cap progress at target.
        ops.append((
            """INSERT INTO quest_progress (guild_id, user_id, quest_id, date_key, progress, claimed)
               VALUES (?,?,?,?,?,0)
               ON CONFLICT(guild_id, user_id, quest_id, date_key) DO UPDATE SET
                   progress = LEAST(CAST(? AS INTEGER), quest_progress.progress + CAST(? AS INTEGER))""",
            (guild_id, user_id, qid, date_key, min(amt, target), target, amt),
        ))
    await _db_transaction(ops)


async def on_progress_event(guild_id: str, user_id: str, event: str, amount: float = 1):
    """Registered on the event bus: every reported event bumps the quests that watch it."""
    await _bump(guild_id, user_id, event, amount)


# ══════════════════════════════════════════════════════════════════════════
# READ / CLAIM
# ══════════════════════════════════════════════════════════════════════════
async def get_today_quests(guild_id: str, user_id: str, now: Optional[datetime] = None) -> list[dict]:
    """Returns each enabled quest def merged with today's progress (0 if untouched)."""
    await _ensure_defaults(guild_id)
    date_key = _today(now)
    defs = await _db_fetch(
        "SELECT * FROM quest_defs WHERE guild_id=? AND enabled=1 ORDER BY quest_type",
        (guild_id,))
    prog_rows = await _db_fetch(
        "SELECT quest_id, progress, claimed FROM quest_progress "
        "WHERE guild_id=? AND user_id=? AND date_key=?",
        (guild_id, user_id, date_key))
    prog = {r["quest_id"]: r for r in prog_rows}
    out = []
    for d in defs:
        d = dict(d)
        p = prog.get(d["quest_id"])
        d["progress"] = int(p["progress"]) if p else 0
        d["claimed"] = bool(p["claimed"]) if p else False
        d["complete"] = d["progress"] >= d["target"]
        out.append(d)
    return out


async def claim_quest(guild_id: str, user_id: str, quest_id: str, now: Optional[datetime] = None):
    """Returns (ok: bool, message: str, reward_gold: int)."""
    date_key = _today(now)
    qdef = await _db_fetch_one(
        "SELECT * FROM quest_defs WHERE guild_id=? AND quest_id=? AND enabled=1",
        (guild_id, quest_id))
    if not qdef:
        return False, "That quest doesn't exist (or was disabled).", 0
    prow = await _db_fetch_one(
        "SELECT progress, claimed FROM quest_progress "
        "WHERE guild_id=? AND user_id=? AND quest_id=? AND date_key=?",
        (guild_id, user_id, quest_id, date_key))
    progress = int(prow["progress"]) if prow else 0
    already = bool(prow["claimed"]) if prow else False
    if already:
        return False, "You already claimed this quest today.", 0
    if progress < int(qdef["target"]):
        return False, "Not finished yet — keep going!", 0
    await _db_exec(
        "UPDATE quest_progress SET claimed=1 WHERE guild_id=? AND user_id=? AND quest_id=? AND date_key=?",
        (guild_id, user_id, quest_id, date_key))
    reward = int(qdef["reward_gold"])
    if reward and _add_gold:
        await _add_gold(guild_id, user_id, reward)
    return True, f"Claimed **{qdef['description']}** — +{reward} {_cur()}", reward


# ══════════════════════════════════════════════════════════════════════════
# PLAYER-FACING: /quests
# ══════════════════════════════════════════════════════════════════════════
def _quest_embed(quests: list[dict], display_name: str) -> Embed:
    e = _info(f"📋 {display_name}'s Daily Quests", color=0xF5A623)
    if not quests:
        e.description = "No quests configured for this server yet."
        return e
    lines = []
    for q in quests:
        emoji = QUEST_TYPES.get(q["quest_type"], ("", "🎯"))[1]
        status = "✅ Claimed" if q["claimed"] else ("🟢 Ready to claim!" if q["complete"] else "")
        lines.append(
            f"{emoji} **{q['description']}**\n"
            f"`{_bar(q['progress'], q['target'])}` {q['progress']}/{q['target']}"
            f"  ·  +{q['reward_gold']} {_cur()}  {status}"
        )
    e.description = "\n\n".join(lines)
    e.set_footer(text="Resets daily at midnight UTC")
    return e


class QuestBoardView(View):
    def __init__(self, guild_id: str, user_id: str, display_name: str, quests: list[dict]):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        self.user_id = user_id
        self.display_name = display_name
        self._add_claim_buttons(quests)

    def _add_claim_buttons(self, quests: list[dict]):
        self.clear_items()
        claimable = [q for q in quests if q["complete"] and not q["claimed"]]
        for q in claimable[:5]:
            emoji = QUEST_TYPES.get(q["quest_type"], ("", "🎯"))[1]
            btn = Button(label=f"Claim: {q['description'][:60]}"[:80],
                         style=discord.ButtonStyle.success, emoji=emoji)
            btn.callback = self._make_claim_cb(q["quest_id"])
            self.add_item(btn)
        refresh = Button(label="🔄 Refresh", style=discord.ButtonStyle.secondary)
        refresh.callback = self._refresh
        self.add_item(refresh)

    def _make_claim_cb(self, quest_id: str):
        async def cb(interaction: discord.Interaction):
            ok, msg, _reward = await claim_quest(self.guild_id, self.user_id, quest_id)
            quests = await get_today_quests(self.guild_id, self.user_id)
            self._add_claim_buttons(quests)
            embed = _quest_embed(quests, self.display_name)
            await interaction.response.edit_message(embed=embed, view=self)
            await interaction.followup.send(embed=(_ok(msg) if ok else _err(msg)), ephemeral=True)
        return cb

    async def _refresh(self, interaction: discord.Interaction):
        quests = await get_today_quests(self.guild_id, self.user_id)
        self._add_claim_buttons(quests)
        await interaction.response.edit_message(embed=_quest_embed(quests, self.display_name), view=self)


# ══════════════════════════════════════════════════════════════════════════
# ADMIN CRUD: /questsadmin
# ══════════════════════════════════════════════════════════════════════════
def _type_options() -> list[discord.SelectOption]:
    opts = [discord.SelectOption(label=v[0][:100], value=k, emoji=v[1], description=f"event: {k}") for k, v in QUEST_TYPES.items()]
    return opts + [discord.SelectOption(label="Custom event…", value=CUSTOM_EVENT, emoji="✏️",
                                        description="Count an event you report with Track Progress")]


class _AddQuestModal(Modal, title="Add Quest"):
    quest_id = TextInput(label="Quest ID (unique, no spaces)", placeholder="daily_streak_bonus", max_length=40)
    target = TextInput(label="Target amount", placeholder="20", max_length=10)
    reward_gold = TextInput(label="Reward (currency)", placeholder="40", max_length=10)
    description = TextInput(label="Description shown to players", placeholder="Send 20 messages today",
                             style=discord.TextStyle.paragraph, max_length=200)
    event = TextInput(label="Event name (what gets counted)", placeholder="my_custom_event", max_length=40, required=False)

    def __init__(self, guild_id: str, quest_type: str):
        super().__init__()
        self.guild_id = guild_id
        self.quest_type = quest_type
        if quest_type != CUSTOM_EVENT:
            self.remove_item(self.event)

    async def on_submit(self, interaction: discord.Interaction):
        qid = "".join(c for c in self.quest_id.value.strip() if c.isalnum() or c == "_")
        try:
            target = max(1, int(self.target.value.strip()))
            reward = max(0, int(self.reward_gold.value.strip()))
        except ValueError:
            return await interaction.response.send_message(embed=_err("Target and reward must be numbers."), ephemeral=True)
        if not qid:
            return await interaction.response.send_message(embed=_err("Quest ID is required."), ephemeral=True)
        qtype = self.quest_type
        if qtype == CUSTOM_EVENT:
            qtype = "".join(c for c in self.event.value.strip().lower() if c.isalnum() or c == "_")
            if not qtype:
                return await interaction.response.send_message(embed=_err("Type the event name to count."), ephemeral=True)
        await _db_exec(
            """INSERT INTO quest_defs (guild_id, quest_id, quest_type, target, reward_gold, description, enabled, is_default)
               VALUES (?,?,?,?,?,?,1,0)
               ON CONFLICT(guild_id, quest_id) DO UPDATE SET
                   quest_type=excluded.quest_type, target=excluded.target,
                   reward_gold=excluded.reward_gold, description=excluded.description""",
            (self.guild_id, qid, qtype, target, reward, self.description.value.strip()))
        await interaction.response.send_message(embed=_ok(f"Quest **{qid}** saved."), ephemeral=True)


class _EditQuestModal(Modal, title="Edit Quest"):
    target = TextInput(label="Target amount")
    reward_gold = TextInput(label="Reward (currency)")
    description = TextInput(label="Description", style=discord.TextStyle.paragraph, max_length=200)

    def __init__(self, guild_id: str, quest_id: str, row: dict):
        super().__init__()
        self.guild_id = guild_id
        self.quest_id = quest_id
        self.target.default = str(row["target"])
        self.reward_gold.default = str(row["reward_gold"])
        self.description.default = row["description"]

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target = max(1, int(self.target.value.strip()))
            reward = max(0, int(self.reward_gold.value.strip()))
        except ValueError:
            return await interaction.response.send_message(embed=_err("Target and reward must be numbers."), ephemeral=True)
        await _db_exec(
            "UPDATE quest_defs SET target=?, reward_gold=?, description=? WHERE guild_id=? AND quest_id=?",
            (target, reward, self.description.value.strip(), self.guild_id, self.quest_id))
        await interaction.response.send_message(embed=_ok(f"Quest **{self.quest_id}** updated."), ephemeral=True)


class QuestAdminView(View):
    def __init__(self, guild_id: str):
        super().__init__(timeout=300)
        self.guild_id = guild_id

    @discord.ui.button(label="📋 List Quests", style=discord.ButtonStyle.secondary, row=0)
    async def list_quests(self, interaction: discord.Interaction, _btn):
        rows = await _db_fetch("SELECT * FROM quest_defs WHERE guild_id=? ORDER BY quest_type", (self.guild_id,))
        if not rows:
            return await interaction.response.send_message(embed=_info("No quests configured yet."), ephemeral=True)
        lines = []
        for r in rows:
            emoji = QUEST_TYPES.get(r["quest_type"], ("", "🎯"))[1]
            state = "🟢" if r["enabled"] else "⚪"
            lines.append(f"{state} {emoji} `{r['quest_id']}` — {r['description']} "
                         f"(target {r['target']}, +{r['reward_gold']}{_cur()})")
        await interaction.response.send_message(
            embed=_info(f"📋 Quests ({len(rows)})", "\n".join(lines)[:4000]), ephemeral=True)

    @discord.ui.button(label="➕ Add Quest", style=discord.ButtonStyle.primary, row=0)
    async def add_quest(self, interaction: discord.Interaction, _btn):
        view = View(timeout=60)
        sel = Select(placeholder="What should be counted?", options=_type_options())

        async def sel_cb(i2: discord.Interaction):
            qtype = i2.data["values"][0]
            await i2.response.send_modal(_AddQuestModal(self.guild_id, qtype))
        sel.callback = sel_cb
        view.add_item(sel)
        await interaction.response.send_message(embed=_info("➕ Add Quest — pick a type first"), view=view, ephemeral=True)

    async def _pick_quest(self, interaction: discord.Interaction, prompt: str):
        rows = await _db_fetch("SELECT quest_id, description FROM quest_defs WHERE guild_id=? ORDER BY quest_id LIMIT 25", (self.guild_id,))
        if not rows:
            await interaction.response.send_message(embed=_err("No quests to pick from."), ephemeral=True)
            return None, None
        opts = [discord.SelectOption(label=r["quest_id"][:100], description=r["description"][:100], value=r["quest_id"]) for r in rows]
        view = View(timeout=60)
        sel = Select(placeholder=prompt, options=opts)
        fut = discord.utils.MISSING
        return view, sel

    @discord.ui.button(label="✏️ Edit Quest", style=discord.ButtonStyle.primary, row=0)
    async def edit_quest(self, interaction: discord.Interaction, _btn):
        rows = await _db_fetch("SELECT * FROM quest_defs WHERE guild_id=? ORDER BY quest_id LIMIT 25", (self.guild_id,))
        if not rows:
            return await interaction.response.send_message(embed=_err("No quests to edit."), ephemeral=True)
        by_id = {r["quest_id"]: dict(r) for r in rows}
        opts = [discord.SelectOption(label=r["quest_id"][:100], description=r["description"][:100], value=r["quest_id"]) for r in rows]
        view = View(timeout=60)
        sel = Select(placeholder="Choose quest to edit…", options=opts)

        async def sel_cb(i2: discord.Interaction):
            qid = i2.data["values"][0]
            await i2.response.send_modal(_EditQuestModal(self.guild_id, qid, by_id[qid]))
        sel.callback = sel_cb
        view.add_item(sel)
        await interaction.response.send_message(embed=_info("✏️ Edit Quest"), view=view, ephemeral=True)

    @discord.ui.button(label="🔀 Enable/Disable", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_quest(self, interaction: discord.Interaction, _btn):
        rows = await _db_fetch("SELECT quest_id, description, enabled FROM quest_defs WHERE guild_id=? ORDER BY quest_id LIMIT 25", (self.guild_id,))
        if not rows:
            return await interaction.response.send_message(embed=_err("No quests yet."), ephemeral=True)
        opts = [discord.SelectOption(
            label=f"{'🟢' if r['enabled'] else '⚪'} {r['quest_id']}"[:100],
            description=r["description"][:100], value=r["quest_id"]) for r in rows]
        view = View(timeout=60)
        sel = Select(placeholder="Choose quest to toggle…", options=opts)

        async def sel_cb(i2: discord.Interaction):
            qid = i2.data["values"][0]
            row = next(r for r in rows if r["quest_id"] == qid)
            new_state = 0 if row["enabled"] else 1
            await _db_exec("UPDATE quest_defs SET enabled=? WHERE guild_id=? AND quest_id=?", (new_state, self.guild_id, qid))
            await i2.response.send_message(embed=_ok(f"`{qid}` is now {'enabled' if new_state else 'disabled'}."), ephemeral=True)
        sel.callback = sel_cb
        view.add_item(sel)
        await interaction.response.send_message(embed=_info("🔀 Enable/Disable"), view=view, ephemeral=True)

    @discord.ui.button(label="🗑️ Delete Quest", style=discord.ButtonStyle.danger, row=1)
    async def delete_quest(self, interaction: discord.Interaction, _btn):
        rows = await _db_fetch("SELECT quest_id, description FROM quest_defs WHERE guild_id=? ORDER BY quest_id LIMIT 25", (self.guild_id,))
        if not rows:
            return await interaction.response.send_message(embed=_err("No quests yet."), ephemeral=True)
        opts = [discord.SelectOption(label=r["quest_id"][:100], description=r["description"][:100], value=r["quest_id"]) for r in rows]
        view = View(timeout=60)
        sel = Select(placeholder="Choose quest to delete…", options=opts)

        async def sel_cb(i2: discord.Interaction):
            qid = i2.data["values"][0]
            await _db_exec("DELETE FROM quest_defs WHERE guild_id=? AND quest_id=?", (self.guild_id, qid))
            await _db_exec("DELETE FROM quest_progress WHERE guild_id=? AND quest_id=?", (self.guild_id, qid))
            await i2.response.send_message(embed=_ok(f"Deleted `{qid}`."), ephemeral=True)
        sel.callback = sel_cb
        view.add_item(sel)
        await interaction.response.send_message(embed=_info("🗑️ Delete Quest"), view=view, ephemeral=True)


# ══════════════════════════════════════════════════════════════════════════
# COMMAND REGISTRATION
# ══════════════════════════════════════════════════════════════════════════
def register_quest_commands(tree, names=("quests", "questsadmin")):
    """Registers the player board and the admin panel on *tree*; returns both commands."""

    @tree.command(name=names[0], description="View and claim your daily quests")
    async def quests_cmd(interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        gid, uid = str(interaction.guild.id), str(interaction.user.id)
        await interaction.response.defer(ephemeral=True, thinking=True)
        quests = await get_today_quests(gid, uid)
        view = QuestBoardView(gid, uid, interaction.user.display_name, quests)
        await interaction.followup.send(embed=_quest_embed(quests, interaction.user.display_name),
                                         view=view, ephemeral=True)

    @tree.command(name=names[1], description="Manage daily quests (Manage Server only)")
    async def questsadmin_cmd(interaction: discord.Interaction):
        if _is_admin and not _is_admin(interaction):
            return await interaction.response.send_message(embed=_err("You need **Manage Server** permission."), ephemeral=True)
        if not interaction.guild:
            return await interaction.response.send_message("Server only.", ephemeral=True)
        gid = str(interaction.guild.id)
        await _ensure_defaults(gid)
        view = QuestAdminView(gid)
        await interaction.response.send_message(
            embed=_info("🎯 Quest Admin", "Add, edit, enable/disable, or delete daily quests for this server."),
            view=view, ephemeral=True)

    return quests_cmd, questsadmin_cmd


# Public alias for external callers (e.g. user_panel.py) — avoids reaching
# into a "private" underscore name from another module.
build_quest_embed = _quest_embed


# ══════════════════════════════════════════════════════════════════════════
# COG ENTRY POINT (Bot Forge Studio loads this file as an extension)
# ══════════════════════════════════════════════════════════════════════════
from discord.ext import commands  # noqa: E402

BOARD_COMMAND = %%board_command%%
ADMIN_COMMAND = %%admin_command%%
host.wallet = %%wallet%%
host.currency_name = %%currency_name%%
host.currency_emoji = %%currency_emoji%%


class QuestsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._cmds: list = []
        self._last: dict = {}

    async def cog_load(self):
        await init_quest_tables()
        on_event(on_progress_event)
        self._cmds = list(register_quest_commands(self.bot.tree, (BOARD_COMMAND, ADMIN_COMMAND)))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        import time as _t
        now, key = _t.monotonic(), (message.guild.id, message.author.id)
        if now - self._last.get(key, 0) < 2.0:      # a burst of messages counts once per two seconds
            return
        self._last[key] = now
        await on_progress_event(str(message.guild.id), str(message.author.id), "messages", 1)

    async def cog_unload(self):
        import bs_runtime as bs
        bs.off_event(on_progress_event)
        for c in self._cmds:
            self.bot.tree.remove_command(c.name)
        await host.close()


async def setup(bot: commands.Bot):
    await bot.add_cog(QuestsCog(bot))
