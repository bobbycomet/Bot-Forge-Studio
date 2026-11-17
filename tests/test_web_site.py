"""Web development nodes + dashboard test mode: HTML / CSS / browser JS pages, WebSocket live updates, dashboard -> bot events.
Needs Node >= 22.13 and npm registry access.  Run from tests/:  python test_web_site.py"""
import asyncio, json, os, pathlib, subprocess, sys, tempfile, time, urllib.request, urllib.error

sys.path.insert(0, "..")
from botstudio.core import project, web_validate
from botstudio.core.model import Graph

HERE = pathlib.Path(__file__).resolve().parent
FAILS = []
def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    if not cond: FAILS.append(msg)

def graph(bad=False):
    g = Graph(); g.settings.name = "Site Test"
    g.add_node("module.dashboard", 0, 0, {"title": "Test Dash", "translate": False})
    html = g.add_node("web.html", 0, 200, {"slug": "status", "title": "Status", "nav_title": "Status"})
    css = g.add_node("web.css", -300, 200, {"name": "layout"}); js = g.add_node("web.js", -300, 400, {"name": "app"})
    g.connect(css.id, "attach", html.id, "assets"); g.connect(js.id, "attach", html.id, "assets")
    if bad:
        html.props["code"] = '<div id="a"><p>x</div><button onclick="go()">go</button>'
        js.props["code"] = "const fs = require('fs');"
    rt = g.add_node("web.route", 0, 700, {"method": "POST", "path": "/api/custom/ping"})
    jg = g.add_node("data.json_get", 300, 700, {"path": "note"}); ob = g.add_node("data.object", 600, 700, {"keys": "note"})
    em = g.add_node("web.emit_bot", 900, 700, {"name": "dashboard_action"}); rj = g.add_node("web.json", 1200, 700)
    g.connect(rt.id, "next", em.id, "in"); g.connect(rt.id, "body", jg.id, "source"); g.connect(jg.id, "value", ob.id, "note")
    g.connect(ob.id, "json", em.id, "data"); g.connect(em.id, "next", rj.id, "in")
    t = g.add_node("trigger.dashboard_event", 0, 1000, {"name": "dashboard_action"}); lg = g.add_node("action.log", 300, 1000, {"text": "got {user_id}"})
    g.connect(t.id, "next", lg.id, "in")
    return g

def req(base, path, method="GET", body=None, cookie=None):
    r = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={"content-type": "application/json", **({"cookie": cookie} if cookie else {})})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k): return None
    op = urllib.request.build_opener(NoRedirect)
    try:
        resp = op.open(r, timeout=5); return resp.status, resp.read().decode(), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), e.headers

