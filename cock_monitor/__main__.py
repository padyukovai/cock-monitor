"""python -m cock_monitor [preflight] [...]"""

from __future__ import annotations

import sys

from cock_monitor.preflight import main as preflight_main


def main(argv: list[str] | None = None) -> int:
    a = list(argv if argv is not None else sys.argv[1:])
    if a[:1] == ["conntrack-decide"]:
        from cock_monitor.conntrack_decide_cli import run as conntrack_decide_run

        return conntrack_decide_run(a[1:])
    if a[:1] == ["conntrack-storage"]:
        from cock_monitor.conntrack_storage_cli import run as conntrack_storage_run

        return conntrack_storage_run(a[1:])
    if a[:1] == ["preflight"]:
        a = a[1:]
    if a[:1] in (["help"], ["-h"], ["--help"]):
        return preflight_main(["--help"])
    return preflight_main(a)


if __name__ == "__main__":
    raise SystemExit(main())
