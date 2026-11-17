"""Web test console: run the generated dashboard locally against a *fake Discord* and inspect it.

* no bot token, no real server, no OAuth — the dashboard starts in TEST MODE (loopback only) with an instant login,
* pages, API routes and the WebSocket all work, and the live log is split into server / HTTP / WebSocket views,
* an API console fires requests as the logged-in test user; a button pushes a live event to every open page.
The Discord bot is not started and is never touched.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

from PyQt6.QtCore import QProcess, QProcessEnvironment, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget)

from ..core import jscheck, project


def cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) if not sys.platform.startswith("win") else Path(os.environ.get("LOCALAPPDATA", Path.home()))
    d = base / "botstudio" / "webtest"
    d.mkdir(parents=True, exist_ok=True)
    return d


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def node_ok() -> tuple[str | None, str]:
    node = jscheck.node_binary() or shutil.which("node")
    if not node:
        return None, "Node.js was not found. Test mode needs Node.js 22.13 or newer on this machine (nodejs.org). The deployed bot can download its own private copy, but the local test console cannot."
    try:
        v = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout.strip().lstrip("v")
        major, minor = (int(x) for x in v.split(".")[:2])
        if (major, minor) < (22, 13):
            return None, f"Node.js {v} is too old — the dashboard needs 22.13 or newer."
        return node, f"Node.js {v}"
    except Exception as e:
        return None, f"Could not run node: {e}"


class _Request(threading.Thread):
    """HTTP request off the UI thread; reports back through a callback signal."""

    def __init__(self, base: str, method: str, path: str, body: str, cookie: str, done):
        super().__init__(daemon=True)
        self.args, self.done = (base, method, path, body, cookie), done

    def run(self):
        base, method, path, body, cookie = self.args

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        op = urllib.request.build_opener(NoRedirect)
        try:
            if not cookie:
                try:
                    op.open(urllib.request.Request(base + "/auth/login"), timeout=5)
                except urllib.error.HTTPError as e:
                    cookie = (e.headers.get("Set-Cookie") or "").split(";")[0]
            data = body.encode() if body.strip() else None
            req = urllib.request.Request(base + path, method=method, data=data,
                                         headers={"content-type": "application/json", "cookie": cookie or ""})
            try:
                resp = op.open(req, timeout=10)
                status, text = resp.status, resp.read().decode("utf-8", "replace")
            except urllib.error.HTTPError as e:
                status, text = e.code, e.read().decode("utf-8", "replace")
            try:
                text = json.dumps(json.loads(text), indent=2)
            except ValueError:
                pass
            self.done(f"HTTP {status}\n\n{text[:6000]}", cookie)
        except Exception as e:
            self.done(f"Request failed: {e}", cookie)


class WebTestDialog(QDialog):
    result_ready = pyqtSignal(str, str)

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Web test console — your dashboard against a fake Discord")
        self.resize(980, 720)
        self.proc: QProcess | None = None
        self.npm: QProcess | None = None
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.cookie = ""
        self.dir: Path | None = None
        self.status = QLabel("Not running.")
        self.status.setWordWrap(True)
        self.start_btn = QPushButton("Build && start")
        self.start_btn.setObjectName("primary")
        self.rebuild_btn = QPushButton("Rebuild (hot reload)")
        self.stop_btn = QPushButton("Stop")
        self.open_btn = QPushButton("Open in browser")
        for b in (self.rebuild_btn, self.stop_btn, self.open_btn):
            b.setEnabled(False)
        self.start_btn.clicked.connect(self.start)
        self.rebuild_btn.clicked.connect(self.rebuild)
        self.stop_btn.clicked.connect(self.stop)
        self.open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.base + "/auth/login")))
        self.pages = QHBoxLayout()
        top = QHBoxLayout()
        for b in (self.start_btn, self.rebuild_btn, self.stop_btn, self.open_btn):
            top.addWidget(b)
        top.addStretch()
        self.tabs = QTabWidget()
        self.all_log, self.http_log, self.ws_log = (QPlainTextEdit() for _ in range(3))
        for w, name in ((self.all_log, "Server log"), (self.http_log, "HTTP requests"), (self.ws_log, "WebSocket")):
            w.setReadOnly(True)
            w.setMaximumBlockCount(2000)
            self.tabs.addTab(w, name)
        # API console
        self.method = QComboBox()
        self.method.addItems(["GET", "POST", "PUT", "DELETE"])
        self.path = QLineEdit("/api/me")
        self.body = QLineEdit()
        self.body.setPlaceholderText('JSON body, e.g. {"note": "hi"}')
        send = QPushButton("Send")
        send.clicked.connect(self.send_request)
        self.response = QPlainTextEdit()
        self.response.setReadOnly(True)
        api = QWidget()
        al = QVBoxLayout(api)
        row = QHBoxLayout()
        row.addWidget(self.method)
        row.addWidget(self.path, 1)
        row.addWidget(send)
        al.addLayout(row)
        al.addWidget(self.body)
        al.addWidget(self.response, 1)
        self.tabs.addTab(api, "API console")
        # live events
        self.evt = QLineEdit("update")
        self.evt_data = QLineEdit('{"n": 1}')
        push = QPushButton("Push live event to open pages")
        push.clicked.connect(self.push_event)
        live = QWidget()
        ll = QVBoxLayout(live)
        ll.addWidget(QLabel("Simulates the bot (or a Web Route) broadcasting. Pages receive it in BotStudio.live(handler) as {type:'event', event, data}."))
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Event"))
        r2.addWidget(self.evt)
        r2.addWidget(QLabel("Data (JSON)"))
        r2.addWidget(self.evt_data, 1)
        r2.addWidget(push)
        ll.addLayout(r2)
        ll.addStretch()
        self.tabs.addTab(live, "Live events")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Runs your generated dashboard in <b>test mode</b>: fake Discord, instant login, loopback only. The bot itself is not started."))
        lay.addLayout(top)
        lay.addWidget(self.status)
        lay.addLayout(self.pages)
        lay.addWidget(self.tabs, 1)
        self.result_ready.connect(self._show_result)

    # ---- build & run -------------------------------------------------------------------
    def _write(self) -> bool:
        res = project.build(self.win.graph)
        if not res.ok:
            self.status.setText("The project has errors — fix them first:\n" + "\n".join(res.errors[:5]))
            return False
        if not res.dashboard:
            self.status.setText("This project has no dashboard. Add a **Web Dashboard** module node first.")
            return False
        slug = self.win.graph.settings.slug
        self.dir = cache_dir() / slug
        self.dir.mkdir(parents=True, exist_ok=True)
        old_pkg = (self.dir / "dashboard" / "package.json").read_text() if (self.dir / "dashboard" / "package.json").exists() else ""
        for rel, text in res.files.items():
            if rel.startswith("dashboard/") or rel in ("manifest.json",):
                p = self.dir / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text, encoding="utf-8")
        self._pkg_changed = old_pkg != res.files["dashboard/package.json"]
        cfg = json.loads(res.files["dashboard/config.json"])
        while self.pages.count():
            it = self.pages.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for pg in cfg.get("pages", []):
            if pg["type"] == "site":
                b = QPushButton(f"Open /site/{pg['arg']}.html")
                b.clicked.connect(lambda _=False, a=pg["arg"]: QDesktopServices.openUrl(QUrl(f"{self.base}/auth/login?next=/site/{a}.html")) or None)
                self.pages.addWidget(b)
        self.pages.addStretch()
        return True

    def start(self):
        node, msg = node_ok()
        if node is None:
            self.status.setText(msg)
            return
        if not self._write():
            return
        self.node = node
        dash = self.dir / "dashboard"
        if self._pkg_changed or not (dash / "node_modules").exists():
            self.status.setText("Installing dashboard packages (npm install) — first run only…")
            self.npm = QProcess(self)
            self.npm.setWorkingDirectory(str(dash))
            self.npm.readyReadStandardOutput.connect(lambda: self._log(bytes(self.npm.readAllStandardOutput()).decode("utf-8", "replace")))
            self.npm.readyReadStandardError.connect(lambda: self._log(bytes(self.npm.readAllStandardError()).decode("utf-8", "replace")))
            self.npm.finished.connect(lambda code, _s: self._launch() if code == 0 else self.status.setText("npm install failed — see the Server log (network access to the npm registry is needed once)."))
            self.npm.start(shutil.which("npm") or "npm", ["install", "--omit=dev", "--no-audit", "--no-fund", "--loglevel=error"])
        else:
            self._launch()

    def _launch(self):
        self.stop(quiet=True)
        dash = self.dir / "dashboard"
        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        for k, v in {"BS_TEST_MODE": "1", "BS_BASE_DIR": str(self.dir), "DASHBOARD_PORT": str(self.port), "DASHBOARD_URL": self.base,
                     "DASHBOARD_HOST": "127.0.0.1"}.items():
            env.insert(k, v)
        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(str(dash))
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(lambda: self._log(bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")))
        self.proc.finished.connect(lambda *_: self.status.setText("Stopped."))
        self.proc.start(self.node, ["server.js"])
        self.status.setText(f"Running at {self.base} — login is automatic in test mode. Edit the project and press “Rebuild” to hot-reload pages, routes and config.")
        for b in (self.rebuild_btn, self.stop_btn, self.open_btn):
            b.setEnabled(True)
        self.win._webtest_started = True

    def rebuild(self):
        if self._write():
            self.status.setText("Rebuilt — the server reloads config, routes and pages by itself (a change to the packages needs Stop → Build && start).")

    def stop(self, quiet: bool = False):
        if self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.terminate()
            if not self.proc.waitForFinished(2000):
                self.proc.kill()
        self.proc = None
        if not quiet:
            self.status.setText("Stopped.")
            for b in (self.rebuild_btn, self.stop_btn, self.open_btn):
                b.setEnabled(False)

    def done(self, *_):
        self.stop(quiet=True)
        super().done(*_)

    def closeEvent(self, e):
        self.stop(quiet=True)
        super().closeEvent(e)

    # ---- logs & requests ----------------------------------------------------------------
    def _log(self, text: str):
        for ln in text.splitlines():
            if not ln.strip():
                continue
            self.all_log.appendPlainText(ln)
            if "| HTTP " in ln or ln.startswith("HTTP "):
                self.http_log.appendPlainText(ln.split("dashboard |", 1)[-1].strip())
            if "| WS " in ln or " WS " in ln:
                self.ws_log.appendPlainText(ln.split("dashboard |", 1)[-1].strip())

    def send_request(self):
        if self.proc is None:
            self.response.setPlainText("Start the server first.")
            return
        _Request(self.base, self.method.currentText(), self.path.text().strip() or "/", self.body.text(), self.cookie,
                 lambda text, ck: self.result_ready.emit(text, ck)).start()

    def _show_result(self, text: str, cookie: str):
        self.cookie = cookie or self.cookie
        self.response.setPlainText(text)

    def push_event(self):
        if self.proc is None:
            self.response.setPlainText("Start the server first.")
            return
        try:
            data = json.loads(self.evt_data.text() or "{}")
        except ValueError:
            self.response.setPlainText("The data must be valid JSON.")
            self.tabs.setCurrentIndex(3)
            return
        _Request(self.base, "POST", "/__test/live", json.dumps({"event": self.evt.text() or "update", "data": data}), self.cookie,
                 lambda text, ck: self.result_ready.emit(text, ck)).start()
        self.tabs.setCurrentIndex(3)
