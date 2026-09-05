---
name: agent-taskboard
description: Load this skill whenever an agent needs to create, claim, execute, comment on, review, move, complete, or delete Agent Taskboard work; coordinate using the taskboard; or troubleshoot the local taskboard service and API.
---

## Authoritative Flow

1. Resolve the repository root two directories above this skill directory. All source paths below are relative to that root. Read `README.md`:
   - Use **Coordination workflow** to determine the next action and which role owns it.
   - Use **Agent API** for current request bodies, concurrency behavior, and endpoint paths.
   - Use **Artifacts** for the current filesystem and URL workflow.
2. Determine your role:
   - The agent explicitly designated as **taskmaster** follows the taskmaster steps in the README and owns creation, decomposition, dispatch, ambiguity, all status transitions, independent review, acceptance, and claim cleanup.
   - Every other execution agent is a **worker** unless the user explicitly says otherwise. Workers claim under their own identity, perform work, and report through comments; they do not move statuses.
3. Before claiming a task or posting a comment, choose an agent name unique to your worker instance and use that same name for claims and comments throughout the task. Concurrent workers must not share a bare agent name.
4. Read current state through the HTTP API before writing. For task updates, use the returned `version`; never guess it. Do not read or modify SQLite directly.
5. Perform exactly the role-appropriate action from README **Coordination workflow**. Use Markdown comments for starts, progress, blockers, review feedback, and completion evidence.
6. When acting as taskmaster:
   - Review every expected result independently against primary evidence. An unanswered central question, a failed required build, or an unapproved scope reduction blocks acceptance even when the handoff is polished.
   - Before cleaning up a stale claim, independently verify whether the worker process is still running, whether it posted a `## Ready for review` handoff, and whether that handoff meets the task's acceptance criteria. A dead worker's handoff is not by itself sufficient to move the task to **Done**.
7. If producing visual output, create the HTML file according to README **Artifacts**, refresh/discover its stable URL, and include that URL in a task comment.
8. Read the API response and confirm the intended state. Treat HTTP 409 as a real claim/version conflict: refresh state and coordinate rather than overriding another agent.
9. Never delete a task, comment, or artifact unless the user explicitly requested deletion. Completion means the taskmaster moves the task to **Done**.

## Ambiguity and Model Assignment

Ambiguity is a model-routing field with values `low`, `medium`, `high`, and `very_high`. Set the level according to the level-to-model mapping and dispatch that model. Do not invent a separate definition of ambiguity based on task wording.

Read `GET /api/config` and look up `models[task.ambiguity]` before dispatch. The mapping is configured in `[models]` in `taskboard.toml`. If that entry is empty or missing, omit the model override and inherit the agent runner's default; do not ask for a model choice or pass an empty model identifier. Otherwise, pass the configured model identifier as the override. Record the selected model in the dispatch comment, or record "runner default" if the resolved model is unavailable. Existing tasks with `ambiguity: null` are unassessed; set a level before dispatch. New tasks require an explicit level. The taskmaster owns ambiguity changes and model assignment.

## Sources of Truth

### Workflow and API Usage
- **Source:** `README.md`
- **Look for:** `Coordination workflow`, `Agent API`, `Endpoints`, and `Artifacts`.
- **Use:** Follow the numbered lifecycle and copy current API shapes from this file rather than from this skill.

### API, Validation, and Database Schema
- **Source:** `taskboard.py`
- **Look for:** `SCHEMA`, `Taskboard`, and `TaskboardHandler`.
- **Use:** Resolve ambiguity about accepted fields, validation, claim atomicity, comment activity, deletion behavior, HTTP status codes, and routes.

### Browser Behavior
- **Sources:**
  - `static/index.html`
  - `static/app.js`
- **Look for:** routed task URLs, Markdown rendering, web identity, claim controls, comments, and artifact actions.
- **Use:** Consult these only for UI behavior; use the backend for API authority.

### Runtime Configuration
- **Source:** `systemd/agent-taskboard.service`
- **Look for:** `ExecStart`, writable directories, user, and restart policy.
- **Use:** Derive the current host/port and filesystem permissions. Check service health with systemd before diagnosing API failures.

### Tests
- **Source:** `tests/test_taskboard.py`
- **Look for:** end-to-end examples of creation, claim conflicts, release, comments, editing/deletion, stable URLs, artifacts, and optimistic version checks.
- **Use:** Consult when request/response semantics remain unclear after reading the README and backend.

## Guardrails

- Do not impersonate a worker by claiming with another agent's identity.
- Do not bypass a conflicting claim; communicate through a comment or ask the taskmaster.
- Do not store artifact HTML in task descriptions, comments, or SQLite. Store the file in the configured artifact directory and reference its stable URL.
- Do not put secrets or credentials in tasks, comments, or artifacts.
- Do not change statuses as a worker. A `## Ready for review` comment is the worker-to-taskmaster handoff signal.
- Do not treat **Done** as deletion; retain completed work unless the user explicitly requests removal.
