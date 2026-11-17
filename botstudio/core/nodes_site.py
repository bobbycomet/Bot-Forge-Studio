"""Web Development nodes — the browser side of a dashboard, plus the two-way bridge to the bot.

Three execution environments exist in a Bot Forge Studio project, and the node categories keep them apart:

  Discord bot (Python)   Trigger / Action / Flow / Data / Game … nodes       -> cogs/*.py        (discord.py)
  Dashboard server (JS)  Web Route + "Server JavaScript"                     -> dashboard/routes (Node + Express)
  Browser (JS)           Web: HTML Page, CSS Stylesheet, Browser JavaScript  -> dashboard/web/public/site/*

Browser code never sees the bot token and cannot call discord.js; it talks to the dashboard server with fetch()
and WebSockets, and the server talks to the bot through the shared database (events in, live updates out).
"""
from __future__ import annotations

from .codegen import GenError
from .codegen_js import jsstr
from .model import NodeSpec, Prop, register
from .nodes import D, EX

HTML_DEFAULT = '''<main class="wrap">
  <h1>Live bot status</h1>
  <p id="status" class="muted">Connecting…</p>
  <section class="card">
    <h2>Say something as the bot</h2>
    <form id="announce-form">
      <input id="msg" placeholder="Message" maxlength="200" required>
      <button class="btn primary" type="submit">Send</button>
    </form>
    <p id="result" class="muted"></p>
  </section>
</main>
'''

CSS_DEFAULT = '''/* Styles for this page. The dashboard theme (colours, fonts) is already loaded; add yours on top. */
.wrap { max-width: 720px; margin: 0 auto; padding: 24px 16px; }
.card { background: var(--panel); border: 1px solid var(--raised); border-radius: 6px; padding: 16px; margin-top: 16px; }
#announce-form { display: flex; gap: 8px; flex-wrap: wrap; }
#announce-form input { flex: 1 1 220px; }
@media (max-width: 480px) { .wrap { padding: 12px 8px; } }
'''

JS_DEFAULT = '''// Browser JavaScript: runs in the visitor's browser. No discord.js, no require(), no secrets.
// BotStudio.api(path) calls your dashboard server (it adds the session cookie for you);
// BotStudio.live(handler) receives live updates over a WebSocket.
const $ = (id) => document.getElementById(id);

BotStudio.live((msg) => {
  if (msg.type === 'status') {
    const s = msg.data;
    $('status').textContent = s.online ? `Bot online · ${s.latency_ms ?? '—'} ms · ${s.guilds ?? '?'} servers` : 'Bot offline';
  }
});

$('announce-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    const guild = BotStudio.guild();
    const channels = await BotStudio.api(`/api/guilds/${guild}/channels`);
    await BotStudio.api(`/api/guilds/${guild}/send`, { method: 'POST', body: { channel_id: channels[0].id, content: $('msg').value } });
    $('result').textContent = 'Sent.';
    $('msg').value = '';
  } catch (err) {
    $('result').textContent = err.message;   // the server's error text, never a stack trace
  }
});
'''

SITE_ENV = "Runs in the visitor's browser"


register(NodeSpec(
    "web.html", "HTML Page", "Web", "module",
    inputs=[EX("assets", "CSS / JS")],
    props=[Prop("title", "Page title", "str", "My dashboard page"),
           Prop("slug", "File name (letters, numbers, dashes)", "str", "status", help="The page is served at /site/<name>.html"),
           Prop("nav_title", "Dashboard menu entry (blank = not listed)", "str", "Status"),
           Prop("login", "Visitors must be logged in", "bool", True,
                help="Strongly recommended. Even when off, API calls from the page are still protected by their own checks."),
           Prop("theme", "Use the dashboard theme", "bool", True),
           Prop("back_link", "Show a '← Dashboard' link", "bool", True),
           Prop("code", "HTML (the page body)", "text", HTML_DEFAULT,
                help="Write what goes inside <body>. Studio wraps it in a full document and links the connected CSS and JavaScript nodes.")],
    web_only=True,
    description="A web page for your dashboard (HTML, browser environment). Write the body; connect CSS and Browser JavaScript nodes to its "
                "'CSS / JS' input. Served at /site/<name>.html behind dashboard login."))


register(NodeSpec(
    "web.css", "CSS Stylesheet", "Web", "module",
    outputs=[EX("attach", "attach to page")],
    props=[Prop("name", "Section name", "str", "page", help="Letters, numbers and dashes. Used for the file name and comments."),
           Prop("code", "CSS", "text", CSS_DEFAULT)],
    web_only=True,
    description="Styles for an HTML Page (browser environment): selectors, flexbox, grid, responsive media queries, animations. "
                "Split styling into several CSS nodes (layout, theme, components) and attach them in order."))


