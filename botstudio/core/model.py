"""Graph model: port/prop/node specs, the node registry, and the project file."""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

FORMAT_VERSION = 1


# ----------------------------------------------------------------------------
# Specs (the "pre-compiled chunks")
# ----------------------------------------------------------------------------

@dataclass
class Port:
    name: str
    kind: str = "data"           # "exec" | "data"
    label: str = ""
    dtype: str = "any"           # colour hint only

    @property
    def text(self) -> str:
        return self.label or self.name


@dataclass
class Prop:
    key: str
    label: str = ""
    kind: str = "str"            # str | text | int | float | bool | choice | any
    default: Any = ""
    choices: tuple = ()
    help: str = ""
    fmt: bool = False            # supports {user.mention}-style placeholders
    port: bool = False           # a data input port of the same name can override it

    @property
    def text(self) -> str:
        return self.label or self.key.replace("_", " ").title()


@dataclass
class NodeSpec:
    type_id: str
    title: str
    category: str
    kind: str                    # trigger | action | flow | data | module
    inputs: list = field(default_factory=list)
    outputs: list = field(default_factory=list)
    props: list = field(default_factory=list)
    gen: Optional[Callable] = None        # statements (trigger/action/flow) or expr (data)
    description: str = ""
    intents: tuple = ()                   # e.g. ("message_content",)
    requires: tuple = ()                  # pip packages
    env: tuple = ()                       # (NAME, default, comment)
    cache: bool = False                   # data node evaluated once into a variable
    dynamic_ports: Optional[Callable] = None   # props -> (inputs, outputs)
    files: Optional[Callable] = None      # module nodes: (node, ctx) -> {path: source}
    commands: Optional[Callable] = None   # module nodes: props -> [command names]
    bundle: Optional[dict] = None         # .bsmod bundles: {relative path: text}
    standalone: Optional[str] = None      # bundle is a complete bot; value = its entry script
    # ---- web dashboard (discord.js) ------------------------------------------------------
    gen_js: Optional[Callable] = None     # JS twin of ``gen`` — makes the node usable inside a Web Route
    requires_js: tuple = ()               # npm packages for the dashboard, e.g. ("express@^4.21.0",)
    web_only: bool = False                # lives in the dashboard (Web Route …) and never compiles into the bot
    dash_pages: Optional[Callable] = None # module nodes: node -> [page dicts] the dashboard shows for this module

    def prop(self, key: str) -> Optional[Prop]:
        return next((p for p in self.props if p.key == key), None)


CATEGORY_COLORS = {
    "Trigger": "#d9534f",
    "Action": "#1a9fff",
    "Flow": "#f0ad4e",
    "Data": "#5ba32b",
    "Storage": "#a06be0",
    "Moderation": "#e0709a",
    "Interact": "#ff8f3f",
    "Economy": "#d6b300",
    "Game": "#c2410c",
    "Community": "#14b8a6",
    "Web": "#38bdf8",
    "Module": "#2fb8b0",
    "Custom": "#8f98a0",
}

_REGISTRY: dict[str, NodeSpec] = {}


def register(spec: NodeSpec, replace: bool = False) -> NodeSpec:
    if spec.type_id in _REGISTRY and not replace:
        raise ValueError(f"Duplicate node type registered: {spec.type_id}")
    _REGISTRY[spec.type_id] = spec
    return spec


def get_spec(type_id: str) -> Optional[NodeSpec]:
    return _REGISTRY.get(type_id)


def all_specs() -> list[NodeSpec]:
    return sorted(_REGISTRY.values(), key=lambda s: (s.category, s.title))


# ----------------------------------------------------------------------------
# Graph
# ----------------------------------------------------------------------------

def new_id() -> str:
    return uuid.uuid4().hex[:6]


