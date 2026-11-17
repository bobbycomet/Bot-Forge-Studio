"""Starter projects shown in File > Examples."""
from __future__ import annotations

from .model import Graph
from . import project as _p  # noqa: F401  (registers nodes)


def hello() -> Graph:
    g = Graph()
    g.settings.name = "Hello Bot"
    t = g.add_node("trigger.slash", 60, 80, {"name": "hello", "description": "Say hello"})
    r = g.add_node("action.reply", 380, 80, {"content": "Hello {user.mention}! 👋"})
    g.connect(t.id, "next", r.id, "in")
    return g


def coinflip() -> Graph:
    g = Graph()
    g.settings.name = "Coin Flip"
    t = g.add_node("trigger.slash", 40, 80, {"name": "flip", "description": "Flip a coin"})
    c = g.add_node("data.random_choice", 40, 330, {"items": "Heads|Tails"})
    f = g.add_node("data.format", 330, 330, {"template": "🪙 {user.mention} flipped **{side}**!"})
    r = g.add_node("action.reply", 640, 80)
    g.connect(c.id, "value", f.id, "side")
    g.connect(f.id, "text", r.id, "content")
    g.connect(t.id, "next", r.id, "in")
    return g


def mod_timeout() -> Graph:
    g = Graph()
    g.settings.name = "Mod Tools"
    t = g.add_node("trigger.slash", 40, 60, {"name": "mute", "description": "Timeout a member",
                                              "params": "target:user, minutes:int=10, reason:str?"})
    p = g.add_node("flow.has_permission", 360, 60, {"permission": "moderate_members"})
    m = g.add_node("mod.timeout", 660, 20, {"reason": "{user.name} used /mute"})
    ok = g.add_node("action.reply", 960, 20, {"content": "Muted {target.mention} for {minutes} minutes.", "ephemeral": True})
    no = g.add_node("action.reply", 660, 260, {"content": "You can't do that.", "ephemeral": True})
    g.connect(t.id, "next", p.id, "in")
    g.connect(p.id, "true", m.id, "in")
    g.connect(p.id, "false", no.id, "in")
    g.connect(t.id, "target", m.id, "user")
    g.connect(t.id, "minutes", m.id, "minutes")
    g.connect(m.id, "next", ok.id, "in")
    return g


def welcome() -> Graph:
    g = Graph()
    g.settings.name = "Welcome Bot"
    t = g.add_node("trigger.member_join", 40, 80)
    e = g.add_node("data.env", 40, 300, {"name": "WELCOME_CHANNEL_ID"})
    s = g.add_node("action.send", 340, 80, {"embed_title": "Welcome!", "embed_description": "Glad you're here, {user.mention} 🎉"})
    r = g.add_node("action.add_role", 660, 80, {"role": "Member"})
    g.connect(t.id, "next", s.id, "in")
    g.connect(e.id, "value", s.id, "channel")
    g.connect(s.id, "next", r.id, "in")
    return g


def command_counter() -> Graph:
    g = Graph()
    g.settings.name = "Counter"
    t = g.add_node("trigger.slash", 40, 80, {"name": "count", "description": "Count how often you used this"})
    i = g.add_node("store.incr", 340, 80, {"namespace": "uses", "key": "{user.id}"})
    r = g.add_node("action.reply", 660, 80, {"content": "{user.mention}, that's use number {value}.", "ephemeral": True})
    f = g.add_node("data.format", 340, 300, {"template": "{user.mention}, that's use number {n}."})
    g.connect(t.id, "next", i.id, "in")
    g.connect(i.id, "next", r.id, "in")
    g.connect(i.id, "value", f.id, "n")
    g.connect(f.id, "text", r.id, "content")
    return g


def anon_posting() -> Graph:
    g = Graph()
    g.settings.name = "Anonymous Posts"
    g.settings.status_text = "/anon"
    g.add_node("module.anon_post", 80, 80)
    return g


