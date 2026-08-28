#!/usr/bin/env python3
"""Local SQLite-backed taskboard and sandboxed HTML artifact server."""

from __future__ import annotations

import argparse
import json
import math
import mimetypes
import os
import re
import signal
import sqlite3
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "taskboard.db"
DEFAULT_ARTIFACTS = ROOT / "artifacts"
STATIC_DIR = ROOT / "static"
STATUSES = ("backlog", "ready", "in_progress", "review", "done")
STATUS_LABELS = {
    "backlog": "Backlog",
    "ready": "Ready",
    "in_progress": "In progress",
    "review": "Review",
    "done": "Done",
}
PRIORITIES = ("low", "medium", "high", "urgent")
AUTHOR_TYPES = ("human", "agent")
MAX_BODY_BYTES = 1_048_576
MAX_ARTIFACT_BYTES = 10 * 1_048_576
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL CHECK(length(title) BETWEEN 1 AND 300),
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'backlog'
        CHECK(status IN ('backlog', 'ready', 'in_progress', 'review', 'done')),
    priority TEXT NOT NULL DEFAULT 'medium'
        CHECK(priority IN ('low', 'medium', 'high', 'urgent')),
    labels_json TEXT NOT NULL DEFAULT '[]',
    position REAL NOT NULL DEFAULT 1024,
    claimed_by TEXT NOT NULL DEFAULT '',
    claimed_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_board ON tasks(status, position, id);
CREATE INDEX IF NOT EXISTS idx_tasks_claim ON tasks(claimed_by);
CREATE TABLE IF NOT EXISTS task_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_activity ON task_activity(task_id, id DESC);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    author TEXT NOT NULL CHECK(length(author) BETWEEN 1 AND 100),
    author_type TEXT NOT NULL CHECK(author_type IN ('human', 'agent')),
    body TEXT NOT NULL CHECK(length(body) BETWEEN 1 AND 100000),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comments_task ON comments(task_id, id ASC);
