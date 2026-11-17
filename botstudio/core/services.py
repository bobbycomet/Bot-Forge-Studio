"""Service definitions per init system: systemd (system/user), runit, OpenRC, Windows, plain process."""
from __future__ import annotations

import ntpath
import os
import posixpath
import shutil
import sys

from . import elevate
from .model import DeployTarget, ProjectSettings

INIT_CHOICES = ("auto", "systemd-system", "systemd-user", "runit", "openrc", "windows", "process")


def detect_local_init() -> str:
    """Pick a sensible default for the local machine.

    Prefer user-level or process mode when we are not root so the Bot tab
    can start/stop without requiring elevation on every click.
    """
    if sys.platform.startswith("win"):
        return "windows"
    # Non-root: prefer systemd --user when available, else plain process.
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        if shutil.which("systemctl"):
            return "systemd-user"
        return "process"
    if shutil.which("systemctl"):
        return "systemd-system"
    if shutil.which("sv") and (shutil.which("runsvdir") or shutil.which("chpst")):
        return "runit"
    if shutil.which("rc-service"):
        return "openrc"
    return "process"


def resolve_init(target: DeployTarget) -> str:
    if target.init != "auto":
        return target.init
    return detect_local_init() if target.mode == "local" else "systemd-system"


def is_windows(init: str) -> bool:
    return init == "windows"


def service_name(settings: ProjectSettings, target: DeployTarget) -> str:
    return (target.service_name or settings.slug).strip() or "bot"


def _pj(init: str):
    return ntpath if is_windows(init) else posixpath


def venv_python(path: str, init: str) -> str:
    if is_windows(init):
        return ntpath.join(path, "venv", "Scripts", "python.exe")
    return posixpath.join(path, "venv", "bin", "python")


# ---- web dashboard (discord.js): private Node.js + npm install, "venv-style" per bot -----------------------------

DEPS_SH = """#!/usr/bin/env bash
# Makes sure a suitable Node.js exists for this bot, then installs its npm dependencies into ./node_modules.
# Order of preference: private copy in .runtime/ -> system node -> download a private Node.js 22 (checksum verified).
# Safe to re-run: npm only runs again when package.json changed.
set -euo pipefail
cd "$(dirname "$0")"
MIN_MAJOR=22; MIN_MINOR=13
PORTABLE=__PORTABLE__
mkdir -p .runtime

node_ok() {
  local v ma rest mi
  v="$("$1" --version 2>/dev/null)" || return 1
  v="${v#v}"; ma="${v%%.*}"; rest="${v#*.}"; mi="${rest%%.*}"
  [ "$ma" -gt "$MIN_MAJOR" ] || { [ "$ma" -eq "$MIN_MAJOR" ] && [ "$mi" -ge "$MIN_MINOR" ]; }
}

NODE=""
if [ -x .runtime/node/bin/node ] && node_ok "$PWD/.runtime/node/bin/node"; then NODE="$PWD/.runtime/node/bin/node"
elif command -v node >/dev/null 2>&1 && node_ok "$(command -v node)"; then NODE="$(command -v node)"
fi

if [ -z "$NODE" ]; then
  if [ "$PORTABLE" != "1" ]; then
    echo "Node.js >= $MIN_MAJOR.$MIN_MINOR is required but was not found (private download is disabled)." >&2; exit 1
  fi
  case "$(uname -m)" in
    x86_64|amd64) ARCH=x64 ;; aarch64|arm64) ARCH=arm64 ;; armv7l) ARCH=armv7l ;;
    *) echo "No official Node.js build for $(uname -m); install Node.js $MIN_MAJOR.$MIN_MINOR+ yourself." >&2; exit 1 ;;
  esac
  echo "Downloading a private Node.js 22 (linux-$ARCH) into .runtime/ ..."
  BASE="https://nodejs.org/dist/latest-v22.x"
  SUMS="$(curl -fsSL "$BASE/SHASUMS256.txt")"
  FILE="$(printf '%s\n' "$SUMS" | awk '{print $2}' | grep -E "^node-v[0-9.]+-linux-${ARCH}\\.tar\\.gz$" | head -n1)"
  [ -n "$FILE" ] || { echo "Could not find a Node.js build for linux-$ARCH" >&2; exit 1; }
  curl -fsSL "$BASE/$FILE" -o ".runtime/$FILE"
  EXPECTED="$(printf '%s\n' "$SUMS" | awk -v f="$FILE" '$2==f {print $1}')"
  ACTUAL="$(sha256sum ".runtime/$FILE" | awk '{print $1}')"
  if [ -z "$EXPECTED" ] || [ "$EXPECTED" != "$ACTUAL" ]; then
    echo "Checksum mismatch for $FILE - refusing to use it." >&2; rm -f ".runtime/$FILE"; exit 1
  fi
  rm -rf .runtime/node; mkdir -p .runtime/node
  tar -xzf ".runtime/$FILE" -C .runtime/node --strip-components=1
  rm -f ".runtime/$FILE"
  NODE="$PWD/.runtime/node/bin/node"
fi

echo "$NODE" > .runtime/node_path
export PATH="$(dirname "$NODE"):$PATH"
echo "Using Node.js $("$NODE" --version) at $NODE"

NEW="$(sha256sum package.json | awk '{print $1}')"
OLD="$(cat .runtime/pkg.sha 2>/dev/null || true)"
if [ "$NEW" != "$OLD" ] || [ ! -d node_modules ]; then
  echo "Installing npm dependencies ..."
  npm install --omit=dev --no-audit --no-fund --loglevel=error
  echo "$NEW" > .runtime/pkg.sha
else
  echo "npm dependencies are up to date."
fi
"""

