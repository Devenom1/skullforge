#!/bin/bash
# One-time system setup for SkullForge:
#   - a udev rule so the panel is usable without root for normal operation
#   - a narrowly-scoped sudoers rule so the "Recover Panel" power-cycle
#     action (which genuinely needs root - it toggles a USB port's power
#     state) doesn't need a password prompt every time
#   - the desktop entry + icon, so SkullForge shows up in your app launcher
#
# Run once with sudo:
#   sudo ./packaging/install-system-files.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this with sudo: sudo $0" >&2
    exit 1
fi

REAL_USER="${SUDO_USER:-$(logname)}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Installing udev rule..."
install -m 0644 "$REPO_DIR/data/udev/99-skullforge.rules" /etc/udev/rules.d/99-skullforge.rules
udevadm control --reload-rules
udevadm trigger

echo "Installing recovery script..."
install -m 0755 "$REPO_DIR/tools/skullforge-panel-recover.sh" /usr/local/sbin/skullforge-panel-recover.sh

echo "Installing sudoers rule for user '$REAL_USER'..."
# The trailing "*" is required since the script takes the USB port path as
# an argument (SkullForge's config controls the exact value) - sudoers
# matches the literal command line otherwise. This narrows the grant to
# "run this one script, with any arguments" rather than truly arbitrary
# root commands.
SUDOERS_FILE=/etc/sudoers.d/skullforge
cat > "$SUDOERS_FILE" <<EOF
$REAL_USER ALL=(root) NOPASSWD: /usr/local/sbin/skullforge-panel-recover.sh *
EOF
chmod 0440 "$SUDOERS_FILE"
visudo -cf "$SUDOERS_FILE"

echo "Installing desktop entry and icon..."
install -m 0644 "$REPO_DIR/data/io.skullforge.App.desktop" /usr/share/applications/io.skullforge.App.desktop
install -Dm 0644 "$REPO_DIR/data/io.skullforge.App.svg" /usr/share/icons/hicolor/scalable/apps/io.skullforge.App.svg
gtk-update-icon-cache /usr/share/icons/hicolor >/dev/null 2>&1 || true

echo
echo "Done. If the panel is already plugged in, unplug/replug it (or run"
echo "'sudo udevadm trigger') so the new udev rule applies immediately."
