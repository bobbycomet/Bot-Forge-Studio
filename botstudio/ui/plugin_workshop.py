"""Plugin Workshop: write, save, load and test a custom node (a Python file in the plugins folder) without leaving Studio."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDesktopServices, QFont, QFontDatabase
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout)

from ..core import model, plugins
from .panels import PyHighlighter

TEMPLATE = '''"""A custom node is a Python file in Bot Forge Studio's plugins folder.  Studio calls register_nodes(model) when it loads the file."""


def register_nodes(model):
    def gen(ctx):
        # `ctx` is the code generator.  Emit the Python this node runs inside the command handler.
        sides = int(ctx.prop("sides") or 6)                      # a property typed in the Properties panel
        # ctx.var("total") is this node's output variable; later nodes read it through the data wire
        ctx.emit(f"{ctx.var('total')} = __import__('random').randint(1, {sides})")

    model.register(model.NodeSpec(
        "custom.dice_roll",          # type id: unique; the 'custom.' prefix marks it as plugin-provided
        "Dice Roll",                 # title shown on the node
        "Custom",                    # palette category
        "action",                    # kind: trigger | action | flow | data
        inputs=[model.Port("in", "exec")],                                                  # execution input
        outputs=[model.Port("next", "exec"), model.Port("total", "data", "total", "int")],  # exec out + one data output
        props=[model.Prop("sides", "Sides", "int", 6, help="How many sides the die has")],
        gen=gen,
        description="Rolls a die and outputs the total.",
    ), replace=True)
'''


class PluginWorkshop(QDialog):
    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Plugin Workshop — create a custom node")
        self.resize(900, 680)
        font = QFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        font.setPointSize(11)
        self.name = QLineEdit("dice_roll")
        self.name.setToolTip("File name (letters, digits, underscore). Saved as <name>.py in your plugins folder.")
        self.edit = QPlainTextEdit(TEMPLATE)
        self.edit.setFont(font)
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._hl = PyHighlighter(self.edit.document())
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(170)
        tip = QLabel("A **plugin node** lives in a file, not inside Studio. Edit the code, then <b>Save &amp; load</b> — the node appears in the palette "
                     "under its category. <b>Test generate</b> builds a tiny project with your node to prove the code it emits compiles.")
        tip.setWordWrap(True)
        save = QPushButton("Save && load into Studio")
        save.setObjectName("primary")
        test = QPushButton("Test generate")
        folder = QPushButton("Open plugins folder")
        close = QPushButton("Close")
        save.clicked.connect(self.save_and_load)
        test.clicked.connect(self.test_generate)
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(plugins.user_plugin_dir()))))
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addWidget(QLabel("File name:"))
        row.addWidget(self.name, 1)
        row.addWidget(folder)
        row.addWidget(test)
        row.addWidget(save)
        row.addWidget(close)
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.edit)
        split.addWidget(self.log)
        lay = QVBoxLayout(self)
        lay.addWidget(tip)
        lay.addLayout(row)
        lay.addWidget(split, 1)
        self.loaded: list[str] = []

    def _say(self, text: str):
        self.log.appendPlainText(text)

    def _filename(self) -> str | None:
        n = self.name.text().strip().lower()
        if not n or not n.replace("_", "").isalnum() or n[0].isdigit():
            self._say("✗ File name: use letters, digits and underscores, not starting with a digit.")
            return None
        return n

    def save_and_load(self) -> bool:
        n = self._filename()
        if n is None:
            return False
        src = self.edit.toPlainText()
        try:
            compile(src, f"{n}.py", "exec")
        except SyntaxError as e:
            self._say(f"✗ Syntax error on line {e.lineno}: {e.msg}")
            return False
        if "def register_nodes" not in src:
            self._say("✗ The file must define register_nodes(model).")
            return False
        before = set(model._REGISTRY)
        path = plugins.user_plugin_dir() / f"{n}.py"
        path.write_text(src, encoding="utf-8")
        loaded, errors = (getattr(plugins, "reload_all", None) or plugins.load_all)()
        mine = [e for e in errors if e.startswith(f"{n}.py")]
        if mine:
            self._say("✗ " + mine[0])
            return False
        self.win.palette.reload()
        self.loaded = sorted(set(model._REGISTRY) - before) or self.loaded
        self._say(f"✓ Saved {path}")
        self._say("✓ Loaded. New node type(s): " + (", ".join(self.loaded) if self.loaded else "(updated existing)"))
        self.win._workshop_saved = True
        return True

    def test_generate(self):
        if not self.save_and_load():
            return
        from ..core import project
        from ..core.model import Graph, get_spec
        tid = (self.loaded or [None])[0]
        spec = get_spec(tid) if tid else None
        if spec is None:
            self._say("✗ No node type to test.")
            return
        g = Graph()
        t = g.add_node("trigger.slash", 0, 0, {"name": "try"})
        n = g.add_node(tid, 300, 0)
        try:
            g.connect(t.id, "next", n.id, "in")
            res = project.build(g)
        except Exception as e:                      # a bug in the plugin's gen function
            self._say(f"✗ Your gen() raised {type(e).__name__}: {e}")
            return
        if not res.ok:
            self._say("✗ The generated project has errors:\n  " + "\n  ".join(res.errors[:5]))
            return
        body = next((c.source for c in res.cogs if tid.split('.')[-1] in c.source or n.id in c.source), "")
        lines = [ln for ln in body.splitlines() if n.id in ln]
        self._say("✓ Generated code compiles. What your node emitted:\n  " + "\n  ".join(ln.strip() for ln in lines[:4]))
        self.win._workshop_tested = True
