#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="/etc/cock-monitor.env"
DATA_DIR="/var/lib/cock-monitor"
VENV_DIR="${REPO_ROOT}/.venv"
PYTHON_BIN="${VENV_DIR}/bin/python"
PIP_BIN="${VENV_DIR}/bin/pip"

SERVICES=(
  "cock-monitor.service"
  "cock-monitor-telegram-bot.service"
  "cock-monitor-daily.service"
)

TIMERS=(
  "cock-monitor.timer"
  "cock-monitor-telegram-bot.timer"
  "cock-monitor-daily.timer"
)

log() {
  printf '[install] %s\n' "$1"
}

die() {
  printf '[install] ERROR: %s\n' "$1" >&2
  exit 1
}

ensure_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "Запустите скрипт через sudo: sudo bash install/install-ubuntu-minimal.sh"
  fi
}

ensure_linux_systemd() {
  [[ -f /etc/os-release ]] || die "Не найден /etc/os-release"
  # shellcheck source=/dev/null
  . /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    log "Внимание: скрипт тестировался для Ubuntu (обнаружено: ${ID:-unknown})"
  fi
  command -v systemctl >/dev/null 2>&1 || die "Не найден systemctl (нужен systemd)"
}

ensure_repo_layout() {
  [[ -f "${REPO_ROOT}/pyproject.toml" ]] || die "Не найден pyproject.toml (запускайте из этого репозитория)"
  [[ -d "${REPO_ROOT}/systemd" ]] || die "Не найден каталог systemd/"
  [[ -f "${REPO_ROOT}/config.minimal.env" ]] || die "Не найден config.minimal.env"
}

prompt_nonempty() {
  local prompt="$1"
  local value=""
  while true; do
    read -r -p "${prompt}" value
    if [[ -n "${value}" ]]; then
      printf '%s' "${value}"
      return 0
    fi
    echo "Значение не может быть пустым. Повторите ввод."
  done
}

prompt_token() {
  local value=""
  while true; do
    read -r -s -p "TELEGRAM_BOT_TOKEN: " value
    echo
    if [[ -n "${value}" ]]; then
      printf '%s' "${value}"
      return 0
    fi
    echo "Токен не может быть пустым. Повторите ввод."
  done
}

install_system_dependencies() {
  log "Устанавливаю системные зависимости (apt)..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    curl \
    sqlite3 \
    conntrack \
    python3-matplotlib
}

setup_python_env() {
  log "Готовлю Python-окружение в ${VENV_DIR}..."
  if [[ ! -x "${PYTHON_BIN}" ]]; then
    python3 -m venv "${VENV_DIR}"
  fi
  "${PIP_BIN}" install --upgrade pip wheel
  "${PIP_BIN}" install -e "${REPO_ROOT}[chart]"
}

write_env_file() {
  local token="$1"
  local chat_id="$2"

  if [[ -f "${ENV_FILE}" ]]; then
    read -r -p "${ENV_FILE} уже существует. Перезаписать? [y/N]: " overwrite
    if [[ ! "${overwrite:-}" =~ ^[Yy]$ ]]; then
      log "Оставляю существующий ${ENV_FILE} без изменений."
      return 0
    fi
  fi

  log "Создаю ${ENV_FILE} с минимальной конфигурацией..."
  cat >"${ENV_FILE}" <<EOF
TELEGRAM_BOT_TOKEN=${token}
TELEGRAM_CHAT_ID=${chat_id}

WARN_PERCENT=80
CRIT_PERCENT=95
COOLDOWN_SECONDS=3600

STATE_FILE=${DATA_DIR}/state
METRICS_DB=${DATA_DIR}/metrics.db
METRICS_RECORD_EVERY_RUN=1
METRICS_RETENTION_DAYS=14

CHECK_CONNTRACK_FILL=1
INCLUDE_CONNTRACK_STATS_LINE=1
DRY_RUN=0

MTPROXY_ENABLE=0
INCIDENT_SAMPLER_ENABLE=0
SHAPER_ENABLE=0
LA_ALERT_ENABLE=0
EOF
  chmod 600 "${ENV_FILE}"
}

