#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/telesaved}"
VENV_DIR="${VENV_DIR:-${APP_DIR}/venv}"
SERVICE_NAME="${SERVICE_NAME:-telesaved}"

export DEBIAN_FRONTEND=noninteractive

log(){ echo; echo "==== $* ===="; }
fail(){ echo "ERROR: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Run with sudo/root."
[ -f "${APP_DIR}/app.py" ] || fail "${APP_DIR}/app.py not found."

log "Installing system packages"
apt-get update
apt-get install -y python3 python3-venv python3-pip python3-dev build-essential git ffmpeg curl

log "Creating/updating virtual environment"
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel

log "Installing CPU PyTorch"
"${VENV_DIR}/bin/python" -m pip install --upgrade torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu

log "Installing TeleSaved dependencies"
"${VENV_DIR}/bin/python" -m pip install --upgrade -r "${APP_DIR}/requirements.txt"
"${VENV_DIR}/bin/python" -m pip install --upgrade git+https://github.com/openai/CLIP.git

log "Testing imports"
"${VENV_DIR}/bin/python" - <<'PY'
from aiohttp import web
import telethon, torch, clip, faiss, PIL, numpy, whisper
print('TeleSaved dependency check OK')
print('Torch CUDA available:', torch.cuda.is_available())
print('Whisper:', whisper.__file__)
PY

log "Installing systemd service"
cp "${APP_DIR}/systemd/telesaved.service" "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"

if [ -f "${APP_DIR}/config.json" ]; then
  systemctl restart "${SERVICE_NAME}.service"
  systemctl status "${SERVICE_NAME}.service" --no-pager || true
else
  echo "config.json is not present. Copy config.example.json to config.json and edit Telegram credentials before starting."
fi
