"""Interactive post-install configurator for cock-monitor."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cock_monitor.defaults import DEFAULT_ENV_FILE
from cock_monitor.env import parse_env_file

InputFn = Callable[[str], str]

BASE_SERVICES = (
    "cock-monitor.service",
    "cock-monitor-telegram-bot.service",
    "cock-monitor-daily.service",
)
BASE_TIMERS = (
    "cock-monitor.timer",
    "cock-monitor-telegram-bot.timer",
    "cock-monitor-daily.timer",
)
MODULE_UNITS = {
    "mtproxy": (
        "cock-mtproxy-monitor.service",
        "cock-mtproxy-monitor.timer",
        "cock-mtproxy-daily.service",
        "cock-mtproxy-daily.timer",
    ),
    "incident": (
        "cock-monitor-incident-sampler.service",
        "cock-monitor-incident-sampler.timer",
    ),
    "shaper": ("cock-shaper.service", "cock-shaper.timer"),
    "vless": ("cock-vless-daily.service", "cock-vless-daily.timer"),
}


@dataclass
class WizardState:
    env_values: dict[str, str]
    touched_sections: set[str]
    selected_modules: set[str]
    env_file: Path


def _yes_no(prompt: str, default: bool, ask: InputFn) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    while True:
        raw = ask(prompt + suffix).strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please answer y or n.")


def _prompt_nonempty(prompt: str, current: str, ask: InputFn) -> str:
    while True:
        shown = f"{prompt} [{current}]: " if current else f"{prompt}: "
        raw = ask(shown).strip()
        if raw:
            return raw
        if current:
            return current
        print("Value cannot be empty.")


def _prompt_int(prompt: str, current: str, ask: InputFn, minimum: int = 0) -> str:
    while True:
        shown = f"{prompt} [{current}]: " if current else f"{prompt}: "
        raw = ask(shown).strip() or current
        try:
            value = int(raw)
        except ValueError:
            print("Please enter an integer.")
            continue
        if value < minimum:
            print(f"Value must be >= {minimum}.")
            continue
        return str(value)


def _prompt_float(prompt: str, current: str, ask: InputFn, minimum: float = 0.0) -> str:
    while True:
        shown = f"{prompt} [{current}]: " if current else f"{prompt}: "
        raw = ask(shown).strip() or current
        try:
            value = float(raw)
        except ValueError:
            print("Please enter a number.")
            continue
        if value < minimum:
            print(f"Value must be >= {minimum}.")
            continue
        return str(value)


def _set_bool(env_values: dict[str, str], key: str, enabled: bool) -> None:
    env_values[key] = "1" if enabled else "0"


def _configure_core(state: WizardState, ask: InputFn) -> None:
    env_values = state.env_values
    print("\n[core] Telegram + conntrack + metrics")
    env_values["TELEGRAM_BOT_TOKEN"] = _prompt_nonempty(
        "TELEGRAM_BOT_TOKEN",
        env_values.get("TELEGRAM_BOT_TOKEN", ""),
        ask,
    )
    env_values["TELEGRAM_CHAT_ID"] = _prompt_nonempty(
        "TELEGRAM_CHAT_ID",
        env_values.get("TELEGRAM_CHAT_ID", ""),
        ask,
    )
    env_values["WARN_PERCENT"] = _prompt_int("WARN_PERCENT", env_values.get("WARN_PERCENT", "80"), ask, 1)
    env_values["CRIT_PERCENT"] = _prompt_int("CRIT_PERCENT", env_values.get("CRIT_PERCENT", "95"), ask, 1)
    env_values["COOLDOWN_SECONDS"] = _prompt_int(
        "COOLDOWN_SECONDS", env_values.get("COOLDOWN_SECONDS", "3600"), ask, 0
    )
    env_values["METRICS_RETENTION_DAYS"] = _prompt_int(
        "METRICS_RETENTION_DAYS", env_values.get("METRICS_RETENTION_DAYS", "14"), ask, 0
    )
    state.touched_sections.add("core")


def _configure_mtproxy(state: WizardState, ask: InputFn) -> None:
    env_values = state.env_values
    print("\n[module: mtproxy]")
    env_values["MTPROXY_PORT"] = _prompt_int("MTPROXY_PORT", env_values.get("MTPROXY_PORT", "8443"), ask, 1)
    env_values["MTPROXY_ALERT_COOLDOWN_MINUTES"] = _prompt_int(
        "MTPROXY_ALERT_COOLDOWN_MINUTES",
        env_values.get("MTPROXY_ALERT_COOLDOWN_MINUTES", "30"),
        ask,
        0,
    )
    env_values["MTPROXY_MAX_CONNECTIONS_PER_IP"] = _prompt_int(
        "MTPROXY_MAX_CONNECTIONS_PER_IP",
        env_values.get("MTPROXY_MAX_CONNECTIONS_PER_IP", "20"),
        ask,
        1,
    )
    env_values["MTPROXY_MAX_UNIQUE_IPS"] = _prompt_int(
        "MTPROXY_MAX_UNIQUE_IPS",
        env_values.get("MTPROXY_MAX_UNIQUE_IPS", "50"),
        ask,
        1,
    )
    env_values["MTPROXY_DAILY_TOP_N"] = _prompt_int(
        "MTPROXY_DAILY_TOP_N",
        env_values.get("MTPROXY_DAILY_TOP_N", "10"),
        ask,
        1,
    )
    state.selected_modules.add("mtproxy")


def _configure_incident(state: WizardState, ask: InputFn) -> None:
    env_values = state.env_values
    print("\n[module: incident sampler]")
    _set_bool(
        env_values,
        "INCIDENT_ALERT_ENABLE",
        _yes_no("Enable INCIDENT_ALERT_ENABLE?", env_values.get("INCIDENT_ALERT_ENABLE", "0") == "1", ask),
    )
    env_values["INCIDENT_ALERT_COOLDOWN_SEC"] = _prompt_int(
        "INCIDENT_ALERT_COOLDOWN_SEC",
        env_values.get("INCIDENT_ALERT_COOLDOWN_SEC", "300"),
        ask,
        0,
    )
    env_values["INCIDENT_LOG_DIR"] = _prompt_nonempty(
        "INCIDENT_LOG_DIR",
        env_values.get("INCIDENT_LOG_DIR", "/var/lib/cock-monitor"),
        ask,
    )
    env_values["INCIDENT_STATE_FILE"] = _prompt_nonempty(
        "INCIDENT_STATE_FILE",
        env_values.get("INCIDENT_STATE_FILE", "/var/lib/cock-monitor/incident_sampler.state"),
        ask,
    )
    if _yes_no(
        "Configure TCP probe for service port (recommended for burst diagnosis)?",
        bool(env_values.get("INCIDENT_TCP_PROBE_PORTS", "").strip()),
        ask,
    ):
        env_values["INCIDENT_TCP_PROBE_PORTS"] = _prompt_nonempty(
            "INCIDENT_TCP_PROBE_PORTS",
            env_values.get("INCIDENT_TCP_PROBE_PORTS", "443"),
            ask,
        )
        env_values["INCIDENT_TCP_PROBE_LOCAL_TARGET"] = _prompt_nonempty(
            "INCIDENT_TCP_PROBE_LOCAL_TARGET",
            env_values.get("INCIDENT_TCP_PROBE_LOCAL_TARGET", "127.0.0.1"),
            ask,
        )
        env_values["INCIDENT_TCP_PROBE_EXTERNAL_TARGET"] = _prompt_nonempty(
            "INCIDENT_TCP_PROBE_EXTERNAL_TARGET (public VPS IP)",
            env_values.get("INCIDENT_TCP_PROBE_EXTERNAL_TARGET", ""),
            ask,
        )
    state.selected_modules.add("incident")


def _configure_shaper(state: WizardState, ask: InputFn) -> None:
    env_values = state.env_values
    print("\n[module: shaper]")
    env_values["SHAPER_IFACE"] = _prompt_nonempty("SHAPER_IFACE", env_values.get("SHAPER_IFACE", "ens3"), ask)
    env_values["SHAPER_MAX_RATE_MBIT"] = _prompt_int(
        "SHAPER_MAX_RATE_MBIT", env_values.get("SHAPER_MAX_RATE_MBIT", "100"), ask, 1
    )
    env_values["SHAPER_MIN_RATE_MBIT"] = _prompt_int(
        "SHAPER_MIN_RATE_MBIT", env_values.get("SHAPER_MIN_RATE_MBIT", "10"), ask, 1
    )
    env_values["SHAPER_CPU_TARGET_PCT"] = _prompt_int(
        "SHAPER_CPU_TARGET_PCT", env_values.get("SHAPER_CPU_TARGET_PCT", "70"), ask, 1
    )
    state.selected_modules.add("shaper")


def _configure_vless(state: WizardState, ask: InputFn) -> None:
    env_values = state.env_values
    print("\n[module: vless]")
    env_values["XUI_DB_PATH"] = _prompt_nonempty("XUI_DB_PATH", env_values.get("XUI_DB_PATH", ""), ask)
    env_values["VLESS_DAILY_TZ"] = _prompt_nonempty(
        "VLESS_DAILY_TZ", env_values.get("VLESS_DAILY_TZ", "Europe/Moscow"), ask
    )
    env_values["VLESS_TELEGRAM_DISPLAY_TZ"] = _prompt_nonempty(
        "VLESS_TELEGRAM_DISPLAY_TZ",
        env_values.get("VLESS_TELEGRAM_DISPLAY_TZ", "Europe/Moscow"),
        ask,
    )
    env_values["VLESS_DAILY_TOP_N"] = _prompt_int(
        "VLESS_DAILY_TOP_N", env_values.get("VLESS_DAILY_TOP_N", "10"), ask, 1
    )
    env_values["VLESS_ABUSE_GB"] = _prompt_float("VLESS_ABUSE_GB", env_values.get("VLESS_ABUSE_GB", "20.0"), ask, 0.0)
    env_values["VLESS_ABUSE_SHARE_PCT"] = _prompt_float(
        "VLESS_ABUSE_SHARE_PCT", env_values.get("VLESS_ABUSE_SHARE_PCT", "40.0"), ask, 0.0
    )
    state.selected_modules.add("vless")


def _print_review(state: WizardState) -> None:
    print("\n=== Review ===")
    print(f"env file: {state.env_file}")
    print(f"sections touched: {', '.join(sorted(state.touched_sections)) or 'none'}")
    print(f"modules selected: {', '.join(sorted(state.selected_modules)) or 'none'}")
    keys = [
        "ENABLED_MODULES",
        "TELEGRAM_CHAT_ID",
        "WARN_PERCENT",
        "CRIT_PERCENT",
        "XUI_DB_PATH",
    ]
    for key in keys:
        val = state.env_values.get(key, "")
        if key == "TELEGRAM_CHAT_ID" and val:
            val = f"...{val[-4:]}"
        print(f"- {key}={val}")
    print("Type 'ok' to apply, anything else to return to menu.")


def _dump_env(env_values: dict[str, str]) -> str:
    lines = [f"{k}={env_values[k]}" for k in sorted(env_values.keys())]
    return "\n".join(lines) + "\n"


def _run_cmd(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _ensure_root() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("Run configurator as root (sudo).")


def _install_units(repo_root: Path, unit_names: set[str]) -> None:
    for unit in sorted(unit_names):
        src = repo_root / "systemd" / unit
        if not src.is_file():
            raise RuntimeError(f"systemd unit not found: {src}")
        shutil.copyfile(src, Path("/etc/systemd/system") / unit)


def _write_override(service: str, repo_root: Path, python_bin: Path, env_file: Path) -> None:
    dropin = Path("/etc/systemd/system") / f"{service}.d"
    dropin.mkdir(parents=True, exist_ok=True)
    path = dropin / "override.conf"
    if service == "cock-monitor-telegram-bot.service":
        body = (
            "[Service]\n"
            f"WorkingDirectory={repo_root}\n"
            f"Environment=COCK_MONITOR_HOME={repo_root}\n"
            "ExecStart=\n"
            f"ExecStart={python_bin} -m telegram_bot --poll-once {env_file}\n"
        )
    elif service == "cock-monitor-daily.service":
        body = (
            "[Service]\n"
            f"WorkingDirectory={repo_root}\n"
            "ExecStart=\n"
            f"ExecStart={python_bin} -m cock_monitor daily-chart --env-file {env_file} --send-telegram\n"
        )
    elif service == "cock-monitor.service":
        body = (
            "[Service]\n"
            f"WorkingDirectory={repo_root}\n"
            "ExecStart=\n"
            f"ExecStart={python_bin} -m cock_monitor conntrack-check {env_file}\n"
        )
    elif service == "cock-mtproxy-monitor.service":
        body = (
            "[Service]\n"
            f"WorkingDirectory={repo_root}\n"
            "ExecStart=\n"
            f"ExecStart={python_bin} -m cock_monitor mtproxy-collect --env-file {env_file}\n"
        )
    elif service == "cock-mtproxy-daily.service":
        body = (
            "[Service]\n"
            f"WorkingDirectory={repo_root}\n"
            "ExecStart=\n"
            f"ExecStart={python_bin} -m cock_monitor mtproxy-daily --env-file {env_file} --hours 24 --send-telegram\n"
        )
    elif service == "cock-vless-daily.service":
        body = (
            "[Service]\n"
            f"WorkingDirectory={repo_root}\n"
            "ExecStart=\n"
            f"ExecStart={python_bin} -m cock_monitor vless-report --env-file {env_file} --send-telegram --mode daily\n"
        )
    elif service == "cock-monitor-incident-sampler.service":
        body = (
            "[Service]\n"
            f"WorkingDirectory={repo_root}\n"
            "ExecStart=\n"
            f"ExecStart={python_bin} -m cock_monitor.services.incident_sampler {env_file}\n"
        )
    else:
        body = (
            "[Service]\n"
            "ExecStart=\n"
            f"ExecStart={repo_root}/bin/cock-cpu-shaper.sh {env_file}\n"
        )
    path.write_text(body, encoding="utf-8")


def _sync_enabled_modules(state: WizardState) -> None:
    modules = {"core", *state.selected_modules}
    order = ["core"] + sorted(m for m in modules if m != "core")
    state.env_values["ENABLED_MODULES"] = ",".join(order)
    for legacy in ("MTPROXY_ENABLE", "INCIDENT_SAMPLER_ENABLE", "SHAPER_ENABLE"):
        state.env_values.pop(legacy, None)


def _apply_configuration(state: WizardState, repo_root: Path) -> None:
    _ensure_root()
    _sync_enabled_modules(state)
    data = _dump_env(state.env_values)
    target = state.env_file
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_suffix(target.suffix + ".bak")
        shutil.copyfile(target, backup)
        print(f"backup saved: {backup}")
    target.write_text(data, encoding="utf-8")
    os.chmod(target, 0o600)

    unit_names = set(BASE_SERVICES + BASE_TIMERS)
    for module in state.selected_modules:
        unit_names.update(MODULE_UNITS[module])
    _install_units(repo_root, unit_names)

    python_bin = repo_root / ".venv" / "bin" / "python"
    for service in sorted(name for name in unit_names if name.endswith(".service")):
        _write_override(service, repo_root, python_bin, target)

    _run_cmd(["systemctl", "daemon-reload"])
    timers = sorted(name for name in unit_names if name.endswith(".timer"))
    if timers:
        _run_cmd(["systemctl", "enable", "--now", *timers])
    _run_cmd([str(python_bin), "-m", "cock_monitor", "preflight", str(target)])
    _run_cmd([str(python_bin), "-m", "cock_monitor", "config-check", str(target)])
    print("\napply complete. active timers:")
    for timer in timers:
        print(f"- {timer}")


def run(argv: list[str] | None = None, *, input_fn: InputFn = input) -> int:
    parser = argparse.ArgumentParser(description="Interactive cock-monitor configurator")
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)

    env_file = args.env_file.expanduser().resolve()
    if env_file.is_file():
        env_values = parse_env_file(env_file)
    else:
        env_values = {}
    env_values.setdefault("LA_ALERT_ENABLE", "0")

    state = WizardState(env_values=env_values, touched_sections=set(), selected_modules=set(), env_file=env_file)

    while True:
        print(
            "\nSelect action:\n"
            "1) Configure core\n"
            "2) Configure module\n"
            "3) Review and apply\n"
            "4) Exit without apply"
        )
        action = input_fn("> ").strip()
        if action == "1":
            _configure_core(state, input_fn)
        elif action == "2":
            module = input_fn("Module (mtproxy/incident/shaper/vless): ").strip().lower()
            if module == "mtproxy":
                _configure_mtproxy(state, input_fn)
            elif module == "incident":
                _configure_incident(state, input_fn)
            elif module == "shaper":
                _configure_shaper(state, input_fn)
            elif module == "vless":
                _configure_vless(state, input_fn)
            else:
                print("Unknown module.")
        elif action == "3":
            _print_review(state)
            if input_fn("> ").strip().lower() == "ok":
                _apply_configuration(state, args.repo_root.expanduser().resolve())
                return 0
        elif action == "4":
            print("Exit without changes.")
            return 0
        else:
            print("Unknown action.")

