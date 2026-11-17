"""The guided-lesson engine.

A lesson is *data*: an ordered list of :class:`Step` objects.  The runner turns each step into a task on the real interface —
highlight the node in the palette, wait until the user has dropped it on the canvas, highlight it there, open its properties,
explain the property, validate what they typed — and only moves on when the step's completion check passes.

Step kinds
    info      read-only explanation (optionally highlighting a part of the interface)
    add_node  highlight the node in the palette; done when a node of that type exists   (claims it under ``key``)
    select    highlight the claimed node on the canvas; done when it is selected
    set_prop  highlight one property editor; done when ``check(value)`` passes (and the value changed)
    connect   highlight both nodes; done when the wire exists
    validate  done when the project builds without errors (plus an optional extra ``check``)
    action    a button runs ``button`` (open a dialog, a website …); done when ``check(win, runner)`` is true
    confirm   things only the user can do (launch the bot, test in Discord): done when they press "I did this"
    branch    the user picks a path; later steps with ``when={key: value}`` only apply to that path
    finish    the closing summary; completing it marks the lesson done
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Union

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (QAbstractScrollArea, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QTabWidget,
                             QTextBrowser, QVBoxLayout, QWidget)

from .. import theme
from . import progress

Body = Union[str, Callable[["LessonRunner"], str]]
AUTO_KINDS = ("add_node", "select", "connect")


@dataclass
class Step:
    kind: str
    title: str
    body: Body = ""
    key: str = ""                                   # add_node / select / set_prop: which claimed node
    type_id: str = ""                               # add_node
    prop: str = ""                                  # set_prop
    check: Optional[Callable] = None                # set_prop: (value, node, runner) -> None|str ; validate/action: (win, runner) -> None|str|bool
    allow_default: bool = False                     # set_prop: the default value may already be correct
    src: tuple = ()                                 # connect: (key, port)
    dst: tuple = ()
    when: Optional[dict] = None                     # only applies when ctx[k] == v for every k, v
    choices: list = field(default_factory=list)     # branch: [(value, label)]
    target: str = ""                                # info/validate/...: palette | props | canvas | bottom | right | type:<id> | node:<key> | prop:<key>:<p>
    button: Optional[tuple] = None                  # (label, fn(win, runner))
    hint: str = ""                                  # extra line under the status while waiting
    count: int = 1                                  # add_node: how many nodes of that type must exist (claims the Nth as key)


@dataclass
class Lesson:
    id: str
    title: str
    difficulty: str                                 # Beginner | Intermediate | Advanced
    minutes: int
    summary: str
    steps: list
    outcomes: list = field(default_factory=list)    # shown in the hub
    prerequisites: str = ""


# --------------------------------------------------------------------------------------- overlay
class _Overlay(QWidget):
    """Dims everything except the highlighted rectangles. Mouse-transparent: the user keeps working in the real interface."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.rects: list[QRect] = []
        self._phase = 0
        self._t = QTimer(self)
        self._t.timeout.connect(self._tick)
        self._t.start(60)
        self.hide()

    def _tick(self):
        self._phase = (self._phase + 1) % 40
        if self.rects:
            self.update()

    def set_rects(self, rects: list[QRect]):
        self.rects = [r.adjusted(-5, -5, 5, 5) for r in rects if r is not None and not r.isNull()]
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        full = QPainterPath()
        full.addRect(QRectF(self.rect()))
        if self.rects:
            holes = QPainterPath()
            for r in self.rects:
                holes.addRoundedRect(QRectF(r), 6, 6)
            p.fillPath(full.subtracted(holes), QColor(0, 0, 0, 95))
            pulse = abs(20 - self._phase) / 20.0
            pen = QPen(QColor(102, 192, 244, int(150 + 105 * pulse)))
            pen.setWidth(3)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for r in self.rects:
                p.drawRoundedRect(QRectF(r), 6, 6)
        else:
            p.fillPath(full, QColor(0, 0, 0, 45))


