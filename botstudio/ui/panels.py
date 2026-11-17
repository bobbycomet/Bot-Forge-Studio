"""Dock panels: node palette, property editor, code preview, problems, output and the Bot Manager."""
from __future__ import annotations

import re
import time

from PyQt6.QtCore import QMimeData, QRegularExpression, Qt, pyqtSignal
from PyQt6.QtGui import (QColor, QFont, QFontDatabase, QSyntaxHighlighter, QTextCharFormat, QRegularExpressionValidator)
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
                             QScrollArea, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core.model import CATEGORY_COLORS, Graph, all_specs
from . import theme
from .canvas import MIME


# ---------------------------------------------------------------------------- palette

class _DragTree(QTreeWidget):
    def mimeData(self, items):
        m = QMimeData()
        if items and items[0].data(0, Qt.ItemDataRole.UserRole):
            m.setData(MIME, items[0].data(0, Qt.ItemDataRole.UserRole).encode())
        return m


class PalettePanel(QWidget):
    addRequested = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search nodes…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.tree = _DragTree()
        self.tree.setHeaderHidden(True)
        self.tree.setDragEnabled(True)
        self.tree.setIndentation(14)
        self.tree.itemDoubleClicked.connect(self._dbl)
        hint = QLabel("Double-click or drag onto the canvas")
        hint.setObjectName("muted")
        lay.addWidget(self.search)
        lay.addWidget(self.tree, 1)
        lay.addWidget(hint)
        self.reload()

    def reload(self):
        self.tree.clear()
        cats: dict[str, QTreeWidgetItem] = {}
        order = list(CATEGORY_COLORS)
        for spec in sorted(all_specs(), key=lambda s: (order.index(s.category) if s.category in order else 99, s.title)):
            top = cats.get(spec.category)
            if top is None:
                top = QTreeWidgetItem([spec.category])
                top.setForeground(0, QColor(CATEGORY_COLORS.get(spec.category, theme.TEXT)))
                f = top.font(0)
                f.setBold(True)
                top.setFont(0, f)
                top.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.tree.addTopLevelItem(top)
                top.setExpanded(True)
                cats[spec.category] = top
            it = QTreeWidgetItem([spec.title])
            it.setData(0, Qt.ItemDataRole.UserRole, spec.type_id)
            it.setToolTip(0, spec.description or spec.title)
            top.addChild(it)

    def _filter(self, text: str):
        text = text.lower().strip()
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            vis = 0
            for j in range(top.childCount()):
                ch = top.child(j)
                hit = not text or text in ch.text(0).lower() or text in (ch.toolTip(0) or "").lower()
                ch.setHidden(not hit)
                vis += hit
            top.setHidden(vis == 0)

    def _dbl(self, item, _col):
        tid = item.data(0, Qt.ItemDataRole.UserRole)
        if tid:
            self.addRequested.emit(tid)


# ---------------------------------------------------------------------------- properties

