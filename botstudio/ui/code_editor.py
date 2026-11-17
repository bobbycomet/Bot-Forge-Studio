"""Full-size Custom Python editor with completions, snippets, and diagnostics."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QRect, QSize, QTimer
from PyQt6.QtGui import (
    QColor, QFont, QFontDatabase, QKeySequence, QPainter, QShortcut,
    QTextCharFormat, QTextCursor, QTextFormat,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTextEdit, QToolBar, QVBoxLayout, QWidget,
)

from .panels import CssHighlighter, HtmlHighlighter, JsHighlighter, PyHighlighter

try:
    from ..core import editor_assist
except ImportError:
    from botstudio.core import editor_assist  # type: ignore


class _LineNumberArea(QWidget):
    def __init__(self, editor: "_CodeEdit"):
        super().__init__(editor)
        self._editor = editor

    def sizeHint(self):
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):
        self._editor.paint_line_numbers(event)


class _CodeEdit(QPlainTextEdit):
    """Code editor with line numbers, Ctrl+Space complete, tab = 4 spaces."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._assist_parent = None  # CodeEditorDialog
        self._line_numbers = _LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_width)
        self.updateRequest.connect(self._on_update_request)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_line_number_width(0)
        self._highlight_current_line()

    def line_number_area_width(self) -> int:
        digits = max(2, len(str(max(1, self.blockCount()))))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_number_width(self, _=0):
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _on_update_request(self, rect: QRect, dy: int):
        if dy:
            self._line_numbers.scroll(0, dy)
        else:
            self._line_numbers.update(0, rect.y(), self._line_numbers.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self._line_numbers.setGeometry(
            QRect(cr.left(), cr.top(), self.line_number_area_width(), cr.height()))

    def paint_line_numbers(self, event):
        painter = QPainter(self._line_numbers)
        painter.fillRect(event.rect(), QColor("#1a1f27"))
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                number = str(block_number + 1)
                painter.setPen(QColor("#6b7a88"))
                painter.drawText(
                    0, top,
                    self._line_numbers.width() - 6,
                    self.fontMetrics().height(),
                    Qt.AlignmentFlag.AlignRight,
                    number,
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1

    def _highlight_current_line(self):
        if self.isReadOnly():
            return
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor("#243040"))
        sel.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        # Keep diagnostic underlines from parent if any — parent sets extras after analyze.
        # Only set current-line here when no parent manage; dialog merges later.
        extras = [sel]
        # Preserve underline selections tagged by dialog via _diag_selections
        for s in getattr(self, "_diag_selections", []) or []:
            extras.append(s)
        self.setExtraSelections(extras)

    def set_diagnostic_selections(self, selections: list):
        self._diag_selections = list(selections)
        self._highlight_current_line()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Tab and not event.modifiers():
            self.insertPlainText("    ")
            return
        if event.key() == Qt.Key.Key_Space and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if self._assist_parent:
                self._assist_parent.show_completions()
            return
        super().keyPressEvent(event)
        if self._assist_parent and (event.text() in "._" or (event.text().isalnum() if event.text() else False)):
            self._assist_parent.schedule_completion_hint()


class CodeEditorDialog(QDialog):
    """Modal editor used by PropertyPanel 'Open in editor…'."""

    def __init__(self, parent, title: str, source: str, context: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle(title or "Custom Python")
        self.resize(960, 640)
        self.setModal(True)
        self.context = context or {}
        self.lang = self.context.get("runtime") or "python"
        self.js = self.lang == "js"                       # server-side JavaScript (Web Route flows)
        self.web = self.lang in ("html", "css", "browserjs")
        self._completion_items: list[editor_assist.Completion] = []
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(400)
        self._debounce.timeout.connect(self._quiet_analyze)

        font = QFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        font.setPointSize(11)

        tip = QLabel(
            ("Inline handler code (an <b>async</b> function body). Scope usually includes <b>bot</b>, <b>bs</b>, "
             "and trigger locals (<b>interaction</b>, <b>message</b>, <b>req</b>/<b>res</b>, …). Use <b>var</b> to share a "
             "variable with later nodes. " if self.js else
             "Inline handler code. Scope usually includes <b>self</b>, <b>self.bot</b>, <b>bs</b>, "
             "and trigger locals (<b>interaction</b>, <b>message</b>, …). ")
            + "Ctrl+Space: complete · Snippets menu · diagnostics update as you type."
        )
        if self.web:
            tip.setText({
                "html": "<b>HTML</b> — the page body. Studio wraps it in a document and links the CSS / JavaScript nodes connected to this page. "
                        "Inline scripts, <b>on…=</b> handlers and <b>style=</b> attributes are blocked by the dashboard's security policy.",
                "css": "<b>CSS</b> — styles for the connected page. Flexbox, grid, media queries and animations all work; external fonts / imports are blocked.",
                "browserjs": "<b>Browser JavaScript</b> — runs in the visitor's <b>browser</b>, not in discord.js and not on the server. "
                             "Use <b>BotStudio.api()</b> / <b>fetch</b> for your Web Routes and <b>BotStudio.live()</b> for WebSocket updates. "
                             "<b>require()</b> and <b>process</b> do not exist here; secrets never belong in this file.",
            }[self.lang] + " Ctrl+Space: complete · diagnostics update as you type.")
        tip.setWordWrap(True)

        self.edit = _CodeEdit()
        self.edit._assist_parent = self
        self.edit.setFont(font)
        self.edit.setPlainText(source)
        self.edit.setTabChangesFocus(False)
        self.edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._hl = {"js": JsHighlighter, "browserjs": JsHighlighter, "html": HtmlHighlighter, "css": CssHighlighter}.get(self.lang, PyHighlighter)(self.edit.document())
        self.edit.textChanged.connect(self._on_text_changed)

        # Completions popup (floating list under cursor would be ideal; docked list is robust)
        self.comp_list = QListWidget()
        self.comp_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.comp_list.itemActivated.connect(self._apply_selected_completion)
        self.comp_list.setMinimumWidth(260)
        self.comp_list.setMaximumWidth(320)

        self.diag_list = QListWidget()
        self.diag_list.itemClicked.connect(self._goto_diagnostic)
        self.diag_list.setMinimumHeight(100)

        right = QVBoxLayout()
        right_w = QWidget()
        right_w.setLayout(right)
        right.addWidget(QLabel("Completions"))
        right.addWidget(self.comp_list, 2)
        self.scope_list = QListWidget()
        self.scope_list.setToolTip("Variables from the nodes this one is attached to — double-click to insert")
        for v in self.context.get("vars", []):
            it = QListWidgetItem(f"{v['name']}  —  {v.get('type', 'any')} · {v.get('origin', '')}")
            it.setData(Qt.ItemDataRole.UserRole, v["name"])
            self.scope_list.addItem(it)
        for o in self.context.get("outputs", []):
            it = QListWidgetItem(f"{o}  —  output (assign it)")
            it.setData(Qt.ItemDataRole.UserRole, o)
            self.scope_list.addItem(it)
        self.scope_list.itemDoubleClicked.connect(lambda it: self._insert(it.data(Qt.ItemDataRole.UserRole)))
        ups = self.context.get("upstream") or []
        right.addWidget(QLabel("Attached nodes: " + (", ".join(ups) if ups else "none yet")))
        right.addWidget(QLabel("In scope"))
        right.addWidget(self.scope_list, 1)
        right.addWidget(QLabel("Problems"))
        right.addWidget(self.diag_list, 1)

        split = QSplitter()
        split.addWidget(self.edit)
        split.addWidget(right_w)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)

        bar = QToolBar()
        act_complete = bar.addAction("Complete")
        act_complete.setShortcut(QKeySequence("Ctrl+Space"))
        act_complete.triggered.connect(self.show_completions)
        act_snip = bar.addAction("Snippets…")
        act_snip.triggered.connect(self._pick_snippet)
        act_check = bar.addAction("Check syntax")  # JavaScript: uses node --check when Node.js is installed
        act_check.triggered.connect(self._force_analyze)
        if not self.web:
            act_fix = bar.addAction("Insert `await bs.respond`")
            act_fix.triggered.connect(lambda: self._insert(
                "await bs.respond(interaction, '', null, { ephemeral: true });" if self.js
                else 'await bs.respond(interaction, "", ephemeral=True)'))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(tip)
        lay.addWidget(bar)
        lay.addWidget(split, 1)
        lay.addWidget(buttons)

        QShortcut(QKeySequence("Ctrl+Space"), self, self.show_completions)

        self._force_analyze()
        self.show_completions()

    # ----- public
    def source(self) -> str:
        return self.edit.toPlainText()

    # ----- completions
    def schedule_completion_hint(self):
        # Debounced refresh of the side list while typing
        if not self._debounce.isActive():
            pass
        self._debounce.start()
        # lightweight: refresh completions when prefix length >= 1
        self.show_completions(quiet=True)

    def show_completions(self, quiet: bool = False):
        pos = self.edit.textCursor().position()
        items = editor_assist.completions(self.edit.toPlainText(), pos, self.context)
        self._completion_items = items
        self.comp_list.clear()
        for c in items:
            label = c.label
            if c.detail:
                label = f"{c.label}  —  {c.detail}"
            it = QListWidgetItem(label)
            it.setData(Qt.ItemDataRole.UserRole, c)
            self.comp_list.addItem(it)
        if items and not quiet:
            self.comp_list.setCurrentRow(0)

    def _apply_selected_completion(self, item: QListWidgetItem | None = None):
        item = item or self.comp_list.currentItem()
        if not item:
            return
        c = item.data(Qt.ItemDataRole.UserRole)
        if not c:
            return
        src = self.edit.toPlainText()
        pos = self.edit.textCursor().position()
        new_src, new_pos = editor_assist.apply_completion(src, pos, c)
        self.edit.blockSignals(True)
        self.edit.setPlainText(new_src)
        self.edit.blockSignals(False)
        cur = self.edit.textCursor()
        cur.setPosition(new_pos)
        self.edit.setTextCursor(cur)
        self.edit.setFocus()
        self._force_analyze()
        self.show_completions(quiet=True)

    # ----- snippets
    def _pick_snippet(self):
        snips = editor_assist.snippets("js" if self.js else "python")
        names = [f"{s.name} — {s.description}" if s.description else s.name for s in snips]
        if not names:
            QMessageBox.information(self, "Snippets", "No snippets registered.")
            return
        name, ok = QInputDialog.getItem(self, "Insert snippet", "Snippet:", names, 0, False)
        if not ok:
            return
        idx = names.index(name)
        self._insert(snips[idx].code)

    def _insert(self, text: str):
        self.edit.insertPlainText(text if text.endswith("\n") else text)
        self.edit.setFocus()
        self._force_analyze()

    # ----- diagnostics
    def _on_text_changed(self):
        self._debounce.start()

    def _quiet_analyze(self):
        self._run_analyze()

    def _force_analyze(self):
        self._run_analyze()

    def _run_analyze(self):
        diags = editor_assist.analyze(self.edit.toPlainText(), self.context)
        self.diag_list.clear()
        # Clear previous extra selections
        selections = []
        for d in diags:
            icon = {"error": "●", "warning": "▲", "info": "■"}.get(d.severity, "•")
            text = f"{icon} L{d.line + 1}: {d.message}"
            if d.suggestion:
                text += f"\n    → {d.suggestion}"
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, d)
            if d.severity == "error":
                it.setForeground(QColor("#ff6b6b"))
            elif d.severity == "warning":
                it.setForeground(QColor("#ffd166"))
            self.diag_list.addItem(it)

            # underline line
            try:
                block = self.edit.document().findBlockByNumber(d.line)
                if block.isValid():
                    cur = QTextCursor(block)
                    cur.select(QTextCursor.SelectionType.LineUnderCursor)
                    sel = QTextEdit.ExtraSelection()
                    sel.cursor = cur
                    fmt = QTextCharFormat()
                    if d.severity == "error":
                        fmt.setUnderlineColor(QColor("#ff6b6b"))
                    else:
                        fmt.setUnderlineColor(QColor("#ffd166"))
                    fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
                    sel.format = fmt
                    selections.append(sel)
            except Exception:
                pass
        self.edit.set_diagnostic_selections(selections)

    def _goto_diagnostic(self, item: QListWidgetItem):
        d = item.data(Qt.ItemDataRole.UserRole)
        if not d:
            return
        block = self.edit.document().findBlockByNumber(d.line)
        if not block.isValid():
            return
        cur = QTextCursor(block)
        cur.movePosition(QTextCursor.MoveOperation.Right, n=min(d.col, block.length()))
        self.edit.setTextCursor(cur)
        self.edit.setFocus()
        if d.suggestion:
            # Show suggestion in status via tooltip flash
            self.diag_list.setToolTip(d.suggestion)
