"""python -m cock_monitor — v2 modular CLI."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    a = list(argv if argv is not None else sys.argv[1:])

    if a[:1] == ["run"]:
        from cock_monitor.run_cli import run

        return run(a[1:])
    if a[:1] == ["modules"]:
        from cock_monitor.run_cli import list_modules_cmd

        return list_modules_cmd(a[1:])
    if a[:1] == ["install"]:
        from cock_monitor.install_cli import main as install_main

        return install_main(["install", *a[1:]])
    if a[:1] == ["uninstall"]:
        from cock_monitor.install_cli import main as install_main

        return install_main(["uninstall", *a[1:]])
    if a[:1] == ["daily-chart"]:
        from pathlib import Path

        from cock_monitor.run_cli import run_module

        env = Path(a[2] if len(a) > 2 and not a[1].startswith("-") else "/etc/cock-monitor.env")
        from cock_monitor.run_cli import _run_core_daily

        return _run_core_daily(env)
    if a[:1] == ["conntrack-check"]:
        from pathlib import Path

        from cock_monitor.run_cli import run_module

        dry = "--dry-run" in a
        env_arg = next((x for x in a[1:] if not x.startswith("-")), "/etc/cock-monitor.env")
        return run_module("core", Path(env_arg), dry_run=dry)
    if a[:1] == ["vless-report"]:
        from cock_monitor.services.vless_report import run as vless_run

        return vless_run(a[1:])
    if a[:1] == ["mtproxy-collect"]:
        from cock_monitor.mtproxy_collect_cli import run as mtproxy_run

        return mtproxy_run(a[1:])
    if a[:1] == ["mtproxy-daily"]:
        from cock_monitor.mtproxy_daily_cli import run as mtproxy_daily_run

        return mtproxy_daily_run(a[1:])
    if a[:1] == ["conntrack-decide"]:
        from cock_monitor.conntrack_decide_cli import run as conntrack_decide_run

        return conntrack_decide_run(a[1:])
    if a[:1] == ["config-check"]:
        from cock_monitor.config_check_cli import run as config_check_run

        return config_check_run(a[1:])
    if a[:1] == ["preflight"]:
        from cock_monitor.preflight import main as preflight_main

        return preflight_main(a[1:] if len(a) > 1 else [])
    if a[:1] == ["burst-capture"]:
        from cock_monitor.burst_capture_cli import run as burst_capture_run

        return burst_capture_run(a[1:])
    if a[:1] == ["leak-investigation"]:
        from cock_monitor.leak_investigation_cli import run as leak_inv_run

        return leak_inv_run(a[1:])
    if a[:1] == ["telegram"]:
        from cock_monitor.platform.telegram.__main__ import main as telegram_main

        return telegram_main()
    if a[:1] in (["help"], ["-h"], ["--help"]):
        print(
            "Usage: python -m cock_monitor "
            "{run|modules|install|uninstall|preflight|config-check|burst-capture|leak-investigation|telegram} ...\n"
            "  run <module> [env_file] [--dry-run]\n"
            "  install --profile stack-3xui [--role exit-node] [--wipe-data]\n"
            "  burst-capture --env-file /etc/cock-monitor.env start --duration 60"
        )
        return 0

    from cock_monitor.preflight import main as preflight_main

    return preflight_main(a)


if __name__ == "__main__":
    raise SystemExit(main())