prepare_data_dir() {
  log "Создаю runtime-каталог ${DATA_DIR}..."
  mkdir -p "${DATA_DIR}"
  chmod 700 "${DATA_DIR}"
}

install_systemd_units() {
  local unit
  local source_path
  local dropin_dir
  local override_file

  log "Устанавливаю systemd unit/timer файлы..."
  for unit in "${SERVICES[@]}" "${TIMERS[@]}"; do
    source_path="${REPO_ROOT}/systemd/${unit}"
    [[ -f "${source_path}" ]] || die "Не найден unit: ${source_path}"
    install -m 644 "${source_path}" "/etc/systemd/system/${unit}"
  done

  for unit in "${SERVICES[@]}"; do
    dropin_dir="/etc/systemd/system/${unit}.d"
    override_file="${dropin_dir}/override.conf"
    mkdir -p "${dropin_dir}"

    if [[ "${unit}" == "cock-monitor-telegram-bot.service" ]]; then
      cat >"${override_file}" <<EOF
[Service]
WorkingDirectory=${REPO_ROOT}
Environment=COCK_MONITOR_HOME=${REPO_ROOT}
ExecStart=
ExecStart=${PYTHON_BIN} -m telegram_bot --poll-once ${ENV_FILE}
EOF
    elif [[ "${unit}" == "cock-monitor-daily.service" ]]; then
      cat >"${override_file}" <<EOF
[Service]
WorkingDirectory=${REPO_ROOT}
ExecStart=
ExecStart=${PYTHON_BIN} -m cock_monitor daily-chart --env-file ${ENV_FILE} --send-telegram
EOF
    else
      cat >"${override_file}" <<EOF
[Service]
WorkingDirectory=${REPO_ROOT}
ExecStart=
ExecStart=${PYTHON_BIN} -m cock_monitor conntrack-check ${ENV_FILE}
EOF
    fi
  done
}

validate_configuration() {
  log "Запускаю preflight и config-check..."
  "${PYTHON_BIN}" -m cock_monitor preflight "${ENV_FILE}"
  "${PYTHON_BIN}" -m cock_monitor config-check "${ENV_FILE}"
}

enable_timers() {
  log "Включаю таймеры..."
  systemctl daemon-reload
  systemctl enable --now \
    cock-monitor.timer \
    cock-monitor-telegram-bot.timer \
    cock-monitor-daily.timer
}

print_summary() {
  echo
  log "Установка завершена."
  echo "Что включено:"
  echo "  - cock-monitor.timer"
  echo "  - cock-monitor-telegram-bot.timer"
  echo "  - cock-monitor-daily.timer"
  echo
  echo "Полезные команды диагностики:"
  echo "  systemctl list-timers --all | awk 'NR==1 || /cock-monitor/'"
  echo "  systemctl status cock-monitor.service --no-pager"
  echo "  systemctl status cock-monitor-telegram-bot.service --no-pager"
  echo "  systemctl status cock-monitor-daily.service --no-pager"
  echo "  journalctl -u cock-monitor.service -n 100 --no-pager"
  echo "  journalctl -u cock-monitor-telegram-bot.service -n 100 --no-pager"
  echo "  journalctl -u cock-monitor-daily.service -n 100 --no-pager"
}

main() {
  ensure_root
  ensure_linux_systemd
  ensure_repo_layout

  cat <<'EOF'
=== cock-monitor: минимальная интерактивная установка (Ubuntu) ===

Нужно ввести:
1) TELEGRAM_BOT_TOKEN:
   - откройте @BotFather, создайте бота, скопируйте токен.
2) TELEGRAM_CHAT_ID:
   - отправьте сообщение боту;
   - откройте https://api.telegram.org/bot<TOKEN>/getUpdates
     и возьмите поле chat.id.

Скрипт создаст /etc/cock-monitor.env, /var/lib/cock-monitor,
установит базовые зависимости и включит 3 systemd timer-а.
EOF

  local token
  local chat_id
  token="$(prompt_token)"
  chat_id="$(prompt_nonempty "TELEGRAM_CHAT_ID: ")"

  install_system_dependencies
  setup_python_env
  write_env_file "${token}" "${chat_id}"
  prepare_data_dir
  install_systemd_units
  validate_configuration
  enable_timers
  print_summary
}

main "$@"
