#!/usr/bin/env bash
# Run on the Raspberry Pi: sudo bash systemd/install_ledart.sh
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
VENV_DIR="$PROJECT_ROOT/ledenv"

if (( EUID != 0 )); then
    exec sudo bash "${BASH_SOURCE[0]}" "$@"
fi

if [[ ! -d /run/systemd/system ]] || ! command -v apt-get >/dev/null; then
    echo "Run this installer on Raspberry Pi OS with systemd." >&2
    exit 1
fi
for file in LED_Code.py requirements.txt systemd/ledart.service; do
    if [[ ! -f "$PROJECT_ROOT/$file" ]]; then
        echo "Missing project file: $PROJECT_ROOT/$file" >&2
        exit 1
    fi
done
# Keep generated systemd paths literal, including project directories with spaces.
# Reject special characters that systemd would interpret as escapes or variables.
if [[ "$PROJECT_ROOT" == *[!a-zA-Z0-9_./\ -]* ]]; then
    echo "Project path must contain only letters, digits, spaces, _, -, / and ." >&2
    exit 1
fi

apt-get update
apt-get install -y python3 python3-venv python3-dev build-essential pkg-config libgl1 libglib2.0-0
python3 -c 'import sys; sys.exit("Python 3.9 or newer is required." if sys.version_info < (3, 9) else 0)'

if [[ -e "$VENV_DIR" && ! -f "$VENV_DIR/pyvenv.cfg" ]]; then
    echo "$VENV_DIR already exists but is not a virtual environment." >&2
    exit 1
fi
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
"$VENV_DIR/bin/python" -m pip install -r "$PROJECT_ROOT/requirements.txt"
"$VENV_DIR/bin/python" -m pip check
"$VENV_DIR/bin/python" -c 'from PIL import Image, ImageOps, ImageSequence; from rpi_ws281x import Color, PixelStrip; import cv2'
mkdir -p -- "$PROJECT_ROOT/Input"

UNIT_DIR="$(mktemp -d)"
trap 'rm -f -- "$UNIT_DIR/ledart.service"; rmdir -- "$UNIT_DIR"' EXIT
sed "s|@PROJECT_ROOT@|$PROJECT_ROOT|g" "$PROJECT_ROOT/systemd/ledart.service" > "$UNIT_DIR/ledart.service"

systemd-analyze verify "$UNIT_DIR/ledart.service"
install -m 0644 "$UNIT_DIR/ledart.service" /etc/systemd/system/ledart.service
systemctl daemon-reload
systemctl enable ledart.service
systemctl restart ledart.service
systemctl --no-pager --full status ledart.service
printf '\nledart is enabled at boot. View logs: sudo journalctl -u ledart -f\n'
printf 'Put images, GIFs, or videos in: %s/Input\n' "$PROJECT_ROOT"
