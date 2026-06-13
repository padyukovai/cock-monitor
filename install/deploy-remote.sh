#!/usr/bin/env bash
# Remote redeploy helper — run from workstation with SSH access.
set -euo pipefail

HOST="${1:?Usage: deploy-remote.sh <ssh-host> <profile>}"
PROFILE="${2:?Usage: deploy-remote.sh <ssh-host> <profile>}"
REPO="${APP_DIR:-/opt/cock-monitor}"

ssh -o BatchMode=yes "$HOST" "cd '$REPO' && git pull && sudo bash install/uninstall.sh --wipe-data && sudo bash install/install.sh --profile '$PROFILE' --wipe-data"
