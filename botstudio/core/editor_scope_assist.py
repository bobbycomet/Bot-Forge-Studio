"""Scope-aware IDE features for the Custom Python / Custom JavaScript editors.

* names from every node the custom node is attached to complete with their type and origin
* `user.` / `interaction.` / `guild.` … complete real members for the connected variable's type
* `os.` / `json.` / `re.` … complete from the Python standard library (any stdlib module works)
* variables, functions, classes and imports defined in the code itself complete too
* names that nothing provides are flagged (pyflakes-style) with a hint about which node would provide them
"""
from __future__ import annotations

import ast
import builtins
import difflib
import importlib
import re
import sys

# members per Bot Forge Studio port type (discord.py names; the JS list covers discord.js-style access via bs.attr)
TYPE_MEMBERS = {
    "user": ["id", "name", "display_name", "global_name", "mention", "bot", "avatar", "display_avatar", "roles", "top_role",
             "guild", "guild_permissions", "joined_at", "created_at", "nick", "status", "voice", "send()", "add_roles()",
             "remove_roles()", "timeout()", "kick()", "ban()", "edit()", "move_to()", "get_role()", "is_timed_out()"],
    "channel": ["id", "name", "mention", "guild", "category", "topic", "position", "slowmode_delay", "nsfw", "send()",
                "purge()", "history()", "fetch_message()", "create_thread()", "create_webhook()", "set_permissions()",
                "edit()", "delete()", "threads", "members"],
    "guild": ["id", "name", "owner", "owner_id", "member_count", "members", "channels", "text_channels", "voice_channels",
              "roles", "emojis", "icon", "banner", "premium_tier", "get_member()", "get_channel()", "get_role()",
              "fetch_member()", "create_text_channel()", "create_role()", "ban()", "kick()", "default_role", "me"],
    "message": ["id", "content", "author", "channel", "guild", "mentions", "attachments", "embeds", "reference", "created_at",
                "jump_url", "reply()", "delete()", "edit()", "add_reaction()", "pin()", "create_thread()", "clean_content"],
    "interaction": ["user", "guild", "channel", "channel_id", "guild_id", "client", "command", "data", "response", "followup",
                    "message", "namespace", "permissions", "created_at", "locale", "original_response()",
                    "edit_original_response()", "delete_original_response()", "response.send_message()",
                    "response.defer()", "response.send_modal()", "response.edit_message()", "followup.send()"],
    "str": ["upper()", "lower()", "title()", "strip()", "split()", "join()", "replace()", "startswith()", "endswith()",
            "format()", "find()", "isdigit()", "zfill()", "casefold()", "splitlines()"],
    "int": ["bit_length()", "to_bytes()"],
    "request": ["query", "body", "params", "headers", "method", "path", "user", "ip", "get()", "cookies"],
    "response": ["status()", "json()", "send()", "redirect()", "set()", "type()", "end()", "cookie()"],
    "bs": ["respond()", "send()", "kvGet()", "kvSet()", "kvIncr()", "kvTop()", "kvList()", "httpGet()", "sleep()",
           "jsonGet()", "tryJson()", "safeUrl()", "guildId()", "guild()", "sendMessage()", "memberName()", "rest",
           "botStatus()", "log"],
}
TYPE_MEMBERS["attachment"] = ["filename", "url", "size", "content_type", "read()", "save()", "to_file()"]
TYPE_MEMBERS["role"] = ["id", "name", "mention", "color", "permissions", "position", "members", "edit()", "delete()"]
TYPE_MEMBERS["any"] = []

_PY_BUILTINS = set(dir(builtins))
_ALWAYS = {"self", "bs", "discord", "asyncio", "commands", "app_commands", "tasks", "__name__", "__file__"}
_JS_BUILTINS = set("""undefined null true false NaN Infinity console JSON Math Date Number String Boolean Array Object Map Set
Promise Symbol BigInt RegExp Error TypeError parseInt parseFloat isNaN setTimeout clearTimeout setInterval clearInterval
URL URLSearchParams fetch process require module exports Buffer encodeURIComponent decodeURIComponent structuredClone
await async var let const function return if else for while of in new this class try catch finally throw switch case break
continue typeof instanceof delete void default""".split())


def _split_member(name: str) -> str:
    return name[:-2] if name.endswith("()") else name


