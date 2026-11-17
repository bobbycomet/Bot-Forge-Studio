"""Turns a graph into Python (discord.py cogs).

Every node spec carries a pre-built code chunk (``spec.gen``). The generator
walks execution wires from each trigger, asks each node for its statements, and
resolves data wires into inline expressions (or cached variables).
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field

from .. import __version__
from .model import Graph, Node, get_spec

IDENT = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_PLACEHOLDER = re.compile(r"\{([A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)*)\}")


class GenError(Exception):
    pass


def slugify(text: str, fallback: str = "x") -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s or fallback


def literal(value, kind: str) -> str:
    """Python literal for a property value of the given kind."""
    if kind == "bool":
        if isinstance(value, str):
            return repr(value.strip().lower() in ("1", "true", "yes", "on"))
        return repr(bool(value))
    if kind == "int":
        try:
            return repr(int(str(value).strip()))      # exact for 64-bit Discord snowflakes
        except (TypeError, ValueError):
            try:
                return repr(int(float(value)))
            except (TypeError, ValueError):
                return "0"
    if kind == "float":
        try:
            return repr(float(value))
        except (TypeError, ValueError):
            return "0.0"
    if kind == "any":
        if isinstance(value, (int, float, bool)):
            return repr(value)
        s = str(value if value is not None else "")
        t = s.strip()
        if re.fullmatch(r"-?\d+", t):
            return t
        if re.fullmatch(r"-?\d*\.\d+", t):
            return t
        if t.lower() in ("true", "false"):
            return t.capitalize()
        return repr(s)
    return repr("" if value is None else str(value))


def render_template(text: str, scope: dict) -> str:
    """'Hi {user.mention}!' -> ('Hi ' + str(interaction.user.mention) + '!')

    Only plain dotted attribute chains whose first name is in *scope* are
    substituted — never calls or arbitrary expressions — so a shared project
    file cannot smuggle code in through a text field.
    """
    parts, pos = [], 0
    for m in _PLACEHOLDER.finditer(text or ""):
        root, *rest = m.group(1).split(".")
        if root not in scope:
            continue
        if m.start() > pos:
            parts.append(repr(text[pos:m.start()]))
        parts.append("str(" + scope[root] + "".join("." + r for r in rest) + ")")
        pos = m.end()
    if pos < len(text or ""):
        parts.append(repr(text[pos:]))
    if not parts:
        return "''"
    if len(parts) == 1:
        return parts[0]
    return "(" + " + ".join(parts) + ")"


@dataclass
class CogSource:
    slug: str
    path: str
    source: str
    commands: list = field(default_factory=list)
    intents: set = field(default_factory=set)
    requires: set = field(default_factory=set)
    env: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    node_ids: list = field(default_factory=list)
    extra_files: dict = field(default_factory=dict)


class GenCtx:
    """Passed to every node's ``gen`` function."""

    def __init__(self, graph: Graph):
        self.g = graph
        self.node: Node | None = None
        self.lines: list[str] = []
        self.ind = 0
        self.warnings: list[str] = []
        self.scope: dict = {}
        self.outs: dict = {}
        self.caps: set = set()
        self.decorators: list[str] = []
        self.name = "handler"
        self.signature = ""
        self.preamble: list[str] = []
        self.class_lines: list[str] = []
        self.on_load: list[str] = []
        self.on_unload: list[str] = []
        self.commands: list[str] = []
        self._assigned: list[dict] = [{}]
        self._stack: list[str] = []

    # -- basics -----------------------------------------------------------
    def emit(self, line: str = ""):
        self.lines.append("    " * self.ind + line if line else "")

    def warn(self, msg: str):
        who = f"[{self.node.id}] " if self.node else ""
        self.warnings.append(who + msg)

    def has(self, cap: str) -> bool:
        return cap in self.caps

    def prop(self, key: str):
        return self.node.prop_value(key)

    def var(self, port: str) -> str:
        return f"v_{self.node.id}_{port}"

    def default_user(self) -> str | None:
        for k in ("user", "author", "member"):
            if k in self.scope:
                return self.scope[k]
        return None

    @contextmanager
    def _enter(self, node: Node):
        prev = self.node
        self.node = node
        try:
            yield
        finally:
            self.node = prev

    # -- values -------------------------------------------------------------
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
            return render_template(str(value or ""), self.scope)
        return literal(value, p.kind)

    def is_blank(self, key: str) -> bool:
        if self.g.incoming(self.node.id, key):
            return False
        return str(self.node.prop_value(key) or "").strip() == ""

    def expr_of(self, node_id: str, port: str) -> str:
        src = self.g.nodes.get(node_id)
        if src is None or src.spec is None:
            raise GenError(f"Missing node {node_id}")
        spec = src.spec
        if spec.kind == "trigger":
            if port not in self.outs:
                raise GenError(f"{spec.title}: output '{port}' is not available in this context")
            return self.outs[port]
        if spec.kind == "data":
            return self._data_expr(src, port)
        return f"v_{src.id}_{port}"

    def _data_expr(self, node: Node, port: str) -> str:
        spec = node.spec
        if spec.cache:
            for scope in reversed(self._assigned):
                if node.id in scope:
                    return scope[node.id]
            if node.id in self._stack:
                raise GenError(f"Data loop through {spec.title} [{node.id}]")
            self._stack.append(node.id)
            with self._enter(node):
                expr = spec.gen(self, port)
            self._stack.pop()
            var = f"v_{node.id}"
            self.emit(f"{var} = {expr}")
            self._assigned[-1][node.id] = var
            return var
        if node.id in self._stack:
            raise GenError(f"Data loop through {spec.title} [{node.id}]")
        self._stack.append(node.id)
        try:
            with self._enter(node):
                return spec.gen(self, port)
        finally:
            self._stack.pop()

    # -- message helpers ---------------------------------------------------------
    def embed_expr(self) -> str:
        t, d, c, f = (self.val("embed_title"), self.val("embed_description"),
                      self.val("embed_color"), self.val("embed_footer"))
        if all(x == "''" for x in (t, d, f)):
            return "None"
        return f"bs.embed({t}, {d}, {c}, {f})"

    # -- control flow ---------------------------------------------------------------
    def chain(self, port: str) -> int:
        """Emit everything wired after exec output *port* of the current node."""
        node = self.node
        edges = sorted(self.g.outgoing(node.id, port),
                       key=lambda e: (self.g.nodes[e.dst].y, self.g.nodes[e.dst].x))
        before = len(self.lines)
        for e in edges:
            self.run_node(self.g.nodes[e.dst])
        return len(self.lines) - before

    @contextmanager
    def block(self, port: str | None = None):
        """Indented block; when *port* is given, its chain is emitted and `pass` added if empty."""
        self.ind += 1
        self._assigned.append({})
        start = len(self.lines)
        try:
            if port is not None:
                self.chain(port)
            yield
        finally:
            if len(self.lines) == start:
                self.emit("pass")
            self._assigned.pop()
            self.ind -= 1

    def run_node(self, node: Node):
        spec = node.spec
        if spec is None:
            self.warn(f"Unknown node type '{node.type_id}' skipped")
            return
        if spec.kind in ("data", "trigger", "module"):
            return
        if node.id in self._stack:
            raise GenError(f"Execution loop at {spec.title} [{node.id}] — loops are not supported")
        self._stack.append(node.id)
        try:
            with self._enter(node):
                self.emit(f"# [{node.id}] {spec.title}")
                spec.gen(self)
                if spec.kind == "action" and any(p.name == "next" for p in spec.outputs):
                    self.chain("next")
        finally:
            self._stack.pop()


