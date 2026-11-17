"""Ready-to-run example bots for the Bot Wizard: six simple, six advanced.

Each entry in EXAMPLE_INFO describes one example (difficulty, architecture, features by level, extra setup) and names the function that
builds its graph.  Every example is a complete project: properties are pre-filled, advanced ones are split into labelled *groups* (one per
subsystem) instead of one tangle, and nothing depends on code outside Bot Forge Studio.
"""
from __future__ import annotations

from .model import Graph

SETUP_COMMON = """### Discord credentials (the same for every example)
1. **Developer Portal → New Application → Bot → Reset Token.** Copy the token once; it goes into the *Deploy* prompt (stored only in the bot's `.env`, never in the project).
2. **Privileged intents:** switch on the ones listed under *Intents & permissions* below (Bot tab → Privileged Gateway Intents).
3. **OAuth2 → URL Generator:** scopes `bot` + `applications.commands`, tick the permissions listed below, open the URL and invite the bot to a **test server**.
4. In *Project → Settings* set a **Dev server ID** so commands appear instantly on your test server.
5. **Build (F5) → Deploy (F6) → Start.** The log should say *Logged in as …*. Keep the token private — if it leaks, **Reset Token** at once.

*(The guided lesson “Build Your First Discord Bot” walks through every one of these steps with screenshots-free, click-by-click help.)*"""


def _n(g: Graph, type_id: str, x: float, y: float, **props):
    return g.add_node(type_id, x, y, props)


def _w(g: Graph, a, ap: str, b, bp: str):
    e = g.connect(a.id, ap, b.id, bp)
    assert e is not None, (a.type_id, ap, b.type_id, bp)
    return e


def _chain(g: Graph, *nodes):
    for a, b in zip(nodes, nodes[1:]):
        _w(g, a, "next", b, "in")


def _group(g: Graph, title: str, nodes: list):
    g.groups.append({"id": f"grp_{len(g.groups) + 1:02d}", "title": title, "node_ids": [n.id for n in nodes], "collapsed": False})


def _slash(g, x, y, name, desc, params="", perm="none", **kw):
    return _n(g, "trigger.slash", x, y, name=name, description=desc, params=params, require_permission=perm, **kw)


def _reply(g, x, y, content="", **kw):
    return _n(g, "action.reply", x, y, content=content, **kw)


# ============================================================================================ simple bots
def welcome_bot() -> Graph:
    g = Graph()
    g.settings.name = "Welcome Bot"
    g.settings.status_text = "👋 welcoming newcomers"
    join = _n(g, "trigger.member_join", 40, 40)
    env = _n(g, "data.env", 40, 260, name="WELCOME_CHANNEL_ID")
    send = _n(g, "action.send", 360, 40, embed_title="Welcome to {guild.name}!",
              embed_description="Glad you're here, {user.mention} 🎉\nRead the rules with **/rules**, then say hi!", embed_color="5865F2")
    role = _n(g, "action.add_role", 700, 40, role="Newcomer")
    dm = _n(g, "action.dm", 1000, 40, content="Welcome to **{guild.name}**! Type /rules in the server for a quick guide.")
    _chain(g, join, send, role, dm)
    _w(g, env, "value", send, "channel")
    _group(g, "When someone joins", [join, env, send, role, dm])
    t = _slash(g, 40, 480, "rules", "Read the server rules")
    tabs = _n(g, "ui.tabs", 360, 480, tabs="## Be kind\nNo harassment, hate speech or personal attacks.\n## Keep it tidy\nUse the right channel for each topic; no spam.\n## Need help?\nAsk a moderator — we're friendly.")
    _w(g, t, "next", tabs, "in")
    _group(g, "/rules guide", [t, tabs])
    return g


def moderation_bot() -> Graph:
    g = Graph()
    g.settings.name = "Moderation Bot"
    g.settings.status_text = "🛡️ keeping things tidy"
    # /warn with automatic escalation: three warnings = one hour timeout
    t = _slash(g, 40, 40, "warn", "Warn a member", "target:user, reason:str", "moderate_members")
    w = _n(g, "mod.warn", 360, 40, namespace="warnings", reason="{reason}")
    cmp = _n(g, "data.compare", 360, 260, op=">=", b="3")
    br = _n(g, "flow.branch", 660, 40)
    to = _n(g, "mod.timeout", 960, 0, minutes=60, reason="3 warnings")
    ok = _reply(g, 960, 220, "⚠️ Warned {target.mention} for: {reason}")
    esc = _reply(g, 1260, 0, "🔇 {target.mention} reached 3 warnings and is timed out for an hour.")
    _w(g, t, "next", w, "in"); _w(g, t, "target", w, "user"); _w(g, w, "next", br, "in")
    _w(g, w, "count", cmp, "a"); _w(g, cmp, "result", br, "condition")
    _w(g, br, "true", to, "in"); _w(g, t, "target", to, "user"); _w(g, to, "next", esc, "in"); _w(g, br, "false", ok, "in")
    _group(g, "/warn (auto-escalates)", [t, w, cmp, br, to, ok, esc])
    nodes = []
    for i, (name, desc, params, perm, typ, props, msg) in enumerate((
        ("timeout", "Time a member out", "target:user, minutes:int=10, reason:str?", "moderate_members", "mod.timeout", {"reason": "{reason}"}, "🔇 {target.mention} timed out for {minutes} minutes."),
        ("kick", "Kick a member", "target:user, reason:str?", "kick_members", "mod.kick", {"reason": "{reason}"}, "👢 {target.mention} was kicked."),
        ("ban", "Ban a member", "target:user, reason:str?", "ban_members", "mod.ban", {"reason": "{reason}"}, "🔨 {target.mention} was banned."),
    )):
        y = 520 + i * 220
        tt = _slash(g, 40, y, name, desc, params, perm)
        m = _n(g, typ, 360, y, **props)
        r = _reply(g, 700, y, msg, ephemeral=True)
        _w(g, tt, "next", m, "in"); _w(g, tt, "target", m, "user"); _w(g, m, "next", r, "in")
        if name == "timeout":
            _w(g, tt, "minutes", m, "minutes")
        nodes += [tt, m, r]
    _group(g, "Timeout / kick / ban", nodes)
    tp = _slash(g, 40, 1220, "purge", "Delete recent messages", "limit:int=10", "manage_messages")
    pg = _n(g, "mod.purge", 360, 1220, limit=10)
    pr = _reply(g, 700, 1220, "🧹 Deleted {deleted} messages.", ephemeral=True)
    pf = _n(g, "data.format", 360, 1420, template="🧹 Deleted {deleted} messages.")
    _w(g, tp, "next", pg, "in"); _w(g, tp, "limit", pg, "limit"); _w(g, pg, "next", pr, "in")
    _w(g, pg, "deleted", pf, "deleted"); _w(g, pf, "text", pr, "content")
    ts = _slash(g, 40, 1620, "slowmode", "Set slowmode in this channel", "seconds:int=5", "manage_channels")
    sm = _n(g, "mod.slowmode", 360, 1620, seconds=5)
    sr = _reply(g, 700, 1620, "🐢 Slowmode updated.", ephemeral=True)
    _w(g, ts, "next", sm, "in"); _w(g, ts, "seconds", sm, "seconds"); _w(g, sm, "next", sr, "in")
    _group(g, "Channel clean-up", [tp, pg, pr, pf, ts, sm, sr])
    return g


