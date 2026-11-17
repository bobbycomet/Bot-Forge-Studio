"""Editor scope: what a Custom Python / Custom JavaScript node can see.

A custom node is "aware" of every node it is attached to: the trigger it hangs off (interaction,
user, command parameters …), the outputs of every action node earlier in the flow, and anything
wired into its own data inputs.  The editor uses this for completion, attribute completion and
undefined-name diagnostics; codegen uses the same table to emit readable aliases, so a name that
completes in the editor is a name that exists at runtime.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .model import Graph, Node


@dataclass
class ScopeVar:
    name: str            # name usable in user code
    expr: str            # runtime expression it aliases (python or js)
    dtype: str = "any"   # user | channel | guild | message | str | int | bool | any | interaction | ...
    origin: str = ""     # human text: "Slash Command [ab12cd]"
    doc: str = ""
    always: bool = False # exists without an alias line (trigger locals like `interaction`)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")
    return s if s and not s[0].isdigit() else ("n_" + s if s else "n")


def upstream_nodes(graph: Graph, node: Node) -> list[Node]:
    """Every node whose execution or data can reach *node*, nearest first (triggers last)."""
    seen: dict[str, int] = {}
    order: list[Node] = []
    frontier = [(node.id, 0)]
    while frontier:
        cur, depth = frontier.pop(0)
        for e in graph.edges:
            if e.dst != cur or e.src in seen or e.src == node.id:
                continue
            seen[e.src] = depth + 1
            n = graph.nodes.get(e.src)
            if n is not None:
                order.append(n)
            frontier.append((e.src, depth + 1))
    order.sort(key=lambda n: seen[n.id])
    return order


def trigger_locals(graph: Graph, trigger: Node, js: bool = False) -> list[ScopeVar]:
    """Names the trigger's handler provides, by dry-running its generator."""
    spec = trigger.spec
    if spec is None:
        return []
    out: list[ScopeVar] = []
    fn = spec.gen_js if js else spec.gen
    if fn is None:
        return out
    try:
        if js:
            from .codegen_js import GenCtxJS as Ctx
        else:
            from .codegen import GenCtx as Ctx
        ctx = Ctx(graph)
        ctx.node = trigger
        fn(ctx)
        dt = {p.name: p.dtype for p in trigger.ports()[1] if p.kind == "data"}
        for name, expr in ctx.outs.items():
            out.append(ScopeVar(name, expr, dt.get(name, _guess(name)), f"{spec.title} [{trigger.id}]",
                                always=(name == expr)))
        if not js:
            sig = ctx.signature or ""
            for part in sig.split(","):
                nm = part.split(":")[0].strip()
                if nm and nm != "self" and not any(v.name == nm for v in out):
                    out.append(ScopeVar(nm, nm, "interaction" if nm == "interaction" else _guess(nm),
                                        f"{spec.title} [{trigger.id}]", always=True))
    except Exception:
        pass
    return out


def _guess(name: str) -> str:
    return {"user": "user", "author": "user", "member": "user", "channel": "channel", "guild": "guild",
            "message": "message", "interaction": "interaction", "content": "str"}.get(name, "any")


def scope_for(graph: Graph, node: Node, js: bool = False) -> list[ScopeVar]:
    """Variables visible inside *node*'s code (triggers first, then earlier actions, then own inputs)."""
    vars_: list[ScopeVar] = []
    taken: set[str] = set()

    def add(v: ScopeVar):
        base, n = v.name, 2
        while v.name in taken:
            v.name = f"{base}_{n}"
            n += 1
        taken.add(v.name)
        vars_.append(v)

    ups = upstream_nodes(graph, node)
    for t in (n for n in reversed(ups) if n.spec and n.spec.kind == "trigger"):
        for v in trigger_locals(graph, t, js):
            if v.always:
                if v.name not in taken:
                    taken.add(v.name)
                    vars_.append(v)
            else:
                add(v)
    ins, _ = node.ports()
    if js:
        for nm, ty, doc in (("req", "request", "Express request"), ("res", "response", "Express response"),
                            ("bs", "bs", "Bot Forge Studio dashboard helpers")):
            if nm not in taken:
                taken.add(nm)
                vars_.append(ScopeVar(nm, nm, ty, "Web Route", doc, always=True))
    for p in ins:
        if p.kind == "data":
            add(ScopeVar(p.name, p.name, p.dtype, "wired input", always=True))
    for n in reversed(ups):
        spec = n.spec
        if spec is None or spec.kind == "trigger":
            continue
        _, outs = n.ports()
        for p in outs:
            if p.kind != "data":
                continue
            if spec.kind == "data" and spec.cache:
                continue
            expr = f"v_{n.id}_{p.name}"
            add(ScopeVar(f"{_slug(spec.title)}_{p.name}", expr, p.dtype,
                         f"{spec.title} [{n.id}]", f"output '{p.name}' of {spec.title}"))
    return vars_


def referenced_aliases(code: str, vars_: list[ScopeVar]) -> list[ScopeVar]:
    """Scope variables the code actually uses and that need an alias line."""
    return [v for v in vars_ if not v.always and v.name != v.expr
            and re.search(rf"(?<![\w.]){re.escape(v.name)}\b", code)]


def editor_context(graph: Graph, node: Node, prop_key: str | None = None) -> dict:
    """The dict handed to CodeEditorDialog / editor_assist."""
    web = {"web.html": "html", "web.css": "css", "web.js": "browserjs"}.get(node.type_id)
    if web:
        return {"runtime": web, "node_type": node.type_id, "names": [], "vars": [], "upstream": [], "outputs": []}
    js = node.type_id == "action.js"
    vs = scope_for(graph, node, js)
    return {
        "runtime": "js" if js else "python",
        "node_type": node.type_id,
        "names": [v.name for v in vs],
        "vars": [{"name": v.name, "type": v.dtype, "origin": v.origin, "doc": v.doc} for v in vs],
        "upstream": [f"{n.spec.title if n.spec else n.type_id} [{n.id}]" for n in upstream_nodes(graph, node)],
        "outputs": custom_outputs(node),
    }


def custom_names(text, default: str = "") -> list[str]:
    out: list[str] = []
    for k in re.split(r"[,\s]+", str(text if text is not None else default)):
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,31}", k or "") and k not in out:
            out.append(k)
    return out


def custom_outputs(node: Node) -> list[str]:
    return custom_names(node.props.get("outputs"), "result")