def _class_name(slug: str) -> str:
    return "".join(w.capitalize() for w in slug.split("_")) + "Cog"


def compile_component(graph: Graph, comp: list[Node], used_slugs: set) -> CogSource | None:
    """Compile one connected group of nodes into a cog file."""
    modules = [n for n in comp if n.spec and n.spec.kind == "module"]
    triggers = [n for n in comp if n.spec and n.spec.kind == "trigger"]

    if modules:
        node = modules[0]
        spec = node.spec
        slug = _unique(f"mod_{slugify(spec.type_id.split('.')[-1])}_{node.id}", used_slugs)
        files = spec.files(node, slug) if spec.files else {}
        if spec.standalone:
            return CogSource(slug, "", "", [], set(spec.intents), set(spec.requires), list(spec.env), [],
                             [node.id], files)
        main = files.pop(f"cogs/{slug}.py", "")
        cmds = list(spec.commands(node.props)) if spec.commands else []
        if spec.bundle is not None and not cmds:
            cmds = ["*"]  # unknown command set: resync whenever a bundle changes
        return CogSource(slug, f"cogs/{slug}.py", main, cmds, set(spec.intents), set(spec.requires),
                         list(spec.env), [], [node.id], files)

    if not triggers:
        orphans = [n for n in comp if n.spec and n.spec.kind in ("action", "flow")]
        if orphans:
            cs = CogSource("", "", "", warnings=[
                f"[{n.id}] {n.spec.title} is not connected to any trigger and was skipped" for n in orphans])
            return cs
        return None

    first = triggers[0]
    slug = _unique(f"{slugify(first.spec.title)}_{first.id}", used_slugs)
    cog = CogSource(slug, f"cogs/{slug}.py", "", node_ids=[n.id for n in comp])
    handlers: list[str] = []
    class_lines: list[str] = []
    on_load: list[str] = []
    on_unload: list[str] = []

    for t in triggers:
        spec = t.spec
        ctx = GenCtx(graph)
        ctx.node = t
        ctx.name = f"{slugify(spec.title)}_{t.id}"
        ctx.ind = 2
        try:
            spec.gen(ctx)
            for line in ctx.preamble:
                ctx.emit(line)
            ctx.chain("next")
        except GenError as e:
            raise GenError(f"{spec.title} [{t.id}]: {e}") from None
        body = ctx.lines or ["        pass"]
        if not any(ln.strip() and not ln.strip().startswith("#") for ln in body):
            body.append("        pass")
        out = ["    " + d for d in ctx.decorators]
        out.append(f"    async def {ctx.name}({ctx.signature}):")
        out.extend(body)
        handlers.append("\n".join(out))
        class_lines += ctx.class_lines
        on_load += ctx.on_load
        on_unload += ctx.on_unload
        cog.commands += ctx.commands
        cog.warnings += ctx.warnings
        cog.intents |= set(spec.intents)

    # specs used anywhere in the component
    for n in comp:
        s = n.spec
        if s is None:
            continue
        cog.intents |= set(s.intents)
        cog.requires |= set(s.requires)
        cog.env += list(s.env)

    reachable = _reachable(graph, triggers)
    for n in comp:
        if n.spec and n.spec.kind in ("action", "flow") and n.id not in reachable:
            cog.warnings.append(f"[{n.id}] {n.spec.title} is not reachable from a trigger")

    cls = _class_name(slug)
    src = [
        f"# Generated by Bot Forge Studio {__version__} — edit the graph, not this file.",
        "from __future__ import annotations",
        "",
        "import asyncio",
        "import discord",
        "from discord import app_commands",
        "from discord.ext import commands, tasks",
        "import bs_runtime as bs",
        "",
        "",
        f"class {cls}(commands.Cog):",
        "    def __init__(self, bot: commands.Bot):",
        "        self.bot = bot",
        "",
    ]
    if on_load:
        src.append("    async def cog_load(self):")
        src += [f"        {x}" for x in on_load]
        src.append("")
    if on_unload:
        src.append("    async def cog_unload(self):")
        src += [f"        {x}" for x in on_unload]
        src.append("")
    for cl in class_lines:
        src.append(f"    {cl}" if cl else "")
    src.append("")
    src.append("\n\n".join(handlers))
    src += ["", "", "async def setup(bot: commands.Bot):", f"    await bot.add_cog({cls}(bot))", ""]
    cog.source = "\n".join(src)
    return cog


def _reachable(graph: Graph, triggers: list[Node]) -> set:
    seen: set[str] = set()
    stack = [t.id for t in triggers]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        node = graph.nodes[cur]
        _, outs = node.ports()
        for p in outs:
            if p.kind == "exec":
                stack += [e.dst for e in graph.outgoing(cur, p.name)]
    return seen


def _unique(slug: str, used: set) -> str:
    base, i = slug, 2
    while slug in used:
        slug = f"{base}_{i}"
        i += 1
    used.add(slug)
    return slug
