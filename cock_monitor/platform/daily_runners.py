"""Daily report systemd units and their cock_monitor CLI entrypoints."""

from __future__ import annotations

from pathlib import Path

# service unit name -> argv after `python -m cock_monitor`
DAILY_SERVICE_COMMANDS: dict[str, list[str]] = {
    "cock-monitor-daily.service": [
        "daily-chart",
        "--env-file",
        "{env}",
        "--send-telegram",
    ],
    "cock-vless-daily.service": [
        "vless-report",
        "--env-file",
        "{env}",
        "--send-telegram",
        "--mode",
        "daily",
    ],
    "cock-mtproxy-daily.service": [
        "mtproxy-daily",
        "--env-file",
        "{env}",
        "--hours",
        "24",
        "--send-telegram",
    ],
}


def daily_service_names() -> frozenset[str]:
    return frozenset(DAILY_SERVICE_COMMANDS)


def is_daily_service(service: str) -> bool:
    return service in DAILY_SERVICE_COMMANDS


def exec_start_argv(python_bin: Path, env_file: Path, service: str) -> list[str] | None:
    """Full argv for systemd ExecStart, or None if service is not a daily unit."""
    template = DAILY_SERVICE_COMMANDS.get(service)
    if template is None:
        return None
    env_s = str(env_file)
    args = [str(python_bin), "-m", "cock_monitor"]
    for part in template:
        args.append(env_s if part == "{env}" else part)
    return args


def exec_start_line(python_bin: Path, env_file: Path, service: str) -> str | None:
    argv = exec_start_argv(python_bin, env_file, service)
    if argv is None:
        return None
    return " ".join(argv)
