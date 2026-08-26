#!/usr/bin/env bash
# Build a portable TeleSaved AI Debian package.
#
# Key design choice:
#   The package does NOT copy the builder machine's Python virtual environment.
#   During package installation, the target Debian/Ubuntu machine creates its
#   own /opt/telesaved/venv with its own Python 3.13 or 3.14 interpreter.
#
# Default source: /opt/telesaved
# Default output: /opt/telesaved/dist/telesaved-ai_4.4.3-2_all.deb
#
# Usage:
#   sudo bash build-telesaved-deb-4.4.3-2.sh
#
# Optional overrides:
#   SOURCE_DIR=/path/to/telesaved OUTPUT_DIR=/path/to/output \
#     sudo -E bash build-telesaved-deb-4.4.3-2.sh

set -Eeuo pipefail
IFS=$'\n\t'
umask 022

PACKAGE_NAME="telesaved-ai"
PACKAGE_VERSION="4.4.3-2"
APP_VERSION="4.4.3"
SOURCE_DIR="${SOURCE_DIR:-/opt/telesaved}"
OUTPUT_DIR="${OUTPUT_DIR:-${SOURCE_DIR}/dist}"
BUILD_ROOT=""
LOG_FILE="${OUTPUT_DIR}/build-telesaved-${PACKAGE_VERSION}.log"
PACKAGE_FILE="${OUTPUT_DIR}/${PACKAGE_NAME}_${PACKAGE_VERSION}_all.deb"

BLUE='\033[1;34m'
GREEN='\033[1;32m'
YELLOW='\033[1;33m'
RED='\033[1;31m'
RESET='\033[0m'