# --------------------------------------------------------------------------------------- coach panel
class _Coach(QFrame):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("lessonCoach")
        self.setFixedWidth(440)
        self.setStyleSheet(f"#lessonCoach {{ background:{theme.PANEL}; border:2px solid {theme.ACCENT}; border-radius:8px; }}"
                           f"#lessonCoach QLabel {{ background:transparent; }}")
        self.head = QLabel()
        self.head.setObjectName("muted")
        self.title = QLabel()
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-size:15px; font-weight:bold; color:white")
        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(True)
        self.body.setMinimumHeight(190)
        self.body.setMaximumHeight(360)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.extra = QHBoxLayout()
        self.back = QPushButton("◀ Back")
        self.next = QPushButton("Next ▶")
        self.next.setObjectName("primary")
        self.close_btn = QPushButton("Save && close")
        nav = QHBoxLayout()
        nav.addWidget(self.back)
        nav.addStretch()
        nav.addWidget(self.close_btn)
        nav.addWidget(self.next)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)
        for w in (self.head, self.title, self.body, self.status):
            lay.addWidget(w)
        lay.addLayout(self.extra)
        lay.addLayout(nav)

    def set_status(self, level: str, text: str):
        color = {"ok": theme.GOOD, "wait": theme.MUTED, "warn": theme.WARN, "error": theme.BAD}.get(level, theme.MUTED)
        icon = {"ok": "✓ ", "wait": "⏳ ", "warn": "⚠ ", "error": "✗ "}.get(level, "")
        import html
        import re
        rich = re.sub(r"`([^`]+)`", r"<code>\1</code>", re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(text))).replace("\n", "<br>")
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setStyleSheet(f"color:{color}; font-size:12px")
        self.status.setText(icon + rich if text else "")

    def clear_extra(self):
        while self.extra.count():
            it = self.extra.takeAt(0)
            if it.widget():
                it.widget().deleteLater()


