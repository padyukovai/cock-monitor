"""Redirect to platform telegram CLI."""

from cock_monitor.platform.telegram.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