"""


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def clean_string(value: Any, field: str, maximum: int, required: bool = False, strip: bool = True) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be a string")
    if strip:
        value = value.strip()
    if required and not value:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} is required")
    if len(value) > maximum:
        raise ApiError(HTTPStatus.BAD_REQUEST, f"{field} must be at most {maximum} characters")
    return value


def clean_labels(value: Any) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ApiError(HTTPStatus.BAD_REQUEST, "labels must be an array")
    labels: List[str] = []
    for item in value:
        label = clean_string(item, "label", 40)
        if label and label not in labels:
            labels.append(label)
    if len(labels) > 20:
        raise ApiError(HTTPStatus.BAD_REQUEST, "labels may contain at most 20 entries")
    return labels


def clean_actor(value: Any) -> str:
    return clean_string(value, "actor", 100) if value is not None else ""


class Taskboard:
    def __init__(self, db_path: Path, artifacts_dir: Path):
        self.db_path = db_path.expanduser().resolve()
        self.artifacts_dir = artifacts_dir.expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
            ).fetchone()
            if table is not None:
                columns = {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}
                if "claimed_by" not in columns:
                    raise RuntimeError(
                        "Unsupported database schema; start with a fresh taskboard database"
                    )
            connection.executescript(SCHEMA)
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"database foreign-key check failed: {violations}")

    def health(self) -> Dict[str, Any]:
        with self.connect() as connection:
            connection.execute("SELECT 1").fetchone()
            count = connection.execute("SELECT count(*) FROM tasks").fetchone()[0]
        return {"status": "ok", "database": "ok", "active_tasks": count, "time": utc_now()}

    def _artifact_path(self, raw_path: Any, require_exists: bool = False) -> Path:
        path_text = clean_string(raw_path, "artifact path", 500).replace("\\", "/")
        logical = PurePosixPath(path_text)
        if (
            not path_text
            or logical.is_absolute()
            or logical.suffix.lower() not in {".html", ".htm"}
            or any(part in {"", ".", ".."} or part.startswith(".") for part in logical.parts)
        ):
            raise ApiError(HTTPStatus.BAD_REQUEST, "artifact path must be a relative .html or .htm path")
        candidate = self.artifacts_dir.joinpath(*logical.parts)
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(self.artifacts_dir)
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "artifact path escapes the artifacts directory")
        current = self.artifacts_dir
        for part in logical.parts:
            current = current / part
            if current.is_symlink():
                raise ApiError(HTTPStatus.BAD_REQUEST, "symbolic links are not allowed for artifacts")
        if require_exists and (not candidate.is_file() or candidate.is_symlink()):
            raise ApiError(HTTPStatus.NOT_FOUND, "artifact not found")
        return candidate

    def list_artifacts(self) -> List[Dict[str, Any]]:
        artifacts: List[Dict[str, Any]] = []
        for candidate in self.artifacts_dir.rglob("*"):
            try:
                relative = candidate.relative_to(self.artifacts_dir)
                if (
                    not candidate.is_file()
                    or candidate.is_symlink()
                    or candidate.suffix.lower() not in {".html", ".htm"}
                    or any(part.startswith(".") for part in relative.parts)
                ):
                    continue
                stat = candidate.stat()
                path = relative.as_posix()
                artifacts.append(
                    {
                        "path": path,
                        "name": candidate.stem,
                        "size": stat.st_size,
                        "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc)
                        .isoformat(timespec="seconds")
                        .replace("+00:00", "Z"),
                        "url": "/artifacts/view/" + quote(path, safe="/"),
                    }
                )
            except OSError:
                continue
        return sorted(artifacts, key=lambda item: item["modified_at"], reverse=True)

    def artifact_file(self, raw_path: str) -> Path:
        path = self._artifact_path(unquote(raw_path), require_exists=True)
        if path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "artifact is larger than 10 MiB")
        return path

    def delete_artifact(self, raw_path: str) -> str:
        path = self._artifact_path(unquote(raw_path), require_exists=True)
        relative = path.relative_to(self.artifacts_dir).as_posix()
        try:
            path.unlink()
        except OSError as error:
            raise ApiError(HTTPStatus.INTERNAL_SERVER_ERROR, f"could not delete artifact: {error.strerror}")
        return relative

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> Dict[str, Any]:
        task = dict(row)
        try:
            task["labels"] = json.loads(task.pop("labels_json"))
        except (TypeError, json.JSONDecodeError):
            task["labels"] = []
        task["url"] = f"/tasks/{task['id']}"
        return task

    def list_tasks(self, query: Dict[str, List[str]]) -> List[Dict[str, Any]]:
        clauses = []
        parameters: List[Any] = []
        status = query.get("status", [""])[0]
        claim = query.get("claim", [""])[0]
        search = query.get("q", [""])[0].strip()
        if status:
            if status not in STATUSES:
                raise ApiError(HTTPStatus.BAD_REQUEST, "invalid status")
            clauses.append("t.status = ?")
            parameters.append(status)
        if claim == "unclaimed":
            clauses.append("t.claimed_by = ''")
        elif claim == "claimed":
            clauses.append("t.claimed_by <> ''")
        elif claim:
            clauses.append("t.claimed_by = ?")
            parameters.append(claim)
        if search:
            clauses.append("lower(t.title || ' ' || t.description || ' ' || t.labels_json || ' ' || t.claimed_by) LIKE ?")
            parameters.append(f"%{search.lower()}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT t.*, (SELECT count(*) FROM comments c WHERE c.task_id = t.id) AS comment_count "
                f"FROM tasks t {where} ORDER BY t.position ASC, t.id ASC",
                parameters,
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def get_task(self, task_id: int) -> Dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT t.*, (SELECT count(*) FROM comments c WHERE c.task_id = t.id) AS comment_count "
                "FROM tasks t WHERE t.id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "task not found")
        return self._row_to_task(row)

    @staticmethod
    def _record_activity(
        connection: sqlite3.Connection,
        task_id: int,
        action: str,
        actor: str,
        details: Dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO task_activity(task_id, action, actor, details_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, action, actor, json.dumps(details, separators=(",", ":")), utc_now()),
        )

    def _validated_values(self, body: Dict[str, Any], creating: bool) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        if creating or "title" in body:
            values["title"] = clean_string(body.get("title"), "title", 300, required=True)
        if creating or "description" in body:
            values["description"] = clean_string(body.get("description"), "description", 100_000, strip=False)
        if creating or "status" in body:
            status = body.get("status", "backlog")
            if status not in STATUSES:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"status must be one of {', '.join(STATUSES)}")
            values["status"] = status
        if creating or "priority" in body:
            priority = body.get("priority", "medium")
            if priority not in PRIORITIES:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"priority must be one of {', '.join(PRIORITIES)}")
            values["priority"] = priority
        if creating or "labels" in body:
            values["labels_json"] = json.dumps(clean_labels(body.get("labels")), separators=(",", ":"))
        if "position" in body:
            try:
                position = float(body["position"])
            except (TypeError, ValueError):
                raise ApiError(HTTPStatus.BAD_REQUEST, "position must be a number")
            if not math.isfinite(position) or abs(position) > 1e15:
                raise ApiError(HTTPStatus.BAD_REQUEST, "position is outside the supported range")
            values["position"] = position
        return values

    def create_task(self, body: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"title", "description", "status", "priority", "labels", "position", "actor"}
        unknown = set(body) - allowed
        if unknown:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"unknown fields: {', '.join(sorted(unknown))}")
        values = self._validated_values(body, creating=True)
        actor = clean_actor(body.get("actor"))
        now = utc_now()
        with self.connect() as connection:
            if "position" not in values:
                values["position"] = connection.execute(
                    "SELECT coalesce(max(position), 0) + 1024 FROM tasks WHERE status = ?",
                    (values["status"],),
                ).fetchone()[0]
            columns = list(values) + ["created_at", "updated_at"]
            parameters = [values[column] for column in values] + [now, now]
            cursor = connection.execute(
                f"INSERT INTO tasks({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                parameters,
            )
            task_id = cursor.lastrowid
            self._record_activity(connection, task_id, "created", actor, {"status": values["status"]})
        return self.get_task(task_id)

    def update_task(self, task_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"title", "description", "status", "priority", "labels", "position", "actor", "version"}
        unknown = set(body) - allowed
        if unknown:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"unknown fields: {', '.join(sorted(unknown))}")
        values = self._validated_values(body, creating=False)
        actor = clean_actor(body.get("actor"))
        expected_version = body.get("version")
        if expected_version is not None and (not isinstance(expected_version, int) or expected_version < 1):
            raise ApiError(HTTPStatus.BAD_REQUEST, "version must be a positive integer")
        if not values:
            raise ApiError(HTTPStatus.BAD_REQUEST, "no task fields were supplied")
        with self.connect() as connection:
            previous = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if previous is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "task not found")
            if expected_version is not None and previous["version"] != expected_version:
                raise ApiError(HTTPStatus.CONFLICT, "task changed since it was loaded; refresh and retry")
            assignments = [f"{column} = ?" for column in values]
            parameters = [values[column] for column in values]
            assignments.extend(["updated_at = ?", "version = version + 1"])
            parameters.extend([utc_now(), task_id])
            connection.execute(f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?", parameters)
            changed = {key: values[key] for key in values if previous[key] != values[key]}
            self._record_activity(connection, task_id, "updated", actor, changed)
        return self.get_task(task_id)

    def delete_task(self, task_id: int) -> None:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            if cursor.rowcount == 0:
                raise ApiError(HTTPStatus.NOT_FOUND, "task not found")

    def claim_task(self, task_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"agent", "actor"}
        unknown = set(body) - allowed
        if unknown:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"unknown fields: {', '.join(sorted(unknown))}")
        agent = clean_string(body.get("agent"), "agent", 100, required=True)
        actor = clean_actor(body.get("actor")) or agent
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute("SELECT claimed_by FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "task not found")
            if task["claimed_by"] and task["claimed_by"] != agent:
                raise ApiError(HTTPStatus.CONFLICT, f"task is already claimed by {task['claimed_by']}")
            if not task["claimed_by"]:
                now = utc_now()
                connection.execute(
                    "UPDATE tasks SET claimed_by = ?, claimed_at = ?, updated_at = ?, version = version + 1 WHERE id = ?",
                    (agent, now, now, task_id),
                )
                self._record_activity(connection, task_id, "claimed", actor, {"agent": agent})
        return self.get_task(task_id)

    def release_task(self, task_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"agent", "actor", "force"}
        unknown = set(body) - allowed
        if unknown:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"unknown fields: {', '.join(sorted(unknown))}")
        agent = clean_string(body.get("agent"), "agent", 100)
        actor = clean_actor(body.get("actor")) or agent
        force = body.get("force", False)
        if not isinstance(force, bool):
            raise ApiError(HTTPStatus.BAD_REQUEST, "force must be a boolean")
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = connection.execute("SELECT claimed_by FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "task not found")
            claimed_by = task["claimed_by"]
            if claimed_by and not force and agent != claimed_by:
                raise ApiError(HTTPStatus.CONFLICT, f"task is claimed by {claimed_by}; only that agent may release it")
            if claimed_by:
                now = utc_now()
                connection.execute(
                    "UPDATE tasks SET claimed_by = '', claimed_at = NULL, updated_at = ?, version = version + 1 WHERE id = ?",
                    (now, task_id),
                )
                self._record_activity(connection, task_id, "released", actor, {"agent": claimed_by})
        return self.get_task(task_id)

    def list_comments(self, task_id: int) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "task not found")
            rows = connection.execute(
                "SELECT id, task_id, author, author_type, body, created_at, updated_at "
                "FROM comments WHERE task_id = ? ORDER BY id ASC",
                (task_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_comment(self, task_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"author", "author_type", "body"}
        unknown = set(body) - allowed
        if unknown:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"unknown fields: {', '.join(sorted(unknown))}")
        author = clean_string(body.get("author"), "author", 100, required=True)
        author_type = body.get("author_type", "human")
        if author_type not in AUTHOR_TYPES:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"author_type must be one of {', '.join(AUTHOR_TYPES)}")
        comment_body = clean_string(body.get("body"), "body", 100_000, required=True, strip=False)
        now = utc_now()
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "task not found")
            cursor = connection.execute(
                "INSERT INTO comments(task_id, author, author_type, body, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (task_id, author, author_type, comment_body, now, now),
            )
            comment_id = cursor.lastrowid
            self._record_activity(
                connection,
                task_id,
                "commented",
                author,
                {"comment_id": comment_id, "author_type": author_type},
            )
            row = connection.execute(
                "SELECT id, task_id, author, author_type, body, created_at, updated_at FROM comments WHERE id = ?",
                (comment_id,),
            ).fetchone()
        return dict(row)

    def update_comment(self, comment_id: int, body: Dict[str, Any]) -> Dict[str, Any]:
        allowed = {"body", "editor", "editor_type"}
        unknown = set(body) - allowed
        if unknown:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"unknown fields: {', '.join(sorted(unknown))}")
        comment_body = clean_string(body.get("body"), "body", 100_000, required=True, strip=False)
        editor = clean_string(body.get("editor"), "editor", 100, required=True)
        editor_type = body.get("editor_type", "human")
        if editor_type not in AUTHOR_TYPES:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"editor_type must be one of {', '.join(AUTHOR_TYPES)}")
        now = utc_now()
        with self.connect() as connection:
            previous = connection.execute(
                "SELECT task_id FROM comments WHERE id = ?", (comment_id,)
            ).fetchone()
            if previous is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "comment not found")
            connection.execute(
                "UPDATE comments SET body = ?, updated_at = ? WHERE id = ?",
                (comment_body, now, comment_id),
            )
            self._record_activity(
                connection,
                previous["task_id"],
                "comment_edited",
                editor,
                {"comment_id": comment_id, "editor_type": editor_type},
            )
            row = connection.execute(
                "SELECT id, task_id, author, author_type, body, created_at, updated_at FROM comments WHERE id = ?",
                (comment_id,),
            ).fetchone()
        return dict(row)

    def delete_comment(self, comment_id: int, actor: str) -> int:
        with self.connect() as connection:
            previous = connection.execute(
                "SELECT task_id, author, author_type FROM comments WHERE id = ?", (comment_id,)
            ).fetchone()
            if previous is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "comment not found")
            connection.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
            self._record_activity(
                connection,
                previous["task_id"],
                "comment_deleted",
                actor,
                {
                    "comment_id": comment_id,
                    "author": previous["author"],
                    "author_type": previous["author_type"],
                },
            )
        return previous["task_id"]

    def activity(self, task_id: int) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is None:
                raise ApiError(HTTPStatus.NOT_FOUND, "task not found")
            rows = connection.execute(
                "SELECT id, action, actor, details_json, created_at FROM task_activity "
                "WHERE task_id = ? ORDER BY id DESC LIMIT 200",
                (task_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["details"] = json.loads(item.pop("details_json"))
            except json.JSONDecodeError:
                item["details"] = {}
            result.append(item)
        return result


class TaskboardServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple, app: Taskboard):
        self.app = app
        super().__init__(address, TaskboardHandler)


class TaskboardHandler(BaseHTTPRequestHandler):
    server: TaskboardServer
    server_version = "AgentTaskboard/2.0"

    def log_message(self, message: str, *args: Any) -> None:
        sys.stderr.write(f"{self.log_date_time_string()} {self.client_address[0]} {message % args}\n")

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self._security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_api_error(self, error: ApiError) -> None:
        self._send_json(error.status, {"error": error.message})

    def _read_json(self) -> Dict[str, Any]:
        if self.headers.get("X-Taskboard-Client") != "1":
            raise ApiError(HTTPStatus.FORBIDDEN, "X-Taskboard-Client: 1 is required")
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json")
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc.lower() != self.headers.get("Host", "").lower():
            raise ApiError(HTTPStatus.FORBIDDEN, "cross-origin writes are not allowed")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ApiError(HTTPStatus.BAD_REQUEST, "invalid Content-Length")
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request body must be between 1 byte and 1 MiB")
        try:
            body = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError(HTTPStatus.BAD_REQUEST, "request body is not valid JSON")
        if not isinstance(body, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "request body must be a JSON object")
        return body

    @staticmethod
    def _task_id(match: re.Match) -> int:
        return int(match.group(1))

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/health":
                self._send_json(HTTPStatus.OK, self.server.app.health())
                return
            if path == "/api/config":
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "statuses": [{"id": item, "label": STATUS_LABELS[item]} for item in STATUSES],
                        "priorities": PRIORITIES,
                        "author_types": AUTHOR_TYPES,
                    },
                )
                return
            if path == "/api/tasks":
                self._send_json(HTTPStatus.OK, {"tasks": self.server.app.list_tasks(parse_qs(parsed.query))})
                return
            match = re.fullmatch(r"/api/tasks/(\d+)", path)
            if match:
                self._send_json(HTTPStatus.OK, {"task": self.server.app.get_task(self._task_id(match))})
                return
            match = re.fullmatch(r"/api/tasks/(\d+)/comments", path)
            if match:
                self._send_json(HTTPStatus.OK, {"comments": self.server.app.list_comments(self._task_id(match))})
                return
            match = re.fullmatch(r"/api/tasks/(\d+)/activity", path)
            if match:
                self._send_json(HTTPStatus.OK, {"activity": self.server.app.activity(self._task_id(match))})
                return
            if path == "/api/artifacts":
                self._send_json(HTTPStatus.OK, {"artifacts": self.server.app.list_artifacts()})
                return
            if path.startswith("/artifacts/view/"):
                self._serve_artifact(path[len("/artifacts/view/"):])
                return
            if path in {"/", "/index.html", "/artifacts"} or re.fullmatch(r"/tasks/\d+", path):
                self._serve_static(STATIC_DIR / "index.html", "text/html; charset=utf-8")
                return
            if path == "/app.js":
                self._serve_static(STATIC_DIR / "app.js", "text/javascript; charset=utf-8")
                return
            if path == "/styles.css":
                self._serve_static(STATIC_DIR / "styles.css", "text/css; charset=utf-8")
                return
            if path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self._security_headers()
                self.end_headers()
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "not found")
        except ApiError as error:
            self._send_api_error(error)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as error:  # pragma: no cover
            self.log_error("Unhandled request failure: %r", error)
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal server error"})

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            body = self._read_json()
            if path == "/api/tasks":
                self._send_json(HTTPStatus.CREATED, {"task": self.server.app.create_task(body)})
                return
            match = re.fullmatch(r"/api/tasks/(\d+)/claim", path)
            if match:
                self._send_json(HTTPStatus.OK, {"task": self.server.app.claim_task(self._task_id(match), body)})
                return
            match = re.fullmatch(r"/api/tasks/(\d+)/release", path)
            if match:
                self._send_json(HTTPStatus.OK, {"task": self.server.app.release_task(self._task_id(match), body)})
                return
            match = re.fullmatch(r"/api/tasks/(\d+)/comments", path)
            if match:
                self._send_json(HTTPStatus.CREATED, {"comment": self.server.app.add_comment(self._task_id(match), body)})
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "not found")
        except ApiError as error:
            self._send_api_error(error)

    def do_PATCH(self) -> None:
        try:
            path = urlparse(self.path).path
            body = self._read_json()
            match = re.fullmatch(r"/api/comments/(\d+)", path)
            if match:
                comment = self.server.app.update_comment(int(match.group(1)), body)
                self._send_json(HTTPStatus.OK, {"comment": comment})
                return
            match = re.fullmatch(r"/api/tasks/(\d+)", path)
            if match:
                task = self.server.app.update_task(self._task_id(match), body)
                self._send_json(HTTPStatus.OK, {"task": task})
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "not found")
        except ApiError as error:
            self._send_api_error(error)

    def do_DELETE(self) -> None:
        try:
            path = urlparse(self.path).path
            body = self._read_json()
            unknown = set(body) - {"actor"}
            if unknown:
                raise ApiError(HTTPStatus.BAD_REQUEST, f"unknown fields: {', '.join(sorted(unknown))}")
            actor = clean_actor(body.get("actor"))
            match = re.fullmatch(r"/api/comments/(\d+)", path)
            if match:
                task_id = self.server.app.delete_comment(int(match.group(1)), actor)
                self._send_json(HTTPStatus.OK, {"deleted": True, "task_id": task_id})
                return
            match = re.fullmatch(r"/api/tasks/(\d+)", path)
            if match:
                self.server.app.delete_task(self._task_id(match))
                self._send_json(HTTPStatus.OK, {"deleted": True})
                return
            if path.startswith("/api/artifacts/"):
                artifact_path = self.server.app.delete_artifact(path[len("/api/artifacts/"):])
                self._send_json(HTTPStatus.OK, {"deleted": True, "path": artifact_path})
                return
            raise ApiError(HTTPStatus.NOT_FOUND, "not found")
        except ApiError as error:
            self._send_api_error(error)

    def _serve_static(self, path: Path, content_type: str) -> None:
        try:
            body = path.read_bytes()
        except OSError:
            raise ApiError(HTTPStatus.NOT_FOUND, "static asset not found")
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'",
        )
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _serve_artifact(self, raw_path: str) -> None:
        path = self.server.app.artifact_file(raw_path)
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self._security_headers()
        self.send_header(
            "Content-Security-Policy",
            "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src data: blob:; font-src data:; connect-src 'none'; form-action 'none'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'self'",
        )
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("TASKBOARD_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("TASKBOARD_PORT", "7778")))
    parser.add_argument("--db", type=Path, default=Path(os.environ.get("TASKBOARD_DB", DEFAULT_DB)))
    parser.add_argument("--artifacts", type=Path, default=Path(os.environ.get("TASKBOARD_ARTIFACTS", DEFAULT_ARTIFACTS)))
    parser.add_argument("--init-only", action="store_true", help="initialize the database and exit")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app = Taskboard(args.db, args.artifacts)
    if args.init_only:
        print(f"Initialized {app.db_path}")
        return 0
    server = TaskboardServer((args.host, args.port), app)

    def stop(_signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    print(f"Agent Taskboard listening on http://{args.host}:{args.port}", flush=True)
    print(f"Database: {app.db_path}", flush=True)
    print(f"Artifacts: {app.artifacts_dir}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
