"""Every guided lesson must be completable: a scripted 'perfect learner' performs each step on the real main window and the engine
has to recognise it.  Also checks that wrong answers are rejected with a message.   Run from tests/:  python test_lessons.py"""
import os, sys, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = os.path.join(os.environ["HOME"], "cfg")
sys.path.insert(0, "..")
from PyQt6.QtCore import QPointF
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication
app = QApplication(sys.argv)
from botstudio.ui import theme
theme.apply(app)
from botstudio.ui.main_window import MainWindow
from botstudio.ui.lessons import progress
from botstudio.ui.lessons.content import LESSONS, PY_REPORT, PY_STATS, HTML_L6, CSS_L6, JS_L6
from botstudio.ui.lessons.engine import LessonRunner
from botstudio.core.model import Graph

FAILS = []
def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond: FAILS.append(msg)

PY_READ = ('rows = [(k, v) for k, v in await bs.kv_top_any("tickets", 50)\n        if k.startswith(f"{guild.id}:") and isinstance(v, dict)]\n'
           'lines = [f"#{k.split(\':\')[1]} — {v[\'title\']}" for k, v in rows[-10:]]\nresult = "\\n".join(lines) or "No reports yet."')
SOL = {
 "l1": {("slash","name"):"greet", ("slash","description"):"Get a friendly greeting", ("reply","content"):"Hello {user.mention}, welcome to {guild.name}!",
        ("slash2","name"):"flip", ("rand","items"):"Heads|Tails|Edge", ("fmt","template"):"🪙 {user.mention} flipped **{side}**!",
        ("send","channel"):"123456789012345678", ("send","content"):"Welcome {user.mention}!"},
 "l2": {("t1","name"):"report", ("form","fields"):"Title | title | short | yes | What is wrong?\nDetails | details | long | yes | Tell us more",
        ("py","inputs"):"title, details", ("py","outputs"):"ticket_id", ("py","code"):PY_REPORT, ("trk","event"):"report_filed",
        ("fmt","template"):"🎫 Ticket **#{id}** filed — thanks {user.mention}!", ("t2","name"):"reports", ("t2","require_permission"):"manage_guild",
        ("py2","code"):PY_READ},
 "l3": {("sc","side_names_list"):"Sunwardens, Moonwardens", ("buy","name"):"buy", ("spend","amount"):120, ("give","template"):"steel_sword",
        ("ok","content"):"🛒 {user.mention} bought an Iron Sword!", ("no","content"):"You need more coins for that."},
 "l4": {("cmd","name"):"roll", ("fmt","template"):"🎲 {user.mention} rolled a **{n}**!"},
 "l5": {("cmd","name"):"stats", ("cmd","params"):"numbers:str", ("py","inputs"):"numbers", ("py","outputs"):"mean, summary", ("py","code"):PY_STATS},
 "l6": {("dash","title"):"Visits dashboard", ("rt","path"):"/api/custom/stats", ("vi","namespace"):"visits", ("vi","key"):"{guild_id}", ("ob","keys"):"visits",
        ("bc","event"):"visit", ("page","slug"):"visits", ("page","code"):HTML_L6, ("css","code"):CSS_L6, ("js","code"):JS_L6,
        ("rt2","method"):"POST", ("rt2","path"):"/api/custom/ping"},
}
BRANCH = {"path": "coin", "backend": "node"}