def utility_bot() -> Graph:
    g = Graph()
    g.settings.name = "Utility Bot"
    g.settings.status_text = "🧰 /serverinfo /poll"
    t = _slash(g, 40, 40, "serverinfo", "Information about this server")
    r = _reply(g, 360, 40, embed_title="{guild.name}", embed_description="👥 **{guild.member_count}** members\n🆔 `{guild.id}`", embed_color="5865F2")
    _w(g, t, "next", r, "in")
    t2 = _slash(g, 40, 260, "userinfo", "Information about a member", "target:user")
    du = _n(g, "data.user", 40, 480)
    ts = _n(g, "data.snowflake_time", 340, 480)
    f = _n(g, "data.format", 640, 480, template="Account created <t:{ts}:R>\nUser ID: `{uid}`")
    r2 = _reply(g, 940, 260, embed_title="{target.display_name}", embed_color="57F287")
    _w(g, t2, "next", r2, "in"); _w(g, t2, "target", du, "user"); _w(g, du, "id", ts, "id"); _w(g, du, "id", f, "uid")
    _w(g, ts, "value", f, "ts"); _w(g, f, "text", r2, "embed_description")
    t3 = _slash(g, 40, 900, "poll", "Start a quick poll", "question:str, options:str")
    p = _n(g, "action.poll", 360, 900)
    _w(g, t3, "next", p, "in"); _w(g, t3, "question", p, "question"); _w(g, t3, "options", p, "options")
    t4 = _slash(g, 40, 1120, "avatar", "Show someone's avatar", "target:user")
    r4 = _reply(g, 360, 1120, "{target.display_avatar}")
    _w(g, t4, "next", r4, "in")
    t5 = _slash(g, 40, 1320, "ping", "Is the bot alive?")
    r5 = _reply(g, 360, 1320, "🏓 Pong!", ephemeral=True)
    _w(g, t5, "next", r5, "in")
    return g


def reaction_role_bot() -> Graph:
    g = Graph()
    g.settings.name = "Reaction Role Bot"
    g.settings.status_text = "🎨 pick your colour"
    t = _slash(g, 40, 40, "rolepanel", "Post the role picker (admins)", perm="manage_roles")
    panel = _n(g, "ui.panel", 360, 40, content="🎨 **Pick your colour role** — press a button to get it, press again to remove it.",
               buttons="Red | role_red | danger | 🔴\nBlue | role_blue | primary | 🔵\nGreen | role_green | success | 🟢")
    ack = _reply(g, 700, 40, "Role panel posted.", ephemeral=True)
    _chain(g, t, panel, ack)
    _group(g, "Post the panel", [t, panel, ack])
    for i, (cid, role) in enumerate((("role_red", "Red"), ("role_blue", "Blue"), ("role_green", "Green"))):
        y = 340 + i * 300
        c = _n(g, "trigger.component", 40, y, custom_id=cid, kind="button")
        h = _n(g, "flow.has_role", 360, y, role=role)
        rm = _n(g, "action.remove_role", 660, y - 60, role=role)
        rr = _reply(g, 960, y - 60, f"Removed the **{role}** role.", ephemeral=True)
        ad = _n(g, "action.add_role", 660, y + 100, role=role)
        ar = _reply(g, 960, y + 100, f"You now have the **{role}** role!", ephemeral=True)
        _w(g, c, "next", h, "in"); _w(g, h, "true", rm, "in"); _w(g, rm, "next", rr, "in"); _w(g, h, "false", ad, "in"); _w(g, ad, "next", ar, "in")
        _group(g, f"{role} toggle", [c, h, rm, rr, ad, ar])
    return g


def ticket_bot() -> Graph:
    g = Graph()
    g.settings.name = "Ticket Bot"
    g.settings.status_text = "🎫 need help? open a ticket"
    t = _slash(g, 40, 40, "ticketpanel", "Post the ticket panel (admins)", perm="manage_guild")
    panel = _n(g, "ui.panel", 360, 40, content="🎫 **Need help?** Press the button to open a private ticket with our staff.",
               buttons="Open a ticket | ticket_open | primary | 🎫")
    ack = _reply(g, 700, 40, "Ticket panel posted.", ephemeral=True)
    _chain(g, t, panel, ack)
    _group(g, "Panel", [t, panel, ack])
    c = _n(g, "trigger.component", 40, 340, custom_id="ticket_open", kind="button")
    ch = _n(g, "action.create_ticket", 360, 340, name="ticket-{user.name}", staff_role="Support")
    hello = _n(g, "action.send", 700, 340, content="Hello {user.mention}! Describe your problem and a **Support** member will be with you shortly.")
    close_panel = _n(g, "ui.panel", 1040, 340, content="When you're done, close the ticket:", buttons="Close ticket | ticket_close | danger | 🔒")
    f = _n(g, "data.format", 700, 560, template="🎫 Your ticket is ready: {mention}")
    done = _reply(g, 1380, 340, ephemeral=True)
    _chain(g, c, ch, hello, close_panel, done)
    _w(g, ch, "channel", hello, "channel"); _w(g, ch, "channel", close_panel, "channel"); _w(g, ch, "mention", f, "mention"); _w(g, f, "text", done, "content")
    _group(g, "Open a ticket", [c, ch, hello, close_panel, f, done])
    cc = _n(g, "trigger.component", 40, 760, custom_id="ticket_close", kind="button")
    ask = _n(g, "ui.confirm", 360, 760, content="Close this ticket? The channel will be deleted.")
    say = _reply(g, 700, 700, "🔒 Closing in 5 seconds…")
    wait = _n(g, "action.delay", 1000, 700, seconds=5)
    gone = _n(g, "action.delete_channel", 1300, 700)
    keep = _reply(g, 700, 900, "Okay, the ticket stays open.", ephemeral=True)
    _w(g, cc, "next", ask, "in"); _w(g, ask, "yes", say, "in"); _chain(g, say, wait, gone); _w(g, ask, "no", keep, "in")
    _group(g, "Close a ticket", [cc, ask, say, wait, gone, keep])
    return g