class PropertyPanel(QScrollArea):
    changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self._host = QWidget()
        self.setWidget(self._host)
        self._lay = QVBoxLayout(self._host)
        self._lay.setContentsMargins(10, 10, 10, 10)
        self.show_node(None, None)

    def _clear(self):
        """Remove every widget *and* nested layout so forms never stack."""
        def wipe(layout):
            if layout is None:
                return
            while layout.count():
                it = layout.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()
                child = it.layout()
                if child is not None:
                    wipe(child)
                # spacer items need no extra work after takeAt
        wipe(self._lay)

    def show_node(self, graph: Graph | None, node_id: str | None):
        self._graph = graph
        self._clear()
        self._pw = {}
        node = graph.nodes.get(node_id) if graph and node_id else None
        if node is None:
            lbl = QLabel("Select a node to edit its properties.\n\nRight-click the canvas to add nodes.\n"
                         "Drag from a port to connect.\nMiddle-drag pans, the wheel zooms.")
            lbl.setObjectName("muted")
            lbl.setWordWrap(True)
            self._lay.addWidget(lbl)
            self._lay.addStretch()
            return
        spec = node.spec
        if spec is None:
            lbl = QLabel(f"This node type ({node.type_id}) is not installed.\nIts data is kept so nothing is lost.")
            lbl.setWordWrap(True)
            self._lay.addWidget(lbl)
            self._lay.addStretch()
            return
        title = QLabel(spec.title)
        title.setObjectName("h")
        self._lay.addWidget(title)
        cat = QLabel(f"{spec.category} · id {node.id}")
        cat.setStyleSheet(f"color:{CATEGORY_COLORS.get(spec.category, theme.MUTED)}")
        self._lay.addWidget(cat)
        if spec.description:
            d = QLabel(spec.description)
            d.setObjectName("muted")
            d.setWordWrap(True)
            self._lay.addWidget(d)
        form_host = QWidget()
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignTop)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for p in spec.props:
            w = self._make(node, p)
            wired = graph.incoming(node.id, p.key) is not None
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(2)
            bl.addWidget(w)
            if wired:
                w.setEnabled(False)
                note = QLabel("driven by a wire")
                note.setStyleSheet(f"color:{theme.ACCENT}; font-size:11px")
                bl.addWidget(note)
            elif p.help:
                h = QLabel(p.help)
                h.setObjectName("muted")
                h.setWordWrap(True)
                h.setStyleSheet("font-size:11px")
                bl.addWidget(h)
            lab = QLabel(p.text)
            lab.setWordWrap(True)
            self._pw[p.key] = box           # guided lessons highlight the editor of a specific property
            form.addRow(lab, box)
        self._lay.addWidget(form_host)
        if spec.intents or spec.requires:
            info = QLabel("Needs: " + ", ".join([f"{i} intent" for i in spec.intents] + list(spec.requires)))
            info.setObjectName("muted")
            info.setWordWrap(True)
            self._lay.addWidget(info)
        self._lay.addStretch()

    def _set(self, node, key, value):
        node.props[key] = value
        self.changed.emit(node.id)

    def _make(self, node, p) -> QWidget:
        v = node.prop_value(p.key)
        if p.kind == "bool":
            w = QCheckBox()
            w.setChecked(bool(v) if not isinstance(v, str) else v.lower() in ("1", "true", "yes"))
            w.toggled.connect(lambda x: self._set(node, p.key, bool(x)))
        elif p.kind == "choice":
            w = QComboBox()
            w.addItems([str(c) for c in p.choices])
            w.setCurrentText(str(v))
            w.currentTextChanged.connect(lambda x: self._set(node, p.key, x))
        elif p.kind == "text":
            is_js = node.type_id == "action.js"     # server-side JavaScript (Web Route flows)
            web_lang = {"web.html": "html", "web.css": "css", "web.js": "browserjs"}.get(node.type_id) if p.key == "code" else None
            is_code = is_js or bool(web_lang) or p.key in ("code", "python", "source", "script") or "python" in (p.label or "").lower()
            w = QPlainTextEdit(str(v if v is not None else ""))
            font = QFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
            font.setPointSize(11)
            if is_code:
                w.setFont(font)
                w.setFixedHeight(220)
                w.setTabChangesFocus(False)
                if web_lang:
                    w.setPlaceholderText({"html": "<!-- the page body: Studio adds <html>, <head> and your CSS / JS links -->",
                                          "css": "/* styles for this page */",
                                          "browserjs": "// runs in the visitor's BROWSER — not discord.js"}[web_lang])
                    self._web_hl = {"html": HtmlHighlighter, "css": CssHighlighter, "browserjs": JsHighlighter}[web_lang](w.document())
                elif is_js:
                    w.setPlaceholderText("// Server JavaScript runs inside the Web Route handler — req, res, bs available")
                    self._js_hl = JsHighlighter(w.document())
                else:
                    w.setPlaceholderText("# Python runs inside the handler — self, bs, interaction/message available")
            else:
                w.setFixedHeight(120)
                w.setTabChangesFocus(True)
            edit_w = w   # bind now: `w` is rebound to the wrapper below, which has no toPlainText()
            w.textChanged.connect(lambda _=None, e=edit_w, n=node, k=p.key: self._set(n, k, e.toPlainText()))
            if is_code:
                wrap = QWidget()
                vl = QVBoxLayout(wrap)
                vl.setContentsMargins(0, 0, 0, 0)
                vl.setSpacing(4)
                vl.addWidget(w)
                try:
                    from ..core.scope import editor_context as _ec
                    _g = getattr(self, "_graph", None)
                    _names = _ec(_g, node, p.key)["names"] if _g else []
                except Exception:
                    _names = []
                hint = QLabel("In scope: " + (", ".join(_names[:14]) + (" …" if len(_names) > 14 else "") if _names
                                              else "connect this node after a trigger to see its variables"))
                hint.setObjectName("muted")
                hint.setWordWrap(True)
                hint.setStyleSheet("font-size:11px")
                vl.addWidget(hint)
                btn = QPushButton("Open in editor…")
                btn.setToolTip("Full-size code editor for this node")
                def _open(n=node, key=p.key, edit=w):
                    from .code_editor import CodeEditorDialog
                    from ..core.scope import editor_context
                    tid = getattr(n, "type_id", "")
                    lang = {"action.js": "js", "web.html": "html", "web.css": "css", "web.js": "browserjs"}.get(tid, "python")
                    label = {"js": "Server JavaScript", "html": "HTML", "css": "CSS", "browserjs": "Browser JavaScript", "python": "Custom Python"}[lang]
                    title = f"{label} — {(n.spec.title if getattr(n, 'spec', None) else None) or tid}"
                    # everything this node is attached to: trigger locals, earlier nodes' outputs, wired inputs
                    ctx = editor_context(self._graph, n, key) if getattr(self, "_graph", None) else {"runtime": "js" if lang == "js" else lang}
                    dlg = CodeEditorDialog(self, title, edit.toPlainText(), context=ctx)
                    if dlg.exec():
                        text = dlg.source()
                        edit.setPlainText(text)
                        self._set(n, key, text)
                btn.clicked.connect(lambda *_args: _open())
                vl.addWidget(btn)
                w = wrap
        elif p.kind == "int":
            w = QLineEdit("" if v is None else str(v))
            w.setValidator(QRegularExpressionValidator(QRegularExpression(r"-?\d{0,19}")))
            w.textEdited.connect(lambda x: self._set(node, p.key, int(x) if re.fullmatch(r"-?\d+", x) else 0))
        elif p.kind == "float":
            w = QDoubleSpinBox()
            w.setRange(-1e9, 1e9)
            w.setDecimals(2)
            try:
                w.setValue(float(v))
            except (TypeError, ValueError):
                w.setValue(0)
            w.valueChanged.connect(lambda x: self._set(node, p.key, x))
        else:
            w = QLineEdit("" if v is None else str(v))
            w.textEdited.connect(lambda x: self._set(node, p.key, x))
        return w


