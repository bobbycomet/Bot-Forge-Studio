"""Bot Forge Studio main window and application entry point."""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, QPointF, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (QApplication, QDockWidget, QFileDialog, QInputDialog, QLineEdit, QMainWindow,
                             QMessageBox, QTabWidget, QToolBar)

from .. import __version__
from ..core import examples, plugins, project, projectdir, services
from ..core.lifecycle import BotManager
from ..core.model import Graph
from . import theme
from .canvas import GraphScene, NodeView
from .dialogs import SettingsDialog, TargetDialog
from .panels import (CodeView, ManagerPanel, OutputLog, PalettePanel, ProblemsList, PropertyPanel)
from . import onboarding
from . import community_plugins

FILE_FILTER = "Bot Forge Studio project (*.bstudio)"


class _Signals(QObject):
    log = pyqtSignal(str)
    done = pyqtSignal(object)


class Job(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn, self.signals = fn, _Signals()

    def run(self):
        try:
            result = self.fn(self.signals.log.emit)
        except Exception:  # never let a worker thread die silently
            self.signals.log.emit(traceback.format_exc())
            result = None
        self.signals.done.emit(result)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.graph = Graph()
        self.path: str | None = None
        self.project_dir: Path | None = None     # the project folder the graph lives in (None = a loose .bstudio file)
        self.managed_only = False                # a deployed bot opened without a stored graph: control only, no editing/sync
        self.dirty = False
        self.token = ""
        self.build_result: project.BuildResult | None = None
        self.undo_stack: list[str] = []
        self.redo_stack: list[str] = []
        self._prop_checkpointed = False
        self._busy = False
        self.pool = QThreadPool.globalInstance()

        self.scene = GraphScene(self.graph)
        self.view = NodeView(self.scene)
        self.setCentralWidget(self.view)
        self._build_docks()
        self._build_menus()
        self.statusBar().showMessage("Ready")

        self.scene.aboutToChange.connect(self.checkpoint)
        self.scene.modified.connect(self._modified)
        self.scene.nodeSelected.connect(self._node_selected)
        self.scene.message.connect(lambda m: self.statusBar().showMessage(m, 5000))
        self.scene.nodeActivated.connect(self._show_node_code)

        self.build_timer = QTimer(self, singleShot=True, interval=400)
        self.build_timer.timeout.connect(self.rebuild)
        self.sync_timer = QTimer(self, singleShot=True, interval=1500)
        self.sync_timer.timeout.connect(self._auto_sync)
        self.poll = QTimer(self, interval=6000)
        self.poll.timeout.connect(self.refresh_status)
        self.poll.start()

        self.resize(1480, 900)
        self.setAcceptDrops(True)
        self._load_graph(examples.hello(), None)
        self._title()

    # ------------------------------------------------------------------ layout
    def _dock(self, title, widget, area, name):
        d = QDockWidget(title, self)
        d.setObjectName(name)
        d.setWidget(widget)
        d.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
        )
        # Closing hides the dock; View menu toggleViewAction brings it back.
        d.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.addDockWidget(area, d)
        return d

    def _build_docks(self):
        L, R, B = Qt.DockWidgetArea.LeftDockWidgetArea, Qt.DockWidgetArea.RightDockWidgetArea, Qt.DockWidgetArea.BottomDockWidgetArea
        self.palette = PalettePanel()
        self.palette.addRequested.connect(self._add_at_center)
        self.d_palette = self._dock("Nodes", self.palette, L, "palette")

        self.props = PropertyPanel()
        self.props.changed.connect(self._prop_changed)
        self.manager = ManagerPanel()
        self.manager.deployRequested.connect(self.deploy)
        self.manager.syncRequested.connect(self.live_sync)
        self.manager.actionRequested.connect(self.control)
        self.manager.logsRequested.connect(self.fetch_logs)
        self.manager.targetRequested.connect(self.edit_target)
        self.manager.refreshRequested.connect(self.refresh_status)
        self.manager.autoSyncToggled.connect(lambda on: self.statusBar().showMessage(
            "Auto live-sync on: edits are pushed ~1.5s after you stop" if on else "Auto live-sync off", 4000))
        tabs = QTabWidget()
        tabs.addTab(self.props, "Properties")
        tabs.addTab(self.manager, "Bot")
        self.right_tabs = tabs
        self.d_right = self._dock("Inspector", tabs, R, "inspector")
        self.d_right.setMinimumWidth(320)

        self.code = CodeView()
        self.problems = ProblemsList()
        self.problems.nodeRequested.connect(self._focus_node)
        self.output = OutputLog()
        bt = QTabWidget()
        bt.addTab(self.code, "Generated code")
        bt.addTab(self.problems, "Problems")
        bt.addTab(self.output, "Output")
        self.bottom_tabs = bt
        self.d_bottom = self._dock("Build", bt, B, "build")
        self.resizeDocks([self.d_bottom], [240], Qt.Orientation.Vertical)

    def _act(self, menu, text, slot, shortcut=None, tip=None):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setStatusTip(tip)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def _build_menus(self):
        mb = self.menuBar()
        f = mb.addMenu("&File")
        self._act(f, "New", self.new_project, "Ctrl+N")
        self._act(f, "Open…", self.open_project, "Ctrl+O")
        self._act(f, "Open folder…", self.open_folder, "Ctrl+Shift+O",
                  "Open a project folder or a deployed bot folder (no .bstudio file needed)")
        self._act(f, "Bot folders…", self.show_bot_folders, None,
                  "Every bot found in the bots folder, with open / start / stop / restart")
        ex = f.addMenu("Examples")
        for name, fn in examples.EXAMPLES.items():
            self._act(ex, name, lambda _=False, fn=fn: self._open_example(fn))
        f.addSeparator()
        self._act(f, "Save", self.save, "Ctrl+S")
        self._act(f, "Save As…", self.save_as, "Ctrl+Shift+S")
        self._act(f, "Save as project folder…", self.save_as_folder, None,
                  "Save the graph and bot info into a folder so it can be reopened, deployed and controlled from there")
        f.addSeparator()
        self._act(f, "Export build folder…", self.export_build, "Ctrl+E",
                  "Write bot.py, cogs, .env.example and service files to a folder")
        self._act(f, "Install plugin…", self.install_plugin)
        self._act(f, "Install module folder…", self.install_module_folder)
        self._act(f, "Reload plugins", self.reload_plugins)
        self._act(f, "Open plugins folder", self.open_plugins)
        self._act(f, "Browse plugin hub…", self.open_plugin_browser)
        f.addSeparator()
        self._act(f, "Quit", self.close, "Ctrl+Q")

        e = mb.addMenu("&Edit")
        self.a_undo = self._act(e, "Undo", self.undo, "Ctrl+Z")
        self.a_redo = self._act(e, "Redo", self.redo, "Ctrl+Shift+Z")
        e.addSeparator()
        self._act(e, "Duplicate", self.scene.duplicate_selected)
        self._act(e, "Delete", self.scene.delete_selected)
        e.addSeparator()
        self._act(e, "Group selected", self.scene.group_selected, "Ctrl+G")
        self._act(e, "Ungroup", self.scene.ungroup_selected, "Ctrl+Shift+G")
        e.addSeparator()
        add = e.addMenu("Add node")
        NodeView.populate_add_menu(add, self._add_at_center)

        p = mb.addMenu("&Project")
        self._act(p, "Settings…", self.edit_settings)
        self._act(p, "Deploy target…", self.edit_target)
        p.addSeparator()
        self._act(p, "Build / validate", self.rebuild, "F5")
        self._act(p, "Deploy", self.deploy, "F6")
        self._act(p, "Live sync", self.live_sync, "F7")

        b = mb.addMenu("&Bot")
        for label, action in (("Start", "start"), ("Stop", "stop"), ("Restart", "restart"),
                              ("Enable at boot", "enable"), ("Disable at boot", "disable")):
            self._act(b, label, lambda _=False, a=action: self.control(a))
        b.addSeparator()
        self._act(b, "Fetch logs", self.fetch_logs)

        v = mb.addMenu("&View")
        for d in (self.d_palette, self.d_right, self.d_bottom):
            v.addAction(d.toggleViewAction())
        v.addSeparator()
        self._act(v, "Show all panels", self.show_all_panels, tip="Restore Nodes, Inspector, and Build docks")
        self._act(v, "Fit graph", self.view.fit_all, "F")

        e = mb  # keep reference quiet
        # Edit menu already created above — add group shortcuts if Edit exists
        # (group actions are on the canvas too)

        h = mb.addMenu("&Help")
        self._act(h, "Bot Wizard…", self.open_bot_wizard, tip="Starter project + optional tour")
        self._act(h, "Guided lessons…", self.open_lessons, tip="Interactive lessons that build real bots on this canvas")
        self._act(h, "Guided tour…", self.open_tour)
        self._act(h, "Learn Python & nodes…", self.open_learning)
        h.addSeparator()
        self._act(h, "Plugin Workshop (create a custom node)…", self.open_plugin_workshop)
        self._act(h, "Web test console (dashboard)…", self.open_web_test, tip="Run your dashboard locally against a fake Discord")
        self._act(h, "Browse plugins (official / community)…", self.open_plugin_browser)
        h.addSeparator()
        self._act(h, "About", self.about)

        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)
        for label, slot in (("▶ Build", self.rebuild), ("⬆ Deploy", self.deploy), ("⟳ Live Sync", self.live_sync),
                            ("⚙ Settings", self.edit_settings)):
            a = QAction(label, self)
            a.triggered.connect(slot)
            tb.addAction(a)

    # ------------------------------------------------------------------ graph state
    def _title(self):
        name = (self.project_dir.name + "/" if self.project_dir else "") + os.path.basename(self.path) if self.path else (
            self.project_dir.name if self.project_dir else "untitled")
        self.setWindowTitle(f"{'*' if self.dirty else ''}{name} — {self.graph.settings.name} — Bot Forge Studio")
        self.manager.set_target(self._target_text())

    def _target_text(self):
        t = self.graph.target
        where = "this machine" if t.mode == "local" else f"{t.ssh_user + '@' if t.ssh_user else ''}{t.host or '(no host)'}"
        return f"{where} · {services.resolve_init(t)} · {t.path or 'default folder'}"

    def _load_graph(self, graph: Graph, path: str | None):
        self.graph, self.path, self.dirty = graph, path, False
        self.project_dir, self.managed_only = None, False
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.scene.set_graph(graph)
        self.props.show_node(graph, None)
        self.rebuild()
        self.view.fit_all()
        self._title()

    def _snapshot(self) -> str:
        d = self.graph.to_dict()
        return json.dumps({"nodes": d["nodes"], "edges": d["edges"]})

    def checkpoint(self):
        self.undo_stack.append(self._snapshot())
        del self.undo_stack[:-100]
        self.redo_stack.clear()

    def _restore(self, snap: str):
        d = json.loads(snap)
        g = Graph.from_dict({"nodes": d["nodes"], "edges": d["edges"]})
        self.graph.nodes, self.graph.edges = g.nodes, g.edges
        self.scene.rebuild()
        self.props.show_node(self.graph, None)
        self._modified(checkpointed=True)

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self._snapshot())
            self._restore(self.undo_stack.pop())

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self._snapshot())
            self._restore(self.redo_stack.pop())

    def _modified(self, checkpointed=False):
        self.dirty = True
        self._title()
        self.build_timer.start()

    def _prop_changed(self, node_id: str):
        if not self._prop_checkpointed:
            # first edit since selecting: the snapshot must predate it; approximate with current-minus-one by re-taking now
            self.undo_stack.append(self._snapshot())
            self._prop_checkpointed = True
        self.scene.refresh_node(node_id)
        self._modified()

    def _node_selected(self, node_id):
        self._prop_checkpointed = False
        self.props.show_node(self.graph, node_id)
        if node_id:
            self.right_tabs.setCurrentIndex(0)

    def _focus_node(self, node_id: str):
        it = self.scene.node_items.get(node_id)
        if it:
            self.scene.clearSelection()
            it.setSelected(True)
            self.view.centerOn(it)

    def _add_at_center(self, type_id: str):
        c = self.view.mapToScene(self.view.viewport().rect().center())
        self.scene.add_node(type_id, QPointF(c.x() - 100, c.y() - 40))

    # ------------------------------------------------------------------ build
    def _service_preview(self) -> dict:
        t = self.graph.target
        init = services.resolve_init(t)
        path = t.path or (f"C:\\BotStudio\\{self.graph.settings.slug}" if init == "windows" else f"/opt/{self.graph.settings.slug}")
        return services.generate(self.graph.settings, t, path, init, t.run_user,
                                 self.build_result.entry if self.build_result else 'bot.py',
                                 dashboard=bool(self.build_result and self.build_result.dashboard))

    def rebuild(self):
        res = project.build(self.graph)
        self.build_result = res
        files = dict(res.files)
        files.update({f"service/{k}": v for k, v in self._service_preview().items()})
        self.code.set_files(files)
        self.problems.set_problems(res.errors, res.warnings)
        n_err, n_warn = len(res.errors), len(res.warnings)
        self.bottom_tabs.setTabText(1, f"Problems ({n_err + n_warn})" if n_err + n_warn else "Problems")
        self.statusBar().showMessage(
            f"{len(res.cogs)} cog(s) · {len(res.files)} files · " + (f"{n_err} error(s)" if n_err else "builds cleanly")
            + (f" · {n_warn} warning(s)" if n_warn else ""), 6000)
        if self.manager.auto.isChecked() and res.ok and not self._busy and self.sender() is self.build_timer:
            self.sync_timer.start()
        return res

    def _show_node_code(self, node_id: str):
        if not self.build_result:
            return
        for c in self.build_result.cogs:
            if node_id in c.node_ids:
                self.code.select(c.path)
                self.bottom_tabs.setCurrentIndex(0)
                return

    # ------------------------------------------------------------------ file ops
    def _maybe_save(self) -> bool:
        if not self.dirty:
            return True
        r = QMessageBox.question(self, "Unsaved changes", "Save changes first?",
                                 QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
                                 | QMessageBox.StandardButton.Cancel)
        if r == QMessageBox.StandardButton.Save:
            return self.save()
        return r == QMessageBox.StandardButton.Discard

    def new_project(self):
        if self._maybe_save():
            self._load_graph(Graph(), None)

    def _open_example(self, fn):
        if self._maybe_save():
            self._load_graph(fn(), None)
            self.dirty = True
            self._title()

    def open_project(self):
        if not self._maybe_save():
            return
        start_dir = ""
        if self.path:
            start_dir = str(Path(self.path).parent)
        # Allow .bstudio projects, portable packs, and "All files" so a mis-filtered
        # file still shows up on every platform.
        filters = (
            "Bot Forge Studio project (*.bstudio);;"
            "Bot Forge Studio pack (*.bspack *.zip);;"
            "All files (*)"
        )
        p, _ = QFileDialog.getOpenFileName(self, "Open project", start_dir, filters)
        if not p:
            # A file dialog cannot select a folder, so cancelling falls through to a
            # folder picker (also reachable directly via File → Open folder…).
            d = QFileDialog.getExistingDirectory(
                self, "Or choose a folder that contains a .bstudio project", start_dir or ""
            )
            if not d:
                return
            p = d
        self._open_path(p)

    def open_folder(self):
        """Open a saved/exported/deployed bot folder (one that holds project.bstudio)."""
        if not self._maybe_save():
            return
        start_dir = str(Path(self.path).parent) if self.path else ""
        d = QFileDialog.getExistingDirectory(self, "Open project folder", start_dir)
        if d:
            self._open_path(d)

    @staticmethod
    def _folder_for(graph_path) -> Path | None:
        """The project folder a graph file lives in: a marked folder, a deployed bot folder, or any folder inside the
        bots folder.  A loose .bstudio file somewhere else is not a project folder."""
        d = Path(graph_path).parent
        if projectdir.read_marker(d) or projectdir.in_bots_root(d) or (d / "manifest.json").is_file():
            return d
        return None

    def open_bot_folder(self, folder: str):
        if self._maybe_save():
            self._open_path(folder)

    def show_bot_folders(self):
        from .bot_folders import BotFoldersDialog
        self._botfolders = BotFoldersDialog(self)
        self._botfolders.show()

    def _open_path(self, p: str):
        """Load a .bstudio / .bspack / project folder (or any file inside one) into the editor."""
        try:
            resolved = Graph.resolve_project_path(p)
            graph = Graph.load(resolved)
            open_path = resolved if resolved.lower().endswith((".bstudio", ".bspack", ".zip")) or Path(resolved).is_file() else p
            folder = None if resolved.lower().endswith((".bspack", ".zip")) else self._folder_for(resolved)
            moved = projectdir.adopt_folder(graph, folder) if folder else False
            self._load_graph(graph, open_path)
            self.project_dir = folder
            self._title()
            missing = graph.missing_node_types()
            if missing:
                QMessageBox.warning(
                    self, "Missing node types",
                    "This project uses node types that are not installed:\n\n"
                    + "\n".join(f"  • {t}" for t in missing[:20])
                    + "\n\nInstall the required plugins, then reopen. "
                    "The nodes were kept so nothing is lost.")
            note = f"Opened {open_path}"
            if folder:
                note += f" — this folder is the bot's install folder" + (" (path updated)" if moved else "")
            self.statusBar().showMessage(note, 6000)
        except projectdir.NoGraphError as e:
            ans = QMessageBox.question(
                self, "Bot has no stored graph",
                f"{e}\n\nOpen it for control only (Bot tab: start, stop, restart, logs)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans == QMessageBox.StandardButton.Yes:
                self._manage_only(e.folder)
        except Exception as e:
            QMessageBox.critical(
                self, "Could not open",
                f"{e}\n\n"
                "Tips:\n"
                "• File → Save as project folder… stores the graph and bot info in a folder you can reopen.\n"
                "• Deploy and Export also write project.bstudio and .botstudio.json into the bot folder; "
                "use File → Open folder… (or Bot folders…) on it.")

    def _manage_only(self, folder: str):
        try:
            g = projectdir.graph_from_deployed(folder)
        except Exception as e:
            QMessageBox.critical(self, "Could not open", str(e))
            return
        self._load_graph(g, None)
        self.project_dir, self.managed_only = Path(folder), True
        self._title()
        self.right_tabs.setCurrentWidget(self.manager)
        self.statusBar().showMessage(
            f"{folder}: control only — this bot has no stored graph, so Deploy / Live sync / Save are disabled", 8000)
        self.refresh_status()

    def _write_project(self, path: str) -> None:
        """Write the graph; inside a project folder also refresh the marker and keep the install folder pointing here."""
        d = Path(path).parent
        folder = d if (projectdir.read_marker(d) or projectdir.in_bots_root(d)) else None
        if folder:
            projectdir.adopt_folder(self.graph, folder)
        self.graph.save(path)
        if folder:
            projectdir.write_marker(self.graph, folder, Path(path).name)
        self.project_dir = folder or (self.project_dir if self.project_dir and Path(path).parent == self.project_dir else None)

    def _refuse_managed(self, what: str) -> bool:
        if not self.managed_only:
            return False
        QMessageBox.information(
            self, "Control only",
            f"{what} is disabled: this bot folder has no stored graph, so {what.lower()} would replace or remove its "
            "files. Open the original project (File → Open) and Deploy from there.")
        return True

    def save(self) -> bool:
        if self._refuse_managed("Saving"):
            return False
        if not self.path:
            return self.save_as()
        try:
            self._write_project(self.path)
        except Exception as e:
            QMessageBox.critical(self, "Could not save", str(e))
            return False
        self.dirty = False
        self._title()
        self.statusBar().showMessage(f"Saved {self.path}", 4000)
        return True

    def save_as(self) -> bool:
        if self._refuse_managed("Saving"):
            return False
        start = f"{self.graph.settings.slug}.bstudio"
        if self.path:
            start = self.path
        p, _ = QFileDialog.getSaveFileName(
            self, "Save project", start,
            "Bot Forge Studio project (*.bstudio);;All files (*)"
        )
        if not p:
            return False
        if not p.lower().endswith(".bstudio"):
            p = p + ".bstudio"
        self.path = p
        return self.save()

    def save_as_folder(self) -> bool:
        """Save into a regular folder (default: inside the bots folder). The folder gets project.bstudio plus a
        .botstudio.json marker, so it is detected, can be reopened by picking the folder, and can be deployed to
        and controlled (start / stop / restart) from there."""
        if self._refuse_managed("Saving"):
            return False
        root = projectdir.bots_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        start = str(self.project_dir.parent if self.project_dir else root)
        d = QFileDialog.getExistingDirectory(self, "Save project into folder (pick or create one)", start)
        if not d:
            return False
        dp = Path(d)
        try:
            if dp.resolve() == root.resolve():
                dp = root / self.graph.settings.slug          # never turn the bots folder itself into a bot
        except OSError:
            pass
        existing = projectdir.classify(dp)
        if existing is not None and existing.graph_file is not None and dp != self.project_dir:
            if QMessageBox.question(self, "Replace saved project?",
                                    f"{dp} already holds the project “{existing.name}”.\nReplace its saved graph with this one?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return False
        elif existing is None and dp.is_dir() and any(dp.iterdir()):
            if QMessageBox.question(self, "Folder is not empty",
                                    f"{dp} already contains other files.\nAdd the project files next to them?",
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return False
        try:
            target = projectdir.save_graph_to_folder(self.graph, dp)
        except Exception as e:
            QMessageBox.critical(self, "Could not save", str(e))
            return False
        self.path, self.project_dir, self.dirty = str(target), dp, False
        self._title()
        self.statusBar().showMessage(f"Saved project folder {dp} — Deploy installs the bot here; Bot tab controls it", 7000)
        return True

    def export_build(self):
        res = self.rebuild()
        if not res.ok:
            QMessageBox.warning(self, "Build has errors", "Fix the problems first:\n\n" + "\n".join(res.errors[:8]))
            return
        d = QFileDialog.getExistingDirectory(self, "Export build to folder")
        if not d:
            return
        t = self.graph.target
        init = services.resolve_init(t)
        files = dict(res.files)
        files.update(services.generate(self.graph.settings, t, d, init, t.run_user, res.entry, dashboard=res.dashboard))
        for rel, text in files.items():
            tgt = Path(d) / rel
            tgt.parent.mkdir(parents=True, exist_ok=True)
            tgt.write_text(text, encoding="utf-8")
            if rel.endswith((".sh", "/run")):
                tgt.chmod(0o755)
        # Also drop project.bstudio + the marker so the exported folder is detected and can be reopened / controlled
        try:
            self.graph.save(str(Path(d) / projectdir.GRAPH_NAME))
            projectdir.write_marker(self.graph, d)
        except Exception as e:
            self.output.write(f"Note: could not write project.bstudio into export: {e}")
        self.output.write(
            f"Exported {len(files)} files to {d} (service paths point at that folder).\n"
            f"Also wrote project.bstudio and .botstudio.json — use File → Open folder… on it to edit the graph again."
        )
        self.bottom_tabs.setCurrentIndex(2)

    def open_plugins(self):
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        if hasattr(plugins, "user_plugin_dir"):
            d = plugins.user_plugin_dir()
        else:
            d = plugins.plugin_dirs()[0]
            d.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(d)))

    def install_plugin(self):
        """Install a .bsmod or .py plugin via file picker."""
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Install plugin", "",
            "Bot Forge Studio plugins (*.bsmod *.py);;All files (*)")
        if paths:
            self._install_paths(paths)

    def install_module_folder(self):
        """Install a multi-file module directory (module.json + entry + side files)."""
        d = QFileDialog.getExistingDirectory(self, "Install module folder")
        if d:
            self._install_paths([d])

    def reload_plugins(self):
        reload_fn = getattr(plugins, "reload_all", None) or plugins.load_all
        loaded, errors = reload_fn()
        self.palette.reload()
        if loaded:
            self.output.write("Plugins loaded: " + ", ".join(loaded))
        for err in errors:
            self.output.write("Plugin error: " + err)
        if not errors:
            self.output.write("Plugins reloaded.")
        self.bottom_tabs.setCurrentIndex(2)
        self.statusBar().showMessage(
            f"Plugins: {len(loaded)} loaded" + (f", {len(errors)} errors" if errors else ""), 5000)

    def _install_paths(self, paths: list[str]):
        ok, fail = [], []
        install = getattr(plugins, "install_plugin", None)
        for p in paths:
            try:
                if install is None:
                    raise RuntimeError("plugins.install_plugin is missing — update core/plugins.py")
                name = install(p)
                ok.append(str(name))
            except Exception as e:
                fail.append(f"{Path(p).name}: {e}")
        self.palette.reload()
        self.bottom_tabs.setCurrentIndex(2)
        if ok:
            self.output.write("Installed: " + ", ".join(ok))
            self.statusBar().showMessage(f"Installed {len(ok)} module(s)", 5000)
        for err in fail:
            self.output.write("Install failed: " + err)
        if fail and not ok:
            QMessageBox.warning(self, "Install failed", "\n".join(fail))

    def dragEnterEvent(self, e):
        if not e.mimeData().hasUrls():
            e.ignore()
            return
        for url in e.mimeData().urls():
            local = url.toLocalFile()
            if not local:
                continue
            path = Path(local)
            if path.suffix.lower() in (".bsmod", ".py"):
                e.acceptProposedAction()
                return
            if path.is_dir() and (path / "module.json").is_file():
                e.acceptProposedAction()
                return
        e.ignore()

    def dropEvent(self, e):
        paths = []
        for u in e.mimeData().urls():
            local = u.toLocalFile()
            if not local:
                continue
            path = Path(local)
            if path.suffix.lower() in (".bsmod", ".py") or (
                    path.is_dir() and (path / "module.json").is_file()):
                paths.append(local)
        if paths:
            self._install_paths(paths)
            e.acceptProposedAction()
        else:
            e.ignore()

    # ------------------------------------------------------------------ dialogs
    def edit_settings(self):
        dlg = SettingsDialog(self.graph, self)
        if dlg.exec():
            dlg.apply()
            self._modified()

    def edit_target(self):
        dlg = TargetDialog(self.graph, self)
        if dlg.exec():
            dlg.apply()
            self._modified()
            self.refresh_status()

    def about(self):
        """About dialog — avoid QMessageBox.about with <a href> (can freeze Linux/AppImage)."""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QDialogButtonBox, QTextBrowser
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        dlg = QDialog(self)
        dlg.setWindowTitle("About Bot Forge Studio")
        dlg.setModal(True)
        dlg.resize(480, 360)
        # Application-modal only for this window — never try to grab the whole desktop
        dlg.setWindowModality(Qt.WindowModality.WindowModal)

        browser = QTextBrowser(dlg)
        browser.setOpenExternalLinks(False)  # we handle clicks ourselves (non-blocking)
        browser.setReadOnly(True)
        browser.setHtml(
            f"<h2>Bot Forge Studio {__version__}</h2>"
            "<p>Node-based IDE, compiler and lifecycle manager for Discord bots.</p>"
            "<p><b>Developed by Comet UX Labs</b><br>"
            "Lead developer: <b>Bobby Comet</b></p>"
            "<p>License: <b>GPLv3</b><br>"
            "Community plugins must be GPLv3 or MIT.</p>"
            "<p>Plugin hub:</p>"
            f"<ul>"
            f"<li><a href='{community_plugins.PLUGIN_HUB}'>Plugins</a></li>"
            f"<li><a href='{community_plugins.OFFICIAL_URL}'>Official</a></li>"
            f"<li><a href='{community_plugins.COMMUNITY_URL}'>Community</a></li>"
            f"</ul>"
            "<p>Build with nodes, deploy anywhere, update live.</p>"
        )

        def _open_link(url: QUrl):
            # Fire-and-forget; never block the UI thread on xdg-open
            QDesktopServices.openUrl(url)

        browser.anchorClicked.connect(_open_link)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(dlg.accept)

        lay = QVBoxLayout(dlg)
        lay.addWidget(browser, 1)
        lay.addWidget(buttons)
        dlg.exec()

    def show_all_panels(self):
        for d in (self.d_palette, self.d_right, self.d_bottom):
            d.show()
            d.raise_()
        self.statusBar().showMessage("Panels restored — use View menu anytime to toggle them", 5000)

    def open_lessons(self):
        from .lessons import LessonHub
        LessonHub(self).exec()

    def open_plugin_workshop(self):
        from .plugin_workshop import PluginWorkshop
        self._workshop = PluginWorkshop(self)
        self._workshop.show()

    def open_web_test(self):
        from .web_test import WebTestDialog
        self._webtest = WebTestDialog(self)
        self._webtest.show()

    def open_tour(self):
        onboarding.TourDialog(self).exec()

    def open_learning(self):
        onboarding.LearningDialog(self).exec()

    def open_bot_wizard(self):
        dlg = onboarding.BotWizardDialog(self)
        if not dlg.exec() or not dlg.choice:
            return
        from ..core import examples
        fn = examples.resolve(dlg.choice)
        if callable(fn):
            self._load_graph(fn(), None)
        else:
            self.statusBar().showMessage(f"Unknown starter: {dlg.choice}", 4000)
            return
        if getattr(dlg, "start_lesson", None):
            from .lessons.content import LESSONS
            from .lessons.hub import run_lesson
            from .lessons import progress
            les = next((l for l in LESSONS if l.id == dlg.start_lesson), None)
            if les is not None:
                progress.reset(les.id)
                run_lesson(self, les, resume=False)
        if dlg.tour_check.isChecked():
            self.open_tour()

    def open_plugin_browser(self):
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QListWidget, QDialogButtonBox, QTextBrowser
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        dlg = QDialog(self)
        dlg.setWindowTitle("Plugin hub — official & community")
        dlg.resize(640, 420)
        info = QTextBrowser()
        info.setOpenExternalLinks(True)
        info.setMarkdown(
            "Browse and install plugins from the Bot Forge Studio hub.\n\n"
            f"- [All plugins]({community_plugins.PLUGIN_HUB})\n"
            f"- [Official]({community_plugins.OFFICIAL_URL}) (Comet UX Labs)\n"
            f"- [Community]({community_plugins.COMMUNITY_URL}) (GPLv3 or MIT)\n\n"
            f"{community_plugins.LICENSE_NOTE}\n\n"
            "Local install still works: **File → Install module folder…** or drag-and-drop "
            "a folder / `.bsmod` / `.py` onto the window."
        )
        lst = QListWidget()
        rows = community_plugins.all_catalog()
        for row in rows:
            lst.addItem(f"[{row['channel']}] {row['title']} — {row['summary']} ({row['license']})")
        def open_sel():
            i = lst.currentRow()
            if i >= 0:
                QDesktopServices.openUrl(QUrl(rows[i]["url"]))
        lst.itemDoubleClicked.connect(lambda *_: open_sel())
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        open_btn = btns.addButton("Open selected on GitHub", QDialogButtonBox.ButtonRole.ActionRole)
        open_btn.clicked.connect(open_sel)
        hub_btn = btns.addButton("Open plugin hub", QDialogButtonBox.ButtonRole.ActionRole)
        hub_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(community_plugins.PLUGIN_HUB)))
        btns.rejected.connect(dlg.reject)
        btns.accepted.connect(dlg.accept)
        lay = QVBoxLayout(dlg)
        lay.addWidget(info)
        lay.addWidget(QLabel("Catalog (offline-friendly list; double-click to open):"))
        lay.addWidget(lst, 1)
        lay.addWidget(btns)
        dlg.exec()

    # ------------------------------------------------------------------ lifecycle
    def _job(self, fn, done=None, tab=2):
        if self._busy:
            self.statusBar().showMessage("Another operation is still running…", 3000)
            return
        self._busy = True
        self.manager.set_busy(True)
        self.bottom_tabs.setCurrentIndex(tab)
        job = Job(fn)
        job.signals.log.connect(self.output.write)

        def finish(result):
            self._busy = False
            self.manager.set_busy(False)
            if done:
                done(result)
        job.signals.done.connect(finish)
        self.pool.start(job)

    def _ask_token(self) -> str | None:
        text, ok = QInputDialog.getText(
            self, "Bot token", f"Paste the bot token for {self.graph.settings.token_env}.\n"
            "It is written to .env on the target and never stored in the project file.\n"
            "Leave blank to keep the token already in .env.",
            QLineEdit.EchoMode.Password, self.token)
        if not ok:
            return None
        self.token = text.strip()
        return self.token

    def deploy(self):
        if self._refuse_managed("Deploying"):
            return
        res = self.rebuild()
        if not res.ok:
            QMessageBox.warning(self, "Build has errors", "Fix the problems first:\n\n" + "\n".join(res.errors[:8]))
            self.bottom_tabs.setCurrentIndex(1)
            return
        token = self._ask_token()
        if token is None:
            return
        mgr = BotManager(self.graph)
        self._job(lambda log: mgr.deploy(res, token, log), lambda ok: self.refresh_status())

    def live_sync(self):
        if self._refuse_managed("Live sync"):
            return
        res = self.rebuild()
        if not res.ok:
            self.bottom_tabs.setCurrentIndex(1)
            return
        mgr = BotManager(self.graph)
        self._job(lambda log: mgr.live_sync(res, log), lambda r: QTimer.singleShot(3000, self.refresh_status))

    def _auto_sync(self):
        if self.build_result and self.build_result.ok and not self._busy and not self.managed_only:
            mgr = BotManager(self.graph)
            res = self.build_result
            self._job(lambda log: mgr.live_sync(res, log), None, tab=self.bottom_tabs.currentIndex())

    def control(self, action: str):
        mgr = BotManager(self.graph)
        self._job(lambda log: mgr.control(action, log), lambda r: QTimer.singleShot(1500, self.refresh_status))

    def fetch_logs(self):
        mgr = BotManager(self.graph)

        def work(log):
            log("—— last log lines ——\n" + mgr.logs(150))
        self._job(work)

    def refresh_status(self):
        if self._busy or not self.d_right.isVisible() or self.right_tabs.currentWidget() is not self.manager:
            return
        mgr = BotManager(self.graph)
        job = Job(lambda log: mgr.status())
        job.signals.done.connect(lambda st: st and self.manager.set_status(st))
        self.pool.start(job)

    def closeEvent(self, e):
        if self._maybe_save():
            e.accept()
        else:
            e.ignore()


def run(argv) -> int:
    app = QApplication(argv)
    app.setApplicationName("BotStudio")          # stable WM class / desktop-file match; the visible name is in every window title
    app.setDesktopFileName("botstudio")
    theme.apply(app)
    loaded, errors = plugins.load_all()
    win = MainWindow()
    win.palette.reload()
    if loaded:
        win.output.write("Plugins loaded: " + ", ".join(loaded))
    for e in errors:
        win.output.write("Plugin error: " + e)
    if len(argv) > 1 and os.path.exists(argv[1]):
        win._open_path(argv[1])        # a .bstudio, a project folder, or any file inside one
    win.show()
    return app.exec()
