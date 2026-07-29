"use strict";

(() => {
  const API_ROOT = "/api/hermes/v1";
  const REFRESH_MS = 4_000;
  const HIDDEN_REFRESH_MS = 12_000;
  const MAX_EVENT_PAGES = 10;
  const EVENT_PAGE_SIZE = 500;

  const CHANNELS = [
    {
      id: "all",
      label: "全部事件",
      title: "全部真实事件",
      description: "按数据库游标排序；未验证来源会明确标记。",
    },
    {
      id: "control-room",
      label: "# control-room",
      title: "总控室",
      description: "目标、总控状态与唯一下一步。",
    },
    {
      id: "planning",
      label: "# planning",
      title: "规划",
      description: "任务理解、拆解与验收标准。",
    },
    {
      id: "implementation",
      label: "# implementation",
      title: "实现",
      description: "Coding Agent、Host Worker 与 Commit 事实。",
    },
    {
      id: "review",
      label: "# review",
      title: "独立审查",
      description: "Reviewer 发现、返工要求与复审结果。",
    },
    {
      id: "qa",
      label: "# qa",
      title: "质量保障",
      description: "Docker 测试、CI 与失败证据。",
    },
    {
      id: "release",
      label: "# release",
      title: "交付",
      description: "受控 Push、Draft PR 与当前 Head。",
    },
    {
      id: "user-action-required",
      label: "# user-action-required",
      title: "需要人工处理",
      description: "只显示授权、重大歧义与最终验收。",
    },
  ];

  const APP_ACTIONS = ["pause", "resume", "supplement", "reject", "approve"];

  const WORKFLOW_LABELS = {
    pending: "待领取",
    claimed: "已领取",
    running: "工作中",
    agent_done: "等待调度",
    in_review: "独立审查中",
    await_user: "等待人工验收",
    ready_for_manual_merge: "已批准，等待 GitHub 操作",
    escalated: "需要人工处理",
    failed: "任务失败",
    completed: "任务完成",
    CODEX_RUNNING: "Coding Agent 工作中",
    REPOSITORY_PREPARING: "准备独立工作区",
    TESTING: "Docker 测试中",
    COMMITTING: "生成 Commit",
    PUSHING: "受控 Push",
    PR_CREATED: "Draft PR 已创建",
    CODEX_FAILED: "Coding Agent 失败",
    CODEX_NO_CHANGES: "未产生代码变更",
    TEST_FAILED: "测试失败",
    BLOCKED: "任务受阻",
  };

  const EVENT_TITLES = {
    task_created: "任务已进入 Control Plane",
    "operator.pause": "Human Owner 暂停任务",
    "operator.resume": "Human Owner 继续任务",
    operator_requirements_added: "Human Owner 补充要求",
    operator_approved: "Human Owner 批准交付",
    operator_rejected: "Human Owner 拒绝结果",
    plan_created: "规划已持久化",
    progress: "实现进度",
    agent_run: "Coding Agent 运行证据",
    repository_prepared: "独立工作区已准备",
    codex_result: "Host Codex 运行结果",
    inspect_changes: "工作树变更检查",
    docker_test_result: "Docker 测试结果",
    push: "受控 Push",
    round2_push: "返工 Commit 已 Push",
    draft_pr: "Draft PR 已创建",
    pr_created: "PR 事件",
    round2_label: "返工轮次已标记",
    review: "独立审查结果",
    head_mismatch: "PR Head 不匹配",
    ci_passed: "CI 已通过",
    ci_fail: "CI 未通过",
    await_user: "等待 Human Owner 最终验收",
    escalated: "需要 Human Owner 处理",
  };

  const ACTOR_LABELS = {
    human_owner: "Human Owner",
    hermes_master: "Hermes Master",
    planner: "Planning Agent",
    planning_agent: "Planning Agent",
    coding: "Coding Agent",
    coding_agent: "Coding Agent",
    reviewer: "Independent Reviewer",
    independent_reviewer: "Independent Reviewer",
    qa: "QA Agent",
    qa_agent: "QA Agent",
    release: "Release Agent",
    release_agent: "Release Agent",
    scheduler: "Hermes Scheduler",
    worker: "Host Worker",
  };

  const FACT_KEYS = [
    "round",
    "branch",
    "commit_sha",
    "new_head",
    "expected_head",
    "actual_head",
    "pr_number",
    "ci_status",
    "requested_ci_status",
    "actual_ci_status",
    "verdict",
    "findings",
    "exit_code",
    "passed",
    "failed",
    "skipped",
    "timed_out",
    "cleanup_succeeded",
    "residual_container_count",
    "changed_files",
    "modified_files",
    "requirements",
    "requirements_revision",
  ];

  const state = {
    csrfToken: null,
    identityMode: null,
    serviceOnline: false,
    clientCached: false,
    tasks: [],
    selectedTask: null,
    events: [],
    activeChannel: "all",
    refreshTimer: null,
    refreshInFlight: false,
    lastUpdatedAt: null,
    paletteItems: [],
    paletteIndex: 0,
  };

  const ui = {
    workbench: document.querySelector('[data-ui="workbench"]'),
    serviceState: document.querySelector('[data-ui="service-state"]'),
    serviceStateLabel: document.querySelector('[data-ui="service-state-label"]'),
    connectionState: document.querySelector('[data-ui="connection-state"]'),
    connectionStateLabel: document.querySelector('[data-ui="connection-state-label"]'),
    taskList: document.querySelector('[data-ui="task-list"]'),
    taskCount: document.querySelector('[data-ui="task-count"]'),
    channelList: document.querySelector('[data-ui="channel-list"]'),
    channelTitle: document.querySelector('[data-ui="channel-title"]'),
    channelDescription: document.querySelector('[data-ui="channel-description"]'),
    threadStage: document.querySelector('[data-ui="thread-stage"]'),
    threadTitle: document.querySelector('[data-ui="thread-title"]'),
    threadSummary: document.querySelector('[data-ui="thread-summary"]'),
    workflowState: document.querySelector('[data-ui="workflow-state"]'),
    controlState: document.querySelector('[data-ui="control-state"]'),
    taskVersion: document.querySelector('[data-ui="task-version"]'),
    operatorActions: document.querySelector('[data-ui="operator-actions"]'),
    actionReason: document.querySelector('[data-ui="action-reason"]'),
    actionRequirements: document.querySelector('[data-ui="action-requirements"]'),
    actionFeedback: document.querySelector('[data-ui="action-feedback"]'),
    eventStream: document.querySelector('[data-ui="event-stream"]'),
    refreshEvents: document.querySelector('[data-ui="refresh-events"]'),
    evidenceTrust: document.querySelector('[data-ui="evidence-trust"]'),
    taskId: document.querySelector('[data-ui="task-id"]'),
    taskRepo: document.querySelector('[data-ui="task-repo"]'),
    issueLink: document.querySelector('[data-ui="issue-link"]'),
    commitSha: document.querySelector('[data-ui="commit-sha"]'),
    prLink: document.querySelector('[data-ui="pr-link"]'),
    ciStatus: document.querySelector('[data-ui="ci-status"]'),
    heartbeatAt: document.querySelector('[data-ui="heartbeat-at"]'),
    updatedAt: document.querySelector('[data-ui="updated-at"]'),
    commandForm: document.querySelector('[data-ui="command-form"] form'),
    commandFeedback: document.querySelector('[data-ui="command-feedback"]'),
    cacheState: document.querySelector('[data-ui="cache-state"]'),
    identityMode: document.querySelector('[data-ui="identity-mode"]'),
    focusCommand: document.querySelector('[data-ui="focus-command"]'),
    searchTrigger: document.querySelector('[data-ui="search-trigger"]'),
    commandPalette: document.querySelector('[data-ui="command-palette"]'),
    paletteSearch: document.querySelector('[data-ui="palette-search"]'),
    paletteResults: document.querySelector('[data-ui="palette-results"]'),
    toastRegion: document.querySelector('[data-ui="toast-region"]'),
  };

  class ApiError extends Error {
    constructor(status, payload) {
      super(payload && payload.error ? String(payload.error) : `http_${status}`);
      this.name = "ApiError";
      this.status = status;
      this.payload = payload;
    }
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function clear(node) {
    node.replaceChildren();
  }

  function requestId(prefix) {
    if (globalThis.crypto && typeof globalThis.crypto.randomUUID === "function") {
      return `${prefix}:${globalThis.crypto.randomUUID()}`;
    }
    const random = Math.random().toString(36).slice(2);
    return `${prefix}:${Date.now()}:${random}`;
  }

  async function api(path, options = {}) {
    const method = options.method || "GET";
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    const request = {
      method,
      headers,
      credentials: "same-origin",
      cache: "no-store",
    };
    if (options.body !== undefined) {
      headers.set("Content-Type", "application/json");
      if (state.csrfToken) {
        headers.set("X-CSRF-Token", state.csrfToken);
      }
      request.body = JSON.stringify(options.body);
    }
    const response = await fetch(`${API_ROOT}${path}`, request);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new ApiError(response.status, payload);
    }
    return payload;
  }

  function setTone(node, tone) {
    if (node) {
      node.dataset.tone = tone;
    }
  }

  function setServiceTruth(mode, label) {
    const tone = mode === "online" ? "success" : mode === "connecting" ? "pending" : "error";
    setTone(ui.serviceState, tone);
    ui.serviceStateLabel.textContent = label;
  }

  function connectionTruth() {
    if (!state.serviceOnline) {
      return {
        label: state.clientCached ? "连接中断 · 上次读取" : "连接中断",
        tone: "error",
      };
    }
    if (!state.selectedTask) {
      return { label: "Agent 未启动", tone: "neutral" };
    }
    const connection = state.selectedTask.connection_state;
    if (connection === "online") {
      return { label: "Agent 实时在线", tone: "success" };
    }
    if (connection === "agent_not_started") {
      return { label: "Agent 未启动", tone: "neutral" };
    }
    if (connection === "disconnected") {
      return { label: "Agent 连接中断", tone: "error" };
    }
    return { label: `连接状态：${connection || "未知"}`, tone: "warning" };
  }

  function renderConnectionTruth() {
    const truth = connectionTruth();
    setTone(ui.connectionState, truth.tone);
    ui.connectionStateLabel.textContent = truth.label;
    ui.cacheState.textContent = state.clientCached ? "是（上次读取）" : "否";
    ui.identityMode.textContent = state.identityMode || "未确认";
  }

  function workflowLabel(raw) {
    return WORKFLOW_LABELS[raw] || raw || "未知";
  }

  function controlLabel(raw) {
    if (raw === "paused") {
      return "已暂停";
    }
    if (raw === "active") {
      return "活动";
    }
    return raw || "未知";
  }

  function formatTime(value) {
    if (value === null || value === undefined || value === "") {
      return "—";
    }
    let date;
    if (typeof value === "number") {
      date = new Date(value * 1_000);
    } else {
      const numeric = Number(value);
      date = Number.isFinite(numeric) && String(value).trim() !== ""
        ? new Date(numeric * 1_000)
        : new Date(value);
    }
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(date);
  }

  function shortSha(value) {
    if (!value) {
      return "—";
    }
    const text = String(value);
    return text.length > 12 ? text.slice(0, 12) : text;
  }

  function safeGithubUrl(repo, kind, number) {
    if (repo !== "yzhlx/hermes-open-swe-smoke-test") {
      return null;
    }
    const parsed = Number(number);
    if (!Number.isInteger(parsed) || parsed < 1) {
      return null;
    }
    const segment = kind === "issue" ? "issues" : "pull";
    return `https://github.com/${repo}/${segment}/${parsed}`;
  }

  function setLinkSlot(slot, label, href) {
    clear(slot);
    if (!href) {
      slot.textContent = "—";
      return;
    }
    const link = element("a", null, label);
    link.href = href;
    link.target = "_blank";
    link.rel = "noreferrer";
    slot.append(link);
  }

  function renderEvidence() {
    const task = state.selectedTask;
    if (!task) {
      ui.taskId.textContent = "—";
      ui.taskRepo.textContent = "—";
      ui.issueLink.textContent = "—";
      ui.commitSha.textContent = "—";
      ui.prLink.textContent = "—";
      ui.ciStatus.textContent = "—";
      ui.heartbeatAt.textContent = "—";
      ui.updatedAt.textContent = state.lastUpdatedAt ? formatTime(state.lastUpdatedAt) : "—";
      ui.evidenceTrust.textContent = "无任务";
      setTone(ui.evidenceTrust, "neutral");
      return;
    }

    ui.taskId.textContent = task.task_id || `job:${task.job_id}`;
    ui.taskRepo.textContent = task.repo || "—";
    ui.commitSha.textContent = shortSha(task.commit_sha);
    if (task.commit_sha) {
      ui.commitSha.title = String(task.commit_sha);
    } else {
      ui.commitSha.removeAttribute("title");
    }
    ui.ciStatus.textContent = task.ci_status || "—";
    ui.heartbeatAt.textContent = formatTime(task.last_heartbeat_at);
    ui.updatedAt.textContent = state.lastUpdatedAt ? formatTime(state.lastUpdatedAt) : "—";

    setLinkSlot(
      ui.issueLink,
      `Issue #${task.issue_number}`,
      safeGithubUrl(task.repo, "issue", task.issue_number),
    );
    setLinkSlot(
      ui.prLink,
      `Draft PR #${task.pr_number}`,
      safeGithubUrl(task.repo, "pr", task.pr_number),
    );

    if (task.pr_number && task.commit_sha && task.ci_status === "success") {
      ui.evidenceTrust.textContent = "GitHub 证据已关联";
      setTone(ui.evidenceTrust, "success");
    } else if (task.pr_number || task.commit_sha || task.ci_status) {
      ui.evidenceTrust.textContent = "部分工程证据";
      setTone(ui.evidenceTrust, "warning");
    } else {
      ui.evidenceTrust.textContent = "仅 Control Plane";
      setTone(ui.evidenceTrust, "neutral");
    }
  }

  function taskLabel(task) {
    const goal = typeof task.goal === "string" && task.goal.trim()
      ? task.goal.trim()
      : task.task_id || `Job ${task.job_id}`;
    return goal;
  }

  function renderTasks() {
    clear(ui.taskList);
    ui.taskList.setAttribute("aria-busy", "false");
    ui.taskCount.textContent = String(state.tasks.length);

    if (!state.tasks.length) {
      const empty = element("div", "empty-state");
      empty.append(
        element("span", "empty-glyph"),
        element("h3", null, "暂无真实任务"),
        element("p", null, "在下方输入目标，任务会直接写入 Control Plane。"),
      );
      ui.taskList.append(empty);
      return;
    }

    const ordered = [...state.tasks].sort((left, right) => right.job_id - left.job_id);
    for (const task of ordered) {
      const button = element("button", "task-item");
      button.type = "button";
      button.dataset.jobId = String(task.job_id);
      button.setAttribute(
        "aria-current",
        state.selectedTask && state.selectedTask.job_id === task.job_id ? "true" : "false",
      );
      const title = element("strong", null, taskLabel(task));
      const meta = element(
        "span",
        null,
        `${workflowLabel(task.state)} · ${task.task_id || `job:${task.job_id}`}`,
      );
      button.append(title, meta);
      button.addEventListener("click", () => selectTask(task.job_id));
      ui.taskList.append(button);
    }
  }

  function channelCounts() {
    const counts = new Map(CHANNELS.map((channel) => [channel.id, 0]));
    counts.set("all", state.events.length);
    for (const event of state.events) {
      const key = event.channel || "control-room";
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return counts;
  }

  function renderChannels() {
    const counts = channelCounts();
    clear(ui.channelList);
    for (const channel of CHANNELS) {
      const button = element("button", "channel-button");
      button.type = "button";
      button.dataset.channel = channel.id;
      button.setAttribute("aria-current", state.activeChannel === channel.id ? "true" : "false");
      button.append(
        element("span", null, channel.label),
        element("span", "channel-count", counts.get(channel.id) || 0),
      );
      button.addEventListener("click", () => setChannel(channel.id));
      ui.channelList.append(button);
    }
  }

  function setChannel(channelId) {
    const next = CHANNELS.find((channel) => channel.id === channelId) || CHANNELS[0];
    state.activeChannel = next.id;
    ui.channelTitle.textContent = next.title;
    ui.channelDescription.textContent = next.description;
    renderChannels();
    renderEvents();
  }

  function actorLabel(event) {
    if (event.unverified_display) {
      return "未验证来源";
    }
    if (event.actor_role && ACTOR_LABELS[event.actor_role]) {
      return ACTOR_LABELS[event.actor_role];
    }
    if (event.source_type === "github") {
      return "GitHub";
    }
    if (event.source_type === "operator") {
      return "Human Owner";
    }
    if (event.source_type === "worker") {
      return "Host Worker";
    }
    return event.source_type || "Control Plane";
  }

  function eventTitle(event) {
    return EVENT_TITLES[event.type] || EVENT_TITLES[event.raw_type] || event.raw_type || "未知事件";
  }

  function firstText(payload, keys) {
    for (const key of keys) {
      const value = payload[key];
      if (typeof value === "string" && value.trim()) {
        return value.trim();
      }
    }
    return null;
  }

  function eventMessage(event) {
    const payload = event.payload || {};
    const supplied = firstText(payload, ["message", "summary", "goal", "instruction"]);
    if (supplied) {
      return supplied;
    }
    switch (event.raw_type) {
      case "pause_requested":
      case "paused":
        return payload.reason || "Human Owner 请求在安全检查点暂停。";
      case "resumed":
        return payload.reason || "Human Owner 请求继续同一个任务。";
      case "operator_requirements_added":
        return payload.reason || "新的验收要求已写入任务。";
      case "operator_approved":
        return payload.reason || "结果已获 Human Owner 批准；后续 GitHub 操作仍由人工决定。";
      case "operator_rejected":
        return payload.reason || "结果被退回，任务将在同一证据链继续。";
      case "agent_run":
        return `已记录第 ${payload.round || "—"} 轮 Coding Agent 运行。`;
      case "repository_prepared":
        return "Host Worker 已记录独立工作区准备结果。";
      case "codex_result":
        return `Host Codex 已退出，exit code ${payload.exit_code ?? "—"}。`;
      case "inspect_changes":
        return "Host Worker 已检查工作树变更。";
      case "docker_test_result":
        return `Docker 测试退出码 ${payload.exit_code ?? "—"}；清理结果已记录。`;
      case "push":
        return `分支 ${payload.branch || "—"} 已完成受控 Push。`;
      case "round2_push":
        return `返工已更新 Draft PR #${payload.pr_number || "—"} 的 Head。`;
      case "draft_pr":
      case "pr_created":
        return `Draft PR #${payload.pr_number || "—"} 已关联到当前任务。`;
      case "review":
        return `独立审查结论：${payload.verdict || "—"}。`;
      case "head_mismatch":
        return "Control Plane 发现任务 Commit 与当前 PR Head 不一致，验收已停止。";
      case "ci_passed":
        return "GitHub CI 成功事实已写入事件库。";
      case "ci_fail":
        return "CI 未达到 success，独立审查或最终验收不会继续。";
      case "await_user":
        return "任务已到达 Human Owner 最终验收门。";
      case "escalated":
        return "自动返工已停止，需要 Human Owner 处理。";
      default:
        return `持久化事件：${event.raw_type || event.type || "unknown"}。`;
    }
  }

  function factValue(value) {
    if (value === null || value === undefined) {
      return "—";
    }
    if (Array.isArray(value)) {
      return value.map((item) => String(item)).join(" · ") || "—";
    }
    if (typeof value === "object") {
      const text = JSON.stringify(value);
      return text.length > 240 ? `${text.slice(0, 237)}…` : text;
    }
    const text = String(value);
    return text.length > 240 ? `${text.slice(0, 237)}…` : text;
  }

  function eventFacts(payload) {
    return FACT_KEYS
      .filter((key) => Object.hasOwn(payload, key))
      .map((key) => [key, factValue(payload[key])]);
  }

  function renderEvents() {
    const visible = state.activeChannel === "all"
      ? state.events
      : state.events.filter((event) => event.channel === state.activeChannel);
    clear(ui.eventStream);
    ui.eventStream.setAttribute("aria-busy", "false");

    if (!visible.length) {
      const empty = element("li", "empty-state");
      empty.append(
        element("span", "empty-glyph"),
        element("h3", null, state.selectedTask ? "这个频道还没有事件" : "还没有事件"),
        element(
          "p",
          null,
          state.selectedTask
            ? "事件只会在对应工程步骤真实发生后出现。"
            : "创建任务后，Human Owner 的原始命令会成为第一条持久化事件。",
        ),
      );
      ui.eventStream.append(empty);
      return;
    }

    for (const event of visible) {
      const row = element("li", "event-row");
      const marker = element("span", "event-marker");
      marker.setAttribute("aria-hidden", "true");
      const body = element("article", "event-body");
      const meta = element("div", "event-meta");
      meta.append(
        element("span", "event-actor", actorLabel(event)),
        element("span", "event-channel", `# ${event.channel || "control-room"}`),
        element("time", "tnum", formatTime(event.timestamp)),
      );
      const trust = element(
        "span",
        "event-trust",
        event.unverified_display ? "来源未验证" : `${event.source_type}:${event.source_id}`,
      );
      trust.dataset.unverified = event.unverified_display ? "true" : "false";
      meta.append(trust);

      const title = element("h3", "event-title", eventTitle(event));
      const message = element("p", "event-message", eventMessage(event));
      body.append(meta, title, message);

      const facts = eventFacts(event.payload || {});
      if (facts.length) {
        const list = element("dl", "event-facts");
        for (const [key, value] of facts) {
          list.append(element("dt", null, key), element("dd", null, value));
        }
        body.append(list);
      }
      row.append(marker, body);
      ui.eventStream.append(row);
    }
  }

  function renderSelectedTask() {
    const task = state.selectedTask;
    if (!task) {
      ui.threadStage.textContent = "NO ACTIVE TASK";
      ui.threadTitle.textContent = "等待第一个真实任务";
      ui.threadSummary.textContent = "在下方输入开发目标。创建后，这里只展示持久化事件，不生成角色台词。";
      ui.workflowState.textContent = "未开始";
      ui.controlState.textContent = "活动";
      ui.taskVersion.textContent = "—";
    } else {
      ui.threadStage.textContent = String(task.task_id || `JOB ${task.job_id}`).toUpperCase();
      ui.threadTitle.textContent = taskLabel(task);
      const scope = typeof task.scope === "string"
        ? task.scope
        : Array.isArray(task.scope)
          ? task.scope.join(" · ")
          : "";
      ui.threadSummary.textContent = scope || "该任务未提供额外工作范围说明。";
      ui.workflowState.textContent = workflowLabel(task.state);
      ui.controlState.textContent = controlLabel(task.control_state);
      ui.taskVersion.textContent = String(task.version ?? "—");
    }
    renderConnectionTruth();
    renderActionAvailability();
    renderEvidence();
    renderTasks();
    renderChannels();
    renderEvents();
    ui.refreshEvents.disabled = !task;
  }

  function taskIsTerminal(task) {
    return task && ["completed", "failed", "escalated", "BLOCKED"].includes(task.state);
  }

  function renderActionAvailability() {
    const task = state.selectedTask;
    const buttons = ui.operatorActions.querySelectorAll("[data-action]");
    for (const button of buttons) {
      const action = button.dataset.action;
      let enabled = Boolean(task && state.serviceOnline && state.csrfToken);
      if (enabled && action === "pause") {
        enabled = task.control_state === "active" && !taskIsTerminal(task) && task.state !== "await_user";
      } else if (enabled && action === "resume") {
        enabled = task.control_state === "paused" && !taskIsTerminal(task);
      } else if (enabled && action === "supplement") {
        enabled = !taskIsTerminal(task);
      } else if (enabled && action === "approve") {
        enabled = task.state === "await_user";
      } else if (enabled && action === "reject") {
        enabled = ["await_user", "ready_for_manual_merge"].includes(task.state);
      }
      button.disabled = !enabled;
      button.setAttribute("aria-disabled", enabled ? "false" : "true");
    }
  }

  function updateUrl(task) {
    const url = new URL(location.href);
    if (task) {
      url.searchParams.set("task", task.task_id || String(task.job_id));
    } else {
      url.searchParams.delete("task");
    }
    history.replaceState(null, "", url);
  }

  async function loadTaskDetail(jobId) {
    const payload = await api(`/tasks/${encodeURIComponent(jobId)}`);
    return payload.task;
  }

  function collapseAtomicEvents(events) {
    const result = [];
    for (const event of events) {
      const previous = result[result.length - 1];
      const samePauseRequest = previous
        && previous.type === "operator.pause"
        && event.type === "operator.pause"
        && previous.source_type === event.source_type
        && previous.source_id === event.source_id
        && previous.payload
        && event.payload
        && previous.payload.request_id === event.payload.request_id;
      if (samePauseRequest) {
        result[result.length - 1] = event;
      } else {
        result.push(event);
      }
    }
    return result;
  }

  async function loadAllEvents(jobId) {
    const events = [];
    let cursor = 0;
    for (let page = 0; page < MAX_EVENT_PAGES; page += 1) {
      const payload = await api(
        `/tasks/${encodeURIComponent(jobId)}/events?after=${cursor}&limit=${EVENT_PAGE_SIZE}`,
      );
      const batch = Array.isArray(payload.events) ? payload.events : [];
      events.push(...batch);
      if (batch.length < EVENT_PAGE_SIZE) {
        break;
      }
      const next = Number(payload.next_cursor);
      if (!Number.isInteger(next) || next <= cursor) {
        break;
      }
      cursor = next;
    }
    return collapseAtomicEvents(events);
  }

  async function selectTask(jobId, options = {}) {
    const candidate = state.tasks.find((task) => task.job_id === Number(jobId));
    if (candidate) {
      state.selectedTask = candidate;
      renderSelectedTask();
    }
    try {
      const [task, events] = await Promise.all([
        loadTaskDetail(jobId),
        loadAllEvents(jobId),
      ]);
      state.selectedTask = task;
      state.events = events;
      state.lastUpdatedAt = new Date();
      state.clientCached = false;
      updateUrl(task);
      renderSelectedTask();
    } catch (error) {
      state.clientCached = Boolean(state.selectedTask);
      renderConnectionTruth();
      if (!options.silent) {
        reportFailure("无法读取该任务的最新事件。", error, () => selectTask(jobId));
      }
    }
  }

  function selectedFromLocation(tasks) {
    const ref = new URL(location.href).searchParams.get("task");
    if (ref) {
      const match = tasks.find(
        (task) => task.task_id === ref || String(task.job_id) === ref,
      );
      if (match) {
        return match;
      }
    }
    return [...tasks].sort((left, right) => right.job_id - left.job_id)[0] || null;
  }

  async function refreshData(options = {}) {
    if (state.refreshInFlight) {
      return;
    }
    state.refreshInFlight = true;
    try {
      const [statusPayload, tasksPayload] = await Promise.all([
        api("/status"),
        api("/tasks"),
      ]);
      state.serviceOnline = Boolean(statusPayload.ok && statusPayload.ui_available);
      state.identityMode = statusPayload.identity_mode || state.identityMode;
      state.tasks = Array.isArray(tasksPayload.tasks) ? tasksPayload.tasks : [];
      state.clientCached = false;
      state.lastUpdatedAt = new Date();
      setServiceTruth("online", "Control Plane 在线");

      const currentId = state.selectedTask && state.selectedTask.job_id;
      const next = currentId
        ? state.tasks.find((task) => task.job_id === currentId)
        : selectedFromLocation(state.tasks);
      state.selectedTask = next || null;
      if (state.selectedTask) {
        const [freshTask, events] = await Promise.all([
          loadTaskDetail(state.selectedTask.job_id),
          loadAllEvents(state.selectedTask.job_id),
        ]);
        state.selectedTask = freshTask;
        state.events = events;
        updateUrl(freshTask);
      } else {
        state.events = [];
        updateUrl(null);
      }
      renderSelectedTask();
    } catch (error) {
      state.serviceOnline = false;
      state.clientCached = state.tasks.length > 0;
      setServiceTruth("offline", "Control Plane 连接中断");
      renderConnectionTruth();
      renderActionAvailability();
      if (!options.silent) {
        reportFailure("无法读取 Control Plane。后台任务状态尚未确认。", error, () => refreshData());
      }
    } finally {
      state.refreshInFlight = false;
      scheduleRefresh();
    }
  }

  function scheduleRefresh() {
    if (state.refreshTimer) {
      globalThis.clearTimeout(state.refreshTimer);
    }
    const delay = document.visibilityState === "hidden" ? HIDDEN_REFRESH_MS : REFRESH_MS;
    state.refreshTimer = globalThis.setTimeout(() => refreshData({ silent: true }), delay);
  }

  function errorMessage(error) {
    const code = error instanceof ApiError ? error.message : "network_unavailable";
    const messages = {
      origin_forbidden: "请求来源不受信任。请从工作台固定地址操作。",
      csrf_forbidden: "本地身份会话已失效，正在重新建立会话。",
      repo_not_allowed: "目标仓库不在授权白名单。",
      goal_required: "开发目标不能为空。",
      request_id_required: "请求标识缺失，任务未创建。",
      version_conflict: "任务已被其他事件更新；最新版本已重新载入。",
      operator_action_not_allowed: "该人工动作不在允许列表。",
      operator_action_requires_pending_job: "当前阶段不能暂停；请等待安全检查点。",
      approve_not_allowed_in_state: "任务尚未到达最终验收门。",
      reject_not_allowed_in_state: "当前阶段没有可拒绝的交付结果。",
      requirements_required: "请至少填写一条补充要求。",
      idempotency_conflict: "同一请求标识对应了不同内容，写入已拒绝。",
      payload_too_large: "请求超过 64 KiB 限制，请缩短说明。",
      network_unavailable: "本地服务不可达，请确认工作台后台仍在运行。",
    };
    return messages[code] || `请求失败：${code}`;
  }

  function showToast(message, retry) {
    const toast = element("section", "toast");
    toast.append(element("p", null, message));
    const actions = element("div", "action-row");
    if (typeof retry === "function") {
      const retryButton = element("button", null, "重试");
      retryButton.type = "button";
      retryButton.addEventListener("click", () => {
        toast.remove();
        retry();
      });
      actions.append(retryButton);
    }
    const closeButton = element("button", null, "关闭");
    closeButton.type = "button";
    closeButton.addEventListener("click", () => toast.remove());
    actions.append(closeButton);
    toast.append(actions);
    ui.toastRegion.append(toast);
  }

  function reportFailure(context, error, retry) {
    showToast(`${context} ${errorMessage(error)}`, retry);
  }

  function setFeedback(node, message, tone = "neutral") {
    node.textContent = message;
    node.dataset.tone = tone;
  }

  function setButtonState(button, status, label) {
    if (!button.dataset.idleLabel) {
      button.dataset.idleLabel = button.textContent.trim();
    }
    if (status) {
      button.dataset.state = status;
    } else {
      delete button.dataset.state;
    }
    button.textContent = label || button.dataset.idleLabel;
  }

  async function bootstrapSession() {
    const payload = await api("/session");
    state.csrfToken = payload.csrf_token;
    state.identityMode = payload.identity_mode;
    renderConnectionTruth();
  }

  async function submitTask(event) {
    event.preventDefault();
    if (!ui.commandForm.reportValidity()) {
      return;
    }
    const submit = ui.commandForm.querySelector('button[type="submit"]');
    const formData = new FormData(ui.commandForm);
    const goal = String(formData.get("goal") || "").trim();
    const scope = String(formData.get("scope") || "").trim();
    const acceptance = String(formData.get("acceptance") || "")
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
    const repo = String(formData.get("repo") || "");
    const retained = ui.commandForm.dataset.requestId;
    const currentRequestId = retained || requestId("workbench");
    ui.commandForm.dataset.requestId = currentRequestId;

    setButtonState(submit, "loading", "正在创建…");
    submit.disabled = true;
    setFeedback(ui.commandFeedback, "目标正在写入 Control Plane。");
    try {
      const payload = await api("/tasks", {
        method: "POST",
        body: {
          request_id: currentRequestId,
          repo,
          goal,
          scope,
          acceptance,
        },
      });
      delete ui.commandForm.dataset.requestId;
      ui.commandForm.reset();
      setButtonState(submit, "success", "任务已创建");
      setFeedback(
        ui.commandFeedback,
        `${payload.task.task_id} 已进入 Control Plane。`,
        "success",
      );
      await refreshData({ silent: true });
      await selectTask(payload.task.job_id, { silent: true });
      globalThis.setTimeout(() => setButtonState(submit, null), 1_000);
    } catch (error) {
      setButtonState(submit, "error", "创建失败");
      setFeedback(ui.commandFeedback, errorMessage(error), "error");
      if (error instanceof ApiError && error.message === "csrf_forbidden") {
        state.csrfToken = null;
        await bootstrapSession().catch(() => null);
      }
      reportFailure("任务没有写入 Control Plane。", error, () => submit.click());
    } finally {
      submit.disabled = false;
      globalThis.setTimeout(() => {
        if (submit.dataset.state === "error") {
          setButtonState(submit, null);
        }
      }, 1_500);
    }
  }

  async function performAction(action, button) {
    const task = state.selectedTask;
    if (!task || !APP_ACTIONS.includes(action)) {
      return;
    }
    const reason = ui.actionReason.value.trim();
    const requirements = ui.actionRequirements.value
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean);
    if (action === "supplement" && !requirements.length) {
      ui.actionRequirements.setAttribute("aria-invalid", "true");
      setFeedback(ui.actionFeedback, "请至少填写一条补充要求。", "error");
      ui.actionRequirements.focus();
      return;
    }
    ui.actionRequirements.removeAttribute("aria-invalid");
    setButtonState(button, "loading", "正在写入…");
    button.disabled = true;
    try {
      const payload = await api(`/tasks/${encodeURIComponent(task.job_id)}/actions`, {
        method: "POST",
        body: {
          action,
          request_id: requestId(`operator:${action}`),
          expected_version: task.version,
          reason: reason || null,
          requirements: action === "supplement" ? requirements : null,
        },
      });
      state.selectedTask = payload.task;
      if (action === "supplement") {
        ui.actionRequirements.value = "";
      }
      ui.actionReason.value = "";
      setButtonState(button, "success", "已写入");
      setFeedback(
        ui.actionFeedback,
        `动作 ${action} 已写入任务版本 ${payload.task.version}。`,
        "success",
      );
      await selectTask(task.job_id, { silent: true });
      globalThis.setTimeout(() => setButtonState(button, null), 1_000);
    } catch (error) {
      setButtonState(button, "error", "写入失败");
      setFeedback(ui.actionFeedback, errorMessage(error), "error");
      if (error instanceof ApiError && error.status === 409) {
        await selectTask(task.job_id, { silent: true });
      }
      reportFailure("人工控制动作未生效。", error, () => performAction(action, button));
      globalThis.setTimeout(() => setButtonState(button, null), 1_500);
    } finally {
      renderActionAvailability();
    }
  }

  function buildPaletteItems() {
    const taskItems = [...state.tasks]
      .sort((left, right) => right.job_id - left.job_id)
      .map((task) => ({
        label: taskLabel(task),
        meta: task.task_id || `job:${task.job_id}`,
        keywords: `${taskLabel(task)} ${task.task_id || ""} ${task.repo || ""}`.toLowerCase(),
        run: () => selectTask(task.job_id),
      }));
    const channelItems = CHANNELS.slice(1).map((channel) => ({
      label: channel.label,
      meta: "频道",
      keywords: `${channel.label} ${channel.title} ${channel.description}`.toLowerCase(),
      run: () => setChannel(channel.id),
    }));
    state.paletteItems = [...taskItems, ...channelItems];
  }

  function filteredPaletteItems() {
    const query = ui.paletteSearch.value.trim().toLowerCase();
    if (!query) {
      return state.paletteItems;
    }
    return state.paletteItems.filter((item) => item.keywords.includes(query));
  }

  function renderPalette() {
    const items = filteredPaletteItems();
    state.paletteIndex = Math.max(0, Math.min(state.paletteIndex, items.length - 1));
    clear(ui.paletteResults);
    if (!items.length) {
      ui.paletteResults.append(
        element("p", "palette-empty", "没有匹配的真实任务或频道。"),
      );
      return;
    }
    items.forEach((item, index) => {
      const button = element("button", "palette-result");
      button.type = "button";
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", index === state.paletteIndex ? "true" : "false");
      button.append(element("span", null, item.label), element("span", null, item.meta));
      button.addEventListener("click", () => {
        ui.commandPalette.close();
        item.run();
      });
      ui.paletteResults.append(button);
    });
  }

  function openPalette() {
    buildPaletteItems();
    state.paletteIndex = 0;
    ui.paletteSearch.value = "";
    renderPalette();
    ui.commandPalette.showModal();
    ui.workbench.inert = true;
    ui.paletteSearch.focus();
  }

  function runPaletteSelection() {
    const items = filteredPaletteItems();
    const item = items[state.paletteIndex];
    if (!item) {
      return;
    }
    ui.commandPalette.close();
    item.run();
  }

  function setupPalette() {
    ui.searchTrigger.addEventListener("click", openPalette);
    ui.commandPalette.addEventListener("close", () => {
      ui.workbench.inert = false;
      ui.searchTrigger.focus();
    });
    ui.commandPalette.addEventListener("click", (event) => {
      if (event.target === ui.commandPalette) {
        ui.commandPalette.close();
      }
    });
    ui.paletteSearch.addEventListener("input", () => {
      state.paletteIndex = 0;
      renderPalette();
    });
    ui.paletteSearch.addEventListener("keydown", (event) => {
      const items = filteredPaletteItems();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        state.paletteIndex = Math.min(state.paletteIndex + 1, Math.max(items.length - 1, 0));
        renderPalette();
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        state.paletteIndex = Math.max(state.paletteIndex - 1, 0);
        renderPalette();
      } else if (event.key === "Enter") {
        event.preventDefault();
        runPaletteSelection();
      }
    });
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (ui.commandPalette.open) {
          ui.commandPalette.close();
        } else {
          openPalette();
        }
      }
    });
  }

  function setupActions() {
    for (const button of ui.operatorActions.querySelectorAll("[data-action]")) {
      button.addEventListener("click", () => performAction(button.dataset.action, button));
    }
    ui.actionRequirements.addEventListener("input", () => {
      ui.actionRequirements.removeAttribute("aria-invalid");
    });
  }

  function setupStaticInteractions() {
    ui.commandForm.addEventListener("submit", submitTask);
    ui.focusCommand.addEventListener("click", () => {
      ui.commandForm.scrollIntoView({ behavior: "smooth", block: "center" });
      document.querySelector("#goal").focus({ preventScroll: true });
    });
    ui.refreshEvents.addEventListener("click", () => {
      if (state.selectedTask) {
        selectTask(state.selectedTask.job_id);
      }
    });
    document.addEventListener("visibilitychange", scheduleRefresh);
    setupActions();
    setupPalette();
  }

  async function initialize() {
    setServiceTruth("connecting", "正在连接 Control Plane");
    renderChannels();
    setupStaticInteractions();
    try {
      await bootstrapSession();
      await refreshData({ silent: true });
    } catch (error) {
      state.serviceOnline = false;
      state.clientCached = false;
      setServiceTruth("offline", "Control Plane 连接中断");
      renderConnectionTruth();
      reportFailure("工作台没有建立本地身份会话。", error, initialize);
      scheduleRefresh();
    }
  }

  initialize();
})();
