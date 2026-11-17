"""Turns Web Route flows into JavaScript for the web dashboard (discord.js side).

The bot itself is always discord.py (see :mod:`codegen`); this module only compiles the part of the
graph that lives in the dashboard.  Same idea as the Python generator: every node spec may carry a
second pre-built chunk, ``spec.gen_js``.  The generator walks execution wires from each Web Route
trigger, asks each node for its statements and resolves data wires into inline expressions.

Each connected group becomes one CommonJS file ``dashboard/routes/<slug>.js`` exporting a list of
route objects; the dashboard server loads them and hot-reloads them when they change.
"""
from __future__ import annotations

import json
import re
from contextlib import contextmanager

from .. import __version__
from dataclasses import dataclass, field

from .codegen import GenCtx, GenError, IDENT, _reachable, _unique, slugify
from .model import Graph, Node

__all__ = ["GenError", "GenCtxJS", "jsstr", "literal_js", "render_template_js", "compile_web_component", "WebSource",
           "IDENT", "slugify"]

MAX_SAFE = 9007199254740991
_PLACEHOLDER_JS = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)*)\}")

JS_EMPTY = '""'


def jsstr(value) -> str:
    """A JavaScript string literal.  JSON is valid JS, so user text can never break out of it."""
    s = json.dumps("" if value is None else str(value), ensure_ascii=False)
    return s.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def literal_js(value, kind: str) -> str:
    """JS literal for a property value of the given kind.

    Integers beyond 2**53 (Discord snowflakes) are emitted as *strings* — that is how
    discord.js represents IDs, and a JS number would silently lose precision.
    """
    if kind == "bool":
        if isinstance(value, str):
            return "true" if value.strip().lower() in ("1", "true", "yes", "on") else "false"
        return "true" if value else "false"
    if kind == "int":
        try:
            n = int(str(value).strip())
        except (TypeError, ValueError):
            try:
                n = int(float(value))
            except (TypeError, ValueError, OverflowError):
                return "0"
        return jsstr(str(n)) if abs(n) > MAX_SAFE else str(n)
    if kind == "float":
        try:
            f = float(value)
        except (TypeError, ValueError):
            return "0"
        if f != f or f in (float("inf"), float("-inf")):
            return "0"
        return repr(int(f)) if f.is_integer() and abs(f) < 1e15 else repr(f)
    if kind == "any":
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return literal_js(value, "int" if isinstance(value, int) else "float")
        s = str(value if value is not None else "")
        t = s.strip()
        if re.fullmatch(r"-?\d+", t):
            return literal_js(t, "int")
        if re.fullmatch(r"-?\d*\.\d+", t):
            return t
        if t.lower() in ("true", "false"):
            return t.lower()
        return jsstr(s)
    return jsstr(value)


def render_template_js(text: str, scope: dict) -> str:
    """'Hi {user.mention}!' -> ("Hi " + bs.s(bs.attr(user, "mention")) + "!")

    Only plain dotted attribute chains whose first name is in *scope* are substituted — never
    calls or arbitrary expressions — so a shared project file cannot smuggle code in through a
    text field.  ``bs.attr`` understands the Python-style names (``mention``, ``display_name``,
    ``name`` …) as well as native discord.js ones, so the same project works in both modes.
    """
    text = text or ""
    parts, pos = [], 0
    for m in _PLACEHOLDER_JS.finditer(text):
        root, *rest = m.group(1).split(".")
        if root not in scope:
            continue
        if m.start() > pos:
            parts.append(jsstr(text[pos:m.start()]))
        if rest:
            parts.append(f"bs.s(bs.attr({scope[root]}, {jsstr('.'.join(rest))}))")
        else:
            parts.append(f"bs.s({scope[root]})")
        pos = m.end()
    if pos < len(text):
        parts.append(jsstr(text[pos:]))
    if not parts:
        return JS_EMPTY
    if len(parts) == 1:
        return parts[0]
    return "(" + " + ".join(parts) + ")"