info() { printf '\n%b==> %s%b\n' "$BLUE" "$*" "$RESET"; }
ok()   { printf '%b[OK]%b %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%b[WARN]%b %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die()  { printf '%b[FAIL]%b %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

python_syntax_check() {
    local file_path="$1"
    python3 - "$file_path" <<'PY_CHECK'
from pathlib import Path
import sys
path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
compile(source, str(path), "exec")
PY_CHECK
}

cleanup() {
    if [[ -n "$BUILD_ROOT" && -d "$BUILD_ROOT" ]]; then
        rm -rf "$BUILD_ROOT"
    fi
}

on_error() {
    local exit_code=$?
    printf '\n%b[FAIL]%b Build failed at line %s with exit code %s.\n' \
        "$RED" "$RESET" "$1" "$exit_code" >&2
    printf 'Build log: %s\n' "$LOG_FILE" >&2
    exit "$exit_code"
}

trap cleanup EXIT
trap 'on_error "$LINENO"' ERR

require_root() {
    [[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run this builder as root: sudo bash $0"
}

require_source_tree() {
    [[ -d "$SOURCE_DIR" ]] || die "Source directory does not exist: $SOURCE_DIR"
    [[ -f "$SOURCE_DIR/app.py" ]] || die "Missing source application: $SOURCE_DIR/app.py"
    [[ -f "$SOURCE_DIR/frontend/index.html" ]] || \
        die "Missing frontend: $SOURCE_DIR/frontend/index.html"

    python_syntax_check "$SOURCE_DIR/app.py" || \
        die "The source app.py does not pass Python syntax compilation."
}

ensure_build_tools() {
    local missing=()
    local command_name

    for command_name in python3 rsync dpkg-deb dpkg-parsechangelog gzip; do
        command -v "$command_name" >/dev/null 2>&1 || missing+=("$command_name")
    done

    if (( ${#missing[@]} > 0 )); then
        info "Installing package-build prerequisites"
        apt-get update
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
            python3 rsync dpkg-dev gzip ca-certificates
    fi

    for command_name in python3 rsync dpkg-deb gzip; do
        command -v "$command_name" >/dev/null 2>&1 || \
            die "Required build command is unavailable: $command_name"
    done
}

prepare_output() {
    install -d -m 0755 "$OUTPUT_DIR"
    touch "$LOG_FILE"
    chmod 0644 "$LOG_FILE"
    exec > >(tee -a "$LOG_FILE") 2>&1

    info "Building ${PACKAGE_NAME} ${PACKAGE_VERSION}"
    printf 'Source application: %s\n' "$SOURCE_DIR"
    printf 'Output package:     %s\n' "$PACKAGE_FILE"
    printf 'Package strategy:  target-created Python venv (3.13 or 3.14)\n'
}

create_package_tree() {
    BUILD_ROOT="$(mktemp -d -t telesaved-deb.XXXXXXXX)"
    local package_root="$BUILD_ROOT/pkg"

    install -d -m 0755 \
        "$package_root/DEBIAN" \
        "$package_root/opt/telesaved" \
        "$package_root/lib/systemd/system" \
        "$package_root/usr/local/sbin" \
        "$package_root/usr/lib/telesaved" \
        "$package_root/usr/share/doc/${PACKAGE_NAME}"
}

copy_application() {
    local package_root="$BUILD_ROOT/pkg"

    info "Copying TeleSaved application files"

    # Preserve application code, templates, static files, and bundled models.
    # Exclude the builder's machine-specific credentials, sessions, venv,
    # caches, mutable database/state, logs, and media-derived directories.
    rsync -a \
        --exclude '/venv/' \
        --exclude '/.venv/' \
        --exclude '/dist/' \
        --exclude '/.git/' \
        --exclude '__pycache__/' \
        --exclude '*.pyc' \
        --exclude '*.pyo' \
        --exclude '/config.json' \
        --exclude '/telesaved_session*' \
        --exclude '/state.json' \
        --exclude '/telesaved.db*' \
        --exclude '/settings.json' \
        --exclude '/feedback.json' \
        --exclude '/auth.json' \
        --exclude '/jobs.json' \
        --exclude '/trash_index.json' \
        --exclude '*.log' \
        --exclude '/frames/' \
        --exclude '/previews/' \
        --exclude '/thumbs/' \
        --exclude '/scenes/' \
        --exclude '/audio/' \
        --exclude '/trash/' \
        --exclude '/backups/' \
        --exclude '/telegram_metadata/' \
        "$SOURCE_DIR/" "$package_root/opt/telesaved/"

    [[ -f "$package_root/opt/telesaved/app.py" ]] || \
        die "app.py was not copied into the package tree."
    [[ -f "$package_root/opt/telesaved/frontend/index.html" ]] || \
        die "frontend/index.html was not copied into the package tree."
}

patch_application() {
    local app_path="$BUILD_ROOT/pkg/opt/telesaved/app.py"

    info "Patching the application version and Telegram authorization behavior"

    APP_PATH="$app_path" APP_VERSION="$APP_VERSION" python3 <<'PY'
import ast
import os
from pathlib import Path

path = Path(os.environ["APP_PATH"])
new_version = os.environ["APP_VERSION"]
source = path.read_text(encoding="utf-8")
lines = source.splitlines(keepends=True)
tree = ast.parse(source, filename=str(path))

version_node = None
start_client_node = None

for node in tree.body:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in targets):
            version_node = node
    if isinstance(node, ast.AsyncFunctionDef) and node.name == "start_client":
        start_client_node = node

if version_node is None:
    raise SystemExit("Could not locate the APP_VERSION assignment by Python syntax tree.")
if start_client_node is None:
    raise SystemExit("Could not locate async def start_client() by Python syntax tree.")
if not getattr(version_node, "end_lineno", None):
    raise SystemExit("This Python interpreter cannot determine APP_VERSION source boundaries.")
if not getattr(start_client_node, "end_lineno", None):
    raise SystemExit("This Python interpreter cannot determine start_client() source boundaries.")

replacement_function = '''async def start_client():
    global client

    if not TELEGRAM_CONFIGURED or client is None:
        await main()
        return

    await client.connect()

    if not await client.is_user_authorized():
        logger.warning(
            "Telegram credentials are configured, but the saved Telegram session is not authorized. "
            "TeleSaved will continue in web mode without Telegram synchronization. "
            "Run 'sudo telesaved-telegram-login' and then restart the service."
        )
        try:
            await client.disconnect()
        except Exception:
            logger.exception("Could not disconnect the unauthorized Telegram client")
        client = None
        await main()
        return

    await main()
'''

replacements = [
    (
        version_node.lineno - 1,
        version_node.end_lineno,
        [f'APP_VERSION = "{new_version}"\n'],
    ),
    (
        start_client_node.lineno - 1,
        start_client_node.end_lineno,
        [replacement_function],
    ),
]

for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
    lines[start:end] = replacement

patched = "".join(lines)
ast.parse(patched, filename=str(path))
path.write_text(patched, encoding="utf-8")

print(f"Patched APP_VERSION to {new_version}")
print(
    "Replaced start_client() using AST line boundaries: "
    f"original lines {start_client_node.lineno}-{start_client_node.end_lineno}"
)
PY

    python_syntax_check "$app_path"
    grep -q "APP_VERSION = \"${APP_VERSION}\"" "$app_path" || \
        die "APP_VERSION patch validation failed."
    grep -q "telesaved-telegram-login" "$app_path" || \
        die "Telegram startup patch validation failed."

    ok "app.py passed syntax and patch validation."
}

write_runtime_requirements() {
    local package_root="$BUILD_ROOT/pkg"

    # Versions are intentionally pinned to the dependency set proven on the
    # Debian 13 / Python 3.13 TeleSaved repair, with NumPy corrected to 2.4.6
    # so it remains compatible with Numba 0.66.0 and Whisper.
    cat > "$package_root/usr/lib/telesaved/requirements-runtime.txt" <<'REQUIREMENTS'
aiohttp==3.14.1
Telethon==1.44.0
ldap3==2.9.1
Pillow==12.3.0
numpy==2.4.6
numba==0.66.0
faiss-cpu==1.14.3
ultralytics==8.4.100
openai-whisper==20250625
tiktoken>=0.11,<1
ftfy>=6.3,<7
regex>=2024.11.6
tqdm>=4.67,<5
REQUIREMENTS
}

write_runtime_installer() {
    local package_root="$BUILD_ROOT/pkg"

    cat > "$package_root/usr/lib/telesaved/install-runtime.sh" <<'RUNTIME_INSTALLER'
#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 022

APP_DIR="/opt/telesaved"
VENV_DIR="$APP_DIR/venv"
BACKUP_VENV="$APP_DIR/venv.pre-package-install"
REQUIREMENTS="/usr/lib/telesaved/requirements-runtime.txt"
PYTHON_BIN="${TELESAVED_PYTHON_BIN:-/usr/bin/python3}"
TORCH_INDEX_URL="https://download.pytorch.org/whl/cpu"
LOG_FILE="/var/log/telesaved-runtime-install.log"

exec > >(tee -a "$LOG_FILE") 2>&1

info() { printf '\n==> %s\n' "$*"; }
fail() { printf '\n[FAIL] %s\n' "$*" >&2; exit 1; }

restore_previous_venv() {
    local exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        printf '\n[FAIL] Runtime installation failed with exit code %s.\n' "$exit_code" >&2
        rm -rf "$VENV_DIR"
        if [[ -d "$BACKUP_VENV" ]]; then
            mv "$BACKUP_VENV" "$VENV_DIR"
            printf '[RECOVERY] Restored the previous TeleSaved venv.\n' >&2
        fi
    fi
    exit "$exit_code"
}
trap restore_previous_venv EXIT

[[ -x "$PYTHON_BIN" ]] || fail "Python interpreter not found: $PYTHON_BIN"
[[ -f "$APP_DIR/app.py" ]] || fail "TeleSaved app.py is missing."
[[ -f "$REQUIREMENTS" ]] || fail "Runtime requirements file is missing."

PYTHON_VERSION="$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
case "$PYTHON_VERSION" in
    3.13|3.14)
        ;;
    *)
        fail "TeleSaved 4.4.3-2 supports target Python 3.13 or 3.14; found $PYTHON_VERSION."
        ;;
esac

info "Preparing TeleSaved Python $PYTHON_VERSION virtual environment"
rm -rf "$BACKUP_VENV"
if [[ -d "$VENV_DIR" ]]; then
    mv "$VENV_DIR" "$BACKUP_VENV"
fi

"$PYTHON_BIN" -m venv "$VENV_DIR"
PYTHON="$VENV_DIR/bin/python"
PIP=("$PYTHON" -m pip)

info "Installing compatible Python packaging tools"
# PyTorch 2.13 requires setuptools >=77.0.3. OpenAI CLIP's legacy setup.py
# still requires pkg_resources, so setuptools must remain below 81.
"${PIP[@]}" install --upgrade \
    "pip==26.1.2" \
    "setuptools==80.9.0" \
    "wheel==0.47.0" \
    "packaging==26.2"

info "Installing CPU-only PyTorch and torchvision"
"${PIP[@]}" install --upgrade \
    --index-url "$TORCH_INDEX_URL" \
    "torch==2.13.0+cpu" \
    "torchvision==0.28.0+cpu"

info "Installing OpenAI CLIP"
"${PIP[@]}" install --no-build-isolation \
    "git+https://github.com/openai/CLIP.git@d50d76daa670286dd6cacf3bcd80b5e4823fc8e1"

info "Installing TeleSaved runtime dependencies"
"${PIP[@]}" install --upgrade -r "$REQUIREMENTS"

info "Validating Python dependency consistency"
"${PIP[@]}" check

info "Validating TeleSaved imports"
"$PYTHON" - <<'PY'
import sys
import aiohttp
import telethon
import ldap3
import torch
import torchvision
import clip
import PIL
import ultralytics
import numpy
import numba
import faiss
import whisper

print("Python:", sys.version.replace("\n", " "))
print("aiohttp:", aiohttp.__version__)
print("Telethon:", telethon.__version__)
print("ldap3:", ldap3.__version__)
print("torch:", torch.__version__)
print("torchvision:", torchvision.__version__)
print("clip:", clip.__file__)
print("Pillow:", PIL.__version__)
print("Ultralytics:", ultralytics.__version__)
print("NumPy:", numpy.__version__)
print("Numba:", numba.__version__)
print("FAISS:", getattr(faiss, "__version__", "version not exposed"))
print("Whisper:", whisper.__file__)
assert torch.cuda.is_available() is False, "CPU package unexpectedly reports CUDA available"
PY

info "Validating application syntax and Whisper CLI"
"$PYTHON" -m py_compile "$APP_DIR/app.py"
"$VENV_DIR/bin/whisper" --help >/dev/null

rm -rf "$BACKUP_VENV"
trap - EXIT
printf '\n[OK] TeleSaved runtime environment is ready at %s\n' "$VENV_DIR"
RUNTIME_INSTALLER

    chmod 0755 "$package_root/usr/lib/telesaved/install-runtime.sh"
}

write_service() {
    local package_root="$BUILD_ROOT/pkg"

    cat > "$package_root/lib/systemd/system/telesaved.service" <<'SERVICE'
[Unit]
Description=TeleSaved AI local media archive
Wants=network-online.target
After=network-online.target local-fs.target

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/opt/telesaved
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=CUDA_VISIBLE_DEVICES=
Environment=PATH=/opt/telesaved/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=TELESAVED_WHISPER_BIN=/opt/telesaved/venv/bin/whisper
ExecStart=/opt/telesaved/venv/bin/python /opt/telesaved/app.py
Restart=on-failure
RestartSec=10
TimeoutStartSec=0
KillSignal=SIGINT

[Install]
WantedBy=multi-user.target
SERVICE
}

write_helper_commands() {
    local package_root="$BUILD_ROOT/pkg"

    cat > "$package_root/usr/local/sbin/telesaved-telegram-login" <<'LOGIN_HELPER'
#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

APP_DIR="/opt/telesaved"
PYTHON="$APP_DIR/venv/bin/python"
CONFIG="$APP_DIR/config.json"
SERVICE="telesaved.service"

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
    printf 'Run this command as root: sudo telesaved-telegram-login\n' >&2
    exit 1
}
[[ -x "$PYTHON" ]] || {
    printf 'TeleSaved virtual environment is missing. Run: sudo telesaved-repair-dependencies\n' >&2
    exit 1
}
[[ -f "$CONFIG" ]] || {
    printf 'Telegram credentials are not configured yet. Complete the TeleSaved setup page first.\n' >&2
    exit 2
}

systemctl stop "$SERVICE" >/dev/null 2>&1 || true
restart_service() {
    systemctl start "$SERVICE" >/dev/null 2>&1 || true
}
trap restart_service EXIT

cd "$APP_DIR"
"$PYTHON" - <<'PY'
import asyncio
import json
from pathlib import Path
from telethon import TelegramClient

app_dir = Path("/opt/telesaved")
config_path = app_dir / "config.json"
session_path = app_dir / "telesaved_session"

try:
    config = json.loads(config_path.read_text(encoding="utf-8"))
except Exception as exc:
    raise SystemExit(f"Cannot read {config_path}: {exc}")

try:
    api_id = int(config.get("api_id") or 0)
except Exception as exc:
    raise SystemExit(f"api_id is invalid: {exc}")
api_hash = str(config.get("api_hash") or "").strip()

if not api_id or not api_hash:
    raise SystemExit("config.json does not contain a valid api_id and api_hash.")

async def authorize():
    print("TeleSaved Telegram authorization")
    print("Telegram may ask for your phone number, login code, and two-step-verification password.")

    client = TelegramClient(str(session_path), api_id, api_hash)
    try:
        await client.start()
        if not await client.is_user_authorized():
            raise SystemExit("Telegram authorization did not complete.")
        me = await client.get_me()
        name = " ".join(
            part
            for part in [getattr(me, "first_name", ""), getattr(me, "last_name", "")]
            if part
        ).strip()
        username = getattr(me, "username", None)
        identity = name or (
            f"@{username}" if username else str(getattr(me, "id", "authorized user"))
        )
        print(f"Telegram authorization completed for: {identity}")
    finally:
        await client.disconnect()

asyncio.run(authorize())
PY

printf '\nTelegram is authorized. TeleSaved is being restarted.\n'
LOGIN_HELPER

    cat > "$package_root/usr/local/sbin/telesaved-repair-dependencies" <<'REPAIR_HELPER'
#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
    printf 'Run this command as root: sudo telesaved-repair-dependencies\n' >&2
    exit 1
}

systemctl stop telesaved.service >/dev/null 2>&1 || true
restart_service() {
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl start telesaved.service >/dev/null 2>&1 || true
}
trap restart_service EXIT

/usr/lib/telesaved/install-runtime.sh
printf '\nTeleSaved dependencies were rebuilt successfully.\n'
REPAIR_HELPER

    chmod 0755 \
        "$package_root/usr/local/sbin/telesaved-telegram-login" \
        "$package_root/usr/local/sbin/telesaved-repair-dependencies"
}

write_maintainer_scripts() {
    local package_root="$BUILD_ROOT/pkg"

    cat > "$package_root/DEBIAN/postinst" <<'POSTINST'
#!/usr/bin/env bash
set -Eeuo pipefail

case "${1:-configure}" in
    configure|abort-upgrade|abort-remove|abort-deconfigure)
        ;;
    *)
        exit 0
        ;;
