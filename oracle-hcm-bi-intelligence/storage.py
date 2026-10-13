from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path() -> Path:
    path = Path(os.getenv("APP_DB_PATH", "./data/bi_intelligence.sqlite3")).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def definitions_dir() -> Path:
    path = Path(os.getenv("APP_DEFINITIONS_DIR", "./data/definitions")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _fernet() -> Fernet:
    key = os.getenv("APP_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "APP_ENCRYPTION_KEY is not set. Generate a Fernet key and configure it "
            "outside source control before storing environment credentials."
        )
    try:
        return Fernet(key.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise RuntimeError("APP_ENCRYPTION_KEY is not a valid Fernet key.") from exc


def encrypt_secret(value: str) -> bytes:
    return _fernet().encrypt(value.encode("utf-8"))


def decrypt_secret(value: bytes | str | None) -> str:
    if not value:
        return ""
    raw = value.encode("utf-8") if isinstance(value, str) else value
    try:
        return _fernet().decrypt(raw).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError(
            "Could not decrypt a saved credential. Confirm APP_ENCRYPTION_KEY matches "
            "the key used when the credential was stored."
        ) from exc


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS environments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                wsdl_url TEXT NOT NULL,
                service_endpoint TEXT,
                wsdl_auth_mode TEXT NOT NULL DEFAULT 'none',
                wsdl_username TEXT,
                wsdl_password_enc BLOB,
                soap_username TEXT NOT NULL,
                soap_password_enc BLOB NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_test_at TEXT,
                last_test_status TEXT,
                last_test_message TEXT,
                UNIQUE(name)
            );

            CREATE TABLE IF NOT EXISTS catalog_objects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                environment_id INTEGER NOT NULL,
                object_path TEXT NOT NULL,
                object_name TEXT,
                object_type TEXT,
                description TEXT,
                parent_path TEXT,
                raw_metadata_json TEXT NOT NULL,
                definition_file TEXT,
                content_fingerprint TEXT,
                last_seen_at TEXT NOT NULL,
                FOREIGN KEY(environment_id) REFERENCES environments(id) ON DELETE CASCADE,
                UNIQUE(environment_id, object_path)
            );

            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                environment_id INTEGER NOT NULL,
                root_path TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                objects_seen INTEGER NOT NULL DEFAULT 0,
                folders_seen INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                FOREIGN KEY(environment_id) REFERENCES environments(id) ON DELETE CASCADE
            );
            """
        )


def save_environment(
    *,
    name: str,
    wsdl_url: str,
    service_endpoint: str | None,
    wsdl_auth_mode: str,
    wsdl_username: str | None,
    wsdl_password: str | None,
    soap_username: str,
    soap_password: str,
) -> int:
    now = utc_now()
    encrypted_wsdl_password = encrypt_secret(wsdl_password or "") if wsdl_auth_mode == "http_basic" else None
    encrypted_soap_password = encrypt_secret(soap_password)
    with connect() as conn:
        existing = conn.execute("SELECT id FROM environments WHERE name = ?", (name,)).fetchone()
        if existing:
            env_id = int(existing["id"])
            conn.execute(
                """
                UPDATE environments SET wsdl_url=?, service_endpoint=?, wsdl_auth_mode=?,
                    wsdl_username=?, wsdl_password_enc=?, soap_username=?, soap_password_enc=?,
                    updated_at=? WHERE id=?
                """,
                (
                    wsdl_url, service_endpoint or None, wsdl_auth_mode, wsdl_username or None,
                    encrypted_wsdl_password, soap_username, encrypted_soap_password, now, env_id
                ),
            )
            return env_id
        cur = conn.execute(
            """
            INSERT INTO environments
            (name, wsdl_url, service_endpoint, wsdl_auth_mode, wsdl_username,
             wsdl_password_enc, soap_username, soap_password_enc, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name, wsdl_url, service_endpoint or None, wsdl_auth_mode, wsdl_username or None,
                encrypted_wsdl_password, soap_username, encrypted_soap_password, now, now
            ),
        )
        return int(cur.lastrowid)


def list_environments() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, name, wsdl_url, service_endpoint, wsdl_auth_mode, wsdl_username,
                      soap_username, created_at, updated_at, last_test_at, last_test_status,
                      last_test_message
               FROM environments ORDER BY name"""
        ).fetchall()
        return [dict(row) for row in rows]


def get_environment(env_id: int, include_secrets: bool = False) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM environments WHERE id = ?", (env_id,)).fetchone()
    if not row:
        return None
    result = dict(row)
    if include_secrets:
        result["soap_password"] = decrypt_secret(result.pop("soap_password_enc"))
        result["wsdl_password"] = decrypt_secret(result.pop("wsdl_password_enc"))
    else:
        result.pop("soap_password_enc", None)
        result.pop("wsdl_password_enc", None)
    return result


def update_test_result(env_id: int, status: str, message: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE environments SET last_test_at=?, last_test_status=?, last_test_message=? WHERE id=?",
            (utc_now(), status, message[:1000], env_id),
        )


def start_scan(env_id: int, root_path: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO scans(environment_id, root_path, started_at, status) VALUES (?, ?, ?, 'running')",
            (env_id, root_path, utc_now()),
        )
        return int(cur.lastrowid)


def finish_scan(
    scan_id: int, status: str, objects_seen: int, folders_seen: int, error_message: str | None = None
) -> None:
    with connect() as conn:
        conn.execute(
            """UPDATE scans SET finished_at=?, status=?, objects_seen=?, folders_seen=?, error_message=?
               WHERE id=?""",
            (utc_now(), status, objects_seen, folders_seen, error_message, scan_id),
        )


def upsert_catalog_object(
    env_id: int,
    *,
    object_path: str,
    object_name: str,
    object_type: str,
    description: str,
    parent_path: str,
    raw_metadata: Any,
    definition_file: str | None = None,
    content_fingerprint: str | None = None,
) -> None:
    raw_json = json.dumps(raw_metadata, ensure_ascii=False, default=str)
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO catalog_objects
            (environment_id, object_path, object_name, object_type, description, parent_path,
             raw_metadata_json, definition_file, content_fingerprint, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(environment_id, object_path) DO UPDATE SET
              object_name=excluded.object_name, object_type=excluded.object_type,
              description=excluded.description, parent_path=excluded.parent_path,
              raw_metadata_json=excluded.raw_metadata_json,
              definition_file=COALESCE(excluded.definition_file, catalog_objects.definition_file),
              content_fingerprint=COALESCE(excluded.content_fingerprint, catalog_objects.content_fingerprint),
              last_seen_at=excluded.last_seen_at
            """,
            (
                env_id, object_path, object_name, object_type, description, parent_path,
                raw_json, definition_file, content_fingerprint, utc_now()
            ),
        )


def list_catalog_objects(env_id: int, limit: int = 1000) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT object_path, object_name, object_type, description, parent_path,
                      definition_file, content_fingerprint, last_seen_at
               FROM catalog_objects WHERE environment_id=?
               ORDER BY object_type, object_path LIMIT ?""",
            (env_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def list_scans(env_id: int, limit: int = 20) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, root_path, started_at, finished_at, status, objects_seen,
                      folders_seen, error_message FROM scans
               WHERE environment_id=? ORDER BY id DESC LIMIT ?""",
            (env_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