class GenCtxJS(GenCtx):
    """Passed to every node's ``gen_js`` function (same surface as the Python ``GenCtx``)."""

    def __init__(self, graph: Graph):
        super().__init__(graph)
        # filled in by trigger nodes
        self.js_kind = "events"        # commands | events | intervals | routes
        self.js_head: list[str] = []   # object-literal properties placed before the handler
        self.js_method = "run"
        self.partials: set[str] = set()

    # -- basics ------------------------------------------------------------
    def emit(self, line: str = ""):
        self.lines.append("  " * self.ind + line if line else "")

    def val(self, key: str, default: str | None = None) -> str:
        """Expression for input *key*: connected wire > inline property > default."""
        node = self.node
        edge = self.g.incoming(node.id, key)
        if edge is not None:
            return self.expr_of(edge.src, edge.src_port)
        p = node.spec.prop(key)
        if p is None:
            if default is None:
                raise GenError(f"{node.spec.title} [{node.id}]: input '{key}' needs a connection")
            return default
        value = node.prop_value(key)
        if p.fmt and p.kind in ("str", "text"):
            return render_template_js(str(value or ""), self.scope)
        return literal_js(value, p.kind)

    def embed_expr(self) -> str:
        t, d, c, f = (self.val("embed_title"), self.val("embed_description"),
                      self.val("embed_color"), self.val("embed_footer"))
        if all(x == JS_EMPTY for x in (t, d, f)):
            return "null"
        return f"bs.embed({t}, {d}, {c}, {f})"

    # -- data nodes ----------------------------------------------------------
    def _gen_expr(self, node: Node, port: str) -> str:
        spec = node.spec
        if spec.gen_js is None:
            raise GenError(_no_js(spec, node))
        with self._enter(node):
            return spec.gen_js(self, port)

    def _data_expr(self, node: Node, port: str) -> str:
        spec = node.spec
        if spec.cache:
            for scope in reversed(self._assigned):
                if node.id in scope:
                    return scope[node.id]
            if node.id in self._stack:
                raise GenError(f"Data loop through {spec.title} [{node.id}]")
            self._stack.append(node.id)
            try:
                expr = self._gen_expr(node, port)
            finally:
                self._stack.pop()
            var = f"v_{node.id}"
            self.emit(f"var {var} = {expr};")
            self._assigned[-1][node.id] = var
            return var
        if node.id in self._stack:
            raise GenError(f"Data loop through {spec.title} [{node.id}]")
        self._stack.append(node.id)
        try:
            return self._gen_expr(node, port)
        finally:
            self._stack.pop()

    # -- control flow ----------------------------------------------------------
    @contextmanager
    def sub(self, port: str | None = None):
        """Indented scope for a ``{ ... }`` block the caller opens/closes; chains *port* when given."""
        self.ind += 1
        self._assigned.append({})
        try:
            if port is not None:
                self.chain(port)
            yield
        finally:
            self._assigned.pop()
            self.ind -= 1

    def run_node(self, node: Node):
        spec = node.spec
        if spec is None:
            self.warn(f"Unknown node type '{node.type_id}' skipped")
            return
        if spec.kind in ("data", "trigger", "module"):
            return
        if spec.gen_js is None:
            raise GenError(_no_js(spec, node))
        if node.id in self._stack:
            raise GenError(f"Execution loop at {spec.title} [{node.id}] — loops are not supported")
        self._stack.append(node.id)
        try:
            with self._enter(node):
                self.emit(f"// [{node.id}] {spec.title}")
                spec.gen_js(self)
                if spec.kind == "action" and any(p.name == "next" for p in spec.outputs):
                    self.chain("next")
        finally:
            self._stack.pop()


def _indent(lines: list[str], n: int) -> list[str]:
    pad = "  " * n
    return [pad + ln if ln.strip() else "" for ln in lines]


def _no_js(spec, node) -> str:
    return (f"{spec.title} [{node.id}] works in the bot, not inside a Web Route. Web Routes can use storage, flow, "
            f"data, JSON, HTTP and Custom JavaScript nodes.")


@dataclass
class WebSource:
    slug: str
    path: str
    source: str
    warnings: list = field(default_factory=list)
    requires: set = field(default_factory=set)
    env: list = field(default_factory=list)
    node_ids: list = field(default_factory=list)


def compile_web_component(graph: Graph, comp: list[Node], used_slugs: set) -> WebSource | None:
    """Compile one connected group of nodes (Web Route flows) into ``routes/<slug>.js``."""
    triggers = [n for n in comp if n.spec and n.spec.kind == "trigger"]
    if not triggers:
        orphans = [n for n in comp if n.spec and n.spec.kind in ("action", "flow")]
        if orphans:
            return WebSource("", "", "", warnings=[
                f"[{n.id}] {n.spec.title} is not connected to a Web Route and was skipped" for n in orphans])
        return None
    for t in triggers:
        if not t.spec.web_only:
            raise GenError(f"{t.spec.title} [{t.id}] is a bot trigger — it cannot share a flow with a Web Route")
    first = triggers[0]
    slug = _unique(f"{slugify(first.spec.title)}_{first.id}", used_slugs)
    out = WebSource(slug, f"dashboard/routes/{slug}.js", "", node_ids=[n.id for n in comp])
    handlers: list[str] = []
    for t in triggers:
        spec = t.spec
        ctx = GenCtxJS(graph)
        ctx.node = t
        ctx.ind = 0
        try:
            spec.gen_js(ctx)
            for line in ctx.preamble:
                ctx.emit(line)
            ctx.chain("next")
        except GenError as e:
            raise GenError(f"{spec.title} [{t.id}]: {e}") from None
        lines = ["{"] + _indent(ctx.js_head, 1) + [f"  async run({ctx.signature}) {{"] + _indent(ctx.lines, 2) + ["  },", "},"]
        handlers.append("\n".join(_indent(lines, 1)))
        out.warnings += ctx.warnings
    for n in comp:
        s = n.spec
        if s is not None:
            out.requires |= set(s.requires_js)
            out.env += list(s.env)
    reachable = _reachable(graph, triggers)
    for n in comp:
        if n.spec and n.spec.kind in ("action", "flow") and n.id not in reachable:
            out.warnings.append(f"[{n.id}] {n.spec.title} is not reachable from a Web Route")
    out.source = "\n".join([
        f"// Generated by Bot Forge Studio {__version__} — edit the graph, not this file.",
        "'use strict';",
        "// eslint-disable-next-line no-unused-vars",
        "const bs = require('../bs_dash');",
        "",
        "module.exports = [",
        "\n".join(handlers),
        "];",
        "",
    ])
    return out
