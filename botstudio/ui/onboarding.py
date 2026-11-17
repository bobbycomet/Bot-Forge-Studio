"""Guided tour (with UI highlights), Bot Wizard, and Python learning track."""
from __future__ import annotations

from PyQt6.QtCore import QRect, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QPainter
from PyQt6.QtWidgets import (QTreeWidget, QTreeWidgetItem, 
    QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
    QListWidget, QMessageBox, QPushButton, QSplitter, QTextBrowser,
    QVBoxLayout, QWidget, QInputDialog,
)

TOUR_STEPS = [
    ("Welcome to Bot Forge Studio",
     "Bot Forge Studio is a **node-based IDE** for Discord bots.\n\n"
     "You draw a graph; Studio compiles it to real **discord.py** you can read and deploy.\n\n"
     "This tour highlights each area of the UI. Click **Next** to continue.",
     None),
    ("The canvas",
     "This is the graph editor.\n\n"
     "• **Left-drag empty space** — pan\n"
     "• **Shift+left-drag** — select several nodes\n"
     "• **Scroll wheel** — zoom\n"
     "• **Right-click** — add nodes or group selection\n"
     "• **Ctrl+G** / **Ctrl+Shift+G** — group / ungroup\n\n"
     "Click a group's **header** to collapse or expand it.",
     "view"),
    ("Node palette",
     "The **Nodes** panel lists every built-in and plugin node.\n\n"
     "Double-click or **drag** a node onto the canvas.\n"
     "Search filters by name or description.",
     "d_palette"),
    ("Properties & Inspector",
     "Select a node to edit its **Properties** here.\n\n"
     "The **Bot** tab manages deploy target, start/stop, and logs.\n\n"
     "If a panel disappears: **View → Show all panels**.",
     "d_right"),
    ("Generated code & problems",
     "After **Build (F5)**, open **Generated code** to read the real Python Studio emitted.\n\n"
     "**Problems** lists validation errors. Click a problem to focus that node.\n"
     "**Output** shows deploy/plugin messages.",
     "d_bottom"),
    ("Wires: exec vs data",
     "• **Exec wires** (chevrons) — run order: Trigger → Action → …\n"
     "• **Data wires** — plug values into ports (text, users, numbers)\n\n"
     "Triggers (red) start a flow. Actions (blue) do work. "
     "Flow (orange) branches. Data (green) supplies values.",
     "view"),
    ("Bot Wizard & Examples",
     "Use **Help → Bot Wizard…** (or File → Examples) to start from a working graph.\n\n"
     "We open the Wizard when this tour finishes.",
     None),
    ("Plugins",
     "**File → Install module folder…** adds multi-file packs (`module.json` + code).\n"
     "**File → Install plugin…** / drag-drop adds `.bsmod` or `.py` graph nodes.\n\n"
     "Official & community plugins: **Help → Browse plugins…**",
     None),
    ("Custom Python & learning",
     "Need logic with no node yet? Use **Custom Python** and **Open in editor…** in Properties.\n\n"
     "**Help → Learn Python & nodes…** — lessons from zero to writing your own bot and custom nodes.",
     None),
    ("Deploy",
     "**F5** Build → **F6** Deploy → **Bot** menu to start/stop.\n"
     "**F7** Live sync pushes cog changes without a full redeploy.\n\n"
     "You are ready — **Next** opens the **Bot Wizard**.",
     None),
]


class _HighlightOverlay(QWidget):
    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._hole = QRect()
        self.hide()

    def set_hole_widget(self, w: QWidget | None):
        if w is None or not w.isVisible():
            self._hole = QRect()
        else:
            top = self.parentWidget()
            br = w.rect()
            tl = w.mapTo(top, br.topLeft())
            self._hole = QRect(tl, br.size()).adjusted(-6, -6, 6, 6)
        self.update()

    def paintEvent(self, e):
        # Draw four dim rectangles around the hole — CompositionMode_Clear is
        # unreliable on many Linux/AppImage OpenGL/software backends (shows solid black).
        p = QPainter(self)
        p.setPen(Qt.PenStyle.NoPen)
        dim = QColor(0, 0, 0, 150)
        r = self.rect()
        h = self._hole
        if h.isNull():
            p.fillRect(r, dim)
            return
        # top, bottom, left, right strips
        p.fillRect(QRect(r.left(), r.top(), r.width(), max(0, h.top() - r.top())), dim)
        p.fillRect(QRect(r.left(), h.bottom(), r.width(), max(0, r.bottom() - h.bottom())), dim)
        p.fillRect(QRect(r.left(), h.top(), max(0, h.left() - r.left()), h.height()), dim)
        p.fillRect(QRect(h.right(), h.top(), max(0, r.right() - h.right()), h.height()), dim)
        p.setPen(QColor(80, 180, 255))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(h.adjusted(0, 0, -1, -1))


class TourDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Bot Forge Studio tour")
        self.resize(520, 400)
        self.setModal(True)
        self._i = 0
        self._overlay = None
        if parent is not None:
            self._overlay = _HighlightOverlay(parent)
            self._overlay.setGeometry(parent.rect())
            self._overlay.show()
            self._overlay.raise_()

        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(True)
        self.lbl = QLabel()
        prev = QPushButton("Back")
        self.next_btn = QPushButton("Next")
        close = QPushButton("Close")
        prev.clicked.connect(self._back)
        self.next_btn.clicked.connect(self._next)
        close.clicked.connect(self._finish)
        nav = QHBoxLayout()
        nav.addWidget(prev)
        nav.addStretch()
        nav.addWidget(self.next_btn)
        nav.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.lbl)
        lay.addWidget(self.body, 1)
        lay.addLayout(nav)
        self.finished.connect(self._cleanup)
        self._show()

    def _target(self, key: str | None) -> QWidget | None:
        win = self.parent()
        if not win or not key:
            return None
        return getattr(win, key, None)

    def _show(self):
        title, md, key = TOUR_STEPS[self._i]
        self.lbl.setText(f"<b>Step {self._i + 1}/{len(TOUR_STEPS)} — {title}</b>")
        self.body.setMarkdown(md)
        self.next_btn.setText("Finish & open Bot Wizard" if self._i == len(TOUR_STEPS) - 1 else "Next")
        if self._overlay:
            if self.parent():
                self._overlay.setGeometry(self.parent().rect())
            self._overlay.set_hole_widget(self._target(key))
            self._overlay.show()
            self._overlay.raise_()
            self.raise_()

    def _back(self):
        self._i = max(0, self._i - 1)
        self._show()

    def _next(self):
        if self._i >= len(TOUR_STEPS) - 1:
            self._finish(open_wizard=True)
            return
        self._i += 1
        self._show()

    def _finish(self, open_wizard: bool = False):
        self._cleanup()
        self.accept()
        if open_wizard and self.parent() is not None:
            QTimer.singleShot(100, lambda: getattr(self.parent(), "open_bot_wizard", lambda: None)())

    def _cleanup(self, *_):
        if self._overlay is not None:
            self._overlay.hide()
            self._overlay.deleteLater()
            self._overlay = None

    def reject(self):
        self._cleanup()
        super().reject()


BASICS = [
    ("Hello Bot", "hello", "One slash command that greets the user."),
    ("Coin Flip", "coinflip", "Random choice + formatted reply — data wiring."),
    ("Mod Tools", "mod_timeout", "Permission gate + timeout + replies."),
    ("Use Counter", "command_counter", "Storage: count how often each member used a command."),
    ("Birthday Bit", "birthday_bit", "Birthdays & anniversaries module plus a guide and a dropdown."),
    ("Signup Form", "signup_form", "Modal form + confirm dialog + reply."),
    ("Server Guide", "server_guide", "Tabbed menu, ticket panel and a button handler."),
    ("Economy Starter", "daily_reward", "/daily, /balance and a richest board."),
    ("Event Planner", "event_planner", "Spawn/drop timers, countdowns, guild events, task lists."),
    ("Emote Library", "emote_bot", "Image emotes with a visual browser."),
    ("Post Router", "router_bot", "Route followed / forwarded posts to channels, forums, threads."),
    ("Clan Roster", "clan_bot", "Game profiles + level-bracket role lists."),
    ("Faction Wars", "faction_wars", "Factions, scoring, bosses, arena, quests and cards."),
    ("Bot + web dashboard", "dashboard", "A discord.py bot and its discord.js dashboard from one graph."),
]
LEVEL_ICON = {"Beginner": "🟢 beginner", "Intermediate": "🟡 intermediate", "Advanced": "🔴 advanced"}


