#!/usr/bin/env bash
# Push chatMCD from the Mac to the droplet and restart the service.
#
#   bash deploy/deploy.sh
#
# Reads DROPLET_SSH / DROPLET_PATH from .env. Never touches the server's own
# state: the venv, the question database and .env are all excluded.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# One deploy at a time. Two overlapping runs rsync and re-chown the same tree,
# and there is a window where the service user cannot write the database.
LOCKFILE="${TMPDIR:-/tmp}/chatmcd-deploy.lock"
exec 9>"$LOCKFILE"
if command -v flock >/dev/null 2>&1; then
  flock -n 9 || { echo "Another deploy is already running ($LOCKFILE)."; exit 1; }
else
  LOCKDIR="${LOCKFILE%.lock}.lockdir"
  mkdir "$LOCKDIR" 2>/dev/null || { echo "Another deploy is running ($LOCKDIR)."; exit 1; }
  trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT
fi

# .env is a systemd EnvironmentFile, NOT a bash script. systemd does not run a
# shell, so `RATE_LIMIT=20 per minute` is legal there and the service reads it
# fine — but `source` runs `per minute` as a command and set -e aborts the whole
# deploy on line 6. Read only the keys this script needs, without a shell.
read_env() {
  [[ -f .env ]] || return 0
  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *=* ]] || continue
    key="${line%%=*}"; val="${line#*=}"
    case "$key" in DROPLET_SSH|DROPLET_PATH|SERVER_NAME|SSH_KEY) ;; *) continue ;; esac
    val="${val%\"}"; val="${val#\"}"          # systemd strips surrounding quotes
    val="${val%\'}"; val="${val#\'}"
    printf -v "$key" '%s' "$val"
  done < .env
}
read_env
DROPLET_SSH="${DROPLET_SSH:-}"
DROPLET_PATH="${DROPLET_PATH:-/opt/chatmcd}"
SERVER_NAME="${SERVER_NAME:-chatmcd.mdeller.com}"
SSH_KEY="${SSH_KEY:-}"

[[ -n "$DROPLET_SSH" ]] || { echo "DROPLET_SSH is not set. Copy .env.example to .env."; exit 1; }

SSH_OPTS=()
[[ -n "$SSH_KEY" ]] && SSH_OPTS=(-e "ssh -i ${SSH_KEY/#\~/$HOME}")

echo "==> Syncing to ${DROPLET_SSH}:${DROPLET_PATH}"
# training/ is 20 MB of corpus the server has no use for; adapters/ and models/
# are gigabytes that live on Hugging Face, not here.
rsync -az --delete ${SSH_OPTS[@]+"${SSH_OPTS[@]}"} \
  --exclude '.venv/' --exclude '.venv-gradio/' --exclude '__pycache__/' --exclude '*.pyc' \
  --exclude '.git/' --exclude '.env' \
  --exclude 'var/' --exclude 'logs/' --exclude 'dist/' \
  --exclude 'adapters/' --exclude 'models/' --exclude 'training/' \
  --exclude 'trash/' --exclude '*.zip' --exclude '.DS_Store' \
  ./ "${DROPLET_SSH}:${DROPLET_PATH}/"

echo "==> Installing dependencies and restarting"
SSH_CMD=(ssh)
[[ -n "$SSH_KEY" ]] && SSH_CMD=(ssh -i "${SSH_KEY/#\~/$HOME}")
"${SSH_CMD[@]}" "$DROPLET_SSH" bash -s <<REMOTE
set -euo pipefail
cd "${DROPLET_PATH}"
if [[ ! -x .venv/bin/python ]]; then
  echo "No venv yet: run deploy/provision.sh as root first."; exit 1
fi
sudo -u chatmcd env PIP_NO_CACHE_DIR=1 ./.venv/bin/pip install --quiet -r chatmcd/requirements.txt
# rsync runs as root and leaves new files root-owned. Chown, but prune the venv
# and the live database: a blanket chown over an open SQLite file has taken a
# sibling app down mid-run.
sudo find "${DROPLET_PATH}" \
  -path "${DROPLET_PATH}/.venv" -prune -o \
  -path "${DROPLET_PATH}/var" -prune -o \
  -exec chown chatmcd:chatmcd {} +
sudo install -d -o chatmcd -g chatmcd "${DROPLET_PATH}/var"
sudo systemctl restart chatmcd-web.service
sleep 2
sudo systemctl is-active chatmcd-web.service
REMOTE

echo "==> Verifying the live site"
# The deploy exiting 0 says the commands ran, not that the site serves the new
# build. Fetch it back and check.
HEALTH=$(curl -sf --max-time 20 "https://${SERVER_NAME}/api/health" || true)
if [[ "$HEALTH" == *'"ok": true'* || "$HEALTH" == *'"ok":true'* ]]; then
  echo "    https://${SERVER_NAME}/ is up: ${HEALTH}"
else
  echo "    WARNING: https://${SERVER_NAME}/api/health did not answer as expected."
  echo "    got: ${HEALTH:-<nothing>}"
  exit 1
fi
# The embed route is the one WordPress depends on; check it separately.
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "https://${SERVER_NAME}/embed")
[[ "$CODE" == "200" ]] && echo "    /embed 200" || { echo "    WARNING: /embed returned $CODE"; exit 1; }