esac

install -d -m 0755 /opt/telesaved
systemctl stop telesaved.service >/dev/null 2>&1 || true

recover_service() {
    systemctl daemon-reload >/dev/null 2>&1 || true
    systemctl start telesaved.service >/dev/null 2>&1 || true
}
trap recover_service ERR

/usr/lib/telesaved/install-runtime.sh

systemctl daemon-reload
systemctl enable telesaved.service >/dev/null
systemctl restart telesaved.service
trap - ERR

printf '\nTeleSaved AI 4.4.3-2 installed.\n'
printf 'Web interface: http://SERVER-IP:8080\n'
printf 'Telegram login: sudo telesaved-telegram-login\n'
printf 'Dependency repair: sudo telesaved-repair-dependencies\n'
POSTINST

    cat > "$package_root/DEBIAN/prerm" <<'PRERM'
#!/usr/bin/env bash
set -e

case "${1:-remove}" in
    upgrade)
        systemctl stop telesaved.service >/dev/null 2>&1 || true
        ;;
    remove|deconfigure)
        systemctl disable --now telesaved.service >/dev/null 2>&1 || true
        ;;
esac

exit 0
PRERM

    cat > "$package_root/DEBIAN/postrm" <<'POSTRM'
#!/usr/bin/env bash
set -e