# ---------------------------------------------------------------------------- code view

class PyHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)

        def fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            f.setFontItalic(italic)
            return f

        kw = r"\b(?:and|as|assert|async|await|break|class|continue|def|elif|else|except|finally|for|from|if|import|in|is|lambda|not|or|pass|raise|return|try|while|with|yield|None|True|False|self)\b"
        self.rules = [
            (QRegularExpression(kw), fmt("#ff8d8d", True)),
            (QRegularExpression(r"\b\d+(\.\d+)?\b"), fmt("#9be37f")),
            (QRegularExpression(r"@\w[\w.]*"), fmt("#ffd166")),
            (QRegularExpression(r"\b(?:def|class)\s+(\w+)"), fmt("#66c0f4", True)),
            (QRegularExpression(r"'[^'\\\n]*(?:\\.[^'\\\n]*)*'|\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\""), fmt("#a5d6a7")),
            (QRegularExpression(r"#[^\n]*"), fmt("#6b7a88", italic=True)),
            (QRegularExpression(r"\[[0-9a-f]{6}\]"), fmt("#66c0f4")),
        ]

    def highlightBlock(self, text):
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)


def _fmt(color, bold=False, italic=False):
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.Weight.Bold)
    f.setFontItalic(italic)
    return f


class _RuleHighlighter(QSyntaxHighlighter):
    RULES: list = []

    def __init__(self, doc):
        super().__init__(doc)
        self.rules = [(QRegularExpression(rx), _fmt(*fm)) for rx, fm in self.RULES]

    def highlightBlock(self, text):
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)