DEPS_PS1 = """# Makes sure a suitable Node.js exists for this bot, then installs its npm dependencies into .\\node_modules.
$ErrorActionPreference = "Stop"
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Dir
$Portable = __PORTABLE__
$MinMajor = 22; $MinMinor = 13
New-Item -ItemType Directory -Force -Path ".runtime" | Out-Null

function Test-Node($exe) {
    try {
        $v = ((& $exe --version) -replace '^v', '').Split('.')
        return ([int]$v[0] -gt $MinMajor) -or (([int]$v[0] -eq $MinMajor) -and ([int]$v[1] -ge $MinMinor))
    } catch { return $false }
}

$Node = $null
$private = Join-Path $Dir ".runtime\\node\\node.exe"
if ((Test-Path $private) -and (Test-Node $private)) { $Node = $private }
else {
    $sys = Get-Command node -ErrorAction SilentlyContinue
    if ($sys -and (Test-Node $sys.Source)) { $Node = $sys.Source }
}

if (-not $Node) {
    if (-not $Portable) { throw "Node.js >= $MinMajor.$MinMinor is required but was not found (private download is disabled)." }
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "arm64" } else { "x64" }
    Write-Host "Downloading a private Node.js 22 (win-$arch) into .runtime ..."
    $base = "https://nodejs.org/dist/latest-v22.x"
    $sums = (Invoke-WebRequest -UseBasicParsing "$base/SHASUMS256.txt").Content -split "`n"
    $line = $sums | Where-Object { $_ -match "node-v[0-9.]+-win-$arch\\.zip\\s*$" } | Select-Object -First 1
    if (-not $line) { throw "Could not find a Node.js build for win-$arch" }
    $parts = $line.Trim() -split "\\s+"
    $expected = $parts[0]; $file = $parts[1]
    $zip = Join-Path $Dir ".runtime\\$file"
    Invoke-WebRequest -UseBasicParsing "$base/$file" -OutFile $zip
    if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLower() -ne $expected.ToLower()) { Remove-Item $zip -Force; throw "Checksum mismatch for $file" }
    if (Test-Path ".runtime\\node") { Remove-Item ".runtime\\node" -Recurse -Force }
    Expand-Archive -Path $zip -DestinationPath ".runtime" -Force
    Rename-Item (Join-Path $Dir ".runtime\\" + [IO.Path]::GetFileNameWithoutExtension($file)) "node"
    Remove-Item $zip -Force
    $Node = $private
}

Set-Content -Path ".runtime\\node_path.txt" -Value $Node -Encoding ascii
$env:PATH = "$(Split-Path $Node);$env:PATH"
Write-Host "Using Node.js $(& $Node --version) at $Node"

$new = (Get-FileHash "package.json" -Algorithm SHA256).Hash
$stamp = ".runtime\\pkg.sha"
$old = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { "" }
if (($new -ne $old) -or -not (Test-Path "node_modules")) {
    Write-Host "Installing npm dependencies ..."
    $npm = Join-Path (Split-Path $Node) "npm.cmd"
    & $npm install --omit=dev --no-audit --no-fund --loglevel=error
    if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    Set-Content -Path $stamp -Value $new -Encoding ascii
} else { Write-Host "npm dependencies are up to date." }
"""