def emote_adder() -> Graph:
    g = Graph()
    g.settings.name = "Emote Adder"
    t = g.add_node("trigger.slash", 40, 40, {"name": "addemote", "description": "Add an application emoji",
                                              "params": "name:str, image:attachment",
                                              "require_permission": "manage_guild", "defer": True,
                                              "defer_ephemeral": True})
    a = g.add_node("action.app_emoji_add", 380, 40)
    b = g.add_node("flow.branch", 700, 40)
    okf = g.add_node("data.format", 700, 330, {"template": "Added {emoji} — use it as :{name}:"})
    ok = g.add_node("action.reply", 1010, 20, {"ephemeral": True})
    bad = g.add_node("action.reply", 1010, 240, {"ephemeral": True})
    badf = g.add_node("data.format", 700, 480, {"template": "Couldn't add it: {error}"})
    g.connect(t.id, "next", a.id, "in")
    g.connect(t.id, "name", a.id, "name")
    g.connect(t.id, "image", a.id, "image")
    g.connect(a.id, "next", b.id, "in")
    g.connect(a.id, "ok", b.id, "condition")
    g.connect(a.id, "emoji", okf.id, "emoji")
    g.connect(a.id, "error", badf.id, "error")
    g.connect(b.id, "true", ok.id, "in")
    g.connect(b.id, "false", bad.id, "in")
    g.connect(okf.id, "text", ok.id, "content")
    g.connect(badf.id, "text", bad.id, "content")
    return g


def community() -> Graph:
    g = Graph()
    g.settings.name = "Community Bot"
    g.add_node("module.xp", 60, 60)
    g.add_node("module.uid_registry", 60, 360)
    t = g.add_node("trigger.slash", 420, 60, {"name": "ping", "description": "Latency check"})
    r = g.add_node("action.reply", 740, 60, {"content": "Pong! 🏓"})
    g.connect(t.id, "next", r.id, "in")
    return g


def dashboard() -> Graph:
    """A discord.py bot plus its discord.js web dashboard, translated from the same graph."""
    g = Graph()
    g.settings.name = "Dashboard Bot"
    # --- the bot (Python)
    g.add_node("module.xp", 60, 60)
    t = g.add_node("trigger.slash", 60, 300, {"name": "count", "description": "Count how often you used this"})
    i = g.add_node("store.incr", 380, 300, {"namespace": "uses", "key": "{guild.id}:{user.id}"})
    f = g.add_node("data.format", 380, 520, {"template": "{user.mention}, that's use number {n}."})
    r = g.add_node("action.reply", 700, 300, {"ephemeral": True})
    g.connect(t.id, "next", i.id, "in")
    g.connect(i.id, "next", r.id, "in")
    g.connect(i.id, "value", f.id, "n")
    g.connect(f.id, "text", r.id, "content")
    # --- the dashboard (JavaScript): pages come from the bot above; one custom endpoint on top
    g.add_node("module.dashboard", 60, 640, {"title": "Server Dashboard",
                                             "pages": "Stats | api | /api/custom/stats"})
    rt = g.add_node("web.route", 1020, 60, {"method": "GET", "path": "/api/custom/stats"})
    vi = g.add_node("store.incr", 1340, 60, {"namespace": "dashboard_visits", "key": "{guild_id}"})
    ob = g.add_node("data.object", 1340, 300, {"keys": "server, visits"})
    rj = g.add_node("web.json", 1660, 60)
    g.connect(rt.id, "next", vi.id, "in")
    g.connect(rt.id, "guild_id", ob.id, "server")
    g.connect(vi.id, "value", ob.id, "visits")
    g.connect(vi.id, "next", rj.id, "in")
    g.connect(ob.id, "json", rj.id, "data")
    return g


