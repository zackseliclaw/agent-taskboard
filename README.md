# Agent Taskboard

A dependency-free local Kanban board for coordinating work between humans and AI agents. Tasks, exclusive claims, comments, and activity history are stored in SQLite. Visual artifacts remain ordinary HTML files on disk and are discovered dynamically rather than stored in the database.

## Layout

- Application: `taskboard.py`
- Configuration: `taskboard.ini` — display name, server host/port, database path, and model overrides by ambiguity level. Edit this file and restart the service; see [Configuration](#configuration) for defaults and overrides.
- SQLite database: `data/taskboard.db`
- Artifact files: `artifacts/`
- Systemd unit template: `systemd/agent-taskboard.service`

The lanes are **Backlog**, **Ready**, **In progress**, **Review**, and **Done**. Every task has a stable URL such as `/tasks/17`. Task descriptions and comments support Markdown, including automatic links for bare HTTP(S) URLs. Web comments use the configured display name (default `User`). Comments can be edited inline from their ellipsis menu. Task cards show the current claim holder.

## Installation and setup

You need Python 3.7 or newer. There are no Python packages to install. Running the board as a background service also requires Linux with systemd and sudo access.

1. Clone or download this repository and open its directory.
2. Edit `taskboard.ini`: set your display name, listening address and port, and any model overrides you want. See [Configuration](#configuration) for details.
3. Start the board using one of the options below. The database and artifacts directory are created automatically on first startup.

To try it in your terminal:

```bash
python3 taskboard.py
```

Leave the terminal running; press Ctrl+C to stop. Open `http://localhost:7778` in your browser, substituting your configured port. If the board runs on another machine, use that machine's address and configure network access appropriately.

To install it as a background service that starts at boot:

```bash
sudo ./install-service.sh
systemctl status agent-taskboard
```

The installer derives the project directory from its own location and runs the service as the invoking user. You can explicitly set `TASKBOARD_USER` and `TASKBOARD_GROUP` when installing. Rerunning the installer updates the service unit and restarts the board without deleting its data.

After changing configuration, restart the service. To investigate startup problems, read its logs:

```bash
sudo systemctl restart agent-taskboard
journalctl -u agent-taskboard -f
```

### Agent installation and setup

If you're an agent setting this up, complete all of the following. `install-service.sh` installs only the board's systemd service; installing skills and scheduling the overseer are separate setup steps.

1. **Start and verify the board.** Check the Python version and whether the service already exists, preserve existing configuration and data, and follow the setup steps above using the user's requested settings. Use the service installer when a persistent systemd service is wanted; use the terminal command for a foreground run. Verify the configured URL responds at `/api/health`.
2. **Install the bundled skills into your own persistent skill library.** Install every skill directory in `~/agent-taskboard/skills` (or `skills/` under the actual checkout) using your agent runner's supported skill installation mechanism. This includes both [agent-taskboard](skills/agent-taskboard/SKILL.md), for operating the board, and [taskboard-overseer-cron](skills/taskboard-overseer-cron/SKILL.md), for scheduled oversight. Install the complete directories so supporting files are retained. Reading the skills in the current conversation is not sufficient: verify that future sessions and scheduled agent runs can discover and load both skills. Reuse or update existing registrations for this checkout rather than creating duplicates, and preserve unrelated skills. Record the absolute checkout path and effective board URL in persistent agent configuration. When loading an installed skill, use that checkout as the repository root; a copied skill's installation directory is not the repository, and symlinks must be resolved to their source before deriving the root.
3. **Create and enable an hourly overseer cron job.** Use your agent runner's persistent scheduler to create a job named `taskboard-overseer` for this checkout. Set its cron expression to `0 * * * *` (once every hour, at minute zero), or the scheduler's equivalent hourly recurrence. Inspect existing jobs first and update the matching job instead of creating a duplicate. The job must launch an agent run that loads both installed skills and follows the overseer workflow; an HTTP health check alone does not perform oversight. Give the run the absolute checkout path, effective board URL, and access to the worker state and delegation tools needed by the overseer. Use this prompt, replacing the placeholders with the actual values:

   ```text
   Perform the hourly Agent Taskboard overseer sweep.
   Repository root: <absolute checkout path>
   Load the installed agent-taskboard and taskboard-overseer-cron skills.
   Use the repository root above for configuration, artifacts, and implementation references.
   Follow taskboard-overseer-cron, using agent-taskboard for API requests,
   coordination rules, and model routing. Report actions or blockers as directed
   by the overseer skill
   ```

4. **Verify and report setup.** Read back the saved job and confirm it is enabled, runs hourly, contains the correct prompt and checkout context, and has a next run time. Report the browser URL, how the board is running, the installed skill locations, and the overseer job identifier, schedule, timezone, and next run. If your runner cannot persistently install skills or schedule agent runs, report that specific setup step as incomplete and explain what capability is missing.

## Configuration

Requires Python 3.7 or newer (config file uses the built-in `configparser` module). The committed `taskboard.ini` is the actual configuration file; edit it directly and restart the service. It is loaded from the project directory regardless of the launch directory. Missing files or settings use defaults; invalid settings and unknown keys stop startup with a clear error.

See [taskboard.ini](taskboard.ini) for the available settings. Write values without quotes. Use whole-line `#` or `;` comments; inline comment characters are treated as part of the value.

Each task has an ambiguity level: **Low**, **Medium**, **High**, or **Very high**. The `[models]` settings (`low`, `medium`, `high`, and `very_high`) map those levels to model identifiers understood by your agent runner. For example, setting `high = provider/model-name` tells an agent following the taskboard skill to use that model when delegating a High task. The board stores the level; it does not launch models itself.

A blank value (`low =`, for example) means **no model override**: the agent inherits the runner's default model without asking you to choose one. It does not mean no model is used or that work is skipped. If every entry is blank, all four levels inherit the runner's default. Changing the mapping affects future delegations after a service restart; it does not change task levels or models already running.

Relative database paths resolve against the project directory. Generated artifacts always use the project's `artifacts/` directory; this path is not configurable. Existing comment authors and task history are preserved when the display name changes.

Precedence is **CLI → environment → INI → defaults**. Overrides are `--host` / `TASKBOARD_HOST`, `--port` / `TASKBOARD_PORT`, `--db` / `TASKBOARD_DB`, and `--display-name` / `TASKBOARD_DISPLAY_NAME`. Environment settings for systemd belong in a service override, not merely your interactive shell.

Configuration is publicly readable by clients; do not put credentials in model identifiers or the committed INI.

New service installations read host and port from configuration. Existing installations with explicit `ExecStart` arguments retain those overrides until the unit is updated. If changing storage locations, ensure the service user can write them and update the unit's `ReadWriteDirectories` accordingly.

## Using the board

Create tasks with a title, Markdown description, ambiguity level, and optional labels. Move cards between Backlog, Ready, In progress, Review, and Done. Open a card to edit it, read comments, or copy its stable URL.

Claims show who is currently working on a task. The task page includes controls to claim work or release a stale claim. Deleting a task permanently removes its comments and activity too.

Agent integration instructions are in [the taskboard skill](skills/agent-taskboard/SKILL.md).

## Artifacts

Place self-contained HTML reports in `artifacts/`, including subdirectories, then refresh the Artifacts page. Open a report or copy its stable link into a task description or comment.

Only non-hidden `.html` and `.htm` regular files are listed. Symbolic links and path traversal are rejected. Artifacts are served with a restrictive CSP and browser sandbox: inline CSS, inline JavaScript, data images, and blob images work, while network calls, forms, plugins, top navigation, and taskboard API access are blocked.

## Security

The default binding is local-only. Setting `server.host = "0.0.0.0"` makes the board reachable from networks allowed by the host firewall. There is no login layer; do not put secrets in tasks, comments, or artifacts. Add host firewall rules or an authenticated reverse proxy before exposing this port beyond a trusted development network.

## Tests

```bash
cd /path/to/agent-taskboard
python3 -m unittest discover -s tests -v
```
