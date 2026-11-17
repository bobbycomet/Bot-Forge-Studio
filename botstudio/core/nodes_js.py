"""Dashboard-side (discord.js) twins of the nodes that make sense inside a Web Route.

The bot is discord.py; the web dashboard is discord.js.  A Web Route flow runs in the dashboard
process, so only nodes that do not need the live Discord gateway get a ``spec.gen_js`` here:
storage, flow, data, JSON, HTTP, delay, log, and *Send to Channel* (over Discord's REST API).
Bot-only nodes (Reply, roles, moderation …) intentionally have no JS twin; using one inside a
Web Route is a build error that says so.

Plugin authors: set ``spec.gen_js`` (and ``requires_js``) in ``register_nodes(model)``, or ship a
``graph_nodes_js.py`` with ``register_nodes_js(model)`` that attaches generators to specs the
Python side registered.  See ``DASHBOARD.md``.
"""
from __future__ import annotations

import re

from .codegen import GenError
from .codegen_js import jsstr, literal_js, render_template_js
from .model import NodeSpec, Prop, get_spec, register
from .nodes import D, EX

# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------

def js(type_id: str):
    """Decorator: attach a JS generator to an already-registered node type."""
    def deco(fn):
        spec = get_spec(type_id)
        if spec is None:
            raise KeyError(f"nodes_js: unknown node type {type_id}")
        spec.gen_js = fn
        return fn
    return deco


def _if_else(ctx, cond: str):
    ctx.emit(f"if ({cond}) {{")
    with ctx.sub("true"):
        pass
    if ctx.g.outgoing(ctx.node.id, "false"):
        ctx.emit("} else {")
        with ctx.sub("false"):
            pass
    ctx.emit("}")


# ---------------------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------------------

@js("action.send")
def gen_send_channel(ctx):
    """Post to a channel over REST. The channel must belong to the server in the request (checked at runtime)."""
    ctx.emit(f"var {ctx.var('sent')} = await bs.sendMessage(req, {ctx.val('channel')}, {ctx.val('content')});")


@js("action.log")
def gen_log(ctx):
    level = str(ctx.prop("level") or "info")
    if level not in ("debug", "info", "warning", "error"):
        level = "info"
    ctx.emit(f"bs.log.{level}('%s', {ctx.val('text')});")


@js("action.http_get")
def gen_http(ctx):
    v = ctx.var
    ctx.emit(f"var {v('_r')} = await bs.httpGet({ctx.val('url')});")
    ctx.emit(f"var {v('status')} = {v('_r')}.status;")
    ctx.emit(f"var {v('text')} = {v('_r')}.text;")
    ctx.emit(f"var {v('json')} = bs.tryJson({v('text')});")


@js("action.delay")
def gen_delay(ctx):
    ctx.emit(f"await bs.sleep(Number({ctx.val('seconds')}) * 1000);")


# -- Custom JavaScript (the JS-mode twin of Custom Python) ------------------------------

def _custom_ports_js(props):
    from .scope import custom_names
    return ([D(n) for n in custom_names(props.get("inputs"), "")],
            [D(n) for n in custom_names(props.get("outputs"), "result")])


def gen_custom_js(ctx):
    from .scope import custom_names, referenced_aliases, scope_for
    node = ctx.node
    code = str(ctx.prop("code") or "").rstrip()
    ins = custom_names(node.props.get("inputs"), "")
    outs = custom_names(node.props.get("outputs"), "result")
    for n in ins:
        e = ctx.g.incoming(node.id, n)
        ctx.emit(f"var {n} = {ctx.expr_of(e.src, e.src_port) if e else 'null'};")
    for v in referenced_aliases(code, scope_for(ctx.g, node, js=True)):
        if v.name not in ins:
            ctx.emit(f"var {v.name} = {v.expr};")
    for o in outs:
        ctx.emit(f"var {o} = null;")
    if code.strip():
        ctx.emit("{")
        with ctx.sub():
            for ln in code.splitlines():
                ctx.emit(ln)
        ctx.emit("}")
    for o in outs:
        ctx.emit(f"var {ctx.var(o)} = {o};")