def generate(settings: ProjectSettings, target: DeployTarget, install_path: str, init: str,
             run_user: str = "", entry: str = "bot.py", dashboard: bool = False) -> dict[str, str]:
    files = _generate(settings, target, install_path, init, run_user)
    if dashboard:
        portable = bool(getattr(target, "portable_node", True))
        if is_windows(init):
            files["dashboard/deps.ps1"] = DEPS_PS1.replace("__PORTABLE__", "$true" if portable else "$false")
            files["install_service.ps1"] = files["install_service.ps1"].replace(
                "& \"$Dir\\venv\\Scripts\\pip.exe\" install -r \"$Dir\\requirements.txt\" -q",
                "& \"$Dir\\venv\\Scripts\\pip.exe\" install -r \"$Dir\\requirements.txt\" -q\n"
                "& powershell -NoProfile -ExecutionPolicy Bypass -File \"$Dir\\dashboard\\deps.ps1\"\n"
                "if ($LASTEXITCODE -ne 0) {{ throw \"dashboard: Node.js / npm setup failed\" }}".replace("{{", "{").replace("}}", "}"), 1)
        else:
            files["dashboard/deps.sh"] = DEPS_SH.replace("__PORTABLE__", "1" if portable else "0")
            files["install.sh"] = files["install.sh"].replace(
                "./venv/bin/pip install -r requirements.txt -q\n",
                "./venv/bin/pip install -r requirements.txt -q\nchmod +x dashboard/deps.sh\n./dashboard/deps.sh\n", 1)
    if entry != "bot.py":
        files = {k: v.replace("bot.py", entry) for k, v in files.items()}
    return files