@dataclass
class Node:
    id: str
    type_id: str
    x: float = 0.0
    y: float = 0.0
    props: dict = field(default_factory=dict)

    @property
    def spec(self) -> Optional[NodeSpec]:
        return get_spec(self.type_id)

    def ports(self) -> tuple[list[Port], list[Port]]:
        spec = self.spec
        if spec is None:
            return [], []
        ins, outs = list(spec.inputs), list(spec.outputs)
        if spec.dynamic_ports:
            d_in, d_out = spec.dynamic_ports(self.props)
            ins += d_in
            outs += d_out
        return ins, outs

    def port(self, name: str, output: bool) -> Optional[Port]:
        ins, outs = self.ports()
        return next((p for p in (outs if output else ins) if p.name == name), None)

    def prop_value(self, key: str):
        if key in self.props:
            return self.props[key]
        p = self.spec.prop(key) if self.spec else None
        return p.default if p else None


@dataclass(frozen=True)
class Edge:
    src: str
    src_port: str
    dst: str
    dst_port: str


@dataclass
class ProjectSettings:
    name: str = "My Bot"
    token_env: str = "DISCORD_TOKEN"
    dev_guild_id: str = ""
    command_prefix: str = "!"
    status_text: str = ""
    activity_type: str = "watching"
    log_level: str = "INFO"
    extra_intents: list = field(default_factory=list)
    extra_requirements: list = field(default_factory=list)
    hot_reload: bool = True
    extra_packages: list = field(default_factory=list)       # extra npm packages for the web dashboard

    @property
    def slug(self) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")
        return s or "bot"


@dataclass
class DeployTarget:
    mode: str = "local"                  # local | ssh
    path: str = ""                       # install dir on the target
    init: str = "auto"                   # auto|systemd-system|systemd-user|runit|openrc|windows|process
    service_name: str = ""
    run_user: str = ""
    python: str = "python3"
    host: str = ""
    ssh_user: str = ""
    ssh_port: int = 22
    ssh_key: str = ""
    use_sudo: bool = True
    node: str = "node"                  # dashboard: node executable (used only when downloads are off)
    portable_node: bool = True          # dashboard: download a private Node.js when the system one is missing/too old
    env: dict = field(default_factory=dict)     # extra non-secret env vars (the token is never saved)


