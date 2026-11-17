"""`BotStudio --selftest`: headless smoke test used by the packaging scripts (no display needed)."""
from __future__ import annotations

import os
import sys


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from . import __version__
    from .core import examples, project
    from .core.model import Graph
    from .core.scope import editor_context
    from .ui import theme
    from .ui.panels import PropertyPanel
    theme.apply(app)
    fails = []
    for name, fn in examples.EXAMPLES.items():
        res = project.build(fn())
        if not res.ok:
            fails.append((name, res.errors))
    # custom nodes: scope + property panel round trip (regression: 'QWidget' has no attribute 'toPlainText')
    g = Graph()
    t = g.add_node("trigger.slash", 0, 0, {"name": "x"})
    c = g.add_node("action.python", 200, 0)
    g.connect(t.id, "next", c.id, "in")
    if "user" not in editor_context(g, c)["names"]:
        fails.append(("scope", "trigger variables missing"))
    from PyQt6.QtWidgets import QPlainTextEdit
    pp = PropertyPanel()
    pp.show_node(g, c.id)
    pp.findChildren(QPlainTextEdit)[0].setPlainText("result = 1")
    if c.props.get("code") != "result = 1":
        fails.append(("panel", "code edit did not reach the node"))
    # the runtime and dashboard helpers must be bundled
    from pathlib import Path
    rt = Path(__file__).resolve().parent / "runtime"
    for f in ("bs_runtime.py", "bs_dash.js", "dash_server.js", "game/bs_game.py", "game/bsg_quests.py", "game/bsg_gear.py",
              "game/bsg_dungeons.py", "game/bsg_cards.py", "game/bsg_scoring.py", "game/bsg_bosses.py", "game/bsg_arena.py", "game/bsg_fonts_tpl.py", "game/fonts/Outfit.ttf", "game/fonts/WorkSans.ttf",
              "community/bsc_birthdays.py", "community/bsc_post_router.py", "community/bsc_task_lists.py", "community/bsc_events.py",
              "community/bsc_emotes.py", "community/bsp_database.py", "community/bsd_db.py", "dash_mock.js"):
        if not (rt / f).is_file():
            fails.append(("bundle", f"missing runtime/{f}"))
    # every game module + UI / economy node must build into valid Python
    g = Graph()
    for t in ("module.quests", "module.gear", "module.dungeons", "module.cards", "module.scoring", "module.bosses", "module.arena"):
        g.add_node(t, 0, 0)
    r = project.build(g)
    if not r.ok or "bsg_fonts.py" not in r.files:
        fails.append(("game modules", r.errors or "bsg_fonts.py missing"))
    g = Graph()
    for t in ("module.emotes", "module.birthdays", "module.post_router", "module.role_brackets", "module.game_profiles",
              "module.task_lists", "module.event_scheduler"):
        g.add_node(t, 0, 0)
    r = project.build(g)
    if not r.ok or "bsd_db.py" not in r.files or "bsp_database.py" not in r.files:
        fails.append(("community modules", r.errors or "support files missing"))
    import re
    tok = re.compile(r"[A-Za-z0-9_-]{24}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}")
    if any(tok.search(t) for t in r.files.values()):
        fails.append(("security", "a token-shaped string is bundled"))
    # guided lessons, examples, web nodes
    from .ui.lessons.content import LESSONS
    from .core import example_bots, web_validate
    if len(LESSONS) != 6 or any(not l.steps for l in LESSONS):
        fails.append(("lessons", f"expected 6 lessons, found {len(LESSONS)}"))
    if len(example_bots.EXAMPLE_INFO) != 12:
        fails.append(("examples", "expected 12 ready-to-run examples"))
    for info in example_bots.EXAMPLE_INFO:
        r = project.build(getattr(example_bots, info["fn"])())
        if not r.ok:
            fails.append((info["title"], r.errors[:1]))
    if not any(i[2] == "error" for i in web_validate.check_browser_js("require('fs')")):
        fails.append(("web validators", "browser JS must reject require()"))
    print(f"Bot Forge Studio {__version__} selftest:", "FAILED" if fails else "OK")
    for f in fails:
        print("  ", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
