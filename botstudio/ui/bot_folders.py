"""Bot folders: every project / deployed bot found in the bots folder, with open / start / stop / restart."""
from __future__ import annotations

from PyQt6.QtCore import Qt, QThreadPool
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
                             QVBoxLayout)

from ..core import projectdir
from ..core.lifecycle import BotManager


class BotFoldersDialog(QDialog):
    """Opens through File → Bot folders…  ``win`` is the main window (its Job helper runs work off the UI thread)."""

    def __init__(self, win):
        super().__init__(win)
        self.win = win
        self.setWindowTitle("Bot folders")
        self.resize(760, 480)
        self.root = projectdir.bots_root()
        self.infos: list[projectdir.ProjectFolder] = []
        self.states: dict[str, str] = {}
        self.head = QLabel()
        self.head.setObjectName("muted")
        self.head.setWordWrap(True)
        self.list = QListWidget()
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.btn_open = QPushButton("Open in editor")
        self.btn_open.setObjectName("primary")
        self.btn_start, self.btn_stop, self.btn_restart = QPushButton("Start"), QPushButton("Stop"), QPushButton("Restart")
        self.btn_refresh = QPushButton("Refresh")
        close = QPushButton("Close")
        self.btn_open.clicked.connect(self._open)
        self.btn_start.clicked.connect(lambda: self._control("start"))
        self.btn_stop.clicked.connect(lambda: self._control("stop"))
        self.btn_restart.clicked.connect(lambda: self._control("restart"))
        self.btn_refresh.clicked.connect(self.refresh)
        close.clicked.connect(self.accept)
        self.list.itemDoubleClicked.connect(lambda *_: self._open())
        self.list.currentRowChanged.connect(self._sel)
        row = QHBoxLayout()
        for b in (self.btn_open, self.btn_start, self.btn_stop, self.btn_restart, self.btn_refresh):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(close)
        lay = QVBoxLayout(self)
        lay.addWidget(self.head)
        lay.addWidget(self.list, 1)
        lay.addLayout(row)
        lay.addWidget(self.log)
        self.refresh()

    # ---------------------------------------------------------------- data
    def _current(self) -> projectdir.ProjectFolder | None:
        i = self.list.currentRow()
        return self.infos[i] if 0 <= i < len(self.infos) else None

    def _label(self, info: projectdir.ProjectFolder) -> str:
        state = self.states.get(str(info.path), "")
        what = ("project" if info.editable else "deployed — no stored graph") + (" · deployed" if info.editable and info.deployed else "")
        mark = {"active": "● running", "inactive": "○ stopped"}.get(state, "")
        return f"{info.name}   {mark}\n    {info.path}   ({what})"

    def refresh(self):
        self.infos = projectdir.scan(self.root)
        self.head.setText(f"Bots folder: {self.root}\nFolders saved with “Save as project folder…” or deployed by Studio are detected here. "
                          "Start / Stop ask for administrator rights (pkexec on Linux, UAC on Windows) only when the bot runs as a system service.")
        self.list.clear()
        for info in self.infos:
            self.list.addItem(QListWidgetItem(self._label(info)))
        if self.infos:
            self.list.setCurrentRow(0)
        else:
            self.list.addItem(QListWidgetItem("No bot folders yet — use File → Save as project folder… or Deploy."))
        self._sel(self.list.currentRow())
        for info in self.infos:
            self._probe(info)

    def _sel(self, _row):
        has = self._current() is not None
        for b in (self.btn_open, self.btn_start, self.btn_stop, self.btn_restart):
            b.setEnabled(has)

    def _probe(self, info):
        from .main_window import Job
        key = str(info.path)

        def work(_log):
            return BotManager.for_folder(info.path).status().get("state", "unknown")

        job = Job(work)

        def done(state):
            if isinstance(state, str):
                self.states[key] = state
                for i, inf in enumerate(self.infos):
                    if str(inf.path) == key and i < self.list.count():
                        self.list.item(i).setText(self._label(inf))
        job.signals.done.connect(done)
        QThreadPool.globalInstance().start(job)

    # ---------------------------------------------------------------- actions
    def _open(self):
        info = self._current()
        if info is None:
            return
        self.accept()
        self.win.open_bot_folder(str(info.path))

    def _control(self, action: str):
        info = self._current()
        if info is None:
            return
        from .main_window import Job
        self.log.appendPlainText(f"{action} {info.name} …")
        for b in (self.btn_start, self.btn_stop, self.btn_restart):
            b.setEnabled(False)

        def work(log):
            return BotManager.for_folder(info.path).control(action, log)

        job = Job(work)
        job.signals.log.connect(self.log.appendPlainText)

        def done(_r):
            self._sel(self.list.currentRow())
            self._probe(info)
        job.signals.done.connect(done)
        QThreadPool.globalInstance().start(job)
