"""python -m cock_monitor [preflight] [...]"""

from __future__ import annotations

import sys

from cock_monitor.preflight import main as preflight_main


def main(argv: list[str] | None = None) -> int:
    a = list(argv if argv is not None else sys.argv[1:])
    if a[:1] == ["daily-chart"]:
        from cock_monitor.daily_chart_cli import run as daily_chart_run

        return daily_chart_run(a[1:])
    if a[:1] == ["vless-report"]:
        from cock_monitor.services.vless_report import run as vless_report_run

        return vless_report_run(a[1:])
    if a[:1] == ["mtproxy-collect"]:
        from cock_monitor.mtproxy_collect_cli import run as mtproxy_collect_run

        return mtproxy_collect_run(a[1:])
    if a[:1] == ["mtproxy-daily"]:
        from cock_monitor.mtproxy_daily_cli import run as mtproxy_daily_run

        return mtproxy_daily_run(a[1:])
    if a[:1] == ["conntrack-check"]:
        from cock_monitor.conntrack_check_cli import run as conntrack_check_run

        return conntrack_check_run(a[1:])
    if a[:1] == ["conntrack-decide"]:
        from cock_monitor.conntrack_decide_cli import run as conntrack_decide_run

        return conntrack_decide_run(a[1:])
    if a[:1] == ["conntrack-storage"]:
        from cock_monitor.conntrack_storage_cli import run as conntrack_storage_run

        return conntrack_storage_run(a[1:])
    if a[:1] == ["config-check"]:
        from cock_monitor.config_check_cli import run as config_check_run

        return config_check_run(a[1:])
    if a[:1] == ["preflight"]:
        a = a[1:]
    if a[:1] in (["help"], ["-h"], ["--help"]):
        return preflight_main(["--help"])
    return preflight_main(a)


if __name__ == "__main__":
    raise SystemExit(main())
