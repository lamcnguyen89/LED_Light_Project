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
for file in LED_Code.py web_ui.py requirements.txt templates/index.html \
    static/web_ui.css static/web_ui.js systemd/ledart.service systemd/ledart-web.service \
    systemd/ledart-web-storage.conf; do
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

# Keep the WebUI unprivileged. Prefer the sudo caller, then the checkout owner.
WEB_USER="${LEDART_WEB_USER:-${SUDO_USER:-$(stat -c '%U' "$PROJECT_ROOT")}}"
if [[ "$WEB_USER" == root ]]; then
    WEB_USER=ledart-web
    if ! id "$WEB_USER" >/dev/null 2>&1; then
        useradd --system --user-group --no-create-home --home-dir "$PROJECT_ROOT" \
            --shell /usr/sbin/nologin "$WEB_USER"
    fi
fi
if [[ "$WEB_USER" == *[!a-zA-Z0-9_-]* ]] || ! id "$WEB_USER" >/dev/null 2>&1; then
    echo "LEDART_WEB_USER must name an existing unprivileged user." >&2
    exit 1
fi
if [[ "$(id -u "$WEB_USER")" == 0 ]]; then
    echo "The WebUI must run as a non-root user." >&2
    exit 1
fi
if [[ -L "$PROJECT_ROOT/Input" ]]; then
    echo "Input must be a real directory, not a symbolic link." >&2
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
"$VENV_DIR/bin/python" -c 'from PIL import Image, ImageOps, ImageSequence; from rpi_ws281x import Color, PixelStrip; import cv2; import flask; import waitress'
mkdir -p -- "$PROJECT_ROOT/Input"
# Directory write permission permits deleting existing files without changing their ownership.
chown "$WEB_USER:$(id -gn "$WEB_USER")" "$PROJECT_ROOT/Input"
chmod u+rwx "$PROJECT_ROOT/Input"
for file in web_ui.py templates/index.html static/web_ui.css static/web_ui.js ledenv/bin/python; do
    if ! runuser -u "$WEB_USER" -- test -r "$PROJECT_ROOT/$file"; then
        echo "$WEB_USER cannot read $PROJECT_ROOT/$file." >&2
        echo "Grant that user access to the project and its parent directories, then rerun." >&2
        exit 1
    fi
done
if ! runuser -u "$WEB_USER" -- test -x "$VENV_DIR/bin/python"; then
    echo "$WEB_USER cannot execute $VENV_DIR/bin/python." >&2
    exit 1
fi

UNIT_DIR="$(mktemp -d)"
trap 'rm -f -- "$UNIT_DIR/ledart.service" "$UNIT_DIR/ledart-web.service" "$UNIT_DIR/ledart-web.service.d/zzz-ledart-storage.conf"; rmdir -- "$UNIT_DIR/ledart-web.service.d" "$UNIT_DIR"' EXIT
mkdir -p -- "$UNIT_DIR/ledart-web.service.d"
install -m 0644 "$PROJECT_ROOT/systemd/ledart-web-storage.conf" "$UNIT_DIR/ledart-web.service.d/zzz-ledart-storage.conf"
for unit in ledart.service ledart-web.service; do
    sed -e "s|@PROJECT_ROOT@|$PROJECT_ROOT|g" -e "s|@WEB_USER@|$WEB_USER|g" \
        "$PROJECT_ROOT/systemd/$unit" > "$UNIT_DIR/$unit"
done
SYSTEMD_UNIT_PATH="$UNIT_DIR:" systemd-analyze verify "$UNIT_DIR/ledart.service" "$UNIT_DIR/ledart-web.service"
for unit in ledart.service ledart-web.service; do
    install -m 0644 "$UNIT_DIR/$unit" "/etc/systemd/system/$unit"
done
# Retain user overrides (such as port settings), but supersede the old storage policy.
install -d -m 0755 /etc/systemd/system/ledart-web.service.d
install -m 0644 "$PROJECT_ROOT/systemd/ledart-web-storage.conf" \
    /etc/systemd/system/ledart-web.service.d/zzz-ledart-storage.conf
systemctl daemon-reload
systemctl enable ledart.service ledart-web.service
# ExecStartPre performs real upload file operations as WEB_USER inside the service sandbox.
if ! systemctl restart ledart-web.service; then
    echo "WebUI startup/storage check failed. Installation is not complete." >&2
    journalctl --no-pager -u ledart-web.service -n 40
    exit 1
fi
systemctl restart ledart.service
systemctl --no-pager --full status ledart.service ledart-web.service
printf '\nWebUI storage check passed. Both services are enabled at boot.\n'
printf 'Player logs: sudo journalctl -u ledart -f\n'
printf 'WebUI logs: sudo journalctl -u ledart-web -f\n'
printf 'Open http://<pi-ip>:8080 from your browser (WebUI user: %s).\n' "$WEB_USER"
printf 'Media folder: %s/Input\n' "$PROJECT_ROOT"