def announcement_bot() -> Graph:
    g = Graph()
    g.settings.name = "Announcement Bot"
    g.settings.status_text = "📣 /announce"
    t = _slash(g, 40, 40, "announce", "Post an announcement (staff)", "channel:channel", "manage_guild")
    form = _n(g, "ui.form", 360, 40, title="New announcement",
              fields="Title | title | short | yes | What's the headline?\nMessage | message | long | yes | Write the announcement\nPing (optional) | ping | short | no | e.g. @everyone or a role mention")
    send = _n(g, "action.send", 700, 40, embed_color="FEE75C", embed_footer="Announced by staff")
    done = _reply(g, 1040, 40, "✅ Announcement sent.", ephemeral=True)
    nope = _reply(g, 700, 260, "Announcement cancelled.", ephemeral=True)
    _w(g, t, "next", form, "in"); _w(g, form, "next", send, "in"); _w(g, form, "cancel", nope, "in"); _w(g, send, "next", done, "in")
    _w(g, t, "channel", send, "channel"); _w(g, form, "title", send, "embed_title"); _w(g, form, "message", send, "embed_description"); _w(g, form, "ping", send, "content")
    _group(g, "/announce", [t, form, send, done, nope])
    return g


# ============================================================================================ advanced bots
def _shop_flow(g, x, y, item_id, price, title="Shop"):
    t = _slash(g, x, y, "buy", "Buy an item from the shop")
    ask = _n(g, "ui.confirm", x + 320, y, content=f"Buy **{title}** for {price}?")
    sp = _n(g, "eco.spend", x + 640, y, amount=price)
    gv = _n(g, "game.give_gear", x + 960, y, template=item_id)
    ok = _reply(g, x + 1280, y, f"🛒 {{user.mention}} bought **{title}**!")
    no = _reply(g, x + 960, y + 220, "You can't afford that yet.", ephemeral=True)
    _w(g, t, "next", ask, "in"); _w(g, ask, "yes", sp, "in"); _w(g, sp, "paid", gv, "in"); _w(g, gv, "next", ok, "in"); _w(g, sp, "insufficient", no, "in")
    return [t, ask, sp, gv, ok, no]


def rpg_bot() -> Graph:
    g = Graph()
    g.settings.name = "RPG Bot"
    g.settings.status_text = "⚔️ /faction join"
    sc = _n(g, "module.scoring", 60, 60, side_names_list="Sunwardens, Moonwardens")
    t = _slash(g, 400, 60, "join", "Join a faction", "team:str")
    st = _n(g, "game.set_team", 700, 60)
    r = _reply(g, 1000, 60, "Welcome to **{team}**, {user.mention}!")
    _chain(g, t, st, r); _w(g, t, "team", st, "team")
    _group(g, "Factions & phrase scoring", [sc, t, st, r])
    gear = _n(g, "module.gear", 60, 360)
    shop = _shop_flow(g, 400, 360, "steel_sword", 120, "Steel Sword")
    _group(g, "Items & shop (atomic purchase)", [gear] + shop)
    dun = _n(g, "module.dungeons", 60, 720)
    boss = _n(g, "module.bosses", 60, 900)
    arena = _n(g, "module.arena", 60, 1080)
    tf = _slash(g, 400, 900, "fighting", "Is a boss running?")
    act = _n(g, "game.boss_active", 700, 900)
    f = _n(g, "data.format", 1000, 1100, template="⚔️ **{name}** has {hp} HP left — use your faction's phrases!")
    yes = _reply(g, 1300, 900); no = _reply(g, 1000, 900, "No boss right now.", ephemeral=True)
    _w(g, tf, "next", act, "in"); _w(g, act, "active", yes, "in"); _w(g, act, "idle", no, "in"); _w(g, act, "name", f, "name"); _w(g, act, "hp", f, "hp"); _w(g, f, "text", yes, "content")
    _group(g, "Combat: dungeons, bosses, arena", [dun, boss, arena, tf, act, f, yes, no])
    xp = _n(g, "module.xp", 60, 1380)
    qs = _n(g, "module.quests", 60, 1560)
    cd = _n(g, "module.cards", 60, 1740)
    _group(g, "Progression: XP, quests, profile cards", [xp, qs, cd])
    return g


