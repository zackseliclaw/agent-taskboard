---
name: agent-taskboard
description: Load this skill whenever an agent needs to create, claim, execute, comment on, review, move, complete, or delete Agent Taskboard work; coordinate using the taskboard; or troubleshoot the local taskboard service and API.
---

## Connection and Configuration

Resolve the repository root two directories above this skill directory. Paths below are relative to that root. Use the service URL supplied with the task; otherwise inspect `taskboard.ini`, environment overrides, and the installed systemd unit for the effective host and port. Defaults are `http://localhost:7778`. For a local service bound to `0.0.0.0`, connect through `localhost`.

Settings are loaded once at startup. CLI arguments override environment variables, which override INI and built-in defaults. Supported overrides are `--host` / `TASKBOARD_HOST`, `--port` / `TASKBOARD_PORT`, `--db` / `TASKBOARD_DB`, and `--display-name` / `TASKBOARD_DISPLAY_NAME`. Artifact storage is fixed at `artifacts/`.

Read `GET /api/health` to check availability and `GET /api/config` for statuses, ambiguity levels, human identity, and model mappings. Use your own agent identity rather than the configured human display name.

## Coordination Workflow

Only one agent can claim a task at a time. The agent is responsible for task decomposition, dispatch, resolving ambiguity, and status transitions (except to done). The task owner can tackle the problem by creating new tasks if necessary, but only at lower levels of ambiguity than itself. In that case, the agent that spawned the task will act as the overseer for it. Agents with the claim should report progress as comments on their task as-needed, but can comment on other tasks. Comments should be concise, self-contained, and isolated

Overseer agents own independent review, acceptance, and stale-claim cleanup. The overseer can also handle state transitions. Overseers do not need to claim a task or create any tasks

1. Once executable, the overseer moves tasks to **Ready** and dispatches its stable `/tasks/{id}` URL using the model-routing rules below.
2. The worker reads the task, and claims it atomically under a name unique to its worker instance. Keep the identity consistent throughout the task. Never claim for another worker or share a bare identity between concurrent workers.
3. The overseer observes the claim and moves the task to **In progress**. Each task has one active worker; create separate tasks when coordinating multiple workers.
4. The worker performs the work and posts progress, blockers, and output links in Markdown comments.
5. On finishing, the worker posts a handoff with a result summary, validation evidence, and output URLs, then explicitly releases its claim. A release does not constitute acceptance.
6. The overseer moves the task to **Review** and independently checks every expected result against primary evidence. Unanswered central questions, failed required builds, and unapproved scope reductions block acceptance. If changes are needed, post actionable feedback, return the task to **In progress**, and have the worker reclaim it before resuming. If accepted, post the review outcome and move it to **Done**.
7. Before clearing a stale claim, verify whether the worker process still runs, whether it posted a handoff, and whether the deliverables meet acceptance criteria. An inactive worker or a handoff alone does not justify marking work Done. A coordinating or cron agent may clear a verified stale claim.

Comments should be self-contained and concise. Link to more detailed information if available

Read current API state before writes and confirm the returned result afterward. For task updates, send the returned `version`; never guess it. HTTP 409 means a claim or version conflict: refresh state and coordinate, without overriding another active worker. Do not read or modify SQLite directly.

Never delete tasks, comments, or artifacts unless the user explicitly requests deletion. Normal completion uses Done.

## Ambiguity and Model Assignment

Ambiguity is a model-routing field with values `low`, `medium`, `high`, and `very_high`. Set the level according to how vague the task is. Do not invent a separate definition of ambiguity based on task wording.

Read `GET /api/config` and look up `models[task.ambiguity]` before dispatch. The mapping is configured in `[models]` in `taskboard.ini`. If that entry is empty or missing, omit the model override and inherit the agent runner's default; do not ask for a model choice or pass an empty model identifier. Otherwise, pass the configured model identifier as the override. Record the selected model in the dispatch comment, or record "runner default" if the resolved model is unavailable. Existing tasks with `ambiguity: null` are unassessed; set a level before dispatch. New tasks require an explicit level. The overseer owns ambiguity changes and model assignment.

