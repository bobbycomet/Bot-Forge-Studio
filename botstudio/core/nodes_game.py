"""Game systems: ready-made modules (Quests, Gear & Rarity, Dungeons & Raids, Profile & Rank Cards) and the small
nodes that connect your own flows to them (Track Progress, Give Gear, Combat Stats, Teams).

Modules are generic — nothing in them is tied to one bot.  They share one wallet (the same one the Economy nodes
use), one event bus (``bs.track``) and a service registry, so any combination works: an economy bot needs only the
Economy nodes, a quest bot adds Quests, an RPG adds Gear + Dungeons + Cards.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path

from .codegen import GenError
from .model import NodeSpec, Prop, register
from .modules import fill
from .nodes import D, EX

GAME_DIR = Path(__file__).resolve().parent.parent / "runtime" / "game"
FONT_DIR = GAME_DIR / "fonts"


def _read(name: str) -> str:
    return (GAME_DIR / name).read_text(encoding="utf-8")


def _fonts_module() -> str:
    def b64(n):
        return repr(base64.b64encode((FONT_DIR / n).read_bytes()).decode("ascii"))
    return _read("bsg_fonts_tpl.py").replace("%%OUTFIT%%", b64("Outfit.ttf")).replace("%%WORKSANS%%", b64("WorkSans.ttf"))


def _defaults(text: str) -> str:
    """Fill every placeholder a support file still has with its neutral default (it is only imported, never configured)."""
    return re.sub(r"%%(\w+)%%", lambda m: {"wallet": "'wallet'", "currency_name": "'coins'", "currency_emoji": "'🪙'",
                                            "gear_command": "'gear'", "admin_command": "'gearadmin'"}.get(m.group(1), "None"), text)


CURRENCY_PROPS = [
    Prop("wallet", "Wallet / currency table", "str", "wallet",
         help="Same name as the Economy nodes' 'Currency / wallet name' — rewards land in that wallet."),
    Prop("currency_name", "Currency name", "str", "coins"),
    Prop("currency_emoji", "Currency emoji", "str", "🪙"),
]


def _support(node, spec, extra=()):
    out = {"bs_game.py": _read("bs_game.py")}
    for name in extra:
        out[name] = _defaults(_read(name)) if name != "bsg_fonts.py" else _fonts_module()
    return out


# -------------------------------------------------------------------------------------------------- Quests

def _quests_files(node, slug):
    out = _support(node, node.spec)
    out[f"cogs/{slug}.py"] = fill(_read("bsg_quests.py"), node, node.spec)
    return out


register(NodeSpec(
    "module.quests", "Quests", "Game", "module",
    props=[Prop("board_command", "Player command", "str", "quests"), Prop("admin_command", "Admin command", "str", "questsadmin")] + CURRENCY_PROPS,
    files=_quests_files, commands=lambda p: [p.get("board_command", "quests"), p.get("admin_command", "questsadmin")],
    requires=("aiosqlite>=0.20",),
    description="Daily quests with progress bars and claim buttons. Quests count events (messages, currency earned, matches, dungeon clears, "
                "or your own via Track Progress). Admins edit quests live with the admin command."))


# -------------------------------------------------------------------------------------------------- Gear

def _gear_files(node, slug):
    out = _support(node, node.spec)
    out[f"cogs/{slug}.py"] = fill(_read("bsg_gear.py"), node, node.spec)
    return out


register(NodeSpec(
    "module.gear", "Gear & Rarity", "Game", "module",
    props=[Prop("gear_command", "Player command", "str", "gear"), Prop("admin_command", "Admin command", "str", "gearadmin")] + CURRENCY_PROPS,
    files=_gear_files, commands=lambda p: [p.get("gear_command", "gear"), p.get("admin_command", "gearadmin")],
    requires=("aiosqlite>=0.20",),
    description="Weapons, armor and accessories with six rarity tiers, enchants and evolving. Admins build the catalog; "
                "Give Gear hands items out from any flow."))


# -------------------------------------------------------------------------------------------------- Dungeons

def _dungeon_files(node, slug):
    out = _support(node, node.spec, ("bsg_gear.py",))
    out[f"cogs/{slug}.py"] = fill(_read("bsg_dungeons.py"), node, node.spec)
    return out


register(NodeSpec(
    "module.dungeons", "Dungeons & Raids", "Game", "module",
    props=[Prop("dungeon_command", "Player command", "str", "dungeon"), Prop("admin_command", "Admin command", "str", "dungeonadmin"),
           Prop("attack_words_list", "Attack words (blank = every message attacks)", "text", "",
                help="Comma or line separated. Messages in a run thread that contain one of these deal damage."),
           Prop("attack_cooldown", "Seconds between a player's attacks", "float", 1.5)] + CURRENCY_PROPS,
    files=_dungeon_files, commands=lambda p: [p.get("dungeon_command", "dungeon"), p.get("admin_command", "dungeonadmin")],
    intents=("message_content",), requires=("aiosqlite>=0.20",),
    description="Solo, team and AFK dungeon runs in threads with monsters, loot tables and block / spell defence. "
                "Damage comes from the player's gear (Gear & Rarity). Content is edited live in the admin command."))


# -------------------------------------------------------------------------------------------------- Cards

def _card_files(node, slug):
    out = _support(node, node.spec, ("bsg_fonts.py",))
    out[f"cogs/{slug}.py"] = fill(_read("bsg_cards.py"), node, node.spec)
    return out


register(NodeSpec(
    "module.cards", "Profile & Rank Cards", "Game", "module",
    props=[Prop("profile_command", "Profile command", "str", "profile"), Prop("rank_command", "Rank command", "str", "rankcard"),
           Prop("xp_table", "XP table (from XP & Levels)", "str", "xp"),
           Prop("accent", "Accent colour (hex)", "str", "7C3AED")] + CURRENCY_PROPS,
    files=_card_files, commands=lambda p: [p.get("profile_command", "profile"), p.get("rank_command", "rankcard")],
    requires=("aiosqlite>=0.20", "Pillow>=10.1"),
    description="Image cards for /profile and /rank: avatar, level, currency, team, gear stats and a progress bar. "
                "Reads levels from the XP & Levels module."))


# -------------------------------------------------------------------------------------------------- Scoring / Bosses / Arena

def _scoring_files(node, slug):
    out = _support(node, node.spec)
    out[f"cogs/{slug}.py"] = fill(_read("bsg_scoring.py"), node, node.spec)
    return out


register(NodeSpec(
    "module.scoring", "Factions & Phrase Scoring", "Game", "module",
    props=[Prop("side_names_list", "Starting factions (names, comma separated)", "text", "Blue Team, Red Team",
                help="Two are created for every server the first time; add or rename any time with /faction.")],
    files=_scoring_files, commands=lambda p: ["faction", "trigger", "score", "scoreboard", "scoreconfig"],
    intents=("message_content",), requires=("aiosqlite>=0.20",),
    description="Teams that earn points when members use their trigger phrases: weighted phrases per faction, per-message cap, phrase "
                "cooldown, daily soft cap, streaks, decay, levels and daily/weekly/monthly boards. Joining a faction sets the member's team."))


def _boss_files(node, slug):
    out = _support(node, node.spec)
    out[f"cogs/{slug}.py"] = fill(_read("bsg_bosses.py"), node, node.spec)
    return out


register(NodeSpec(
    "module.bosses", "Boss Fights", "Game", "module",
    props=[Prop("message_damage", "Damage per chat message (0 = only scoring hits / nodes)", "float", 0)] + CURRENCY_PROPS,
    files=_boss_files, commands=lambda p: ["boss"],
    requires=("aiosqlite>=0.20",),
    description="Timed community bosses with HP, phases, periodic attacks (score penalty, silence, damage drop), a live damage feed, "
                "queue / recurring spawns and rewards (currency, role, message). Damage comes from scoring hits, chat, or the Deal Boss Damage node."))


def _arena_files(node, slug):
    out = _support(node, node.spec)
    out[f"cogs/{slug}.py"] = fill(_read("bsg_arena.py"), node, node.spec)
    return out


register(NodeSpec(
    "module.arena", "Arena (PvP)", "Game", "module",
    props=CURRENCY_PROPS,
    files=_arena_files, commands=lambda p: ["arena", "arenaconfig"],
    intents=("message_content",), requires=("aiosqlite>=0.20",),
    description="1v1 duels in a thread: challenge with Accept/Decline, optional wager, HP from gear, damage from typing (scoring hits "
                "when Factions & Phrase Scoring is installed), knockouts, Elo per faction, streaks, daily winnings cap and a ladder."))


# ================================================================================ connecting nodes

def _guild(ctx) -> str:
    return ctx.scope.get("guild", "None")


def _user(ctx, key="user") -> str:
    u = ctx.val(key, ctx.default_user() or "None")
    if u == "None":
        raise GenError(f"{ctx.node.spec.title} [{ctx.node.id}]: no member — wire one into '{key}' or use it under a Slash Command / On Message trigger")
    return u


def gen_track(ctx):
    ev = str(ctx.prop("event") or "").strip()
    if not ev:
        raise GenError(f"Track Progress [{ctx.node.id}]: give the event a name")
    ctx.emit(f"await bs.track({_guild(ctx)}, {_user(ctx)}, {ev!r}, {ctx.val('amount')})")


register(NodeSpec("game.track", "Track Progress", "Game", "action",
                  inputs=[EX("in"), D("user", "user"), D("amount", "int")], outputs=[EX()],
                  props=[Prop("event", "Event name", "str", "match_won",
                              help="Quests that watch this event move forward. Built-in names: messages, currency_earned, match_played, "
                                   "match_won, boss_damage, dungeon_clear, item_crafted, daily_login — or invent your own."),
                         Prop("amount", "Amount", "float", 1, port=True)],
                  gen=gen_track, description="Reports that something happened for a member so Quests can count it."))


def gen_give_gear(ctx):
    ctx.emit(f"{ctx.var('instance')} = await bs.service('gear.grant', {_guild(ctx)}, {_user(ctx)}, {ctx.val('template')}, {ctx.val('rarity')})")


register(NodeSpec("game.give_gear", "Give Gear", "Game", "action",
                  inputs=[EX("in"), D("user", "user"), D("template", "str"), D("rarity", "str")],
                  outputs=[EX(), D("instance", "str")],
                  props=[Prop("template", "Gear ID", "str", "iron_sword", port=True, help="An id from the Gear & Rarity catalog."),
                         Prop("rarity", "Rarity", "choice", "normal", choices=("normal", "basic", "rare", "legendary", "mythic", "god"), port=True)],
                  gen=gen_give_gear, requires=(),
                  description="Gives a member a copy of a gear template (shop purchase, quest reward, event prize). Needs the Gear & Rarity module."))


def gen_stats(ctx):
    v = ctx.var
    ctx.emit(f"_stats = await bs.service('gear.stats', {_guild(ctx)}, {_user(ctx)}) or {{}}")
    ctx.emit(f"{v('attack')} = _stats.get('atk', 0)")
    ctx.emit(f"{v('health')} = _stats.get('hp', 0)")
    ctx.emit(f"{v('block')} = _stats.get('block', 0)")


register(NodeSpec("game.stats", "Combat Stats", "Game", "action",
                  inputs=[EX("in"), D("user", "user")], outputs=[EX(), D("attack", "int"), D("health", "int"), D("block", "int")],
                  gen=gen_stats, description="A member's total attack / health / block from equipped gear. Needs the Gear & Rarity module."))


def gen_set_team(ctx):
    ctx.emit(f"await bs.kv_set('team', bs.eco_key({_guild(ctx)}, {_user(ctx)}), {ctx.val('team')} or None)")


register(NodeSpec("game.set_team", "Set Team", "Game", "action",
                  inputs=[EX("in"), D("user", "user"), D("team", "str")], outputs=[EX()],
                  props=[Prop("team", "Team name (blank = leave team)", "str", "Red", fmt=True, port=True)],
                  gen=gen_set_team, description="Puts a member on a team / faction. Dungeon parties and cards read it."))


def gen_get_team(ctx):
    ctx.emit(f"{ctx.var('team')} = await bs.kv_get('team', bs.eco_key({_guild(ctx)}, {_user(ctx)}), '') or ''")


register(NodeSpec("game.get_team", "Get Team", "Game", "action",
                  inputs=[EX("in"), D("user", "user")], outputs=[EX(), D("team", "str")],
                  gen=gen_get_team, description="The member's team name, or empty if they have none."))


def gen_team_count(ctx):
    ctx.emit(f"{ctx.var('count')} = len([1 for k, v in await bs.kv_top_any('team', 5000) if k.startswith(str(getattr({_guild(ctx)}, 'id', {_guild(ctx)})) + ':') and v == {ctx.val('team')}])")


register(NodeSpec("game.team_size", "Team Size", "Game", "action",
                  inputs=[EX("in"), D("team", "str")], outputs=[EX(), D("count", "int")],
                  props=[Prop("team", "Team", "str", "Red", port=True)], gen=gen_team_count,
                  description="How many members of this server are on a team."))


def gen_score_add(ctx):
    ctx.emit(f"{ctx.var('credited')} = await bs.service('score.add', {_guild(ctx)}, {_user(ctx)}, {ctx.val('faction')}, {ctx.val('hits')}) or 0")


register(NodeSpec("game.score_add", "Add Faction Score", "Game", "action",
                  inputs=[EX("in"), D("user", "user"), D("faction", "str"), D("hits", "int")], outputs=[EX(), D("credited", "int")],
                  props=[Prop("faction", "Faction", "str", "Blue Team", port=True), Prop("hits", "Points", "float", 1, port=True)],
                  gen=gen_score_add, description="Gives a member points for a faction (counts toward levels, boards and boss damage). Needs Factions & Phrase Scoring."))


def gen_score_get(ctx):
    v = ctx.var
    ctx.emit(f"_scores = await bs.service('score.get', {_guild(ctx)}, {_user(ctx)}) or []")
    ctx.emit("_top = max(_scores, key=lambda s: s['lifetime'], default=None) or {}")
    ctx.emit(f"{v('faction')} = _top.get('name', '') if _top.get('lifetime') else ''")
    ctx.emit(f"{v('level')} = _top.get('level', 0)")
    ctx.emit(f"{v('lifetime')} = _top.get('lifetime', 0)")
    ctx.emit(f"{v('current')} = _top.get('current', 0)")


register(NodeSpec("game.score_get", "Get Faction Score", "Game", "action",
                  inputs=[EX("in"), D("user", "user")],
                  outputs=[EX(), D("faction", "str"), D("level", "int"), D("lifetime", "int"), D("current", "int")],
                  gen=gen_score_get, description="The faction a member scores most for, with their level and scores there."))


def gen_boss_start(ctx):
    ctx.emit(f"{ctx.var('ok')} = await bs.service('boss.start', {_guild(ctx)}, {ctx.val('boss')}, {ctx.scope.get('channel', 'None')})")


register(NodeSpec("game.boss_start", "Start Boss", "Game", "action",
                  inputs=[EX("in"), D("boss", "str")], outputs=[EX(), D("ok", "bool")],
                  props=[Prop("boss", "Boss id", "str", "boss", port=True, help="The id shown by /boss list.")],
                  gen=gen_boss_start, description="Starts a boss from any flow (a schedule, a milestone, a button). Needs Boss Fights."))


def gen_boss_damage(ctx):
    ctx.emit(f"{ctx.var('hp')} = await bs.service('boss.damage', {_guild(ctx)}, {_user(ctx)}, {ctx.val('amount')})")


register(NodeSpec("game.boss_damage", "Deal Boss Damage", "Game", "action",
                  inputs=[EX("in"), D("user", "user"), D("amount", "int")], outputs=[EX(), D("hp", "int")],
                  props=[Prop("amount", "Damage", "float", 10, port=True)],
                  gen=gen_boss_damage, description="Hits the active boss for a member; 'hp' is what is left (empty if no boss is active)."))


def gen_boss_active(ctx):
    v = ctx.var
    ctx.emit(f"_boss = await bs.service('boss.status', {_guild(ctx)})")
    ctx.emit(f"{v('name')} = (_boss or {{}}).get('name', '')")
    ctx.emit(f"{v('hp')} = (_boss or {{}}).get('hp', 0)")
    ctx.emit("if _boss:")
    with ctx.block("active"):
        pass
    ctx.emit("else:")
    with ctx.block("idle"):
        pass


register(NodeSpec("game.boss_active", "Boss Active?", "Game", "flow",
                  inputs=[EX("in")], outputs=[EX("active"), EX("idle"), D("name", "str"), D("hp", "int")],
                  gen=gen_boss_active, description="Branches on whether a boss fight is running right now."))


def gen_arena_rating(ctx):
    v = ctx.var
    ctx.emit(f"_r = await bs.service('arena.rating', {_guild(ctx)}, {_user(ctx)}) or {{}}")
    ctx.emit(f"{v('rating')} = _r.get('rating', 1000)")
    ctx.emit(f"{v('wins')} = _r.get('wins', 0)")
    ctx.emit(f"{v('losses')} = _r.get('losses', 0)")


register(NodeSpec("game.arena_rating", "Get Arena Rating", "Game", "action",
                  inputs=[EX("in"), D("user", "user")], outputs=[EX(), D("rating", "int"), D("wins", "int"), D("losses", "int")],
                  gen=gen_arena_rating, description="A member's arena Elo rating and record. Needs Arena (PvP)."))


def _weighted_pairs(text) -> list:
    out = []
    for part in str(text or "").split("|"):
        label, _, w = part.strip().rpartition(":") if ":" in part else (part.strip(), "", "1")
        if not label.strip():
            continue
        try:
            out.append((label.strip(), max(0.0, float(w or 1))))
        except ValueError:
            out.append((part.strip(), 1.0))
    return out or [("", 1.0)]


register(NodeSpec("data.weighted_choice", "Weighted Choice", "Data", "data",
                  outputs=[D("value", "str")],
                  props=[Prop("items", "Items (name:weight, separated by |)", "str", "Common:70|Rare:25|Legendary:5",
                              help="Each weight is relative: 70 / 25 / 5 means a 70 % / 25 % / 5 % chance. Weights need not add up to 100.")],
                  gen=lambda ctx, port: f"bs.weighted_choice({_weighted_pairs(ctx.prop('items'))!r})", cache=True,
                  description="Picks one item with the odds you give it — the heart of a gacha pull or a loot table."))
