"""Community modules loaded into a real commands.Bot (no Discord connection), incl. hot reload / unload.
Run from tests/:  python test_community.py"""
import asyncio, os, sys, tempfile

sys.path.insert(0, "..")
from botstudio.core import project
from botstudio.core.model import Graph

FAILS = []
def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond: FAILS.append(msg)

MODS = ("module.emotes", "module.birthdays", "module.post_router", "module.role_brackets",
        "module.game_profiles", "module.task_lists", "module.event_scheduler")

async def main():
    g = Graph()
    for t in MODS:
        g.add_node(t, 0, 0)
    res = project.build(g)
    check(res.ok, f"build ok {res.errors}")
    d = tempfile.mkdtemp()
    for rel, text in res.files.items():
        p = os.path.join(d, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(text)
    os.environ["DATA_DIR"] = os.path.join(d, "data")
    sys.path.insert(0, d)
    import discord
    from discord.ext import commands
    import bs_runtime as bs
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
    ctl = await bs.setup_control(bot)
    check(not ctl.errors, f"every community cog loads {ctl.errors}")
    ctl.watch.cancel()
    tops = sorted(c.name for c in bot.tree.get_commands())
    for want in ("emote", "emotes", "birthday", "anniversary", "birthday-config", "birthday-help", "router", "router-test", "bracket",
                 "game", "gameuid", "tasklist", "reminder", "addspawn", "eventcreate", "upcoming", "events-help", "listtargets"):
        check(want in tops, f"/{want} registered")
    check(len([c for c in bot.tree.get_commands(type=discord.AppCommandType.message)]) >= 2, "message context menus registered")
    check(set(bot.cogs) >= {"SetupCog", "RouterCog", "BracketCog", "GameCog", "UidCog", "TaskLists", "Remind", "Delivery"},
          f"all cogs present: {sorted(bot.cogs)}")
    # shared per-guild DB works for router + brackets + games
    import bsp_database
    db = await bot.db_manager.get(1234)
    gid = await db.add_game_profile("Aion2")
    check(bool(gid) and (await db.get_game_profile_by_name("aion2"))["name"] == "Aion2", "game profile created + case-insensitive lookup")
    await db.set_user_uid(5, gid, "UID-77")
    check(await db.get_user_uid(5, gid) == "UID-77", "per-game UID stored")
    # tasks DB + timeutil
    check(bot.db is not None, "task/reminder DB attached to the bot")
    import bsd_timeutil as tu
    check(tu.parse_interval("every 2 days") == 172800 and tu.parse_when("01:30", "UTC").kind == "timer", "time parsing works")
    # hot reload each module (teardown must remove support cogs, then reload cleanly)
    for path in list(ctl.loaded):
        ok = await ctl._load(path, "x", reload=True)
        check(ok, f"hot reload {path} {ctl.errors[-1:] if not ok else ''}")
    check(len([c for c in bot.cogs if c in ("RouterCog", "UidCog", "Remind", "Delivery")]) == 4, "support cogs re-added exactly once")
    # events: scheduler + collected commands
    import cogs
    ev = [m for n, m in sys.modules.items() if n.startswith("cogs.mod_event_scheduler")][0]
    check(ev.scheduler.running, "event scheduler running")
    check(ev.normalize_tz("EST") == "America/New_York", "timezone aliases")
    # birthdays: data helpers + leap day
    bd = [m for n, m in sys.modules.items() if n.startswith("cogs.mod_birthdays")][0]
    check(bd.is_leap_year(2024) and not bd.is_leap_year(1900), "leap-year helper")
    check("TOKEN" not in open(bd.__file__, encoding="utf-8").read(), "no token handling left in the birthdays module")
    # generated files must not contain a discord token pattern
    import re
    pat = re.compile(r"[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}")
    bad = [r for r, t in res.files.items() if pat.search(t)]
    check(not bad, f"no token-shaped strings in generated files {bad}")
    await bot.close()
    print("\nFAILED" if FAILS else "\nALL PASSED")
    return 1 if FAILS else 0

sys.exit(asyncio.run(main()))