systemctl daemon-reload >/dev/null 2>&1 || true

if [[ "${1:-}" == "purge" ]]; then
    rm -rf /opt/telesaved/venv /opt/telesaved/venv.pre-package-install
fi

exit 0
POSTRM

    chmod 0755 \
        "$package_root/DEBIAN/postinst" \
        "$package_root/DEBIAN/prerm" \
        "$package_root/DEBIAN/postrm"
}

write_control_metadata() {
    local package_root="$BUILD_ROOT/pkg"
    local installed_size

    installed_size="$(du -sk "$package_root" | awk '{print $1}')"

    cat > "$package_root/DEBIAN/control" <<CONTROL
Package: ${PACKAGE_NAME}
Version: ${PACKAGE_VERSION}
Section: utils
Priority: optional
Architecture: all
Maintainer: Jamie Villegas <jamie@simgaymer.com>
Depends: python3 (>= 3.13), python3-venv, python3-pip, ffmpeg, git, ca-certificates, libgl1, libgomp1, libglib2.0-0 | libglib2.0-0t64
Installed-Size: ${installed_size}
Homepage: https://github.com/simgaymer/telesaved-ai
Description: Local-first Telegram media archive with AI indexing
 TeleSaved AI downloads and organizes configured Telegram video sources,
 generates previews, performs CLIP and YOLO analysis, provides semantic
 FAISS search, supports Whisper transcription, and exposes a local web UI.
 .
 This package creates its Python virtual environment on the target machine,
 preventing Python 3.13/3.14 and cross-distribution venv incompatibilities.
CONTROL

    cat > "$package_root/usr/share/doc/${PACKAGE_NAME}/changelog.Debian" <<CHANGELOG
telesaved-ai (${PACKAGE_VERSION}) unstable; urgency=medium

  * Build the Python virtual environment on the target system.
  * Support Python 3.13 and Python 3.14 target installations.
  * Install CPU-only PyTorch 2.13 and torchvision 0.28.
  * Install OpenAI CLIP, Whisper, FAISS, Ultralytics, Telethon, aiohttp, and LDAP.
  * Keep the web interface running when Telegram credentials exist but the
    Telethon session has not yet been authorized.
  * Add telesaved-telegram-login and telesaved-repair-dependencies helpers.
  * Pin NumPy below 2.5 for Numba 0.66 / Whisper compatibility.
  * Pin setuptools below 81 for OpenAI CLIP's legacy setup.py.

 -- Jamie Villegas <jamie@simgaymer.com>  Wed, 22 Jul 2026 15:00:00 -0500
CHANGELOG

    gzip -9n "$package_root/usr/share/doc/${PACKAGE_NAME}/changelog.Debian"

    cat > "$package_root/usr/share/doc/${PACKAGE_NAME}/README.Debian" <<'README'
TeleSaved AI Debian package
===========================

This package deliberately creates /opt/telesaved/venv on the machine where
it is installed. It does not copy a venv from the machine that built the .deb.
This permits a package built on Ubuntu with Python 3.14 to be installed on
Debian 13 / Proxmox with Python 3.13.

The target machine must have internet access during package configuration so
pip can retrieve CPU PyTorch and the other Python runtime dependencies.

Useful commands:

  systemctl status telesaved.service --no-pager
  journalctl -u telesaved.service -n 100 --no-pager
  sudo telesaved-telegram-login
  sudo telesaved-repair-dependencies

Application-generated configuration, Telegram session files, databases, and
media metadata are intentionally not included in the package build.
README
}