def _generate(settings: ProjectSettings, target: DeployTarget, install_path: str, init: str,
              run_user: str = "") -> dict[str, str]:
    """Return {relative path: text} for the service definition, install script and control script."""
    name = service_name(settings, target)
    desc = f"{settings.name} (Discord bot, built with Bot Forge Studio)"
    py = venv_python(install_path, init)
    out: dict[str, str] = {}

    if init == "systemd-system":
        protect_home = "true" if not install_path.startswith("/home") else "false"
        user_lines = f"User={run_user}\nGroup={run_user}\n" if run_user else ""
        out[f"{name}.service"] = f"""[Unit]
Description={desc}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
{user_lines}WorkingDirectory={install_path}
ExecStart={py} {install_path}/bot.py

Restart=on-failure
RestartSec=8
StartLimitIntervalSec=60
StartLimitBurst=5

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome={protect_home}
ReadWritePaths={install_path}
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true

StandardOutput=journal
StandardError=journal
SyslogIdentifier={name}

[Install]
WantedBy=multi-user.target
"""
    elif init == "systemd-user":
        out[f"{name}.service"] = f"""[Unit]
Description={desc}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={install_path}
ExecStart={py} {install_path}/bot.py
Restart=on-failure
RestartSec=8
StandardOutput=journal
StandardError=journal
SyslogIdentifier={name}

[Install]
WantedBy=default.target
"""
    elif init == "runit":
        chpst = f"chpst -u {run_user} " if run_user else ""
        out[f"runit/{name}/run"] = f"""#!/bin/sh
exec 2>&1
cd {install_path}
exec {chpst}{py} {install_path}/bot.py
"""
        out[f"runit/{name}/log/run"] = f"""#!/bin/sh
mkdir -p {install_path}/logs/runit
exec svlogd -tt {install_path}/logs/runit
"""
    elif init == "openrc":
        user_line = f'command_user="{run_user}"\n' if run_user else ""
        out[f"{name}.openrc"] = f"""#!/sbin/openrc-run
name="{settings.name}"
description="{desc}"
supervisor="supervise-daemon"
command="{py}"
command_args="{install_path}/bot.py"
{user_line}directory="{install_path}"
respawn_delay=8
respawn_max=5
respawn_period=60
output_log="{install_path}/logs/openrc.out"
error_log="{install_path}/logs/openrc.err"

depend() {{
    need net
}}
"""
    elif init == "windows":
        out["run_bot.bat"] = f"""@echo off
cd /d "{install_path}"
"{py}" bot.py
"""
        out["install_service.ps1"] = f"""# Run from an elevated PowerShell. Uses NSSM when available, otherwise a Scheduled Task.
$ErrorActionPreference = "Stop"
$Name = "{name}"
$Dir  = "{install_path}"
$Py   = "{py}"
New-Item -ItemType Directory -Force -Path "$Dir\\logs", "$Dir\\data" | Out-Null
if (-not (Test-Path "$Dir\\venv\\Scripts\\python.exe")) {{
    $want = "{'' if (target.python or 'python3') in ('python3', 'python') else target.python}"
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    $sysPy = Get-Command python -ErrorAction SilentlyContinue
    if ($want) {{ & $want -m venv "$Dir\\venv" }}
    elseif ($launcher) {{ & py -3 -m venv "$Dir\\venv" }}
    elseif ($sysPy -and ($sysPy.Source -notlike "*\\WindowsApps\\*")) {{ & python -m venv "$Dir\\venv" }}
    else {{ throw "Python 3.10 or newer was not found. Install it from python.org (or run: winget install Python.Python.3.12), then Deploy again." }}
    if (-not (Test-Path "$Dir\\venv\\Scripts\\python.exe")) {{ throw "Could not create the Python virtual environment." }}
}}
& "$Dir\\venv\\Scripts\\pip.exe" install -r "$Dir\\requirements.txt" -q
if (-not (Test-Path "$Dir\\.env")) {{ Copy-Item "$Dir\\.env.example" "$Dir\\.env"; Write-Host "Edit $Dir\\.env and add your token." }}
$nssm = Get-Command nssm -ErrorAction SilentlyContinue
if ($nssm) {{
    & nssm install $Name $Py "$Dir\\bot.py" | Out-Null
    & nssm set $Name AppDirectory $Dir | Out-Null
    & nssm set $Name AppStdout "$Dir\\logs\\service.out" | Out-Null
    & nssm set $Name AppStderr "$Dir\\logs\\service.err" | Out-Null
    & nssm set $Name AppExit Default Restart | Out-Null
    & nssm set $Name AppRestartDelay 8000 | Out-Null
    & nssm set $Name Start SERVICE_AUTO_START | Out-Null
}} else {{
    $action   = New-ScheduledTaskAction -Execute $Py -Argument "bot.py" -WorkingDirectory $Dir
    $trigger  = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force | Out-Null
}}
Write-Host "Installed '$Name'."
"""
        # Robust controller: NSSM → Scheduled Task → pure process mode (PID file).
        # Process mode needs no admin rights, so the Bot tab Start button always works.
        out["botctl.ps1"] = f"""param([Parameter(Mandatory=$true)][ValidateSet("start","stop","restart","status","enable","disable","mode")]$Action)
$ErrorActionPreference = "Continue"
$Name = "{name}"
$Dir  = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Dir) {{ $Dir = "{install_path}" }}
$Py   = "{py}"
$PidFile = Join-Path $Dir "bot.pid"
$LogFile = Join-Path $Dir "logs\\process.out"
New-Item -ItemType Directory -Force -Path (Join-Path $Dir "logs") | Out-Null

function Test-ProcessAlive {{
    if (-not (Test-Path $PidFile)) {{ return $false }}
    $procId = Get-Content $PidFile -ErrorAction SilentlyContinue
    if (-not $procId) {{ return $false }}
    $p = Get-Process -Id $procId -ErrorAction SilentlyContinue
    return ($null -ne $p)
}}

function Start-ProcessMode {{
    if (Test-ProcessAlive) {{ Write-Output "already running"; return }}
    $p = Start-Process -FilePath $Py -ArgumentList "bot.py" -WorkingDirectory $Dir `
        -RedirectStandardOutput $LogFile -RedirectStandardError $LogFile `
        -WindowStyle Hidden -PassThru
    $p.Id | Set-Content $PidFile
    Write-Output "started"
}}

function Stop-ProcessMode {{
    if (-not (Test-ProcessAlive)) {{ Write-Output "not running"; return }}
    $procId = Get-Content $PidFile
    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    # also kill any stray bot.py for this dir
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {{ $_.CommandLine -like "*bot.py*" -and $_.CommandLine -like "*$Dir*" }} |
        ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
    Write-Output "stopped"
}}

$nssm = Get-Command nssm -ErrorAction SilentlyContinue
$hasNssm = $false
if ($nssm) {{
    $st = & nssm status $Name 2>$null
    if ($LASTEXITCODE -eq 0 -or $st) {{ $hasNssm = $true }}
}}

$hasTask = $false
try {{
    $t = Get-ScheduledTask -TaskName $Name -ErrorAction Stop
    if ($t) {{ $hasTask = $true }}
}} catch {{}}

if ($Action -eq "mode") {{
    if ($hasNssm) {{ Write-Output "nssm" }} elseif ($hasTask) {{ Write-Output "task" }} else {{ Write-Output "process" }}
    exit 0
}}

if ($hasNssm) {{
    switch ($Action) {{
        "enable"  {{ & nssm set $Name Start SERVICE_AUTO_START; Write-Output "enabled" }}
        "disable" {{ & nssm set $Name Start SERVICE_DEMAND_START; Write-Output "disabled" }}
        "status"  {{
            $s = & nssm status $Name 2>$null
            if ($s -match "SERVICE_RUNNING|RUNNING") {{ Write-Output "active" }} else {{ Write-Output "inactive" }}
        }}
        default   {{
            $o = (& nssm $Action $Name 2>&1 | Out-String)
            if ($LASTEXITCODE -ne 0) {{ Write-Output $o; Write-Output "ACCESS_DENIED or failed (exit $LASTEXITCODE)"; exit 5 }}
            Write-Output $Action
        }}
    }}
}} elseif ($hasTask) {{
  try {{
    $ErrorActionPreference = "Stop"
    switch ($Action) {{
        "start"   {{ Start-ScheduledTask -TaskName $Name; Write-Output "started" }}
        "stop"    {{
            Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
            Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object {{ $_.CommandLine -like "*bot.py*" }} |
                ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
            Write-Output "stopped"
        }}
        "restart" {{ Stop-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue; Start-Sleep -Seconds 1; Start-ScheduledTask -TaskName $Name; Write-Output "restarted" }}
        "enable"  {{ Enable-ScheduledTask -TaskName $Name; Write-Output "enabled" }}
        "disable" {{ Disable-ScheduledTask -TaskName $Name; Write-Output "disabled" }}
        "status"  {{
            $st = (Get-ScheduledTask -TaskName $Name).State
            if ($st -eq "Running") {{ Write-Output "active" }} else {{ Write-Output "inactive" }}
        }}
    }}
  }} catch {{ Write-Output "ACCESS_DENIED: $($_.Exception.Message)"; exit 5 }}
}} else {{
    # Pure process mode – no admin required
    switch ($Action) {{
        "start"   {{ Start-ProcessMode }}
        "stop"    {{ Stop-ProcessMode }}
        "restart" {{ Stop-ProcessMode; Start-Sleep -Seconds 1; Start-ProcessMode }}
        "enable"  {{ Write-Output "process mode has no boot enable (use install_service.ps1 as admin)" }}
        "disable" {{ Write-Output "process mode has no boot disable" }}
        "status"  {{ if (Test-ProcessAlive) {{ Write-Output "active" }} else {{ Write-Output "inactive" }} }}
    }}
}}
"""
    else:  # process (or any non-Windows init that needs a fallback controller)
        pass

    # Always ship a process-mode controller on Unix so the Bot tab can fall back
    # even when the primary init is systemd/runit/openrc.
    if not is_windows(init):
        out["botctl.sh"] = f"""#!/usr/bin/env bash
# Process-mode controller (works without any init system / root).
# Also used as fallback when systemctl/sv/rc-service is unavailable or denied.
set -e
cd "$(dirname "$0")"
mkdir -p logs
PID=bot.pid
alive() {{ [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; }}
case "${{1:-status}}" in
  start)
    if alive; then echo "already running"; exit 0; fi
    nohup {py} bot.py >> logs/process.out 2>&1 &
    echo $! > "$PID"
    echo "started"
    ;;
  stop)
    if alive; then
      kill "$(cat "$PID")" 2>/dev/null || true
      rm -f "$PID"
      echo "stopped"
    else
      echo "not running"
    fi
    ;;
  restart)
    "$0" stop || true
    sleep 1
    "$0" start
    ;;
  status)
    if alive; then echo "active"; else echo "inactive"; fi
    ;;
  enable|disable)
    echo "process mode has no boot enable/disable (use install.sh for a real service)"
    ;;
  *)
    echo "usage: $0 start|stop|restart|status|enable|disable"
    exit 2
    ;;
esac
"""

    if not is_windows(init):
        out["install.sh"] = _install_sh(name, install_path, init, target, run_user)
    return out


