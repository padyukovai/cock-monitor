from __future__ import annotations

from pathlib import Path

from cock_monitor import configure_cli


def _make_input(responses: list[str]):
    items = iter(responses)

    def _input(_: str) -> str:
        return next(items)

    return _input


def test_run_exit_without_apply(tmp_path: Path) -> None:
    env_file = tmp_path / "test.env"
    rc = configure_cli.run(
        ["--env-file", str(env_file), "--repo-root", str(tmp_path)],
        input_fn=_make_input(["4"]),
    )
    assert rc == 0
    assert not env_file.exists()


def test_run_configure_core_then_apply(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / "test.env"
    applied: dict[str, object] = {}

    def fake_apply(state: configure_cli.WizardState, repo_root: Path) -> None:
        applied["repo_root"] = repo_root
        applied["warn"] = state.env_values["WARN_PERCENT"]
        applied["modules"] = set(state.selected_modules)
        env_file.write_text("ok\n", encoding="utf-8")

    monkeypatch.setattr(configure_cli, "_apply_configuration", fake_apply)

    rc = configure_cli.run(
        ["--env-file", str(env_file), "--repo-root", str(tmp_path)],
        input_fn=_make_input(
            [
                "1",
                "token",
                "123",
                "81",
                "96",
                "3600",
                "15",
                "3",
                "ok",
            ]
        ),
    )

    assert rc == 0
    assert applied["repo_root"] == tmp_path.resolve()
    assert applied["warn"] == "81"
    assert applied["modules"] == set()
    assert env_file.exists()


def test_run_configure_module_then_back_to_menu_and_cancel_apply(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / "test.env"
    called = {"apply": 0}

    def fake_apply(state: configure_cli.WizardState, repo_root: Path) -> None:
        called["apply"] += 1

    monkeypatch.setattr(configure_cli, "_apply_configuration", fake_apply)

    rc = configure_cli.run(
        ["--env-file", str(env_file), "--repo-root", str(tmp_path)],
        input_fn=_make_input(
            [
                "2",
                "mtproxy",
                "8443",
                "30",
                "20",
                "50",
                "10",
                "3",
                "back",
                "4",
            ]
        ),
    )

    assert rc == 0
    assert called["apply"] == 0

