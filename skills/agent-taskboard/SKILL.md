---
name: agent-taskboard
description: Load this skill whenever an agent needs to create, claim, execute, comment on, review, move, complete, or delete Agent Taskboard work; create or reference HTML artifacts; coordinate with the taskmaster; or troubleshoot the local taskboard service and API.
---

## What This Skill Covers

This skill maps agents to the local Agent Taskboard sources of truth and enforces the taskmaster/worker coordination contract. The repository documentation and code are authoritative; do not rely on remembered endpoint shapes or field names.

## Authoritative Flow

1. Read `/local/home/zackseli/.meshclaw/workspace/agent-taskboard/README.md`:
   - Use **Coordination workflow** to determine the next action and which role owns it.
   - Use **Agent API** for current request bodies, concurrency behavior, and endpoint paths.
   - Use **Artifacts** for the current filesystem and URL workflow.
2. Determine your role:
   - The agent explicitly designated as **taskmaster** follows the taskmaster steps in the README and owns creation, decomposition, dispatch, priority, all status transitions, review, acceptance, and claim cleanup.
   - Every other execution agent is a **worker** unless Zack explicitly says otherwise. Workers claim under their own identity, perform work, and report through comments; they do not move statuses.
3. Read current state through the HTTP API before writing. For task updates, use the returned `version`; never guess it. Do not read or modify SQLite directly.
4. Perform exactly the role-appropriate action from README **Coordination workflow**. Use Markdown comments for starts, progress, blockers, review feedback, and completion evidence.
5. If producing visual output, create the HTML file according to README **Artifacts**, refresh/discover its stable URL, and include that URL in a task comment.
6. Read the API response and confirm the intended state. Treat HTTP 409 as a real claim/version conflict: refresh state and coordinate rather than overriding another agent.
7. Never delete a task, comment, or artifact unless Zack explicitly requested deletion. Completion means the taskmaster moves the task to **Done**.

## Sources of Truth

### Workflow and API Usage
- **Source:** `/local/home/zackseli/.meshclaw/workspace/agent-taskboard/README.md`
- **Look for:** `Coordination workflow`, `Agent API`, `Endpoints`, and `Artifacts`.
- **Use:** Follow the numbered lifecycle and copy current API shapes from this file rather than from this skill.

### API, Validation, and Database Schema
- **Source:** `/local/home/zackseli/.meshclaw/workspace/agent-taskboard/taskboard.py`
- **Look for:** `SCHEMA`, `Taskboard`, and `TaskboardHandler`.
- **Use:** Resolve ambiguity about accepted fields, validation, claim atomicity, comment activity, deletion behavior, HTTP status codes, and routes.

### Browser Behavior
- **Sources:**
  - `/local/home/zackseli/.meshclaw/workspace/agent-taskboard/static/index.html`
  - `/local/home/zackseli/.meshclaw/workspace/agent-taskboard/static/app.js`
- **Look for:** routed task URLs, Markdown rendering, web identity, claim controls, comments, and artifact actions.
- **Use:** Consult these only for UI behavior; use the backend for API authority.

### Runtime Configuration
- **Source:** `/local/home/zackseli/.meshclaw/workspace/agent-taskboard/systemd/agent-taskboard.service`
- **Look for:** `ExecStart`, writable directories, user, and restart policy.
- **Use:** Derive the current host/port and filesystem permissions. Check service health with systemd before diagnosing API failures.

### Tests
- **Source:** `/local/home/zackseli/.meshclaw/workspace/agent-taskboard/tests/test_taskboard.py`
- **Look for:** end-to-end examples of creation, claim conflicts, release, comments, editing/deletion, stable URLs, artifacts, and optimistic version checks.
- **Use:** Consult when request/response semantics remain unclear after reading the README and backend.

## Guardrails

- Do not impersonate a worker by claiming with another agent's identity.
- Do not bypass a conflicting claim; communicate through a comment or ask the taskmaster.
- Do not store artifact HTML in task descriptions, comments, or SQLite. Store the file in the configured artifact directory and reference its stable URL.
- Do not put secrets or credentials in tasks, comments, or artifacts.
- Do not change statuses as a worker. A `## Ready for review` comment is the worker-to-taskmaster handoff signal.
- Do not treat **Done** as deletion; retain completed work unless Zack explicitly requests removal.