class BotWizardDialog(QDialog):
    """Ready-to-run example bots: pick one, read how it is built, load it, inspect it, run it, change it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from ..core import example_bots as eb
        self.eb = eb
        self.setWindowTitle("Bot Wizard — ready-to-run examples")
        self.resize(1000, 700)
        self.choice = None
        self.start_lesson = None
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMaximumWidth(340)
        for label, cat in (("Simple bots — start here", "simple"), ("Advanced bots — modular, multi-system", "advanced")):
            top = QTreeWidgetItem([label])
            top.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.tree.addTopLevelItem(top)
            for info in eb.EXAMPLE_INFO:
                if info["category"] == cat:
                    it = QTreeWidgetItem([f"{info['title']}   ·   {LEVEL_ICON[info['difficulty']]}"])
                    it.setData(0, Qt.ItemDataRole.UserRole, info["fn"])
                    top.addChild(it)
            top.setExpanded(True)
        basics = QTreeWidgetItem(["Tiny starters & feature demos"])
        basics.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.tree.addTopLevelItem(basics)
        for title, key, summary in BASICS:
            it = QTreeWidgetItem([title])
            it.setData(0, Qt.ItemDataRole.UserRole, key)
            it.setToolTip(0, summary)
            basics.addChild(it)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(True)
        self.load_btn = QPushButton("Load this example")
        self.load_btn.setObjectName("primary")
        self.lesson_btn = QPushButton("Load it and start its guided lesson")
        self.portal_btn = QPushButton("Open Discord Developer Portal")
        close = QPushButton("Close")
        self.tour_check = QCheckBox("Start the Studio tour afterwards")
        self.load_btn.clicked.connect(lambda: self._ok(False))
        self.lesson_btn.clicked.connect(lambda: self._ok(True))
        self.portal_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://discord.com/developers/applications")))
        close.clicked.connect(self.reject)
        top_row = QHBoxLayout()
        top_row.addWidget(self.tree)
        top_row.addWidget(self.detail, 1)
        row = QHBoxLayout()
        row.addWidget(self.tour_check)
        row.addStretch()
        for b in (self.portal_btn, self.lesson_btn, self.load_btn, close):
            row.addWidget(b)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Pick an example, read how it is built, then load it: every node and property is yours to inspect, run and change."))
        lay.addLayout(top_row, 1)
        lay.addLayout(row)
        self.tree.currentItemChanged.connect(lambda cur, _prev: self._show(cur))
        self.tree.setCurrentItem(self.tree.topLevelItem(0).child(0))

    def _fn(self):
        it = self.tree.currentItem()
        return it.data(0, Qt.ItemDataRole.UserRole) if it else None

    def _show(self, item):
        fn = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        info = self.eb.INFO_BY_FN.get(fn)
        self.load_btn.setEnabled(bool(fn))
        from .lessons.content import LESSONS
        ids = {l.id for l in LESSONS}
        self.lesson_btn.setVisible(bool(info and info.get("lesson") in ids))
        if not fn:
            self.detail.setMarkdown("Choose an example on the left.")
            return
        from ..core import examples, project
        from ..core.requirements import needs_markdown
        builder = examples.resolve(fn)
        try:
            g = builder()
            res = project.build(g)
            kinds = {}
            for n in g.nodes.values():
                kinds[n.spec.category if n.spec else "?"] = kinds.get(n.spec.category if n.spec else "?", 0) + 1
            stats = f"**{len(g.nodes)} nodes** · " + " · ".join(f"{v} {k}" for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:5])
            mods = sorted({n.spec.title for n in g.nodes.values() if n.spec and n.spec.kind == "module"})
            needs = needs_markdown(g, res)
            build_ok = "builds cleanly" if res.ok else "has errors: " + "; ".join(res.errors[:2])
            reqs = sorted(res.requirements)
        except Exception as e:                      # a broken example must not break the wizard
            stats, mods, needs, build_ok, reqs = "", [], "(could not analyse)", f"could not be built: {e}", []
        if info is None:
            summary = next((s for t, k, s in BASICS if k == fn), "")
            self.detail.setMarkdown(f"## {item.text(0)}\n\n{summary}\n\n{stats}\n\nThe project {build_ok}.\n\n### Intents & permissions\n{needs}\n\n---\n{self.eb.SETUP_COMMON}")
            return
        feats = "\n".join(f"| {t} | {LEVEL_ICON[lv]} |" for t, lv in info["features"])
        md = (f"## {info['title']}\n\n**{LEVEL_ICON[info['difficulty']].title()}** · {info['category']} example\n\n{info['summary']}\n\n{stats}"
              + (f"\n\n**Modules used:** {', '.join(mods)}" if mods else "") + f" · the project {build_ok}.\n\n"
              f"### How it is built\n{info['architecture']}\n\n### Features\n| Feature | Level |\n|---|---|\n{feats}\n\n"
              f"### Before you run it\n{info['extra_setup']}\n\n### Intents & permissions it needs\n{needs}\n\n"
              + (f"**Dependencies installed on deploy:** {', '.join(reqs)}\n\n" if reqs else "")
              + "### Run it\n1. **Load** the example and click through its nodes (Properties show every setting).\n2. Follow the credentials steps below.\n"
                "3. **F5** builds, **F6** deploys, then **Start**. Change anything and rebuild — it is your project now.\n\n---\n" + self.eb.SETUP_COMMON)
        self.detail.setMarkdown(md)

    def _ok(self, with_lesson: bool):
        fn = self._fn()
        if not fn:
            return
        self.choice = fn
        info = self.eb.INFO_BY_FN.get(fn)
        self.start_lesson = info.get("lesson") if (with_lesson and info) else None
        self.accept()


def _L(id, track, title, body, quiz, example=None):
    return {"id": id, "track": track, "title": title, "body": body, "quiz": quiz, "example": example}

LESSONS = [
    _L('py01', 'Python basics', '1. What Bot Forge Studio generates and why it matters',
       """## Purpose of this curriculum

This learning track is intentionally long. The goal is that after working through it you can read every line Studio emits, write discord.py by hand, ship moderation and economy features safely, deploy with services, and extend Studio with plugins.

## What Build produces

Studio walks the graph component by component and writes:

- `bot.py` — intents, Bot subclass, error handler, entrypoint
- `bs_runtime.py` — shared helpers (`respond`, `embed`, `kv_*`, moderation wrappers, live control cog)
- `cogs/*.py` — one cog per connected component of nodes
- `requirements.txt`, `manifest.json`, `.env.example`
- optional service unit scripts from the deploy target settings

## Exec versus data

