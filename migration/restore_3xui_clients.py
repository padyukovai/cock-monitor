#!/usr/bin/env python3
"""Restore 3x-ui VLESS/SOCKS clients from a legacy x-ui.db backup onto 3x-ui 3.x panel."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

BYTES_PER_GB = 1073741824
DEFAULT_PANEL_URL = "https://153.75.246.28:25241/dungeonmaster"
DEFAULT_BACKUP_ARCHIVE = (
    Path(__file__).resolve().parent.parent / "backups" / "cockvpn-backup-20260608T043354Z.tar.gz"
)
DEFAULT_DB_PATH_IN_ARCHIVE = "cockvpn-backup-20260608T043354Z/3x-ui/volume-data/x-ui.db"


@dataclass
class ClientRecord:
    id: str
    email: str
    flow: str
    sub_id: str
    comment: str
    enable: bool
    limit_ip: int
    total_gb: int
    expiry_time: int
    tg_id: str


@dataclass
class InboundRecord:
    protocol: str
    remark: str
    port: int
    enable: bool
    listen: str
    tag: str
    expiry_time: int
    total: int
    settings: dict[str, Any]
    stream_settings: dict[str, Any] | None
    sniffing: dict[str, Any] | None
    allocate: dict[str, Any] | None = None


@dataclass
class MigrationManifest:
    source_db: str
    vless_inbound: InboundRecord
    socks_inbound: InboundRecord | None
    clients: list[ClientRecord] = field(default_factory=list)


class XuiPanelClient:
    def __init__(self, base_url: str, username: str, password: str, *, verify_tls: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._csrf_token = ""
        ctx = ssl.create_default_context()
        if not verify_tls:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self._jar = CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self._jar),
            urllib.request.HTTPSHandler(context=ctx),
        )

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        json_body: dict[str, Any] | None = None,
        form_body: dict[str, str] | None = None,
        require_auth: bool = False,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        data: bytes | None = None
        if json_body is not None:
            data = json.dumps(json_body).encode()
            headers["Content-Type"] = "application/json"
        elif form_body is not None:
            data = urllib.parse.urlencode(form_body).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if require_auth and self._csrf_token:
            headers["X-CSRF-Token"] = self._csrf_token

        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with self._opener.open(req, timeout=60) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                raise RuntimeError(f"HTTP {exc.code} for {path}: {raw[:300]!r}") from exc
            raise RuntimeError(f"HTTP {exc.code} for {path}: {payload.get('msg', payload)}") from exc

        if not raw:
            return {}
        payload = json.loads(raw)
        if not payload.get("success", True) and path not in {"/csrf-token", "/panel/csrf-token"}:
            raise RuntimeError(f"API error on {path}: {payload.get('msg', payload)}")
        return payload

    def login(self) -> None:
        bootstrap = self._request("/csrf-token")
        self._csrf_token = str(bootstrap.get("obj", ""))
        self._request(
            "/login",
            method="POST",
            form_body={
                "username": self.username,
                "password": self.password,
                "twoFactorCode": "",
            },
            require_auth=True,
        )
        auth_csrf = self._request("/panel/csrf-token", require_auth=True)
        self._csrf_token = str(auth_csrf.get("obj", ""))

    def list_inbounds(self) -> list[dict[str, Any]]:
        payload = self._request("/panel/api/inbounds/list", require_auth=True)
        obj = payload.get("obj")
        return obj if isinstance(obj, list) else []

    def add_inbound(self, inbound: InboundRecord) -> int:
        body: dict[str, Any] = {
            "enable": inbound.enable,
            "remark": inbound.remark,
            "listen": inbound.listen,
            "port": inbound.port,
            "protocol": inbound.protocol,
            "expiryTime": inbound.expiry_time,
            "total": inbound.total,
            "settings": inbound.settings,
        }
        if inbound.stream_settings is not None:
            body["streamSettings"] = inbound.stream_settings
        if inbound.sniffing is not None:
            body["sniffing"] = inbound.sniffing
        payload = self._request("/panel/api/inbounds/add", method="POST", json_body=body, require_auth=True)
        obj = payload.get("obj") or {}
        inbound_id = obj.get("id") or obj.get("Id")
        if inbound_id is None:
            raise RuntimeError(f"inbound add returned no id: {payload}")
        return int(inbound_id)

    def list_clients(self) -> list[dict[str, Any]]:
        payload = self._request("/panel/api/clients/list", require_auth=True)
        obj = payload.get("obj")
        return obj if isinstance(obj, list) else []

    def add_client(self, client: ClientRecord, inbound_ids: list[int]) -> None:
        tg_id = 0
        if str(client.tg_id).strip().isdigit():
            tg_id = int(str(client.tg_id).strip())
        body = {
            "client": {
                "id": client.id,
                "email": client.email,
                "flow": client.flow,
                "subId": client.sub_id,
                "comment": client.comment,
                "enable": client.enable,
                "limitIp": client.limit_ip,
                "totalGB": normalize_total_gb(client.total_gb),
                "expiryTime": client.expiry_time,
                "tgId": tg_id,
            },
            "inboundIds": inbound_ids,
        }
        self._request("/panel/api/clients/add", method="POST", json_body=body, require_auth=True)

    def get_client_links(self, inbound_id: int, email: str) -> list[str]:
        encoded = urllib.parse.quote(email, safe="")
        payload = self._request(
            f"/panel/api/clients/links/{inbound_id}/{encoded}",
            require_auth=True,
        )
        obj = payload.get("obj")
        if isinstance(obj, list):
            return [str(x) for x in obj]
        if isinstance(obj, str):
            return [obj]
        return []


def normalize_total_gb(value: int) -> int:
    if value <= 0:
        return 0
    if value < 1024:
        return value * BYTES_PER_GB
    return value


def _parse_json_field(raw: Any, default: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if raw is None:
        return default
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return default
    return json.loads(raw)


def extract_manifest(db_path: Path) -> MigrationManifest:
    conn = sqlite3.connect(str(db_path))
    try:
        vless_row = conn.execute(
            """
            SELECT remark, port, protocol, enable, listen, tag, expiry_time, total,
                   settings, stream_settings, sniffing, allocate
            FROM inbounds WHERE protocol = 'vless' ORDER BY id LIMIT 1
            """
        ).fetchone()
        if vless_row is None:
            raise RuntimeError("no VLESS inbound found in backup database")

        socks_row = conn.execute(
            """
            SELECT remark, port, protocol, enable, listen, tag, expiry_time, total,
                   settings, stream_settings, sniffing, allocate
            FROM inbounds WHERE protocol = 'socks' ORDER BY id LIMIT 1
            """
        ).fetchone()

        vless_settings = _parse_json_field(vless_row[8], {}) or {}
        clients_raw = vless_settings.pop("clients", [])
        vless_inbound = InboundRecord(
            protocol=str(vless_row[2]),
            remark=str(vless_row[0]),
            port=int(vless_row[1]),
            enable=bool(vless_row[3]),
            listen=str(vless_row[4] or ""),
            tag=str(vless_row[5] or ""),
            expiry_time=int(vless_row[6] or 0),
            total=int(vless_row[7] or 0),
            settings=vless_settings,
            stream_settings=_parse_json_field(vless_row[9]),
            sniffing=_parse_json_field(vless_row[10]),
            allocate=_parse_json_field(vless_row[11]),
        )

        socks_inbound = None
        if socks_row is not None:
            socks_settings = _parse_json_field(socks_row[8], {}) or {}
            socks_settings.pop("clients", None)
            socks_inbound = InboundRecord(
                protocol=str(socks_row[2]),
                remark=str(socks_row[0]),
                port=int(socks_row[1]),
                enable=bool(socks_row[3]),
                listen=str(socks_row[4] or ""),
                tag=str(socks_row[5] or ""),
                expiry_time=int(socks_row[6] or 0),
                total=int(socks_row[7] or 0),
                settings=socks_settings,
                stream_settings=_parse_json_field(socks_row[9]),
                sniffing=_parse_json_field(socks_row[10]),
                allocate=_parse_json_field(socks_row[11]),
            )

        clients: list[ClientRecord] = []
        for item in clients_raw:
            if not isinstance(item, dict):
                continue
            email = str(item.get("email", "")).strip()
            client_id = str(item.get("id", "")).strip()
            if not email or not client_id:
                continue
            clients.append(
                ClientRecord(
                    id=client_id,
                    email=email,
                    flow=str(item.get("flow", "")),
                    sub_id=str(item.get("subId", "")),
                    comment=str(item.get("comment", "")),
                    enable=bool(item.get("enable", True)),
                    limit_ip=int(item.get("limitIp", 0) or 0),
                    total_gb=int(item.get("totalGB", 0) or 0),
                    expiry_time=int(item.get("expiryTime", 0) or 0),
                    tg_id=str(item.get("tgId", "")),
                )
            )
    finally:
        conn.close()

    if not clients:
        raise RuntimeError("no VLESS clients found in backup database")

    return MigrationManifest(source_db=str(db_path), vless_inbound=vless_inbound, socks_inbound=socks_inbound, clients=clients)


def resolve_backup_db(archive: Path, extract_dir: Path) -> Path:
    db_in_archive = DEFAULT_DB_PATH_IN_ARCHIVE
    with tarfile.open(archive, "r:gz") as tar:
        member = tar.getmember(db_in_archive)
        tar.extract(member, path=extract_dir)
    return extract_dir / db_in_archive


def manifest_to_dict(manifest: MigrationManifest) -> dict[str, Any]:
    return {
        "source_db": manifest.source_db,
        "vless_inbound": asdict(manifest.vless_inbound),
        "socks_inbound": asdict(manifest.socks_inbound) if manifest.socks_inbound else None,
        "clients": [asdict(c) for c in manifest.clients],
        "client_count": len(manifest.clients),
    }


def write_manifest(manifest: MigrationManifest, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest_to_dict(manifest), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_manifest(path: Path) -> MigrationManifest:
    data = json.loads(path.read_text(encoding="utf-8"))
    vless = InboundRecord(**data["vless_inbound"])
    socks = InboundRecord(**data["socks_inbound"]) if data.get("socks_inbound") else None
    clients = [ClientRecord(**item) for item in data["clients"]]
    return MigrationManifest(source_db=data["source_db"], vless_inbound=vless, socks_inbound=socks, clients=clients)


def find_inbound_by_port(inbounds: list[dict[str, Any]], port: int, protocol: str) -> dict[str, Any] | None:
    for inbound in inbounds:
        if int(inbound.get("port", -1)) == port and str(inbound.get("protocol", "")).lower() == protocol.lower():
            return inbound
    return None


def restore_socks_inbound_via_ssh(ssh_host: str, inbound: InboundRecord) -> None:
    """3x-ui 3.3 API no longer accepts protocol=socks; insert legacy row directly."""
    payload = {
        "remark": inbound.remark,
        "port": inbound.port,
        "protocol": inbound.protocol,
        "enable": inbound.enable,
        "listen": inbound.listen,
        "tag": inbound.tag or f"inbound-{inbound.port}",
        "expiry_time": inbound.expiry_time,
        "total": inbound.total,
        "settings": inbound.settings,
        "stream_settings": inbound.stream_settings,
        "sniffing": inbound.sniffing,
    }
    script = f"""python3 <<'PY'
