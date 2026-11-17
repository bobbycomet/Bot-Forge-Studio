"""Plugin loader: ~/.config/botstudio/plugins and bundled paths.

Supports:
  *.bsmod          — zip (module.json + python files)
  *.py             — register_nodes(model) and/or register_editor_assist(api)
  <directory>/     — module.json + files; optional graph_nodes.py for wired nodes
                     optional editor_assist.py with register_editor_assist(api)

Installing a module folder loads BOTH the Module runtime node AND any
connectable Trigger/Action/Flow/Data nodes defined in graph_nodes.py.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path

from . import model, modules, editor_assist
from .model import NodeSpec, Port, Prop, register

PLUGIN_SUFFIXES = (".bsmod", ".py")


def plugin_dirs() -> list[Path]:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    dirs = [
        base / "botstudio" / "plugins",
        Path(__file__).resolve().parent.parent.parent / "plugins",
        Path(__file__).resolve().parent.parent / "bundled_plugins",
    ]
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", "") or "")
        if meipass:
            dirs.append(meipass / "plugins")
            dirs.append(meipass / "botstudio" / "bundled_plugins")
        dirs.append(Path(sys.executable).resolve().parent / "plugins")
    seen: set[str] = set()
    out: list[Path] = []
    for d in dirs:
        key = str(d)
        if key not in seen:
            seen.add(key)
            out.append(d)
    return out


def user_plugin_dir() -> Path:
    d = plugin_dirs()[0]
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_module_dir(path: Path) -> bool:
    return path.is_dir() and (path / "module.json").is_file()


def _read_module_meta_and_files_from_dir(path: Path) -> tuple[dict, dict[str, str]]:
    meta = json.loads((path / "module.json").read_text(encoding="utf-8"))
    files: dict[str, str] = {}
    for p in path.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(path).as_posix()
        if rel == "module.json" or rel.startswith(".") or "__pycache__" in rel.split("/"):
            continue
        # Skip pure graph-node plugins inside the folder (loaded separately)
        if rel in ("graph_nodes.py", "feature_graph_nodes.py", "graph_nodes_js.py"):
            continue
        if p.suffix.lower() in (".py", ".txt", ".json", ".md"):
            files[rel] = p.read_text(encoding="utf-8")
    return meta, files


def _register_from_meta(meta: dict, files: dict[str, str], source_label: str) -> NodeSpec:
    entry = meta.get("standalone_entry") or meta["entry"]
    if entry not in files:
        raise ValueError(
            f"{source_label}: entry '{entry}' missing (have: {', '.join(sorted(files)[:12])})")
    props = [
        Prop(p["key"], p.get("label", ""), p.get("kind", "str"), p.get("default", ""),
             tuple(p.get("choices", ())), p.get("help", ""))
        for p in meta.get("props", [])
    ]
    desc = meta.get("description", "") or ""
    desc = (desc + "  Includes runtime on the canvas; use companion Trigger/Action "
            "nodes from this plugin to wire into Slash / On Message / Reply.").strip()

    # Exec ports so the module can be placed on a wire (codegen pass-through).
    spec = NodeSpec(
        f"bundle.{meta['id']}", meta.get("title", meta["id"]), "Module", "module",
        inputs=[Port("in", "exec", "")],
        outputs=[Port("next", "exec", "")],
        props=props,
        intents=tuple(meta.get("intents", ())),
        requires=tuple(meta.get("requires", ())),
        env=tuple(tuple(e) for e in meta.get("env", ())),
        description=desc,
        bundle=files,
        standalone=meta.get("standalone_entry"),
    )

    def make_files(node, slug, _spec=spec, _entry=entry):
        out = {}
        for name, text in _spec.bundle.items():
            rendered = modules.fill(text, node, _spec) if name.endswith(".py") else text
            if _spec.standalone:
                out[name] = rendered
            else:
                out[f"cogs/{slug}.py" if name == _entry else name] = rendered
        return out

    spec.files = make_files
    spec.commands = lambda p, _c=tuple(meta.get("commands", ())): list(_c)
    spec.dash_pages = modules.dash_pages_from_meta(meta)
    register(spec, replace=True)
    return spec


def _load_graph_nodes_from_dir(path: Path) -> list[str]:
    """Load graph_nodes.py from a module folder → connectable nodes + editor assist."""
    loaded = []
    for name in ("graph_nodes.py", "feature_graph_nodes.py", "graph_nodes_js.py", "editor_assist.py"):
        candidate = path / name
        if not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location(
            f"bs_mod_graph_{path.name}_{candidate.stem}", candidate)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "register_nodes"):
            mod.register_nodes(model)
            loaded.append(f"{path.name}/{name}")
        if hasattr(mod, "register_nodes_js"):
            mod.register_nodes_js(model)      # dashboard side: attach spec.gen_js to nodes registered above
            if f"{path.name}/{name}" not in loaded:
                loaded.append(f"{path.name}/{name}")
        if editor_assist.register_from_module(mod):
            if f"{path.name}/{name}" not in loaded:
                loaded.append(f"{path.name}/{name} (editor)")
    return loaded


def load_module_dir(path: str | Path) -> NodeSpec:
    path = Path(path)
    if not is_module_dir(path):
        raise ValueError(f"{path}: not a module folder (need module.json)")
    meta, files = _read_module_meta_and_files_from_dir(path)
    spec = _register_from_meta(meta, files, path.name)
    _load_graph_nodes_from_dir(path)
    return spec


def _load_one_file(path: Path) -> str:
    if path.suffix == ".bsmod":
        return modules.load_bsmod(path).title
    if path.suffix == ".py":
        spec = importlib.util.spec_from_file_location(f"bs_plugin_{path.stem}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        did = False
        if hasattr(mod, "register_nodes"):
            mod.register_nodes(model)
            did = True
        if hasattr(mod, "register_nodes_js"):
            mod.register_nodes_js(model)
            did = True
        if editor_assist.register_from_module(mod):
            did = True
        if not did:
            raise ValueError(
                f"{path.name}: need register_nodes(model) and/or register_editor_assist(api)")
        return path.stem
    raise ValueError(f"Unsupported plugin type: {path.suffix}")


def load_all() -> tuple[list[str], list[str]]:
    try:
        editor_assist.reset_plugin_assist()
    except Exception:
        pass
    loaded, errors = [], []
    for d in plugin_dirs():
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            try:
                if p.is_dir() and is_module_dir(p):
                    title = load_module_dir(p).title
                    loaded.append(title)
                elif p.is_file() and p.suffix in PLUGIN_SUFFIXES:
                    loaded.append(_load_one_file(p))
            except Exception as e:
                errors.append(f"{p.name}: {e}")
    return loaded, errors


def reload_all() -> tuple[list[str], list[str]]:
    return load_all()


def install_plugin(src: str | Path) -> str:
    """Install .bsmod, .py, or multi-file module folder into the user plugins dir."""
    src = Path(src)

    if src.is_dir():
        if not is_module_dir(src):
            raise ValueError(
                f"{src.name}: folder must contain module.json "
                "(and an entry .py listed in that file)")
        dest = user_plugin_dir() / src.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(
            src, dest,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".DS_Store"))
        return load_module_dir(dest).title

    if not src.is_file():
        raise FileNotFoundError(src)
    if src.suffix.lower() not in PLUGIN_SUFFIXES:
        raise ValueError("Only .bsmod, .py, or a module folder (with module.json) are supported")
    dest = user_plugin_dir() / src.name
    shutil.copy2(src, dest)
    return _load_one_file(dest)


def pack_module_dir_to_bsmod(src: str | Path, dest: str | Path | None = None) -> Path:
    src = Path(src)
    if not is_module_dir(src):
        raise ValueError(f"{src}: need module.json")
    dest = Path(dest) if dest else src.with_suffix(".bsmod")
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(src / "module.json", "module.json")
        for p in src.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(src).as_posix()
            if rel == "module.json" or "__pycache__" in rel.split("/") or rel.startswith("."):
                continue
            z.write(p, rel)
    return dest
