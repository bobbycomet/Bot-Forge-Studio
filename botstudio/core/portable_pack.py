
"""Portable build packs: zip of generated bot + embedded project graph."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Callable

from .model import Graph

PACK_COMMENT = "Bot Forge Studio portable pack"
GRAPH_NAME = "project.bstudio"
PLUGINS_NAME = "required_plugins.json"
MANIFEST_NAME = "pack_manifest.json"


def write_pack(
    path: str | Path,
    files: dict[str, str],
    graph: Graph,
    *,
    include_graph: bool = True,
) -> Path:
    """Write a .bspack (zip) containing build files and optional project.bstudio."""
    path = Path(path)
    if path.suffix.lower() not in (".bspack", ".zip"):
        path = path.with_suffix(".bspack")

    req = graph.required_plugins()
    missing = graph.missing_node_types()
    meta = {
        "format": 1,
        "kind": "botstudio_portable_pack",
        "slug": graph.settings.slug,
        "name": graph.settings.name,
        "entry": None,
        "includes_graph": include_graph,
        "required_plugins": req,
        "missing_node_types_at_export": missing,
    }

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.comment = PACK_COMMENT.encode("utf-8")
        for rel, text in files.items():
            # text files from codegen
            data = text if isinstance(text, (bytes, bytearray)) else str(text).encode("utf-8")
            z.writestr(rel.replace("\\\\", "/"), data)
        z.writestr(PLUGINS_NAME, json.dumps(req, indent=2).encode("utf-8"))
        z.writestr(MANIFEST_NAME, json.dumps(meta, indent=2).encode("utf-8"))
        if include_graph:
            z.writestr(GRAPH_NAME, json.dumps(graph.to_dict(), indent=2).encode("utf-8"))
    return path


def read_pack_listing(path: str | Path) -> dict:
    path = Path(path)
    with zipfile.ZipFile(path, "r") as z:
        names = z.namelist()
        meta = {}
        if MANIFEST_NAME in names:
            meta = json.loads(z.read(MANIFEST_NAME).decode("utf-8"))
        req = []
        if PLUGINS_NAME in names:
            req = json.loads(z.read(PLUGINS_NAME).decode("utf-8"))
        has_graph = GRAPH_NAME in names
        return {
            "names": names,
            "meta": meta,
            "required_plugins": req or meta.get("required_plugins") or [],
            "has_graph": has_graph,
        }


def extract_pack(path: str | Path, dest: str | Path) -> dict:
    """Extract pack to dest directory. Returns listing info."""
    path, dest = Path(path), Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    info = read_pack_listing(path)
    with zipfile.ZipFile(path, "r") as z:
        z.extractall(dest)
    return info


def load_graph_from_pack(path: str | Path) -> Graph | None:
    path = Path(path)
    with zipfile.ZipFile(path, "r") as z:
        if GRAPH_NAME not in z.namelist():
            return None
        data = json.loads(z.read(GRAPH_NAME).decode("utf-8"))
    return Graph.from_dict(data)