normalize_permissions() {
    local package_root="$BUILD_ROOT/pkg"

    find "$package_root" -type d -exec chmod 0755 {} +
    find "$package_root" -type f -exec chmod 0644 {} +

    chmod 0755 \
        "$package_root/DEBIAN/postinst" \
        "$package_root/DEBIAN/prerm" \
        "$package_root/DEBIAN/postrm" \
        "$package_root/usr/lib/telesaved/install-runtime.sh" \
        "$package_root/usr/local/sbin/telesaved-telegram-login" \
        "$package_root/usr/local/sbin/telesaved-repair-dependencies"

    # Preserve executable bits on any application scripts from the source tree.
    while IFS= read -r -d '' script; do
        chmod 0755 "$script"
    done < <(find "$package_root/opt/telesaved" -maxdepth 2 -type f -name '*.sh' -print0)
}

validate_package_tree() {
    local package_root="$BUILD_ROOT/pkg"

    info "Validating package contents"

    bash -n "$package_root/DEBIAN/postinst"
    bash -n "$package_root/DEBIAN/prerm"
    bash -n "$package_root/DEBIAN/postrm"
    bash -n "$package_root/usr/lib/telesaved/install-runtime.sh"
    bash -n "$package_root/usr/local/sbin/telesaved-telegram-login"
    bash -n "$package_root/usr/local/sbin/telesaved-repair-dependencies"
    python_syntax_check "$package_root/opt/telesaved/app.py"

    if find "$package_root/opt/telesaved" -maxdepth 2 \
        \( -name 'config.json' -o -name 'telesaved_session*' -o -name 'telesaved.db*' -o -name 'state.json' \) \
        -print -quit | grep -q .; then
        die "The package contains machine-specific TeleSaved data."
    fi

    ok "Package tree validation passed."
}