def economy_bot() -> Graph:
    g = Graph()
    g.settings.name = "Economy Bot"
    g.settings.status_text = "🪙 /daily /work /shop"
    # daily + work (timed rewards)
    t = _slash(g, 40, 40, "daily", "Claim your daily coins")
    c = _n(g, "eco.claim", 340, 40, name="daily", hours=24)
    give = _n(g, "eco.give", 640, 0, amount=250)
    f1 = _n(g, "data.format", 640, 220, template="💰 +250 coins! You now have **{bal}**.")
    ok = _reply(g, 940, 0)
    f2 = _n(g, "data.format", 640, 420, template="⏳ Come back in **{left}**.")
    wait = _reply(g, 940, 300, ephemeral=True)
    _w(g, t, "next", c, "in"); _w(g, c, "ready", give, "in"); _w(g, give, "next", ok, "in"); _w(g, give, "balance", f1, "bal"); _w(g, f1, "text", ok, "content")
    _w(g, c, "waiting", wait, "in"); _w(g, c, "remaining", f2, "left"); _w(g, f2, "text", wait, "content")
    t2 = _slash(g, 40, 640, "work", "Work for coins (once an hour)")
    c2 = _n(g, "eco.claim", 340, 640, name="work", hours=1)
    ri = _n(g, "data.random_int", 340, 860, low=20, high=80)
    g2 = _n(g, "eco.give", 640, 640)
    f3 = _n(g, "data.format", 640, 860, template="💼 You worked hard and earned coins! Balance: **{bal}**")
    ok2 = _reply(g, 940, 640)
    w2 = _reply(g, 640, 1060, "You're tired — try again later.", ephemeral=True)
    _w(g, t2, "next", c2, "in"); _w(g, c2, "ready", g2, "in"); _w(g, ri, "value", g2, "amount"); _w(g, g2, "next", ok2, "in"); _w(g, g2, "balance", f3, "bal"); _w(g, f3, "text", ok2, "content")
    _w(g, c2, "waiting", w2, "in")
    _group(g, "Earning: /daily and /work", [t, c, give, f1, ok, f2, wait, t2, c2, ri, g2, f3, ok2, w2])
    # balance, richest, pay
    tb = _slash(g, 40, 1260, "balance", "Your coins"); b = _n(g, "eco.balance", 340, 1260); fb = _n(g, "data.format", 340, 1460, template="You have **{n}** coins.")
    rb = _reply(g, 640, 1260, ephemeral=True)
    _w(g, tb, "next", b, "in"); _w(g, b, "next", rb, "in"); _w(g, b, "balance", fb, "n"); _w(g, fb, "text", rb, "content")
    tr = _slash(g, 40, 1660, "richest", "Top 10 richest members"); top = _n(g, "eco.top", 340, 1660); rr = _reply(g, 640, 1660, embed_title="Richest members")
    _w(g, tr, "next", top, "in"); _w(g, top, "next", rr, "in"); _w(g, top, "board", rr, "embed_description")
    tp = _slash(g, 40, 1860, "pay", "Send coins to a member", "target:user, amount:int"); pay = _n(g, "eco.transfer", 340, 1860)
    ps = _reply(g, 640, 1860, "💸 Sent **{amount}** coins to {target.mention}."); pf = _reply(g, 640, 2060, "That didn't work — check the amount and your balance.", ephemeral=True)
    _w(g, tp, "next", pay, "in"); _w(g, tp, "target", pay, "to_user"); _w(g, tp, "amount", pay, "amount"); _w(g, pay, "sent", ps, "in"); _w(g, pay, "failed", pf, "in")
    _group(g, "Balances & payments", [tb, b, fb, rb, tr, top, rr, tp, pay, ps, pf])
    # shop + inventory
    ts = _slash(g, 40, 2300, "buy", "Buy a Lucky Charm"); ask = _n(g, "ui.confirm", 340, 2300, content="Buy a **Lucky Charm** for 150 coins?")
    sp = _n(g, "eco.spend", 640, 2300, amount=150); it = _n(g, "eco.item_give", 940, 2300, item="Lucky Charm", qty=1)
    so = _reply(g, 1240, 2300, "🍀 {user.mention} bought a **Lucky Charm**!"); sn = _reply(g, 940, 2520, "Not enough coins.", ephemeral=True)
    _w(g, ts, "next", ask, "in"); _w(g, ask, "yes", sp, "in"); _w(g, sp, "paid", it, "in"); _w(g, it, "next", so, "in"); _w(g, sp, "insufficient", sn, "in")
    ti = _slash(g, 40, 2720, "inventory", "Your items"); inv = _n(g, "eco.inventory", 340, 2720); ir = _reply(g, 640, 2720, embed_title="Your inventory", ephemeral=True)
    _w(g, ti, "next", inv, "in"); _w(g, inv, "next", ir, "in"); _w(g, inv, "text", ir, "embed_description")
    _group(g, "Shop & inventory", [ts, ask, sp, it, so, sn, ti, inv, ir])
    # gamble
    tg = _slash(g, 40, 2960, "bet", "Bet coins: 45% to double them", "amount:int"); bs_ = _n(g, "eco.spend", 340, 2960)
    ch = _n(g, "flow.chance", 640, 2960, percent=45); dbl = _n(g, "data.math", 640, 3200, op="*", b="2")
    win = _n(g, "eco.give", 940, 2960); wr = _reply(g, 1240, 2960, "🎉 You won! Balance: **{bal}**"); fw = _n(g, "data.format", 940, 3200, template="🎉 You won **{won}** coins! Balance: **{bal}**")
    lr = _reply(g, 940, 3160, "💥 You lost your bet.", ephemeral=False); nf = _reply(g, 640, 3360, "You can't cover that bet.", ephemeral=True)
    _w(g, tg, "next", bs_, "in"); _w(g, tg, "amount", bs_, "amount"); _w(g, bs_, "paid", ch, "in"); _w(g, ch, "true", win, "in"); _w(g, tg, "amount", dbl, "a")
    _w(g, dbl, "result", win, "amount"); _w(g, win, "next", wr, "in"); _w(g, win, "balance", fw, "bal"); _w(g, dbl, "result", fw, "won"); _w(g, fw, "text", wr, "content")
    _w(g, ch, "false", lr, "in"); _w(g, bs_, "insufficient", nf, "in")
    _group(g, "Gambling (spend first, then chance)", [tg, bs_, ch, dbl, win, wr, fw, lr, nf])
    return g