class HtmlHighlighter(_RuleHighlighter):
    RULES = [
        (r"</?[A-Za-z][A-Za-z0-9-]*", ("#ff8d8d", True)),
        (r"/?>", ("#ff8d8d", True)),
        (r"\b[A-Za-z-]+(?==)", ("#ffd166",)),
        (r"\"[^\"]*\"|'[^']*'", ("#a5d6a7",)),
        (r"&[A-Za-z]+;", ("#9be37f",)),
        (r"<!--.*?-->", ("#6b7a88", False, True)),
    ]


class CssHighlighter(_RuleHighlighter):
    RULES = [
        (r"[.#]?[A-Za-z_-][\w-]*(?=[^{}]*\{)", ("#66c0f4", True)),
        (r"[a-z-]+(?=\s*:)", ("#ffd166",)),
        (r"#[0-9a-fA-F]{3,8}\b|\b\d+(\.\d+)?(px|em|rem|%|vh|vw|s|ms|deg|fr)?\b", ("#9be37f",)),
        (r"@[a-z-]+", ("#ff8d8d", True)),
        (r"var\(--[\w-]+\)", ("#c792ea",)),
        (r"\"[^\"]*\"|'[^']*'", ("#a5d6a7",)),
        (r"/\*.*?\*/", ("#6b7a88", False, True)),
    ]


class JsHighlighter(QSyntaxHighlighter):
    """Light JavaScript highlighter (keywords, numbers, strings, comments) in the Griffin Dark palette."""

    def __init__(self, doc):
        super().__init__(doc)

        def fmt(color, bold=False, italic=False):
            f = QTextCharFormat()
            f.setForeground(QColor(color))
            if bold:
                f.setFontWeight(QFont.Weight.Bold)
            f.setFontItalic(italic)
            return f

        kw = (r"\b(?:async|await|break|case|catch|class|const|continue|default|delete|do|else|export|extends|finally|for|"
              r"function|if|import|in|instanceof|let|new|of|return|switch|this|throw|try|typeof|var|void|while|yield|"
              r"null|undefined|true|false|require|module)\b")
        self.rules = [
            (QRegularExpression(kw), fmt("#ff8d8d", True)),
            (QRegularExpression(r"\b\d+(\.\d+)?\b"), fmt("#9be37f")),
            (QRegularExpression(r"\b(?:bs|bot|client)\b"), fmt("#ffd166")),
            (QRegularExpression(r"\b(?:function|class)\s+(\w+)"), fmt("#66c0f4", True)),
            (QRegularExpression(r"'[^'\\\n]*(?:\\.[^'\\\n]*)*'|\"[^\"\\\n]*(?:\\.[^\"\\\n]*)*\"|`[^`]*`"), fmt("#a5d6a7")),
            (QRegularExpression(r"//[^\n]*"), fmt("#6b7a88", italic=True)),
        ]

    def highlightBlock(self, text):
        for rx, f in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), f)