def _install_sh(name: str, path: str, init: str, target: DeployTarget, run_user: str) -> str:
    # Prefer pkexec (GUI) over plain sudo when elevating.
    sudo = '$SUDO' if init in ("systemd-system", "runit", "openrc") else ""
    head = f"""#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Prefer pkexec so a graphical password dialog appears; fall back to sudo.
if [ "$(id -u)" -eq 0 ]; then
  SUDO=""
elif command -v pkexec >/dev/null 2>&1; then
  SUDO="pkexec"
else
  SUDO="sudo"
fi
PY="${{PYTHON:-{target.python or 'python3'}}}"
[ -d venv ] || "$PY" -m venv venv
./venv/bin/pip install --upgrade pip -q
./venv/bin/pip install -r requirements.txt -q
mkdir -p logs data
[ -f .env ] || {{ cp .env.example .env; echo "NOTE: edit .env and add your bot token"; }}
chmod 600 .env
chmod +x botctl.sh 2>/dev/null || true
"""
    if init == "systemd-system":
        user = ""
        if run_user:
            user = (f'id -u {run_user} >/dev/null 2>&1 || {sudo} useradd --system --home {path} --shell /usr/sbin/nologin {run_user}\n'
                    f'{sudo} chown -R {run_user}:{run_user} "{path}"\n')
        body = (f'{user}{sudo} install -m 644 {name}.service /etc/systemd/system/{name}.service\n'
                f'{sudo} systemctl daemon-reload\n{sudo} systemctl enable {name}\n')
    elif init == "systemd-user":
        body = (f'mkdir -p ~/.config/systemd/user\ninstall -m 644 {name}.service ~/.config/systemd/user/{name}.service\n'
                f'systemctl --user daemon-reload\nsystemctl --user enable {name}\n'
                f'loginctl enable-linger "$USER" 2>/dev/null || true\n')
    elif init == "runit":
        chown = f'{sudo} chown -R {run_user}:{run_user} "{path}"\n' if run_user else ""
        body = (f'{chown}chmod +x runit/{name}/run runit/{name}/log/run\n'
                f'SVDIR=/etc/service; [ -d "$SVDIR" ] || SVDIR=/var/service\n'
                f'{sudo} ln -sfn "$PWD/runit/{name}" "$SVDIR/{name}"\n')
    elif init == "openrc":
        chown = f'{sudo} chown -R {run_user}:{run_user} "{path}"\n' if run_user else ""
        body = (f'{chown}{sudo} install -m 755 {name}.openrc /etc/init.d/{name}\n'
                f'{sudo} rc-update add {name} default\n')
    else:
        body = "chmod +x botctl.sh\n"
    return head + body + f'echo "Installed {name}."\n'


