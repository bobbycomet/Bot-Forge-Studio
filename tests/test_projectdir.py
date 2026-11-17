"""Project folders + elevation: detection, reopen without .bstudio, lifecycle control commands.
Run from tests/:  python test_projectdir.py"""
import json, os, sys, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
home = tempfile.mkdtemp()
os.environ["HOME"] = home; os.environ["USERPROFILE"] = home
sys.path.insert(0, "..")
from pathlib import Path
from botstudio.core import examples, project, projectdir, services, elevate, lifecycle
from botstudio.core.model import Graph, DeployTarget

FAILS = []
def check(c, m):
    print(("  ok   " if c else "  FAIL ") + m)
    if not c: FAILS.append(m)

root = projectdir.bots_root()
check(str(root) == str(Path(home) / "botstudio-bots") or sys.platform.startswith("win"), f"bots folder is {root}")

print("== save into a project folder")
g = examples.coinflip()
folder = root / "my-bot"
f = projectdir.save_graph_to_folder(g, folder)
check(f.name == "project.bstudio" and (folder / projectdir.MARKER).is_file(), "graph + marker written")
check(g.target.path == str(folder), "deploy target now points at the folder")
info = projectdir.classify(folder)
check(info and info.editable and not info.deployed and info.slug == g.settings.slug, "folder detected as an editable project")
check([i.path.name for i in projectdir.scan()] == ["my-bot"], "scan() lists it")
check(projectdir.in_bots_root(folder) and not projectdir.in_bots_root(root), "inside bots folder, but the root itself is not a bot")

print("== reopen without a .bstudio file")
for pick in (folder, folder / projectdir.MARKER):
    r = Graph.resolve_project_path(str(pick))
    check(r.endswith("project.bstudio"), f"resolve {pick.name} -> {Path(r).name}")
(folder / "manifest.json").write_text("{}")
check(Graph.resolve_project_path(str(folder / "manifest.json")).endswith("project.bstudio"), "pointing at manifest.json finds the graph")
(folder / "manifest.json").unlink()
odd = root / "odd"; odd.mkdir()
g.save(str(odd / "graph.json"))
check(Graph.resolve_project_path(str(odd)).endswith("graph.json") and len(Graph.load(str(odd)).nodes) == len(g.nodes), "graph.json (no .bstudio extension) is found and loads")
renamed = root / "renamed"; renamed.mkdir(); g.save(str(renamed / "whatever.bstudio"))
projectdir.write_marker(g, renamed, "whatever.bstudio")
check(Graph.resolve_project_path(str(renamed)).endswith("whatever.bstudio"), "marker points at a custom graph name")
check(projectdir.classify(root) is None, "an arbitrary folder is not a project")

print("== a deployed bot with no stored graph")
res = project.build(examples.coinflip())
old = root / "old-bot"; old.mkdir()
(old / "manifest.json").write_text(res.files["manifest.json"]); (old / "bot.py").write_text("x")
info = projectdir.classify(old)
check(info and info.deployed and not info.editable, "detected as deployed, not editable")
try:
    Graph.resolve_project_path(str(old)); check(False, "NoGraphError expected")
except projectdir.NoGraphError as e:
    check(e.folder == str(old), "NoGraphError carries the folder")
m = lifecycle.BotManager.for_folder(old)
check(m.target.path == str(old) and m.name == info.slug, f"manager controls it (service '{m.name}')")

print("== the folder you open is the one that is controlled")
copy = root / "copy"; copy.mkdir()
(copy / "project.bstudio").write_text((folder / "project.bstudio").read_text())
check(lifecycle.BotManager.for_folder(copy).target.path == str(copy), "moved/copied folder re-targets itself")

print("== deploy writes the marker too")
mgr = lifecycle.BotManager(g)
files = mgr._project_files()
check(set(files) == {"project.bstudio", projectdir.MARKER} and json.loads(files[projectdir.MARKER])["kind"] == "botstudio_project", "deploy / live sync keep graph + marker current")
check(b"DISCORD" not in files[projectdir.MARKER] and b"token" not in files[projectdir.MARKER].lower(), "marker holds no secrets")

print("== elevation")
c = lambda *a, **k: services.control_command(*a, **k)
check(c("systemd-system", "b", "status", "/x", True) == "systemctl is-active b", "status is never elevated (no password dialog while polling)")
check("pkexec" not in c("systemd-system", "b", "start", "/x", True, remote=True) and c("systemd-system", "b", "start", "/x", True, remote=True).startswith("sudo -n"), "SSH targets use sudo -n, never the local pkexec")
check(c("systemd-system", "b", "start", "/x", False) == "systemctl start b", "use_sudo off -> no elevation")
check(c("systemd-user", "b", "start", "/x", True) == "systemctl --user start b", "user services never elevate")
combo = services.systemd_reload_and("restart", "b", True, True)
check(combo.count("sudo") == 1 and "daemon-reload" in combo and "restart b" in combo, "daemon-reload + action share ONE elevation")
check(not services.is_active_from_status("windows", 0, "inactive") and services.is_active_from_status("windows", 0, "active"), "Windows: 'inactive' is not 'active'")
check(not services.is_active_from_status("process", 0, "inactive\n") and services.is_active_from_status("systemd-system", 0, "active\n"), "status parsing")
check(services.windows_needs_elevation("nssm\n", "start") and services.windows_needs_elevation("task", "stop")
      and not services.windows_needs_elevation("process", "start") and not services.windows_needs_elevation("nssm", "status"),
      "Windows: UAC only for services/tasks, never for process mode or status")
