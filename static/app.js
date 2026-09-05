(() => {
  "use strict";

  const state = { displayName: "User", tasks: [], artifacts: [], statuses: [], draggedId: null, currentTask: null, comments: [], editingCommentId: null };
  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];
  const hasDocument = typeof document !== "undefined";
  const board = hasDocument ? $("#board") : null;
  const dialog = hasDocument ? $("#task-dialog") : null;

  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (options.body) {
      headers["Content-Type"] = "application/json";
      headers["X-Taskboard-Client"] = "1";
    }
    const response = await fetch(path, { ...options, headers });
    let payload = {};
    try { payload = await response.json(); } catch (_) { /* response may be empty */ }
    if (!response.ok) throw new Error(payload.error || `${response.status} ${response.statusText}`);
    return payload;
  }

  function toast(message, error = false) {
    const item = document.createElement("div");
    item.className = `toast${error ? " error" : ""}`;
    item.textContent = message;
    $("#toast-region").append(item);
    setTimeout(() => item.remove(), 4000);
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[character]));
  }

  function renderInlineMarkdown(source) {
    const code = [];
    const links = [];
    const linkToken = (label, url) => {
      const normalized = url.toLowerCase();
      let href = url;
      if (normalized.startsWith("www.")) href = `https://${url}`;
      else if (!(normalized.startsWith("http://") || normalized.startsWith("https://") || normalized.startsWith("mailto:") || normalized.startsWith("/") || normalized.startsWith("#"))) return null;
      const token = `\u0000LINK${links.length}\u0000`;
      links.push(`<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`);
      return token;
    };
    let value = escapeHtml(source).replace(/`([^`\n]+)`/g, (_, content) => {
      const token = `\u0000CODE${code.length}\u0000`;
      code.push(`<code>${content}</code>`);
      return token;
    });
    value = value.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (match, label, url) => linkToken(label, url) || match);
    value = value.replace(/(^|[\s(])((?:https?:\/\/|www\.)[^\s<]+)/gi, (match, prefix, rawUrl) => {
      let url = rawUrl;
      let trailing = "";
      while (/[.,!?;:]$/.test(url)) { trailing = url.slice(-1) + trailing; url = url.slice(0, -1); }
      if (url.endsWith(")") && !url.includes("(")) { trailing = `)${trailing}`; url = url.slice(0, -1); }
      return `${prefix}${linkToken(url, url) || url}${trailing}`;
    });
    value = value.replace(/(^|[\s(])((?:\/tasks\/\d+|\/artifacts\/view\/[^\s<]+))/g, (match, prefix, rawUrl) => {
      let url = rawUrl;
      let trailing = "";
      while (/[.,!?;:]$/.test(url)) { trailing = url.slice(-1) + trailing; url = url.slice(0, -1); }
      return `${prefix}${linkToken(url, url) || url}${trailing}`;
    });
    value = value.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    value = value.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    value = value.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    links.forEach((content, index) => { value = value.replace(`\u0000LINK${index}\u0000`, content); });
    code.forEach((content, index) => { value = value.replace(`\u0000CODE${index}\u0000`, content); });
    return value;
  }

  function splitTableRow(source) {
    let line = source.trim();
    if (line.startsWith("|")) line = line.slice(1);
    const cells = [];
    let cell = "";
    let codeFenceLength = 0;
    for (let index = 0; index < line.length; index += 1) {
      const character = line[index];
      if (character === "\\" && line[index + 1] === "|") {
        cell += "|";
        index += 1;
        continue;
      }
      if (character === "`") {
        let end = index + 1;
        while (line[end] === "`") end += 1;
        const runLength = end - index;
        if (!codeFenceLength) codeFenceLength = runLength;
        else if (codeFenceLength === runLength) codeFenceLength = 0;
        cell += line.slice(index, end);
        index = end - 1;
        continue;
      }
      if (character === "|" && !codeFenceLength) {
        cells.push(cell.trim());
        cell = "";
        continue;
      }
      cell += character;
    }
    cells.push(cell.trim());
    if (cells.length > 1 && cells[cells.length - 1] === "") cells.pop();
    return cells;
  }

  function isTableRow(source) {
    const trimmed = source.trim();
    if (!trimmed) return false;
    const cells = splitTableRow(source);
    return cells.length > 1 || trimmed.startsWith("|") || (trimmed.endsWith("|") && !trimmed.endsWith("\\|"));
  }

  function parseTableAlignments(source, columnCount) {
    if (!isTableRow(source)) return null;
    const cells = splitTableRow(source);
    if (cells.length !== columnCount) return null;
    const alignments = [];
    for (const cell of cells) {
      const marker = cell.trim();
      if (!/^:?-{3,}:?$/.test(marker)) return null;
      const left = marker.startsWith(":");
      const right = marker.endsWith(":");
      alignments.push(left && right ? "center" : right ? "right" : left ? "left" : "");
    }
    return alignments;
  }

  function renderTableRow(tag, cells, alignments) {
    return `<tr>${alignments.map((alignment, index) => {
      const scope = tag === "th" ? ' scope="col"' : "";
      const className = alignment ? ` class="align-${alignment}"` : "";
      return `<${tag}${scope}${className}>${renderInlineMarkdown(cells[index] || "")}</${tag}>`;
    }).join("")}</tr>`;
  }

  function renderMarkdown(source) {
    if (!source || !source.trim()) return '<p class="empty-copy">No description provided.</p>';
    const lines = source.replace(/\r\n?/g, "\n").split("\n");
    const output = [];
    let inCode = false;
    let codeLines = [];
    let listType = null;
    const closeList = () => {
      if (listType) output.push(`</${listType}>`);
      listType = null;
    };
    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      if (/^```/.test(line)) {
        if (inCode) {
          output.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
          codeLines = [];
          inCode = false;
        } else {
          closeList();
          inCode = true;
        }
        continue;
      }
      if (inCode) { codeLines.push(line); continue; }
      if (!line.trim()) { closeList(); continue; }
      const heading = line.match(/^(#{1,6})\s+(.+)$/);
      if (heading) {
        closeList();
        const level = heading[1].length;
        output.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
        continue;
      }
      const tableHeaders = splitTableRow(line);
      const tableAlignments = isTableRow(line) && index + 1 < lines.length
        ? parseTableAlignments(lines[index + 1], tableHeaders.length)
        : null;
      if (tableAlignments) {
        closeList();
        const bodyRows = [];
        index += 2;
        while (index < lines.length && isTableRow(lines[index])) {
          bodyRows.push(renderTableRow("td", splitTableRow(lines[index]), tableAlignments));
          index += 1;
        }
        index -= 1;
        const body = bodyRows.length ? `<tbody>${bodyRows.join("")}</tbody>` : "";
        output.push(`<div class="markdown-table-wrapper"><table><thead>${renderTableRow("th", tableHeaders, tableAlignments)}</thead>${body}</table></div>`);
        continue;
      }
      const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      if (unordered || ordered) {
        const nextType = unordered ? "ul" : "ol";
        if (listType !== nextType) { closeList(); output.push(`<${nextType}>`); listType = nextType; }
        output.push(`<li>${renderInlineMarkdown((unordered || ordered)[1])}</li>`);
        continue;
      }
      closeList();
      const quote = line.match(/^>\s?(.*)$/);
      if (quote) output.push(`<blockquote>${renderInlineMarkdown(quote[1])}</blockquote>`);
      else if (/^\s*(---+|___+)\s*$/.test(line)) output.push("<hr>");
      else output.push(`<p>${renderInlineMarkdown(line)}</p>`);
    }
    closeList();
    if (inCode) output.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
    return output.join("\n");
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { renderMarkdown };
    return;
  }

  function initials(value) {
    if (!value) return "–";
    return value.split(/[\s._-]+/).filter(Boolean).slice(0, 2).map((part) => part[0].toUpperCase()).join("");
  }

  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  }

  function formatTime(value) {
    return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
  }

  function navigate(path, replace = false) {
    if (replace) history.replaceState({}, "", path);
    else history.pushState({}, "", path);
    route();
  }

  function filteredTasks() {
    const search = $("#search").value.trim().toLowerCase();
    const claim = $("#claim-filter").value;
    return state.tasks.filter((task) => {
      if (claim === "unclaimed" && task.claimed_by) return false;
      if (claim === "claimed" && !task.claimed_by) return false;
      if (!search) return true;
      return [task.title, task.description, task.claimed_by, ...task.labels].join(" ").toLowerCase().includes(search);
    });
  }

  function buildCard(task) {
    const card = document.createElement("a");
    card.className = `task-card ambiguity-${task.ambiguity}`;
    card.href = task.url;
    card.draggable = true;
    card.dataset.id = task.id;
    const top = document.createElement("div"); top.className = "card-top";
    const id = document.createElement("span"); id.className = "task-id"; id.textContent = `TASK-${task.id}`;
    const ambiguity = document.createElement("span"); ambiguity.className = `ambiguity ${task.ambiguity}`; ambiguity.textContent = task.ambiguity ? task.ambiguity.replaceAll("_", " ") : "Not assessed";
    top.append(id, ambiguity);
    const title = document.createElement("h3"); title.textContent = task.title;
    card.append(top, title);
    if (task.labels.length) {
      const labels = document.createElement("div"); labels.className = "labels";
      task.labels.slice(0, 5).forEach((value) => {
        const label = document.createElement("span"); label.className = "label"; label.textContent = value; labels.append(label);
      });
      card.append(labels);
    }
    const footer = document.createElement("div"); footer.className = "card-footer";
    const avatar = document.createElement("span"); avatar.className = `avatar${task.claimed_by ? " claimed" : ""}`; avatar.textContent = initials(task.claimed_by);
    const claim = document.createElement("span"); claim.className = "claim-label"; claim.textContent = task.claimed_by ? `Claimed by ${task.claimed_by}` : "Unclaimed";
    footer.append(avatar, claim);
    if (task.comment_count) {
      const comments = document.createElement("span"); comments.className = "comment-indicator"; comments.textContent = `◌ ${task.comment_count}`; footer.append(comments);
    }
    card.append(footer);
    card.addEventListener("click", (event) => {
      event.preventDefault();
      if (card.dataset.wasDragged === "true") return;
      navigate(task.url);
    });
    card.addEventListener("dragstart", () => {
      state.draggedId = task.id;
      card.dataset.wasDragged = "true";
      card.classList.add("dragging");
    });
    card.addEventListener("dragend", () => {
      state.draggedId = null;
      card.classList.remove("dragging");
      $$(".lane").forEach((lane) => lane.classList.remove("drag-over"));
      setTimeout(() => { card.dataset.wasDragged = "false"; }, 0);
    });
    return card;
  }

  function renderBoard() {
    const tasks = filteredTasks();
    board.replaceChildren();
    state.statuses.forEach((status) => {
      const lane = document.createElement("section"); lane.className = "lane"; lane.dataset.status = status.id;
      const laneTasks = tasks.filter((task) => task.status === status.id).sort((a, b) => a.position - b.position || a.id - b.id);
      const header = document.createElement("header"); header.className = "lane-header";
      const dot = document.createElement("span"); dot.className = "lane-dot";
      const title = document.createElement("span"); title.className = "lane-title"; title.textContent = status.label;
      const count = document.createElement("span"); count.className = "lane-count"; count.textContent = laneTasks.length;
      header.append(dot, title, count);
      const cards = document.createElement("div"); cards.className = "lane-cards";
      if (laneTasks.length) laneTasks.forEach((task) => cards.append(buildCard(task)));
      else { const empty = document.createElement("div"); empty.className = "empty-lane"; empty.textContent = "Drop a task here"; cards.append(empty); }
      lane.append(header, cards);
      lane.addEventListener("dragover", (event) => { event.preventDefault(); lane.classList.add("drag-over"); });
      lane.addEventListener("dragleave", (event) => { if (!lane.contains(event.relatedTarget)) lane.classList.remove("drag-over"); });
      lane.addEventListener("drop", async (event) => {
        event.preventDefault(); lane.classList.remove("drag-over");
        if (state.draggedId) await moveTask(state.draggedId, status.id);
      });
      board.append(lane);
    });
    $("#board-summary").textContent = `${tasks.length} visible · ${state.tasks.length} tasks`;
  }

  async function moveTask(taskId, status) {
    const task = state.tasks.find((item) => item.id === taskId);
    if (!task) return;
    const targetTasks = state.tasks.filter((item) => item.status === status && item.id !== taskId);
    const position = targetTasks.length ? Math.max(...targetTasks.map((item) => item.position)) + 1024 : 1024;
    const previous = { status: task.status, position: task.position };
    Object.assign(task, { status, position });
    renderBoard();
    try {
      const payload = await api(`/api/tasks/${task.id}`, { method: "PATCH", body: JSON.stringify({ status, position, version: task.version, actor: state.displayName }) });
      Object.assign(task, payload.task);
    } catch (error) {
      Object.assign(task, previous);
      toast(error.message, true);
      await loadTasks();
    }
  }

  function openEditor(task = null) {
    $("#task-form").reset();
    $("#task-ambiguity").value = "";
    $("#task-status").value = "backlog";
    $("#task-id").value = task?.id || "";
    $("#task-version").value = task?.version || "";
    $("#dialog-eyebrow").textContent = task ? `TASK-${task.id}` : "New work item";
    $("#dialog-title").textContent = task ? "Edit task" : "Create task";
    if (task) {
      $("#task-title").value = task.title;
      $("#task-status").value = task.status;
      $("#task-ambiguity").value = task.ambiguity || "";
      $("#task-labels").value = task.labels.join(", ");
      $("#task-description").value = task.description;
    }
    dialog.showModal();
    $("#task-title").focus();
  }

  async function saveTask(event) {
    event.preventDefault();
    const id = $("#task-id").value;
    const body = {
      title: $("#task-title").value,
      status: $("#task-status").value,
      ambiguity: $("#task-ambiguity").value,
      labels: $("#task-labels").value.split(",").map((item) => item.trim()).filter(Boolean),
      description: $("#task-description").value,
      actor: state.displayName,
    };
    if (id) body.version = Number($("#task-version").value);
    try {
      const payload = await api(id ? `/api/tasks/${id}` : "/api/tasks", { method: id ? "PATCH" : "POST", body: JSON.stringify(body) });
      dialog.close();
      toast(id ? "Task updated" : "Task created");
      await loadTasks();
      navigate(payload.task.url);
    } catch (error) { toast(error.message, true); }
  }

  async function deleteCurrentTask() {
    const task = state.currentTask;
    if (!task || !confirm(`Permanently delete TASK-${task.id} and all of its comments?`)) return;
    try {
      await api(`/api/tasks/${task.id}`, { method: "DELETE", body: JSON.stringify({ actor: state.displayName }) });
      state.currentTask = null;
      await loadTasks();
      toast("Task deleted");
      navigate("/");
    } catch (error) { toast(error.message, true); }
  }

  function renderTaskDetail() {
    const task = state.currentTask;
    if (!task) return;
    $("#task-loading").classList.add("hidden");
    $("#task-content").classList.remove("hidden");
    $("#detail-id").textContent = `TASK-${task.id}`;
    $("#detail-title").textContent = task.title;
    $("#detail-description").innerHTML = renderMarkdown(task.description);
    const status = state.statuses.find((item) => item.id === task.status)?.label || task.status;
    $("#detail-meta").replaceChildren();
    [status, task.ambiguity ? `${task.ambiguity.replaceAll("_", " ")} ambiguity` : "Ambiguity not assessed", `Updated ${formatTime(task.updated_at)}`].forEach((value, index) => {
      const item = document.createElement("span"); item.className = index < 2 ? `meta-pill ${index === 1 ? task.ambiguity : ""}` : "meta-time"; item.textContent = value; $("#detail-meta").append(item);
    });
    const labels = $("#detail-labels"); labels.replaceChildren();
    task.labels.forEach((value) => { const label = document.createElement("span"); label.className = "label"; label.textContent = value; labels.append(label); });
    const claimState = $("#claim-state"); claimState.replaceChildren();
    if (task.claimed_by) {
      const avatar = document.createElement("span"); avatar.className = "avatar claimed large"; avatar.textContent = initials(task.claimed_by);
      const copy = document.createElement("span"); copy.innerHTML = `<strong>${escapeHtml(task.claimed_by)}</strong><small>Claimed ${formatTime(task.claimed_at)}</small>`;
      claimState.append(avatar, copy);
      $("#claim-form").classList.add("hidden");
      $("#release-claim").classList.remove("hidden");
    } else {
      const avatar = document.createElement("span"); avatar.className = "avatar large"; avatar.textContent = "–";
      const copy = document.createElement("span"); copy.innerHTML = "<strong>Unclaimed</strong><small>Available for an agent</small>";
      claimState.append(avatar, copy);
      $("#claim-form").classList.remove("hidden");
      $("#release-claim").classList.add("hidden");
    }
    document.title = `TASK-${task.id} · ${task.title}`;
  }

  async function loadTaskDetail(taskId, { silent = false } = {}) {
    const scrollTop = window.scrollY;
    if (!silent) {
      $("#task-loading").textContent = "Loading task…";
      $("#task-loading").classList.remove("hidden");
      $("#task-content").classList.add("hidden");
    }
    try {
      const [taskPayload, commentsPayload] = await Promise.all([api(`/api/tasks/${taskId}`), api(`/api/tasks/${taskId}/comments`)]);
      const previousFingerprint = JSON.stringify([
        state.currentTask?.id,
        state.currentTask?.version,
        state.comments.map((comment) => [comment.id, comment.updated_at]),
      ]);
      const nextFingerprint = JSON.stringify([
        taskPayload.task.id,
        taskPayload.task.version,
        commentsPayload.comments.map((comment) => [comment.id, comment.updated_at]),
      ]);
      if (silent && previousFingerprint === nextFingerprint) return;
      state.currentTask = taskPayload.task;
      state.comments = commentsPayload.comments;
      renderTaskDetail();
      renderComments();
      if (silent) {
        window.scrollTo(0, scrollTop);
        requestAnimationFrame(() => window.scrollTo(0, scrollTop));
      }
    } catch (error) {
      if (!silent) {
        state.currentTask = null;
        $("#task-loading").textContent = error.message;
        document.title = "Task not found · Agent Taskboard";
      }
    }
  }

  async function claimCurrentTask(event) {
    event.preventDefault();
    if (!state.currentTask) return;
    const agent = $("#claim-agent").value.trim();
    if (!agent) return;
    localStorage.setItem("taskboard.claim.agent", agent);
    try {
      const payload = await api(`/api/tasks/${state.currentTask.id}/claim`, { method: "POST", body: JSON.stringify({ agent, actor: state.displayName }) });
      state.currentTask = payload.task;
      toast(`Claimed by ${agent}`);
      await loadTasks();
      renderTaskDetail();
    } catch (error) { toast(error.message, true); }
  }

  async function releaseCurrentTask() {
    if (!state.currentTask) return;
    try {
      const payload = await api(`/api/tasks/${state.currentTask.id}/release`, { method: "POST", body: JSON.stringify({ force: true, actor: state.displayName }) });
      state.currentTask = payload.task;
      toast("Claim released");
      await loadTasks();
      renderTaskDetail();
    } catch (error) { toast(error.message, true); }
  }

  function renderCommentEditor(comment, article, body) {
    const form = document.createElement("form"); form.className = "comment-edit-form";
    const textarea = document.createElement("textarea"); textarea.rows = 6; textarea.required = true; textarea.value = comment.body;
    const actions = document.createElement("div"); actions.className = "comment-edit-actions";
    const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "button secondary"; cancel.textContent = "Cancel";
    const save = document.createElement("button"); save.type = "submit"; save.className = "button primary"; save.textContent = "Save changes";
    cancel.addEventListener("click", () => { state.editingCommentId = null; renderComments(); });
    form.addEventListener("submit", (event) => updateComment(event, comment.id, textarea.value));
    actions.append(cancel, save); form.append(textarea, actions); body.replaceWith(form);
    requestAnimationFrame(() => textarea.focus());
  }

  function renderComments() {
    const list = $("#comments-list"); list.replaceChildren();
    $("#comment-count").textContent = state.comments.length;
    if (!state.comments.length) {
      const empty = document.createElement("div"); empty.className = "comments-empty"; empty.textContent = "No comments yet. Add the first update or question."; list.append(empty); return;
    }
    state.comments.forEach((comment) => {
      const article = document.createElement("article"); article.className = "comment"; article.id = `comment-${comment.id}`;
      const header = document.createElement("header");
      const avatar = document.createElement("span"); avatar.className = `avatar ${comment.author_type === "agent" ? "claimed" : "human"}`; avatar.textContent = initials(comment.author);
      const identity = document.createElement("div");
      const name = document.createElement("strong"); name.textContent = comment.author;
      const edited = comment.updated_at !== comment.created_at ? " · edited" : "";
      const meta = document.createElement("span"); meta.textContent = `${comment.author_type} · ${formatTime(comment.created_at)}${edited}`;
      identity.append(name, meta); header.append(avatar, identity);
      const menu = document.createElement("details"); menu.className = "comment-menu";
      const summary = document.createElement("summary"); summary.setAttribute("aria-label", "Comment actions"); summary.textContent = "…";
      const items = document.createElement("div"); items.className = "action-menu-items";
      const edit = document.createElement("button"); edit.type = "button"; edit.textContent = "Edit";
      edit.addEventListener("click", (event) => { event.preventDefault(); state.editingCommentId = comment.id; renderComments(); });
      const remove = document.createElement("button"); remove.type = "button"; remove.className = "danger-item"; remove.textContent = "Delete";
      remove.addEventListener("click", (event) => { event.preventDefault(); deleteComment(comment); });
      items.append(edit, remove); menu.append(summary, items); header.append(menu);
      const body = document.createElement("div"); body.className = "markdown-body comment-body"; body.innerHTML = renderMarkdown(comment.body);
      article.append(header, body); list.append(article);
      if (state.editingCommentId === comment.id) renderCommentEditor(comment, article, body);
    });
  }

  async function updateComment(event, commentId, body) {
    event.preventDefault();
    try {
      const payload = await api(`/api/comments/${commentId}`, {
        method: "PATCH",
        body: JSON.stringify({ body, editor: state.displayName, editor_type: "human" }),
      });
      const index = state.comments.findIndex((comment) => comment.id === commentId);
      if (index >= 0) state.comments[index] = payload.comment;
      state.editingCommentId = null;
      renderComments();
      toast("Comment updated");
    } catch (error) { toast(error.message, true); }
  }

  async function deleteComment(comment) {
    if (!confirm(`Permanently delete this comment by ${comment.author}?`)) return;
    try {
      await api(`/api/comments/${comment.id}`, {
        method: "DELETE",
        body: JSON.stringify({ actor: state.displayName }),
      });
      state.comments = state.comments.filter((item) => item.id !== comment.id);
      if (state.editingCommentId === comment.id) state.editingCommentId = null;
      if (state.currentTask) state.currentTask.comment_count = state.comments.length;
      renderComments();
      await loadTasks();
      toast("Comment deleted");
    } catch (error) { toast(error.message, true); }
  }

  async function addComment(event) {
    event.preventDefault();
    if (!state.currentTask) return;
    const body = $("#comment-body").value;
    try {
      const payload = await api(`/api/tasks/${state.currentTask.id}/comments`, {
        method: "POST",
        body: JSON.stringify({ author: state.displayName, author_type: "human", body }),
      });
      state.comments.push(payload.comment);
      $("#comment-body").value = "";
      state.currentTask.comment_count = state.comments.length;
      renderComments();
      await loadTasks();
      toast("Comment added");
    } catch (error) { toast(error.message, true); }
  }

  function renderArtifacts() {
    const search = $("#artifact-search").value.trim().toLowerCase();
    const artifacts = state.artifacts.filter((item) => item.path.toLowerCase().includes(search));
    const list = $("#artifact-list"); list.replaceChildren();
    if (!artifacts.length) {
      const empty = document.createElement("div"); empty.className = "page-state"; empty.textContent = state.artifacts.length ? "No matching artifacts" : "No HTML artifacts found"; list.append(empty); return;
    }
    artifacts.forEach((artifact) => {
      const card = document.createElement("article"); card.className = "artifact-item";
      const link = document.createElement("a"); link.className = "artifact-main"; link.href = artifact.url; link.target = "_blank"; link.rel = "noopener";
      const icon = document.createElement("span"); icon.className = "artifact-icon"; icon.textContent = "◇";
      const content = document.createElement("span");
      const title = document.createElement("strong"); title.textContent = artifact.name;
      const path = document.createElement("span"); path.textContent = artifact.path;
      const meta = document.createElement("small"); meta.textContent = `${formatSize(artifact.size)} · ${formatTime(artifact.modified_at)}`;
      content.append(title, path, meta);
      const arrow = document.createElement("span"); arrow.className = "artifact-arrow"; arrow.textContent = "↗";
      link.append(icon, content, arrow);
      const menu = document.createElement("details"); menu.className = "artifact-menu";
      const summary = document.createElement("summary"); summary.setAttribute("aria-label", "Artifact actions"); summary.textContent = "…";
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Delete";
      remove.addEventListener("click", (event) => { event.preventDefault(); deleteArtifact(artifact); });
      menu.append(summary, remove); card.append(link, menu); list.append(card);
    });
  }

  async function deleteArtifact(artifact) {
    if (!confirm(`Permanently delete ${artifact.path} from disk?`)) return;
    const encodedPath = artifact.path.split("/").map(encodeURIComponent).join("/");
    try {
      await api(`/api/artifacts/${encodedPath}`, { method: "DELETE", body: JSON.stringify({ actor: state.displayName }) });
      if (state.artifacts.some((item) => item.path === artifact.path)) {
        state.artifacts = state.artifacts.filter((item) => item.path !== artifact.path);
        $("#artifact-count").textContent = state.artifacts.length;
        renderArtifacts();
      }
      toast("Artifact deleted");
    } catch (error) { toast(error.message, true); }
  }

  function showView(name) {
    $$(".view").forEach((view) => view.classList.toggle("active", view.id === `${name}-view`));
    $$(".tab").forEach((tab) => {
      const active = (name === "board" && tab.dataset.route === "/") || (name === "artifacts" && tab.dataset.route === "/artifacts");
      tab.classList.toggle("active", active);
    });
  }

  function route() {
    const taskMatch = location.pathname.match(/^\/tasks\/(\d+)\/?$/);
    if (taskMatch) {
      showView("task");
      loadTaskDetail(Number(taskMatch[1]));
    } else if (location.pathname === "/artifacts" || location.pathname === "/artifacts/") {
      showView("artifacts"); renderArtifacts(); document.title = "Artifacts · Agent Taskboard";
    } else {
      if (location.pathname !== "/") history.replaceState({}, "", "/");
      showView("board"); renderBoard(); document.title = "Agent Taskboard";
    }
  }

  async function loadTasks() {
    const payload = await api("/api/tasks");
    state.tasks = payload.tasks;
    renderBoard();
  }

  async function loadArtifacts() {
    const payload = await api("/api/artifacts");
    state.artifacts = payload.artifacts;
    $("#artifact-count").textContent = state.artifacts.length;
    renderArtifacts();
  }

  async function refresh(showToast = false) {
    try {
      await Promise.all([loadTasks(), loadArtifacts()]);
      const health = await api("/api/health");
      $("#health").className = "health ok";
      $("#health").lastChild.textContent = ` ${health.active_tasks} tasks`;
      const match = location.pathname.match(/^\/tasks\/(\d+)\/?$/);
      const taskId = match ? Number(match[1]) : null;
      if (taskId && state.currentTask?.id === taskId && !state.editingCommentId) {
        await loadTaskDetail(taskId, { silent: true });
      }
      if (showToast) toast("Taskboard refreshed");
    } catch (error) {
      $("#health").className = "health error";
      $("#health").lastChild.textContent = " Offline";
      toast(error.message, true);
    }
  }

  async function initialize() {
    $$(".route-button, .tab").forEach((button) => button.addEventListener("click", () => navigate(button.dataset.route)));
    $("#new-task").addEventListener("click", () => openEditor());
    $("#refresh").addEventListener("click", () => refresh(true));
    $("#refresh-artifacts").addEventListener("click", () => loadArtifacts().then(() => toast("Artifacts refreshed")).catch((error) => toast(error.message, true)));
    $("#search").addEventListener("input", renderBoard);
    $("#claim-filter").addEventListener("change", renderBoard);
    $("#artifact-search").addEventListener("input", renderArtifacts);
    $("#task-form").addEventListener("submit", saveTask);
    $("#edit-task").addEventListener("click", () => state.currentTask && openEditor(state.currentTask));
    $("#delete-task").addEventListener("click", deleteCurrentTask);
    $("#claim-form").addEventListener("submit", claimCurrentTask);
    $("#release-claim").addEventListener("click", releaseCurrentTask);
    $("#comment-form").addEventListener("submit", addComment);
    $("#copy-task-link").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(location.href); toast("Task URL copied"); }
      catch (_) { toast(location.href); }
    });
    $$(".close-dialog").forEach((button) => button.addEventListener("click", () => dialog.close()));
    dialog.addEventListener("click", (event) => {
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    });
    window.addEventListener("popstate", route);
    $("#claim-agent").value = localStorage.getItem("taskboard.claim.agent") || "";
    try {
      const config = await api("/api/config");
      state.displayName = config.identity.display_name;
      state.statuses = config.statuses;
      $("#task-status").replaceChildren(...state.statuses.map((status) => new Option(status.label, status.id)));
      await refresh();
      route();
    } catch (error) { toast(error.message, true); }
    setInterval(() => { if (!dialog.open && document.visibilityState === "visible") refresh(); }, 15000);
  }

  initialize();
})();
