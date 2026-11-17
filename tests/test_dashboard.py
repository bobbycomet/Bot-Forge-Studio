"""Web dashboard tests. Run from the tests/ folder:  python test_dashboard.py

Needs Node.js >= 22.13 and network access to the npm registry (one install of discord.js + express).
Checks: translation of the bot into pages, Python-only builds are untouched, nodes that cannot run
in the dashboard are rejected, generated JS parses, the real dashboard server works end to end against
a fake Discord (OAuth login, REST data, scopes, announce, CSRF, Web Routes, live reload), and the
Python bot's supervisor starts / stops the dashboard process.
"""
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.request

sys.path.insert(0, "..")
from botstudio.core import examples, project, services  # noqa: E402
from botstudio.core.model import DeployTarget, Graph  # noqa: E402
from botstudio.core.nodes_web import parse_pages  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
FAILS = []


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def write(build, dest: pathlib.Path):
    for rel, text in build.files.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def main():
    print("== bots without a dashboard are unchanged")
    for name, fn in examples.EXAMPLES.items():
        g = fn()
        r = project.build(g)
        has_dash = any(n.type_id == "module.dashboard" for n in g.nodes.values())
        check(r.ok and r.dashboard == has_dash and bool([k for k in r.files if k.startswith("dashboard/")]) == has_dash,
              f"{name}: ok, dashboard={has_dash}")

    print("== translation of the bot")
    r = project.build(examples.dashboard())
    cfg = json.loads(r.files["dashboard/config.json"])
    kinds = [(p["type"], p["arg"]) for p in cfg["pages"]]
    check(("commands", "") in kinds and ("leaderboard", "xp") in kinds and ("leaderboard", "uses") in kinds,
          "commands page + XP and counter leaderboards come from the graph")
    check(not any(a == "dashboard_visits" for _, a in kinds), "storage used only inside a Web Route is not exposed")
    check(any(c["name"] == "count" for c in cfg["commands"]), "slash commands listed")
    m = json.loads(r.files["manifest.json"])
    check(m["dashboard"] is True and m["dashboard_sig"], "manifest tells the bot to run the dashboard")
    check(not any(p.startswith("dashboard/") for p in m["cogs"]), "dashboard files are not bot cogs")
    g = examples.dashboard()
    g2 = examples.dashboard()
    for n in g2.nodes.values():
        if n.type_id == "web.route":
            n.props["path"] = "/api/custom/other"
    sig_a = json.loads(project.build(g).files["manifest.json"])["restart_sig"]
    sig_b = json.loads(project.build(g2).files["manifest.json"])["restart_sig"]
    check(sig_a == sig_b, "route changes do not force a bot restart (dashboard reloads live)")

    print("== global tables are owner-only")
    g = Graph()
    t = g.add_node("trigger.slash", 0, 0, {"name": "c"})
    i = g.add_node("store.incr", 100, 0, {"namespace": "everyone", "key": "{user.id}"})
    g.connect(t.id, "next", i.id, "in")
    g.add_node("module.dashboard", 0, 200)
    cfg = json.loads(project.build(g).files["dashboard/config.json"])
    page = next(p for p in cfg["pages"] if p["arg"] == "everyone")
    check(page["scope"] == "global" and page["ownerOnly"], "key without {guild.id}: shown to the bot owner only")

    print("== node rules")
    g = Graph()
    d = g.add_node("module.dashboard", 0, 0)
    rt = g.add_node("web.route", 0, 100)
    rep = g.add_node("action.reply", 200, 100, {"content": "x"})
    g.connect(rt.id, "next", rep.id, "in")
    res = project.build(g)
    check(not res.ok and any("works in the bot" in e for e in res.errors), "Reply inside a Web Route is an error that explains why")
    g = Graph()
    g.add_node("web.route", 0, 0)
    res = project.build(g)
    check(not res.ok and any("Web Dashboard node" in e for e in res.errors), "Web Route without a dashboard is an error")
    g = Graph()
    g.add_node("module.dashboard", 0, 0)
    t = g.add_node("trigger.slash", 0, 100, {"name": "x"})
    c = g.add_node("action.js", 200, 100, {"code": "x"})
    g.connect(t.id, "next", c.id, "in")
    check(not project.build(g).ok, "Custom JavaScript inside the bot is rejected")
    g = Graph()
    g.add_node("module.dashboard", 0, 0)
    rt = g.add_node("web.route", 0, 100)
    c = g.add_node("action.js", 200, 100, {"code": "const x = (1;"})
    g.connect(rt.id, "next", c.id, "in")
    check(any("cogs" not in e and ".js" in e for e in project.build(g).errors), "JS syntax errors in a Custom JavaScript node fail the build")
    pages = parse_pages("A | kv | t edit\nB | leaderboard | l global\nC | api | /etc/passwd\nD | kv | bad$name")
    check([(p["type"], p["scope"], p["edit"]) for p in pages] == [("kv", "guild", True), ("leaderboard", "global", False)],
          "page parser validates tables, flags and api paths")

    print("== plugins can add dashboard pages")
    import zipfile
    from botstudio.core import modules
    z = pathlib.Path(tempfile.mkdtemp(prefix="bsmod_")) / "pts.bsmod"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("module.json", json.dumps({
            "id": "pts", "title": "Points", "entry": "cogs/pts.py",
            "props": [{"key": "table", "kind": "str", "default": "points"}],
            "dashboard": {"pages": ["Points board | leaderboard | {table}", "Bad | leaderboard | bad$$"]}}))
        zf.writestr("cogs/pts.py", "async def setup(bot):\n    pass\n")
    spec = modules.load_bsmod(z)
    g = Graph()
    g.add_node(spec.type_id, 0, 0, {"table": "gold"})
    g.add_node("module.dashboard", 0, 200)
    cfg = json.loads(project.build(g).files["dashboard/config.json"])
    check(any(p["title"] == "Points board" and p["arg"] == "gold" for p in cfg["pages"]), "plugin page appears, {table} filled from the node")
    check(not any(p["title"] == "Bad" for p in cfg["pages"]), "invalid plugin pages are dropped")

    print("== services")
    g = examples.dashboard()
    res = project.build(g)
    svc = services.generate(g.settings, DeployTarget(), "/opt/x", "systemd-system", "u", res.entry, dashboard=True)
    check("dashboard/deps.sh" in svc and "./dashboard/deps.sh" in svc["install.sh"], "install.sh sets up the dashboard's Node.js + npm")
    check("/opt/x/venv/bin/python /opt/x/bot.py" in svc["dashboard-bot.service"], "one service: the Python bot (it runs the dashboard)")
    check("dashboard/deps.sh" not in services.generate(g.settings, DeployTarget(), "/opt/x", "systemd-system", "u", "bot.py"),
          "no dashboard, no Node setup")

    print("== the dashboard server (real Express + fake Discord)")
    d = pathlib.Path(tempfile.mkdtemp(prefix="bsdash_"))
    write(res, d)
    subprocess.run(["npm", "install", "--omit=dev", "--no-audit", "--no-fund", "--loglevel=error"], cwd=d / "dashboard", check=True)
    (d / "dashboard" / "dash_harness.js").write_text((HERE / "dash_harness.js").read_text(encoding="utf-8"), encoding="utf-8")
    p = subprocess.run(["node", "dash_harness.js"], cwd=d / "dashboard", capture_output=True, text=True, timeout=180)
    check(p.returncode == 0, "dashboard end-to-end" + ("" if p.returncode == 0 else "\n" + (p.stdout + p.stderr)[-1500:]))
    os.remove(d / "dashboard" / "dash_harness.js")

    print("== the bot supervises the dashboard")
    from botstudio.runtime import bs_runtime as bs  # noqa: E402

    async def supervise():
        (d / "dashboard" / ".runtime").mkdir(exist_ok=True)
        (d / "dashboard" / ".runtime" / "node_path").write_text(subprocess.run(["which", "node"], capture_output=True, text=True).stdout.strip())
        port = 33000 + os.getpid() % 1000
        os.environ.update(DASHBOARD_PORT=str(port), DASHBOARD_URL=f"http://127.0.0.1:{port}")
        runner = bs.DashboardRunner(d)
        await runner.ensure(True, "sig1")
        ok = runner.running
        for _ in range(50):
            await asyncio.sleep(0.2)
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=2) as resp:
                    started = resp.status == 200
                    break
            except OSError:
                started = False
        pid = runner.proc.pid
        await runner.ensure(True, "sig1")
        same = runner.proc.pid == pid                      # unchanged signature: not restarted
        await runner.ensure(True, "sig2")
        restarted = runner.proc.pid != pid                 # new server.js: restarted
        await runner.ensure(False, None)
        return ok, started, same, restarted, runner.running

    ok, started, same, restarted, still = asyncio.run(supervise())
    check(ok and started, "bot starts the dashboard process and it answers")
    check(same and restarted, "restart only when server files change")
    check(not still, "dashboard stops with the bot")

    print("\nFAILED" if FAILS else "\nALL PASSED")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
