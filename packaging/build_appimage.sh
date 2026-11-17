#!/usr/bin/env bash
# Build BotForgeStudio-<version>-x86_64.AppImage. Works anywhere with curl + bash; no root, no FUSE needed.
#
# glibc: PyInstaller bundles what it finds on the build machine, so build on the OLDEST distro you
# support. This script (1) uses a relocatable python-build-standalone interpreter (built against old glibc)
# instead of the distro python and (2) FAILS if any bundled binary needs glibc newer than $MAX_GLIBC.
# Target: any distro with glibc 2.34 or newer (Ubuntu 22.04+, Debian 12+, Fedora 35+, RHEL 9+ ...).
# Reproducible build:   docker run --rm -v "$PWD/..":/src -w /src/packaging ubuntu:22.04 \
#                              bash -c 'apt-get update && apt-get install -y curl ca-certificates binutils libgl1 libegl1 \
#                              libdbus-1-3 libfontconfig1 file && ./build_appimage.sh'
set -euo pipefail

MAX_GLIBC="${MAX_GLIBC:-2.34}"
PBS_TAG="${PBS_TAG:-20241016}"
PBS_PY="${PBS_PY:-3.12.7}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
WORK="${WORK:-$HERE/build}"
OUT="${OUT:-$ROOT/dist}"
VERSION="$(python3 - <<PY
import re;print(re.search(r'__version__\s*=\s*"([^"]+)"', open("$ROOT/botstudio/__init__.py").read()).group(1))
PY
)"
ARCH=x86_64

mkdir -p "$WORK" "$OUT"
cd "$WORK"

echo "==> portable Python $PBS_PY"
PY_TGZ="cpython-${PBS_PY}%2B${PBS_TAG}-${ARCH}-unknown-linux-gnu-install_only.tar.gz"
[ -d python ] || { curl -fsSL -o py.tgz "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${PY_TGZ}" && tar xzf py.tgz && rm py.tgz; }
PY="$WORK/python/bin/python3"

echo "==> venv + dependencies"
rm -rf venv && "$PY" -m venv venv
./venv/bin/pip install -q --upgrade pip
./venv/bin/pip install -q "PyQt6>=6.5" pyinstaller
# discord.py etc. are NOT bundled: they run inside the bots Studio generates, on the target machine.

echo "==> X11/XCB helper libraries for Qt's xcb plugin, taken from Ubuntu 22.04 (keeps the bundle's glibc needs low)"
export BUNDLE_LIBS_DIR="$WORK/xcblibs"
rm -rf "$BUNDLE_LIBS_DIR"
python3 "$HERE/fetch_debs.py" "$BUNDLE_LIBS_DIR" libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
  libxcb-render-util0 libxcb-xinerama0 libxcb-xkb1 libxcb-util1 libxcb-shape0 libxcb-randr0 libxcb-sync1 \
  libxcb-xfixes0 libxcb-shm0 libxcb-render0 libxcb-glx0 libxcb-present0 libxcb-dri3-0 libxkbcommon-x11-0
[ -e "$BUNDLE_LIBS_DIR/libxcb-cursor.so.0" ] || { echo "libxcb-cursor extraction failed" >&2; exit 1; }

echo "==> PyInstaller"
rm -rf pyi-build pyi-dist
./venv/bin/pyinstaller --noconfirm --clean --workpath pyi-build --distpath pyi-dist "$HERE/BotStudio.spec"

echo "==> smoke test of the frozen app (headless)"
QT_QPA_PLATFORM=offscreen ./pyi-dist/BotStudio/BotStudio --selftest

echo "==> glibc audit (limit $MAX_GLIBC)"
need=$(find pyi-dist/BotStudio -type f \( -name '*.so*' -o -perm -u+x \) -print0 \
  | xargs -0 -n1 sh -c 'file -b "$0" | grep -q ELF && objdump -T "$0" 2>/dev/null' \
  | grep -o 'GLIBC_[0-9.]*' | sed 's/GLIBC_//' | sort -Vu | tail -1)
echo "highest glibc symbol required by the bundle: ${need:-none}"
if [ -n "$need" ] && [ "$(printf '%s\n%s\n' "$need" "$MAX_GLIBC" | sort -V | tail -1)" != "$MAX_GLIBC" ]; then
  echo "ERROR: bundle needs glibc $need > $MAX_GLIBC. Build on an older base image (see header)." >&2
  [ "${ALLOW_NEWER_GLIBC:-0}" = 1 ] || exit 1
fi

echo "==> AppDir"
APPDIR="$WORK/BotStudio.AppDir"
rm -rf "$APPDIR" && mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a pyi-dist/BotStudio/. "$APPDIR/usr/bin/"
cp "$HERE/botstudio.desktop" "$APPDIR/botstudio.desktop"
cp "$HERE/botstudio-256.png" "$APPDIR/botstudio.png"
cp "$HERE/botstudio-256.png" "$APPDIR/.DirIcon"
cp "$HERE/botstudio-256.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/botstudio.png"
cat > "$APPDIR/AppRun" <<'RUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export QT_QPA_PLATFORMTHEME="${QT_QPA_PLATFORMTHEME:-}"
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
exec "$HERE/usr/bin/BotStudio" "$@"
RUN
chmod +x "$APPDIR/AppRun"

echo "==> appimagetool"
[ -x appimagetool ] || { curl -fsSL -o appimagetool "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage" && chmod +x appimagetool; }
rm -f "$OUT/BotForgeStudio-${VERSION}-${ARCH}.AppImage"
ARCH=$ARCH ./appimagetool --appimage-extract-and-run --no-appstream --comp "${COMP:-zstd}" "$APPDIR" "$OUT/BotForgeStudio-${VERSION}-${ARCH}.AppImage"
chmod +x "$OUT/BotForgeStudio-${VERSION}-${ARCH}.AppImage"
echo "==> done: $OUT/BotForgeStudio-${VERSION}-${ARCH}.AppImage"
