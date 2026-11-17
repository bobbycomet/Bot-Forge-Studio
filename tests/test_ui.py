import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, '..')
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QPointF
app = QApplication(sys.argv)
from botstudio.ui import theme
theme.apply(app)
from botstudio.ui.main_window import MainWindow
from botstudio.core import examples
w = MainWindow(); w.show()
for name, fn in examples.EXAMPLES.items():
    w._load_graph(fn(), None)
    assert w.build_result.ok, (name, w.build_result.errors)
    for nid in list(w.scene.node_items)[:2]:
        w.scene.node_items[nid].setSelected(True)
w._load_graph(examples.coinflip(), None)
# add node, wire it, edit a dynamic-port prop, undo
t = next(n for n in w.graph.nodes.values() if n.type_id == "trigger.slash")
w.scene.clearSelection(); t_item = w.scene.node_items[t.id]; t_item.setSelected(True)
t.props["params"] = "amount:int=3, who:user"
w._prop_changed(t.id)
assert t.port("amount", True) and t.port("who", True)
n = w.scene.add_node("action.log", QPointF(500, 400))
w.graph.connect(t.id, "next", n.id, "in"); w.scene.rebuild(); w._modified()
w.rebuild(); print("after edit:", w.build_result.ok, w.build_result.warnings)
w.undo(); w.undo(); print("undo ok, nodes:", len(w.graph.nodes))
w.redo()
# manager status render
w.manager.set_status({"state":"active","info":{"user":"Bot#1","now":100,"started":0,"latency_ms":42,"guilds":3,"cogs":["a"],"errors":[],"restart_required":True}})
w.grab().save("/tmp/shot.png")
print("size", w.size().width(), w.size().height())

# ---- web dashboard (discord.js) additions
w._load_graph(examples.dashboard(), None)
assert w.build_result.ok, w.build_result.errors
assert w.build_result.dashboard
files = [w.code.combo.itemText(i) for i in range(w.code.combo.count())]
assert any(f.endswith("config.json") for f in files) and any("routes/" in f for f in files), files
from botstudio.ui.dialogs import SettingsDialog, TargetDialog
d = SettingsDialog(w.graph); d.pkgs.setPlainText("cheerio@^1.0.0"); d.apply()
assert w.graph.settings.extra_packages == ["cheerio@^1.0.0"]
td = TargetDialog(w.graph); td.portable.setChecked(False); td.apply(); assert w.graph.target.portable_node is False
w.rebuild()
assert '"cheerio": "^1.0.0"' in w.build_result.files["dashboard/package.json"]
assert "dashboard/deps.sh" in w._service_preview() if hasattr(w, "_service_preview") else True
from botstudio.ui.code_editor import CodeEditorDialog
ed = CodeEditorDialog(None, "t", "const x = (1;\nawait bs.respond(interaction, 'x');", {"runtime": "js"})
ed._force_analyze(); assert ed.diag_list.count() >= 1
print("dashboard UI OK")

from botstudio.ui import onboarding
dlg = onboarding.LearningDialog(w)
dlg.tracks.setCurrentRow([dlg.tracks.item(i).text() for i in range(dlg.tracks.count())].index("Build a bot"))
dlg._load_example()
assert any(n.type_id == "module.birthdays" for n in w.graph.nodes.values()) and w.graph.settings.name == "Birthday Bit"
print("lesson example loads OK")