def daily_reward() -> Graph:
    """Economy: /daily pays once per day, /balance shows the wallet, /richest shows the board."""
    g = Graph()
    g.settings.name = "Economy Starter"
    t = g.add_node("trigger.slash", 40, 40, {"name": "daily", "description": "Claim your daily coins"})
    c = g.add_node("eco.claim", 340, 40, {"name": "daily", "hours": 24})
    give = g.add_node("eco.give", 640, 20, {"amount": 250})
    okf = g.add_node("data.format", 640, 220, {"template": "💰 +250 coins! You now have **{bal}**."})
    ok = g.add_node("action.reply", 960, 20)
    wf = g.add_node("data.format", 640, 420, {"template": "⏳ Come back in **{left}**."})
    wait = g.add_node("action.reply", 960, 320, {"ephemeral": True})
    g.connect(t.id, "next", c.id, "in")
    g.connect(c.id, "ready", give.id, "in"); g.connect(give.id, "next", ok.id, "in")
    g.connect(give.id, "balance", okf.id, "bal"); g.connect(okf.id, "text", ok.id, "content")
    g.connect(c.id, "waiting", wait.id, "in"); g.connect(c.id, "remaining", wf.id, "left"); g.connect(wf.id, "text", wait.id, "content")
    t2 = g.add_node("trigger.slash", 40, 620, {"name": "balance", "description": "Your coins"})
    b = g.add_node("eco.balance", 340, 620)
    bf = g.add_node("data.format", 340, 800, {"template": "You have **{n}** coins."})
    br = g.add_node("action.reply", 640, 620, {"ephemeral": True})
    g.connect(t2.id, "next", b.id, "in"); g.connect(b.id, "next", br.id, "in")
    g.connect(b.id, "balance", bf.id, "n"); g.connect(bf.id, "text", br.id, "content")
    t3 = g.add_node("trigger.slash", 40, 1000, {"name": "richest", "description": "Top 10 richest members"})
    top = g.add_node("eco.top", 340, 1000)
    tr = g.add_node("action.reply", 640, 1000, {"embed_title": "Richest members"})
    g.connect(t3.id, "next", top.id, "in"); g.connect(top.id, "next", tr.id, "in"); g.connect(top.id, "board", tr.id, "embed_description")
    return g


def signup_form() -> Graph:
    """Interact: a form (modal) whose answers feed a confirm dialog and a reply."""
    g = Graph()
    g.settings.name = "Signup Form"
    t = g.add_node("trigger.slash", 40, 40, {"name": "apply", "description": "Apply to join the team"})
    f = g.add_node("ui.form", 340, 40, {"title": "Application",
                                         "fields": "Name | name | short | yes | Your name\nWhy us? | why | long | yes | Tell us why"})
    cf = g.add_node("ui.confirm", 700, 40, {"content": "Send your application?"})
    fmt = g.add_node("data.format", 700, 260, {"template": "✅ Thanks **{name}** — we got: {why}"})
    yes = g.add_node("action.reply", 1020, 20, {"ephemeral": True})
    no = g.add_node("action.reply", 1020, 220, {"content": "Cancelled.", "ephemeral": True})
    g.connect(t.id, "next", f.id, "in"); g.connect(f.id, "next", cf.id, "in")
    g.connect(cf.id, "yes", yes.id, "in"); g.connect(cf.id, "no", no.id, "in")
    g.connect(f.id, "name", fmt.id, "name"); g.connect(f.id, "why", fmt.id, "why"); g.connect(fmt.id, "text", yes.id, "content")
    return g