build_package() {
    local package_root="$BUILD_ROOT/pkg"

    info "Building Debian package"
    rm -f "$PACKAGE_FILE"
    dpkg-deb --root-owner-group --build "$package_root" "$PACKAGE_FILE"

    [[ -s "$PACKAGE_FILE" ]] || die "dpkg-deb did not create the package."
}

validate_built_package() {
    info "Inspecting the completed Debian package"

    dpkg-deb --info "$PACKAGE_FILE"

    local contents_file="$BUILD_ROOT/package-contents.txt"
    dpkg-deb --contents "$PACKAGE_FILE" > "$contents_file"

    grep -q './opt/telesaved/app.py' "$contents_file" || die "Built package is missing app.py."
    grep -q './lib/systemd/system/telesaved.service' "$contents_file" || die "Built package is missing the service unit."
    grep -q './usr/local/sbin/telesaved-telegram-login' "$contents_file" || die "Built package is missing the Telegram helper."
    grep -q './usr/lib/telesaved/install-runtime.sh' "$contents_file" || die "Built package is missing the runtime installer."

    ok "Debian package contents validated."
}

print_summary() {
    local size sha
    size="$(du -h "$PACKAGE_FILE" | awk '{print $1}')"
    sha="$(sha256sum "$PACKAGE_FILE" | awk '{print $1}')"

    printf '\n============================================================\n'
    printf 'TeleSaved AI package build complete\n'
    printf 'Package: %s\n' "$PACKAGE_FILE"
    printf 'Size:    %s\n' "$size"
    printf 'SHA256:  %s\n' "$sha"
    printf 'Log:     %s\n' "$LOG_FILE"
    printf '============================================================\n'
    printf '\nInstall on the Debian/Proxmox target with:\n\n'
    printf '  sudo apt install %q\n' "$PACKAGE_FILE"
    printf '\nAfter completing the web setup, authorize Telegram with:\n\n'
    printf '  sudo telesaved-telegram-login\n\n'
}

require_root
prepare_output
ensure_build_tools
require_source_tree
create_package_tree
copy_application
patch_application
write_runtime_requirements
write_runtime_installer
write_service
write_helper_commands
write_maintainer_scripts
write_control_metadata
normalize_permissions
validate_package_tree
build_package
validate_built_package
print_summary