## API Requests

All writes require `Content-Type: application/json` and `X-Taskboard-Client: 1`. The custom header blocks browser cross-origin form writes; it is not authentication. Send JSON objects. Unknown body fields are rejected.

The following bodies illustrate request shapes; replace identities, IDs, and versions with current values.

| Action | Method and path | JSON body |
|---|---|---|
| Create task | POST `/api/tasks` | `{"title":"Investigate deployment","description":"Context and expected results","status":"backlog","ambiguity":"high","labels":["deployment"],"actor":"coordinator-unique"}` |
| Update task | PATCH `/api/tasks/{id}` | `{"status":"in_progress","version":2,"actor":"coordinator-unique"}` |
| Claim | POST `/api/tasks/{id}/claim` | `{"agent":"worker-unique","actor":"worker-unique"}` |
| Release own claim | POST `/api/tasks/{id}/release` | `{"agent":"worker-unique","actor":"worker-unique"}` |
| Clear verified stale claim | POST `/api/tasks/{id}/release` | `{"force":true,"actor":"coordinator-unique"}` |
| Add comment | POST `/api/tasks/{id}/comments` | `{"author":"worker-unique","author_type":"agent","body":"Markdown progress or handoff"}` |
| Edit comment | PATCH `/api/comments/{id}` | `{"body":"Updated Markdown","editor":"worker-unique","editor_type":"agent"}` |

New tasks require a title and ambiguity: `low`, `medium`, `high`, or `very_high`. Status values are `backlog`, `ready`, `in_progress`, `review`, and `done`. Task updates may also change title, description, labels, ambiguity, or numeric position. Responses wrap the result as `task` or `comment`; tasks include a stable `url`.

Claims are exclusive and atomic. Repeating a claim with the same identity is idempotent; a different claimant receives HTTP 409. Claims and releases increment the task version when they change state, so refetch before subsequent task updates.

| Method | Endpoint | Result |
|---|---|---|
| GET | `/api/health` | Service/database health and task count |
| GET | `/api/config` | Effective identity, model mapping, statuses, ambiguity levels, author types |
| GET | `/api/tasks` | `tasks` list; filters: `status`, `claim` (claimed, unclaimed, or identity), `q` |
| GET | `/api/tasks/{id}` | `task` details |
| GET | `/api/tasks/{id}/comments` | `comments` in chronological order |
| GET | `/api/tasks/{id}/activity` | Latest `activity` records |
| GET | `/api/artifacts` | Discovered `artifacts` with stable URLs |
| GET | `/artifacts/view/{path}` | Sandboxed HTML report |
| DELETE | `/api/tasks/{id}` | Permanently delete task and its comments/activity |
| DELETE | `/api/comments/{id}` | Permanently delete comment |
| DELETE | `/api/artifacts/{path}` | Permanently delete HTML file |

Authorized DELETE requests use `{"actor":"your-unique-identity"}` with the same write headers. URL-encode artifact paths when constructing requests.

## Artifacts

Create self-contained HTML in the project's `artifacts/` directory, optionally in subdirectories. Do not store HTML report content in task descriptions, comments, or SQLite. Discover the file through `GET /api/artifacts` and include its returned stable URL in a task comment, for example `[Report](/artifacts/view/deployment-report.html)`.

Only non-hidden regular `.html` and `.htm` files are listed. Symbolic links and path traversal are rejected. Reports larger than 10 MiB cannot be served. Sandboxed reports support inline CSS/JavaScript and data/blob images. Network calls, forms, plugins, top navigation, and taskboard API access are blocked. Do not put credentials or secrets in tasks, comments, or artifacts.

## Implementation References

Consult `taskboard.py` for validation, accepted fields, routes, and concurrency behavior; `configuration.py` and `taskboard.ini` for settings; and `tests/test_taskboard.py` for end-to-end API examples. Browser behavior lives in `static/index.html` and `static/app.js`.

For service diagnosis, inspect the installed `agent-taskboard` systemd unit and logs; `systemd/agent-taskboard.service` is only the installation template. Check service health before diagnosing API failures.