Exec wires order side effects. Data wires plug expressions into those effects. Confusing them is the most common beginner mistake.

## Study method used throughout

For every lesson: build a tiny graph, press F5, open Generated code, and explain the cog out loud. If you cannot explain a line, the lesson is not finished.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       [{'q': 'Primary emitted language?', 'choices': ['JSON only', 'Python 3 + discord.py', 'Java'], 'answer': 1}]),
    _L('py02', 'Python basics', '2. Variables, types, naming, conversion',
       """## Core types in bot work

Strings, ints (including snowflakes), floats, bools, None, lists, dicts, and discord.py model objects (Member, Message, Interaction).

## Naming

Use snake_case. Avoid reserved slash parameter names Studio blocks (`user`, `interaction`, `guild`, …). Prefer explicit names: `target_member` over `x`.

## Conversion

`int`, `str`, `float` raise on bad input. Validate at the edge of the handler and return ephemeral errors. Large Discord IDs must remain exact integers — Studio’s literal helper prefers int for digit strings.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       [{'q': 'Bad int conversion raises…', 'choices': ['ValueError', 'SystemExit', 'Nothing'], 'answer': 0}]),
    _L('py03', 'Python basics', '3. Strings, formatting, Discord length limits',
       """## f-strings in hand-written code

Prefer f-strings when you write Python yourself. Studio Format nodes intentionally use safer concatenation of scoped attributes rather than free-form eval.

## Methods

lower, upper, strip, split, join, replace, startswith, endswith, `in` for substring checks.

## Limits

Message content ~2000, embed description 4096, title 256, field value 1024, max 25 fields. Design copy to fit; do not rely on silent truncation for UX.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('py04', 'Python basics', '4. Lists, dicts, iteration',
       """## Lists and dicts

Append, index, len, for-loops, `.get` with defaults, `.items()`.

## Comprehensions

Powerful but easy to overuse in hot message handlers. Keep per-message work small.

## Graph boundary

Heavy loops belong in Custom Python or modules, not twenty data nodes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('py05', 'Python basics', '5. Functions, scope, Custom Python inlining',
       """## Functions

Define helpers with clear signatures. Custom Python blocks are inlined into handlers — defining a nested `def` inside the block is fine; expecting a separate module automatically is not.

## Early return

Validate → ephemeral error → `return` is the standard pattern so later moderation actions do not run.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       [{'q': 'Custom Python default behavior?', 'choices': ['New file always', 'Inline into handler', 'Import-time only'], 'answer': 1}]),
    _L('py06', 'Python basics', '6. Booleans and branching',
       """## Truthiness

Empty containers and zero are falsey. The string `"0"` is truthy — parse explicitly.

## Studio flow

Branch, Has Permission, Has Role, Is Bot, Is Owner, Text Matches, Chance, Stop. Combine with data Compare / Logic / Pick Value.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('py07', 'Python basics', '7. Exceptions and user-visible errors',
       """## try/except

Catch ValueError for parsing, Forbidden for permissions, HTTPException for API failures. Log unexpected errors; tell users what to do next without leaking internals.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       [{'q': 'Uncaught handler exceptions…', 'choices': ['Always DM the owner', 'Often look like silent or generic failures to users', 'Upgrade discord.py'], 'answer': 1}]),
    _L('as01', 'async & discord.py', '8. Event loop discipline',
       """## Rule

Never block the loop. Use await asyncio.sleep, await network calls, await discord.py methods. time.sleep and synchronous requests are bugs in bot code.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       [{'q': 'time.sleep in a command…', 'choices': ['Fine', 'Blocks the bot', 'Only blocks one guild'], 'answer': 1}]),
    _L('as02', 'async & discord.py', '9. Interactions deeply',
       """## Respond or defer within ~3 seconds

bs.respond routes to response or followup. Defer on Slash node for slow work. Ephemeral for private feedback. interaction.guild may be None in DMs.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('as03', 'async & discord.py', '10. Messages, embeds, files',
       """## Sending

channel.send, message.reply, bs.send with channel id. Embeds via discord.Embed or bs.embed. Files via discord.File. Handle Forbidden when the bot cannot speak in-channel.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('as04', 'async & discord.py', '11. Permissions and hierarchy',
       """## Checks

guild_permissions flags, role membership, ownership. Bots cannot moderate equals/above in role hierarchy. Least privilege for the bot role in production.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       [{'q': 'First step for /ban design?', 'choices': ['Skip checks', 'Permission gate', 'Hardcode Administrator'], 'answer': 1}]),
    _L('as05', 'async & discord.py', '12. Cogs, listeners, intents',
       """## Structure

Cog classes, setup(bot), listeners, app commands. Privileged intents must be enabled in the portal and in code. Studio merges intents from nodes and settings.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('bs01', 'Bot Forge Studio deep', '13. Ports, properties, dynamic ports',
       """## Wiring rules

Exec to exec, data to data. Wired data overrides properties. Slash params and Format placeholders create ports dynamically; pruning edges after edits is expected.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       [{'q': 'Wired data port…', 'choices': ['Ignored', 'Overrides property', 'Crashes build'], 'answer': 1}]),
    _L('bs02', 'Bot Forge Studio deep', '14. Codegen reading protocol',
       """## Protocol

One change → Build → read cog → match # [id] comments. Empty branches may be pass. Orphans warn and skip. Modules are separate files.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('bs03', 'Bot Forge Studio deep', '15. Custom Python craft',
       """## Patterns

Validate; return; use bs.kv_*; avoid sleep/run/input. Optional hasattr guards for plugin helpers. Prefer short blocks and real modules for systems.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('bs04', 'Bot Forge Studio deep', '16. Modules, bsmod, graph_nodes',
       """## Packs

module.json + entry + optional graph_nodes.py. %%KEY%% literal fill. Standalone packs replace the whole bot process. License GPLv3 or MIT for community distribution.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('md01', 'Moderation & patterns', '17. Timeout command blueprint',
       """## Blueprint

Slash(target, minutes, reason?) → Has Permission moderate_members → Timeout → ephemeral success. False path: deny. Put invoker in reason. Check ok for hierarchy failures.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('md02', 'Moderation & patterns', '18. Warns, purge, slowmode, lock',
       """## Tools

Record Warning increments KV. Purge caps at 100. Slowmode 0–21600. Lock toggles @everyone send_messages. Always still need Discord perms on the bot role.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       [{'q': 'Purge node max messages?', 'choices': ['10', '100', '5000'], 'answer': 1}]),
    _L('md03', 'Moderation & patterns', '19. Nicknames, untimeout, gates',
       """## Nodes

Set Nickname, Remove Timeout, Is Bot, Is Owner, In These Channels, Text Matches. Use owner checks sparingly; prefer role/permission systems for staff teams.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('st01', 'Storage & data', '20. KV store architecture',
       """## Design

Namespaces + keys including guild id. kv_get/set/incr/top. SQLite under data/. Prefer incr for counters. Delete Value clears keys.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('st02', 'Storage & data', '21. Data toolbox',
       """## Nodes

String Tools, Join Text, First Non-Empty, Pick Value, Clamp, Snowflake→Unix Time, Compare, Logic, Math, Random, Format, Env, Now.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('op01', 'Deploy & production', '22. Secrets and env',
       """## Practice

Token only in target .env. Optional GUILD_ID for fast slash sync while developing. Never commit secrets. Rotate on leak.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       [{'q': 'Production token storage?', 'choices': ['Git', '.env / secret store', 'Graph JSON'], 'answer': 1}]),
    _L('op02', 'Deploy & production', '23. Deploy and live sync',
       """## Ops

F6 full install. F7 delta push + hot reload for cogs. Restart when restart_sig changes. Status.json for health. Fetch logs from Bot panel.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('op03', 'Deploy & production', '24. Service managers',
       """## Targets

systemd system/user, runit, openrc, windows NSSM/task, process mode. SSH key auth for remote Linux. Dedicated run_user on servers.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex01', 'Expert craft', '25. Hand-written minimal bot',
       """## Baseline

Write a minimal intents + tree.command + bot.run(os.environ[...]) bot from memory. Everything Studio adds should remain understandable relative to this baseline.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex02', 'Expert craft', '26. Custom node plugin',
       """## register_nodes

Ship a .py plugin that registers an action node calling bs.respond. Reload plugins, wire it, read codegen. Treat plugins as code execution.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex03', 'Expert craft', '27. Module pack delivery',
       """## Delivery

module.json, cog, helpers, graph_nodes. Install folder. Verify commands_sig resync. Document props for operators.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex04', 'Expert craft', '28. Production checklist',
       """## Checklist

Design, prototype, read codegen, extract modules, test on dev guild, deploy, document, license, iterate with live sync. Know hierarchy vs intent vs perm failures apart.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex05', 'Expert craft', '29. Rate limits and scale',
       """## Scale

Avoid REST storms. Cache thoughtfully. Studio default is single process — plan sharding as a deliberate export evolution when needed.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex06', 'Expert craft', '30. Security threat model',
       """## Trust

Graphs, Custom Python, and plugins run with full bot authority. Review third-party packs. Least privilege roles. No eval of user text.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex31', 'Expert craft', '31. 31. Slash versus prefix',
       """## 31. Slash versus prefix

Slash is typed and discoverable; prefix needs message content and parsing. Prefer slash for public commands.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex32', 'Expert craft', '32. 32. Deferral strategy',
       """## 32. Deferral strategy

Defer when work exceeds three seconds. Always follow up. Ephemeral defer for private pipelines.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex33', 'Expert craft', '33. 33. Application emojis',
       """## 33. Application emojis

Upload constraints: size, mime, name charset. List/remove nodes for management commands.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex34', 'Expert craft', '34. 34. HTTP integration',
       """## 34. HTTP integration

http_get returns status/text/json. Handle failures. Do not block. Respect third-party API ToS.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex35', 'Expert craft', '35. 35. Schedulers',
       """## 35. Schedulers

tasks.loop with before_loop wait_until_ready; cancel on unload; keep work light.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex36', 'Expert craft', '36. 36. Reactions',
       """## 36. Reactions

Raw events for uncached messages; ignore self reactions; careful emoji string forms.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex37', 'Expert craft', '37. 37. Welcomes',
       """## 37. Welcomes

members intent; env channel id; add role with hierarchy awareness.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex38', 'Expert craft', '38. 38. Format node mastery',
       """## 38. Format node mastery

Placeholders become ports; scope variables expand; keep templates short and clear.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex39', 'Expert craft', '39. 39. Boolean networks',
       """## 39. Boolean networks

Compare and Logic nodes before Branch; readable for collaborators.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex40', 'Expert craft', '40. 40. Random nodes',
       """## 40. Random nodes

Cached evaluation semantics; one roll per handler evaluation scope.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex41', 'Expert craft', '41. 41. Config via env',
       """## 41. Config via env

Feature flags and channel IDs in .env so the same graph deploys to staging/prod differently.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex42', 'Expert craft', '42. 42. Canvas grouping',
       """## 42. Canvas grouping

Groups organize; collapse headers; codegen still follows wires not group boxes.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex43', 'Expert craft', '43. 43. Problems workflow',
       """## 43. Problems workflow

Fix errors first; treat warnings as tasks; click through to nodes.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex44', 'Expert craft', '44. 44. Export vs deploy',
       """## 44. Export vs deploy

Export for git/review; deploy for service install on machines you control.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex45', 'Expert craft', '45. 45. Hot reload limits',
       """## 45. Hot reload limits

Cogs reload; core runtime/bot.py changes need restart; watch status flags.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex46', 'Expert craft', '46. 46. Test matrix',
       """## 46. Test matrix

Missing user perm, missing bot perm, hierarchy fail, happy path, bad input.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex47', 'Expert craft', '47. 47. Logging practice',
       """## 47. Logging practice

Levels, rotation, no secrets, actionable messages.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex48', 'Expert craft', '48. 48. Bot UX writing',
       """## 48. Bot UX writing

Short, ephemeral errors, next steps, less jargon.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex49', 'Expert craft', '49. 49. Leaving the graph',
       """## 49. Leaving the graph

Export and grow into a full service architecture when requirements demand it.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
    _L('ex50', 'Expert craft', '50. 50. Capstone projects',
       """## 50. Capstone projects

Warn escalator; staff lock flow; XP rank; plugin calling an external game API — each with README and dev guild demo.

### Required practice

Implement a minimal demonstration on the canvas (or in Custom Python if more appropriate). Build, read the cog, and write three sentences in your own words describing failure modes.

---

### Deep dive and common pitfalls

Read the Generated code after every change. Prefer small graphs that prove one idea. When something fails in Discord, distinguish: (1) Studio build errors, (2) missing intents, (3) missing bot permissions, (4) role hierarchy, (5) logic bugs in your graph. Those five buckets solve most "it does nothing" reports.

When you paste snippets into Custom Python, remember the block is async-capable already if the handler is async — use `await` for discord.py and `bs.*` helpers. Do not nest `asyncio.run`. Do not start new event loops.

As you advance, maintain a personal library of working subgraphs (permission-gated mod actions, ephemeral error patterns, KV counters). Reuse them instead of reinventing wiring under pressure.
""",
       []),
]




BUILD_LESSONS = [
    _L('bd01', 'Build a bot', 'Birthday Bit — what to choose and why',
       """## What you are building

**Birthday Bit** announces birthdays and "community anniversaries" (how long someone has been in your server) with an embed, an optional GIF, a custom message and a **temporary role** that disappears the next day.

Press **Load this example** below. You get two things on the canvas:

1. **Birthdays & Anniversaries** (teal, *Community*) — the whole feature in one module node.
2. A small flow: `/party` shows a **Tabbed Menu** guide, and `/cake` uses **Ask: Dropdown** — so you also see how ordinary nodes sit beside a module.

## Step 1 — pick the module, not a pile of nodes

When a feature has its own storage, a timer and several commands, Bot Forge Studio ships it as a **Module** node. A module is a ready-made cog: you place it, and its slash commands appear in the generated bot. Nodes (red / blue / orange / green) are for the glue around it.

Rule of thumb: *needs a database or a clock → module; reacts to one command → nodes.*

## Step 2 — check intents

Select the module and read its description. Birthdays needs the **Members** intent (to hand out and remove the role). Bot Forge Studio turns it on for you in `bot.py`, but you must also switch **Server Members Intent** on in the Discord Developer Portal → Bot.

## Step 3 — name it

**Project → Settings**: set *Bot name* and a *Status text* (the example uses "🎂 /birthday set"). Never put your token in a project file; it lives in `.env` as `DISCORD_TOKEN` and is never saved with the graph.

## Step 4 — build and read the code

Press **F5**. Open **Generated code**: the module appears as `cogs/mod_birthdays_….py`, your `/party` and `/cake` flows as their own cogs. Press **Problems** to confirm there are none.

## Step 5 — deploy and configure in Discord

1. Deploy (**Bot** tab) and start the bot.
2. In your server, an admin runs, once:
   - `/birthday-config channel` — where announcements go
   - `/birthday-config role` — the temporary birthday role (keep it **below** the bot's own role)
   - `/birthday-config time` and `/birthday-config timezone` — when to announce
   - `/birthday-config message` — default wording, `{user}` is filled in
3. Members run `/birthday set 07-14` and `/anniversary set 03-02-2021`.
4. Test with `/birthday test` before the big day.

## Step 6 — make it yours

- Add a **Custom Python** node after `/cake`: it can read the dropdown's `value` and do anything Python can.
- Add **Economy → Give Currency** to hand out coins on a birthday flow.
- Add **Task Lists & Reminders** for a staff checklist.

### Common problems

- *No announcement:* channel or time not configured, wrong timezone, or the bot cannot send in that channel.
- *Role not given:* the birthday role is above the bot's highest role, or the Members intent is off.
- *Commands missing:* global commands can take a while; set a **Dev server ID** in Project settings for instant sync while testing.
""",
       [{'q': 'When should a feature be a Module node instead of plain nodes?', 'choices': ['When it has its own storage, a timer and several commands', 'Never; modules are legacy', 'Only for moderation'], 'answer': 0},
        {'q': 'Where does the bot token go?', 'choices': ['Inside the project file', 'In .env as DISCORD_TOKEN', 'In the node properties'], 'answer': 1},
        {'q': 'The birthday role is not being given. Most likely cause?', 'choices': ['The role is above the bot\'s highest role', 'The bot name is too long', 'Python is outdated'], 'answer': 0}],
       example='birthday_bit'),
    _L('bd02', 'Build a bot', 'Economy bot — wallet, daily reward, shop',
       """## Goal

A currency members earn and spend: `/daily`, `/balance`, a richest board — then a shop.

Press **Load this example** (Economy Starter) and open the flows:

- **Timed Reward (Cooldown)** lets a member through once per period and gives you `remaining` ("3h 12m") for the "come back later" branch.
- **Give Currency** adds to the wallet and outputs the new `balance`.
- **Top Balances** returns a ready-to-post ranking.

## Adding a shop

Build `/buy` as: Slash Command → **Confirm (Yes/No)** → **Spend Currency** → `paid` → **Give Item** → Reply, and `not enough` → Reply "you can't afford that". Because Spend only takes money if it can, you never need a separate balance check.

## Several currencies

Every economy node has a *Currency / wallet name*. Use `coins` for everyday play and `gems` for rare rewards — they are separate tables, and the **Web Dashboard** (optional) shows each one as a leaderboard page.

## Quests and games on top

The game modules (Quests, Gear & Rarity, Dungeons & Raids, Profile & Rank Cards) share this same wallet. Put the same wallet name in their properties and a quest reward is just another **Give Currency**.
""",
       [{'q': 'Which node takes currency only if the member can afford it?', 'choices': ['Give Currency', 'Spend Currency', 'Get Balance'], 'answer': 1}],
       example='daily_reward'),
    _L('bd03', 'Build a bot', 'Event planner — timers, countdowns and sign-ups',
       """## Goal

A guild-coordination bot: spawn and drop timers, countdowns to big dates, a quest and trade board, and events with sign-up buttons.

Load **Event Planner**. It contains two modules:

- **Event Scheduler** — every *add* command asks for a **channel** (pick it from the dropdown), then opens a **form**. Forms hold five fields at most, so time and timezone share one field, e.g. `14:30 America/New_York`.
- **Task Lists & Reminders** — `/tasklist` for checklists with timers, `/reminder` for one-off or repeating reminders.

## Things to know

- Times are stored with their own timezone, so daylight-saving changes are handled for you.
- Members set their own zone once with `/tasklist timezone`; typed dates then mean *their* local time, and Discord shows every post in each reader's own time.
- Both modules keep data under `data/` — back that folder up.
""",
       [{'q': 'Why do some forms combine time and timezone in one field?', 'choices': ['Discord modals allow at most 5 text fields', 'It is faster to type', 'Timezones are optional'], 'answer': 0}],
       example='event_planner'),
]
BUILD_LESSONS.append(_L('bd04', 'Build a bot', 'Faction wars — scoring, bosses and the arena',
       """## Goal

Two (or more) factions compete. Members join one, score points by using their faction's trigger phrases, fight community bosses together and duel each other in the arena.

Load **Faction Wars**. The pieces fit together through three things only — you never wire them by hand:

1. **The wallet** — currency from bosses, arena wagers and quest rewards lands in the same place Economy nodes use.
2. **The event bus** — every scoring hit is announced as a `hits` event; **Boss Fights** turns those into damage, **Quests** counts them.
3. **Services** — modules call each other by name (`gear.stats`, `score.scan`, `boss.start`), and quietly do without a module that is not installed.

## Set-up order in Discord

1. `/faction create` — names, emojis, a role per faction (two are created for you).
2. `/trigger add` or `/trigger bulk` — the phrases each faction scores for, with weights. Use `/trigger test` to check a sentence.
3. `/scoreconfig show|set` — message cap, phrase cooldown, daily soft cap, decay, `track` to limit scoring to certain channels.
4. `/boss create`, `/boss phases`, `/boss rewards`, then `/boss start` (or `/summon` from the example, or `/boss recurring`).
5. `/arena challenge @member 50` — Elo is kept per faction; `/arenaconfig set` tunes timers, HP and the daily winnings cap.

## Ideas

- **Faction Score** and **Get Faction Score** nodes let your own flows hand out or read points.
- **Deal Boss Damage** lets a button or command hit the boss.
- Use **Timed Reward** to give a daily bonus to the winning faction's members.
""",
       [{'q': 'How does a scoring hit end up as boss damage?', 'choices': ['Through the event bus — Boss Fights listens for "hits"', 'You must wire it with nodes', 'It cannot'], 'answer': 0},
        {'q': 'What does the daily soft cap do?', 'choices': ['Stops scoring entirely', 'Makes hits above the cap worth a fraction', 'Resets all scores'], 'answer': 1}],
       example='faction_wars'))
LESSONS += BUILD_LESSONS

class LearningDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Learn — Python, discord.py & Bot Forge Studio")
        self.resize(900, 600)
        self.setModal(True)

        self.tracks = QListWidget()
        self.lessons = QListWidget()
        self.body = QTextBrowser()
        self.body.setOpenExternalLinks(True)

        self._by_track: dict[str, list[dict]] = {}
        for les in LESSONS:
            self._by_track.setdefault(les["track"], []).append(les)
        for track in self._by_track:
            self.tracks.addItem(track)
        self.tracks.setCurrentRow(0)
        self.tracks.currentRowChanged.connect(self._fill_lessons)
        self.lessons.currentRowChanged.connect(self._show_lesson)

        quiz_btn = QPushButton("Take quiz for this lesson")
        quiz_btn.clicked.connect(self._quiz)
        next_btn = QPushButton("Next lesson")
        next_btn.clicked.connect(self._next_lesson)
        self.example_btn = QPushButton("Load this example")
        self.example_btn.setToolTip("Open the lesson's example project on the canvas and close this window")
        self.example_btn.clicked.connect(self._load_example)

        left = QVBoxLayout()
        left.addWidget(QLabel("Track"))
        left.addWidget(self.tracks, 1)
        left.addWidget(QLabel("Lesson"))
        left.addWidget(self.lessons, 2)
        left_w = QWidget()
        left_w.setLayout(left)

        right = QVBoxLayout()
        right.addWidget(self.body, 1)
        row = QHBoxLayout()
        row.addWidget(self.example_btn)
        row.addWidget(quiz_btn)
        row.addWidget(next_btn)
        right.addLayout(row)
        right_w = QWidget()
        right_w.setLayout(right)

        split = QSplitter()
        split.addWidget(left_w)
        split.addWidget(right_w)
        split.setStretchFactor(1, 3)

        tip = QLabel("Goal: leave with enough Python + discord.py to write a bot or a custom node plugin. The \"Build a bot\" track walks through complete bots and loads each as an example.")
        tip.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addWidget(tip)
        lay.addWidget(split, 1)
        self._fill_lessons(0)

    def _fill_lessons(self, row: int):
        self.lessons.clear()
        if row < 0:
            return
        track = self.tracks.item(row).text()
        for les in self._by_track.get(track, []):
            self.lessons.addItem(les["title"])
        if self.lessons.count():
            self.lessons.setCurrentRow(0)

    def _current_lesson(self) -> dict | None:
        tr = self.tracks.currentRow()
        lr = self.lessons.currentRow()
        if tr < 0 or lr < 0:
            return None
        track = self.tracks.item(tr).text()
        items = self._by_track.get(track, [])
        if lr >= len(items):
            return None
        return items[lr]

    def _show_lesson(self, row: int):
        les = self._current_lesson()
        if not les:
            self.body.setMarkdown("_Select a lesson._")
            self.example_btn.setVisible(False)
            return
        self.body.setMarkdown(les["body"])
        self.example_btn.setVisible(bool(les.get("example")))

    def _load_example(self):
        les = self._current_lesson()
        win = self.parent()
        if not les or not les.get("example") or not hasattr(win, "_load_graph"):
            return
        from ..core import examples
        fn = getattr(examples, les["example"], None)
        if callable(fn):
            win._load_graph(fn(), None)
            self.accept()

    def _next_lesson(self):
        r = self.lessons.currentRow()
        if r + 1 < self.lessons.count():
            self.lessons.setCurrentRow(r + 1)
            return
        t = self.tracks.currentRow()
        if t + 1 < self.tracks.count():
            self.tracks.setCurrentRow(t + 1)

    def _quiz(self):
        les = self._current_lesson()
        if not les:
            return
        correct = 0
        for item in les.get("quiz", []):
            choice, ok = QInputDialog.getItem(
                self, "Quiz", item["q"], item["choices"], 0, False)
            if not ok:
                return
            if item["choices"].index(choice) == item["answer"]:
                correct += 1
        total = len(les.get("quiz") or [])
        QMessageBox.information(self, "Quiz result", f"Score: {correct}/{total}")