# ---- control command lines (run on the target) ----------------------------------

def _elev(cmd: str, use_sudo: bool = True, remote: bool = False) -> str:
    """Prefix a command with the best available privilege escalator (see :mod:`elevate`).

    Local: pkexec (graphical PolicyKit dialog), else ``sudo -n``, nothing when already root.
    Remote (SSH): ``sudo -n`` only — the local pkexec says nothing about the far machine.
    """
    return elevate.elevate_linux(cmd, use_sudo, remote)


def control_command(init: str, name: str, action: str, path: str, use_sudo: bool = True, remote: bool = False) -> str:
    """Return a shell/PowerShell command that performs *action* on the service.

    Actions: start | stop | restart | status | enable | disable
    Read-only probes (status) are never elevated, so polling the Bot tab does not raise a password dialog.
    """
    def el(cmd: str) -> str:
        return cmd if action == "status" else _elev(cmd, use_sudo, remote)

    if init == "systemd-system":
        verb = "is-active" if action == "status" else action
        return el(f"systemctl {verb} {name}")
    if init == "systemd-user":
        verb = "is-active" if action == "status" else action
        return f"systemctl --user {verb} {name}"
    if init == "runit":
        sv = {"start": "up", "stop": "down", "restart": "restart", "status": "status"}.get(action)
        if sv is None:  # enable/disable handled at install time
            return "true"
        return el(f"sv {sv} {name}")
    if init == "openrc":
        if action in ("enable", "disable"):
            return el(f"rc-update {'add' if action == 'enable' else 'del'} {name} default")
        return el(f"rc-service {name} {action}")
    if init == "windows":
        return windows_botctl(path, action)
    # process mode (and generic fallback)
    return f'bash "{posixpath.join(path, "botctl.sh")}" {action}'


