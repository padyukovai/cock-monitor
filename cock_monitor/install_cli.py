"""Install / uninstall cock-monitor v2."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from cock_monitor.platform.config import build_env_from_profile, repo_root, write_env_file
from cock_monitor.platform.daily_runners import exec_start_line, is_daily_service
from cock_monitor.platform.profile_ops import format_post_install_checklist, load_profile_ops
from cock_monitor.platform.registry import get_registry
from cock_monitor.platform.storage.manager import StorageManager

LEGACY_UNITS = [
    "cock-monitor.service",
    "cock-monitor.timer",
    "cock-monitor-telegram-bot.service",
    "cock-monitor-telegram-bot.timer",
    "cock-monitor-daily.service",
    "cock-monitor-daily.timer",
    "cock-mtproxy-monitor.service",
    "cock-mtproxy-monitor.timer",
    "cock-mtproxy-daily.service",
    "cock-mtproxy-daily.timer",
    "cock-vless-daily.service",
    "cock-vless-daily.timer",
    "cock-shaper.service",
    "cock-shaper.timer",
    "cock-monitor-incident-sampler.service",
    "cock-monitor-incident-sampler.timer",
]

V2_UNITS = [
    "cock-monitor-core.service",
    "cock-monitor-core.timer",
    "cock-monitor-telegram.service",
    "cock-monitor-telegram.timer",
    "cock-monitor-wg.service",
    "cock-monitor-wg.timer",
    "cock-monitor-mtproxy.service",
    "cock-monitor-mtproxy.timer",
    "cock-monitor-incident.service",
    "cock-monitor-incident.timer",
    "cock-monitor-shaper.service",
    "cock-monitor-shaper.timer",
    "cock-monitor-hop.service",
    "cock-monitor-hop.timer",
]


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, check=check, text=True)


def uninstall(*, wipe_data: bool, env_file: Path, data_dir: Path) -> int:
    all_units = LEGACY_UNITS + V2_UNITS
    timers = [u for u in all_units if u.endswith(".timer")]
    try:
        _run(["systemctl", "disable", "--now", *timers], check=False)
    except FileNotFoundError:
        pass
    systemd_dir = Path("/etc/systemd/system")
    for unit in all_units:
        path = systemd_dir / unit
        if path.is_file():
            path.unlink()
        dropin = systemd_dir / f"{unit}.d"
        if dropin.is_dir():
            shutil.rmtree(dropin)
    try:
        _run(["systemctl", "daemon-reload"], check=False)
    except FileNotFoundError:
        pass
    if wipe_data:
        if data_dir.is_dir():
            shutil.rmtree(data_dir)
        if env_file.is_file():
            env_file.unlink()
    return 0


def _write_systemd_override(
    service: str, repo: Path, python_bin: Path, env_file: Path
) -> None:
    dropin = Path("/etc/systemd/system") / f"{service}.d"
    dropin.mkdir(parents=True, exist_ok=True)
    exec_line = exec_start_line(python_bin, env_file, service) if is_daily_service(service) else None
    if exec_line:
        body = (
            "[Service]\n"
            f"WorkingDirectory={repo}\n"
            f"Environment=COCK_MONITOR_HOME={repo}\n"
            "ExecStart=\n"
            f"ExecStart={exec_line}\n"
        )
    else:
        body = (
            "[Service]\n"
            f"WorkingDirectory={repo}\n"
            f"Environment=COCK_MONITOR_HOME={repo}\n"
        )
    (dropin / "override.conf").write_text(body, encoding="utf-8")


def collect_install_units(env: dict[str, str]) -> set[str]:
    """Return systemd unit names to install for the given env (testable without root)."""
    return get_registry().install_systemd_units(env)


def run_post_install_scripts(profile: str, repo: Path) -> int:
    """Run profile POST_INSTALL_SCRIPTS (only when explicitly requested)."""
    try:
        ops = load_profile_ops(profile)
    except FileNotFoundError as e:
        print(f"post-install: {e}", file=sys.stderr)
        return 1
    if not ops.post_install_scripts:
        print("post-install: no scripts for profile", file=sys.stderr)
        return 0
    rc = 0
    for script in ops.post_install_scripts:
        path = (repo / script).resolve()
        if not path.is_file():
            print(f"ERROR: post-install script not found: {path}", file=sys.stderr)
            rc = 1
            continue
        print(f"post-install: bash {script}")
        result = subprocess.run(["bash", str(path)], cwd=str(repo), check=False)
        if result.returncode != 0:
            rc = result.returncode
    return rc


def print_post_install_checklist(profile: str) -> None:
    for line in format_post_install_checklist(profile):
        print(line)


def install(
    *,
    profile: str,
    modules_override: list[str] | None,
    wipe_data: bool,
    env_file: Path,
    data_dir: Path,
    token: str | None,
    chat_id: str | None,
    repo: Path,
    run_post_install: bool = False,
) -> int:
    if os.geteuid() != 0:
        print("install: run as root (sudo)", file=sys.stderr)
        return 1

    uninstall(wipe_data=wipe_data, env_file=env_file, data_dir=data_dir)

    venv = repo / ".venv"
    python_bin = venv / "bin" / "python"
    if not python_bin.is_file():
        _run([sys.executable, "-m", "venv", str(venv)])
        _run([str(venv / "bin" / "pip"), "install", "--upgrade", "pip", "wheel"])
        _run([str(venv / "bin" / "pip"), "install", "-e", f"{repo}[chart]"])

    env = build_env_from_profile(profile, modules_override=modules_override)
    if token:
        env["TELEGRAM_BOT_TOKEN"] = token
    if chat_id:
        env["TELEGRAM_CHAT_ID"] = chat_id
    write_env_file(env_file, env)

    data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(data_dir, 0o700)

    db_path = Path(env.get("METRICS_DB", str(data_dir / "metrics.db")))
    if wipe_data:
        StorageManager(db_path).wipe()
    StorageManager(db_path).migrate_all(env)

    registry = get_registry()
    units_to_install = registry.install_systemd_units(env)

    systemd_src = repo / "systemd"
    systemd_dst = Path("/etc/systemd/system")
    for unit in units_to_install:
        src = systemd_src / unit
        if not src.is_file():
            print(f"warn: missing unit template {src}", file=sys.stderr)
            continue
        shutil.copyfile(src, systemd_dst / unit)
        if unit.endswith(".service"):
            _write_systemd_override(unit, repo, python_bin, env_file)

    _run(["systemctl", "daemon-reload"])
    timers = sorted(u for u in units_to_install if u.endswith(".timer"))
    if timers:
        _run(["systemctl", "enable", "--now", *timers])

    _run([str(python_bin), "-m", "cock_monitor", "run", "core", str(env_file), "--dry-run"])
    print("install complete. enabled timers:")
    for t in timers:
        print(f"  {t}")
    print_post_install_checklist(profile)
    if run_post_install:
        post_rc = run_post_install_scripts(profile, repo)
        if post_rc != 0:
            return post_rc
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cock-monitor v2 install")
    parser.add_argument("--profile", default="core")
    parser.add_argument("--modules", help="Override ENABLED_MODULES (comma-separated)")
    parser.add_argument("--wipe-data", action="store_true")
    parser.add_argument("--env-file", default="/etc/cock-monitor.env")
    parser.add_argument("--data-dir", default="/var/lib/cock-monitor")
    parser.add_argument("--repo", default=str(repo_root()))
    parser.add_argument("--token")
    parser.add_argument("--chat-id")
    parser.add_argument(
        "--run-post-install",
        action="store_true",
        help="Run profile POST_INSTALL_SCRIPTS after install (default: print checklist only)",
    )
    parser.add_argument("command", choices=["install", "uninstall"], nargs="?", default="install")
    args = parser.parse_args(argv)

    env_file = Path(args.env_file)
    data_dir = Path(args.data_dir)
    modules = args.modules.split(",") if args.modules else None

    if args.command == "uninstall":
        return uninstall(wipe_data=args.wipe_data, env_file=env_file, data_dir=data_dir)
    return install(
        profile=args.profile,
        modules_override=modules,
        wipe_data=args.wipe_data,
        env_file=env_file,
        data_dir=data_dir,
        token=args.token,
        chat_id=args.chat_id,
        repo=Path(args.repo).resolve(),
        run_post_install=args.run_post_install,
    )
