"""Web dashboard nodes (the discord.js side of Bot Forge Studio).

The bot is discord.py. When the graph contains a **Web Dashboard** node, Bot Forge Studio also generates a
small Node.js app (``dashboard/``) that the bot starts and supervises. The dashboard shares the bot's
``data/bot.db`` and ``status.json`` and talks to Discord over REST, so it never needs a second
gateway connection.

* **Web Dashboard** (module): Discord OAuth2 login, server picker, and pages. With *Translate my bot*
  on, pages are derived from the graph: a Commands page for every slash command, a leaderboard for
  every Add-to-Counter table, a table for every Save/Get Value namespace, and whatever a module
  (XP, UID registry, plugins) declares via ``dash_pages``. You can add your own pages by hand.
* **Web Route** (trigger): your own HTTP endpoint under ``/api/custom/…`` served by the dashboard.
  Chain storage / flow / data / JSON nodes and finish with **Respond JSON**.
"""
from __future__ import annotations

import re

from .codegen import GenError
from .codegen_js import jsstr, literal_js
from .model import NodeSpec, Prop, get_spec, register
from .nodes import D, EX

EXPRESS = "express@^4.21.0"
DISCORDJS = "discord.js@^14.17.0"
WS = "ws@^8.18.0"
DASH_ENV = (
    ("DISCORD_CLIENT_SECRET", "", "Dashboard login: OAuth2 client secret (Developer Portal > OAuth2)"),
    ("DASHBOARD_URL", "http://localhost:3000", "Public URL of the dashboard. Add <URL>/auth/callback as a redirect in the Developer Portal > OAuth2"),
    ("DASHBOARD_HOST", "127.0.0.1", "Interface to listen on (0.0.0.0 = all; put HTTPS in front when public)"),
    ("DASHBOARD_PORT", "3000", "Port the dashboard listens on"),
)

ROUTE_PATH = re.compile(r"^/api/custom(/[A-Za-z0-9_\-:.]+)*$")
NS = re.compile(r"^[A-Za-z0-9_:\-]{1,64}$")
PAGE_TYPES = ("overview", "commands", "leaderboard", "kv", "announce", "api", "text", "site")

DEFAULT_PAGES = ""   # extra, hand-written pages; the rest comes from "Translate my bot"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "page"


def parse_pages(text: str) -> list[dict]:
    """'Title | type | argument' lines -> page descriptors.

    leaderboard / kv take a table name and optional flags: ``global`` (not per server — bot owner only)
    and, for kv, ``edit`` (allow changes from the dashboard).
    """
    pages = []
    for line in str(text or "").splitlines():
        parts = [p.strip() for p in line.split("|", 2)]
        if not parts[0]:
            continue
        typ = (parts[1].lower() if len(parts) > 1 and parts[1] else "overview")
        arg = parts[2] if len(parts) > 2 else ""
        if typ not in PAGE_TYPES:
            continue
        page = {"title": parts[0][:40], "type": typ, "arg": arg, "scope": "guild", "edit": False}
        if typ in ("leaderboard", "kv"):
            toks = arg.split()
            if not toks or not NS.match(toks[0]):
                continue
            page.update(arg=toks[0], scope="global" if "global" in toks[1:] else "guild", edit=typ == "kv" and "edit" in toks[1:])
        elif typ == "api":
            if not ROUTE_PATH.match(arg.split("?", 1)[0]) or ":" in arg:
                continue
        elif typ == "text":
            page["arg"] = arg[:2000]
        elif typ == "site":
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", arg.strip()):
                continue
            page["arg"] = arg.strip()
        pages.append(page)
    return pages


def finish_pages(pages: list[dict]) -> tuple[list[dict], dict]:
    """Assign ids, drop duplicates and build the allow-lists the dashboard server enforces."""
    out, seen = [], set()
    allow = {"leaderboards": [], "kv": [], "announce": False}
    for p in pages:
        key = (p["type"], p.get("arg", "") if p["type"] in ("leaderboard", "kv", "api") else p["title"])
        if key in seen:
            continue
        seen.add(key)
        p = dict(p)
        p["id"] = f"{_slug(p['title'])}-{len(out)}"
        if p["type"] == "leaderboard":
            allow["leaderboards"].append({"ns": p["arg"], "scope": p["scope"]})
        elif p["type"] == "kv":
            allow["kv"].append({"ns": p["arg"], "scope": p["scope"], "edit": bool(p.get("edit"))})
        elif p["type"] == "announce":
            allow["announce"] = True
        p["ownerOnly"] = p["scope"] == "global" and p["type"] in ("leaderboard", "kv")
        out.append(p)
    return out, allow


# ---------------------------------------------------------------------------------------
# Web Dashboard module
# ---------------------------------------------------------------------------------------

register(NodeSpec(
    "module.dashboard", "Web Dashboard", "Module", "module",
    props=[
        Prop("title", "Dashboard title", "str", "Bot Dashboard"),
        Prop("accent", "Accent colour (hex)", "str", "66c0f4"),
        Prop("translate", "Translate my bot into pages", "bool", True,
             help="Adds a Commands page, a leaderboard per Add-to-Counter table, a table per Save/Get Value table, and the "
                  "pages modules (XP, UID registry …) declare. Tables whose key does not start with {guild.id} are not per "
                  "server, so they are shown to the bot owner only."),
        Prop("announce", "Announce page (post as the bot)", "bool", True),
        Prop("pages", "Extra pages (one per line)", "text", DEFAULT_PAGES,
             help="Title | type | argument.  Types: overview · commands · leaderboard | table · kv | table [edit] [global] · "
                  "announce · api | /api/custom/path (a Web Route's JSON) · text | words · site | page-name (an HTML Page node)"),
        Prop("restrict_user_ids", "Only these user IDs may log in (blank = any server manager)", "text", ""),
    ],
    requires_js=(DISCORDJS, EXPRESS), env=DASH_ENV, web_only=True,
    description="A web dashboard for this bot, built with discord.js and Express and run by the bot. Discord login; only members "
                "with Manage Server in a server that has the bot can use it."))


