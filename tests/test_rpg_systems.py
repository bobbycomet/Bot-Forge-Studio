"""Factions & Phrase Scoring, Boss Fights and Arena — logic end to end without a Discord connection.
Run from tests/:  python test_rpg_systems.py"""
import asyncio, json, os, sys, tempfile
from datetime import timedelta

sys.path.insert(0, "..")
from botstudio.core import project
from botstudio.core.model import Graph

FAILS = []
def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond: FAILS.append(msg)

def mod(prefix):
    return [m for n, m in sys.modules.items() if n.startswith("cogs.mod_" + prefix)][0]

async def main():
    g = Graph()
    sc = g.add_node("module.scoring", 0, 0, {"side_names_list": "Order, Chaos"})
    for t in ("module.bosses", "module.arena", "module.gear"):
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
    check(not ctl.errors, f"cogs load {ctl.errors}")
    ctl.watch.cancel()
    names = sorted(c.name for c in bot.tree.get_commands())
    for want in ("faction", "trigger", "score", "scoreboard", "scoreconfig", "boss", "arena", "arenaconfig"):
        check(want in names, f"/{want} registered")
    for path in list(ctl.loaded):
        check(await ctl._load(path, "x", reload=True), f"hot reload {path}")
    for t in ("scoring", "bosses", "arena"):
        for c in bot.cogs.values():
            pass
    SC, BF, AR = mod("scoring"), mod("bosses"), mod("arena")
    G = "1"

    # ---------------------------------------------------------------- scoring
    facs = await SC.ensure_factions(G)
    check([f["name"] for f in facs] == ["Order", "Chaos"], f"starting factions come from the node property: {[f['name'] for f in facs]}")
    from bs_game import host
    for ph, w in (("good morning", 1), ("let's go", 2), ("hype", 3)):
        await host.db_exec("INSERT INTO sc_triggers (guild_id,faction,phrase,weight) VALUES (?,?,?,?)", (G, "order", ph, w))
    await host.db_exec("INSERT INTO sc_triggers (guild_id,faction,phrase,weight) VALUES (?,?,?,?)", (G, "chaos", "mayhem", 1))
    SC.bust_patterns(G)
    h = await SC.scan_text(G, "u1", "Good morning! LET'S GO let's go")
    check(h == {"order": 5.0}, f"weighted hits with repeats: {h}")
    check(await SC.scan_text(G, "u1", "good morning") == {}, "per-phrase cooldown blocks an immediate repeat")
    check(await SC.scan_text(G, "u2", "hype hype hype hype", cooldown=False) == {"order": 10.0}, "per-message cap (10) applies")
    check("mayhem" not in (await SC.scan_text(G, "u3", "hypemayhem", cooldown=False)) , "whole-word matching only")
    check(set(await SC.scan_text(G, "u3", "mayhem and hype", cooldown=False)) == {"order", "chaos"}, "several factions can score on one message")
    events = []
    async def listener(g_, u_, ev, amt): events.append((ev, amt))
    bs.on_event(listener)
    await SC.set_cfg(G, "daily_softcap", 10)
    c1 = await SC.add_score(G, "u9", "order", 8)
    c2 = await SC.add_score(G, "u9", "order", 6)           # 2 at full value + 4 at 20 %
    check(c1 == 8 and abs(c2 - (2 + 4 * 0.2)) < 1e-9, f"daily soft cap: {c1}, {c2}")
    check(("hits", 8) in events and any(e[0] == "hits:order" for e in events), "scoring emits bus events")
    check(("level_up", 3) in events or any(e[0] == "level_up" for e in events) or SC.level_for(10.8) == 0, "level maths consistent")
    sc9 = {s["key"]: s for s in await SC.member_scores(G, "u9")}
    check(abs(sc9["order"]["lifetime"] - 10.8) < 1e-9 and sc9["chaos"]["lifetime"] == 0, "member scores stored per faction")
    check((await SC.dominant_faction(G, "u9"))["key"] == "order", "dominant faction")
    check(SC.tier_name(0) == "Neutral" and SC.tier_name(80) == "Devoted" and SC.tier_name(100) == "Extreme", "alignment tiers")
    svc = await bs.service("score.get", G, "u9")
    check(isinstance(svc, list) and svc[0]["name"] == "Order", "score.get service")
    await bs.service("score.add", G, "u10", "Chaos", 4)
    check((await SC.member_scores(G, "u10"))[1]["lifetime"] == 4, "score.add service (by faction name)")

    # ---------------------------------------------------------------- bosses
    now = BF.now_utc()
    await BF.init_boss_tables()
    await host.db_exec("INSERT INTO bf_bosses (guild_id,boss_id,name,max_hp,hp,duration_mins,phases,attack_effect,attack_value,attack_interval,rewards) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (G, "king", "The King", 100, 100, 30, json.dumps([{"name": "Enraged", "threshold": 0.5, "multiplier": 2, "attack_interval_secs": 60}]),
                        "score_penalty", 3, 1, json.dumps({"scope": "topN", "N": 1, "grants": [{"type": "currency", "value": "250"}]})))
    await host.db_exec("INSERT INTO bf_bosses (guild_id,boss_id,name,max_hp,hp,duration_mins) VALUES (?,?,?,?,?,?)", (G, "next", "Second", 50, 50, 10))
    ok, _m = await BF.start_boss(bot, G, "king")
    check(ok and (await BF.active_boss(G))["boss_id"] == "king", "boss starts")
    check(not (await BF.start_boss(bot, G, "next"))[0], "only one boss at a time")
    hp = (await BF.deal_damage(bot, G, "u9", 30))["hp"]
    check(hp == 70, f"damage reduces HP: {hp}")
    b = await BF.deal_damage(bot, G, "u10", 25)           # 45/100 -> below 50 % -> phase 2 (x2 multiplier applies after)
    check(b["phase"] == 2 and b["dmg_mult"] == 2.0 and b["attack_interval"] == 60, f"phase transition: phase {b['phase']} x{b['dmg_mult']}")
    b = await BF.deal_damage(bot, G, "u10", 5)            # 5 * 2.0 = 10
    check(abs(b["hp"] - 35) < 1e-6, f"phase multiplier applies: {b['hp']}")
    # boss attack: score_penalty hits participants' current score
    before = (await SC.member_scores(G, "u9"))[0]["current"]
    await BF._attack(bot, await BF.active_boss(G))
    after = (await SC.member_scores(G, "u9"))[0]["current"]
    check(abs((before - after) - 3) < 1e-6, f"score_penalty reduces current score by 3 ({before:.1f}->{after:.1f})")
    # silence blocks damage
    await host.db_exec("UPDATE bf_bosses SET attack_effect='silence', attack_value=30 WHERE guild_id=? AND boss_id='king'", (G,))
    await BF._attack(bot, await BF.active_boss(G))
    check(await BF.deal_damage(bot, G, "u9", 5) is None, "silence ignores damage")
    await host.db_exec("UPDATE bf_bosses SET silenced_until=NULL WHERE guild_id=? AND boss_id='king'", (G,))
    # damage via the scoring event bus
    live = (await BF.active_boss(G))["hp"]
    await SC.add_score(G, "u11", "chaos", 3, softcap=False)
    check((await BF.active_boss(G))["hp"] < live, "scoring hits become boss damage through the event bus")
    # knockout -> rewards -> queue
    await host.db_exec("INSERT INTO bf_queue (guild_id,boss_id,queued_by,queued_at) VALUES (?,?,?,?)", (G, "next", "1", now.isoformat()))
    bal0 = await host.balance(G, "u10")
    await BF.deal_damage(bot, G, "u10", 500)
    check((await BF.get_boss(G, "king"))["status"] == "won", "boss defeated")
    check(await host.balance(G, "u10") == bal0 + 250, "top-N reward paid in the shared wallet")
    check((await BF.active_boss(G))["boss_id"] == "next", "queued boss starts when the first ends")
    await host.db_exec("UPDATE bf_bosses SET ends_at=? WHERE guild_id=? AND boss_id='next'", ((now - timedelta(minutes=1)).isoformat(), G))
    await BF.tick(bot)
    check((await BF.get_boss(G, "next"))["status"] == "lost", "time-out ends the boss as lost")

    # ---------------------------------------------------------------- arena
    check(AR.elo_k(1000) == 40 and AR.elo_k(1200) == 32 and AR.elo_k(1500) == 16, "dynamic K-factor")
    check(abs(AR.elo_change(1000, 1000) - 20) < 1e-9 and AR.elo_change(1000, 1400) > AR.elo_change(1400, 1000), "Elo favours the underdog")
    await AR.init_arena_tables()
    await bs.eco_add("wallet", G, "a1", 1000); await bs.eco_add("wallet", G, "a2", 1000)
    await host.db_exec("INSERT INTO ar_matches (match_id,guild_id,challenger_id,defender_id,faction_c,faction_d,wager,status,c_hp,d_hp,c_max,d_max,started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("m1", G, "a1", "a2", "Order", "Chaos", 100, "active", 80, 0, 100, 100, AR.now_utc().isoformat()))
    AR._state["m1"] = {"c_hp": 80.0, "d_hp": 0.0, "c_max": 100.0, "d_max": 100.0, "ends": AR.now_utc()}
    await AR.resolve(bot, "m1", knockout=True)
    ra, rb = await AR.rating(G, "a1", "Order"), await AR.rating(G, "a2", "Chaos")
    check(ra["rating"] > 1000 > rb["rating"] and ra["wins"] == 1 and rb["losses"] == 1 and ra["ko_count"] == 1, f"ratings updated: {ra['rating']:.0f}/{rb['rating']:.0f}")
    check(await host.balance(G, "a1") == 1100 and await host.balance(G, "a2") == 900, "wager moves between wallets")
    check(await AR._daily_won(G, "a1") == 100, "daily winnings tracked")
    await AR.resolve(bot, "m1")                              # resolving twice must do nothing
    check((await AR.rating(G, "a1", "Order"))["wins"] == 1, "a match resolves only once")
    await bs.set_cfg if False else None
    await bs.kv_set("arena_cfg", G, {"gold_daily_cap": 120})
    await host.db_exec("INSERT INTO ar_matches (match_id,guild_id,challenger_id,defender_id,faction_c,faction_d,wager,status,c_hp,d_hp,c_max,d_max,started_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       ("m2", G, "a1", "a2", "Order", "Chaos", 100, "active", 50, 10, 100, 100, AR.now_utc().isoformat()))
    AR._state["m2"] = {"c_hp": 50.0, "d_hp": 10.0, "c_max": 100.0, "d_max": 100.0, "ends": AR.now_utc()}
    await AR.resolve(bot, "m2")
    check(await AR._daily_won(G, "a1") <= 120, f"daily cap limits winnings: {await AR._daily_won(G, 'a1')}")
    check((await AR.rating(G, "a1", "Order"))["win_streak"] == 2, "win streak")
    check((await bs.service("arena.rating", G, "a1", "Order"))["wins"] == 2, "arena.rating service")
    bs.off_event(listener)
    await bot.close()
    print("\nFAILED" if FAILS else "\nALL PASSED")
    return 1 if FAILS else 0

sys.exit(asyncio.run(main()))
