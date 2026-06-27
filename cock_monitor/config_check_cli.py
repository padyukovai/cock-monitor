"""CLI for config validation and diagnostics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cock_monitor.config_loader import ConfigValidationResult, load_config, validate_config
from cock_monitor.config_schema import AppConfig
from cock_monitor.defaults import DEFAULT_ENV_FILE
from cock_monitor.platform.config import build_env_from_profile
from cock_monitor.platform.profile_validation import validate_profile_env


def _merge_validation(
    base: ConfigValidationResult,
    profile_errors: list[str],
    profile_warnings: list[str],
) -> ConfigValidationResult:
    return ConfigValidationResult(
        errors=[*base.errors, *profile_errors],
        warnings=[*base.warnings, *profile_warnings],
    )


def _print_validation(result: ConfigValidationResult, *, path_label: str) -> int:
    for w in result.warnings:
        print(f"warn: {w}")
    for e in result.errors:
        print(f"ERROR: {e}")
    if result.ok:
        print(f"ok: config is valid ({path_label})")
        return 0
    return 1


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="cock-monitor config validator")
    parser.add_argument(
        "env_file",
        nargs="?",
        type=Path,
        default=None,
        help=f"Path to env file (default: {DEFAULT_ENV_FILE})",
    )
    parser.add_argument(
        "--profile",
        help="Validate merged env from profile (env file optional)",
    )
    args = parser.parse_args(argv)

    if args.profile:
        try:
            env = build_env_from_profile(args.profile)
        except FileNotFoundError as e:
            print(f"config-check: {e}", file=sys.stderr)
            return 1
        app = AppConfig.from_env_map(env)
        base = validate_config(app)
        profile_errors, profile_warnings = validate_profile_env(env, profile=args.profile)
        result = _merge_validation(base, profile_errors, profile_warnings)
        if args.profile:
            secret_errors = [e for e in result.errors if "TELEGRAM" in e]
            if secret_errors:
                errors = [e for e in result.errors if e not in secret_errors]
                result = ConfigValidationResult(errors=errors, warnings=result.warnings)
        return _print_validation(result, path_label=f"profile:{args.profile}")

    path = (args.env_file or DEFAULT_ENV_FILE).expanduser().resolve()
    if not path.is_file():
        print(f"config-check: env file not found: {path}", file=sys.stderr)
        return 1

    try:
        loaded = load_config(path)
    except OSError as e:
        print(f"config-check: cannot read env file {path}: {e}", file=sys.stderr)
        return 1

    profile_errors, profile_warnings = validate_profile_env(loaded.app.raw)
    result = _merge_validation(loaded.validation, profile_errors, profile_warnings)
    return _print_validation(result, path_label=str(path))
