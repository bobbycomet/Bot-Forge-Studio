# PyInstaller spec shared by the AppImage (Linux) and Windows builds.
#   pyinstaller --noconfirm --clean BotStudio.spec
import os
import sys
from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
IS_WIN = sys.platform.startswith("win")

def _runtime_datas():
    out = []
    base = os.path.join(ROOT, "botstudio", "runtime")
    for root, _dirs, files in os.walk(base):
        if "__pycache__" in root:
            continue
        for f in files:
            if f.endswith((".pyc",)) or f == "__init__.py":
                continue
            out.append((os.path.join(root, f), os.path.join("botstudio", os.path.relpath(root, os.path.dirname(base)))))
    return out


RUNTIME_DATAS = _runtime_datas()

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    datas=RUNTIME_DATAS,   # runtime files are copied into every generated bot / its web dashboard / game modules
    hiddenimports=collect_submodules("botstudio"),
    excludes=["tkinter", "PyQt6.QtWebEngineCore", "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.QtMultimedia",
              "PyQt6.QtPdf", "PyQt6.Qt3DCore", "PyQt6.QtBluetooth", "PyQt6.QtSql", "PyQt6.QtNetwork",
              "PyQt6.QtDBus", "PyQt6.QtSvg", "PyQt6.QtTest", "PyQt6.QtOpenGL"],
)

# Linux: never ship the *build machine's* system libraries (gtk, glib, krb5, systemd, libstdc++ ...).
# They drag in the build host's glibc version; every target distro already has them.  The X11/XCB helper libraries
# Qt's xcb plugin needs (and stock distros often lack) come from BUNDLE_LIBS_DIR instead: packaging/fetch_debs.py
# unpacks them from Ubuntu 22.04, so their glibc needs stay low no matter which machine builds the AppImage.
SYSTEM_DIRS = ("/usr/lib", "/lib", "/usr/local/lib", "/usr/lib64", "/lib64")
BUNDLE_LIBS_DIR = os.environ.get("BUNDLE_LIBS_DIR", "")
KEEP_SYSTEM = () if BUNDLE_LIBS_DIR else (
    "libxcb-cursor", "libxcb-icccm", "libxcb-image", "libxcb-keysyms", "libxcb-render-util", "libxcb-xinerama",
    "libxcb-xkb", "libxcb-shape", "libxcb-randr", "libxcb-util", "libxcb-sync", "libxcb-xfixes", "libxcb-shm",
    "libxcb-render", "libxcb-glx", "libxcb-present", "libxcb-dri3", "libxkbcommon-x11")


def keep(entry):
    name, src, kind = entry
    base = os.path.basename(name)
    if base.startswith("libqgtk3"):          # GTK theme plugin: pulls in all of GTK
        return False
    if IS_WIN or kind not in ("BINARY", "EXTENSION"):
        return True
    if BUNDLE_LIBS_DIR and base.startswith(("libxcb-", "libxkbcommon-x11")):
        return False                         # replaced below by the old-glibc copies
    if src.startswith(SYSTEM_DIRS) and not base.startswith(KEEP_SYSTEM):
        return False
    return True


a.binaries = [b for b in a.binaries if keep(b)]
if BUNDLE_LIBS_DIR and not IS_WIN:
    names = sorted(f for f in os.listdir(BUNDLE_LIBS_DIR) if ".so" in f)
    pointed = {os.path.realpath(os.path.join(BUNDLE_LIBS_DIR, f)) for f in names if os.path.islink(os.path.join(BUNDLE_LIBS_DIR, f))}
    for f in names:
        full = os.path.join(BUNDLE_LIBS_DIR, f)
        real = os.path.realpath(full)
        if os.path.isfile(real) and (os.path.islink(full) or real not in pointed):
            a.binaries += [(f, real, "BINARY")]       # shipped under the soname the loader asks for (libxcb-cursor.so.0)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name="BotStudio", console=False,
    icon=os.path.join(SPECPATH, "botstudio.ico") if IS_WIN else None,
    version=os.path.join(SPECPATH, "version_info.txt") if IS_WIN else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="BotStudio")