def game_bot() -> Graph:
    g = Graph()
    g.settings.name = "Game Bot"
    g.settings.status_text = "🎮 /dice /arena challenge"
    sc = _n(g, "module.scoring", 60, 60, side_names_list="Team Orbit, Team Nova")
    arena = _n(g, "module.arena", 60, 240)
    qs = _n(g, "module.quests", 60, 420)
    xp = _n(g, "module.xp", 60, 600)
    cd = _n(g, "module.cards", 60, 780)
    _group(g, "Systems: rankings, duels, achievements, stats", [sc, arena, qs, xp, cd])
    t = _slash(g, 400, 60, "dice", "Roll to win coins (5 or 6 wins)")
    py = _n(g, "action.python", 700, 60, outputs="roll", inputs="",
            code="import random\n\n# Each run is independent, so a fresh roll per command; the result becomes the `roll` output port.\nroll = random.randint(1, 6)")
    cmp = _n(g, "data.compare", 700, 300, op=">=", b="5")
    br = _n(g, "flow.branch", 1000, 60)
    win = _n(g, "eco.give", 1300, 20, amount=50); trk1 = _n(g, "game.track", 1600, 20, event="match_won")
    fw = _n(g, "data.format", 1300, 240, template="🎲 {user.mention} rolled **{roll}** — you win 50 coins!"); rw = _reply(g, 1900, 20)
    trk2 = _n(g, "game.track", 1300, 420, event="match_played"); fl = _n(g, "data.format", 1300, 600, template="🎲 {user.mention} rolled **{roll}** — better luck next time."); rl = _reply(g, 1600, 420)
    _w(g, t, "next", py, "in"); _w(g, py, "next", br, "in"); _w(g, py, "roll", cmp, "a"); _w(g, cmp, "result", br, "condition")
    _w(g, br, "true", win, "in"); _chain(g, win, trk1, rw); _w(g, py, "roll", fw, "roll"); _w(g, fw, "text", rw, "content")
    _w(g, br, "false", trk2, "in"); _w(g, trk2, "next", rl, "in"); _w(g, py, "roll", fl, "roll"); _w(g, fl, "text", rl, "content")
    _group(g, "/dice mini-game (custom Python + branch + events)", [t, py, cmp, br, win, trk1, fw, rw, trk2, fl, rl])
    jt = _slash(g, 400, 800, "join", "Join a team", "team:str"); js = _n(g, "game.set_team", 700, 800); jr = _reply(g, 1000, 800, "You joined **{team}**!")
    _chain(g, jt, js, jr); _w(g, jt, "team", js, "team")
    _group(g, "Teams (used by rankings and arena Elo)", [jt, js, jr])
    return g


def community_bot() -> Graph:
    g = Graph()
    g.settings.name = "Community Bot"
    g.settings.status_text = "🌱 /rep /quests /rank"
    xp = _n(g, "module.xp", 60, 60); qs = _n(g, "module.quests", 60, 240)
    _group(g, "Member progression", [xp, qs])
    bd = _n(g, "module.birthdays", 60, 480); ev = _n(g, "module.event_scheduler", 60, 660); tk = _n(g, "module.task_lists", 60, 840)
    _group(g, "Events & community calendar", [bd, ev, tk])
    br = _n(g, "module.role_brackets", 60, 1080); gp = _n(g, "module.game_profiles", 60, 1260)
    _group(g, "Automated role brackets by level", [br, gp])
    j = _n(g, "trigger.member_join", 400, 60); ar = _n(g, "action.add_role", 700, 60, role="Member")
    env = _n(g, "data.env", 400, 280, name="WELCOME_CHANNEL_ID")
    sd = _n(g, "action.send", 1000, 60, content="Welcome {user.mention}! 🌱 Say hi, set your birthday with /birthday set, and check /quests.")
    _chain(g, j, ar, sd); _w(g, env, "value", sd, "channel")
    _group(g, "Automatic roles & welcome", [j, ar, env, sd])
    t = _slash(g, 400, 540, "rep", "Give a member a reputation point (once every 12 h)", "target:user")
    c = _n(g, "eco.claim", 700, 540, name="rep", hours=12)
    gv = _n(g, "eco.give", 1000, 500, account="reputation", amount=1); r = _reply(g, 1300, 500, "⭐ {user.mention} gave {target.mention} a reputation point!")
    w = _reply(g, 1000, 720, "You can give reputation again in {left}.", ephemeral=True)
    fw = _n(g, "data.format", 700, 760, template="You can give reputation again in **{left}**.")
    _w(g, t, "next", c, "in"); _w(g, c, "ready", gv, "in"); _w(g, t, "target", gv, "user"); _w(g, gv, "next", r, "in")
    _w(g, c, "waiting", w, "in"); _w(g, c, "remaining", fw, "left"); _w(g, fw, "text", w, "content")
    tr = _slash(g, 400, 960, "reputation", "See a member's reputation", "target:user"); b = _n(g, "eco.balance", 700, 960, account="reputation")
    fb = _n(g, "data.format", 700, 1160, template="{target.display_name} has **{n}** reputation."); rb = _reply(g, 1000, 960)
    _w(g, tr, "next", b, "in"); _w(g, tr, "target", b, "user"); _w(g, b, "next", rb, "in"); _w(g, b, "balance", fb, "n"); _w(g, fb, "text", rb, "content")
    _group(g, "Reputation (cooldown + separate wallet)", [t, c, gv, r, w, fw, tr, b, fb, rb])
    tw = _slash(g, 400, 1360, "warn", "Warn a member", "target:user, reason:str", "moderate_members"); wn = _n(g, "mod.warn", 700, 1360, namespace="warnings", reason="{reason}")
    wr = _reply(g, 1000, 1360, "⚠️ {target.mention} has been warned: {reason}", ephemeral=True)
    _w(g, tw, "next", wn, "in"); _w(g, tw, "target", wn, "user"); _w(g, wn, "next", wr, "in")
    tp = _slash(g, 400, 1560, "poll", "Ask the community", "question:str, options:str"); pl = _n(g, "action.poll", 700, 1560)
    _w(g, tp, "next", pl, "in"); _w(g, tp, "question", pl, "question"); _w(g, tp, "options", pl, "options")
    _group(g, "Moderation & engagement", [tw, wn, wr, tp, pl])
    return g


