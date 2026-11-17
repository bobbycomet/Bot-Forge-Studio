"""Game modules + Interact/Economy nodes, end to end without a Discord connection.
Run from tests/:  python test_game.py"""
import asyncio, os, sys, tempfile, importlib

sys.path.insert(0, "..")
from botstudio.core import project
from botstudio.core.model import Graph

FAILS = []
def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond: FAILS.append(msg)

def write(res, d):
    for rel, text in res.files.items():
        p = os.path.join(d, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(text)

async def main():
    g = Graph()
    for t in ("module.quests", "module.gear", "module.dungeons", "module.cards"):
        g.add_node(t, 0, 0)
    # a flow using the connecting nodes + Interact + Economy nodes
    t = g.add_node("trigger.slash", 0, 300, {"name": "buy"})
    c = g.add_node("ui.confirm", 200, 300, {"content": "Buy the iron sword for 50?"})
    sp = g.add_node("eco.spend", 420, 300, {"amount": 50})
    gg = g.add_node("game.give_gear", 640, 300, {"template": "iron_sword"})
    tr = g.add_node("game.track", 860, 300, {"event": "item_crafted"})
    py = g.add_node("action.python", 1080, 300, {"code": "result = f'{user.display_name} bought {give_gear_instance}'", "outputs": "result"})
    g.connect(t.id, "next", c.id, "in"); g.connect(c.id, "yes", sp.id, "in"); g.connect(sp.id, "paid", gg.id, "in")
    g.connect(gg.id, "next", tr.id, "in"); g.connect(tr.id, "next", py.id, "in")
    res = project.build(g)
    check(res.ok, f"project with game modules builds {res.errors}")
    d = tempfile.mkdtemp(); write(res, d)
    os.environ["DATA_DIR"] = os.path.join(d, "data")
    sys.path.insert(0, d)
    for m in [m for m in sys.modules if m in ("bs_runtime", "bs_game", "bsg_gear", "bsg_fonts") or m.startswith("cogs")]:
        del sys.modules[m]
    import discord
    from discord.ext import commands
    import bs_runtime as bs
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.all())
    ctl = await bs.setup_control(bot)
    check(not ctl.errors, f"every generated cog loads {ctl.errors}")
    names = sorted(c.name for c in bot.tree.get_commands())
    for want in ("quests", "questsadmin", "gear", "gearadmin", "dungeon", "dungeonadmin", "profile", "rankcard", "buy"):
        check(want in names, f"/{want} registered")
    ctl.watch.cancel()

    import bs_game, bsg_gear
    # --- wallet is shared with the Economy nodes
    await bs.eco_add("wallet", 1, 42, 500)
    check(await bs_game.host.balance(1, 42) == 500, "game wallet == Economy wallet")
    # --- quests: event bus -> progress -> claim -> reward in the shared wallet
    qm = [m for n, m in sys.modules.items() if n.startswith("cogs.mod_quests")][0]
    await qm.init_quest_tables()
    todays = await qm.get_today_quests("1", "42")
    check(len(todays) == 3, "default daily quests seeded")
    await bs.track(1, 42, "messages", 20)
    todays = {q["quest_id"]: q for q in await qm.get_today_quests("1", "42")}
    check(todays["daily_messages"]["complete"], "events from bs.track complete a quest")
    ok, msg, reward = await qm.claim_quest("1", "42", "daily_messages")
    check(ok and reward == 40 and await bs_game.host.balance(1, 42) == 540, "claim pays into the shared wallet")
    check(not (await qm.claim_quest("1", "42", "daily_messages"))[0], "a quest cannot be claimed twice")
    # currency earned flows back in as an event (quest on currency_earned)
    await bs_game.host.add_gold(1, 42, 150)
    todays = {q["quest_id"]: q for q in await qm.get_today_quests("1", "42")}
    check(todays["daily_earn"]["complete"], "currency earned counts toward quests")
    # --- gear: template -> grant -> equip -> stats; rarity multiplier
    gm = [m for n, m in sys.modules.items() if n.startswith("cogs.mod_gear")][0]
    await gm.init_gear_tables()
    await bs_game.host.db_exec("INSERT INTO guild_gear (gear_id, guild_id, display_name, gear_type, atk_bonus, hp_bonus) VALUES (?,?,?,?,?,?)",
                               ("iron_sword", "1", "Iron Sword", "weapon", 10, 0))
    inst = await bs.service("gear.grant", 1, 42, "iron_sword", "rare")
    check(bool(inst), "gear.grant service creates an instance")
    ok, _m = await gm.equip_gear("1", "42", inst)
    st = await bs.service("gear.stats", 1, 42)
    check(ok and abs(st["atk"] - (5 + 10 * 1.8)) < 1e-6, f"rare ×1.8 attack applied: {st}")
    # --- dungeons: tables + a definition + team lookup is optional
    dm = [m for n, m in sys.modules.items() if n.startswith("cogs.mod_dungeons")][0]
    await dm.gear.init_gear_tables(); await dm.init_dungeon_tables()
    check(await dm._get_alignment("1", "42") is None, "teams are optional (no team -> open parties)")
    await bs.kv_set("team", bs.eco_key(1, 42), "Red")
    check(await dm._get_alignment("1", "42") == "Red", "Set Team value is what parties read")
    # --- generated flow compiles and uses the right helpers
    flow = next(c.source for c in res.cogs if "buy" in c.source)
    for needle in ("bs.ask_buttons", "bs.eco_spend", "bs.service('gear.grant'", "bs.track(", "give_gear_instance = v_"):
        check(needle in flow, f"flow uses {needle}")
    await bot.close()
    print("\nFAILED" if FAILS else "\nALL PASSED")
    return 1 if FAILS else 0

sys.exit(asyncio.run(main()))
