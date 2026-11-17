"""Arena — 1v1 duels in a thread with HP, Elo ratings, wagers and a ladder (Bot Forge Studio game module).

/arena challenge @member [wager] opens a challenge with Accept / Decline buttons.  When accepted a thread is created and the
fight begins: every message the two fighters post in the thread attacks (damage = scoring hits × attack modifier, or one hit
per message if Factions & Phrase Scoring is not installed).  Max HP comes from a base value plus equipped gear (Gear & Rarity).
A fight ends on a knockout or when the round timer runs out — the fighter with more HP wins.  Elo is tracked per faction
(the member's team) with a dynamic K-factor, a win streak is kept, wagers are paid from the shared wallet with a daily cap on
winnings and diminishing returns for repeat opponents.  Settings live under /arenaconfig.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import bs_runtime as bs
from bs_game import host

log = logging.getLogger("bot.arena")
host.wallet = %%wallet%%
host.currency_name = %%currency_name%%
host.currency_emoji = %%currency_emoji%%
DEFAULTS = {"enabled": True, "round_secs": 90, "base_hp": 100, "challenge_cooldown": 120, "gold_daily_cap": 2000,
            "max_wager": 500, "challenge_timeout": 60}
_thread_match: dict[str, str] = {}         # thread id -> match id
_state: dict[str, dict] = {}               # match id -> live state
_cool: dict[tuple, datetime] = {}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def elo_k(rating: float) -> float:
    return 40.0 if rating < 1100 else 32.0 if rating < 1400 else 16.0


def elo_change(winner: float, loser: float, k: float = 0) -> float:
    k = k or elo_k(winner)
    return k * (1.0 - 1.0 / (1.0 + 10 ** ((loser - winner) / 400)))


def hp_bar(cur: float, mx: float, n: int = 10) -> str:
    pct = max(0.0, min(1.0, cur / max(1.0, mx)))
    block = "🟩" if pct > 0.6 else "🟨" if pct > 0.3 else "🟥"
    f = round(pct * n)
    return block * f + "⬛" * (n - f)


async def init_arena_tables():
    for sql in (
        """CREATE TABLE IF NOT EXISTS ar_matches (match_id TEXT PRIMARY KEY, guild_id TEXT NOT NULL, challenger_id TEXT NOT NULL, defender_id TEXT NOT NULL,
            faction_c TEXT NOT NULL DEFAULT '', faction_d TEXT NOT NULL DEFAULT '', wager REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',
            c_hp REAL, d_hp REAL, c_max REAL, d_max REAL, thread_id TEXT, winner_id TEXT, started_at TEXT, ends_at TEXT, knockout INTEGER NOT NULL DEFAULT 0)""",
        """CREATE TABLE IF NOT EXISTS ar_ratings (guild_id TEXT NOT NULL, user_id TEXT NOT NULL, faction TEXT NOT NULL DEFAULT '',
            rating REAL NOT NULL DEFAULT 1000, wins INTEGER NOT NULL DEFAULT 0, losses INTEGER NOT NULL DEFAULT 0, draws INTEGER NOT NULL DEFAULT 0,
            win_streak INTEGER NOT NULL DEFAULT 0, best_streak INTEGER NOT NULL DEFAULT 0, ko_count INTEGER NOT NULL DEFAULT 0,
            gold_won REAL NOT NULL DEFAULT 0, gold_lost REAL NOT NULL DEFAULT 0, PRIMARY KEY (guild_id, user_id, faction))""",
        """CREATE TABLE IF NOT EXISTS ar_daily (guild_id TEXT NOT NULL, user_id TEXT NOT NULL, day TEXT NOT NULL, won REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id, day))""",
    ):
        await host.db_exec(sql, ())


async def cfg(guild_id: str) -> dict:
    return {**DEFAULTS, **(await bs.kv_get("arena_cfg", guild_id, {}) or {})}


async def rating(guild_id: str, user_id: str, faction: str) -> dict:
    r = await host.db_fetch_one("SELECT * FROM ar_ratings WHERE guild_id=? AND user_id=? AND faction=?", (guild_id, user_id, faction))
    return dict(r) if r else {"rating": 1000.0, "wins": 0, "losses": 0, "draws": 0, "win_streak": 0, "best_streak": 0, "ko_count": 0,
                              "gold_won": 0.0, "gold_lost": 0.0}


async def update_rating(guild_id, user_id, faction, result: str, delta: float, gold: float = 0.0, knockout: bool = False):
    r = await rating(guild_id, user_id, faction)
    streak = r["win_streak"] + 1 if result == "win" else 0
    await host.db_exec(
        """INSERT INTO ar_ratings (guild_id,user_id,faction,rating,wins,losses,draws,win_streak,best_streak,ko_count,gold_won,gold_lost)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(guild_id,user_id,faction) DO UPDATE SET rating=excluded.rating, wins=excluded.wins,
           losses=excluded.losses, draws=excluded.draws, win_streak=excluded.win_streak, best_streak=excluded.best_streak,
           ko_count=excluded.ko_count, gold_won=excluded.gold_won, gold_lost=excluded.gold_lost""",
        (guild_id, user_id, faction, max(100.0, r["rating"] + delta), r["wins"] + (result == "win"), r["losses"] + (result == "loss"),
         r["draws"] + (result == "draw"), streak, max(r["best_streak"], streak), r["ko_count"] + (1 if knockout and result == "win" else 0),
         r["gold_won"] + max(0.0, gold), r["gold_lost"] + max(0.0, -gold)))
    return max(100.0, r["rating"] + delta), streak


async def _daily_won(guild_id: str, user_id: str) -> float:
    r = await host.db_fetch_one("SELECT won FROM ar_daily WHERE guild_id=? AND user_id=? AND day=?", (guild_id, user_id, now_utc().strftime("%Y-%m-%d")))
    return float(r["won"]) if r else 0.0


async def _add_daily(guild_id: str, user_id: str, amount: float):
    await host.db_exec("INSERT INTO ar_daily (guild_id,user_id,day,won) VALUES (?,?,?,?) ON CONFLICT(guild_id,user_id,day) DO UPDATE SET won=won+excluded.won",
                       (guild_id, user_id, now_utc().strftime("%Y-%m-%d"), amount))


async def _repeat_fights_today(guild_id: str, a: str, b: str) -> int:
    r = await host.db_fetch_one(
        "SELECT COUNT(*) c FROM ar_matches WHERE guild_id=? AND status='ended' AND started_at LIKE ? AND "
        "((challenger_id=? AND defender_id=?) OR (challenger_id=? AND defender_id=?))",
        (guild_id, now_utc().strftime("%Y-%m-%d") + "%", a, b, b, a))
    return int(r["c"]) if r else 0


async def _faction_of(guild_id: str, user_id: str) -> str:
    return await host.get_team(guild_id, user_id) or ""


async def _fighter(guild_id: str, user_id: str, base_hp: int) -> tuple[float, float]:
    """(max_hp, attack modifier) from gear when Gear & Rarity is installed."""
    st = await bs.service("gear.stats", guild_id, user_id) or {}
    return max(50.0, float(base_hp) + float(st.get("hp", 0))), 1.0 + max(0.0, float(st.get("atk", 0)) - 5.0) / 100.0


# ----------------------------------------------------------------------------- fight
async def _board(match: dict, st: dict) -> discord.Embed:
    e = discord.Embed(title="⚔️ Arena", colour=discord.Colour.red())
    for uid, hp, mx in ((match["challenger_id"], st["c_hp"], st["c_max"]), (match["defender_id"], st["d_hp"], st["d_max"])):
        e.add_field(name="\u200b", inline=False, value=f"<@{uid}>\n{hp_bar(hp, mx)} {max(0, int(hp))}/{int(mx)}")
    if float(match["wager"]):
        e.set_footer(text=f"Wager: {host.money(match['wager'])}")
    return e


async def begin(bot, match: dict, channel: discord.abc.GuildChannel, c_name: str, d_name: str) -> Optional[discord.Thread]:
    gid = match["guild_id"]
    c = await cfg(gid)
    c_max, c_atk = await _fighter(gid, match["challenger_id"], c["base_hp"])
    d_max, d_atk = await _fighter(gid, match["defender_id"], c["base_hp"])
    try:
        thread = await channel.create_thread(name=f"⚔️ {c_name} vs {d_name}"[:90], type=discord.ChannelType.public_thread,
                                             auto_archive_duration=60)
    except (discord.HTTPException, AttributeError):
        return None
    now = now_utc()
    ends = now + timedelta(seconds=int(c["round_secs"]))
    await host.db_exec("UPDATE ar_matches SET status='active', c_hp=?, d_hp=?, c_max=?, d_max=?, thread_id=?, started_at=?, ends_at=? WHERE match_id=?",
                       (c_max, d_max, c_max, d_max, str(thread.id), now.isoformat(), ends.isoformat(), match["match_id"]))
    st = {"c_hp": c_max, "d_hp": d_max, "c_max": c_max, "d_max": d_max, "atk": {match["challenger_id"]: c_atk, match["defender_id"]: d_atk},
          "ends": ends, "hits": 0, "board": None}
    _state[match["match_id"]] = st
    _thread_match[str(thread.id)] = match["match_id"]
    st["board"] = await thread.send(f"<@{match['challenger_id']}> ⚔️ <@{match['defender_id']}> — **fight!** You have {c['round_secs']}s. Type to attack.",
                                    embed=await _board(match, st), allowed_mentions=discord.AllowedMentions(users=True))
    return thread


async def hit(bot, message: discord.Message) -> None:
    mid = _thread_match.get(str(message.channel.id))
    st = _state.get(mid) if mid else None
    if st is None:
        return
    match = await host.db_fetch_one("SELECT * FROM ar_matches WHERE match_id=?", (mid,))
    if match is None or str(message.author.id) not in (match["challenger_id"], match["defender_id"]):
        return
    gid, uid = match["guild_id"], str(message.author.id)
    scan = await bs.service("score.scan", gid, uid, message.content)
    raw = sum(scan.values()) if isinstance(scan, dict) else 1.0
    if raw <= 0:
        return
    dmg = max(0.1, raw * st["atk"][uid] * 2.0)
    target = "d_hp" if uid == match["challenger_id"] else "c_hp"
    st[target] = max(0.0, st[target] - dmg)
    st["hits"] += 1
    if st["hits"] % 3 == 0 and st["board"] is not None:
        try:
            await st["board"].edit(embed=await _board(dict(match), st))
        except discord.HTTPException:
            pass
    if st["c_hp"] <= 0 or st["d_hp"] <= 0:
        await resolve(bot, mid, knockout=True)


async def resolve(bot, match_id: str, knockout: bool = False) -> None:
    row = await host.db_fetch_one("SELECT * FROM ar_matches WHERE match_id=?", (match_id,))
    if row is None or row["status"] != "active":
        return
    m = dict(row)
    st = _state.pop(match_id, {"c_hp": m["c_hp"], "d_hp": m["d_hp"], "c_max": m["c_max"], "d_max": m["d_max"]})
    _thread_match.pop(str(m.get("thread_id")), None)
    gid, c_id, d_id = m["guild_id"], m["challenger_id"], m["defender_id"]
    if st["c_hp"] > st["d_hp"]:
        win, lose, fw, fl = c_id, d_id, m["faction_c"], m["faction_d"]
    elif st["d_hp"] > st["c_hp"]:
        win, lose, fw, fl = d_id, c_id, m["faction_d"], m["faction_c"]
    else:
        win = lose = None
        fw = fl = ""
    await host.db_exec("UPDATE ar_matches SET status='ended', winner_id=?, c_hp=?, d_hp=?, knockout=? WHERE match_id=?",
                       (win, st["c_hp"], st["d_hp"], 1 if knockout else 0, match_id))
    lines = []
    cset = await cfg(gid)
    if win is None:
        for uid, f in ((c_id, m["faction_c"]), (d_id, m["faction_d"])):
            await update_rating(gid, uid, f, "draw", 0.0)
        lines.append("🤝 **Draw** — ratings unchanged.")
    else:
        rw, rl = (await rating(gid, win, fw))["rating"], (await rating(gid, lose, fl))["rating"]
        delta = elo_change(rw, rl)
        repeats = await _repeat_fights_today(gid, win, lose)
        factor = 1.0 if repeats <= 1 else max(0.25, 1.0 / repeats)      # diminishing returns for farming one opponent
        delta *= factor
        gold = 0.0
        wager = float(m["wager"])
        if wager > 0:
            room = max(0.0, float(cset["gold_daily_cap"]) - await _daily_won(gid, win))
            gold = min(wager * factor, room)
            if gold > 0 and await host.deduct_gold(gid, lose, gold):
                await host.add_gold(gid, win, gold)
                await _add_daily(gid, win, gold)
            else:
                gold = 0.0
        nr, streak = await update_rating(gid, win, fw, "win", delta, gold, knockout)
        await update_rating(gid, lose, fl, "loss", -delta, -gold)
        lines.append(f"🏆 <@{win}> wins{' by **knockout**' if knockout else ''}! Rating **{nr:.0f}** (+{delta:.0f}) · streak {streak}"
                     + (f" · won {host.money(gold)}" if gold else ""))
        lines.append(f"<@{lose}> −{delta:.0f}")
        await bs.track(gid, win, "match_won", 1)
        await bs.track(gid, win, "arena_win", 1)
    for uid in (c_id, d_id):
        await bs.track(gid, uid, "match_played", 1)
    try:
        th = bot.get_channel(int(m["thread_id"])) if m.get("thread_id") else None
        if th is not None:
            await th.send("\n".join(lines), allowed_mentions=discord.AllowedMentions.none())
            await th.edit(archived=True, locked=True)
    except discord.HTTPException:
        pass


async def tick(bot) -> None:
    now = now_utc()
    for mid, st in list(_state.items()):
        if now >= st["ends"]:
            await resolve(bot, mid)


# ----------------------------------------------------------------------------- challenge UI
class ChallengeView(discord.ui.View):
    def __init__(self, bot, match: dict, challenger: discord.Member, defender: discord.Member, channel):
        super().__init__(timeout=60)
        self.bot, self.match, self.challenger, self.defender, self.channel, self.msg = bot, match, challenger, defender, channel, None

    async def interaction_check(self, i: discord.Interaction) -> bool:
        if i.user.id != self.defender.id:
            await i.response.send_message("Only the challenged member can answer.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success, emoji="⚔️")
    async def accept(self, i: discord.Interaction, _b):
        wager = float(self.match["wager"])
        if wager > 0 and (await host.balance(i.guild_id, self.defender.id) < wager or await host.balance(i.guild_id, self.challenger.id) < wager):
            return await i.response.send_message("One of you can't cover the wager.", ephemeral=True)
        await i.response.edit_message(content="Challenge accepted — creating the arena…", view=None)
        self.stop()
        th = await begin(self.bot, self.match, self.channel, self.challenger.display_name, self.defender.display_name)
        if th is None:
            await host.db_exec("UPDATE ar_matches SET status='cancelled' WHERE match_id=?", (self.match["match_id"],))
            await i.followup.send("I couldn't create a thread here (needs Create Public Threads).", ephemeral=True)
        else:
            await i.edit_original_response(content=f"Fight on in {th.mention}!")

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, i: discord.Interaction, _b):
        await host.db_exec("UPDATE ar_matches SET status='declined' WHERE match_id=?", (self.match["match_id"],))
        await i.response.edit_message(content=f"{self.defender.mention} declined.", view=None)
        self.stop()

    async def on_timeout(self):
        await host.db_exec("UPDATE ar_matches SET status='expired' WHERE match_id=? AND status='pending'", (self.match["match_id"],))
        if self.msg is not None:
            try:
                await self.msg.edit(content="The challenge expired.", view=None)
            except discord.HTTPException:
                pass


def build_commands(bot: commands.Bot):
    g = app_commands.Group(name="arena", description="1v1 duels with ratings", guild_only=True)

    @g.command(name="challenge", description="Challenge a member to a duel")
    @app_commands.describe(opponent="Who to fight", wager="Currency each of you puts up (0 = just for rating)")
    async def challenge(interaction: discord.Interaction, opponent: discord.Member, wager: int = 0):
        gid = str(interaction.guild_id)
        c = await cfg(gid)
        if not c["enabled"]:
            return await interaction.response.send_message("The arena is closed.", ephemeral=True)
        if opponent.bot or opponent.id == interaction.user.id:
            return await interaction.response.send_message("Pick another member.", ephemeral=True)
        last = _cool.get((gid, interaction.user.id))
        if last and (now_utc() - last).total_seconds() < int(c["challenge_cooldown"]):
            return await interaction.response.send_message("You challenged someone a moment ago — wait a bit.", ephemeral=True)
        wager = max(0, min(int(wager), int(c["max_wager"])))
        busy = await host.db_fetch_one("SELECT 1 FROM ar_matches WHERE guild_id=? AND status IN ('pending','active') AND (challenger_id IN (?,?) OR defender_id IN (?,?))",
                                       (gid, str(interaction.user.id), str(opponent.id), str(interaction.user.id), str(opponent.id)))
        if busy:
            return await interaction.response.send_message("One of you is already in a fight or has a pending challenge.", ephemeral=True)
        if wager and await host.balance(gid, interaction.user.id) < wager:
            return await interaction.response.send_message("You can't cover that wager.", ephemeral=True)
        _cool[(gid, interaction.user.id)] = now_utc()
        mid = uuid.uuid4().hex[:10]
        fc, fd = await _faction_of(gid, str(interaction.user.id)), await _faction_of(gid, str(opponent.id))
        await host.db_exec("INSERT INTO ar_matches (match_id,guild_id,challenger_id,defender_id,faction_c,faction_d,wager) VALUES (?,?,?,?,?,?,?)",
                           (mid, gid, str(interaction.user.id), str(opponent.id), fc, fd, float(wager)))
        match = dict(await host.db_fetch_one("SELECT * FROM ar_matches WHERE match_id=?", (mid,)))
        view = ChallengeView(bot, match, interaction.user, opponent, interaction.channel)
        await interaction.response.send_message(
            f"⚔️ {opponent.mention}, **{interaction.user.display_name}** challenges you!" + (f" Wager: **{host.money(wager)}** each." if wager else ""),
            view=view, allowed_mentions=discord.AllowedMentions(users=True))
        view.msg = await interaction.original_response()

    @g.command(name="rank", description="Show a member's arena rating")
    async def rank(interaction: discord.Interaction, member: Optional[discord.Member] = None):
        m = member or interaction.user
        rows = await host.db_fetch("SELECT * FROM ar_ratings WHERE guild_id=? AND user_id=? ORDER BY rating DESC", (str(interaction.guild_id), str(m.id)))
        if not rows:
            return await interaction.response.send_message(f"{m.display_name} hasn't fought yet.", ephemeral=True)
        e = discord.Embed(title=f"⚔️ {m.display_name}", colour=discord.Colour.red())
        for r in rows:
            e.add_field(name=r["faction"] or "Unaligned", inline=False,
                        value=f"Rating **{r['rating']:.0f}** · {r['wins']}W {r['losses']}L {r['draws']}D · streak {r['win_streak']} (best {r['best_streak']}) · KOs {r['ko_count']}")
        await interaction.response.send_message(embed=e, ephemeral=True)

    @g.command(name="leaderboard", description="Top ratings")
    async def board(interaction: discord.Interaction, faction: Optional[str] = None):
        gid = str(interaction.guild_id)
        rows = await host.db_fetch("SELECT user_id, faction, rating, wins, losses FROM ar_ratings WHERE guild_id=? " + ("AND faction=? " if faction else "") +
                                   "ORDER BY rating DESC LIMIT 10", (gid, faction) if faction else (gid,))
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        text = "\n".join(f"{medals.get(i, f'`{i}.`')} <@{r['user_id']}> — **{r['rating']:.0f}** ({r['wins']}-{r['losses']}) {r['faction']}" for i, r in enumerate(rows, 1)) or "No fights yet."
        await interaction.response.send_message(embed=discord.Embed(title="⚔️ Arena ladder", description=text, colour=discord.Colour.red()), ephemeral=True)

    @g.command(name="status", description="Fights happening right now")
    async def status(interaction: discord.Interaction):
        rows = await host.db_fetch("SELECT * FROM ar_matches WHERE guild_id=? AND status IN ('pending','active')", (str(interaction.guild_id),))
        text = "\n".join(f"<@{r['challenger_id']}> vs <@{r['defender_id']}> — {r['status']}" + (f" in <#{r['thread_id']}>" if r["thread_id"] else "") for r in rows) or "No fights right now."
        await interaction.response.send_message(text, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())

    cg = app_commands.Group(name="arenaconfig", description="Arena settings (Manage Server)", guild_only=True)

    @cg.command(name="show", description="Show the arena settings")
    async def c_show(interaction: discord.Interaction):
        if not host.is_admin(interaction):
            return await interaction.response.send_message("You need **Manage Server** permission.", ephemeral=True)
        c = await cfg(str(interaction.guild_id))
        await interaction.response.send_message("\n".join(f"`{k}` = **{v}**" for k, v in c.items()), ephemeral=True)

    @cg.command(name="set", description="Change a setting")
    @app_commands.choices(key=[app_commands.Choice(name=k, value=k) for k in DEFAULTS])
    async def c_set(interaction: discord.Interaction, key: str, value: str):
        if not host.is_admin(interaction):
            return await interaction.response.send_message("You need **Manage Server** permission.", ephemeral=True)
        cur = DEFAULTS[key]
        try:
            new = value.strip().lower() in ("1", "true", "yes", "on") if isinstance(cur, bool) else int(float(value))
        except ValueError:
            return await interaction.response.send_message("That value doesn't fit this setting.", ephemeral=True)
        stored = await bs.kv_get("arena_cfg", str(interaction.guild_id), {}) or {}
        stored[key] = new
        await bs.kv_set("arena_cfg", str(interaction.guild_id), stored)
        await interaction.response.send_message(f"`{key}` = **{new}**", ephemeral=True)

    return [g, cg]


async def _svc_rating(guild, user, faction=""):
    return await rating(bs._id_of(guild), bs._id_of(user), faction or await _faction_of(bs._id_of(guild), bs._id_of(user)))


class ArenaCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._names: list[str] = []

    async def cog_load(self):
        await init_arena_tables()
        # a restart cannot resume live fights: close anything left active
        await host.db_exec("UPDATE ar_matches SET status='cancelled' WHERE status IN ('pending','active')", ())
        bs.register_service("arena.rating", _svc_rating)
        for c in build_commands(self.bot):
            self.bot.tree.add_command(c)
            self._names.append(c.name)
        self.watch.start()

    async def cog_unload(self):
        self.watch.cancel()
        bs.unregister_service("arena.rating")
        for n in self._names:
            self.bot.tree.remove_command(n)
        await host.close()

    @tasks.loop(seconds=5)
    async def watch(self):
        try:
            await tick(self.bot)
        except Exception:
            log.exception("arena watchdog failed")

    @watch.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None or str(message.channel.id) not in _thread_match:
            return
        await hit(self.bot, message)


async def setup(bot: commands.Bot):
    await bot.add_cog(ArenaCog(bot))