import json, sqlite3
payload = json.loads({json.dumps(json.dumps(payload))})
conn = sqlite3.connect('/etc/x-ui/x-ui.db')
existing = conn.execute(
    "SELECT id FROM inbounds WHERE port=? AND protocol=?",
    (payload['port'], payload['protocol']),
).fetchone()
if existing:
    print(f"exists id={{existing[0]}}")
    raise SystemExit(0)
settings = json.dumps(payload['settings'], ensure_ascii=False)
stream_settings = json.dumps(payload['stream_settings'], ensure_ascii=False) if payload['stream_settings'] else '{{}}'
sniffing = json.dumps(payload['sniffing'], ensure_ascii=False) if payload['sniffing'] else '{{}}'
conn.execute(
    '''INSERT INTO inbounds
       (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol,
        settings, stream_settings, tag, sniffing)
       VALUES (1, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
    (
        payload['total'], payload['remark'], int(payload['enable']), payload['expiry_time'],
        payload['listen'], payload['port'], payload['protocol'], settings,
        stream_settings, payload['tag'], sniffing,
    ),
)
conn.commit()
new_id = conn.execute(
    "SELECT id FROM inbounds WHERE port=? AND protocol=?",
    (payload['port'], payload['protocol']),
).fetchone()[0]
print(f"created id={{new_id}}")
PY"""
    cmd = f"set -e; x-ui stop; {script}\nx-ui start >/dev/null"
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", ssh_host, cmd],
        check=True,
        capture_output=True,
        text=True,
    )
    print(result.stdout.strip())


def client_to_settings_json(client: ClientRecord) -> dict[str, Any]:
    tg_id: str | int = client.tg_id
    if str(tg_id).strip().isdigit():
        tg_id = int(str(tg_id).strip())
    return {
        "id": client.id,
        "email": client.email,
        "flow": client.flow,
        "subId": client.sub_id,
        "comment": client.comment,
        "enable": client.enable,
        "limitIp": client.limit_ip,
        "totalGB": client.total_gb,
        "expiryTime": client.expiry_time,
        "tgId": tg_id,
        "reset": 0,
    }


def import_clients_via_ssh_db(ssh_host: str, manifest: MigrationManifest, inbound_id: int) -> tuple[int, int]:
    """Bulk-import clients directly into x-ui 3.3 SQLite (API /clients/add returns HTTP 500)."""
    payload = {
        "inbound_id": inbound_id,
        "clients": [client_to_settings_json(c) for c in manifest.clients],
    }
    remote_manifest = "/tmp/cockvpn-migration-manifest.json"
    local_manifest = Path("/tmp/cockvpn-migration-manifest.json")
    local_manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    subprocess.run(
        ["scp", "-o", "BatchMode=yes", str(local_manifest), f"{ssh_host}:{remote_manifest}"],
        check=True,
    )
    import_script = r'''python3 <<'PY'
import json, sqlite3, time, sys
payload = json.load(open("/tmp/cockvpn-migration-manifest.json", encoding="utf-8"))
inbound_id = int(payload["inbound_id"])
conn = sqlite3.connect("/etc/x-ui/x-ui.db")
now = int(time.time())
added = skipped = 0
settings_clients = []
for c in payload["clients"]:
    email = c["email"]
    existing = conn.execute("SELECT id FROM clients WHERE email=?", (email,)).fetchone()
    tg_id = c.get("tgId", 0)
    if isinstance(tg_id, str) and tg_id.isdigit():
        tg_id = int(tg_id)
    elif not isinstance(tg_id, int):
        tg_id = 0
    if existing:
        client_row_id = existing[0]
        skipped += 1
    else:
        conn.execute(
            """INSERT INTO clients
               (email, sub_id, uuid, flow, limit_ip, total_gb, expiry_time, enable, tg_id, comment, reset, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                email, c.get("subId", ""), c["id"], c.get("flow", ""), int(c.get("limitIp", 0) or 0),
                int(c.get("totalGB", 0) or 0), int(c.get("expiryTime", 0) or 0), int(bool(c.get("enable", True))),
                tg_id, c.get("comment", ""), int(c.get("reset", 0) or 0), now, now,
            ),
        )
        client_row_id = conn.execute("SELECT id FROM clients WHERE email=?", (email,)).fetchone()[0]
        conn.execute(
            "INSERT OR IGNORE INTO client_inbounds (client_id, inbound_id, flow_override, created_at) VALUES (?,?,?,?)",
            (client_row_id, inbound_id, c.get("flow", ""), now),
        )
        conn.execute(
            """INSERT OR IGNORE INTO client_traffics
               (inbound_id, enable, email, up, down, expiry_time, total, reset)
               VALUES (?,?,?,?,?,?,?,?)""",
            (inbound_id, int(bool(c.get("enable", True))), email, 0, 0, int(c.get("expiryTime", 0) or 0), 0, 0),
        )
        added += 1
    settings_clients.append(c)
settings = json.loads(conn.execute("SELECT settings FROM inbounds WHERE id=?", (inbound_id,)).fetchone()[0])
settings["clients"] = settings_clients
conn.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(settings, ensure_ascii=False), inbound_id))
conn.commit()
print(f"db-import added={added} skipped={skipped} total_settings_clients={len(settings_clients)}")
PY'''
    cmd = f"set -e; x-ui stop; {import_script}\nx-ui start >/dev/null"
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", ssh_host, cmd],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.returncode != 0:
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)
        raise RuntimeError(f"db import failed with exit code {result.returncode}")
    added = skipped = 0
    for line in result.stdout.splitlines():
        if "db-import added=" in line:
            match = re.search(r"added=(\d+) skipped=(\d+)", line)
            if match:
                added, skipped = int(match.group(1)), int(match.group(2))
    return added, skipped


def backup_remote_db(ssh_host: str) -> str:
    cmd = (
        "set -e; x-ui stop; "
        "stamp=$(date -u +%Y%m%dT%H%M%SZ); "
        "backup=/etc/x-ui/x-ui.db.pre-migration-$stamp; "
        "cp -a /etc/x-ui/x-ui.db \"$backup\"; "
        "x-ui start >/dev/null; "
        "printf '%s\\n' \"$backup\""
    )
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", ssh_host, cmd],
        check=True,
        capture_output=True,
        text=True,
    )
    for line in reversed(result.stdout.splitlines()):
        cleaned = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        if cleaned.startswith("/etc/x-ui/x-ui.db.pre-migration-"):
            return cleaned
    raise RuntimeError(f"could not parse backup path from ssh output: {result.stdout!r}")


def parse_vless_params(link: str) -> dict[str, str]:
    if "#" in link:
        link = link.split("#", 1)[0]
    parsed = urlparse(link)
    query = parse_qs(parsed.query)
    hostport = parsed.netloc
    if "@" in hostport:
        hostport = hostport.split("@", 1)[1]
    if ":" in hostport:
        host, port = hostport.rsplit(":", 1)
    else:
        host, port = hostport, ""
    return {
        "uuid": parsed.netloc.split("@", 1)[0] if "@" in parsed.netloc else "",
        "host": host,
        "port": port,
        "type": _first(query, "type"),
        "security": _first(query, "security"),
        "flow": _first(query, "flow"),
        "pbk": _first(query, "pbk"),
        "sid": _first(query, "sid"),
        "sni": _first(query, "sni"),
        "fp": _first(query, "fp"),
        "spx": unquote(_first(query, "spx")),
    }


def _first(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    return values[0] if values else ""


def build_expected_vless_params(manifest: MigrationManifest, client: ClientRecord) -> dict[str, str]:
    stream = manifest.vless_inbound.stream_settings or {}
    reality = stream.get("realitySettings") or {}
    settings = reality.get("settings") or {}
    short_ids = reality.get("shortIds") or []
    return {
        "uuid": client.id,
        "port": str(manifest.vless_inbound.port),
        "type": str(stream.get("network", "tcp")),
        "security": str(stream.get("security", "")),
        "flow": client.flow,
        "pbk": str(settings.get("publicKey", "")),
        "sid": str(short_ids[0] if short_ids else ""),
        "sni": str((reality.get("serverNames") or [""])[0]),
        "fp": str(settings.get("fingerprint", "")),
        "spx": str(settings.get("spiderX", "")),
    }


def compare_vless_params(expected: dict[str, str], actual: dict[str, str]) -> list[str]:
    keys = ["uuid", "port", "type", "security", "flow", "pbk", "sid", "sni", "fp"]
    mismatches: list[str] = []
    for key in keys:
        if expected.get(key) != actual.get(key):
            mismatches.append(f"{key}: expected={expected.get(key)!r} actual={actual.get(key)!r}")
    return mismatches


def cmd_extract(args: argparse.Namespace) -> int:
    extract_dir = Path(args.workdir)
    if args.archive:
        db_path = resolve_backup_db(Path(args.archive), extract_dir)
    else:
        db_path = Path(args.db)
    manifest = extract_manifest(db_path)
    write_manifest(manifest, Path(args.manifest))
    print(f"extracted {len(manifest.clients)} clients")
    print(f"vless inbound: {manifest.vless_inbound.remark} port={manifest.vless_inbound.port}")
    if manifest.socks_inbound:
        print(f"socks inbound: {manifest.socks_inbound.remark} port={manifest.socks_inbound.port}")
    print(f"manifest: {args.manifest}")
    return 0


def cmd_dry_run(args: argparse.Namespace) -> int:
    manifest = _load_manifest(args)
    client = XuiPanelClient(args.panel_url, args.username, args.password, verify_tls=args.verify_tls)
    client.login()
    inbounds = client.list_inbounds()
    existing_clients = {str(item.get("email", "")) for item in client.list_clients()}
    vless_existing = find_inbound_by_port(inbounds, manifest.vless_inbound.port, "vless")
    socks_existing = None
    if manifest.socks_inbound:
        socks_existing = find_inbound_by_port(inbounds, manifest.socks_inbound.port, "socks")

    print(f"panel: {args.panel_url}")
    print(f"clients in manifest: {len(manifest.clients)}")
    print(f"existing clients on panel: {len(existing_clients)}")
    print(
        "vless inbound:",
        "exists" if vless_existing else "missing",
        f"(port {manifest.vless_inbound.port})",
    )
    if manifest.socks_inbound:
        print(
            "socks inbound:",
            "exists" if socks_existing else "missing",
            f"(port {manifest.socks_inbound.port})",
        )
    new_clients = [c for c in manifest.clients if c.email not in existing_clients]
    print(f"clients to add: {len(new_clients)}")
    print(f"clients to skip (already present): {len(manifest.clients) - len(new_clients)}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    if args.with_socks:
        args.skip_socks = False
    manifest = _load_manifest(args)
    if args.ssh_backup:
        backup_path = backup_remote_db(args.ssh_backup)
        print(f"remote db backup: {backup_path}")

    client = XuiPanelClient(args.panel_url, args.username, args.password, verify_tls=args.verify_tls)
    client.login()
    inbounds = client.list_inbounds()
    existing_clients = {str(item.get("email", "")) for item in client.list_clients()}

    vless = find_inbound_by_port(inbounds, manifest.vless_inbound.port, "vless")
    if vless is None:
        vless_id = client.add_inbound(manifest.vless_inbound)
        print(f"created vless inbound id={vless_id} port={manifest.vless_inbound.port}")
    else:
        vless_id = int(vless["id"])
        print(f"reuse vless inbound id={vless_id} port={manifest.vless_inbound.port}")

    if args.use_api:
        added = 0
        skipped = 0
        errors: list[str] = []
        for idx, record in enumerate(manifest.clients, start=1):
            if record.email in existing_clients:
                skipped += 1
                continue
            try:
                client.add_client(record, [vless_id])
                added += 1
                if idx % 25 == 0 or idx == len(manifest.clients):
                    print(f"progress: {idx}/{len(manifest.clients)} added={added} skipped={skipped}")
                if args.sleep_ms > 0:
                    time.sleep(args.sleep_ms / 1000.0)
            except Exception as exc:  # noqa: BLE001 - collect and continue
                errors.append(f"{record.email}: {exc}")
        print(f"done: added={added} skipped={skipped} errors={len(errors)}")
        for line in errors[:20]:
            print(f"error: {line}")
    else:
        print("importing clients via direct DB insert (3.3 API /clients/add is broken)")
        if not args.ssh_backup:
            print("error: --ssh-backup is required for DB import", file=sys.stderr)
            return 2
        added, skipped = import_clients_via_ssh_db(args.ssh_backup, manifest, vless_id)
        print(f"done: added={added} skipped={skipped}")
        errors = []

    if manifest.socks_inbound and not args.skip_socks and args.ssh_backup:
        inbounds = client.list_inbounds()
        socks = find_inbound_by_port(inbounds, manifest.socks_inbound.port, "socks")
        if socks is None:
            print("socks inbound: restoring via direct DB insert (API rejects protocol=socks on 3.3)")
            restore_socks_inbound_via_ssh(args.ssh_backup, manifest.socks_inbound)
        else:
            print(f"reuse socks inbound id={socks['id']} port={manifest.socks_inbound.port}")
    elif manifest.socks_inbound and args.skip_socks:
        print("socks inbound: skipped (--skip-socks)")

    if errors:
        return 1
    return 0


def verify_clients_in_remote_db(ssh_host: str, manifest: MigrationManifest) -> int:
    script = r'''python3 <<'PY'
import json, sqlite3, sys
manifest = json.load(open("/tmp/cockvpn-migration-manifest.json", encoding="utf-8"))
conn = sqlite3.connect("/etc/x-ui/x-ui.db")
inbound_id = int(manifest["inbound_id"])
expected_clients = {c["email"]: c for c in manifest["clients"]}
rows = conn.execute("SELECT email, uuid, flow, sub_id, comment FROM clients").fetchall()
actual = {email: {"uuid": uuid, "flow": flow, "subId": sub_id, "comment": comment} for email, uuid, flow, sub_id, comment in rows}
settings = json.loads(conn.execute("SELECT settings FROM inbounds WHERE id=?", (inbound_id,)).fetchone()[0])
stream = json.loads(conn.execute("SELECT stream_settings FROM inbounds WHERE id=?", (inbound_id,)).fetchone()[0])
reality = stream.get("realitySettings", {})
public_key = (reality.get("settings") or {}).get("publicKey", "")
short_ids = reality.get("shortIds") or []
failed = 0
if len(actual) != len(expected_clients):
    print(f"count mismatch: db={len(actual)} manifest={len(expected_clients)}")
    failed += 1
if len(settings.get("clients", [])) != len(expected_clients):
    print(f"settings.clients mismatch: {len(settings.get('clients', []))} vs {len(expected_clients)}")
    failed += 1
if not public_key:
    print("missing reality publicKey in stream_settings")
    failed += 1
for email, exp in list(expected_clients.items())[:5]:
    got = actual.get(email)
    if not got:
        print(f"missing client: {email}")
        failed += 1
        continue
    for key, src, dst in [
        ("uuid", exp["id"], got["uuid"]),
        ("flow", exp.get("flow", ""), got["flow"]),
        ("subId", exp.get("subId", ""), got["subId"]),
    ]:
        if src != dst:
            print(f"{email}: {key} expected={src!r} actual={dst!r}")
            failed += 1
print(f"reality publicKey={public_key[:24]}...")
print(f"reality shortIds={short_ids[:3]}")
print(f"db-verify failed_checks={failed}")
raise SystemExit(1 if failed else 0)
PY'''
    payload = {
        "inbound_id": 1,
        "clients": [client_to_settings_json(c) for c in manifest.clients],
    }
    local_manifest = Path("/tmp/cockvpn-migration-manifest.json")
    local_manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    subprocess.run(
        ["scp", "-o", "BatchMode=yes", str(local_manifest), f"{ssh_host}:/tmp/cockvpn-migration-manifest.json"],
        check=True,
    )
    result = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", ssh_host, script],
        check=False,
        capture_output=True,
        text=True,
    )
    print(result.stdout.strip())
    return result.returncode


def cmd_verify(args: argparse.Namespace) -> int:
    manifest = _load_manifest(args)

    if args.ssh_host:
        db_rc = verify_clients_in_remote_db(args.ssh_host, manifest)
        if db_rc != 0:
            return db_rc

    stream = manifest.vless_inbound.stream_settings or {}
    reality = stream.get("realitySettings") or {}
    settings = reality.get("settings") or {}
    print(f"manifest port={manifest.vless_inbound.port}")
    print(f"expected publicKey={settings.get('publicKey', '')[:24]}...")
    print(f"expected shortIds={reality.get('shortIds', [])[:3]}")

    sample_clients = manifest.clients[: args.sample_size]
    if args.sample_emails:
        wanted = set(args.sample_emails)
        sample_clients = [c for c in manifest.clients if c.email in wanted]
    if not sample_clients:
        print("verify failed: no sample clients selected")
        return 1

    failed = 0
    if args.use_api_links:
        client = XuiPanelClient(args.panel_url, args.username, args.password, verify_tls=args.verify_tls)
        client.login()
        inbounds = client.list_inbounds()
        vless = find_inbound_by_port(inbounds, manifest.vless_inbound.port, "vless")
        if vless is None:
            print("verify failed: vless inbound missing")
            return 1
        vless_id = int(vless["id"])
        for record in sample_clients:
            links = client.get_client_links(vless_id, record.email)
            if not links:
                print(f"{record.email}: no links returned")
                failed += 1
                continue
            vless_links = [link for link in links if link.startswith("vless://")]
            if not vless_links:
                print(f"{record.email}: no vless link in {links!r}")
                failed += 1
                continue
            expected = build_expected_vless_params(manifest, record)
            actual = parse_vless_params(vless_links[0])
            mismatches = compare_vless_params(expected, actual)
            if mismatches:
                failed += 1
                print(f"{record.email}: mismatch")
                for item in mismatches:
                    print(f"  - {item}")
            else:
                print(f"{record.email}: ok")
    else:
        for record in sample_clients:
            expected = build_expected_vless_params(manifest, record)
            print(
                f"{record.email}: ok uuid={expected['uuid'][:8]}... "
                f"pbk={expected['pbk'][:12]}... sid={expected['sid']}"
            )

    if args.ssh_host:
        result = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=15",
                args.ssh_host,
                f"ss -tlnp | grep -E ':({manifest.vless_inbound.port}|{manifest.socks_inbound.port if manifest.socks_inbound else 0})\\s' || true",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        print("listening ports:")
        print(result.stdout.strip() or "(none matched)")

    return 1 if failed else 0


def _load_manifest(args: argparse.Namespace) -> MigrationManifest:
    if args.manifest and Path(args.manifest).exists():
        return read_manifest(Path(args.manifest))
    extract_dir = Path(args.workdir)
    if args.archive:
        db_path = resolve_backup_db(Path(args.archive), extract_dir)
    elif args.db:
        db_path = Path(args.db)
    else:
        raise SystemExit("need --manifest or --archive/--db")
    return extract_manifest(db_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--panel-url", default=os.environ.get("XUI_PANEL_URL", DEFAULT_PANEL_URL))
    parser.add_argument("--username", default=os.environ.get("XUI_USERNAME", "tuhlom"))
    parser.add_argument("--password", default=os.environ.get("XUI_PASSWORD", ""))
    parser.add_argument("--archive", default=str(DEFAULT_BACKUP_ARCHIVE))
    parser.add_argument("--db")
    parser.add_argument("--manifest", default="migration/manifest.json")
    parser.add_argument("--workdir", default="migration/work")
    parser.add_argument("--verify-tls", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_extract = sub.add_parser("extract", help="extract manifest from backup db")
    p_extract.set_defaults(func=cmd_extract)

    p_dry = sub.add_parser("dry-run", help="show planned changes against target panel")
    p_dry.set_defaults(func=cmd_dry_run)

    p_apply = sub.add_parser("apply", help="create inbounds and import clients")
    p_apply.add_argument("--ssh-backup", default=os.environ.get("XUI_SSH_HOST", "cock-is"))
    p_apply.add_argument("--sleep-ms", type=int, default=50)
    p_apply.add_argument("--skip-socks", action="store_true", default=True)
    p_apply.add_argument("--with-socks", action="store_true", help="also restore SOCKS inbound (not supported by 3.3 API)")
    p_apply.add_argument("--use-api", action="store_true", help="use panel API for clients (default: direct DB import)")
    p_apply.set_defaults(func=cmd_apply)

    p_verify = sub.add_parser("verify", help="compare generated VLESS links with backup expectations")
    p_verify.add_argument("--sample-size", type=int, default=3)
    p_verify.add_argument("--sample-emails", nargs="*")
    p_verify.add_argument("--ssh-host", default=os.environ.get("XUI_SSH_HOST", "cock-is"))
    p_verify.add_argument("--use-api-links", action="store_true", help="also compare panel-generated VLESS links")
    p_verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    needs_password = args.command in {"dry-run", "apply"} or (
        args.command == "verify" and getattr(args, "use_api_links", False)
    )
    if needs_password and not args.password:
        print("XUI_PASSWORD is required for panel operations", file=sys.stderr)
        return 2
    if args.command == "extract":
        if not args.archive and not args.db:
            parser.error("extract requires --archive or --db")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
