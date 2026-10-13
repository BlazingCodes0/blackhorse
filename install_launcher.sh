#!/bin/bash
set -e
APP_DIR="$HOME/blackhorse"
SHARE="$HOME/.local/share/applications"
DESK="$HOME/Desktop"

mkdir -p "$SHARE" "$DESK"

cat > "$SHARE/BlackHorse.desktop" << EOD
[Desktop Entry]
Version=1.0
Type=Application
Name=BlackHorse
Comment=BlackHorse Trading GUI
Exec=python3 $APP_DIR/app/main.py
Icon=$APP_DIR/logo.png
Terminal=false
Categories=Finance;Utility;
EOD

cp -f "$SHARE/BlackHorse.desktop" "$DESK/BlackHorse.desktop"

chmod +x "$SHARE/BlackHorse.desktop" "$DESK/BlackHorse.desktop" 2>/dev/null || true
chmod +x "$APP_DIR/spice" 2>/dev/null || true

echo "BlackHorse launchers created."