class CodeView(QWidget):
    def __init__(self):
        super().__init__()
        self.files: dict[str, str] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        top = QHBoxLayout()
        self.combo = QComboBox()
        self.combo.currentTextChanged.connect(self._show)
        top.addWidget(QLabel("File:"))
        top.addWidget(self.combo, 1)
        lay.addLayout(top)
        self.edit = QPlainTextEdit()
        self.edit.setReadOnly(True)
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.edit.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.hl = PyHighlighter(self.edit.document())
        self.hl_js = JsHighlighter(None)
        lay.addWidget(self.edit, 1)

    def set_files(self, files: dict[str, str]):
        cur = self.combo.currentText()
        self.files = files
        self.combo.blockSignals(True)
        self.combo.clear()
        order = sorted(files, key=lambda p: (0 if p.startswith("cogs/") and p != "cogs/__init__.py" else 1, p))
        self.combo.addItems([p for p in order if p != "cogs/__init__.py"])
        if cur in files:
            self.combo.setCurrentText(cur)
        self.combo.blockSignals(False)
        self._show(self.combo.currentText())

    def select(self, path: str):
        if path in self.files:
            self.combo.setCurrentText(path)

    def _show(self, path: str):
        bar = self.edit.verticalScrollBar()
        pos = bar.value()
        if path.endswith((".js", ".cjs", ".mjs")):
            self.hl.setDocument(None)
            self.hl_js.setDocument(self.edit.document())
        else:
            self.hl_js.setDocument(None)
            self.hl.setDocument(self.edit.document())
        self.edit.setPlainText(self.files.get(path, ""))
        bar.setValue(pos)


# ---------------------------------------------------------------------------- problems / output

class ProblemsList(QListWidget):
    nodeRequested = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.itemActivated.connect(self._go)
        self.itemClicked.connect(self._go)

    def set_problems(self, errors: list[str], warnings: list[str]):
        self.clear()
        for e in errors:
            it = QListWidgetItem("✖  " + e)
            it.setForeground(QColor(theme.BAD))
            self.addItem(it)
        for w in warnings:
            it = QListWidgetItem("⚠  " + w)
            it.setForeground(QColor(theme.WARN))
            self.addItem(it)
        if not errors and not warnings:
            it = QListWidgetItem("✔  No problems — the graph compiles cleanly.")
            it.setForeground(QColor(theme.GOOD))
            self.addItem(it)

    def _go(self, item):
        m = re.search(r"\[([0-9a-f]{6})\]", item.text())
        if m:
            self.nodeRequested.emit(m.group(1))