# --------------------------------------------------------------------------------------- runner
class LessonRunner(QObject):
    finished = pyqtSignal(bool)

    def __init__(self, win, lesson: Lesson, resume: bool = True):
        super().__init__(win)
        self.win, self.lesson = win, lesson
        saved = progress.get(lesson.id) if resume else {"step": 0, "done": False, "ctx": {}}
        self.ctx: dict = dict(saved.get("ctx", {}))
        self.claimed: dict[str, str] = {}
        self.confirmed: set[int] = set()
        self.idx = min(int(saved.get("step", 0)), len(lesson.steps) - 1)
        self._entered = -1
        self._closing = False
        self.overlay = _Overlay(win)
        self.coach = _Coach(win)
        self.coach.back.clicked.connect(self.back)
        self.coach.next.clicked.connect(self.next)
        self.coach.close_btn.clicked.connect(self.close)
        win.installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)

    # ---- lifecycle ------------------------------------------------------------------
    def start(self):
        self._resolve_before(self.idx)
        self.overlay.setGeometry(self.win.rect())
        self.overlay.show()
        self.overlay.raise_()
        self.coach.show()
        self.coach.raise_()
        self.timer.start(300)
        self._enter(self.idx)

    def close(self, completed: bool = False):
        if self._closing:
            return
        self._closing = True
        self.timer.stop()
        progress.put(self.lesson.id, self.idx, completed, self.ctx)
        self.win.removeEventFilter(self)
        self.overlay.hide()
        self.overlay.deleteLater()
        self.coach.hide()
        self.coach.deleteLater()
        self.finished.emit(completed)

    def eventFilter(self, obj, ev):
        if obj is self.win and ev.type() in (QEvent.Type.Resize, QEvent.Type.Move) and not self._closing:
            self.overlay.setGeometry(self.win.rect())
            self._place()
        return False

    # ---- step bookkeeping ------------------------------------------------------------
    def _applies(self, st: Step) -> bool:
        return not st.when or all(self.ctx.get(k) == v for k, v in st.when.items())

    def visible(self) -> list[int]:
        return [i for i, s in enumerate(self.lesson.steps) if self._applies(s)]

    def step(self) -> Step:
        return self.lesson.steps[self.idx]

    def _resolve_before(self, upto: int):
        """After a restart, re-attach the keys of earlier add_node steps to the nodes already on the canvas."""
        by_type: dict[str, list] = {}
        for nid, n in self.win.graph.nodes.items():
            by_type.setdefault(n.type_id, []).append(nid)
        used: dict[str, int] = {}
        for st in self.lesson.steps[:upto + 1]:
            if st.kind == "add_node" and st.key and self._applies(st):
                lst = by_type.get(st.type_id, [])
                i = used.get(st.type_id, 0)
                if i < len(lst):
                    self.claimed[st.key] = lst[i]
                    used[st.type_id] = i + 1

    def _node(self, key: str):
        nid = self.claimed.get(key)
        return self.win.graph.nodes.get(nid) if nid else None

    def _body(self, st: Step) -> str:
        return st.body(self) if callable(st.body) else st.body

    # ---- navigation -------------------------------------------------------------------
    def _enter(self, i: int):
        self.idx = i
        st = self.step()
        vis = self.visible()
        pos = vis.index(i) + 1 if i in vis else 1
        self.coach.head.setText(f"{self.lesson.title}  ·  step {pos} of {len(vis)}  ·  {self.lesson.difficulty}")
        self.coach.title.setText(st.title)
        self.coach.body.setMarkdown(self._body(st))
        self.coach.back.setEnabled(pos > 1)
        last = pos == len(vis)
        self.coach.next.setText("Finish ✓" if last else ("Skip ▶" if st.kind not in ("info", "finish") else "Next ▶"))
        self.coach.clear_extra()
        self._build_extra(st)
        self._focus_step(st)
        self._entered = i
        progress.put(self.lesson.id, i, False, self.ctx)
        self._poll()
        self._place()

    def _build_extra(self, st: Step):
        if st.kind == "branch":
            for value, label in st.choices:
                b = QPushButton(label)
                b.clicked.connect(lambda _=False, v=value: self._choose(st, v))
                self.coach.extra.addWidget(b)
        if st.kind == "confirm":
            b = QPushButton("I did this ✓")
            b.setObjectName("primary")
            b.clicked.connect(lambda: (self.confirmed.add(self.idx), self._poll()))
            self.coach.extra.addWidget(b)
        if st.button:
            label, fn = st.button
            b = QPushButton(label)
            b.clicked.connect(lambda: (fn(self.win, self), self._poll()))
            self.coach.extra.addWidget(b)
        self.coach.extra.addStretch()

    def _choose(self, st: Step, value):
        self.ctx[st.key] = value
        self._poll()
        QTimer.singleShot(250, self.next)

    def next(self):
        vis = self.visible()
        if self.idx not in vis:
            return self.close(False)
        pos = vis.index(self.idx)
        if pos + 1 >= len(vis):
            return self.close(True)
        self._enter(vis[pos + 1])

    def back(self):
        vis = self.visible()
        pos = vis.index(self.idx) if self.idx in vis else 0
        if pos > 0:
            self._enter(vis[pos - 1])

    # ---- completion checks -------------------------------------------------------------
    def evaluate(self, st: Step) -> tuple[bool, str, str]:
        """-> (done, level, message)"""
        win = self.win
        k = st.kind
        if k in ("info", "finish"):
            return True, "ok", ""
        if k == "branch":
            return (st.key in self.ctx), ("ok" if st.key in self.ctx else "wait"), ("" if st.key in self.ctx else "Choose a path to continue.")
        if k == "confirm":
            done = self.idx in self.confirmed
            return done, ("ok" if done else "wait"), ("Great." if done else "Do it, then press “I did this ✓”.")
        if k == "add_node":
            if st.key in self.claimed and self._node(st.key) is not None:
                return True, "ok", "Node placed."
            free = [nid for nid, n in win.graph.nodes.items() if n.type_id == st.type_id and nid not in self.claimed.values()]
            if free:
                self.claimed[st.key] = free[0]
                return True, "ok", "Node placed."
            from ...core.model import get_spec
            spec = get_spec(st.type_id)
            return False, "wait", f"Waiting for you to add **{spec.title if spec else st.type_id}** — drag it from the highlighted palette entry onto the canvas (double-click works too)."
        node = self._node(st.key) if st.key else None
        if k == "select":
            if node is None:
                return False, "wait", "That node is not on the canvas yet."
            item = win.scene.node_items.get(node.id)
            ok = bool(item is not None and item.isSelected())
            return ok, ("ok" if ok else "wait"), ("Selected." if ok else "Click the highlighted node on the canvas to select it.")
        if k == "set_prop":
            if node is None:
                return False, "wait", "The node this step edits is missing — go back and add it."
            cur = node.props.get(st.prop)
            default = node.spec.prop(st.prop).default if node.spec and node.spec.prop(st.prop) else None
            changed = cur != default or st.allow_default
            err = st.check(cur, node, self) if st.check else None
            if err and changed:
                return False, "error", err
            if err or not changed:
                return False, "wait", (st.hint or "Enter the value in the highlighted field.")
            return True, "ok", "That works."
        if k == "connect":
            a, b = self._node(st.src[0]), self._node(st.dst[0])
            if a is None or b is None:
                return False, "wait", "Both nodes need to be on the canvas first."
            ok = any(e.src == a.id and e.src_port == st.src[1] and e.dst == b.id and e.dst_port == st.dst[1] for e in win.graph.edges)
            return ok, ("ok" if ok else "wait"), ("Connected." if ok else
                    f"Drag from the **{st.src[1]}** port of the first highlighted node to the **{st.dst[1]}** port of the second.")
        if k == "validate":
            res = win.build_result
            if res is None:
                return False, "wait", "The project has not been built yet — edit something or press F5."
            if not res.ok:
                return False, "error", "Build errors: " + "  ·  ".join(res.errors[:3])
            extra = st.check(win, self) if st.check else None
            if extra and extra is not True:
                return False, "warn", str(extra)
            return True, "ok", "The project builds without errors." + (f" ({len(res.warnings)} warning(s) — read them under Problems.)" if res.warnings else "")
        if k == "action":
            r = st.check(win, self) if st.check else None
            ok = r is True or r is None and False
            return bool(r is True), ("ok" if r is True else "wait"), ("Done." if r is True else (str(r) if r else (st.hint or "Press the button, then continue.")))
        return True, "ok", ""

    def _poll(self):
        if self._closing:
            return
        st = self.step()
        done, level, msg = self.evaluate(st)
        self.coach.set_status(level, msg + (("\n" + st.hint) if st.hint and level == "wait" and msg and st.kind != "set_prop" else ""))
        blocking = st.kind not in ("info", "finish")
        self.coach.next.setEnabled(done or True)
        self.coach.next.setText("Next ▶" if done and st.kind != "finish" else
                                ("Finish ✓" if self._is_last() else ("Skip ▶" if blocking and not done else "Next ▶")))
        self.coach.next.setObjectName("primary" if done else "")
        self.coach.next.style().unpolish(self.coach.next)
        self.coach.next.style().polish(self.coach.next)
        if st.kind in ("set_prop", "select", "add_node", "connect") and not self._focused_once(st):
            self._focus_step(st)
        self._highlight(st)
        if done and st.kind in AUTO_KINDS and self._entered == self.idx and not getattr(self, "_auto_pending", False):
            self._auto_pending = True
            at = self.idx
            QTimer.singleShot(900, lambda: self._auto_next(at))

    def _is_last(self) -> bool:
        vis = self.visible()
        return bool(vis) and self.idx == vis[-1]

    def _auto_next(self, at: int):
        self._auto_pending = False
        if not self._closing and self.idx == at and self.evaluate(self.step())[0]:
            self.next()

    _focus_done: int = -1

    def _focused_once(self, st: Step) -> bool:
        return self._focus_done == self.idx

    # ---- highlighting ------------------------------------------------------------------
    def _ensure_dock(self, w):
        p = w
        while p is not None and p is not self.win:
            if p.__class__.__name__ == "QDockWidget":
                p.show()
                p.raise_()
            if isinstance(p.parent(), QTabWidget):
                p.parent().setCurrentWidget(p)
            p = p.parent()

    def _focus_step(self, st: Step):
        """One-off UI preparation when a step is entered: show the right panel, select the right node, scroll to the item."""
        self._focus_done = self.idx
        win = self.win
        if st.kind == "add_node":
            win.palette.search.clear()
            self._ensure_dock(win.palette)
            it = self._palette_item(st.type_id)
            if it is not None:
                win.palette.tree.scrollToItem(it)
                if it.parent() is not None:
                    it.parent().setExpanded(True)
                win.palette.tree.setCurrentItem(it)
        node = self._node(st.key) if st.key else None
        if st.kind in ("select", "set_prop") and node is not None:
            item = win.scene.node_items.get(node.id)
            if item is not None:
                win.view.centerOn(item)
                if st.kind == "set_prop" and not item.isSelected():
                    win.scene.clearSelection()
                    item.setSelected(True)
        if st.kind == "connect":
            for key in (st.src[0], st.dst[0]):
                n = self._node(key)
                if n is not None and n.id in win.scene.node_items:
                    win.view.centerOn(win.scene.node_items[n.id])
        if st.kind == "set_prop":
            self._ensure_dock(win.props)
        if st.target in ("props", "right"):
            self._ensure_dock(win.props)
        if st.target in ("bottom", "problems", "code"):
            self._ensure_dock(getattr(win, "d_bottom", win))

    def _palette_item(self, type_id: str):
        tree = self.win.palette.tree
        stack = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it.data(0, Qt.ItemDataRole.UserRole) == type_id:
                return it
            stack.extend(it.child(i) for i in range(it.childCount()))
        return None

    def _rect_of(self, w: QWidget, r: Optional[QRect] = None) -> Optional[QRect]:
        if w is None or not w.isVisible():
            return None
        r = r if r is not None else w.rect()
        tl = w.mapTo(self.win, r.topLeft())
        return QRect(tl, r.size())

    def _node_rect(self, key: str) -> Optional[QRect]:
        node = self._node(key)
        item = self.win.scene.node_items.get(node.id) if node else None
        if item is None:
            return None
        view = self.win.view
        r = view.mapFromScene(item.sceneBoundingRect()).boundingRect().intersected(view.viewport().rect())
        return self._rect_of(view.viewport(), r) if not r.isNull() else None

    def _targets(self, st: Step) -> list[QRect]:
        win, out = self.win, []
        t = st.target
        if st.kind == "add_node":
            it = self._palette_item(st.type_id)
            if it is not None:
                tree = win.palette.tree
                vr = tree.visualItemRect(it).intersected(tree.viewport().rect())
                if not vr.isNull():
                    out.append(self._rect_of(tree.viewport(), vr))
            if not out:
                out.append(self._rect_of(win.palette.search))
        elif st.kind in ("select",):
            out.append(self._node_rect(st.key))
        elif st.kind == "set_prop":
            w = getattr(win.props, "_pw", {}).get(st.prop)
            if w is not None and w.isVisible():
                for a in _ancestors(w):
                    if isinstance(a, QAbstractScrollArea) and a is not w:
                        a.ensureWidgetVisible(w) if hasattr(a, "ensureWidgetVisible") else None
                        break
                out.append(self._rect_of(w))
            out.append(self._node_rect(st.key))
        elif st.kind == "connect":
            out += [self._node_rect(st.src[0]), self._node_rect(st.dst[0])]
        elif st.kind == "validate":
            out.append(self._rect_of(getattr(win, "d_bottom", None)))
        elif t:
            m = {"palette": win.palette, "props": win.props, "canvas": win.view, "bottom": getattr(win, "d_bottom", None),
                 "right": getattr(win, "d_right", None), "manager": getattr(win, "manager", None), "problems": getattr(win, "d_bottom", None),
                 "code": getattr(win, "d_bottom", None)}
            if t in m:
                out.append(self._rect_of(m[t]))
            elif t.startswith("node:"):
                out.append(self._node_rect(t[5:]))
            elif t.startswith("type:"):
                it = self._palette_item(t[5:])
                if it is not None:
                    tree = win.palette.tree
                    out.append(self._rect_of(tree.viewport(), tree.visualItemRect(it)))
        return [r for r in out if r is not None]

    def _highlight(self, st: Step):
        rects = self._targets(st)
        self.overlay.set_rects(rects)
        self._rects = rects
        self.overlay.raise_()
        self.coach.raise_()

    def _place(self):
        w, c = self.win, self.coach
        c.adjustSize()
        c.setFixedHeight(min(c.sizeHint().height(), int(w.height() * 0.82)))
        m = 14
        spots = [QPoint(w.width() - c.width() - m, w.height() - c.height() - m - 24), QPoint(m + 250, w.height() - c.height() - m - 24),
                 QPoint(w.width() - c.width() - m, 40), QPoint(m + 250, 40)]
        rects = getattr(self, "_rects", [])
        for p in spots:
            r = QRect(p, c.size())
            if not any(r.intersects(h.adjusted(-8, -8, 8, 8)) for h in rects):
                c.move(p)
                return
        c.move(spots[0])


def _ancestors(w: QWidget):
    p = w.parentWidget()
    while p is not None:
        yield p
        p = p.parentWidget()