register(NodeSpec(
    "action.js", "Server JavaScript (Web Route)", "Custom", "action",
    inputs=[EX("in")], outputs=[EX()],
    props=[Prop("code", "JavaScript (runs inside the handler)", "text",
                "// `req`, `res`, `bs` and everything wired before this node are in scope.\n"
                "// Any JavaScript works here. Assign `result` to pass data on.\n"),
           Prop("inputs", "Data inputs (names, comma separated)", "str", ""),
           Prop("outputs", "Data outputs (names, comma separated)", "str", "result")],
    dynamic_ports=_custom_ports_js, gen_js=gen_custom_js, web_only=True,
    description="SERVER-side JavaScript for Web Routes (Node.js on the dashboard server — not the browser): raw JavaScript (an async function body), aware of every node it is "
                "attached to. Use `var` to share a variable with later nodes. Only open projects you trust."))


# ---------------------------------------------------------------------------------------
# flow
# ---------------------------------------------------------------------------------------

@js("flow.branch")
def gen_branch(ctx):
    _if_else(ctx, f"bs.truthy({ctx.val('condition')})")


@js("flow.chance")
def gen_chance(ctx):
    _if_else(ctx, f"(Math.random() * 100 < Number({ctx.val('percent')}))")


@js("flow.stop")
def gen_stop(ctx):
    ctx.emit("return;")


@js("flow.match_text")
def gen_match_text(ctx):
    text = ctx.val("text", '""')
    pattern = jsstr(str(ctx.prop("pattern") or "").lower())
    mode = str(ctx.prop("mode") or "contains")
    t = f"String({text}).toLowerCase()"
    cond = {"equals": f"({t} === {pattern})", "startswith": f"{t}.startsWith({pattern})",
            "endswith": f"{t}.endsWith({pattern})"}.get(mode, f"{t}.includes({pattern})")
    _if_else(ctx, cond)


# ---------------------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------------------

get_spec("data.text").gen_js = lambda ctx, port: ctx.val("value")
get_spec("data.number").gen_js = lambda ctx, port: (
    literal_js(ctx.prop("value"), "float") if "." in str(ctx.prop("value")) else literal_js(ctx.prop("value"), "int"))


@js("data.format")
def gen_format(ctx, port):
    local = {}
    ins, _ = ctx.node.ports()
    for p in ins:
        e = ctx.g.incoming(ctx.node.id, p.name)
        if e:
            local[p.name] = ctx.expr_of(e.src, e.src_port)
    return render_template_js(str(ctx.prop("template") or ""), {**ctx.scope, **local})


@js("data.random_int")
def gen_rand_int(ctx, port):
    return f"bs.randInt({ctx.val('low')}, {ctx.val('high')})"


@js("data.random_choice")
def gen_rand_choice(ctx, port):
    items = [x.strip() for x in str(ctx.prop("items") or "").split("|") if x.strip()] or [""]
    return "bs.choice([" + ", ".join(jsstr(i) for i in items) + "])"


@js("data.compare")
def gen_compare(ctx, port):
    op = str(ctx.prop("op"))
    a, b = ctx.val("a"), ctx.val("b")
    if op == "contains":
        return f"bs.contains({a}, {b})"
    if op == "==":
        return f"bs.eq({a}, {b})"
    if op == "!=":
        return f"(!bs.eq({a}, {b}))"
    return f"bs.cmp({a}, {b}, {jsstr(op if op in ('>', '<', '>=', '<=') else '==')})"


@js("data.logic")
def gen_logic(ctx, port):
    op = str(ctx.prop("op"))
    if op == "not":
        return f"(!bs.truthy({ctx.val('a')}))"
    joiner = "&&" if op == "and" else "||"
    return f"(bs.truthy({ctx.val('a')}) {joiner} bs.truthy({ctx.val('b')}))"


@js("data.math")
def gen_math(ctx, port):
    op = str(ctx.prop("op"))
    a, b = f"Number({ctx.val('a')})", f"Number({ctx.val('b')})"
    if op == "//":
        return f"Math.floor({a} / {b})"
    if op == "%":
        return f"bs.mod({a}, {b})"
    if op not in ("+", "-", "*", "/"):
        op = "+"
    return f"({a} {op} {b})"


@js("data.env")
def gen_env(ctx, port):
    return f"(process.env[{jsstr(str(ctx.prop('name')))}] ?? {jsstr(str(ctx.prop('default')))})"


get_spec("data.now").gen_js = lambda ctx, port: "Math.floor(Date.now() / 1000)"