def gacha_bot() -> Graph:
    g = Graph()
    g.settings.name = "Gacha Bot"
    g.settings.status_text = "🎰 /pull"
    qs = _n(g, "module.quests", 60, 60)
    _group(g, "Quests count pulls (event: gacha_pull)", [qs])
    t = _slash(g, 400, 60, "pull", "Pull a character (100 gems)")
    ask = _n(g, "ui.confirm", 700, 60, content="Spend **100 gems** on one pull?")
    sp = _n(g, "eco.spend", 1000, 60, account="gems", amount=100)
    wc = _n(g, "data.weighted_choice", 1000, 300, items="Slime:30|Goblin:25|Elf:16|Knight:12|Mage:9|Dragon:5|Phoenix:3")
    it = _n(g, "eco.item_give", 1300, 60, account="collection", qty=1)
    trk = _n(g, "game.track", 1600, 60, event="gacha_pull")
    f = _n(g, "data.format", 1300, 300, template="🎰 {user.mention} pulled **{c}**!"); ok = _reply(g, 1900, 60)
    no = _reply(g, 1300, 500, "Not enough gems — try /dailygems.", ephemeral=True)
    _w(g, t, "next", ask, "in"); _w(g, ask, "yes", sp, "in"); _w(g, sp, "paid", it, "in"); _w(g, wc, "value", it, "item"); _chain(g, it, trk, ok)
    _w(g, wc, "value", f, "c"); _w(g, f, "text", ok, "content"); _w(g, sp, "insufficient", no, "in")
    _group(g, "/pull — spend, roll the odds, store, announce", [t, ask, sp, wc, it, trk, f, ok, no])
    tc = _slash(g, 400, 760, "collection", "Your characters"); inv = _n(g, "eco.inventory", 700, 760, account="collection"); cr = _reply(g, 1000, 760, embed_title="Your collection", ephemeral=True)
    _w(g, tc, "next", inv, "in"); _w(g, inv, "next", cr, "in"); _w(g, inv, "text", cr, "embed_description")
    td = _slash(g, 400, 960, "dailygems", "Claim 300 free gems"); cl = _n(g, "eco.claim", 700, 960, name="gems", hours=24)
    gg = _n(g, "eco.give", 1000, 960, account="gems", amount=300); gr = _reply(g, 1300, 960, "💎 +300 gems!", ephemeral=True)
    gw = _reply(g, 1000, 1160, "Come back tomorrow for more gems.", ephemeral=True)
    _w(g, td, "next", cl, "in"); _w(g, cl, "ready", gg, "in"); _w(g, gg, "next", gr, "in"); _w(g, cl, "waiting", gw, "in")
    tb = _slash(g, 400, 1360, "gems", "Your gems"); bb = _n(g, "eco.balance", 700, 1360, account="gems"); fb = _n(g, "data.format", 700, 1560, template="💎 You have **{n}** gems.")
    rb = _reply(g, 1000, 1360, ephemeral=True)
    _w(g, tb, "next", bb, "in"); _w(g, bb, "next", rb, "in"); _w(g, bb, "balance", fb, "n"); _w(g, fb, "text", rb, "content")
    _group(g, "Collection, free gems, balance", [tc, inv, cr, td, cl, gg, gr, gw, tb, bb, fb, rb])
    return g


def multi_system_bot() -> Graph:
    """Everything at once, organised by subsystem: the reference for how a big project stays readable."""
    g = Graph()
    g.settings.name = "Multi-System Bot"
    g.settings.status_text = "🧩 many systems, one bot"
    mods = [("Progression", ["module.xp", "module.quests", "module.cards"]),
            ("Factions & combat", ["module.scoring", "module.gear", "module.bosses", "module.arena", "module.dungeons"]),
            ("Community", ["module.birthdays", "module.event_scheduler", "module.task_lists", "module.emotes"]),
            ("Roster & routing", ["module.role_brackets", "module.game_profiles", "module.post_router"])]
    y = 60
    for title, types in mods:
        nodes = [_n(g, tp, 60, y + 170 * i) for i, tp in enumerate(types)]
        _group(g, f"Modules — {title}", nodes)
        y += 170 * len(types) + 60
    t = _slash(g, 460, 60, "daily", "Claim your daily coins"); c = _n(g, "eco.claim", 760, 60, name="daily", hours=24)
    give = _n(g, "eco.give", 1060, 20, amount=250); ok = _reply(g, 1360, 20, "💰 +250 coins!"); wt = _reply(g, 1060, 220, "⏳ Come back later.", ephemeral=True)
    _w(g, t, "next", c, "in"); _w(g, c, "ready", give, "in"); _w(g, give, "next", ok, "in"); _w(g, c, "waiting", wt, "in")
    sh = _shop_flow(g, 460, 400, "steel_sword", 120, "Steel Sword")
    _group(g, "Flows: economy & shop (talk to modules through the wallet and services)", [t, c, give, ok, wt] + sh)
    dash = _n(g, "module.dashboard", 460, 900, title="Multi-System Dashboard")
    rt = _n(g, "web.route", 460, 1100, method="GET", path="/api/custom/stats"); vi = _n(g, "store.incr", 760, 1100, namespace="visits", key="{guild_id}")
    ob = _n(g, "data.object", 760, 1300, keys="visits"); bc = _n(g, "web.broadcast", 1060, 1100, event="visit"); rj = _n(g, "web.json", 1360, 1100)
    _w(g, rt, "next", vi, "in"); _w(g, vi, "value", ob, "visits"); _chain(g, vi, bc, rj); _w(g, ob, "json", bc, "data"); _w(g, ob, "json", rj, "data")
    pg = _n(g, "web.html", 460, 1500, slug="status", title="Status", nav_title="Status"); css = _n(g, "web.css", 160, 1500); js = _n(g, "web.js", 160, 1700)
    _w(g, css, "attach", pg, "assets"); _w(g, js, "attach", pg, "assets")
    _group(g, "Web dashboard (browser ↔ server ↔ bot)", [dash, rt, vi, ob, bc, rj, pg, css, js])
    return g


# ============================================================================================ catalogue
BEGINNER, INTERMEDIATE, ADVANCED = "Beginner", "Intermediate", "Advanced"

