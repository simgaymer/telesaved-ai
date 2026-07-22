#!/usr/bin/env bash
set -euo pipefail
APP_DIR="/opt/telesaved"
VENV_DIR="${APP_DIR}/venv"
SERVICE_NAME="telesaved"
OVERRIDE_DIR="/etc/systemd/system/${SERVICE_NAME}.service.d"
OVERRIDE_FILE="${OVERRIDE_DIR}/override.conf"
export DEBIAN_FRONTEND=noninteractive

log(){ echo; echo "==== $* ===="; }
fail(){ echo "ERROR: $*" >&2; exit 1; }

[[ "${EUID}" -eq 0 ]] || fail "Run with sudo: sudo $0"
[[ -d "${APP_DIR}" ]] || fail "${APP_DIR} does not exist"

log "Stopping TeleSaved"
systemctl stop "${SERVICE_NAME}" || true

log "Installing required system packages"
apt-get update
apt-get install -y python3 python3-venv python3-pip ffmpeg git

log "Creating TeleSaved venv if needed"
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

log "Upgrading pip tooling"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel

log "Installing/reinstalling OpenAI Whisper into TeleSaved venv"
"${VENV_DIR}/bin/python" -m pip uninstall -y whisper openai-whisper || true
"${VENV_DIR}/bin/python" -m pip install --upgrade openai-whisper

log "Verifying Whisper CLI"
"${VENV_DIR}/bin/python" - <<'PY'
import whisper
print("Whisper module OK:", whisper.__file__)
PY
"${VENV_DIR}/bin/whisper" --help >/tmp/telesaved-whisper-help.txt

log "Backing up systemd override"
mkdir -p "${OVERRIDE_DIR}"
if [[ -f "${OVERRIDE_FILE}" ]]; then
  cp -a "${OVERRIDE_FILE}" "${OVERRIDE_FILE}.backup.$(date +%Y%m%d-%H%M%S)"
fi

log "Writing systemd override to use TeleSaved venv and TeleSaved Whisper"
cat > "${OVERRIDE_FILE}" <<EOF
[Service]
ExecStart=
ExecStart=${VENV_DIR}/bin/python ${APP_DIR}/app.py
Environment="PATH=${VENV_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="PYTHONUNBUFFERED=1"
Environment="TELESAVED_WHISPER_BIN=${VENV_DIR}/bin/whisper"
Environment="TELESAVED_WHISPER_DEVICE=cpu"
Environment="TELESAVED_WHISPER_MODEL=tiny"
Environment="TELESAVED_WHISPER_TIMEOUT=3600"
EOF

log "Reloading and starting TeleSaved"
systemctl daemon-reload
systemctl start "${SERVICE_NAME}"
sleep 5

log "Checking TeleSaved status"
if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
  journalctl -u "${SERVICE_NAME}" -n 100 --no-pager
  exit 1
fi

systemctl show "${SERVICE_NAME}" -p ExecStart -p Environment
journalctl -u "${SERVICE_NAME}" -n 40 --no-pager

echo
echo "Done. TeleSaved now uses ${VENV_DIR}/bin/whisper for transcripts."