def main():
    print("== validators")
    check(any("closed" in i[3] for i in web_validate.check_html("<div><p>x</div>")), "HTML: unclosed tag")
    check(any("blocked" in i[3] for i in web_validate.check_html('<button onclick="x()">')), "HTML: inline handler flagged (CSP)")
    check(any(i[2] == "error" and "Node.js" in i[3] for i in web_validate.check_browser_js("const x = require('fs')")), "browser JS: require() is an error")
    check(any("discord.js" in i[3] for i in web_validate.check_browser_js("const {Client} = require('discord.js')")), "browser JS: discord.js refused")
    check(any("token" in i[3].lower() for i in web_validate.check_browser_js("const t='" + "A"*24 + "." + "B"*6 + "." + "C"*30 + "'")), "browser JS: token-shaped string refused")
    check(any("';'" in i[3] for i in web_validate.check_css(".a { color: red\n margin: 0; }")), "CSS: missing semicolon")

    print("== build")
    bad = project.build(graph(bad=True))
    check(not bad.ok and any("site/status.html" in e for e in bad.errors) and any("status.app.js" in e for e in bad.errors), "broken pages fail the build with file:line errors")
    res = project.build(graph())
    check(res.ok, f"good project builds {res.errors}")
    files = res.files
    page = files["dashboard/web/public/site/status.html"]
    check('/site/status.layout.css' in page and '/site/status.app.js' in page and '/bs-live.js' in page and page.index("bs-live.js") < page.index("status.app.js"), "page links connected CSS/JS in order, after the live helper")
    check("dashboard/web/public/bs-live.js" in files and '"sitePages"' in files["dashboard/config.json"], "helper + per-page login config written")
    check(any(p["type"] == "site" for p in json.loads(files["dashboard/config.json"])["pages"]), "menu entry for the page")
    check('"ws"' in files["dashboard/package.json"], "ws dependency declared")
    check("on_bs_dashboard_event" in [c.source for c in res.cogs if "dashboard_action" in c.source][0], "bot listener for the dashboard event generated")
    g2 = graph(); g2.add_node("web.css", 0, 1400)
    check(any("not connected to an HTML Page" in w for w in project.build(g2).warnings), "unattached CSS node warns")

    print("== running the dashboard in test mode")
    d = pathlib.Path(tempfile.mkdtemp(prefix="bssite_"))
    for rel, text in res.files.items():
        p = d / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text, encoding="utf-8")
    subprocess.run(["npm", "install", "--omit=dev", "--no-audit", "--no-fund", "--loglevel=error"], cwd=d / "dashboard", check=True)
    (d / "dashboard" / "ws_harness.js").write_text((HERE / "ws_harness.js").read_text())
    port = 34000 + os.getpid() % 1000
    env = {**os.environ, "BS_TEST_MODE": "1", "BS_BASE_DIR": str(d), "DASHBOARD_PORT": str(port), "DASHBOARD_URL": f"http://127.0.0.1:{port}", "DASHBOARD_HOST": "127.0.0.1"}
    proc = subprocess.Popen(["node", "server.js"], cwd=d / "dashboard", env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try: req(base, "/api/config"); break
            except Exception: time.sleep(0.2)
        st, body, h = req(base, "/site/status.html")
        check(st == 302, "page redirects anonymous visitors to the login")
        st, _b, h = req(base, "/auth/login")
        cookie = h.get("Set-Cookie", "").split(";")[0]
        check(st == 302 and cookie.startswith("bs_sid="), "test-mode login creates a session without Discord")
        sid = cookie.split("=", 1)[1]
        st, body, h = req(base, "/site/status.html", cookie=cookie)
        check(st == 200 and "<title>Status</title>" in body and "Content-Security-Policy" in h and "ws://" in h["Content-Security-Policy"], "page served behind login with a CSP that allows the same-origin socket")
        st, body, _h = req(base, "/site/status.app.js", cookie=cookie)
        check(st == 200 and "BotStudio.live" in body, "browser script served")
        st, body, _h = req(base, "/api/me", cookie=cookie)
        check(st == 200 and json.loads(body)["guilds"][0]["name"] == "Test Server", "fake Discord answers /api/me")
        r = subprocess.run(["node", "ws_harness.js", base, sid], cwd=d / "dashboard", capture_output=True, text=True, timeout=60)
        check(r.returncode == 0, "websocket: auth, origin, subscribe, status, live events" + ("" if r.returncode == 0 else "\n" + (r.stdout + r.stderr)[-800:]))
        st, body, _h = req(base, "/api/custom/ping", "POST", {"note": "hello"}, cookie=cookie)
        check(st == 200, f"web route with 'Send Event to Bot' answers ({st} {body[:80]})")
        st, _b, _h = req(base, "/api/custom/ping", "POST", {"note": "x"})
        check(st == 401, "the route requires login")
    finally:
        proc.terminate()
        try: out = proc.communicate(timeout=5)[0]
        except Exception: out = ""
    check("TEST MODE" in out and "HTTP GET /site/status.html" in out, "test mode logs requests and WebSocket traffic")

    print("== dashboard -> bot")
    os.environ["DATA_DIR"] = str(d / "data"); sys.path.insert(0, str(d))
    import bs_runtime as bs
    async def drain():
        rows = await bs.kv_top_any("dash_events", 10)
        return rows
    rows = asyncio.run(drain())
    check(len(rows) == 1 and rows[0][1]["name"] == "dashboard_action" and rows[0][1]["data"] == {"note": "hello"}, f"event queued for the bot: {rows}")
    bs.live_emit(123, "score", {"n": 1})
    line = (d / "data" / "live_events.jsonl").read_text().strip().splitlines()[-1]
    check(json.loads(line)["event"] == "score", "bot -> dashboard live events are appended for the server to tail")
    print("\nFAILED" if FAILS else "\nALL PASSED")
    return 1 if FAILS else 0

sys.exit(main())
