#!/bin/bash
# Builds ~/Applications/College Interview.app: one click runs scripts/ci.sh open.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$HOME/Applications/College Interview.app"
mkdir -p "$HOME/Applications"; rm -rf "$APP"
osacompile -o "$APP" -e "do shell script quoted form of \"$ROOT/scripts/ci.sh\" & \" open >/dev/null 2>&1 &\""
cp "$ROOT/scripts/app.icns" "$APP/Contents/Resources/applet.icns"
rm -f "$APP/Contents/Resources/Assets.car"
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$APP/Contents/Info.plist" 2>/dev/null
codesign --force --deep -s - "$APP" 2>/dev/null; touch "$APP"; echo "$APP"
