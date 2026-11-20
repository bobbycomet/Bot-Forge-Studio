#!/usr/bin/env bash
# Build the Bot Forge Studio macOS app bundle and a zip suitable for testing or distribution.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
VERSION="$(python3 - <<PY
import re;print(re.search(r'__version__\s*=\s*"([^"]+)"', open("$ROOT/botstudio/__init__.py").read()).group(1))
PY
)"

cd "$ROOT"
python3 -m pip install --upgrade pip
python3 -m pip install "PyQt6>=6.5" pyinstaller
BUILD="$ROOT/build/macos"
DIST="$ROOT/dist/macos"
mkdir -p "$BUILD" "$DIST"
python3 -m PyInstaller --noconfirm --clean --distpath "$DIST" --workpath "$BUILD" "$HERE/BotStudio.spec"

APP="$DIST/Bot Forge Studio.app"
test -d "$APP"
QT_QPA_PLATFORM=offscreen "$APP/Contents/MacOS/BotStudio" --selftest
ditto -c -k --sequesterRsrc --keepParent "$APP" "$DIST/BotForgeStudio-$VERSION-macOS.zip"
echo "Built $DIST/BotForgeStudio-$VERSION-macOS.zip"
