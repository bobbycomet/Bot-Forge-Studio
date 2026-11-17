"""Deploy, live-sync and control a bot on this machine or over SSH.

Remote targets are Linux hosts reached with the system `ssh` binary (key auth,
BatchMode) — no extra Python dependencies, and it honours ~/.ssh/config.
"""
from __future__ import annotations

import io
import json
import os
import posixpath
import shlex
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Callable, Optional

from .model import DeployTarget, Graph
from .project import BuildResult
from . import elevate, projectdir, services

Log = Callable[[str], None]


# ----------------------------------------------------------------------------
# Runners
# ----------------------------------------------------------------------------

class Runner:
    windows = False

    def run(self, cmd: str, cwd: str | None = None, timeout: float = 600, input: bytes | None = None) -> tuple[int, str]:
        raise NotImplementedError

    def run_elevated(self, cmd: str, cwd: str | None = None, timeout: float = 600) -> tuple[int, str]:
        """Run *cmd* with administrator rights (pkexec / sudo on Linux, UAC on Windows). Default: not supported."""
        return self.run(cmd, cwd=cwd, timeout=timeout)

    def put_files(self, dest: str, files: dict[str, bytes]) -> None:
        raise NotImplementedError

    def read(self, path: str) -> Optional[str]:
        raise NotImplementedError

    def remove(self, paths: list[str]) -> None:
        raise NotImplementedError

    def home(self) -> str:
        raise NotImplementedError


class LocalRunner(Runner):
    def __init__(self):
        self.windows = sys.platform.startswith("win")

    def run(self, cmd, cwd=None, timeout=600, input=None):
        try:
            p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, timeout=timeout, input=input,
                               env=elevate.clean_env(), **elevate.popen_flags())
        except subprocess.TimeoutExpired:
            return 124, "timed out"
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")

    def run_elevated(self, cmd, cwd=None, timeout=600):
        if self.windows:
            return elevate.run_windows_elevated(cmd, timeout)          # UAC prompt
        return self.run(elevate.elevate_linux(cmd), cwd=cwd, timeout=timeout)   # pkexec / sudo -n / already root

    def put_files(self, dest, files):
        for rel, data in files.items():
            target = Path(dest) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, target)  # atomic: the live bot never sees half a file
            if rel.endswith((".sh", "/run")):
                target.chmod(0o755)

    def read(self, path):
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return None

    def remove(self, paths):
        for p in paths:
            try:
                Path(p).unlink()
            except OSError:
                pass

    def home(self):
        return str(Path.home())