class OutputLog(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setMaximumBlockCount(5000)

    def write(self, text: str):
        stamp = time.strftime("%H:%M:%S")
        for ln in str(text).splitlines() or [""]:
            self.appendPlainText(f"{stamp}  {ln}")


# ---------------------------------------------------------------------------- bot manager

class ManagerPanel(QWidget):
    deployRequested = pyqtSignal()
    syncRequested = pyqtSignal()
    actionRequested = pyqtSignal(str)
    logsRequested = pyqtSignal()
    targetRequested = pyqtSignal()
    autoSyncToggled = pyqtSignal(bool)
    refreshRequested = pyqtSignal()

    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        t = QLabel("Bot Manager")
        t.setObjectName("h")
        lay.addWidget(t)
        self.target = QLabel("")
        self.target.setObjectName("muted")
        self.target.setWordWrap(True)
        lay.addWidget(self.target)
        b = QPushButton("Deploy target…")
        b.clicked.connect(self.targetRequested)
        lay.addWidget(b)

        box = QFrame()
        box.setStyleSheet(f"QFrame{{background:{theme.PANEL}; border:1px solid {theme.RAISED}; border-radius:4px;}}")
        bl = QVBoxLayout(box)
        self.state = QLabel("● Unknown")
        self.state.setStyleSheet(f"color:{theme.MUTED}; font-size:15px; font-weight:bold; border:none")
        self.details = QLabel("")
        self.details.setWordWrap(True)
        self.details.setStyleSheet("border:none")
        bl.addWidget(self.state)
        bl.addWidget(self.details)
        lay.addWidget(box)

        grid = QGridLayout()
        self.btn_deploy = QPushButton("Deploy")
        self.btn_deploy.setObjectName("primary")
        self.btn_deploy.setToolTip("Build, upload, create the venv, install the service and start it")
        self.btn_sync = QPushButton("Live Sync")
        self.btn_sync.setToolTip("Push only what changed; the running bot hot-reloads without a restart")
        self.btn_start, self.btn_stop, self.btn_restart = QPushButton("Start"), QPushButton("Stop"), QPushButton("Restart")
        self.btn_enable, self.btn_disable = QPushButton("Enable at boot"), QPushButton("Disable at boot")
        self.btn_logs = QPushButton("View logs")
        self.btn_refresh = QPushButton("Refresh")
        self.btn_deploy.clicked.connect(self.deployRequested)
        self.btn_sync.clicked.connect(self.syncRequested)
        self.btn_start.clicked.connect(lambda: self.actionRequested.emit("start"))
        self.btn_stop.clicked.connect(lambda: self.actionRequested.emit("stop"))
        self.btn_restart.clicked.connect(lambda: self.actionRequested.emit("restart"))
        self.btn_enable.clicked.connect(lambda: self.actionRequested.emit("enable"))
        self.btn_disable.clicked.connect(lambda: self.actionRequested.emit("disable"))
        self.btn_logs.clicked.connect(self.logsRequested)
        self.btn_refresh.clicked.connect(self.refreshRequested)
        grid.addWidget(self.btn_deploy, 0, 0)
        grid.addWidget(self.btn_sync, 0, 1)
        grid.addWidget(self.btn_start, 1, 0)
        grid.addWidget(self.btn_stop, 1, 1)
        grid.addWidget(self.btn_restart, 2, 0)
        grid.addWidget(self.btn_logs, 2, 1)
        grid.addWidget(self.btn_enable, 3, 0)
        grid.addWidget(self.btn_disable, 3, 1)
        grid.addWidget(self.btn_refresh, 4, 0, 1, 2)
        lay.addLayout(grid)

        self.auto = QCheckBox("Auto live-sync when I edit")
        self.auto.setToolTip("Rebuilds and pushes changes a moment after each edit. Build errors are never pushed.")
        self.auto.toggled.connect(self.autoSyncToggled)
        lay.addWidget(self.auto)
        lay.addStretch()

    def set_busy(self, busy: bool):
        for b in (self.btn_deploy, self.btn_sync, self.btn_start, self.btn_stop, self.btn_restart,
                  self.btn_enable, self.btn_disable, self.btn_logs, self.btn_refresh):
            b.setEnabled(not busy)

    def set_target(self, text: str):
        self.target.setText(text)

    def set_status(self, st: dict):
        state, info = st.get("state", "unknown"), st.get("info") or {}
        color = {"active": theme.GOOD, "inactive": theme.WARN}.get(state, theme.MUTED)
        label = {"active": "Running", "inactive": "Stopped"}.get(state, "Unknown")
        self.state.setText(f"● {label}")
        self.state.setStyleSheet(f"color:{color}; font-size:15px; font-weight:bold; border:none")
        lines = []
        if st.get("error"):
            lines.append(f"<span style='color:{theme.BAD}'>{st['error']}</span>")
        if info and state == "active":
            up = max(0, int(info.get("now", 0) - info.get("started", 0)))
            lines.append(f"Account: <b>{info.get('user') or '—'}</b>")
            lines.append(f"Uptime: {up // 3600}h {up % 3600 // 60}m · Latency: {info.get('latency_ms', '—')} ms")
            lines.append(f"Servers: {info.get('guilds', 0)} · Cogs loaded: {len(info.get('cogs', []))}")
            if info.get("restart_required"):
                lines.append(f"<span style='color:{theme.WARN}'>Core files changed — restart to apply.</span>")
            for e in info.get("errors", [])[:3]:
                lines.append(f"<span style='color:{theme.BAD}'>{e}</span>")
        self.details.setText("<br>".join(lines))