def _stdlib_members(mod: str) -> list[str]:
    if mod.split(".")[0] not in getattr(sys, "stdlib_module_names", ()):
        return []
    try:
        m = importlib.import_module(mod)
    except Exception:
        return []
    out = []
    for n in dir(m):
        if n.startswith("_"):
            continue
        try:
            out.append(n + ("()" if callable(getattr(m, n)) else ""))
        except Exception:
            out.append(n)
    return out


def python_locals(source: str) -> dict[str, str]:
    """name -> kind (var|func|class|mod) defined by the user's code."""
    found: dict[str, str] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        for m in re.finditer(r"^\s*(?:async\s+def|def|class)\s+(\w+)", source, re.M):
            found[m.group(1)] = "func"
        for m in re.finditer(r"^\s*([A-Za-z_]\w*)\s*=[^=]", source, re.M):
            found.setdefault(m.group(1), "var")
        for m in re.finditer(r"^\s*(?:import|from)\s+([\w.]+)(?:\s+import\s+([\w, ]+))?", source, re.M):
            found[m.group(1).split(".")[0]] = "mod"
        return found
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[n.name] = "func"
            for a in n.args.args + n.args.kwonlyargs:
                found.setdefault(a.arg, "var")
        elif isinstance(n, ast.ClassDef):
            found[n.name] = "class"
        elif isinstance(n, ast.Import):
            for a in n.names:
                found[(a.asname or a.name).split(".")[0]] = "mod"
        elif isinstance(n, ast.ImportFrom):
            for a in n.names:
                found[a.asname or a.name] = "var"
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            found.setdefault(n.id, "var")
        elif isinstance(n, ast.ExceptHandler) and n.name:
            found.setdefault(n.name, "var")
    return found


def _var_types(context: dict, local_src: str) -> dict[str, str]:
    types = {v["name"]: v.get("type", "any") for v in context.get("vars", [])}
    types.setdefault("bs", "bs")
    # `u = user` / `m = interaction.user` keeps the type
    for m in re.finditer(r"^\s*(\w+)\s*=\s*(\w+)(?:\.(user|guild|channel|author|member))?\s*$", local_src, re.M):
        dst, src, attr = m.groups()
        if attr:
            types.setdefault(dst, {"author": "user", "member": "user"}.get(attr, attr))
        elif src in types:
            types.setdefault(dst, types[src])
    return types


def extra_completions(source: str, pos: int, prefix: str, context: dict, Completion, js: bool) -> list:
    out = []
    types = _var_types(context, source)
    if "." in prefix:
        base = prefix.rsplit(".", 1)[0]
        root = base.split(".")[0]
        members: list[str] = []
        if base in types or root in types:
            t = types.get(base) or types.get(root, "any")
            if base != root and base.startswith(root + ".") and root in types:
                sub = base[len(root) + 1:]
                members = [m.split(".", 1)[1] for m in TYPE_MEMBERS.get(types[root], []) if m.startswith(sub + ".")]
                if not members:
                    t = {"guild": "guild", "channel": "channel", "author": "user", "user": "user"}.get(sub, "any")
                    members = TYPE_MEMBERS.get(t, [])
            else:
                members = TYPE_MEMBERS.get(t, [])
        elif not js:
            members = _stdlib_members(base)
            if not members and root in python_locals(source):
                members = []
        tail = prefix.rsplit(".", 1)[1].lower()
        members = [m for m in members if _split_member(m).lower().startswith(tail)] or \
                  [m for m in members if tail in _split_member(m).lower()]
        for m in members:
            lab = f"{base}.{_split_member(m)}"
            out.append(Completion(lab, lab + ("()" if m.endswith("()") else ""), _member_doc(types.get(root, "mod")), "func" if m.endswith("()") else "var"))
        return out
    for v in context.get("vars", []):
        out.append(Completion(v["name"], v["name"], f"{v.get('type', 'any')} · {v.get('origin', '')}".strip(" ·"), "var"))
    if not js:
        for n, kind in python_locals(source).items():
            out.append(Completion(n, n, f"defined in this code ({kind})", "var"))
        for mod in ("os", "re", "json", "math", "random", "time", "datetime", "itertools", "collections", "asyncio",
                    "typing", "functools", "string", "statistics", "textwrap", "uuid", "base64", "hashlib", "sqlite3",
                    "pathlib", "csv", "urllib", "decimal", "fractions", "heapq", "bisect", "copy", "operator"):
            out.append(Completion(mod, mod, "Python standard library", "mod"))
        for b in sorted(_PY_BUILTINS):
            if not b.startswith("_") and b[0].islower():
                out.append(Completion(b, b, "Python builtin", "func"))
    else:
        for m in re.finditer(r"\b(?:var|let|const|function)\s+([A-Za-z_$][\w$]*)", source):
            out.append(Completion(m.group(1), m.group(1), "defined in this code", "var"))
    for o in context.get("outputs", []):
        out.append(Completion(o, o, "output port — assign it to pass data to the next nodes", "var"))
    return out


