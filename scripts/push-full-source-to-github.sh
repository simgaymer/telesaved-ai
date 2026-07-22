#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-git@github.com:simgaymer/telesaved-ai.git}"
APP_DIR="${APP_DIR:-/opt/telesaved}"
WORK_DIR="${WORK_DIR:-/tmp/telesaved-ai-github-sync}"
BRANCH="${BRANCH:-main}"

log(){ echo; echo "==== $* ===="; }
fail(){ echo "ERROR: $*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || fail "git is required"
[ -f "${APP_DIR}/app.py" ] || fail "${APP_DIR}/app.py not found"

log "Preparing sync workspace"
rm -rf "${WORK_DIR}"
git clone "${REPO_URL}" "${WORK_DIR}"
cd "${WORK_DIR}"
git checkout "${BRANCH}"

log "Copying live TeleSaved source"
cp "${APP_DIR}/app.py" ./app.py
mkdir -p frontend
if [ -f "${APP_DIR}/frontend/index.html" ]; then
  cp "${APP_DIR}/frontend/index.html" ./frontend/index.html
elif [ -f "${APP_DIR}/index.html" ]; then
  cp "${APP_DIR}/index.html" ./frontend/index.html
else
  fail "Could not find frontend index.html under ${APP_DIR}"
fi

# Copy safe supporting files if present.
for f in build-telesaved-deb.sh requirements.txt config.example.json install-ldap-deps.sh LICENSE NOTICE SECURITY.md CHANGELOG.md README.md; do
  if [ -f "${APP_DIR}/${f}" ]; then
    cp "${APP_DIR}/${f}" "./${f}"
  fi
done

if [ -d "${APP_DIR}/docs" ]; then
  mkdir -p docs
  cp -a "${APP_DIR}/docs/." docs/
fi
if [ -d "${APP_DIR}/scripts" ]; then
  mkdir -p scripts
  cp -a "${APP_DIR}/scripts/." scripts/
fi
if [ -d "${APP_DIR}/systemd" ]; then
  mkdir -p systemd
  cp -a "${APP_DIR}/systemd/." systemd/
fi

log "Safety check: refusing to commit local secrets/runtime state"
rm -f config.json telesaved_session* *.session *.session-journal auth.json state.json settings.json feedback.json jobs.json telesaved.db telesaved.db-* telesaved.log whisper.log trash_index.json
rm -rf frames previews thumbs scenes audio trash backups telegram_metadata dist build

log "Committing changes"
git add .
if git diff --cached --quiet; then
  echo "No changes to commit."
  exit 0
fi

git commit -m "Sync TeleSaved AI source from deployed application"
git push origin "${BRANCH}"

log "Done"
echo "Repository updated: ${REPO_URL}"