def server_guide() -> Graph:
    """Interact: a tabbed menu and a paged message, plus a ticket panel handled by a component trigger."""
    g = Graph()
    g.settings.name = "Server Guide"
    t = g.add_node("trigger.slash", 40, 40, {"name": "guide", "description": "Read the server guide"})
    tabs = g.add_node("ui.tabs", 340, 40)
    g.connect(t.id, "next", tabs.id, "in")
    t2 = g.add_node("trigger.slash", 40, 360, {"name": "setup_tickets", "description": "Post the ticket panel",
                                                "require_permission": "manage_guild"})
    p = g.add_node("ui.panel", 340, 360, {"content": "Need help? Open a ticket.",
                                           "buttons": "Open ticket | ticket_open | primary | 🎫"})
    ack = g.add_node("action.reply", 660, 360, {"content": "Panel posted.", "ephemeral": True})
    g.connect(t2.id, "next", p.id, "in"); g.connect(p.id, "next", ack.id, "in")
    c = g.add_node("trigger.component", 40, 640, {"custom_id": "ticket_open", "kind": "button"})
    r = g.add_node("action.reply", 340, 640, {"content": "🎫 {user.mention}, a moderator will be with you shortly.", "ephemeral": True})
    g.connect(c.id, "next", r.id, "in")
    return g


def rpg_starter() -> Graph:
    """Game: quests, gear, dungeons and profile cards sharing one wallet; /join picks a team."""
    g = Graph()
    g.settings.name = "RPG Starter"
    g.add_node("module.xp", 60, 40)
    g.add_node("module.quests", 60, 240)
    g.add_node("module.gear", 60, 440)
    g.add_node("module.dungeons", 60, 640)
    g.add_node("module.cards", 60, 840)
    t = g.add_node("trigger.slash", 440, 40, {"name": "join", "description": "Join a team", "params": "team:str"})
    s = g.add_node("game.set_team", 740, 40)
    r = g.add_node("action.reply", 1040, 40, {"content": "Welcome to **{team}**, {user.mention}!"})
    g.connect(t.id, "next", s.id, "in"); g.connect(t.id, "team", s.id, "team"); g.connect(s.id, "next", r.id, "in")
    return g


def birthday_bit() -> Graph:
    """Birthdays & Anniversaries: one module does the work; a small flow adds a /party guide and a cake picker."""
    g = Graph()
    g.settings.name = "Birthday Bit"
    g.settings.status_text = "🎂 /birthday set"
    g.add_node("module.birthdays", 60, 60)
    t = g.add_node("trigger.slash", 60, 320, {"name": "party", "description": "Read how birthdays work here"})
    tabs = g.add_node("ui.tabs", 380, 320, {"tabs": "## Set yours\nUse **/birthday set** with MM-DD. Add your server join date with **/anniversary set**.\n"
                                                   "## Upcoming\n**/birthday upcoming** and **/anniversary upcoming** show who is next.\n"
                                                   "## For admins\nRun **/birthday-config channel**, **role**, **time** and **timezone** once. "
                                                   "**/birthday-config message** edits the default wording; {user} and {years} are filled in."})
    g.connect(t.id, "next", tabs.id, "in")
    t2 = g.add_node("trigger.slash", 60, 640, {"name": "cake", "description": "Pick a cake for someone's birthday"})
    ask = g.add_node("ui.ask_menu", 380, 640, {"content": "Which cake should we get?",
                                                "options": "Chocolate | chocolate | Rich and dark | 🍫\nStrawberry | strawberry | Light and sweet | 🍓\nVanilla | vanilla | Classic | 🍰",
                                                "placeholder": "Pick a cake"})
    fmt = g.add_node("data.format", 700, 860, {"template": "🎂 One **{cake}** cake, coming up!"})
    rep = g.add_node("action.reply", 1020, 640)
    g.connect(t2.id, "next", ask.id, "in"); g.connect(ask.id, "next", rep.id, "in")
    g.connect(ask.id, "value", fmt.id, "cake"); g.connect(fmt.id, "text", rep.id, "content")
    return g