register(NodeSpec(
    "web.js", "Browser JavaScript", "Web", "module",
    outputs=[EX("attach", "attach to page")],
    props=[Prop("name", "Script name", "str", "app", help="Letters, numbers and dashes. Used for the file name."),
           Prop("code", "JavaScript (runs in the browser)", "text", JS_DEFAULT)],
    web_only=True,
    description="Frontend JavaScript for an HTML Page — runs in the visitor's BROWSER (DOM, events, fetch, WebSocket). "
                "It is not discord.js and not the dashboard server: use a Web Route (server) for anything privileged."))


# ----------------------------------------------------------------------------------- server-side bridge nodes (Web Route flows)

def gen_broadcast(ctx):
    if not ctx.has("web"):
        ctx.warn("Broadcast Live Update only works inside a Web Route flow")
        ctx.emit("// (skipped)")
        return
    edge = ctx.g.incoming(ctx.node.id, "data")
    data = ctx.expr_of(edge.src, edge.src_port) if edge else "{}"
    ctx.emit(f"bs.broadcast({ctx.val('event')}, {data}, bs.guildId(req));")


register(NodeSpec(
    "web.broadcast", "Broadcast Live Update", "Action", "action",
    inputs=[EX("in"), D("event", "str"), D("data")], outputs=[EX()],
    props=[Prop("event", "Event name", "str", "update", port=True)],
    gen_js=gen_broadcast, web_only=True,
    description="Pushes a live update over the WebSocket to every dashboard page watching this server. Pages receive it in BotStudio.live()."))


def gen_emit_bot(ctx):
    if not ctx.has("web"):
        ctx.warn("Send Event to Bot only works inside a Web Route flow")
        ctx.emit("// (skipped)")
        return
    edge = ctx.g.incoming(ctx.node.id, "data")
    data = ctx.expr_of(edge.src, edge.src_port) if edge else "{}"
    ctx.emit(f"await bs.emitBot({ctx.val('name')}, {data}, bs.guildId(req), req.user ? req.user.id : null);")


register(NodeSpec(
    "web.emit_bot", "Send Event to Bot", "Action", "action",
    inputs=[EX("in"), D("name", "str"), D("data")], outputs=[EX()],
    props=[Prop("name", "Event name", "str", "dashboard_action", port=True,
                help="The bot reacts with an 'On Dashboard Event' trigger of the same name.")],
    gen_js=gen_emit_bot, web_only=True,
    description="Hands an event to the running Discord bot (through the shared database) — the safe way for a dashboard to make the bot do "
                "something. Validate and authorise in the Web Route first; the browser never talks to the bot directly."))


# ----------------------------------------------------------------------------------- bot-side bridge nodes (discord.py)

def gen_dashboard_event(ctx):
    name = str(ctx.prop("name") or "").strip()
    if not name:
        raise GenError("On Dashboard Event needs an event name")
    ctx.decorators = ['@commands.Cog.listener("on_bs_dashboard_event")']
    ctx.signature = "self, _event: str, data: dict, guild_id: str, user_id: str"
    ctx.preamble.append(f"if _event != {name!r}: return")
    ctx.preamble.append("guild = self.bot.get_guild(int(guild_id)) if guild_id else None")
    ctx.preamble.append("user = guild.get_member(int(user_id)) if guild and user_id else None")
    ctx.outs = {"data": "data", "guild": "guild", "user": "user", "user_id": "user_id", "guild_id": "guild_id"}
    ctx.scope = {"guild": "guild", "user": "user"}


register(NodeSpec(
    "trigger.dashboard_event", "On Dashboard Event", "Trigger", "trigger",
    outputs=[EX(), D("data"), D("user", "user"), D("guild", "guild"), D("user_id", "str"), D("guild_id", "str")],
    props=[Prop("name", "Event name", "str", "dashboard_action", help="Matches the name used by 'Send Event to Bot' in a Web Route.")],
    gen=gen_dashboard_event,
    description="Runs when the web dashboard sends this event to the bot. 'data' is the validated payload from the Web Route."))


def gen_live_push(ctx):
    edge = ctx.g.incoming(ctx.node.id, "data")
    data = ctx.expr_of(edge.src, edge.src_port) if edge else "{}"
    ctx.emit(f"bs.live_emit({ctx.scope.get('guild', 'None')}, {ctx.val('event')}, {data})")


register(NodeSpec(
    "action.live_push", "Push to Dashboard", "Action", "action",
    inputs=[EX("in"), D("event", "str"), D("data")], outputs=[EX()],
    props=[Prop("event", "Event name", "str", "update", fmt=True, port=True)],
    gen=gen_live_push,
    description="Sends a live event from the bot to dashboard pages watching this server (through data/live_events.jsonl and the dashboard's WebSocket)."))