@js("data.string")
def gen_string_op(ctx, port):
    op = str(ctx.prop("op") or "strip")
    s = f"String({ctx.val('text', chr(34) * 2)})"
    return {
        "upper": f"{s}.toUpperCase()", "lower": f"{s}.toLowerCase()", "title": f"bs.title({s})",
        "length": f"[...{s}].length", "reverse": f"[...{s}].reverse().join('')",
        "replace": f"bs.replaceAll({s}, {ctx.val('find')}, {ctx.val('repl')})",
    }.get(op, f"{s}.trim()")


@js("data.concat")
def gen_concat(ctx, port):
    return f"(String({ctx.val('a')}) + String({ctx.val('b')}))"


@js("data.coalesce")
def gen_coalesce(ctx, port):
    return f"bs.coalesce({ctx.val('a')}, {ctx.val('b')})"


@js("data.if_value")
def gen_ternary(ctx, port):
    return f"(bs.truthy({ctx.val('condition')}) ? {ctx.val('when_true')} : {ctx.val('when_false')})"


@js("data.clamp")
def gen_clamp(ctx, port):
    return f"Math.min(Math.max(Number({ctx.val('value')}), Number({ctx.val('low')})), Number({ctx.val('high')}))"


@js("data.snowflake_time")
def gen_snowflake_time(ctx, port):
    return f"bs.snowflakeTime({ctx.val('id', '0')})"


# ---------------------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------------------

@js("store.get")
def gen_kv_get(ctx):
    ctx.emit(f"var {ctx.var('value')} = await bs.kvGet({ctx.val('namespace')}, {ctx.val('key')}, {ctx.val('default')});")


@js("store.set")
def gen_kv_set(ctx):
    ctx.emit(f"await bs.kvSet({ctx.val('namespace')}, {ctx.val('key')}, {ctx.val('value')});")


@js("store.incr")
def gen_kv_incr(ctx):
    ctx.emit(f"var {ctx.var('value')} = await bs.kvIncr({ctx.val('namespace')}, {ctx.val('key')}, {ctx.val('amount')});")


@js("store.delete")
def gen_kv_delete(ctx):
    ctx.emit(f"await bs.kvSet({ctx.val('namespace')}, {ctx.val('key')}, null);")


# ---------------------------------------------------------------------------------------
# NEW shared nodes (work in both modes): JSON helpers for HTTP / web / storage data
# ---------------------------------------------------------------------------------------

def _py_json_get(ctx, port):
    return f"bs.json_get({ctx.val('source')}, {ctx.val('path')}, {ctx.val('default')})"


register(NodeSpec(
    "data.json_get", "Get JSON Field", "Data", "data",
    inputs=[D("source"), D("path", "str")], outputs=[D("value")],
    props=[Prop("path", "Path", "str", "data.items.0.name", port=True,
                help="Dotted path into the JSON (numbers index lists), e.g. results.0.title"),
           Prop("default", "If missing", "any", "")],
    gen=_py_json_get,
    gen_js=lambda ctx, port: f"bs.jsonGet({ctx.val('source')}, {ctx.val('path')}, {ctx.val('default')})",
    description="Read one field out of parsed JSON (an HTTP GET 'json' output, a web request body, …)."))


def _obj_ports(props):
    keys = _obj_keys(props)
    return [D(k) for k in keys], []


def _obj_keys(props) -> list[str]:
    out = []
    for k in re.split(r"[,\s]+", str(props.get("keys", ""))):
        k = k.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", k) and k not in out:
            out.append(k)
    return out


def _py_object(ctx, port):
    pairs = ", ".join(f"{k!r}: {ctx.val(k, 'None')}" for k in _obj_keys(ctx.node.props))
    return "{" + pairs + "}"


def _js_object(ctx, port):
    pairs = ", ".join(f"{jsstr(k)}: {ctx.val(k, 'null')}" for k in _obj_keys(ctx.node.props))
    return "{ " + pairs + " }" if pairs else "{}"


register(NodeSpec(
    "data.object", "Build JSON Object", "Data", "data",
    outputs=[D("json")],
    props=[Prop("keys", "Field names (comma separated)", "str", "name, count")],
    dynamic_ports=_obj_ports, gen=_py_object, gen_js=_js_object,
    description="Each field name becomes an input you can wire. Output a JSON object for web responses or storage."))