class SSHRunner(Runner):
    def __init__(self, t: DeployTarget):
        self.t = t
        self._ssh = ["ssh", "-p", str(t.ssh_port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                     "-o", "StrictHostKeyChecking=accept-new"]
        if t.ssh_key:
            self._ssh += ["-i", os.path.expanduser(t.ssh_key)]
        self._dest = f"{t.ssh_user}@{t.host}" if t.ssh_user else t.host

    def run(self, cmd, cwd=None, timeout=600, input=None):
        remote = f"cd {shlex.quote(cwd)} && {cmd}" if cwd else cmd
        try:
            p = subprocess.run(self._ssh + [self._dest, remote], capture_output=True, timeout=timeout, input=input)
        except FileNotFoundError:
            return 127, "The `ssh` command was not found. Install an OpenSSH client."
        except subprocess.TimeoutExpired:
            return 124, "timed out"
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")

    def put_files(self, dest, files):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for rel, data in files.items():
                info = tarfile.TarInfo(rel)
                info.size = len(data)
                info.mtime = int(time.time())
                info.mode = 0o755 if rel.endswith((".sh", "/run")) else 0o644
                tar.addfile(info, io.BytesIO(data))
        rc, out = self.run(f"mkdir -p {shlex.quote(dest)} && tar -xf - -C {shlex.quote(dest)}", input=buf.getvalue())
        if rc != 0:
            raise RuntimeError(out.strip() or f"upload failed (rc={rc})")

    def read(self, path):
        rc, out = self.run(f"cat {shlex.quote(path)}")
        return out if rc == 0 else None

    def remove(self, paths):
        if paths:
            self.run("rm -f " + " ".join(shlex.quote(p) for p in paths))

    def home(self):
        rc, out = self.run("echo $HOME")
        return out.strip() if rc == 0 and out.strip() else "~"


# ----------------------------------------------------------------------------
# .env merging — never clobber what the server already has
# ----------------------------------------------------------------------------

def merge_env(existing: str | None, wanted: dict[str, str]) -> str:
    lines = (existing or "").splitlines()
    present = set()
    for i, ln in enumerate(lines):
        if "=" in ln and not ln.lstrip().startswith("#"):
            k = ln.split("=", 1)[0].strip()
            present.add(k)
            if k in wanted and wanted[k] != "" and ln.split("=", 1)[1].strip() == "":
                lines[i] = f"{k}={wanted[k]}"  # fill blanks, keep existing values
    for k, v in wanted.items():
        if k not in present:
            lines.append(f"{k}={v}")
    return "\n".join(lines).rstrip("\n") + "\n"


# ----------------------------------------------------------------------------
# Manager
# ----------------------------------------------------------------------------

class BotManager:
    def __init__(self, graph: Graph):
        self.graph = graph

    @classmethod
    def for_folder(cls, folder) -> "BotManager":
        """A manager for a project folder (saved by Studio, or deployed): loads its graph, or — for a deployed bot
        that never stored one — a minimal graph that is enough to start / stop / restart it."""
        d = Path(folder).expanduser()
        try:
            graph = Graph.load(Graph.resolve_project_path(str(d)))
        except projectdir.NoGraphError:
            graph = projectdir.graph_from_deployed(d)
        projectdir.adopt_folder(graph, d)       # the folder you opened is the one that is controlled, wherever it moved
        return cls(graph)

    @property
    def remote(self) -> bool:
        return self.target.mode != "local"

    # -- target helpers ------------------------------------------------------------
    @property
    def target(self) -> DeployTarget:
        return self.graph.target

    @property
    def runner(self) -> Runner:
        return LocalRunner() if self.target.mode == "local" else SSHRunner(self.target)

    @property
    def init(self) -> str:
        return services.resolve_init(self.target)

    @property
    def name(self) -> str:
        return services.service_name(self.graph.settings, self.target)

    def install_path(self, runner: Runner | None = None) -> str:
        t = self.target
        if t.path.strip():
            return t.path.strip()
        r = runner or self.runner
        base = r.home()
        if self.init == "windows":
            return os.path.join(base, "BotStudio", self.graph.settings.slug)
        return posixpath.join(base, "botstudio-bots", self.graph.settings.slug)

    def _join(self, *parts: str) -> str:
        return os.path.join(*parts) if self.init == "windows" else posixpath.join(*parts)

    def _graph_bytes(self) -> bytes:
        """The editor graph as project.bstudio, so the install folder can be reopened
        in Bot Forge Studio (File → Open) and controlled from the manager panel again.
        The bot token is never part of the graph (see Graph.to_dict)."""
        return json.dumps(self.graph.to_dict(), indent=2, ensure_ascii=False).encode("utf-8")

    def _project_files(self) -> dict[str, bytes]:
        """project.bstudio + the .botstudio.json marker that makes the folder detectable and reopenable."""
        return {projectdir.GRAPH_NAME: self._graph_bytes(), projectdir.MARKER: projectdir.marker_bytes(self.graph)}

    # -- deploy ----------------------------------------------------------------------
    def deploy(self, build: BuildResult, token: str = "", log: Log = print, start: bool = True) -> bool:
        """Full install: upload project, create venv, install deps, register the service."""
        if not build.ok:
            log("Build has errors — fix them first.")
            return False
        r, path, init = self.runner, None, self.init
        path = self.install_path(r)
        log(f"Target: {self.target.mode} · {path} · init={init}")
        try:
            files = {k: v.encode("utf-8") for k, v in build.files.items()}
            svc = services.generate(self.graph.settings, self.target, path, init, self.target.run_user, build.entry,
                                    dashboard=build.dashboard)
            files.update({k: v.encode("utf-8") for k, v in svc.items()})
            files.update(self._project_files())
            log(f"Uploading {len(files)} files …")
            r.put_files(path, files)

            wanted = {self.graph.settings.token_env: token}
            if self.graph.settings.dev_guild_id:
                wanted["GUILD_ID"] = self.graph.settings.dev_guild_id
            wanted.update({k: v for k, (v, _) in build.env.items() if v})
            wanted.update(self.target.env)
            env_path = self._join(path, ".env")
            r.put_files(path, {".env": merge_env(r.read(env_path), wanted).encode("utf-8")})
            if init != "windows":
                r.run(f"chmod 600 {shlex.quote(env_path)}")

            log("Installing (venv, requirements" + (", dashboard: private Node.js + npm" if build.dashboard else "")
                + ", service) — this can take a minute …")
            if init == "windows":
                cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -File "{self._join(path, "install_service.ps1")}"'
            else:
                cmd = f"bash {shlex.quote(self._join(path, 'install.sh'))}"
            if init == "windows" and not elevate.is_admin() and not self.remote:
                log("Installing the Windows service / startup task needs administrator rights — approve the Windows (UAC) prompt …")
                rc, out = r.run_elevated(cmd, cwd=path, timeout=900)
            else:
                rc, out = r.run(cmd, cwd=path, timeout=900)
            log(out.strip()[-1500:])
            if rc != 0:
                log(f"Install script failed (exit {rc})." + (" The elevation prompt was cancelled." if rc == elevate.ELEVATION_CANCELLED else ""))
                return False
        except Exception as e:  # surface SSH/IO errors in the log instead of crashing the UI
            log(f"Deploy failed: {e}")
            return False
        if start:
            token_set = bool(token) or self._env_has_token(r, path)
            if not token_set:
                log("No token found in .env — add it, then press Start.")
                return True
            rc, out = self.control("restart", log=log)
            return rc == 0
        return True

    def _env_has_token(self, r: Runner, path: str) -> bool:
        text = r.read(self._join(path, ".env")) or ""
        key = self.graph.settings.token_env
        for ln in text.splitlines():
            if ln.startswith(key + "="):
                return bool(ln.split("=", 1)[1].strip())
        return False

    # -- live update ------------------------------------------------------------------
    def live_sync(self, build: BuildResult, log: Log = print) -> dict:
        """Push only changed files; the running bot hot-reloads cogs by itself."""
        result = {"changed": [], "removed": [], "restart_required": False, "ok": False}
        if not build.ok:
            log("Build has errors — nothing was pushed.")
            return result
        r = self.runner
        path = self.install_path(r)
        try:
            old_text = r.read(self._join(path, "manifest.json"))
            old = json.loads(old_text) if old_text else None
            if old is None:
                log("Nothing deployed here yet — run Deploy first.")
                return result
            # Keep the reopenable project file in step with the editor, even when no
            # generated file changed (it is not part of the manifest).
            try:
                r.put_files(path, self._project_files())
            except Exception as e:
                log(f"Note: could not update project.bstudio on the target: {e}")
            new = build.manifest
            old_files = old.get("files", {})
            changed = [p for p, h in new["files"].items() if old_files.get(p) != h]
            removed = [p for p in old_files if p not in new["files"]]
            result.update(changed=changed, removed=removed)
            if not changed and not removed:
                log("Already up to date.")
                result["ok"] = True
                return result
            data = {p: build.files[p].encode("utf-8") for p in changed if p != "manifest.json"}
            if data:
                r.put_files(path, data)
            if removed:
                r.remove([self._join(path, p) for p in removed])
            if "dashboard/package.json" in changed:
                log("Dashboard dependencies changed — running npm install (private Node.js if needed) …")
                if self.init == "windows":
                    cmd = f'powershell -NoProfile -ExecutionPolicy Bypass -File "{self._join(path, "dashboard", "deps.ps1")}"'
                else:
                    cmd = f"bash {shlex.quote(self._join(path, 'dashboard', 'deps.sh'))}"
                rc, out = r.run(cmd, cwd=path, timeout=900)
                log(out.strip()[-800:])
            if "requirements.txt" in changed:
                log("Requirements changed — installing …")
                pip = self._join(path, "venv", "Scripts", "pip.exe") if self.init == "windows" \
                    else self._join(path, "venv", "bin", "pip")
                rc, out = r.run(f'"{pip}" install -r "{self._join(path, "requirements.txt")}" -q', timeout=600)
                log(out.strip()[-800:])
            r.put_files(path, {"manifest.json": build.files["manifest.json"].encode("utf-8")})  # last: it triggers the reload
            result["restart_required"] = old.get("restart_sig") != new["restart_sig"]
            result["ok"] = True
            log(f"Pushed {len(changed)} changed file(s)" + (f", removed {len(removed)}" if removed else "") + ".")
            if result["restart_required"]:
                log("Core files changed (bot.py / runtime / bundle helpers) — restart the bot to apply them.")
            else:
                log("The running bot will hot-reload the changes within a couple of seconds.")
        except Exception as e:
            log(f"Live sync failed: {e}")
        return result

    # -- control -----------------------------------------------------------------------------
    def control(self, action: str, log: Log | None = None) -> tuple[int, str]:
        """Run start/stop/restart/status/enable/disable against the bot service.

        On Linux systemd this uses pkexec (GUI elevation) when available.
        If a privileged start/restart fails, falls back to process-mode so
        the Bot tab can always launch the bot for testing.
        """
        r = self.runner
        path = self.install_path(r)
        use_sudo = self.target.use_sudo
        remote = self.remote

        # A just-deployed or edited .service must be re-read before start/restart/enable. For system services that is
        # one elevated shell (one password prompt), not two.
        combined = self.init == "systemd-system" and action in ("start", "restart", "enable")
        if not combined and action in ("start", "restart", "enable"):
            reload_cmd = services.daemon_reload_command(self.init, use_sudo, remote)
            if reload_cmd:
                rc_r, out_r = r.run(reload_cmd, cwd=None if self.init == "windows" else path, timeout=30)
                if log and (out_r.strip() or rc_r != 0):
                    log(f"$ {reload_cmd}\n{out_r.strip()}" if out_r.strip() else f"$ {reload_cmd}  (exit {rc_r})")

        cmd = (services.systemd_reload_and(action, self.name, use_sudo, remote) if combined
               else services.control_command(self.init, self.name, action, path, use_sudo, remote))
        if self.init == "windows" and not remote:
            rc, out = self._windows_control(r, path, action, log)
        else:
            rc, out = r.run(cmd, cwd=None if self.init == "windows" else path, timeout=120)
            if log:
                log(f"$ {cmd}\n{out.strip()}" if out.strip() else f"$ {cmd}  (exit {rc})")
            if rc in (126, 127) and cmd.startswith("pkexec") and log:
                log("Authentication was cancelled, or no PolicyKit agent is running in this session.")

        # A bot that was started in process mode (no service manager) must still be stoppable.
        if rc != 0 and action == "stop" and self.init not in ("process", "windows"):
            rc2, out2 = r.run(services.control_command("process", self.name, "stop", path, False), cwd=path, timeout=15)
            if rc2 == 0 and "stopped" in out2.lower():
                if log:
                    log(f"[process mode]\n{out2.strip()}")
                return rc2, out2

        # Fallback: if the real service manager refused start/restart,
        # launch in process mode so the user can still test the bot.
        if rc != 0 and action in ("start", "restart") and self.init not in ("process", "windows"):
            if log:
                log("Service control failed — falling back to process mode (no root required).")
            # Ensure botctl.sh exists (older deploys may lack it)
            botctl = self._join(path, "botctl.sh")
            if not r.read(botctl):
                # Synthesize a minimal process starter
                py = services.venv_python(path, self.init)
                fallback = (
                    f'cd {shlex.quote(path)} && mkdir -p logs && '
                    f'( [ -f bot.pid ] && kill -0 "$(cat bot.pid)" 2>/dev/null && echo already running ) || '
                    f'( nohup {shlex.quote(py)} bot.py >> logs/process.out 2>&1 & echo $! > bot.pid && echo started )'
                )
                rc2, out2 = r.run(fallback, cwd=path, timeout=30)
            else:
                fb_cmd = services.process_start_command(path, "process")
                if action == "restart":
                    # stop first, then start
                    stop_cmd = services.control_command("process", self.name, "stop", path, False)
                    r.run(stop_cmd, cwd=path, timeout=15)
                    time.sleep(1)
                rc2, out2 = r.run(fb_cmd, cwd=path, timeout=30)
            if log:
                log(f"[process fallback]\n{out2.strip()}" if out2.strip() else f"[process fallback] exit {rc2}")
            return rc2, out2

        return rc, out

    def _windows_control(self, r: Runner, path: str, action: str, log: Log | None) -> tuple[int, str]:
        """Windows: services and startup tasks are administrator-owned, so changing them goes through UAC.
        Plain process mode and read-only probes never prompt."""
        rc_m, mode = r.run(services.windows_botctl(path, "mode"), timeout=30)
        cmd = services.windows_botctl(path, action)
        if services.windows_needs_elevation(mode, action) and not elevate.is_admin():
            if log:
                log("This bot runs as a Windows service / startup task — approve the Windows (UAC) prompt to continue …")
            rc, out = r.run_elevated(cmd, timeout=180)
            if rc == elevate.ELEVATION_CANCELLED and log:
                log("The elevation prompt was cancelled; nothing was changed.")
        else:
            rc, out = r.run(cmd, timeout=120)
        if log:
            log(f"$ {cmd}\n{out.strip()}" if out.strip() else f"$ {cmd}  (exit {rc})")
        return rc, out

    def status(self) -> dict:
        """{'state': 'active'|'inactive'|'unknown', 'info': {...status.json...}, 'error': str}"""
        r = self.runner
        path = self.install_path(r)
        info = {}
        try:
            # Avoid the daemon-reload / fallback path for a pure status probe.
            cmd = services.control_command(self.init, self.name, "status", path, self.target.use_sudo, self.remote)
            rc, out = r.run(cmd, cwd=None if self.init == "windows" else path, timeout=30)
            active = services.is_active_from_status(self.init, rc, out)

            # If the service manager says inactive, also check process-mode PID
            # so a fallback-launched bot still shows as running in the UI.
            if not active and self.init not in ("process", "windows"):
                pcmd = services.control_command("process", self.name, "status", path, False)
                prc, pout = r.run(pcmd, cwd=path, timeout=10)
                if services.is_active_from_status("process", prc, pout):
                    active = True
                    info["mode"] = "process"

            txt = r.read(self._join(path, "status.json"))
            if txt:
                try:
                    info.update(json.loads(txt))
                    if info.get("now") and time.time() - float(info["now"]) > 30 and self.target.mode == "local":
                        info["stale"] = True
                except ValueError:
                    pass
            return {"state": "active" if active else "inactive", "info": info, "error": ""}
        except Exception as e:
            return {"state": "unknown", "info": info, "error": str(e)}

    def logs(self, lines: int = 200) -> str:
        r = self.runner
        path = self.install_path(r)
        log_file = self._join(path, "logs", "bot.log")
        if self.init == "windows" or self.target.mode == "local":
            text = r.read(log_file)
            return "\n".join(text.splitlines()[-lines:]) if text else "(no log yet)"
        rc, out = r.run(f"tail -n {int(lines)} {shlex.quote(log_file)}")
        return out if rc == 0 else "(no log yet)"