def _member_doc(t: str) -> str:
    return f"member of {t}"


class _Undef(ast.NodeVisitor):
    def __init__(self, known: set[str]):
        self.known, self.bad = set(known), []

    def run(self, tree):
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                self.known.add(n.name)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                a = n.args
                for x in a.args + a.kwonlyargs + getattr(a, "posonlyargs", []):
                    self.known.add(x.arg)
                for x in (a.vararg, a.kwarg):
                    if x:
                        self.known.add(x.arg)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    self.known.add((a.asname or a.name).split(".")[0])
            elif isinstance(n, ast.ImportFrom):
                for a in n.names:
                    self.known.add(a.asname or a.name)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                self.known.add(n.name)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                self.known.add(n.id)
            elif isinstance(n, (ast.Global, ast.Nonlocal)):
                self.known.update(n.names)
        for n in ast.walk(tree):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in self.known:
                self.bad.append((n.lineno - 1, n.col_offset, n.id))
        return self.bad


def undefined_names(source: str, context: dict, Diagnostic) -> list:
    """pyflakes-lite: a name nothing provides is reported with a hint about which node/port would."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    known = _PY_BUILTINS | _ALWAYS | {v["name"] for v in context.get("vars", [])} | set(context.get("outputs", []))
    out = []
    for line, col, name in _Undef(known).run(tree):
        close = difflib.get_close_matches(name, sorted(known), n=1)
        hint = (f"did you mean `{close[0]}`? " if close else "") + \
               "Names come from the nodes wired before this one (trigger variables, earlier outputs, data inputs)."
        out.append(Diagnostic(line, col, f"undefined name '{name}'", "warning", hint))
    return out


def js_undefined(source: str, context: dict, Diagnostic) -> list:
    known = _JS_BUILTINS | {v["name"] for v in context.get("vars", [])} | set(context.get("outputs", []))
    known |= {m.group(1) for m in re.finditer(r"\b(?:var|let|const|function|class)\s+([A-Za-z_$][\w$]*)", source)}
    known |= {x for m in re.finditer(r"\(([^()]*)\)\s*=>", source) for x in re.findall(r"[A-Za-z_$][\w$]*", m.group(1))}
    known |= {x for m in re.finditer(r"function\s*[\w$]*\s*\(([^()]*)\)", source) for x in re.findall(r"[A-Za-z_$][\w$]*", m.group(1))}
    known |= {x for m in re.finditer(r"\b(?:const|let|var)\s*[\[{]([^\]}]*)[\]}]", source) for x in re.findall(r"[A-Za-z_$][\w$]*", m.group(1))}
    known |= {m.group(1) for m in re.finditer(r"\bcatch\s*\(\s*([A-Za-z_$][\w$]*)", source)}
    out = []
    stripped = re.sub(r"//[^\n]*|/\*.*?\*/|'(?:\\.|[^'\\\n])*'|\"(?:\\.|[^\"\\\n])*\"|`(?:\\.|[^`\\])*`", lambda m: " " * len(m.group(0)), source, flags=re.S)
    for ln, text in enumerate(stripped.splitlines()):
        for m in re.finditer(r"(?<![\w$.])([A-Za-z_$][\w$]*)\s*(?=[(.\[;,)=+\-*/<>!&|?:]|$)", text):
            n = m.group(1)
            if n in known or re.match(r"\s*:", text[m.end():]) and re.search(r"[{,]\s*$", text[:m.start()]):
                continue
            if re.match(r"\s*=[^=]", text[m.end():]) and n not in known:
                known.add(n)   # implicit global assignment; not our business
                continue
            if m.group(0).strip().endswith(":") or text[:m.start()].rstrip().endswith(("{", ",")) and ":" in text[m.end():m.end() + 3]:
                continue
            out.append(Diagnostic(ln, m.start(), f"'{n}' is not defined here", "info",
                                  "Names come from the nodes wired before this one."))
    return out[:25]