def play(lesson, branch_override=None):
    win = MainWindow(); win.show(); app.processEvents()
    win._load_graph(Graph(), None)
    run = LessonRunner(win, lesson, resume=False)
    run.ctx.update(branch_override or {})
    run.start()
    n_added, guard = 0, 0
    sol = SOL[lesson.id]
    while True:
        guard += 1
        if guard > 400:
            return False, "stuck (no progress)", win
        st = run.step()
        done, level, msg = run.evaluate(st)
        k = st.kind
        if k == "add_node" and not done:
            if st.type_id == "custom.dice_roll":
                from botstudio.ui.plugin_workshop import PluginWorkshop
                ws = PluginWorkshop(win)
                ws.edit.setPlainText(ws.edit.toPlainText().replace('"Dice Roll"', '"Roll D20"').replace('"sides", "Sides", "int", 6', '"sides", "Sides", "int", 20'))
                ws.save_and_load(); ws.test_generate(); win._workshop = ws
            node = win.scene.add_node(st.type_id, QPointF(160 * (n_added % 6), 140 * (n_added // 6)))
            n_added += 1
        elif k == "set_prop" and not done:
            val = sol.get((st.key, st.prop))
            if val is None:
                return False, f"no scripted answer for {(st.key, st.prop)} — step '{st.title}': {msg}", win
            # a wrong answer first: the engine must explain, not accept
            node = run._node(st.key)
            node.props[st.prop] = ""
            if st.check:
                run._poll()
            node.props[st.prop] = val
            win._prop_changed(node.id)
        elif k == "connect" and not done:
            a, b = run._node(st.src[0]), run._node(st.dst[0])
            e = win.graph.connect(a.id, st.src[1], b.id, st.dst[1])
            if e is None:
                return False, f"could not connect {st.src}->{st.dst} at '{st.title}' (ports missing?)", win
            win.scene.rebuild(); win._modified()
        elif k == "select" and not done:
            win.scene.clearSelection(); win.scene.node_items[run._node(st.key).id].setSelected(True)
        elif k == "validate":
            win.rebuild()
        elif k == "action":
            if "twenty-sided" in st.title or "Test the generator" in st.title:
                ws = win._workshop
                if "twenty-sided" in st.title:
                    ws.edit.setPlainText(ws.edit.toPlainText().replace('"Dice Roll"', '"Roll D20"').replace('"sides", "Sides", "int", 6', '"sides", "Sides", "int", 20'))
                    ws.save_and_load()
                else:
                    ws.test_generate()
            if "workshop" in st.title.lower() and "Open" in st.title:
                from botstudio.ui.plugin_workshop import PluginWorkshop
                win._workshop = PluginWorkshop(win); win._workshop.show()
            if "Test it locally" in st.title:
                win._webtest_started = True
        elif k == "confirm":
            run.confirmed.add(run.idx)
        elif k == "branch":
            run.ctx[st.key] = (branch_override or BRANCH)[st.key]
        run._poll(); app.processEvents()
        done, level, msg = run.evaluate(run.step())
        if not done:
            return False, f"step {run.idx} '{run.step().title}' ({run.step().kind}) not satisfied: [{level}] {msg}", win
        if run._is_last():
            run.next()
            return True, "", win
        run.next(); app.processEvents()

for lesson in LESSONS:
    branches = [{"path": "coin", "backend": "node"}, {"path": "events", "backend": "python"}] if lesson.id in ("l1", "l6") else [None]
    for br in branches:
        ok, why, win = play(lesson, br)
        label = f"{lesson.id} '{lesson.title}'" + (f" branch {br}" if br else "")
        check(ok, f"{label} completes ({len(lesson.steps)} steps){'' if ok else ' — ' + why}")
        if ok:
            check(progress.get(lesson.id)["done"], f"{lesson.id} recorded as done")
        win.dirty = False; win.close()

# wrong answers are rejected, with a reason
win = MainWindow(); win.show(); win._load_graph(Graph(), None)
run = LessonRunner(win, LESSONS[0], resume=False); run.start()
while run.step().kind != "set_prop": 
    st = run.step()
    if st.kind == "add_node": win.scene.add_node(st.type_id, QPointF(0, 0))
    run._poll(); run.next()
node = run._node("slash")
node.props["name"] = "Greet Me"
done, level, msg = run.evaluate(run.step())
check(not done and level == "error" and "lowercase" in msg, f"invalid command name is explained: {msg[:60]}")
# resume restores position and re-attaches nodes
progress.put("l1", run.idx, False, {})
run2 = LessonRunner(win, LESSONS[0], resume=True); run2.start()
check(run2.idx == run.idx and "slash" in run2.claimed, "resume re-attaches nodes already on the canvas")
check(len(LESSONS) == 6 and all(l.steps for l in LESSONS), "six lessons")
print("\nFAILED" if FAILS else "\nALL PASSED")
sys.exit(1 if FAILS else 0)
