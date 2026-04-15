#!/usr/bin/env bash
# Thin wrapper: incident sampler logic lives in cock_monitor.services.incident_sampler (Python).
set -euo pipefail

umask 077

_COCK_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_COCK_REPO_ROOT="$(cd "${_COCK_SCRIPT_DIR}/.." && pwd)"
export PYTHONPATH="${_COCK_REPO_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"

exec python3 -m cock_monitor.services.incident_sampler "$@"
