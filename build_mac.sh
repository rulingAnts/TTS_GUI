#!/usr/bin/env bash
# build_mac.sh — Build Kokoro TTS Studio .app and .dmg for macOS
# Usage: bash build_mac.sh
set -euo pipefail

APP_NAME="Kokoro TTS Studio"
DMG_NAME="KokoroTTSStudio-mac.dmg"
DIST_DIR="dist"

echo "╔══════════════════════════════════════════════╗"
echo "║   Kokoro TTS Studio — macOS build            ║"
echo "╚══════════════════════════════════════════════╝"
echo ""

# ── Dependency checks ──────────────────────────────────────────────────────
for cmd in python3 pyinstaller create-dmg; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "✗ Missing: $cmd"
    [[ "$cmd" == "create-dmg" ]] && echo "  Install: brew install create-dmg"
    [[ "$cmd" == "pyinstaller" ]] && echo "  Install: pip install pyinstaller"
    exit 1
  fi
done

echo "✓ python3   $(python3 --version)"
echo "✓ pyinstaller $(pyinstaller --version)"
echo "✓ create-dmg found"
echo ""

# ── PyInstaller ────────────────────────────────────────────────────────────
echo "▶ Running PyInstaller..."
pyinstaller kokoro_studio.spec --clean --noconfirm
echo "✓ PyInstaller done"
echo ""

APP_PATH="$DIST_DIR/$APP_NAME.app"
if [ ! -d "$APP_PATH" ]; then
  echo "✗ Expected .app not found at: $APP_PATH"
  exit 1
fi

# ── create-dmg ────────────────────────────────────────────────────────────
echo "▶ Creating DMG..."
rm -f "$DIST_DIR/$DMG_NAME"

create-dmg \
  --volname "$APP_NAME" \
  --volicon "assets/icon.icns" \
  --window-pos 200 140 \
  --window-size 660 400 \
  --icon-size 128 \
  --icon "$APP_NAME.app" 160 185 \
  --hide-extension "$APP_NAME.app" \
  --app-drop-link 500 185 \
  --background "assets/dmg_background.png" \
  "$DIST_DIR/$DMG_NAME" \
  "$APP_PATH" \
  2>/dev/null || \
create-dmg \
  --volname "$APP_NAME" \
  --window-pos 200 140 \
  --window-size 560 320 \
  --icon-size 128 \
  --icon "$APP_NAME.app" 140 150 \
  --hide-extension "$APP_NAME.app" \
  --app-drop-link 420 150 \
  "$DIST_DIR/$DMG_NAME" \
  "$APP_PATH"

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   Build complete ✓                           ║"
echo "╚══════════════════════════════════════════════╝"
echo "  Output: $DIST_DIR/$DMG_NAME"
echo "  Size:   $(du -sh "$DIST_DIR/$DMG_NAME" | cut -f1)"
