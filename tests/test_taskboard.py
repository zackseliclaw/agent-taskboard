import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from taskboard import Taskboard, TaskboardServer


class TaskboardIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.artifacts = root / "artifacts"
        self.app = Taskboard(root / "taskboard.db", self.artifacts)
        self.server = TaskboardServer(("127.0.0.1", 0), self.app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, method="GET", body=None, client_header=True):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
            if client_header:
                headers["X-Taskboard-Client"] = "1"
        request = Request(self.base + path, data=data, headers=headers, method=method)
        with urlopen(request, timeout=2) as response:
            content_type = response.headers.get("Content-Type", "")
            payload = response.read()
            return response.status, response.headers, json.loads(payload) if "json" in content_type else payload

    def create_task(self, title="Investigate workflow"):
        _, _, payload = self.request(
            "/api/tasks",
            "POST",
            {
                "title": title,
                "description": "# Context\n\nTrace the **event queue**.",
                "labels": ["controller", "investigation"],
                "actor": "test",
            },
        )
        return payload["task"]

    def test_task_lifecycle_stable_url_and_removed_fields(self):
        task = self.create_task()
        self.assertEqual("/tasks/1", task["url"])
        self.assertEqual("", task["claimed_by"])
        self.assertNotIn("assignee", task)
        self.assertNotIn("acceptance_criteria", task)
        self.assertNotIn("source_url", task)
        self.assertNotIn("artifacts", task)

        status, _, page = self.request(task["url"])
        self.assertEqual(200, status)
        self.assertIn(b"Agent Taskboard", page)

        _, _, payload = self.request(
            f"/api/tasks/{task['id']}",
            "PATCH",
            {"status": "in_progress", "version": task["version"], "actor": "agent-1"},
        )
        self.assertEqual("in_progress", payload["task"]["status"])
        with self.assertRaises(HTTPError) as conflict:
            self.request(
                f"/api/tasks/{task['id']}",
                "PATCH",
                {"status": "done", "version": 1, "actor": "stale-agent"},
            )
        self.assertEqual(409, conflict.exception.code)

        with self.assertRaises(HTTPError) as removed:
            self.request("/api/tasks", "POST", {"title": "Old shape", "assignee": "agent"})
        self.assertEqual(400, removed.exception.code)

    def test_exclusive_claim_and_release(self):
        task = self.create_task()
        _, _, claimed = self.request(
            f"/api/tasks/{task['id']}/claim", "POST", {"agent": "agent-1", "actor": "agent-1"}
        )
        self.assertEqual("agent-1", claimed["task"]["claimed_by"])
        self.assertIsNotNone(claimed["task"]["claimed_at"])

        _, _, idempotent = self.request(
            f"/api/tasks/{task['id']}/claim", "POST", {"agent": "agent-1"}
        )
        self.assertEqual(claimed["task"]["version"], idempotent["task"]["version"])

        with self.assertRaises(HTTPError) as conflict:
            self.request(f"/api/tasks/{task['id']}/claim", "POST", {"agent": "agent-2"})
        self.assertEqual(409, conflict.exception.code)

        with self.assertRaises(HTTPError) as wrong_agent:
            self.request(f"/api/tasks/{task['id']}/release", "POST", {"agent": "agent-2"})
        self.assertEqual(409, wrong_agent.exception.code)

        _, _, released = self.request(
            f"/api/tasks/{task['id']}/release", "POST", {"agent": "agent-1"}
        )
        self.assertEqual("", released["task"]["claimed_by"])
        self.assertIsNone(released["task"]["claimed_at"])

    def test_human_and_agent_markdown_comments(self):
        task = self.create_task()
        for author, author_type, body in (
            ("Zack", "human", "Please check the **logs**."),
            ("elb-cp", "agent", "Done. See [report](/artifacts/view/report.html)."),
        ):
            status, _, payload = self.request(
                f"/api/tasks/{task['id']}/comments",
                "POST",
                {"author": author, "author_type": author_type, "body": body},
            )
            self.assertEqual(201, status)
            self.assertEqual(author_type, payload["comment"]["author_type"])

        _, _, payload = self.request(f"/api/tasks/{task['id']}/comments")
        self.assertEqual(["human", "agent"], [item["author_type"] for item in payload["comments"]])
        human_comment = payload["comments"][0]
        _, _, edited = self.request(
            f"/api/comments/{human_comment['id']}",
            "PATCH",
            {"body": "Updated with bare link https://example.com/report.", "editor": "Zack", "editor_type": "human"},
        )
        self.assertEqual("Updated with bare link https://example.com/report.", edited["comment"]["body"])
        _, _, deleted = self.request(
            f"/api/comments/{human_comment['id']}", "DELETE", {"actor": "Zack"}
        )
        self.assertTrue(deleted["deleted"])
        self.assertEqual(task["id"], deleted["task_id"])
        _, _, remaining = self.request(f"/api/tasks/{task['id']}/comments")
        self.assertEqual(["agent"], [item["author_type"] for item in remaining["comments"]])
        _, _, activity = self.request(f"/api/tasks/{task['id']}/activity")
        actions = [item["action"] for item in activity["activity"]]
        self.assertIn("comment_edited", actions)
        self.assertIn("comment_deleted", actions)
        _, _, refreshed = self.request(f"/api/tasks/{task['id']}")
        self.assertEqual(1, refreshed["task"]["comment_count"])

    def test_delete_is_permanent_and_cascades_comments(self):
        task = self.create_task()
        self.request(
            f"/api/tasks/{task['id']}/comments",
            "POST",
            {"author": "human", "author_type": "human", "body": "Delete with task."},
        )
        status, _, payload = self.request(
            f"/api/tasks/{task['id']}", "DELETE", {"actor": "web-ui"}
        )
        self.assertEqual(200, status)
        self.assertTrue(payload["deleted"])
        with self.assertRaises(HTTPError) as missing:
            self.request(f"/api/tasks/{task['id']}")
        self.assertEqual(404, missing.exception.code)

    def test_artifacts_have_stable_sandboxed_urls(self):
        self.artifacts.mkdir(parents=True, exist_ok=True)
        (self.artifacts / "report.html").write_text("<h1>Local report</h1>", encoding="utf-8")
        _, _, listing = self.request("/api/artifacts")
        artifact = listing["artifacts"][0]
        self.assertEqual("report.html", artifact["path"])
        self.assertEqual("/artifacts/view/report.html", artifact["url"])
        status, headers, body = self.request(artifact["url"])
        self.assertEqual(200, status)
        self.assertIn(b"Local report", body)
        self.assertIn("sandbox allow-scripts", headers["Content-Security-Policy"])
        self.assertIn("connect-src 'none'", headers["Content-Security-Policy"])
        encoded_path = artifact["path"].replace("/", "%2F")
        status, _, deleted = self.request(
            f"/api/artifacts/{encoded_path}", "DELETE", {"actor": "Zack"}
        )
        self.assertEqual(200, status)
        self.assertTrue(deleted["deleted"])
        self.assertFalse((self.artifacts / "report.html").exists())
        with self.assertRaises(HTTPError) as missing:
            self.request(artifact["url"])
        self.assertEqual(404, missing.exception.code)

    def test_write_requires_custom_header(self):
        with self.assertRaises(HTTPError) as forbidden:
            self.request("/api/tasks", "POST", {"title": "No header"}, client_header=False)
        self.assertEqual(403, forbidden.exception.code)

    def test_archived_tasks_are_filtered_and_archive_is_optimistic_update(self):
        task = self.create_task("Keep history")
        _, _, created_archived = self.request(
            "/api/tasks", "POST", {"title": "Hidden history", "archived": True}
        )
        archived = created_archived["task"]
        self.assertFalse(task["archived"])
        self.assertTrue(archived["archived"])
        _, _, listing = self.request("/api/tasks")
        self.assertEqual([task["id"]], [item["id"] for item in listing["tasks"]])
        _, _, included = self.request("/api/tasks?include_archived=true")
        self.assertEqual({task["id"], archived["id"]}, {item["id"] for item in included["tasks"]})
        _, _, updated = self.request(
            f"/api/tasks/{task['id']}", "PATCH",
            {"archived": True, "version": task["version"], "actor": "test"},
        )
        self.assertTrue(updated["task"]["archived"])
        self.assertEqual(task["version"] + 1, updated["task"]["version"])
        with self.assertRaises(HTTPError) as conflict:
            self.request(f"/api/tasks/{task['id']}", "PATCH", {"archived": False, "version": task["version"]})
        self.assertEqual(409, conflict.exception.code)
        _, _, activity = self.request(f"/api/tasks/{task['id']}/activity")
        self.assertEqual("updated", activity["activity"][0]["action"])
        self.assertEqual({"archived": 1}, activity["activity"][0]["details"])
        _, _, restored = self.request(
            f"/api/tasks/{task['id']}", "PATCH",
            {"archived": False, "version": updated["task"]["version"]},
        )
        self.assertFalse(restored["task"]["archived"])

    def test_archived_column_migrates_without_losing_existing_data(self):
        db_path = Path(self.temp.name) / "legacy.db"
        now = "2026-01-01T00:00:00Z"
        connection = sqlite3.connect(db_path)
        connection.executescript("""
            CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'backlog',
              priority TEXT NOT NULL DEFAULT 'medium', labels_json TEXT NOT NULL DEFAULT '[]',
              position REAL NOT NULL DEFAULT 1024, claimed_by TEXT NOT NULL DEFAULT '',
              claimed_at TEXT, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL);
            CREATE TABLE task_activity (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
              action TEXT NOT NULL, actor TEXT NOT NULL DEFAULT '', details_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
            CREATE TABLE comments (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL,
              author TEXT NOT NULL, author_type TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        """)
        connection.execute("INSERT INTO tasks(title, created_at, updated_at) VALUES (?, ?, ?)", ("Legacy", now, now))
        connection.commit(); connection.close()
        migrated = Taskboard(db_path, Path(self.temp.name) / "legacy-artifacts")
        task = migrated.get_task(1)
        self.assertFalse(task["archived"])
        self.assertEqual("Legacy", task["title"])
        self.assertIn("archived", {row[1] for row in migrated.connect().execute("PRAGMA table_info(tasks)")})

if __name__ == "__main__":
    unittest.main()
