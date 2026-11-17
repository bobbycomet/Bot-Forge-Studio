"""Project settings and deploy-target dialogs."""
from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QLineEdit,
                             QPlainTextEdit, QSpinBox, QVBoxLayout, QLabel)

from ..core.model import DeployTarget, Graph
from ..core.services import INIT_CHOICES, detect_local_init

INTENTS = ("message_content", "members", "presences", "voice_states")
ACTIVITIES = ("watching", "playing", "listening", "competing")
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class SettingsDialog(QDialog):
    def __init__(self, graph: Graph, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Project settings")
        self.g = graph
        s = graph.settings
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(s.name)
        self.token_env = QLineEdit(s.token_env)
        self.guild = QLineEdit(s.dev_guild_id)
        self.guild.setPlaceholderText("optional — instant command sync on one server")
        self.prefix = QLineEdit(s.command_prefix)
        self.status = QLineEdit(s.status_text)
        self.activity = QComboBox()
        self.activity.addItems(ACTIVITIES)
        self.activity.setCurrentText(s.activity_type)
        self.level = QComboBox()
        self.level.addItems(LEVELS)
        self.level.setCurrentText(s.log_level)
        self.hot = QCheckBox("Hot-reload cogs when Live Sync pushes changes")
        self.hot.setChecked(s.hot_reload)
        for label, w in (("Bot name", self.name), ("Token variable", self.token_env), ("Dev server ID", self.guild),
                         ("Prefix", self.prefix), ("Status text", self.status), ("Activity", self.activity),
                         ("Log level", self.level)):
            form.addRow(label, w)
        lay.addLayout(form)
        box = QGroupBox("Extra privileged intents (nodes add the ones they need automatically)")
        bl = QVBoxLayout(box)
        self.intents = {}
        for i in INTENTS:
            cb = QCheckBox(i)
            cb.setChecked(i in s.extra_intents)
            self.intents[i] = cb
            bl.addWidget(cb)
        lay.addWidget(box)
        lay.addWidget(QLabel("Extra pip requirements (one per line)"))
        self.reqs = QPlainTextEdit("\n".join(s.extra_requirements))
        self.reqs.setFixedHeight(70)
        lay.addWidget(self.reqs)
        lay.addWidget(QLabel("Extra npm packages for the web dashboard (one per line, e.g. cheerio@^1.0.0)"))
        self.pkgs = QPlainTextEdit("\n".join(s.extra_packages))
        self.pkgs.setFixedHeight(60)
        lay.addWidget(self.pkgs)
        lay.addWidget(self.hot)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.setMinimumWidth(440)

    def apply(self):
        s = self.g.settings
        s.name = self.name.text().strip() or "My Bot"
        s.token_env = self.token_env.text().strip() or "DISCORD_TOKEN"
        s.dev_guild_id = self.guild.text().strip()
        s.command_prefix = self.prefix.text() or "!"
        s.status_text = self.status.text()
        s.activity_type = self.activity.currentText()
        s.log_level = self.level.currentText()
        s.extra_intents = [k for k, cb in self.intents.items() if cb.isChecked()]
        s.extra_requirements = [x.strip() for x in self.reqs.toPlainText().splitlines() if x.strip()]
        s.extra_packages = [x.strip() for x in self.pkgs.toPlainText().splitlines() if x.strip()]
        s.hot_reload = self.hot.isChecked()


class TargetDialog(QDialog):
    def __init__(self, graph: Graph, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Deploy target")
        self.t = graph.target
        t = self.t
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.mode = QComboBox()
        self.mode.addItems(["local", "ssh"])
        self.mode.setCurrentText(t.mode)
        self.path = QLineEdit(t.path)
        self.path.setPlaceholderText("blank = ~/botstudio-bots/<bot> (or %USERPROFILE%\\BotStudio\\<bot>)")
        self.init = QComboBox()
        self.init.addItems(INIT_CHOICES)
        self.init.setCurrentText(t.init)
        self.name = QLineEdit(t.service_name)
        self.name.setPlaceholderText(graph.settings.slug)
        self.user = QLineEdit(t.run_user)
        self.user.setPlaceholderText("blank = current user (system services: dedicated account recommended)")
        self.python = QLineEdit(t.python)
        self.node = QLineEdit(t.node)
        self.node.setToolTip("Web dashboard: node executable, used only when no suitable Node.js is found and downloads are off.")
        self.portable = QCheckBox("Web dashboard: download a private Node.js 22 into the bot folder if the system one is missing or too old")
        self.portable.setChecked(t.portable_node)
        self.sudo = QCheckBox("Use sudo for service control (non-interactive, needs NOPASSWD or root)")
        self.sudo.setChecked(t.use_sudo)
        self.host = QLineEdit(t.host)
        self.ssh_user = QLineEdit(t.ssh_user)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(t.ssh_port)
        self.key = QLineEdit(t.ssh_key)
        self.key.setPlaceholderText("blank = ssh-agent / ~/.ssh/config")
        for label, w in (("Where", self.mode), ("Install folder", self.path), ("Init system", self.init),
                         ("Service name", self.name), ("Run as user", self.user), ("Python", self.python),
                         ("Node.js (dashboard)", self.node)):
            form.addRow(label, w)
        lay.addLayout(form)
        lay.addWidget(self.sudo)
        lay.addWidget(self.portable)
        self.ssh_box = QGroupBox("SSH (Linux hosts, key authentication)")
        sf = QFormLayout(self.ssh_box)
        for label, w in (("Host", self.host), ("User", self.ssh_user), ("Port", self.port), ("Key file", self.key)):
            sf.addRow(label, w)
        lay.addWidget(self.ssh_box)
        lay.addWidget(QLabel("Extra .env values (KEY=VALUE per line — the bot token is never saved)"))
        self.env = QPlainTextEdit("\n".join(f"{k}={v}" for k, v in t.env.items()))
        self.env.setFixedHeight(80)
        lay.addWidget(self.env)
        self.hint = QLabel(f"Auto-detect on this machine: {detect_local_init()}")
        self.hint.setObjectName("muted")
        lay.addWidget(self.hint)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.mode.currentTextChanged.connect(self._mode)
        self._mode(t.mode)
        self.setMinimumWidth(520)

    def _mode(self, m):
        self.ssh_box.setVisible(m == "ssh")

    def apply(self):
        t = self.t
        t.mode, t.path, t.init = self.mode.currentText(), self.path.text().strip(), self.init.currentText()
        t.service_name, t.run_user = self.name.text().strip(), self.user.text().strip()
        t.python = self.python.text().strip() or "python3"
        t.node = self.node.text().strip() or "node"
        t.portable_node = self.portable.isChecked()
        t.use_sudo = self.sudo.isChecked()
        t.host, t.ssh_user, t.ssh_port, t.ssh_key = (self.host.text().strip(), self.ssh_user.text().strip(),
                                                    self.port.value(), self.key.text().strip())
        env = {}
        for ln in self.env.toPlainText().splitlines():
            if "=" in ln and not ln.strip().startswith("#"):
                k, v = ln.split("=", 1)
                env[k.strip()] = v.strip()
        t.env = env
