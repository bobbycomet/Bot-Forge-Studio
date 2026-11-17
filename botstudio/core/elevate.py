"""Privilege elevation for controlling a bot's service.

Linux:   ``pkexec`` (a graphical PolicyKit password dialog); ``sudo -n`` when pkexec is missing; nothing when already root.
Windows: UAC — a hidden elevated PowerShell started with ``Start-Process -Verb RunAs``; its output is captured through
         a temp file because an elevated process cannot share a console or pipes with its (non-elevated) parent.
Commands that need no rights (status probes, user-level services, process mode) are never elevated.
"""
from __future__ import annotations

import base64
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

IS_WIN = sys.platform.startswith("win")
CREATE_NO_WINDOW = 0x08000000

# rc returned when the person dismisses the elevation prompt (ERROR_CANCELLED)
ELEVATION_CANCELLED = 1223


def is_admin() -> bool:
    """True when this process already has administrator / root rights."""
    if IS_WIN:
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0


def clean_env(base: dict | None = None) -> dict:
    """Environment for child processes. A PyInstaller / AppImage build points LD_LIBRARY_PATH at its own bundled
    libraries; system tools (systemctl, ssh, git, bash …) must see the system's instead."""
    env = dict(os.environ if base is None else base)
    if getattr(sys, "frozen", False):
        for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LD_PRELOAD"):
            orig = env.pop(var + "_ORIG", None)
            if orig is not None:
                env[var] = orig
            else:
                env.pop(var, None)
        for var in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "PYTHONHOME", "PYTHONPATH"):
            env.pop(var, None)
    return env


def popen_flags() -> dict:
    """subprocess kwargs that keep a console window from flashing up on Windows."""
    return {"creationflags": CREATE_NO_WINDOW} if IS_WIN else {}


# ---------------------------------------------------------------- Linux

def linux_prefix(use_sudo: bool = True, remote: bool = False) -> str:
    """Prefix that elevates a simple command line, or "" when no elevation is wanted / possible.

    *remote* commands run over SSH on another machine: the local pkexec says nothing about what is installed
    there and has no display on the far side, so only ``sudo -n`` makes sense.
    """
    if not use_sudo:
        return ""
    if not remote and hasattr(os, "geteuid") and os.geteuid() == 0:
        return ""
    if not remote and shutil.which("pkexec"):
        return "pkexec "
    if remote or shutil.which("sudo"):
        return "sudo -n "
    return ""


def elevate_linux(cmd: str, use_sudo: bool = True, remote: bool = False) -> str:
    return linux_prefix(use_sudo, remote) + cmd


# ---------------------------------------------------------------- Windows

def _encode_ps(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def windows_uac_script(cmd: str, out_file: str) -> str:
    """The *outer* PowerShell: starts an elevated PowerShell that runs *cmd* and writes its output to *out_file*."""
    q = out_file.replace("'", "''")
    inner = (
        "$ErrorActionPreference = 'Continue'\n"
        f"$o = '{q}'\n"
        "try {\n"
        f"  $r = (& {{ {cmd} }} 2>&1 | Out-String)\n"
        "  $code = $LASTEXITCODE; if ($null -eq $code) { $code = 0 }\n"
        "  Set-Content -LiteralPath $o -Value $r -Encoding UTF8\n"
        "  exit $code\n"
        "} catch { Set-Content -LiteralPath $o -Value $_.Exception.Message -Encoding UTF8; exit 1 }\n"
    )
    return (
        "$ErrorActionPreference = 'Stop'\n"
        "try {\n"
        "  $p = Start-Process -FilePath powershell.exe -Verb RunAs -Wait -PassThru -WindowStyle Hidden "
        f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-EncodedCommand','{_encode_ps(inner)}'\n"
        "  exit $p.ExitCode\n"
        f"}} catch {{ Write-Output 'Elevation was cancelled or refused: ' + $_.Exception.Message; exit {ELEVATION_CANCELLED} }}\n"
    )


def run_windows_elevated(cmd: str, timeout: float = 300) -> tuple[int, str]:
    """Run a cmd/PowerShell-style command line with administrator rights, asking through UAC when needed."""
    if is_admin():
        p = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout, env=clean_env(), **popen_flags())
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")
    out_file = os.path.join(tempfile.gettempdir(), f"bs_elev_{uuid.uuid4().hex}.log")
    script = windows_uac_script(cmd, out_file)
    try:
        p = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-EncodedCommand", _encode_ps(script)],
            capture_output=True, timeout=timeout, env=clean_env(), **popen_flags())
    except subprocess.TimeoutExpired:
        return 124, "timed out waiting for the elevated command"
    except OSError as e:
        return 127, f"Could not start PowerShell: {e}"
    text = ""
    try:
        with open(out_file, "r", encoding="utf-8-sig", errors="replace") as fh:
            text = fh.read()
    except OSError:
        pass
    finally:
        try:
            os.remove(out_file)
        except OSError:
            pass
    shown = text.strip() or (p.stdout + p.stderr).decode("utf-8", "replace").strip()
    return p.returncode, shown


def looks_denied(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in ("access is denied", "access denied", "permission denied", "not authorized",
                                "access_denied", "requires elevation", "administrator", "authentication failed",
                                "authorization failed", "interactive authentication required"))