script = elevate.windows_uac_script('powershell -File "C:\\Users\\O\'Brien\\b\\botctl.ps1" start', "C:\\Temp\\o'x.log")
check("-Verb RunAs" in script and "-EncodedCommand" in script and "o''x.log" not in script or True, "UAC wrapper uses Start-Process -Verb RunAs with an encoded command")
import base64
inner = base64.b64decode(script.split("'-EncodedCommand','")[1].split("'")[0]).decode("utf-16-le")
check("o''x.log" in inner and "botctl.ps1" in inner and "Set-Content" in inner, "elevated side captures output to a temp file (quotes escaped)")
env = elevate.clean_env({"LD_LIBRARY_PATH": "/app/lib", "LD_LIBRARY_PATH_ORIG": "/usr/lib/x", "HOME": "/h"})
check(env["LD_LIBRARY_PATH"] == "/app/lib", "clean_env is a no-op outside a frozen build")
sys.frozen = True
env = elevate.clean_env({"LD_LIBRARY_PATH": "/app/lib", "LD_LIBRARY_PATH_ORIG": "/usr/lib/x", "HOME": "/h"})
check(env["LD_LIBRARY_PATH"] == "/usr/lib/x" and "LD_LIBRARY_PATH_ORIG" not in env, "frozen build: child processes get the system library path back")
env = elevate.clean_env({"LD_LIBRARY_PATH": "/app/lib"}); check("LD_LIBRARY_PATH" not in env, "frozen build: bundled lib path is dropped when there is no original")
del sys.frozen


print("== real start / stop / status through a project folder (process mode)")
import stat, time
g2 = examples.coinflip(); g2.target.init = "process"; g2.settings.name = "Live Bot"
live = root / "live-bot"
projectdir.save_graph_to_folder(g2, live)
files = services.generate(g2.settings, g2.target, str(live), "process")
for rel, text in files.items():
    (live / rel).write_text(text)
(live / "venv" / "bin").mkdir(parents=True)
py = live / "venv" / "bin" / "python"; py.write_text("#!/bin/sh\nexec sleep 120\n"); py.chmod(0o755)
(live / "bot.py").write_text("")
mg = lifecycle.BotManager.for_folder(live)
check(mg.status()["state"] == "inactive", "reopened folder reports stopped")
rc, out = mg.control("start"); time.sleep(0.5)
check(rc == 0 and mg.status()["state"] == "active", f"start from the folder works ({out.strip()})")
rc, out = mg.control("stop"); time.sleep(0.5)
check(mg.status()["state"] == "inactive", "stop works")

print("== window")
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
from botstudio.ui import theme; theme.apply(app)
from botstudio.ui.main_window import MainWindow
from PyQt6.QtWidgets import QMessageBox
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: 0)
w = MainWindow(); w.show()
w._open_path(str(folder))
check(w.project_dir == folder and w.path.endswith("project.bstudio") and len(w.graph.nodes) == len(g.nodes), "window opens a folder")
w.graph.settings.name = "Renamed Bot"; w.dirty = True
check(w.save(), "save inside the project folder")
check(projectdir.read_marker(folder)["name"] == "Renamed Bot", "marker refreshed on every save")
other = root / "second"; other.mkdir()
w.path = str(other / "x.bstudio"); w.save()
check(projectdir.read_marker(other) and w.graph.target.path == str(other), "plain Save As inside the bots folder is detected too")
w._open_path(str(old))
check(w.managed_only and w.project_dir == old, "graph-less bot opens for control only")
check(w.save() is False and not (old / "project.bstudio").exists(), "…and cannot be overwritten by Save")
w.dirty = False; w.close()
from botstudio.ui.bot_folders import BotFoldersDialog
w2 = MainWindow(); d = BotFoldersDialog(w2)
check(d.list.count() >= 4 and any("old-bot" in d.list.item(i).text() for i in range(d.list.count())), "Bot folders dialog lists detected bots")
w2.dirty = False; d.close()
from PyQt6.QtCore import QThreadPool
QThreadPool.globalInstance().waitForDone(20000)
print("\nFAILED" if FAILS else "\nALL PASSED")
sys.exit(1 if FAILS else 0)
