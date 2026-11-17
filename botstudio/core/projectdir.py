"""Project folders: a regular folder (normally inside the bots folder) that holds a bot *and* its editor graph.

A folder counts as a project when it has a ``.botstudio.json`` marker, a graph file (``project.bstudio`` or any
single ``*.bstudio``), or a deployed bot (``manifest.json`` + entry script).  Saving or deploying into such a
folder writes the graph and the marker next to the generated files, so the folder can always be

* reopened in the editor (File → Open folder…, or by pointing at any file inside it), even without a ``.bstudio``
  extension on what you picked, and
* controlled (start / stop / restart / logs) through the same service definitions Deploy installed.

Nothing here imports :mod:`model` at module level, so :mod:`model` can use it for path resolution.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .. import __version__

MARKER = ".botstudio.json"
GRAPH_NAME = "project.bstudio"
GRAPH_ALIASES = ("project.bstudio", "project.json", "graph.json")
MARKER_FORMAT = 1


class NoGraphError(FileNotFoundError):
    """A deployed bot folder exists, but it holds no editor graph (made by an old version, or hand-copied)."""

    def __init__(self, folder: str, message: str):
        super().__init__(message)
        self.folder = folder


def bots_root() -> Path:
    """Where Studio keeps bots by default (the same place Deploy installs to)."""
    base = Path.home()
    return base / "BotStudio" if sys.platform.startswith("win") else base / "botstudio-bots"


def _looks_like_graph(p: Path) -> bool:
    try:
        if p.stat().st_size > 64 * 1024 * 1024:
            return False
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and "nodes" in data and ("edges" in data or "settings" in data)


def read_marker(folder) -> Optional[dict]:
    try:
        data = json.loads((Path(folder) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("kind") == "botstudio_project" else None


def find_graph_file(folder) -> Optional[Path]:
    """The graph file of a project folder: marker's pointer, then project.bstudio, a lone *.bstudio, then aliases."""
    d = Path(folder)
    m = read_marker(d)
    if m:
        name = str(m.get("graph") or "")
        if name and "/" not in name and "\\" not in name and (d / name).is_file():
            return d / name
    if not d.is_dir():
        return None
    for alias in GRAPH_ALIASES:
        p = d / alias
        if p.is_file() and (alias.endswith(".bstudio") or _looks_like_graph(p)):
            return p
    found = sorted(d.glob("*.bstudio"))
    if len(found) == 1:
        return found[0]
    for f in found:
        if f.stem == d.name:
            return f
    return found[0] if found else None


@dataclass
class ProjectFolder:
    path: Path
    name: str
    slug: str
    graph_file: Optional[Path]      # None = deployed bot without a stored graph
    deployed: bool                  # generated bot files are present (manifest.json)
    saved_at: float = 0.0
    init: str = ""
    service_name: str = ""

    @property
    def editable(self) -> bool:
        return self.graph_file is not None


def _manifest(folder: Path) -> Optional[dict]:
    try:
        data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _infer_init(folder: Path, slug: str) -> str:
    if (folder / "install_service.ps1").is_file() or (folder / "botctl.ps1").is_file():
        return "windows"
    if (folder / f"{slug}.service").is_file():
        return "auto"          # systemd flavour is decided by the machine (system vs user) when it is controlled
    if (folder / "runit").is_dir():
        return "runit"
    if (folder / f"{slug}.openrc").is_file():
        return "openrc"
    return "auto"


def classify(folder) -> Optional[ProjectFolder]:
    """Describe *folder* when it is a Studio project / deployed bot, else None."""
    d = Path(folder).expanduser()
    if not d.is_dir():
        return None
    marker = read_marker(d)
    graph = find_graph_file(d)
    man = _manifest(d)
    deployed = bool(man and (man.get("bot") or man.get("entry")))
    if not (marker or graph or deployed):
        return None
    slug = str((marker or {}).get("slug") or (man or {}).get("bot") or d.name)
    name = str((marker or {}).get("name") or slug)
    return ProjectFolder(d, name, slug, graph, deployed, float((marker or {}).get("saved_at") or 0),
                         str((marker or {}).get("init") or _infer_init(d, slug)),
                         str((marker or {}).get("service_name") or ""))


def in_bots_root(folder, root: Path | None = None) -> bool:
    """True for a folder *inside* the bots folder (the bots folder itself does not count)."""
    try:
        r = (root or bots_root()).expanduser().resolve()
        p = Path(folder).expanduser().resolve()
    except OSError:
        return False
    return p != r and r in p.parents


def scan(root: Path | None = None) -> list[ProjectFolder]:
    """Every project / deployed bot folder directly inside the bots folder, newest first."""
    r = (root or bots_root()).expanduser()
    out: list[ProjectFolder] = []
    try:
        entries = sorted(r.iterdir())
    except OSError:
        return out
    for e in entries:
        if e.name.startswith(".") or not e.is_dir():
            continue
        info = classify(e)
        if info is not None:
            out.append(info)
    out.sort(key=lambda i: (-i.saved_at, i.name.lower()))
    return out


def marker_dict(graph, graph_file: str = GRAPH_NAME) -> dict:
    s, t = graph.settings, graph.target
    return {
        "kind": "botstudio_project", "format": MARKER_FORMAT, "studio": __version__,
        "name": s.name, "slug": s.slug, "graph": graph_file,
        "service_name": (t.service_name or "").strip(), "init": t.init, "mode": t.mode,
        "saved_at": time.time(),
    }


def marker_bytes(graph, graph_file: str = GRAPH_NAME) -> bytes:
    return (json.dumps(marker_dict(graph, graph_file), indent=2) + "\n").encode("utf-8")


def write_marker(graph, folder, graph_file: str = GRAPH_NAME) -> None:
    d = Path(folder)
    tmp = d / (MARKER + ".tmp")
    tmp.write_bytes(marker_bytes(graph, graph_file))
    os.replace(tmp, d / MARKER)


def adopt_folder(graph, folder) -> bool:
    """Make *folder* the install folder of *graph* (local targets only). Returns True when it changed."""
    t = graph.target
    if t.mode != "local":
        return False
    want = str(Path(folder))
    if os.path.normcase(os.path.normpath(t.path or "")) == os.path.normcase(os.path.normpath(want)):
        return False
    t.path = want
    return True


def save_graph_to_folder(graph, folder, filename: str = GRAPH_NAME) -> Path:
    """Write the graph + marker into *folder* (created if needed) and point the deploy target at it."""
    d = Path(folder).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    adopt_folder(graph, d)
    target = d / filename
    graph.save(str(target))
    write_marker(graph, d, filename)
    return target


def graph_from_deployed(folder):
    """A minimal graph that lets the Bot tab control a deployed bot whose editor graph was never stored."""
    from .model import Graph
    d = Path(folder).expanduser()
    info = classify(d)
    if info is None or not info.deployed:
        raise FileNotFoundError(f"{d} is not a deployed bot folder")
    g = Graph()
    g.settings.name = info.slug
    g.target.mode = "local"
    g.target.path = str(d)
    g.target.init = info.init or "auto"
    if info.service_name:
        g.target.service_name = info.service_name
    return g
