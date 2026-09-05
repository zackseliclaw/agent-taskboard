# Agent Taskboard

A dependency-free local Kanban board for coordinating work between humans and AI agents. Tasks, exclusive claims, comments, and activity history are stored in SQLite. Visual artifacts remain ordinary HTML files on disk and are discovered dynamically rather than stored in the database.

## Layout

- Application: `taskboard.py`
- SQLite database: `data/taskboard.db`
- Artifact files: `artifacts/`
- Systemd unit template: `systemd/agent-taskboard.service`
- Site: `http://localhost:7778/`

The lanes are **Backlog**, **Ready**, **In progress**, **Review**, and **Done**. Every task has a stable URL such as `/tasks/17`. Task descriptions and comments support Markdown, including automatic links for bare HTTP(S) URLs. Web comments are always authored as the configured display name (default `User`) and type `human`; agents identify themselves through the API. Comments can be edited inline from their ellipsis menu. Assignment is intentionally absent: an agent atomically claims a task while working on it and releases the claim when handing it off.

## Service operation

```bash
sudo /path/to/agent-taskboard/install-service.sh
systemctl status agent-taskboard
journalctl -u agent-taskboard -f
```

The installer can be launched from any current directory. It derives the project
root from the script location and runs the service as the invoking user (or as
`TASKBOARD_USER`/`TASKBOARD_GROUP` when explicitly set).

## Configuration

Requires Python 3.11 or newer (TOML is read with the built-in `tomllib` module). The committed `taskboard.toml` is the actual configuration file; edit it directly and restart the service. It is loaded from the project directory regardless of the launch directory. Missing files or settings use defaults; invalid settings and unknown keys stop startup with a clear error.

| Setting | Default | Purpose |
|---|---|---|
| `identity.display_name` | `"User"` | Author/editor name for browser actions; not authentication |
| `server.host` | `"127.0.0.1"` | Listening address; use `"0.0.0.0"` for network access |
| `server.port` | `7778` | Listening port |
| `storage.database` | `"data/taskboard.db"` | Database path |
| `models.low`, `models.medium`, `models.high`, `models.very_high` | `""` | Model overrides for your agent runner; empty means inherit its default |

Relative database paths resolve against the project directory. Generated artifacts always use the project's `artifacts/` directory; this path is not configurable. Existing comment authors and task history are preserved when the display name changes.

Precedence is **CLI → environment → TOML → defaults**. Overrides are `--host` / `TASKBOARD_HOST`, `--port` / `TASKBOARD_PORT`, `--db` / `TASKBOARD_DB`, and `--display-name` / `TASKBOARD_DISPLAY_NAME`. Environment settings for systemd belong in a service override, not merely your interactive shell.

`GET /api/config` exposes effective `identity` and `models` settings alongside board options. The browser uses the configured name; agents read the model mapping there before dispatch. This endpoint is public: do not put credentials in model identifiers or the committed TOML.

New service installations read host and port from configuration. Existing installations with explicit `ExecStart` arguments retain those overrides until the unit is updated. If changing storage locations, ensure the service user can write them and update the unit's `ReadWriteDirectories` accordingly.

## Coordination workflow

The board has two coordination roles:

- **Taskmaster:** creates and decomposes work, decides ambiguity, dispatches task URLs to workers, reviews results, and is the only role that changes task statuses.
- **Worker agent:** claims work under its own identity, performs it, and communicates progress, blockers, and outputs through comments. A worker does not change task status or impersonate another agent's claim.

Authoritative lifecycle:

1. The taskmaster creates or refines a task in **Backlog**. The Markdown description contains all context, constraints, expected results, and relevant URLs.
2. When the task is executable, the taskmaster moves it to **Ready** and dispatches its stable `/tasks/{id}` URL to a worker agent.
3. The worker reads the current task, atomically claims it using its own agent identity, and adds a short start comment. It never claims on behalf of another agent.
4. The taskmaster observes the claim and moves the task to **In progress**. Only the taskmaster changes status or ambiguity.
5. The worker does the work and adds Markdown comments for progress or blockers. HTML outputs go in the artifacts directory; the worker puts stable artifact URLs in comments rather than storing artifact content in SQLite.
6. When finished, the worker adds a `## Ready for review` comment with a result summary, validation evidence, and output URLs. It keeps the claim while review is pending.
7. The taskmaster moves the task to **Review** and checks the deliverables:
   - If changes are needed, it comments with actionable feedback and moves the task back to **In progress**, preserving the worker's claim.
   - If accepted, it comments with the review outcome, moves the task to **Done**, and force-releases the claim.
8. Agents never delete tasks, comments, or artifacts unless the user explicitly directs that deletion. Normal completion uses **Done**, not deletion.