def faction_wars() -> Graph:
    """Factions score with phrases, fight bosses together, duel in the arena, earn quests and show cards."""
    g = Graph()
    g.settings.name = "Faction Wars"
    g.settings.status_text = "/faction join"
    g.add_node("module.scoring", 60, 40, {"side_names_list": "Sunwardens, Moonwardens"})
    g.add_node("module.bosses", 60, 240)
    g.add_node("module.arena", 60, 440)
    g.add_node("module.gear", 60, 640)
    g.add_node("module.quests", 60, 840)
    g.add_node("module.cards", 60, 1040)
    t = g.add_node("trigger.slash", 440, 40, {"name": "summon", "description": "Summon the boss (admins)", "require_permission": "manage_guild",
                                               "params": "boss:str"})
    b = g.add_node("game.boss_start", 740, 40)
    r = g.add_node("action.reply", 1040, 40, {"content": "The boss has been summoned.", "ephemeral": True})
    g.connect(t.id, "next", b.id, "in"); g.connect(t.id, "boss", b.id, "boss"); g.connect(b.id, "next", r.id, "in")
    t2 = g.add_node("trigger.slash", 440, 300, {"name": "fighting", "description": "Is a boss running?"})
    act = g.add_node("game.boss_active", 740, 300)
    f1 = g.add_node("data.format", 1040, 520, {"template": "⚔️ **{name}** is alive with {hp} HP left — type your faction's phrases!"})
    yes = g.add_node("action.reply", 1340, 300)
    no = g.add_node("action.reply", 1040, 300, {"content": "No boss right now.", "ephemeral": True})
    g.connect(t2.id, "next", act.id, "in"); g.connect(act.id, "active", yes.id, "in"); g.connect(act.id, "idle", no.id, "in")
    g.connect(act.id, "name", f1.id, "name"); g.connect(act.id, "hp", f1.id, "hp"); g.connect(f1.id, "text", yes.id, "content")
    return g


def _one_module(name: str, *types: str, status: str = "") -> Graph:
    g = Graph()
    g.settings.name = name
    g.settings.status_text = status
    for i, t in enumerate(types):
        g.add_node(t, 60, 60 + 200 * i)
    return g


def event_planner() -> Graph:
    return _one_module("Event Planner", "module.event_scheduler", "module.task_lists", status="/eventcreate")


def emote_bot() -> Graph:
    return _one_module("Emote Library", "module.emotes", status="/emotes")


def router_bot() -> Graph:
    return _one_module("Post Router", "module.post_router", status="/router sethub")


def clan_bot() -> Graph:
    return _one_module("Clan Roster", "module.game_profiles", "module.role_brackets", status="/bracket list")


EXAMPLES = {
    "Hello (simplest bot)": hello,
    "Coin flip (data wiring)": coinflip,
    "Mod tools (permission branch)": mod_timeout,
    "Welcome + auto role": welcome,
    "Use counter (storage)": command_counter,
    "Anonymous posting (module)": anon_posting,
    "Emote adder (application emojis)": emote_adder,
    "Community (XP + UID modules)": community,
    "Bot + web dashboard (discord.js)": dashboard,
    "Economy (daily, balance, richest)": daily_reward,
    "Signup form (modal + confirm)": signup_form,
    "Server guide (tabs, panel, buttons)": server_guide,
    "RPG starter (quests, gear, dungeons, cards)": rpg_starter,
    "Birthday Bit (birthdays & anniversaries)": birthday_bit,
    "Event planner (spawns, countdowns, events, tasks)": event_planner,
    "Emote library (image emotes)": emote_bot,
    "Post router (hub channel to destinations)": router_bot,
    "Clan roster (game profiles + level brackets)": clan_bot,
    "Faction wars (scoring, bosses, arena, quests)": faction_wars,
}


# ---- the Bot Wizard's ready-to-run examples (see example_bots.EXAMPLE_INFO for their descriptions)
from . import example_bots as _eb   # noqa: E402

for _info in _eb.EXAMPLE_INFO:
    EXAMPLES[f"{_info['title']} ({_info['category']})"] = getattr(_eb, _info["fn"])


def resolve(name: str):
    """Example builder by function name (hello, welcome_bot, …) — used by the Bot Wizard and the lessons."""
    fn = globals().get(name) or getattr(_eb, name, None)
    return fn if callable(fn) else None
