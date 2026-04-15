from __future__ import annotations

from pathlib import Path

from cock_monitor.preflight import run_preflight


def test_preflight_checks_incident_runtime_tools(tmp_path: Path, capsys, monkeypatch) -> None:
    env_path = tmp_path / "incident.env"
    env_path.write_text(
        "\n".join(
            [
                "INCIDENT_SAMPLER_ENABLE=1",
                'INCIDENT_SYSTEMD_UNITS="x-ui.service"',
                'INCIDENT_TCP_PROBE_PORTS="443"',
            ]
        ),
        encoding="utf-8",
    )

    available = {"python3", "curl", "sqlite3", "conntrack", "ping", "timeout", "getent", "ss"}
    monkeypatch.setattr(
        "cock_monitor.preflight._which",
        lambda name: f"/usr/bin/{name}" if name in available else None,
    )

    rc = run_preflight(env_path, minimal=False, implicit_env_path=False)
    out = capsys.readouterr().out

    assert rc == 1
    assert "ERROR: missing: systemctl" in out
    assert "ERROR: missing: ip" in out
    assert "ok: timeout -> /usr/bin/timeout" in out
    assert "ok: getent -> /usr/bin/getent" in out
    assert "ok: ss -> /usr/bin/ss" in out
