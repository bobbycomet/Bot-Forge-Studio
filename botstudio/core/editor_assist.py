"""Custom Python editor assist: completions, snippets, diagnostics, plugin hooks.

Plugins may expose::

    def register_editor_assist(api):
        api.add_completions([{"label": "bs.my_helper", "insert": "bs.my_helper()", ...}])
        api.add_snippets([{"name": "My snippet", "code": "..."}])
        api.add_diagnostic_provider(my_fn)  # (source: str, ctx: dict) -> list[Diagnostic]

The UI calls :func:`completions`, :func:`snippets`, :func:`analyze` which merge
built-ins with anything registered by plugins.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Any, Callable


# --------------------------------------------------------------------------- data
@dataclass
class Completion:
    label: str
    insert: str
    detail: str = ""
    kind: str = "func"  # func | var | snip | mod | keyword
    runtime: str = "python"   # python | js | both — which Custom-code node shows it


@dataclass
class Snippet:
    name: str
    code: str
    description: str = ""
    runtime: str = "python"   # python | js | both


@dataclass
class Diagnostic:
    line: int          # 0-based
    col: int           # 0-based
    message: str
    severity: str = "error"  # error | warning | info
    suggestion: str = ""     # optional replacement / fix hint


# --------------------------------------------------------------------------- plugin registry
_extra_completions: list[Completion] = []
_extra_snippets: list[Snippet] = []
_diagnostic_providers: list[Callable[[str, dict], list[Diagnostic]]] = []
_provider_runtime: dict[int, str] = {}


def _wants(item_runtime: str, runtime: str) -> bool:
    return item_runtime == "both" or item_runtime == runtime


def _rt(context: dict | None) -> str:
    return "js" if (context or {}).get("runtime") == "js" else "python"


class EditorAssistAPI:
    """Passed to plugin ``register_editor_assist(api)``."""

    # ``runtime`` is "python" (default, so existing plugins are unchanged), "js" or "both".
    # An item dict may also carry its own "runtime" key.
    def add_completions(self, items: list[dict | Completion], runtime: str = "python") -> None:
        for it in items:
            if isinstance(it, Completion):
                _extra_completions.append(it)
            else:
                _extra_completions.append(Completion(
                    label=str(it.get("label", "")),
                    insert=str(it.get("insert", it.get("label", ""))),
                    detail=str(it.get("detail", "")),
                    kind=str(it.get("kind", "func")),
                    runtime=str(it.get("runtime", runtime)),
                ))

    def add_snippets(self, items: list[dict | Snippet], runtime: str = "python") -> None:
        for it in items:
            if isinstance(it, Snippet):
                _extra_snippets.append(it)
            else:
                _extra_snippets.append(Snippet(
                    name=str(it.get("name", "Snippet")),
                    code=str(it.get("code", "")),
                    description=str(it.get("description", "")),
                    runtime=str(it.get("runtime", runtime)),
                ))

    def add_diagnostic_provider(self, fn: Callable[[str, dict], list[Diagnostic]], runtime: str = "python") -> None:
        if callable(fn):
            _diagnostic_providers.append(fn)
            _provider_runtime[id(fn)] = runtime


def reset_plugin_assist() -> None:
    """Clear plugin-registered assist data (used before reload_all)."""
    _extra_completions.clear()
    _extra_snippets.clear()
    _diagnostic_providers.clear()
    _provider_runtime.clear()


def register_from_module(mod: Any) -> bool:
    """If module defines register_editor_assist, call it. Returns True if handled."""
    fn = getattr(mod, "register_editor_assist", None)
    if not callable(fn):
        return False
    fn(EditorAssistAPI())
    return True


# --------------------------------------------------------------------------- built-in knowledge
_KEYWORDS = [
    "and", "as", "assert", "async", "await", "break", "class", "continue", "def",
    "elif", "else", "except", "finally", "for", "from", "if", "import", "in", "is",
    "lambda", "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
    "None", "True", "False",
]

_SCOPE_VARS = [
    Completion("self", "self", "Cog instance", "var"),
    Completion("self.bot", "self.bot", "commands.Bot", "var"),
    Completion("bs", "bs", "Bot Forge Studio runtime helpers", "mod"),
    Completion("interaction", "interaction", "discord.Interaction (slash / components)", "var"),
    Completion("message", "message", "discord.Message (message triggers)", "var"),
    Completion("user", "user", "Member/User when provided by trigger", "var"),
    Completion("channel", "channel", "Channel when provided by trigger", "var"),
    Completion("guild", "guild", "Guild when provided by trigger", "var"),
    Completion("member", "member", "Member on join/leave style triggers", "var"),
    Completion("asyncio", "asyncio", "stdlib asyncio", "mod"),
    Completion("discord", "discord", "discord.py", "mod"),
]

_BS_COMPLETIONS = [
    Completion("bs.respond", "await bs.respond(interaction, \"\")", "Reply or followup to an interaction", "func"),
    Completion("bs.send", "await bs.send(channel, \"\")", "Send to a channel (id or object)", "func"),
    Completion("bs.embed", "bs.embed(title=\"\", description=\"\", color=\"5865F2\")", "Build a discord.Embed", "func"),
    Completion("bs.log", "bs.log", "Logger (info/warning/error/exception)", "var"),
    Completion("bs.kv_get", "await bs.kv_get(\"ns\", key, default=None)", "Load value from SQLite KV", "func"),
    Completion("bs.kv_set", "await bs.kv_set(\"ns\", key, value)", "Save value to SQLite KV", "func"),
    Completion("bs.kv_incr", "await bs.kv_incr(\"ns\", key, 1)", "Atomic increment", "func"),
    Completion("bs.kv_top", "await bs.kv_top(\"ns\", 10)", "Top keys by numeric value", "func"),
    Completion("bs.has_perm", "bs.has_perm(user, \"moderate_members\")", "Permission check helper", "func"),
    Completion("bs.has_role", "bs.has_role(user, \"Moderator\")", "Role name/id check", "func"),
    Completion("bs.timeout", "await bs.timeout(user, minutes, reason=\"\")", "Timeout member (0 clears)", "func"),
    Completion("bs.kick", "await bs.kick(user, reason=\"\")", "Kick member", "func"),
    Completion("bs.ban", "await bs.ban(user, reason=\"\", delete_days=0)", "Ban member", "func"),
    Completion("bs.get_member", "bs.get_member(guild, user)", "Resolve Member", "func"),
    Completion("bs.http_get", "bs.http_get(url)", "HTTP GET → status, text, json", "func"),
    Completion("bs.add_role", "bs.add_role(user, role)", "Add role by name/id/object", "func"),
    Completion("bs.remove_role", "bs.remove_role(user, role)", "Remove role", "func"),
]

_DISCORD_COMPLETIONS = [
    Completion("discord.Embed", "discord.Embed(title=\"\", description=\"\", colour=0x5865F2)", "Embed constructor", "func"),
    Completion("discord.Color", "discord.Color", "Color helpers", "mod"),
    Completion("discord.utils.get", "discord.utils.get(iterable, **attrs)", "Find by attribute", "func"),
    Completion("discord.Forbidden", "discord.Forbidden", "Missing permissions exception", "var"),
    Completion("discord.HTTPException", "discord.HTTPException", "API failure exception", "var"),
    Completion("discord.Interaction", "discord.Interaction", "Interaction type", "var"),
]

_BUILTIN_SNIPPETS = [
    Snippet(
        "Ephemeral error + return",
        'await bs.respond(interaction, "Something went wrong.", ephemeral=True)\nreturn',
        "Tell the invoker and stop the handler",
    ),
    Snippet(
        "Permission gate",
        'if not bs.has_perm(interaction.user, "moderate_members"):\n'
        '    await bs.respond(interaction, "You lack permission.", ephemeral=True)\n'
        '    return',
        "Require a guild permission",
    ),
    Snippet(
        "Defer then followup",
        "await interaction.response.defer(ephemeral=True)\n"
        "# ... work ...\n"
        'await interaction.followup.send("Done.")',
        "For work that may exceed 3 seconds",
    ),
    Snippet(
        "KV counter",
        'gid = str(interaction.guild.id)\n'
        'uid = str(interaction.user.id)\n'
        'n = await bs.kv_incr("uses", f"{gid}:{uid}", 1)\n'
        'await bs.respond(interaction, f"Count: {n}")',
        "Per-user counter in the current guild",
    ),
    Snippet(
        "Try / Forbidden",
        "try:\n"
        "    # await action\n"
        "    pass\n"
        "except discord.Forbidden:\n"
        '    await bs.respond(interaction, "I lack permission to do that.", ephemeral=True)\n'
        "    return\n"
        "except discord.HTTPException as e:\n"
        '    await bs.respond(interaction, f"Discord error: {e}", ephemeral=True)\n'
        "    return",
        "Handle Discord API failures",
    ),
    Snippet(
        "Parse int argument",
        "try:\n"
        "    amount = int(amount)\n"
        "except (TypeError, ValueError):\n"
        '    await bs.respond(interaction, "Need a whole number.", ephemeral=True)\n'
        "    return",
        "Validate numeric input",
    ),
    Snippet(
        "Guild-only guard",
        "if interaction.guild is None:\n"
        '    await bs.respond(interaction, "Guild only.", ephemeral=True)\n'
        "    return",
        "Reject DM invocations",
    ),
]



# --------------------------------------------------------------------------- JavaScript knowledge (discord.js mode)
def _plain(ins: str) -> str:
    """'f(${1:x}, ${2:y})' -> 'f(x, y)' — the editor inserts plain text, not tab-stop snippets."""
    return re.sub(r"\$\{\d+:([^}]*)\}", r"\1", ins)


_JS_KEYWORDS = ["await", "async", "const", "let", "var", "if", "else", "for", "of", "while", "return", "try", "catch",
                "throw", "new", "function", "null", "undefined", "true", "false", "typeof"]

_JS_SCOPE_VARS = [
    Completion("bot", "bot", "discord.js Client", "var", "js"),
    Completion("bs", "bs", "Bot Forge Studio runtime helpers", "mod", "js"),
    Completion("interaction", "interaction", "slash command interaction", "var", "js"),
    Completion("message", "message", "message (On Message)", "var", "js"),
    Completion("member", "member", "GuildMember (member join/leave)", "var", "js"),
    Completion("reaction", "reaction", "MessageReaction (On Reaction)", "var", "js"),
    Completion("req", "req", "Express request (Web Route)", "var", "js"),
    Completion("res", "res", "Express response (Web Route)", "var", "js"),
]

_JS_BS = [
    ("bs.respond(interaction, content, embed, { ephemeral })", "await bs.respond(interaction, ${1:''}, null, { ephemeral: true })", "reply/follow-up to a slash command"),
    ("bs.send(bot, channel, content, embed)", "await bs.send(bot, ${1:channel}, ${2:'text'})", "send to a channel or channel ID"),
    ("bs.dm(user, content)", "await bs.dm(${1:user}, ${2:'text'})", "DM a user; false if closed"),
    ("bs.embed(title, description, color, footer)", "bs.embed(${1:'Title'}, ${2:'Description'})", "build an EmbedBuilder (or null if empty)"),
    ("bs.setRole(guild, user, role, add)", "await bs.setRole(interaction.guild, ${1:interaction.member}, ${2:'Member'}, true)", "add/remove a role by name or ID"),
    ("bs.timeout(user, minutes, reason)", "await bs.timeout(${1:user}, ${2:10}, ${3:''})", "time a member out (0 = lift)"),
    ("bs.kick(user, reason)", "await bs.kick(${1:user}, ${2:''})", "kick a member"),
    ("bs.ban(user, reason)", "await bs.ban(${1:user}, ${2:''})", "ban a member"),
    ("bs.hasPerm(member, 'manage_guild')", "bs.hasPerm(${1:interaction.member}, ${2:'manage_guild'})", "permission check"),
    ("bs.hasRole(member, role)", "bs.hasRole(${1:interaction.member}, ${2:'Moderator'})", "role check (name or ID)"),
    ("bs.kvGet(ns, key, default)", "await bs.kvGet(${1:'ns'}, ${2:key}, ${3:0})", "read persistent storage"),
    ("bs.kvSet(ns, key, value)", "await bs.kvSet(${1:'ns'}, ${2:key}, ${3:value})", "write persistent storage (null deletes)"),
    ("bs.kvIncr(ns, key, amount)", "await bs.kvIncr(${1:'ns'}, ${2:key}, 1)", "atomic counter"),
    ("bs.kvTop(ns, limit)", "await bs.kvTop(${1:'ns'}, 10)", "top numeric entries [[key, value], …]"),
    ("bs.httpGet(url)", "await bs.httpGet(${1:url})", "{ status, text } — blocks private addresses"),
    ("bs.tryJson(text)", "bs.tryJson(${1:text})", "JSON.parse that returns null on error"),
    ("bs.jsonGet(obj, 'a.b.0')", "bs.jsonGet(${1:obj}, ${2:'a.b'}, ${3:''})", "read a dotted path"),
    ("bs.sleep(ms)", "await bs.sleep(${1:1000})", "pause without blocking the bot"),
    ("bs.cooldown(name, userId, seconds)", "bs.cooldown(${1:'name'}, ${2:interaction.user.id}, ${3:5})", "seconds left, 0 = go"),
    ("bs.log.info(...)", "bs.log.info('%s', ${1:value})", "write to logs/bot.log"),
]
_JS_BS_COMPLETIONS = [Completion(lbl.split("(")[0], _plain(ins), det, "func", "js") for lbl, ins, det in _JS_BS]

_JS_DISCORD = [
    ("interaction.reply({ content })", "await interaction.reply({ content: ${1:''} })", "reply (first response)"),
    ("interaction.deferReply()", "await interaction.deferReply()", "buy time for slow work"),
    ("interaction.followUp({ content })", "await interaction.followUp({ content: ${1:''} })", "extra message"),
    ("interaction.options.getString('x')", "interaction.options.getString(${1:'name'})", "read a slash option"),
    ("interaction.guild", "interaction.guild", "Guild"),
    ("interaction.channel.send({ content })", "await interaction.channel.send({ content: ${1:''} })", "post in the channel"),
    ("message.reply({ content })", "await message.reply({ content: ${1:''} })", "reply to a message"),
    ("message.delete()", "await message.delete()", "delete a message"),
    ("new EmbedBuilder()", "new EmbedBuilder().setTitle(${1:''}).setDescription(${2:''})", "discord.js embed builder"),
    ("PermissionFlagsBits.", "PermissionFlagsBits.", "permission flags"),
    ("bot.guilds.cache.get(id)", "bot.guilds.cache.get(${1:id})", "find a cached guild"),
    ("bot.channels.fetch(id)", "await bot.channels.fetch(${1:id})", "fetch a channel"),
    ("bot.users.fetch(id)", "await bot.users.fetch(${1:id})", "fetch a user"),
    ("res.status(200).json({})", "res.status(200).json({ ok: true })", "Web Route response"),
]
_JS_DISCORD_COMPLETIONS = [Completion(lbl.split("(")[0], _plain(ins), det, "func", "js") for lbl, ins, det in _JS_DISCORD]

_JS_SNIPPETS = [
    Snippet("Ephemeral reply", "await bs.respond(interaction, 'Only you can see this.', null, { ephemeral: true });\n", "private reply", "js"),
    Snippet("Embed reply", "await bs.respond(interaction, null, bs.embed('Title', 'Description', '66c0f4', 'Footer'));\n", "embed in a slash reply", "js"),
    Snippet("Permission guard", "if (!bs.hasPerm(interaction.member, 'manage_guild')) {\n  await bs.respond(interaction, \"You can't do that.\", null, { ephemeral: true });\n  return;\n}\n", "stop unless the member can manage the server", "js"),
    Snippet("Counter", "const n = await bs.kvIncr('counters', `${interaction.guildId}:${interaction.user.id}`, 1);\n", "per-user counter in storage", "js"),
    Snippet("HTTP + JSON", "const r = await bs.httpGet('https://example.com/api');\nconst data = bs.tryJson(r.text);\nif (r.status !== 200 || !data) { await bs.respond(interaction, 'Request failed.', null, { ephemeral: true }); return; }\n", "fetch JSON safely", "js"),
    Snippet("Try / catch", "try {\n  // risky call\n} catch (e) {\n  bs.log.warning('failed: %s', e && e.message || e);\n}\n", "log instead of crashing", "js"),
    Snippet("Web Route JSON", "res.status(200).json({ ok: true, user: req.user?.id });\n", "respond from a Web Route", "js"),
]

_JS_BLOCKING = [
    (re.compile(r"\b(?:readFileSync|writeFileSync|execSync|spawnSync|readdirSync)\s*\("), "Synchronous file/process call blocks the whole bot.", "Use the async version, e.g. fs.promises.readFile(...)."),
    (re.compile(r"\bwhile\s*\(\s*(?:true|1)\s*\)"), "Endless loop blocks the bot's event loop.", "Add an await (e.g. await bs.sleep(1000)) or use a Schedule trigger."),
    (re.compile(r"\bprocess\.exit\s*\("), "process.exit() stops the whole bot.", "Remove it; use Stop for early exits."),
    (re.compile(r"\bclient\.login\s*\(|\bnew\s+Client\s*\("), "Do not log in or create a second client here.", "Use the existing `bot`."),
]
_JS_NEEDS_AWAIT = re.compile(r"(?<![\w.])(?<!await )(bs\.(?:respond|send|dm|setRole|timeout|kick|ban|deleteMessage|kvGet|kvSet|kvIncr|kvTop|httpGet|sleep|purge|setNickname)"
                             r"|interaction\.(?:reply|followUp|editReply|deferReply)|message\.(?:reply|delete|react))\s*\(")


def _js_diagnostics(source: str) -> list[Diagnostic]:
    from . import jscheck
    out: list[Diagnostic] = []
    issue = jscheck.check_snippet(source)
    if issue:
        line, col, msg = issue
        out.append(Diagnostic(line, col, f"Syntax error: {msg}", "error"))
    for i, text in enumerate(source.splitlines()):
        code = text.split("//", 1)[0]
        for rx, msg, fix in _JS_BLOCKING:
            if rx.search(code):
                out.append(Diagnostic(i, 0, msg, "warning", fix))
        m = _JS_NEEDS_AWAIT.search(code)
        if m and not re.search(r"(?:return|=>|=)\s*$", code[:m.start()]):
            out.append(Diagnostic(i, m.start(), f"{m.group(1)}() is async — add `await` or it may run out of order.", "warning",
                                  f"await {m.group(1)}(...)"))
    return out


# --------------------------------------------------------------------------- completions
def _prefix_at(source: str, pos: int) -> tuple[str, int]:
    """Return (prefix, start_index) for the identifier under/before pos."""
    if pos < 0:
        pos = 0
    if pos > len(source):
        pos = len(source)
    i = pos
    while i > 0 and (source[i - 1].isalnum() or source[i - 1] in "._"):
        i -= 1
    return source[i:pos], i


def completions(source: str, cursor_pos: int, context: dict | None = None) -> list[Completion]:
    """Return completion items filtered by the prefix at cursor_pos."""
    context = context or {}
    if context.get("runtime") in ("html", "css", "browserjs"):
        from . import web_assist
        return web_assist.completions(context["runtime"], source, cursor_pos, Completion)
    prefix, _ = _prefix_at(source, cursor_pos)
    prefix_l = prefix.lower()

    rt = _rt(context)
    items: list[Completion] = []
    if rt == "js":
        items.extend(_JS_SCOPE_VARS)
        items.extend(_JS_BS_COMPLETIONS)
        items.extend(_JS_DISCORD_COMPLETIONS)
        for kw in _JS_KEYWORDS:
            items.append(Completion(kw, kw, "keyword", "keyword", "js"))
    else:
        items.extend(_SCOPE_VARS)
        items.extend(_BS_COMPLETIONS)
        items.extend(_DISCORD_COMPLETIONS)
        for kw in _KEYWORDS:
            items.append(Completion(kw, kw, "keyword", "keyword"))
    items.extend(c for c in _extra_completions if _wants(c.runtime, rt))

    # Names from every node this one is attached to, typed members, stdlib, and the code's own definitions
    from . import editor_scope_assist as _esa
    scoped = _esa.extra_completions(source, cursor_pos, prefix, context, Completion, rt == "js")
    if "." in prefix and scoped:
        return scoped[:80]
    items = scoped + items
    seen_lbl: set[str] = set()
    items = [c for c in items if not (c.label in seen_lbl or seen_lbl.add(c.label))]
    for name in context.get("names") or []:
        if name not in seen_lbl:
            items.append(Completion(str(name), str(name), "from graph scope", "var"))

    if not prefix_l:
        # Prefer high-signal items when the user just hit Ctrl+Space
        priority = {"bs", "await", "interaction", "self", "discord", "bot"} | set(context.get("names") or [])
        items.sort(key=lambda c: (0 if c.label.split(".")[0] in priority else 1, c.label.lower()))
        return items[:80]

    matched = [c for c in items if c.label.lower().startswith(prefix_l)
               or prefix_l in c.label.lower()]
    # Also allow matching on last segment: "resp" → bs.respond
    if not matched:
        matched = [c for c in items if c.label.lower().rsplit(".", 1)[-1].startswith(prefix_l)]
    matched.sort(key=lambda c: (0 if c.label.lower().startswith(prefix_l) else 1, len(c.label), c.label.lower()))
    return matched[:60]


def apply_completion(source: str, cursor_pos: int, item: Completion) -> tuple[str, int]:
    """Replace the current prefix with item.insert. Returns (new_source, new_cursor)."""
    prefix, start = _prefix_at(source, cursor_pos)
    end = cursor_pos
    insert = item.insert
    # Avoid `await await ...` when the insert form still includes await
    line_start = source.rfind("\n", 0, start) + 1
    before = source[line_start:start]
    if insert.startswith("await ") and "await" in before:
        insert = insert[len("await "):]
    new = source[:start] + insert + source[end:]
    return new, start + len(insert)


# --------------------------------------------------------------------------- snippets
def snippets(runtime: str | None = None) -> list[Snippet]:
    """Snippets for the given mode ("python" default, or "js")."""
    rt = "js" if runtime == "js" else "python"
    base = _JS_SNIPPETS if rt == "js" else _BUILTIN_SNIPPETS
    return list(base) + [x for x in _extra_snippets if _wants(x.runtime, rt)]


# --------------------------------------------------------------------------- diagnostics
_BLOCKING_CALLS = [
    (re.compile(r"\btime\.sleep\s*\("), "time.sleep blocks the event loop",
     "Use `await asyncio.sleep(...)` instead."),
    (re.compile(r"\brequests\.(get|post|put|delete)\s*\("), "synchronous requests blocks the bot",
     "Use `await bs.http_get(...)` or aiohttp."),
    (re.compile(r"\binput\s*\("), "input() cannot be used in a bot handler",
     "Read values from interaction/message or storage."),
    (re.compile(r"\bbot\.run\s*\("), "bot.run() must not appear inside a Custom Python node",
     "The generated bot already runs the event loop."),
    (re.compile(r"\basyncio\.run\s*\("), "asyncio.run() nests event loops",
     "This handler is already async — just await coroutines."),
]


def _syntax_diagnostics(source: str) -> list[Diagnostic]:
    if not source.strip():
        return []
    # Indent as a suite so "top-level" await / return is valid in a function body
    indented = "\n".join(("    " + ln if ln.strip() else ln) for ln in source.splitlines())
    wrapped = "async def __bs_custom():\n" + (indented if indented.endswith("\n") else indented + "\n")
    try:
        ast.parse(wrapped)
        return []
    except SyntaxError as e:
        # e.lineno is 1-based inside wrapped function; line 1 is the def
        line = max(0, (e.lineno or 2) - 2)
        col = max(0, (e.offset or 1) - 1)
        msg = e.msg or "Syntax error"
        suggestion = _syntax_suggestion(msg, source, line)
        return [Diagnostic(line, col, msg, "error", suggestion)]


def _syntax_suggestion(msg: str, source: str, line: int) -> str:
    m = msg.lower()
    lines = source.splitlines()
    text = lines[line] if 0 <= line < len(lines) else ""
    if "unexpected eof" in m or "expected an indented block" in m:
        return "Add a body after `:` — e.g. `pass`, or finish the block."
    if "invalid syntax" in m and text.rstrip().endswith("="):
        return "Complete the assignment on the right-hand side."
    if "unmatched" in m or "never closed" in m or "eof while scanning" in m:
        return "Check for a missing closing `)`, `]`, `}`, or quote."
    if "perhaps you forgot a comma" in m:
        return "Add a comma between expressions, or wrap tuples properly."
    if "cannot assign to" in m:
        return "Left-hand side must be a name, index, or attribute — not a call/literal."
    return "Fix the syntax on this line, then re-check."


def _heuristic_diagnostics(source: str) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for i, ln in enumerate(source.splitlines()):
        stripped = ln.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for rx, message, suggestion in _BLOCKING_CALLS:
            if rx.search(ln):
                out.append(Diagnostic(i, 0, message, "warning", suggestion))
        # await missing on common coroutine helpers
        if re.search(r"(?<!\.)\bbs\.(respond|send|kv_get|kv_set|kv_incr|timeout|kick|ban|http_get|add_role|remove_role)\s*\(", ln):
            if "await" not in ln and not stripped.startswith("def ") and "lambda" not in ln:
                out.append(Diagnostic(
                    i, 0,
                    "bs helper is a coroutine and should be awaited",
                    "warning",
                    "Prefix with `await` — e.g. `await bs.respond(...)`.",
                ))
        if re.search(r"\binteraction\.response\.(send_message|defer)\s*\(", ln) and "await" not in ln:
            out.append(Diagnostic(
                i, 0,
                "interaction.response methods are coroutines",
                "warning",
                "Use `await interaction.response.send_message(...)` or `defer`.",
            ))
    return out


def analyze(source: str, context: dict | None = None) -> list[Diagnostic]:
    """Syntax + heuristic + plugin diagnostics."""
    context = context or {}
    if context.get("runtime") in ("html", "css", "browserjs"):
        from . import web_assist
        return web_assist.diagnostics(context["runtime"], source, Diagnostic)
    rt = _rt(context)
    if rt == "js":
        diags = _js_diagnostics(source)
    else:
        diags = _syntax_diagnostics(source)
        # If hard syntax error, still attach heuristics on other lines
        diags.extend(_heuristic_diagnostics(source))
    if context.get("vars") is not None and "vars" in context:
        from . import editor_scope_assist as _esa
        try:
            diags.extend(_esa.js_undefined(source, context, Diagnostic) if rt == "js"
                         else _esa.undefined_names(source, context, Diagnostic))
        except Exception:
            pass
    for prov in _diagnostic_providers:
        if not _wants(_provider_runtime.get(id(prov), "python"), rt):
            continue
        try:
            extra = prov(source, context) or []
            for d in extra:
                if isinstance(d, Diagnostic):
                    diags.append(d)
                elif isinstance(d, dict):
                    diags.append(Diagnostic(
                        int(d.get("line", 0)),
                        int(d.get("col", 0)),
                        str(d.get("message", "")),
                        str(d.get("severity", "warning")),
                        str(d.get("suggestion", "")),
                    ))
        except Exception:
            continue
    # de-dupe by (line, message)
    seen: set[tuple[int, str]] = set()
    uniq: list[Diagnostic] = []
    for d in diags:
        key = (d.line, d.message)
        if key not in seen:
            seen.add(key)
            uniq.append(d)
    uniq.sort(key=lambda d: (d.line, 0 if d.severity == "error" else 1, d.col))
    return uniq