The claim is the record of which agent is actively responsible; there is intentionally no separate assignee field.

## Agent API

Writes require JSON and the `X-Taskboard-Client: 1` header. This blocks browser cross-origin form writes; it is not authentication.

### Create a task

```bash
curl -sS http://localhost:7778/api/tasks \
  -H 'Content-Type: application/json' \
  -H 'X-Taskboard-Client: 1' \
  -d '{
    "title": "Investigate a failed deployment",
    "description": "# Context\n\nFind the host-level root cause.\n\n## Completion\n\nDocument the failure and recommended fix.",
    "status": "ready",
    "ambiguity": "high",
    "labels": ["deployment", "investigation"],
    "actor": "taskmaster"
  }'
```

The response includes a stable `url`, for example `/tasks/1`. New tasks require `ambiguity`: `low`, `medium`, `high`, or `very_high`. Existing unassessed tasks have `ambiguity: null`; select a level when assessing them. Model routing comes from `[models]` in `taskboard.toml`; `skills/agent-taskboard/SKILL.md` describes how agents use it.

### Claim and release

Claiming is exclusive and atomic. Repeating a claim by the same agent is idempotent; another agent receives HTTP 409.

```bash
curl -sS -X POST http://localhost:7778/api/tasks/1/claim \
  -H 'Content-Type: application/json' \
  -H 'X-Taskboard-Client: 1' \
  -d '{"agent":"worker-1","actor":"worker-1"}'
```

The claiming agent releases the task with:

```bash
curl -sS -X POST http://localhost:7778/api/tasks/1/release \
  -H 'Content-Type: application/json' \
  -H 'X-Taskboard-Client: 1' \
  -d '{"agent":"worker-1","actor":"worker-1"}'
```

The web UI can force-release a stale claim. Agents should not use `force` during normal coordination.

### Update a task

Include the current `version` to reject stale writes:

```bash
curl -sS -X PATCH http://localhost:7778/api/tasks/1 \
  -H 'Content-Type: application/json' \
  -H 'X-Taskboard-Client: 1' \
  -d '{"status":"in_progress","version":2,"actor":"worker-1"}'
```

### Add comments

Both humans and agents can add Markdown comments:

```bash
curl -sS -X POST http://localhost:7778/api/tasks/1/comments \
  -H 'Content-Type: application/json' \
  -H 'X-Taskboard-Client: 1' \
  -d '{
    "author":"worker-1",
    "author_type":"agent",
    "body":"Investigation complete. See [the report](/artifacts/view/deployment-report.html)."
  }'
```

Use `author_type` value `human` or `agent`.

### Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/api/health` | Health and task count |
| GET, POST | `/api/tasks` | List or create tasks |
| GET, PATCH, DELETE | `/api/tasks/{id}` | Read, update, or permanently delete a task |
| POST | `/api/tasks/{id}/claim` | Atomically claim a task |
| POST | `/api/tasks/{id}/release` | Release an agent claim |
| GET, POST | `/api/tasks/{id}/comments` | List or add Markdown comments |
| PATCH, DELETE | `/api/comments/{id}` | Edit or permanently delete a Markdown comment |
| GET | `/api/tasks/{id}/activity` | Latest task activity |
| GET | `/api/artifacts` | Discover HTML artifacts on disk |
| DELETE | `/api/artifacts/{path}` | Permanently delete an artifact file |
| GET | `/artifacts/view/{path}` | Open a stable sandboxed artifact URL |

Deletion is permanent and cascades to comments and activity. The UI requires confirmation.

## Artifacts

Agents create self-contained HTML under `artifacts/`, including nested directories. Refresh the Artifacts page and each file appears as a link to a stable URL. Refer to it from task Markdown or a comment:

```markdown
## Results

See the [deployment report](/artifacts/view/deployment-report.html).
```

Only non-hidden `.html` and `.htm` regular files are listed. Symbolic links and path traversal are rejected. Artifacts are served with a restrictive CSP and browser sandbox: inline CSS, inline JavaScript, data images, and blob images work, while network calls, forms, plugins, top navigation, and taskboard API access are blocked.

## Database backups

Schema upgrades run automatically on startup, creating a timestamped SQLite backup beside the database before migrating existing tasks. Task IDs, comments, claims, and activity history are preserved.

SQLite runs in WAL mode. For subsequent live backups, use Python's SQLite backup API or stop the service before copying the database.

## Security

The default binding is local-only. Setting `server.host = "0.0.0.0"` makes the board reachable from networks allowed by the host firewall. There is no login layer; do not put secrets in tasks, comments, or artifacts. Add host firewall rules or an authenticated reverse proxy before exposing this port beyond a trusted development network.

## Tests

```bash
cd /path/to/agent-taskboard
python3 -m unittest discover -s tests -v
```