# modules tell the dashboard what to show (data-driven, so plugins can do the same)
def _xp_pages(node):
    ns = str(node.prop_value("namespace") or "xp")
    return [{"title": "XP leaderboard", "type": "leaderboard", "arg": ns, "scope": "guild"}] if NS.match(ns) else []


def _uid_pages(node):
    games = [g.strip() for g in re.split(r"[,|\n]", str(node.prop_value("games_list") or "")) if g.strip()]
    return [{"title": f"UIDs: {g}"[:40], "type": "kv", "arg": f"uid:{g.lower()}", "scope": "guild", "edit": False}
            for g in games if NS.match(f"uid:{g.lower()}")]


for _tid, _fn in (("module.xp", _xp_pages), ("module.uid_registry", _uid_pages)):
    _s = get_spec(_tid)
    if _s is not None:
        _s.dash_pages = _fn


# ---------------------------------------------------------------------------------------
# Web Route
# ---------------------------------------------------------------------------------------

def gen_web_route(ctx):
    method = str(ctx.prop("method") or "GET").lower()
    if method not in ("get", "post", "put", "delete", "patch"):
        raise GenError(f"unsupported method {method!r}")
    path = str(ctx.prop("path") or "").strip()
    if not ROUTE_PATH.match(path):
        raise GenError("path must start with /api/custom (letters, numbers, - _ . and :params), e.g. /api/custom/stats")
    login, admin, owner = bool(ctx.prop("login")), bool(ctx.prop("guild_admin")), bool(ctx.prop("owner_only"))
    ctx.caps |= {"web"}
    ctx.outs = {
        "user": "req.user", "guild_id": "bs.guildId(req)", "guild": "(await bs.guild(req))",
        "query": "req.query", "body": "(req.body ?? {})", "params": "req.params",
    }
    ctx.scope = ctx.outs
    ctx.js_kind, ctx.signature = "routes", "req, res"
    ctx.js_head = [f"method: {jsstr(method)}, path: {jsstr(path)}, login: {literal_js(login or admin or owner, 'bool')}, "
                   f"guildAdmin: {literal_js(admin, 'bool')}, ownerOnly: {literal_js(owner, 'bool')},"]


register(NodeSpec(
    "web.route", "Web Route", "Trigger", "trigger",
    outputs=[EX(), D("user", "user"), D("guild_id", "guild"), D("guild", "guild"), D("query"), D("body"), D("params")],
    props=[
        Prop("method", "Method", "choice", "GET", choices=("GET", "POST", "PUT", "DELETE", "PATCH")),
        Prop("path", "Path", "str", "/api/custom/hello",
             help="Must start with /api/custom. Use :name for URL parameters (they appear in 'params')."),
        Prop("login", "Require dashboard login", "bool", True),
        Prop("guild_admin", "Caller must manage the server given by ?guild_id=", "bool", False,
             help="The dashboard's API pages add ?guild_id= automatically. Implies login."),
        Prop("owner_only", "Bot owner only", "bool", False, help="Implies login."),
    ],
    gen_js=gen_web_route, requires_js=(DISCORDJS, EXPRESS), env=DASH_ENV[1:], web_only=True,
    description="Your own HTTP endpoint, served by the dashboard. Wire nodes after it and finish with Respond JSON."))


def gen_json(ctx):
    if not ctx.has("web"):
        ctx.warn("Respond JSON only works inside a Web Route flow")
        ctx.emit("// (skipped)")
        return
    status = ctx.val("status")
    edge = ctx.g.incoming(ctx.node.id, "data")
    if edge is not None:
        data = ctx.expr_of(edge.src, edge.src_port)
    else:
        msg = ctx.val("message")
        data = "{ ok: true }" if msg == '""' else f"{{ ok: true, message: {msg} }}"
    ctx.emit(f"res.status(bs.status({status})).json({data});")


register(NodeSpec(
    "web.json", "Respond JSON", "Action", "action",
    inputs=[EX("in"), D("data")], outputs=[EX()],
    props=[Prop("status", "HTTP status", "int", 200),
           Prop("message", "Message (used when no data is wired)", "text", "", fmt=True)],
    gen_js=gen_json, web_only=True,
    description="Send a JSON response. Wire a 'Build JSON Object' (or any data) into 'data'."))


def gen_redirect(ctx):
    if not ctx.has("web"):
        ctx.warn("Redirect only works inside a Web Route flow")
        ctx.emit("// (skipped)")
        return
    ctx.emit(f"res.redirect(bs.safeUrl({ctx.val('url')}));")


register(NodeSpec(
    "web.redirect", "Redirect", "Action", "action",
    inputs=[EX("in"), D("url", "str")], outputs=[EX()],
    props=[Prop("url", "URL (path or https://…)", "str", "/", fmt=True, port=True)],
    gen_js=gen_redirect, web_only=True,
    description="Redirect the browser to another page."))