def systemd_reload_and(action: str, name: str, use_sudo: bool = True, remote: bool = False) -> str:
    """daemon-reload + the action in ONE elevated shell, so the person sees a single password prompt."""
    import shlex
    return _elev("sh -c " + shlex.quote(f"systemctl daemon-reload && systemctl {action} {name}"), use_sudo, remote)


def windows_botctl(path: str, action: str) -> str:
    return (f'powershell -NoProfile -ExecutionPolicy Bypass -File "{ntpath.join(path, "botctl.ps1")}" {action}')


def windows_needs_elevation(mode_output: str, action: str) -> bool:
    """A Windows service (NSSM) or scheduled task needs administrator rights to change; plain process mode and
    read-only probes do not."""
    if action in ("status", "mode"):
        return False
    return mode_output.strip().lower().splitlines()[:1] in (["nssm"], ["task"])


def daemon_reload_command(init: str, use_sudo: bool = True, remote: bool = False) -> str | None:
    """Return a daemon-reload command for the given init, or None if not needed."""
    if init == "systemd-system":
        return _elev("systemctl daemon-reload", use_sudo, remote)
    if init == "systemd-user":
        return "systemctl --user daemon-reload"
    return None


def process_start_command(path: str, init: str) -> str:
    """Direct process-mode start (no service manager). Used as fallback."""
    if is_windows(init):
        return (
            f'powershell -NoProfile -ExecutionPolicy Bypass '
            f'-File "{ntpath.join(path, "botctl.ps1")}" start'
        )
    return f'bash "{posixpath.join(path, "botctl.sh")}" start'


def is_active_from_status(init: str, rc: int, out: str) -> bool:
    text = out.strip().lower()
    first = text.splitlines()[0].strip() if text else ""
    if init in ("systemd-system", "systemd-user"):
        return first == "active"
    if init == "runit":
        return text.startswith("run:")
    if init == "openrc":
        return "started" in text
    # windows (botctl.ps1 prints active / inactive) and process mode (botctl.sh prints the same)
    # NB: "inactive" contains "active", so compare whole words
    return first in ("active", "running", "service_running")
