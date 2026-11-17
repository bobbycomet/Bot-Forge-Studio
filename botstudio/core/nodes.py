"""Built-in node library. Each node is a pre-built chunk of discord.py code."""
from __future__ import annotations

import keyword
import re

from .codegen import GenError, IDENT, literal, render_template
from .model import NodeSpec, Port, Prop, register

EX = lambda name="next", label="": Port(name, "exec", label)          # noqa: E731
D = lambda name, dtype="any", label="": Port(name, "data", label, dtype)  # noqa: E731

PERMS = ("manage_guild", "administrator", "manage_messages", "manage_roles", "manage_channels",
         "kick_members", "ban_members", "moderate_members")

# ---------------------------------------------------------------------------------
# slash command parameter mini-language:  "count:int=5, target:user, reason:str?"
# ---------------------------------------------------------------------------------
RESERVED = {"user", "channel", "guild", "interaction", "self", "next", "bs", "asyncio", "discord"}
PARAM_TYPES = {
    "str": "str", "int": "int", "float": "float", "bool": "bool",
    "user": "discord.Member", "channel": "discord.TextChannel", "role": "discord.Role",
    "attachment": "discord.Attachment",
}


def parse_params(text: str) -> list[tuple[str, str, str | None, bool]]:
    """-> [(name, type, default_literal_or_None, optional)] — required params first."""
    out = []
    for chunk in (text or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        optional = chunk.endswith("?")
        chunk = chunk.rstrip("?").strip()
        default = None
        if "=" in chunk:
            chunk, default = [x.strip() for x in chunk.split("=", 1)]
            optional = True
        name, _, typ = chunk.partition(":")
        name, typ = name.strip().lower(), (typ.strip().lower() or "str")
        if not IDENT.match(name) or typ not in PARAM_TYPES or name in RESERVED or keyword.iskeyword(name):
            continue
        if any(o[0] == name for o in out):
            continue
        out.append((name, typ, default, optional))
    return sorted(out, key=lambda p: p[3])


def _param_ports(props):
    ports = [D(n, t) for n, t, _, _ in parse_params(props.get("params", ""))]
    return [], ports


def _msg_props(include_ephemeral=False):
    props = [
        Prop("content", "Message", "text", "", fmt=True, port=True,
             help="Supports {user.mention}, {channel.name}, {guild.name} and command parameters."),
        Prop("embed_title", "Embed title", "str", "", fmt=True, port=True),
        Prop("embed_description", "Embed text", "text", "", fmt=True, port=True),
        Prop("embed_color", "Embed color", "str", "5865F2", help="Hex, e.g. 5865F2"),
        Prop("embed_footer", "Embed footer", "str", "", fmt=True),
    ]
    if include_ephemeral:
        props.append(Prop("ephemeral", "Only visible to the user", "bool", False))
    return props


def _msg_ports():
    return [D("content", "str"), D("embed_title", "str"), D("embed_description", "str")]


# =================================================================================
# TRIGGERS
# =================================================================================

def gen_slash(ctx):
    n = ctx.node
    name = str(ctx.prop("name") or "").strip().lower()
    if not IDENT.match(name):
        raise GenError(f"command name '{name}' must be lowercase letters/numbers/underscore (max 32)")
    desc = str(ctx.prop("description") or "").strip()[:100] or "No description"
    params = parse_params(ctx.prop("params"))
    ctx.caps |= {"interaction", "guild_ctx"}
    ctx.outs = {"user": "interaction.user", "channel": "interaction.channel",
                "guild": "interaction.guild", "interaction": "interaction"}
    sig = ["self", "interaction: discord.Interaction"]
    for pname, typ, default, optional in params:
        ann = PARAM_TYPES[typ]
        if optional:
            dflt = "None" if default in (None, "") else literal(default, {"int": "int", "float": "float", "bool": "bool"}.get(typ, "str"))
            ann = f"{ann} | None" if dflt == "None" else ann
            sig.append(f"{pname}: {ann} = {dflt}")
        else:
            sig.append(f"{pname}: {ann}")
        ctx.outs[pname] = pname
    ctx.scope = ctx.outs
    ctx.signature = ", ".join(sig)
    ctx.decorators = [f"@app_commands.command(name={name!r}, description={desc!r})"]
    perm = ctx.prop("require_permission")
    if perm and perm != "none":
        ctx.decorators.append(f"@app_commands.default_permissions({perm}=True)")
        ctx.decorators.append(f"@app_commands.checks.has_permissions({perm}=True)")
    if ctx.prop("guild_only"):
        ctx.decorators.append("@app_commands.guild_only()")
    cd = float(ctx.prop("cooldown") or 0)
    if cd > 0:
        ctx.decorators.append(f"@app_commands.checks.cooldown(1, {cd})")
    ctx.commands.append(name)
    if ctx.prop("defer"):
        ctx.preamble.append(f"await interaction.response.defer(ephemeral={bool(ctx.prop('defer_ephemeral'))})")


register(NodeSpec(
    "trigger.slash", "Slash Command", "Trigger", "trigger",
    outputs=[EX(), D("user", "user"), D("channel", "channel"), D("guild", "guild")],
    props=[
        Prop("name", "Command name", "str", "hello"),
        Prop("description", "Description", "str", "Say hello"),
        Prop("params", "Parameters", "str", "",
             help="Comma list: name:type, e.g.  amount:int=5, target:user, note:str?  "
                  "Types: str int float bool user channel role attachment. '?' = optional."),
        Prop("require_permission", "Required permission", "choice", "none", choices=("none",) + PERMS),
        Prop("cooldown", "Cooldown (seconds)", "float", 0),
        Prop("guild_only", "Server only", "bool", True),
        Prop("defer", "Defer response (slow commands)", "bool", False),
        Prop("defer_ephemeral", "Deferred reply is private", "bool", False),
    ],
    gen=gen_slash, dynamic_ports=_param_ports,
    description="A /slash command. Its parameters appear as output ports.",
))


def gen_on_message(ctx):
    ctx.caps |= {"message", "guild_ctx"}
    ctx.outs = {"message": "message", "author": "message.author", "user": "message.author",
                "content": "message.content", "channel": "message.channel", "guild": "message.guild"}
    ctx.scope = ctx.outs
    ctx.decorators = ['@commands.Cog.listener("on_message")']
    ctx.signature = "self, message: discord.Message"
    p = ctx.preamble
    if ctx.prop("ignore_bots"):
        p.append("if message.author.bot: return")
    p.append("if message.guild is None: return")
    cid = str(ctx.prop("only_channel") or "").strip()
    if cid.isdigit():
        p.append(f"if message.channel.id != {int(cid)}: return")
    sw = str(ctx.prop("starts_with") or "")
    if sw:
        p.append(f"if not message.content.lower().startswith({sw.lower()!r}): return")
    ct = str(ctx.prop("contains") or "")
    if ct:
        p.append(f"if {ct.lower()!r} not in message.content.lower(): return")


register(NodeSpec(
    "trigger.message", "On Message", "Trigger", "trigger",
    outputs=[EX(), D("message"), D("author", "user"), D("content", "str"), D("channel", "channel"), D("guild", "guild")],
    props=[
        Prop("ignore_bots", "Ignore bots", "bool", True),
        Prop("only_channel", "Only in channel ID", "str", ""),
        Prop("starts_with", "Starts with", "str", ""),
        Prop("contains", "Contains text", "str", ""),
    ],
    gen=gen_on_message, intents=("message_content",),
    description="Runs for each message that passes the filters. Needs the Message Content intent.",
))


def gen_member_join(ctx):
    ctx.caps |= {"member", "guild_ctx"}
    ctx.outs = {"member": "member", "user": "member", "guild": "member.guild"}
    ctx.scope = ctx.outs
    ctx.decorators = ['@commands.Cog.listener("on_member_join")']
    ctx.signature = "self, member: discord.Member"


register(NodeSpec(
    "trigger.member_join", "Member Joined", "Trigger", "trigger",
    outputs=[EX(), D("member", "user"), D("guild", "guild")],
    gen=gen_member_join, intents=("members",),
    description="Fires when someone joins the server. Needs the Server Members intent.",
))


def gen_member_leave(ctx):
    ctx.caps |= {"member", "guild_ctx"}
    ctx.outs = {"member": "member", "user": "member", "guild": "member.guild"}
    ctx.scope = ctx.outs
    ctx.decorators = ['@commands.Cog.listener("on_member_remove")']
    ctx.signature = "self, member: discord.Member"


register(NodeSpec(
    "trigger.member_leave", "Member Left", "Trigger", "trigger",
    outputs=[EX(), D("member", "user"), D("guild", "guild")],
    gen=gen_member_leave, intents=("members",),
    description="Fires when someone leaves or is removed.",
))


def gen_reaction(ctx):
    ctx.caps |= {"reaction"}
    ctx.outs = {"user_id": "payload.user_id", "message_id": "payload.message_id",
                "channel_id": "payload.channel_id", "emoji": "str(payload.emoji)",
                "guild_id": "payload.guild_id"}
    ctx.scope = ctx.outs
    ctx.decorators = ['@commands.Cog.listener("on_raw_reaction_add")']
    ctx.signature = "self, payload: discord.RawReactionActionEvent"
    ctx.preamble.append("if payload.user_id == self.bot.user.id: return")
    only = str(ctx.prop("emoji_filter") or "").strip()
    if only:
        ctx.preamble.append(f"if str(payload.emoji) != {only!r}: return")


register(NodeSpec(
    "trigger.reaction", "Reaction Added", "Trigger", "trigger",
    outputs=[EX(), D("user_id", "int"), D("message_id", "int"), D("channel_id", "int"),
             D("emoji", "str"), D("guild_id", "int")],
    props=[Prop("emoji_filter", "Only this emoji", "str", "")],
    gen=gen_reaction, description="Fires when someone adds a reaction (works on uncached messages).",
))


def gen_schedule(ctx):
    ctx.caps |= {"scheduled"}
    ctx.outs = {}
    ctx.scope = {}
    mins = max(float(ctx.prop("every_minutes") or 60), 0.1)
    ctx.name = f"loop_{ctx.node.id}"
    ctx.decorators = [f"@tasks.loop(minutes={mins})"]
    ctx.signature = "self"
    ctx.class_lines += [f"@{ctx.name}.before_loop", f"async def _before_{ctx.name}(self):",
                        "    await self.bot.wait_until_ready()", ""]
    ctx.on_load.append(f"self.{ctx.name}.start()")
    ctx.on_unload.append(f"self.{ctx.name}.cancel()")


register(NodeSpec(
    "trigger.schedule", "Every N Minutes", "Trigger", "trigger",
    outputs=[EX()],
    props=[Prop("every_minutes", "Every (minutes)", "float", 60)],
    gen=gen_schedule, description="Runs on a timer. Use channel IDs to say where to post.",
))


def gen_ready(ctx):
    ctx.caps |= {"startup"}
    ctx.outs, ctx.scope = {}, {}
    ctx.decorators = ['@commands.Cog.listener("on_ready")']
    ctx.signature = "self"


register(NodeSpec("trigger.ready", "Bot Ready", "Trigger", "trigger", outputs=[EX()], gen=gen_ready,
                  description="Runs each time the bot connects."))


# =================================================================================
# ACTIONS
# =================================================================================

def gen_reply(ctx):
    content, emb = ctx.val("content"), ctx.embed_expr()
    if ctx.has("interaction"):
        ctx.emit(f"await bs.respond(interaction, {content}, {emb}, ephemeral={ctx.val('ephemeral')})")
    elif ctx.has("message"):
        ctx.emit(f"await message.reply(content={content} or None, embed={emb}, mention_author=False)")
    else:
        ctx.warn("Reply needs a Slash Command or On Message trigger — use 'Send to Channel' instead")
        ctx.emit("pass")


register(NodeSpec(
    "action.reply", "Reply", "Action", "action",
    inputs=[EX("in")] + _msg_ports(), outputs=[EX()], props=_msg_props(True), gen=gen_reply,
    description="Reply to the command or message (optionally as an embed).",
))


def gen_send_channel(ctx):
    if ctx.is_blank("channel"):
        if "channel" in ctx.scope:
            target = ctx.scope["channel"]
        else:
            ctx.warn("Send to Channel has no channel ID and no current channel")
            target = "None"
    else:
        raw = ctx.val("channel")
        target = raw
    ctx.emit(f"{ctx.var('sent')} = await bs.send(self.bot, {target}, {ctx.val('content')}, {ctx.embed_expr()})")


register(NodeSpec(
    "action.send", "Send to Channel", "Action", "action",
    inputs=[EX("in"), D("channel", "channel")] + _msg_ports(),
    outputs=[EX(), D("sent", "message")],
    props=[Prop("channel", "Channel ID (blank = current)", "str", "", fmt=False)] + _msg_props(),
    gen=gen_send_channel, description="Post a message or embed in a channel.",
))


def gen_dm(ctx):
    user = ctx.val("user", ctx.default_user() or "None")
    ctx.emit(f"{ctx.var('ok')} = await bs.dm({user}, {ctx.val('content')}, {ctx.embed_expr()})")


register(NodeSpec(
    "action.dm", "Direct Message", "Action", "action",
    inputs=[EX("in"), D("user", "user")] + _msg_ports(),
    outputs=[EX(), D("ok", "bool")], props=_msg_props(), gen=gen_dm,
    description="DM a user. 'ok' is False if their DMs are closed.",
))


def _role_gen(add):
    def gen(ctx):
        user = ctx.val("user", ctx.default_user() or "None")
        guild = ctx.scope.get("guild", "None")
        ctx.emit(f"await bs.set_role({guild}, {user}, {ctx.val('role')}, add={add})")
    return gen


for _add, _id, _t in ((True, "action.add_role", "Add Role"), (False, "action.remove_role", "Remove Role")):
    register(NodeSpec(
        _id, _t, "Moderation", "action",
        inputs=[EX("in"), D("user", "user"), D("role", "str")], outputs=[EX()],
        props=[Prop("role", "Role (ID or name)", "str", "", port=True)], gen=_role_gen(_add),
        description=f"{_t} for a member (defaults to the triggering user).",
    ))


def _mod_gen(fn, extra=""):
    def gen(ctx):
        user = ctx.val("user", ctx.default_user() or "None")
        args = f"{user}, " + (f"{ctx.val('minutes')}, " if extra else "") + ctx.val("reason")
        ctx.emit(f"{ctx.var('ok')} = await bs.{fn}({args})")
    return gen


register(NodeSpec(
    "mod.timeout", "Timeout Member", "Moderation", "action",
    inputs=[EX("in"), D("user", "user"), D("minutes", "int"), D("reason", "str")],
    outputs=[EX(), D("ok", "bool")],
    props=[Prop("minutes", "Minutes", "float", 10, port=True), Prop("reason", "Reason", "str", "", fmt=True, port=True)],
    gen=_mod_gen("timeout", "m"), description="Temporarily mute a member."))
for _fn, _t in (("kick", "Kick Member"), ("ban", "Ban Member")):
    register(NodeSpec(
        f"mod.{_fn}", _t, "Moderation", "action",
        inputs=[EX("in"), D("user", "user"), D("reason", "str")], outputs=[EX(), D("ok", "bool")],
        props=[Prop("reason", "Reason", "str", "", fmt=True, port=True)], gen=_mod_gen(_fn),
        description=f"{_t}. Gate it behind a permission check."))


def gen_delete(ctx):
    msg = ctx.val("message", ctx.scope.get("message", "None"))
    ctx.emit(f"await bs.delete_message({msg})")


register(NodeSpec("mod.delete", "Delete Message", "Moderation", "action",
                  inputs=[EX("in"), D("message")], outputs=[EX()], gen=gen_delete,
                  description="Delete the triggering message (or a wired-in one)."))


def gen_react(ctx):
    msg = ctx.val("message", ctx.scope.get("message", "None"))
    ctx.emit(f"await {msg}.add_reaction({ctx.val('emoji')})")


register(NodeSpec("action.react", "Add Reaction", "Action", "action",
                  inputs=[EX("in"), D("message")], outputs=[EX()],
                  props=[Prop("emoji", "Emoji", "str", "👍")], gen=gen_react))


def gen_log(ctx):
    level = str(ctx.prop("level") or "info")
    ctx.emit(f"bs.log.{level}({ctx.val('text')})")


register(NodeSpec("action.log", "Log", "Action", "action",
                  inputs=[EX("in"), D("text", "str")], outputs=[EX()],
                  props=[Prop("text", "Text", "str", "", fmt=True, port=True),
                         Prop("level", "Level", "choice", "info", choices=("debug", "info", "warning", "error"))],
                  gen=gen_log, description="Write to bot.log / journal."))


def gen_http(ctx):
    v = ctx.var
    ctx.emit(f"{v('status')}, {v('text')} = await bs.http_get({ctx.val('url')})")
    ctx.emit(f"{v('json')} = bs.try_json({v('text')})")


register(NodeSpec("action.http_get", "HTTP GET", "Action", "action",
                  inputs=[EX("in"), D("url", "str")],
                  outputs=[EX(), D("status", "int"), D("text", "str"), D("json")],
                  props=[Prop("url", "URL", "str", "https://", fmt=True, port=True)],
                  requires=("aiohttp",), gen=gen_http,
                  description="Fetch a URL. 'json' is the parsed body when valid."))


def gen_emoji_add(ctx):
    img = ctx.val("image", ctx.scope.get("image") or ctx.scope.get("attachment") or "None")
    v = ctx.var
    ctx.emit(f"{v('emoji')}, {v('error')} = await bs.app_emoji_add(self.bot, {ctx.val('name')}, {img})")
    ctx.emit(f"{v('ok')} = {v('error')} == ''")


register(NodeSpec("action.app_emoji_add", "Add Application Emoji", "Action", "action",
                  inputs=[EX("in"), D("name", "str"), D("image", "attachment")],
                  outputs=[EX(), D("emoji", "str"), D("ok", "bool"), D("error", "str")],
                  props=[Prop("name", "Emoji name", "str", "", fmt=True, port=True,
                              help="2-32 letters, numbers or underscores.")],
                  requires=("discord.py>=2.5",), gen=gen_emoji_add,
                  description="Upload an image attachment as one of the bot's application emojis (usable in every server)."))


def gen_emoji_remove(ctx):
    v = ctx.var
    ctx.emit(f"{v('ok')}, {v('error')} = await bs.app_emoji_remove(self.bot, {ctx.val('name')})")


register(NodeSpec("action.app_emoji_remove", "Remove Application Emoji", "Action", "action",
                  inputs=[EX("in"), D("name", "str")], outputs=[EX(), D("ok", "bool"), D("error", "str")],
                  props=[Prop("name", "Emoji name", "str", "", fmt=True, port=True)], gen=gen_emoji_remove,
                  description="Delete an application emoji by name."))


def gen_emoji_list(ctx):
    v = ctx.var
    ctx.emit(f"{v('text')}, {v('count')} = await bs.app_emoji_list(self.bot)")


register(NodeSpec("action.app_emoji_list", "List Application Emojis", "Action", "action",
                  inputs=[EX("in")], outputs=[EX(), D("text", "str"), D("count", "int")], gen=gen_emoji_list,
                  description="'text' has one emoji per line; wire it into a Reply."))


def gen_delay(ctx):
    ctx.emit(f"await asyncio.sleep({ctx.val('seconds')})")


register(NodeSpec("action.delay", "Wait", "Action", "action",
                  inputs=[EX("in")], outputs=[EX()],
                  props=[Prop("seconds", "Seconds", "float", 1)], gen=gen_delay))


def _custom_ports(props):
    from .scope import custom_names
    return ([D(n) for n in custom_names(props.get("inputs"), "")],
            [D(n) for n in custom_names(props.get("outputs"), "result")])


def gen_custom(ctx):
    """Custom Python: full Python for logic, discord.py for bot calls. Every node this one is attached to
    is visible by name (see core/scope.py); wired inputs and declared outputs become real ports."""
    from .scope import custom_names, referenced_aliases, scope_for
    node = ctx.node
    code = str(ctx.prop("code") or "").rstrip()
    ins = custom_names(node.props.get("inputs"), "")
    outs = custom_names(node.props.get("outputs"), "result")
    for n in ins:
        e = ctx.g.incoming(node.id, n)
        ctx.emit(f"{n} = {ctx.expr_of(e.src, e.src_port) if e else 'None'}")
    for v in referenced_aliases(code, scope_for(ctx.g, node)):
        if v.name not in ins:
            ctx.emit(f"{v.name} = {v.expr}")
    for o in outs:
        ctx.emit(f"{o} = None")
    if not code.strip():
        ctx.emit("pass")
    for ln in code.splitlines():
        ctx.emit(ln)
    for o in outs:
        ctx.emit(f"{ctx.var(o)} = {o}")


register(NodeSpec("action.python", "Custom Python", "Custom", "action",
                  inputs=[EX("in")], outputs=[EX()],
                  props=[Prop("code", "Python (runs inside the handler)", "text",
                              "# Everything wired before this node is in scope (see the list under the editor).\n"
                              "# Any Python works here; discord.py for bot calls. Assign `result` to pass data on.\n"),
                         Prop("inputs", "Data inputs (names, comma separated)", "str", "",
                              help="Each name becomes an input port and a variable in your code."),
                         Prop("outputs", "Data outputs (names, comma separated)", "str", "result",
                              help="Assign these variables in your code; each becomes an output port.")],
                  dynamic_ports=_custom_ports, gen=gen_custom,
                  description="Raw Python at this point in the flow, aware of every node it is attached to. "
                              "Only open projects you trust."))


# =================================================================================
# FLOW
# =================================================================================

def _if_else(ctx, cond: str):
    ctx.emit(f"if {cond}:")
    with ctx.block("true"):
        pass
    if ctx.g.outgoing(ctx.node.id, "false"):
        ctx.emit("else:")
        with ctx.block("false"):
            pass


def gen_branch(ctx):
    _if_else(ctx, ctx.val("condition"))


register(NodeSpec("flow.branch", "Branch (If)", "Flow", "flow",
                  inputs=[EX("in"), D("condition", "bool")], outputs=[EX("true"), EX("false")],
                  props=[Prop("condition", "Condition", "any", "true", port=True)], gen=gen_branch,
                  description="Runs 'true' or 'false' depending on the condition."))


def gen_has_perm(ctx):
    user = ctx.val("user", ctx.default_user() or "None")
    _if_else(ctx, f"bs.has_perm({user}, {str(ctx.prop('permission'))!r})")


register(NodeSpec("flow.has_permission", "Has Permission?", "Flow", "flow",
                  inputs=[EX("in"), D("user", "user")], outputs=[EX("true"), EX("false")],
                  props=[Prop("permission", "Permission", "choice", "manage_guild", choices=PERMS)],
                  gen=gen_has_perm))


def gen_has_role(ctx):
    user = ctx.val("user", ctx.default_user() or "None")
    _if_else(ctx, f"bs.has_role({user}, {ctx.val('role')})")


register(NodeSpec("flow.has_role", "Has Role?", "Flow", "flow",
                  inputs=[EX("in"), D("user", "user"), D("role", "str")], outputs=[EX("true"), EX("false")],
                  props=[Prop("role", "Role (ID or name)", "str", "", port=True)], gen=gen_has_role))


def gen_chance(ctx):
    _if_else(ctx, f"__import__('random').random() * 100 < {ctx.val('percent')}")


register(NodeSpec("flow.chance", "Random Chance", "Flow", "flow",
                  inputs=[EX("in")], outputs=[EX("true"), EX("false")],
                  props=[Prop("percent", "Chance %", "float", 50, port=True)], gen=gen_chance))


def gen_stop(ctx):
    ctx.emit("return")


register(NodeSpec("flow.stop", "Stop", "Flow", "flow", inputs=[EX("in")], gen=gen_stop,
                  description="End this handler here."))


# =================================================================================
# DATA
# =================================================================================

register(NodeSpec("data.text", "Text", "Data", "data", outputs=[D("value", "str")],
                  props=[Prop("value", "Text", "text", "", fmt=True)],
                  gen=lambda ctx, port: ctx.val("value")))
register(NodeSpec("data.number", "Number", "Data", "data", outputs=[D("value", "int")],
                  props=[Prop("value", "Number", "float", 0)],
                  gen=lambda ctx, port: literal(ctx.prop("value"), "float")
                  if "." in str(ctx.prop("value")) else literal(ctx.prop("value"), "int")))


def _fmt_ports(props):
    import re
    names = []
    for m in re.finditer(r"\{([a-z][a-z0-9_]*)\}", str(props.get("template", ""))):
        if m.group(1) not in names:
            names.append(m.group(1))
    return [D(n) for n in names], []


def gen_format(ctx, port):
    local = {}
    ins, _ = ctx.node.ports()
    for p in ins:
        e = ctx.g.incoming(ctx.node.id, p.name)
        if e:
            local[p.name] = ctx.expr_of(e.src, e.src_port)
    return render_template(str(ctx.prop("template") or ""), {**ctx.scope, **local})


register(NodeSpec("data.format", "Format Text", "Data", "data", outputs=[D("text", "str")],
                  props=[Prop("template", "Template", "text", "Hello {name}!")],
                  gen=gen_format, dynamic_ports=_fmt_ports,
                  description="Each {word} becomes an input you can wire. Trigger variables also work."))


def gen_user_info(ctx, port):
    u = ctx.val("user", ctx.default_user() or "None")
    return {"id": f"{u}.id", "name": f"{u}.name", "display": f"{u}.display_name",
            "mention": f"{u}.mention", "avatar": f"str({u}.display_avatar.url)"}[port]


register(NodeSpec("data.user", "User Info", "Data", "data", inputs=[D("user", "user")],
                  outputs=[D("id", "int"), D("name", "str"), D("display", "str"),
                           D("mention", "str"), D("avatar", "str")], gen=gen_user_info))


def gen_rand_int(ctx, port):
    return f"__import__('random').randint(int({ctx.val('low')}), int({ctx.val('high')}))"


register(NodeSpec("data.random_int", "Random Number", "Data", "data", outputs=[D("value", "int")],
                  props=[Prop("low", "Min", "int", 1, port=True), Prop("high", "Max", "int", 6, port=True)],
                  inputs=[D("low", "int"), D("high", "int")], cache=True, gen=gen_rand_int))


def gen_rand_choice(ctx, port):
    items = [x.strip() for x in str(ctx.prop("items") or "").split("|") if x.strip()] or [""]
    return f"__import__('random').choice({items!r})"


register(NodeSpec("data.random_choice", "Random Choice", "Data", "data", outputs=[D("value", "str")],
                  props=[Prop("items", "Options (separate with |)", "text", "heads|tails")],
                  cache=True, gen=gen_rand_choice))

_CMP = {"==": "==", "!=": "!=", ">": ">", "<": "<", ">=": ">=", "<=": "<="}


def gen_compare(ctx, port):
    op = str(ctx.prop("op"))
    a, b = ctx.val("a"), ctx.val("b")
    if op == "contains":
        return f"(str({b}).lower() in str({a}).lower())"
    return f"({a} {_CMP.get(op, '==')} {b})"


register(NodeSpec("data.compare", "Compare", "Data", "data", inputs=[D("a"), D("b")], outputs=[D("result", "bool")],
                  props=[Prop("a", "A", "any", "", port=True),
                         Prop("op", "Operator", "choice", "==", choices=tuple(_CMP) + ("contains",)),
                         Prop("b", "B", "any", "", port=True)], gen=gen_compare))


def gen_logic(ctx, port):
    op = str(ctx.prop("op"))
    if op == "not":
        return f"(not {ctx.val('a')})"
    return f"({ctx.val('a')} {op} {ctx.val('b')})"


register(NodeSpec("data.logic", "And / Or / Not", "Data", "data", inputs=[D("a", "bool"), D("b", "bool")],
                  outputs=[D("result", "bool")],
                  props=[Prop("a", "A", "any", "true", port=True),
                         Prop("op", "Operator", "choice", "and", choices=("and", "or", "not")),
                         Prop("b", "B", "any", "true", port=True)], gen=gen_logic))


def gen_math(ctx, port):
    op = str(ctx.prop("op"))
    return f"({ctx.val('a')} {op} {ctx.val('b')})"


register(NodeSpec("data.math", "Math", "Data", "data", inputs=[D("a", "int"), D("b", "int")], outputs=[D("result", "int")],
                  props=[Prop("a", "A", "float", 0, port=True),
                         Prop("op", "Operator", "choice", "+", choices=("+", "-", "*", "/", "//", "%")),
                         Prop("b", "B", "float", 1, port=True)], gen=gen_math))


def gen_env(ctx, port):
    return f"__import__('os').getenv({str(ctx.prop('name'))!r}, {str(ctx.prop('default'))!r})"


register(NodeSpec("data.env", "Environment Variable", "Data", "data", outputs=[D("value", "str")],
                  props=[Prop("name", "Name", "str", "LOG_CHANNEL_ID"), Prop("default", "Default", "str", "")],
                  gen=gen_env, description="Read a value from .env at runtime (e.g. a channel ID)."))
register(NodeSpec("data.now", "Unix Time Now", "Data", "data", outputs=[D("value", "int")],
                  cache=True, gen=lambda ctx, port: "int(__import__('time').time())"))


# =================================================================================
# STORAGE (SQLite key/value inside the generated bot)
# =================================================================================

def gen_kv_get(ctx):
    ctx.emit(f"{ctx.var('value')} = await bs.kv_get({ctx.val('namespace')}, {ctx.val('key')}, {ctx.val('default')})")


def gen_kv_set(ctx):
    ctx.emit(f"await bs.kv_set({ctx.val('namespace')}, {ctx.val('key')}, {ctx.val('value')})")


def gen_kv_incr(ctx):
    ctx.emit(f"{ctx.var('value')} = await bs.kv_incr({ctx.val('namespace')}, {ctx.val('key')}, {ctx.val('amount')})")


_KV_NS = Prop("namespace", "Table name", "str", "stats")
_KV_KEY = Prop("key", "Key", "str", "{user.id}", fmt=True, port=True)
register(NodeSpec("store.get", "Load Value", "Storage", "action",
                  inputs=[EX("in"), D("key", "str")], outputs=[EX(), D("value")],
                  props=[_KV_NS, _KV_KEY, Prop("default", "If missing", "any", 0)], gen=gen_kv_get))
register(NodeSpec("store.set", "Save Value", "Storage", "action",
                  inputs=[EX("in"), D("key", "str"), D("value")], outputs=[EX()],
                  props=[_KV_NS, _KV_KEY, Prop("value", "Value", "any", "", port=True)], gen=gen_kv_set))
register(NodeSpec("store.incr", "Add to Counter", "Storage", "action",
                  inputs=[EX("in"), D("key", "str"), D("amount", "int")], outputs=[EX(), D("value", "int")],
                  props=[_KV_NS, _KV_KEY, Prop("amount", "Amount", "float", 1, port=True)], gen=gen_kv_incr))


def gen_kv_delete(ctx):
    ctx.emit(f"await bs.kv_set({ctx.val('namespace')}, {ctx.val('key')}, None)")


register(NodeSpec("store.delete", "Delete Value", "Storage", "action",
                  inputs=[EX("in"), D("key", "str")], outputs=[EX()],
                  props=[_KV_NS, _KV_KEY], gen=gen_kv_delete,
                  description="Remove a key from the KV store (sets it to missing)."))


# =================================================================================
# EXTRA FLOW (lean — common gates without palette clutter)
# =================================================================================

def gen_is_bot(ctx):
    user = ctx.val("user", ctx.default_user() or "None")
    _if_else(ctx, f"getattr({user}, 'bot', False)")


register(NodeSpec("flow.is_bot", "Is Bot?", "Flow", "flow",
                  inputs=[EX("in"), D("user", "user")], outputs=[EX("true"), EX("false")],
                  props=[Prop("user", "User", "any", "", port=True)],
                  gen=gen_is_bot, description="Branch on whether the member is a bot account."))


def gen_is_owner(ctx):
    user = ctx.val("user", ctx.default_user() or "None")
    _if_else(ctx, f"({user} is not None and getattr({user}, 'id', None) == getattr(getattr({user}, 'guild', None), 'owner_id', None))")


register(NodeSpec("flow.is_owner", "Is Server Owner?", "Flow", "flow",
                  inputs=[EX("in"), D("user", "user")], outputs=[EX("true"), EX("false")],
                  gen=gen_is_owner, description="True when the user owns the current guild."))


def gen_in_channel(ctx):
    ch = ctx.val("channel", ctx.scope.get("channel", "None"))
    raw = str(ctx.prop("channel_ids") or "").strip()
    ids = [x.strip() for x in re.split(r"[,\s]+", raw) if x.strip().isdigit()]
    if not ids:
        ctx.warn("In Channel? has no channel IDs configured")
        _if_else(ctx, "False")
        return
    joined = ", ".join(ids)
    _if_else(ctx, f"(getattr({ch}, 'id', None) in ({joined},))")


register(NodeSpec("flow.in_channel", "In These Channels?", "Flow", "flow",
                  inputs=[EX("in"), D("channel", "channel")], outputs=[EX("true"), EX("false")],
                  props=[Prop("channel_ids", "Channel IDs (comma/space)", "text", "",
                              help="Only continue on true when the current channel is one of these.")],
                  gen=gen_in_channel))


def gen_match_text(ctx):
    text = ctx.val("text", "''")
    pattern = str(ctx.prop("pattern") or "")
    mode = str(ctx.prop("mode") or "contains")
    if mode == "equals":
        cond = f"(str({text}).lower() == {pattern.lower()!r})"
    elif mode == "startswith":
        cond = f"str({text}).lower().startswith({pattern.lower()!r})"
    elif mode == "endswith":
        cond = f"str({text}).lower().endswith({pattern.lower()!r})"
    else:
        cond = f"({pattern.lower()!r} in str({text}).lower())"
    _if_else(ctx, cond)


register(NodeSpec("flow.match_text", "Text Matches?", "Flow", "flow",
                  inputs=[EX("in"), D("text", "str")], outputs=[EX("true"), EX("false")],
                  props=[Prop("text", "Text", "str", "", port=True),
                         Prop("mode", "Mode", "choice", "contains",
                              choices=("contains", "equals", "startswith", "endswith")),
                         Prop("pattern", "Pattern", "str", "")],
                  gen=gen_match_text, description="Case-insensitive text gate for commands or filters."))


# =================================================================================
# EXTRA DATA
# =================================================================================

def gen_string_op(ctx, port):
    op = str(ctx.prop("op") or "strip")
    s = ctx.val("text", "''")
    if op == "upper":
        return f"str({s}).upper()"
    if op == "lower":
        return f"str({s}).lower()"
    if op == "title":
        return f"str({s}).title()"
    if op == "length":
        return f"len(str({s}))"
    if op == "reverse":
        return f"str({s})[::-1]"
    if op == "replace":
        return f"str({s}).replace({ctx.val('find')}, {ctx.val('repl')})"
    return f"str({s}).strip()"


register(NodeSpec("data.string", "String Tools", "Data", "data",
                  inputs=[D("text", "str"), D("find", "str"), D("repl", "str")],
                  outputs=[D("value")],
                  props=[Prop("text", "Text", "str", "", port=True),
                         Prop("op", "Operation", "choice", "strip",
                              choices=("strip", "upper", "lower", "title", "length", "reverse", "replace")),
                         Prop("find", "Find (for replace)", "str", "", port=True),
                         Prop("repl", "Replace with", "str", "", port=True)],
                  gen=gen_string_op, description="Common string transforms. 'length' returns an int."))


def gen_concat(ctx, port):
    return f"(str({ctx.val('a')}) + str({ctx.val('b')}))"


register(NodeSpec("data.concat", "Join Text", "Data", "data",
                  inputs=[D("a", "str"), D("b", "str")], outputs=[D("value", "str")],
                  props=[Prop("a", "A", "str", "", port=True), Prop("b", "B", "str", "", port=True)],
                  gen=gen_concat))


def gen_coalesce(ctx, port):
    return f"({ctx.val('a')} if {ctx.val('a')} not in (None, '') else {ctx.val('b')})"


register(NodeSpec("data.coalesce", "First Non-Empty", "Data", "data",
                  inputs=[D("a"), D("b")], outputs=[D("value")],
                  props=[Prop("a", "Preferred", "any", "", port=True),
                         Prop("b", "Fallback", "any", "", port=True)],
                  gen=gen_coalesce, description="Use A unless it is None or empty string, else B."))


def gen_ternary(ctx, port):
    return f"({ctx.val('when_true')} if {ctx.val('condition')} else {ctx.val('when_false')})"


register(NodeSpec("data.if_value", "Pick Value (If)", "Data", "data",
                  inputs=[D("condition", "bool"), D("when_true"), D("when_false")],
                  outputs=[D("value")],
                  props=[Prop("condition", "Condition", "any", "true", port=True),
                         Prop("when_true", "If true", "any", "", port=True),
                         Prop("when_false", "If false", "any", "", port=True)],
                  gen=gen_ternary, description="Data-side if/else without branching the exec wire."))


def gen_clamp(ctx, port):
    return f"min(max({ctx.val('value')}, {ctx.val('low')}), {ctx.val('high')})"


register(NodeSpec("data.clamp", "Clamp Number", "Data", "data",
                  inputs=[D("value", "int"), D("low", "int"), D("high", "int")],
                  outputs=[D("result", "int")],
                  props=[Prop("value", "Value", "float", 0, port=True),
                         Prop("low", "Min", "float", 0, port=True),
                         Prop("high", "Max", "float", 100, port=True)],
                  gen=gen_clamp))


def gen_snowflake_time(ctx, port):
    sid = ctx.val("id", "0")
    return f"int(((int({sid}) >> 22) + 1420070400000) / 1000)"


register(NodeSpec("data.snowflake_time", "Snowflake → Unix Time", "Data", "data",
                  inputs=[D("id", "int")], outputs=[D("value", "int")],
                  props=[Prop("id", "Snowflake ID", "str", "", port=True)],
                  gen=gen_snowflake_time,
                  description="Discord snowflake creation time as Unix seconds (useful for account age)."))


# =================================================================================
# EXTRA MODERATION / CHANNEL ACTIONS
# =================================================================================

def gen_untimeout(ctx):
    user = ctx.val("user", ctx.default_user() or "None")
    ctx.emit(f"{ctx.var('ok')} = await bs.timeout({user}, 0, {ctx.val('reason')})")


register(NodeSpec(
    "mod.untimeout", "Remove Timeout", "Moderation", "action",
    inputs=[EX("in"), D("user", "user"), D("reason", "str")],
    outputs=[EX(), D("ok", "bool")],
    props=[Prop("reason", "Reason", "str", "", fmt=True, port=True)],
    gen=gen_untimeout, description="Clear a member's timeout."))


def gen_nickname(ctx):
    user = ctx.val("user", ctx.default_user() or "None")
    nick = ctx.val("nick")
    ctx.emit(f"{ctx.var('ok')} = False")
    ctx.emit(f"_m = await bs.get_member(None, {user})")
    ctx.emit("if _m is not None:")
    with ctx.block():
        ctx.emit("try:")
        with ctx.block():
            ctx.emit(f"await _m.edit(nick={nick} or None, reason='Bot Forge Studio')")
            ctx.emit(f"{ctx.var('ok')} = True")
        ctx.emit("except discord.HTTPException as _e:")
        with ctx.block():
            ctx.emit("bs.log.warning('nickname failed: %s', _e)")


register(NodeSpec(
    "mod.nickname", "Set Nickname", "Moderation", "action",
    inputs=[EX("in"), D("user", "user"), D("nick", "str")],
    outputs=[EX(), D("ok", "bool")],
    props=[Prop("nick", "Nickname (blank = reset)", "str", "", fmt=True, port=True)],
    gen=gen_nickname, description="Change a member's server nickname. Blank resets it."))


def gen_purge(ctx):
    ch = ctx.val("channel", ctx.scope.get("channel", "None"))
    limit = ctx.val("limit")
    ctx.emit(f"{ctx.var('deleted')} = 0")
    ctx.emit(f"_ch = {ch}")
    ctx.emit("if _ch is not None and hasattr(_ch, 'purge'):")
    with ctx.block():
        ctx.emit("try:")
        with ctx.block():
            ctx.emit(f"_xs = await _ch.purge(limit=max(1, min(int({limit}), 100)))")
            ctx.emit(f"{ctx.var('deleted')} = len(_xs)")
        ctx.emit("except discord.HTTPException as _e:")
        with ctx.block():
            ctx.emit("bs.log.warning('purge failed: %s', _e)")


register(NodeSpec(
    "mod.purge", "Purge Messages", "Moderation", "action",
    inputs=[EX("in"), D("channel", "channel"), D("limit", "int")],
    outputs=[EX(), D("deleted", "int")],
    props=[Prop("limit", "Max messages (1–100)", "int", 10, port=True),
           Prop("channel", "Channel (blank = current)", "str", "", port=True)],
    gen=gen_purge, description="Bulk-delete recent messages in a channel (capped at 100). Needs Manage Messages."))


def gen_slowmode(ctx):
    ch = ctx.val("channel", ctx.scope.get("channel", "None"))
    secs = ctx.val("seconds")
    ctx.emit(f"{ctx.var('ok')} = False")
    ctx.emit(f"_ch = {ch}")
    ctx.emit("if _ch is not None and hasattr(_ch, 'edit'):")
    with ctx.block():
        ctx.emit("try:")
        with ctx.block():
            ctx.emit(f"await _ch.edit(slowmode_delay=max(0, min(int({secs}), 21600)))")
            ctx.emit(f"{ctx.var('ok')} = True")
        ctx.emit("except discord.HTTPException as _e:")
        with ctx.block():
            ctx.emit("bs.log.warning('slowmode failed: %s', _e)")


register(NodeSpec(
    "mod.slowmode", "Set Slowmode", "Moderation", "action",
    inputs=[EX("in"), D("channel", "channel"), D("seconds", "int")],
    outputs=[EX(), D("ok", "bool")],
    props=[Prop("seconds", "Seconds (0 = off, max 21600)", "int", 5, port=True),
           Prop("channel", "Channel (blank = current)", "str", "", port=True)],
    gen=gen_slowmode, description="Set channel slowmode delay in seconds."))


def gen_lock_channel(ctx):
    ch = ctx.val("channel", ctx.scope.get("channel", "None"))
    lock = bool(ctx.prop("lock"))
    send_val = "False" if lock else "None"
    ctx.emit(f"{ctx.var('ok')} = False")
    ctx.emit(f"_ch = {ch}")
    ctx.emit("if _ch is not None and getattr(_ch, 'guild', None) is not None:")
    with ctx.block():
        ctx.emit("try:")
        with ctx.block():
            ctx.emit("_role = _ch.guild.default_role")
            ctx.emit(f"await _ch.set_permissions(_role, send_messages={send_val})")
            ctx.emit(f"{ctx.var('ok')} = True")
        ctx.emit("except discord.HTTPException as _e:")
        with ctx.block():
            ctx.emit("bs.log.warning('lock failed: %s', _e)")


register(NodeSpec(
    "mod.lock_channel", "Lock / Unlock Channel", "Moderation", "action",
    inputs=[EX("in"), D("channel", "channel")],
    outputs=[EX(), D("ok", "bool")],
    props=[Prop("lock", "Lock (unchecked = unlock)", "bool", True),
           Prop("channel", "Channel (blank = current)", "str", "", port=True)],
    gen=gen_lock_channel,
    description="Deny (or reset) @everyone send_messages on a text channel."))


def gen_warn(ctx):
    user = ctx.val("user", ctx.default_user() or "None")
    reason = ctx.val("reason")
    ns = str(ctx.prop("namespace") or "warns")
    ctx.emit(f"_uid = getattr({user}, 'id', {user})")
    ctx.emit(f"_gid = getattr(getattr({user}, 'guild', None), 'id', 0)")
    ctx.emit(f"{ctx.var('count')} = await bs.kv_incr({ns!r}, f'{{_gid}}:{{_uid}}', 1)")
    ctx.emit(f"bs.log.info('warn user=%s count=%s reason=%s', _uid, {ctx.var('count')}, {reason})")


register(NodeSpec(
    "mod.warn", "Record Warning", "Moderation", "action",
    inputs=[EX("in"), D("user", "user"), D("reason", "str")],
    outputs=[EX(), D("count", "int")],
    props=[Prop("namespace", "Table name", "str", "warns"),
           Prop("reason", "Reason", "str", "", fmt=True, port=True)],
    gen=gen_warn,
    description="Increment a per-member warn counter in storage and return the new total."))