EXAMPLE_INFO: list[dict] = [
    # ---------------------------------------------------------------- simple
    dict(fn="welcome_bot", title="Welcome Bot", category="simple", difficulty=BEGINNER,
         summary="Greets new members, gives them a starter role, DMs a short guide and offers a /rules menu.",
         architecture="**Two independent flows.**\n\n`Member Join → Send (embed) → Add Role → DM`\n\n`/rules → Tabbed Menu`\n\nThe join flow is an **event** (nobody asked for it); `/rules` is a **command**. The welcome channel comes from the `WELCOME_CHANNEL_ID` setting (*Data → Environment*), so the project file holds no server-specific IDs.",
         features=[("Welcome embed with placeholders ({user.mention}, {guild.name})", BEGINNER), ("Starter role on join", BEGINNER), ("Direct message", BEGINNER), ("Tabbed rules menu (dropdown)", INTERMEDIATE)],
         extra_setup="1. Create a role named **Newcomer** (the bot's own role must be **above** it in the role list).\n2. In *Bot → Deploy target → Extra .env values* add `WELCOME_CHANNEL_ID=<the channel's ID>` (Developer Mode → right-click → Copy Channel ID).\n3. Turn on **Server Members Intent** in the portal.",
         lesson="l1"),
    dict(fn="moderation_bot", title="Moderation Bot", category="simple", difficulty=INTERMEDIATE,
         summary="Warn (with automatic escalation), timeout, kick, ban, purge and slowmode — each gated by the right permission.",
         architecture="**One flow per command**, each starting with the permission the moderator needs (checked by Discord *before* your logic runs).\n\n`/warn → Warn → count ≥ 3? → Timeout 1 h / Reply` shows **data wiring into a branch**: the Warn node outputs the member's running count, a Compare node tests it, a Branch acts on it.\n\nWarnings are stored in the shared database (table `warnings`), keyed by server and member.",
         features=[("Permission-gated commands", BEGINNER), ("Timeout / kick / ban with reasons", BEGINNER), ("Purge and slowmode", BEGINNER), ("Escalation: 3 warnings → timeout (Compare + Branch)", INTERMEDIATE)],
         extra_setup="The bot's role must be **above** the members it moderates. Give it *Moderate Members*, *Kick Members*, *Ban Members*, *Manage Messages* and *Manage Channels*; grant only what you want it to use.",
         lesson="l2"),
    dict(fn="utility_bot", title="Utility Bot", category="simple", difficulty=BEGINNER,
         summary="Server info, user info (with account age), reaction polls, avatars and a ping command.",
         architecture="**Five tiny commands**, each a trigger and one or two nodes — a good first project to read. `/userinfo` chains **User → Snowflake Time → Format**: a Discord ID *contains* its creation time, which the node extracts and Discord renders as a relative time (`<t:…:R>`) in every viewer's timezone.",
         features=[("/serverinfo and /ping", BEGINNER), ("/avatar", BEGINNER), ("/userinfo with account age (data chain)", INTERMEDIATE), ("/poll with numbered reactions", INTERMEDIATE)],
         extra_setup="Polls need the **Add Reactions** permission."),
    dict(fn="reaction_role_bot", title="Reaction Role Bot", category="simple", difficulty=INTERMEDIATE,
         summary="A permanent button panel where members toggle colour roles themselves.",
         architecture="**One posting flow + one toggle flow per button.**\n\n`/rolepanel → Post Panel` creates buttons with fixed ids (`role_red` …) that survive restarts.\n\nEach button has an **On Component Used** trigger → **Has Role?** → remove it, or add it. The *toggle* (true/false branch) is the pattern to copy for any on/off feature.",
         features=[("Permanent button panel", INTERMEDIATE), ("Component triggers by id", INTERMEDIATE), ("Toggle with Has Role? branch", INTERMEDIATE)],
         extra_setup="Create roles named **Red**, **Blue**, **Green** and put the bot's role above them (a bot can only assign roles *below* its own). Run `/rolepanel` once in the channel where the picker should live."),
    dict(fn="ticket_bot", title="Ticket Bot", category="simple", difficulty=INTERMEDIATE,
         summary="A button opens a private support channel; a Close button deletes it after confirmation.",
         architecture="**Three flows.**\n\n`/ticketpanel → Post Panel`\n\n`Open button → Create Private Channel → Send greeting → Post close button → Reply (link)`\n\n`Close button → Confirm → Reply → Delay → Delete Channel`\n\nThe new channel is only visible to the opener, the **Support** role and the bot. Its *channel* output feeds Send and Post Panel, so the greeting and close button land inside the ticket.",
         features=[("Interaction buttons", BEGINNER), ("Private channel with permission overwrites", INTERMEDIATE), ("Confirm dialog before deleting", INTERMEDIATE)],
         extra_setup="Create a role named **Support**. The bot needs **Manage Channels** (to create/delete) — and, as always, a role above `Support` isn't required, but the channel must not be blocked by category permissions. Run `/ticketpanel` in your help channel."),
    dict(fn="announcement_bot", title="Announcement Bot", category="simple", difficulty=BEGINNER,
         summary="Staff run /announce, fill in a form, and an embed is posted to the channel they chose.",
         architecture="`/announce channel → Show Form → Send → Reply`.\n\nThe slash command's **channel** option is wired straight into Send's *channel* input; the form's fields become outputs wired into the embed title, description and optional ping. A **cancel** path handles someone closing the form.",
         features=[("Form (modal) with three fields", INTERMEDIATE), ("Embed announcements", BEGINNER), ("Permission-gated", BEGINNER)],
         extra_setup="Give the bot **Send Messages** and **Embed Links** in the target channels. To ping a role, type its mention (`<@&ROLE_ID>`) into the *Ping* field; pings are only sent if the bot may mention that role.",
         lesson="l1"),
    # ---------------------------------------------------------------- advanced
    dict(fn="rpg_bot", title="RPG Bot", category="advanced", difficulty=ADVANCED,
         summary="Factions that score with phrases, gear and rarity, dungeons, boss fights, arena duels, quests, XP levels and profile cards.",
         architecture="**Seven modules, four small flows, zero wires between modules.**\n\nSystems cooperate through *three* shared things:\n- the **wallet** (one currency for shop, bosses, arena, quests)\n- the **event bus** (scoring emits `hits`, bosses/quests listen)\n- **services** (`gear.stats`, `score.scan`, `boss.start` …)\n\nThe shop flow shows an **atomic purchase**: *Spend Currency* checks and deducts in one step, so two simultaneous buys can't both succeed.",
         features=[("Factions, phrases, levels", INTERMEDIATE), ("Gear with rarity and enchants", ADVANCED), ("Atomic shop purchase", INTERMEDIATE), ("Dungeons, bosses, arena (Elo)", ADVANCED), ("Quests, XP, cards", INTERMEDIATE)],
         extra_setup="After deploying, configure the game *in Discord* (no code): `/faction create`, `/trigger bulk`, `/gearadmin → Add Gear` (add `steel_sword`), `/boss create`, `/questsadmin`. Needs **Message Content Intent** (phrase scoring) and **Create Public Threads** (dungeons, arena).",
         lesson="l3"),
    dict(fn="economy_bot", title="Economy Bot", category="advanced", difficulty=INTERMEDIATE,
         summary="Daily and hourly rewards, balances, payments, a shop with inventory, a leaderboard and a double-or-nothing bet.",
         architecture="Five **groups** of flows, all on one wallet table:\n\n- **Earning** — Timed Reward (cooldown per member) → Give Currency\n- **Balances & payments** — Get Balance, Transfer Currency (fails safely), Top Balances\n- **Shop** — Confirm → Spend → Give Item; Inventory List\n- **Gambling** — Spend first, *then* roll the odds (Chance)\n\nEvery money change uses an **atomic** node, so concurrent use can't duplicate or lose coins.",
         features=[("Cooldown rewards (/daily, /work)", BEGINNER), ("Balance, leaderboard, /pay", INTERMEDIATE), ("Shop + inventory", INTERMEDIATE), ("Spend-then-chance gambling", INTERMEDIATE)],
         extra_setup="No intents needed. The wallet lives in `data/bot.db` — back it up. To run a second currency, change the *Currency / wallet name* on the nodes (see the Gacha Bot).",
         lesson=None),
    dict(fn="game_bot", title="Game Bot", category="advanced", difficulty=ADVANCED,
         summary="Competitive play: team rankings, arena duels with Elo, achievements via quests, player cards and a coin-flip style mini-game.",
         architecture="**Systems on the left, a mini-game on the right.**\n\nThe *Arena* module owns sessions and matchmaking (challenge → accept → thread → resolve → Elo per faction); *Scoring* owns rankings; *Quests* own achievements; *Cards* only read.\n\n`/dice` shows how **custom Python** (one `random` call, output `roll`) plugs into a branch and reports results with **Track Progress**, so quests count wins and plays without knowing about the mini-game.",
         features=[("Elo-rated duels in threads", ADVANCED), ("Team rankings and boards", INTERMEDIATE), ("Quests as achievements", INTERMEDIATE), ("Custom Python mini-game", ADVANCED)],
         extra_setup="Needs **Message Content Intent** (duel damage comes from messages) and **Create Public Threads**. Players pick a team with `/join`; admins create factions with `/faction create`.",
         lesson="l3"),
    dict(fn="community_bot", title="Community Management Bot", category="advanced", difficulty=ADVANCED,
         summary="Member progression, reputation, automatic roles, birthdays, events, task lists, role brackets, moderation and polls.",
         architecture="Four groups, each owning one concern:\n\n- **Progression** — XP & Levels, Quests\n- **Events & calendar** — Birthdays, Event Scheduler, Task Lists\n- **Automation** — join → role → welcome, role brackets by level\n- **Engagement** — a **reputation** system built from a Timed Reward (one point per giver per 12 h) and a *separate* wallet called `reputation`\n\nReputation shows the pattern for adding a new concept **without code**: pick a wallet name, reuse Give/Balance.",
         features=[("XP, levels, quests", INTERMEDIATE), ("Birthdays and events", INTERMEDIATE), ("Auto roles and brackets", ADVANCED), ("Reputation with cooldown", ADVANCED), ("Moderation + polls", BEGINNER)],
         extra_setup="Create a **Member** role (below the bot's) and put `WELCOME_CHANNEL_ID=<id>` in *Extra .env values*. Enable **Server Members Intent**. Then `/birthday-config`, `/questsadmin`, `/game add` and `/bracket setrole` as needed."),
    dict(fn="gacha_bot", title="Gacha Bot", category="advanced", difficulty=INTERMEDIATE,
         summary="Spend gems on pulls with weighted odds, build a collection, claim free daily gems and complete pull quests.",
         architecture="`/pull → Confirm → Spend (gems) → Weighted Choice → Give Item → Track Progress → Reply`\n\nThe **Weighted Choice** node picks *once* per run (so the item stored and the text shown always match) with the odds you write (`Slime:30|…|Phoenix:3`). Gems and collection are **separate tables**, so the premium currency never mixes with coins. A `gacha_pull` event lets Quests reward pulling.",
         features=[("Weighted random pulls", INTERMEDIATE), ("Collection (inventory)", INTERMEDIATE), ("Premium currency + daily claim", BEGINNER), ("Quest integration via events", ADVANCED)],
         extra_setup="No intents needed. Publish your odds in the server — hidden odds feel unfair. Add a pull quest with `/questsadmin → Custom event… → gacha_pull`."),
    dict(fn="multi_system_bot", title="Multi-System Bot", category="advanced", difficulty=ADVANCED,
         summary="Fifteen modules, an economy, a shop and a web dashboard in one project — organised so you can still read it.",
         architecture="**The reference for a big project.** Everything is grouped on the canvas by subsystem:\n\n- *Modules* — grouped by concern (progression, factions & combat, community, roster & routing)\n- *Flows* — economy and shop, talking to modules only through the wallet and services\n- *Web* — dashboard module, a Web Route, and an HTML page with CSS and browser JavaScript\n\nThree layers: **Discord bot (Python)** · **dashboard server (Node)** · **browser**. Modules share a database but never each other's tables.",
         features=[("Module organisation with groups", ADVANCED), ("Economy + shop flows", INTERMEDIATE), ("Web dashboard (HTML/CSS/JS + route + live socket)", ADVANCED), ("All Community and Game modules", ADVANCED)],
         extra_setup="Several modules want privileged intents: enable **Message Content** and **Server Members**. The dashboard needs `DISCORD_CLIENT_SECRET` and a redirect URL in the portal (see lesson 6). Try it offline first with **Help → Web test console**. Remove modules you don't need — every module adds commands.",
         lesson="l6"),
]
INFO_BY_FN = {i["fn"]: i for i in EXAMPLE_INFO}