class Graph:
    def __init__(self):
        self.nodes: dict[str, Node] = {}
        self.edges: list[Edge] = []
        self.groups: list[dict] = []  # {id, title, node_ids, collapsed}
        self.settings = ProjectSettings()
        self.target = DeployTarget()

    # -- editing ------------------------------------------------------------
    def add_node(self, type_id: str, x: float = 0, y: float = 0, props: dict | None = None,
                 node_id: str | None = None) -> Node:
        spec = get_spec(type_id)
        if spec is None:
            raise KeyError(type_id)
        base = {p.key: p.default for p in spec.props}
        base.update(props or {})
        n = Node(node_id or new_id(), type_id, x, y, base)
        self.nodes[n.id] = n
        return n

    def remove_node(self, node_id: str):
        self.nodes.pop(node_id, None)
        self.edges = [e for e in self.edges if node_id not in (e.src, e.dst)]

    def can_connect(self, src: str, src_port: str, dst: str, dst_port: str) -> tuple[bool, str]:
        if src == dst:
            return False, "A node cannot connect to itself."
        a, b = self.nodes.get(src), self.nodes.get(dst)
        if not a or not b:
            return False, "Unknown node."
        p_out, p_in = a.port(src_port, True), b.port(dst_port, False)
        if not p_out or not p_in:
            return False, "Unknown port."
        if p_out.kind != p_in.kind:
            return False, "Exec ports connect to exec ports, data to data."
        return True, ""

    def connect(self, src: str, src_port: str, dst: str, dst_port: str) -> Edge | None:
        ok, _ = self.can_connect(src, src_port, dst, dst_port)
        if not ok:
            return None
        kind = self.nodes[src].port(src_port, True).kind
        # data inputs accept one wire; exec inputs may have many incoming wires
        if kind == "data":
            self.edges = [e for e in self.edges if not (e.dst == dst and e.dst_port == dst_port)]
        e = Edge(src, src_port, dst, dst_port)
        if e not in self.edges:
            self.edges.append(e)
        return e

    def prune_edges(self):
        """Drop wires whose ports no longer exist (after a dynamic-port change)."""
        keep = []
        for e in self.edges:
            a, b = self.nodes.get(e.src), self.nodes.get(e.dst)
            if a and b and a.port(e.src_port, True) and b.port(e.dst_port, False):
                keep.append(e)
        self.edges = keep

    def incoming(self, node_id: str, port: str) -> Edge | None:
        return next((e for e in self.edges if e.dst == node_id and e.dst_port == port), None)

    def outgoing(self, node_id: str, port: str) -> list[Edge]:
        return [e for e in self.edges if e.src == node_id and e.src_port == port]

    # -- persistence ----------------------------------------------------------
    def required_plugins(self) -> list[dict]:
        """External node/module types referenced by this graph (for share warnings)."""
        out: list[dict] = []
        seen: set[str] = set()
        core_prefixes = ("trigger.", "action.", "flow.", "data.", "mod.", "store.", "web.", "ui.", "eco.", "game.")
        for n in self.nodes.values():
            tid = n.type_id
            if tid in seen:
                continue
            seen.add(tid)
            spec = get_spec(tid)
            if spec is None:
                out.append({"type_id": tid, "title": tid, "kind": "missing"})
                continue
            if spec.kind == "module" or tid.startswith("bundle."):
                out.append({"type_id": tid, "title": spec.title, "kind": "module"})
                continue
            # Non-core type ids are treated as plugin-provided nodes
            if not tid.startswith(core_prefixes):
                out.append({"type_id": tid, "title": spec.title, "kind": "node"})
        out.sort(key=lambda x: x["type_id"])
        return out

    def missing_node_types(self) -> list[str]:
        return sorted({n.type_id for n in self.nodes.values() if get_spec(n.type_id) is None})

    def to_dict(self) -> dict:
        # Only serialise plain data so the .bstudio file always reloads cleanly.
        def _plain(obj):
            if isinstance(obj, dict):
                return {str(k): _plain(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_plain(v) for v in obj]
            if isinstance(obj, (str, int, float, bool)) or obj is None:
                return obj
            return str(obj)

        return {
            "format": FORMAT_VERSION,
            "settings": _plain(dict(self.settings.__dict__)),
            "target": _plain(dict(self.target.__dict__)),
            "nodes": [{"id": n.id, "type": n.type_id, "x": float(n.x), "y": float(n.y),
                       "props": _plain(n.props)}
                      for n in self.nodes.values()],
            "edges": [[e.src, e.src_port, e.dst, e.dst_port] for e in self.edges],
            "groups": _plain(self.groups),
            "required_plugins": self.required_plugins(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Graph":
        if not isinstance(d, dict):
            raise ValueError("Project file is not a JSON object")
        g = cls()
        for k, v in (d.get("settings") or {}).items():
            if hasattr(g.settings, k):
                setattr(g.settings, k, v)
        for k, v in (d.get("target") or {}).items():
            if hasattr(g.target, k):
                setattr(g.target, k, v)
        for nd in d.get("nodes") or []:
            if not isinstance(nd, dict):
                continue
            nid = nd.get("id") or new_id()
            # Accept both "type" (current) and "type_id" (older / external packs)
            type_id = nd.get("type") or nd.get("type_id")
            if not type_id:
                continue
            x = float(nd.get("x", 0) or 0)
            y = float(nd.get("y", 0) or 0)
            props = nd.get("props") if isinstance(nd.get("props"), dict) else {}
            if get_spec(type_id) is None:
                # Keep nodes from a plugin that is not installed so nothing is lost
                g.nodes[nid] = Node(nid, type_id, x, y, props)
                continue
            try:
                g.add_node(type_id, x, y, props, nid)
            except Exception:
                g.nodes[nid] = Node(nid, type_id, x, y, props)
        for edge in d.get("edges") or []:
            try:
                if isinstance(edge, (list, tuple)) and len(edge) >= 4:
                    s, sp, t, tp = edge[0], edge[1], edge[2], edge[3]
                elif isinstance(edge, dict):
                    s, sp, t, tp = edge.get("src"), edge.get("src_port"), edge.get("dst"), edge.get("dst_port")
                else:
                    continue
                if s in g.nodes and t in g.nodes:
                    g.edges.append(Edge(s, sp, t, tp))
            except Exception:
                continue
        g.prune_edges()
        g.groups = list(d.get("groups") or [])
        return g

    def save(self, path: str):
        """Atomically write the project so a crash mid-save cannot corrupt the file."""
        path = str(path)
        data = self.to_dict()
        text = json.dumps(data, indent=2, ensure_ascii=False)
        # Round-trip check before touching the real file
        Graph.from_dict(json.loads(text))
        parent = Path(path).parent
        if parent and not parent.exists():
            parent.mkdir(parents=True, exist_ok=True)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    @classmethod
    def resolve_project_path(cls, path: str) -> str:
        """Accept a .bstudio file, a project folder, any file inside a project folder, or a .bspack.

        Returns the concrete path to the JSON graph file (or the .bspack).  A project folder is any folder
        with a ``.botstudio.json`` marker, a graph file, or a deployed bot (see :mod:`projectdir`) — the
        graph file does not need a ``.bstudio`` extension.
        """
        from . import projectdir
        p = Path(path).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"Path does not exist: {path}")
        if p.is_file():
            if p.suffix.lower() in (".bstudio", ".bspack", ".zip"):
                return str(p)
            # a marker, manifest, bot.py, .env.example … inside a project folder -> that folder's graph
            if p.name == projectdir.MARKER or p.name in ("manifest.json", "bot.py", "requirements.txt", ".env.example"):
                g = projectdir.find_graph_file(p.parent)
                if g is not None:
                    return str(g)
                if projectdir.classify(p.parent) is not None:
                    p = p.parent
                else:
                    return str(p)
            else:
                return str(p)
        g = projectdir.find_graph_file(p)
        if g is not None:
            return str(g)
        info = projectdir.classify(p)
        if info is not None and info.deployed:
            raise projectdir.NoGraphError(
                str(p),
                f"This bot folder has no stored editor graph:\n{path}\n\n"
                "It was deployed by an older version of Bot Forge Studio (or copied without project.bstudio). "
                "You can still start, stop and restart it from the Bot tab, but its nodes cannot be edited "
                "until you open the original project and Deploy again.")
        raise FileNotFoundError(
            f"No Bot Forge Studio project was found in:\n{path}\n\n"
            "Pick a folder that was saved with File → Save as project folder…, or deployed by Studio.")

    @classmethod
    def load(cls, path: str) -> "Graph":
        resolved = cls.resolve_project_path(path)
        lower = resolved.lower()
        if lower.endswith(".bspack") or lower.endswith(".zip"):
            # Lazy import to avoid a hard dependency cycle
            try:
                from . import portable_pack
            except ImportError:
                from botstudio.core import portable_pack  # type: ignore
            g = portable_pack.load_graph_from_pack(resolved)
            if g is None:
                raise ValueError(f"No project.bstudio inside pack: {resolved}")
            return g
        with open(resolved, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("Project file does not contain a JSON object")
        if "nodes" not in data and "settings" not in data:
            raise ValueError(
                "This does not look like a Bot Forge Studio project file.\n"
                "Expected a .bstudio graph (nodes, edges, settings)."
            )
        return cls.from_dict(data)

    def components(self) -> list[list[Node]]:
        """Weakly connected groups of nodes; each becomes one generated cog."""
        seen: set[str] = set()
        groups = []
        adj: dict[str, set[str]] = {nid: set() for nid in self.nodes}
        for e in self.edges:
            adj[e.src].add(e.dst)
            adj[e.dst].add(e.src)
        for nid in sorted(self.nodes, key=lambda i: (self.nodes[i].y, self.nodes[i].x)):
            if nid in seen:
                continue
            stack, comp = [nid], []
            while stack:
                cur = stack.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                comp.append(self.nodes[cur])
                stack.extend(adj[cur] - seen)
            groups.append(sorted(comp, key=lambda n: (n.y, n.x)))
        return groups
