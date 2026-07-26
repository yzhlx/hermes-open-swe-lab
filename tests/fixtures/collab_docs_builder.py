# -*- coding: utf-8 -*-
"""Collaboration V1 文档语义一致性检查器的 fixture 构造器。

本模块生成：
  * build_good_docs()  —— 5 份"全绿"的 synthethic 文档（不含任何真实凭据），
    用于验证检查器在文档合规时全部通过；
  * build_bad_docs(rule_id) —— 在 good 基础上施加一处定向变异，使指定规则失败，
    用于验证检查器能精确捕获该规则的破坏。

所有内容均为合成文本，便于测试确定性；不引用任何真实密钥 / 令牌 / 仓库凭据。
"""

from __future__ import annotations

import sys
import os

# 让测试能 import 被检查的模块
TOOLS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tools"))
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import check_collaboration_docs as cc  # noqa: E402

ROLES = cc.CANONICAL_ROLES
ROLE_FIELDS = cc.CANONICAL_ROLE_FIELDS
EVENTS = cc.CANONICAL_EVENTS
UAR = cc.CANONICAL_UAR_REASONS
DEFAULT_CHANNELS = cc.CANONICAL_DEFAULT_CHANNELS
TEMP_CHANNEL = cc.CANONICAL_TEMP_CHANNEL
CANVAS = cc.CANONICAL_CANVAS_FIELDS
ACP = cc.CANONICAL_ACP_METHODS
MCP = cc.CANONICAL_MCP_TOOLS
PHASES = cc.CANONICAL_PHASES

PROTECTED_REPO = cc.PROTECTED_REPO
ALLOWED_REPO = cc.ALLOWED_REPO


# ---------------------------------------------------------------------------
# 各文档的"好"版本组装
# ---------------------------------------------------------------------------

def _role_overview_table() -> str:
    rows = ["| 角色 (Role) | 类别 | 映射到 constants.py | 主要工作空间 |",
            "| --- | --- | --- | --- |"]
    mapping = {
        "Human Owner": "—",
        "Hermes Master / Boss": "—",
        "Planner / Scheduler": "ROLE_SCHEDULER",
        "Coding Worker": "ROLE_CODING_AGENT",
        "Independent Reviewer": "ROLE_REVIEWER",
        "QA Agent": "—",
        "Documentation Agent": "—",
        "Release Agent": "—",
    }
    ws = {
        "Human Owner": "所有频道",
        "Hermes Master / Boss": "#control-room",
        "Planner / Scheduler": "#planning #control-room",
        "Coding Worker": "#implementation",
        "Independent Reviewer": "#review",
        "QA Agent": "#qa",
        "Documentation Agent": "#planning #implementation",
        "Release Agent": "#release",
    }
    for r in ROLES:
        rows.append(f"| `{r}` | 控制层/执行器 | {mapping[r]} | {ws[r]} |")
    return "\n".join(rows)


def _role_field_spec_table() -> str:
    rows = ["| # | 字段 (Field) | 含义 |", "| --- | --- | --- |"]
    en = ["Unique Responsibility", "Readable Data", "Allowed Tools", "Writable Locations",
          "Forbidden Operations", "Input Contract", "Output Contract", "Start Conditions",
          "Completion Conditions", "Failure & Escalation", "Modify Code?", "Operate GitHub?",
          "Touch Credentials?", "Request Auth?"]
    for i, f in enumerate(ROLE_FIELDS, 1):
        rows.append(f"| {i} | {f} ({en[i-1]}) | 含义说明 |")
    return "\n".join(rows)


def _role_detail_table(role: str) -> str:
    rows = ["| 字段 | 定义 |", "| --- | --- |"]
    special = {
        "Coding Worker": {
            "完成条件": "经 delivery.py 的 DeliveryController.deliver() 完成受控交付（push + Draft PR 创建，唯一负责方），CI 已触发。",
        },
        "Release Agent": {
            "完成条件": "受控交付已编排，Draft PR 生命周期由 delivery.py 管理；Draft PR 的创建唯一由 DeliveryController.deliver() 负责，Release Agent 不得重复声明创建 Draft PR。",
        },
    }
    for f in ROLE_FIELDS:
        if role in special and f in special[role]:
            rows.append(f"| {f} | {special[role][f]} |")
        else:
            rows.append(f"| {f} | {role} 的{f}定义（引用 Hermes Master / Boss 与 Planner / Scheduler 均使用完整名）。 |")
    return "\n".join(rows)


def _role_doc() -> str:
    parts = [
        "# ROLE-AND-PERMISSION-MODEL.md",
        "",
        "## 0. 权威声明",
        "",
        "Hermes Role Definition 是权威角色定义。Buzz Persona 仅展示。不得创建第二身份事实源。",
        "",
        "## 1. 角色总览 (Role Overview)",
        "",
        _role_overview_table(),
        "",
        "## 2. 角色定义字段规范 (Field Spec)",
        "",
        _role_field_spec_table(),
        "",
        "## 3. 角色详细定义 (Detailed Role Definitions)",
        "",
    ]
    for i, r in enumerate(ROLES, 1):
        parts.append(f"### 3.{i} `{r}`")
        parts.append("")
        parts.append(_role_detail_table(r))
        parts.append("")
    parts += [
        "## 4. 权限矩阵速查 (Permission Matrix)",
        "",
        "| 角色 | 修改代码 | 操作 GitHub | 接触凭据 | 请求用户授权 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in ROLES:
        parts.append(f"| `{r}` | 否 | 仅经授权 | 否 | 否 |")
    parts.append("")
    return "\n".join(parts)


def _events_table() -> str:
    rows = ["| # | 事件名 (Event) | 触发时机 | 典型 status | github_refs |",
            "| --- | --- | --- | --- | --- |"]
    triggers = {e: "说明" for e in EVENTS}
    statuses = {e: "success" for e in EVENTS}
    statuses["USER_ACTION_REQUIRED"] = "blocked"
    statuses["TASK_BLOCKED"] = "blocked"
    statuses["CI_PENDING"] = "pending"
    statuses["COMMAND_STARTED"] = "running"
    statuses["REVIEW_STARTED"] = "running"
    statuses["REWORK_STARTED"] = "running"
    refs = {e: "issue" for e in EVENTS}
    for i, e in enumerate(EVENTS, 1):
        desc = "任务达终态（须 FINAL_ACCEPTANCE 完成后方可触发，不得早于最终验收）" if e == "TASK_COMPLETED" \
            else f"{triggers[e]}（{e} 说明）"
        rows.append(f"| {i} | `{e}` | {desc} | {statuses[e]} | {refs[e]} |")
    return "\n".join(rows)


def _uar_table() -> str:
    rows = ["| 原因 (Reason) | 含义 | 典型发起角色 |",
            "| --- | --- | --- |"]
    for r in UAR:
        rows.append(f"| `{r}` | {r} 含义 | Hermes Master / Boss |")
    return "\n".join(rows)


def _activity_doc() -> str:
    return "\n".join([
        "# ACTIVITY-AND-INTERVENTION-EVENT-MODEL.md",
        "",
        "## 0. 权威声明",
        "",
        "事件由 Hermes 控制层生产并存储。Buzz Workspace 仅订阅并展示 Activity Feed，不得作为事件权威源。Activity Feed 是协作展示层，非唯一证据源。",
        "",
        "## 1. Agent Activity Feed 事件模型 (Event Model)",
        "",
        "### 1.2 事件清单 (Event Catalog — 21 events)",
        "",
        _events_table(),
        "",
        "### 1.2.1 统一任务生命周期 (Canonical Task Lifecycle)",
        "",
        "Issue → Plan → Code/Test → Commit → controlled delivery (push + Draft PR via DeliveryController.deliver()) → CI → Independent Review → rework on same PR (若 REQUEST_CHANGES，受 MAX_ROUNDS=2) → FINAL_ACCEPTANCE (Human Owner) → TASK_COMPLETED",
        "",
        "## 2. 人工介入中心 (Human Intervention Center)",
        "",
        "### 2.1 `USER_ACTION_REQUIRED` 原因枚举 (Reason Enum — 8 reasons)",
        "",
        _uar_table(),
        "",
        "## 3. 事件→权威记录映射 (Event → Authority Mapping)",
        "",
        "Activity Feed 是协作展示层；所有权威结论须可同步回 GitHub 或 Hermes 控制层存储。",
        "",
    ])


def _human_doc() -> str:
    channels_line = " ".join(f"`{c}`" for c in DEFAULT_CHANNELS)
    return "\n".join([
        "# HUMAN-AGENT-WORKSPACE-ARCHITECTURE.md",
        "",
        "## 0. 文档目的与权威声明",
        "",
        "本文档为总纲。Buzz、Canvas、Activity Feed 均为协作展示层，不得创建第二事实源。GitHub 是唯一权威事实源。",
        "",
        "## 1. 系统分层 (System Layers)",
        "",
        "### 1.1 `Buzz Workspace`",
        "",
        "频道：默认频道 " + channels_line + "，临时频道 `#task-<task-id>`。",
        "",
        "## 6. 文档间标识符一致性清单 (Cross-Doc Identifier Contract)",
        "",
        "- 角色 (8): " + "、".join(f"`{r}`" for r in ROLES),
        "- 频道: " + channels_line + "，临时 `#task-<task-id>`",
        "",
    ])


def _roadmap_doc() -> str:
    channels_line = " ".join(f"`{c}`" for c in DEFAULT_CHANNELS)
    canvas_line = " / ".join(f"`{c}`" for c in CANVAS)
    acp_rows = ["| 方法 | 职责 |", "| --- | --- |"]
    for m in ACP:
        acp_rows.append(f"| `{m}()` | {m} 职责 |")
    mcp_line = " / ".join(f"`{t}`" for t in MCP)
    # Phase 名称在表格单元格中不得使用 Master/Scheduler/Reviewer 歧义简称，
    # 故 Phase B 使用无歧义表述（C_PHASES 仅校验字母 A-G，不校验名称文本）。
    phase_names = dict(PHASES)
    phase_names["B"] = "Hermes 控制层调度与审查闭环"
    phase_rows = ["| Phase | 名称 | 目标 | 阻塞下一阶段? |", "| --- | --- | --- | --- |"]
    for k in sorted(phase_names.keys()):
        phase_rows.append(f"| `Phase {k}` | {phase_names[k]} | 目标 | 是 |")
    return "\n".join([
        "# COLLABORATION-V1-IMPLEMENTATION-ROADMAP.md",
        "",
        "## 1. Phase 总览 (Phase Overview)",
        "",
        "\n".join(phase_rows),
        "",
        "## 2. Phase 详细设计 (Phase Details)",
        "",
        "### `Phase A` — 工作空间、角色、频道、Canvas、Activity Feed 设计",
        "",
        "目标：完成协作外壳的静态设计。本批 5 份文档（PR #8）即 Phase A 的交付物（协作外壳静态设计）。修改范围：仅新增 docs/architecture/*，不改动 .py。",
        "",
        "### `Phase C` — Buzz 工作空间适配器",
        "",
        "Phase C 不部署真实 Buzz；在进入 Phase G 真实试用前，必须满足 Buzz 接入门禁。",
        "",
        "### Buzz 接入门禁 (BUZZ_ENV_READY)",
        "",
        "1. Buzz 后端已部署或提供可达实例；",
        "2. 账号与 Workspace / 频道 / 连接已建立；",
        "3. 凭据经服务端 .env；",
        "4. 连接与通知链路经冒烟验证。",
        "",
        "### `Phase F` — Smoke Test 真实闭环",
        "",
        "统一生命周期：Issue → Plan → Code/Test → Commit → controlled delivery (push + Draft PR via DeliveryController.deliver()) → CI → Independent Review → rework on same PR → FINAL_ACCEPTANCE (Human Owner) → TASK_COMPLETED。",
        "",
        "### `Phase G` — 用户体验试用和迭代",
        "",
        "前置条件：Phase F 通过，且 Buzz 接入门禁（BUZZ_ENV_READY）通过（真实 Buzz 实例可达、账号 / 连接就绪）。",
        "",
        "## 3. 工作空间设计 (Workspace Design)",
        "",
        "### 3.1 频道拓扑 (Channel Topology)",
        "",
        "默认频道 (Default Channels): " + channels_line,
        "",
        "临时频道 (Temporary Channels): `#task-<task-id>` —— 任务派发时创建，任务 TASK_COMPLETED 且验收后归档。",
        "",
        "### 3.3 生命周期与映射 (Lifecycle & Mapping)",
        "",
        "归档：FINAL_ACCEPTANCE 完成后方可触发 TASK_COMPLETED 并归档；归档前内容须已同步权威记录。",
        "",
        "## 4. Canvas 模板 (Canvas Template)",
        "",
        "Canvas 是协作展示层，12 字段如下（英文原名原样，全文档一致）：",
        "",
        canvas_line,
        "",
        "约束：Canvas 不得成为唯一证据源；Decisions / Acceptance Criteria / Evidence 必须同步到 GitHub。",
        "",
        "## 6. ACP Agent Runtime Adapter 接口 (ACP Agent Runtime Adapter)",
        "",
        "\n".join(acp_rows),
        "",
        "## 7. 受限 MCP Workspace Tools",
        "",
        "工具名（英文原名原样，全文档一致）：",
        "",
        mcp_line,
        "",
        "## 8. 与既有边界的一致性 (Consistency)",
        "",
        f"所有 GitHub 访问受 ALLOWED_GITHUB_REPOS / PROTECTED_REPOS 约束；{PROTECTED_REPO} 永不自动化访问。",
        "",
        "## 9. PR 堆叠与 Rebase 顺序 (Stacked PR Integration Order)",
        "",
        "1. PR #7（审计）先合并进入共同基准 d4-delivery-layer；",
        "2. PR #8（设计）随后 rebase / merge 到更新后的基准再合并；",
        "3. 合并前 PR #8 须 rebase 到 PR #7 合并后的 HEAD。",
        "",
        "ALLOWED_GITHUB_REPOS = {" + ALLOWED_REPO + "}",
        "",
    ])


def _buzz_doc() -> str:
    return "\n".join([
        "# BUZZ-INTEGRATION-BOUNDARY.md",
        "",
        "## 3. Hermes 权威边界 (Hermes Authority Boundary)",
        "",
        f"1. GitHub 唯一权威事实源；ALLOWED_GITHUB_REPOS = {{{ALLOWED_REPO}}}。",
        f"2. 不创建第二任务真相；{PROTECTED_REPO} 属 PROTECTED_REPOS，永不出现于自动化写入。",
        "",
    ])


def build_good_docs() -> dict:
    return {
        cc.DOC_HUMAN: _human_doc(),
        cc.DOC_ROLE: _role_doc(),
        cc.DOC_ACTIVITY: _activity_doc(),
        cc.DOC_BUZZ: _buzz_doc(),
        cc.DOC_ROADMAP: _roadmap_doc(),
    }


# ---------------------------------------------------------------------------
# 定向变异（使指定规则失败）
# ---------------------------------------------------------------------------

def _replace_once(text: str, old: str, new: str) -> str:
    assert old in text, f"mutation anchor not found: {old!r}"
    return text.replace(old, new, 1)


def _mutate(docs: dict, doc_key: str, old: str, new: str) -> dict:
    d = dict(docs)
    d[doc_key] = _replace_once(d[doc_key], old, new)
    return d


_MUTATORS = {}


def _m(rule_id):
    def deco(fn):
        _MUTATORS[rule_id] = fn
        return fn
    return deco


@_m("C_ROLES_COUNT")
def _bad_roles_count(docs):
    # 从角色总览表删除一个角色行
    role_doc = docs[cc.DOC_ROLE]
    line = "| `Coding Worker` | 控制层/执行器 | ROLE_CODING_AGENT | #implementation |"
    assert line in role_doc
    return _mutate(docs, cc.DOC_ROLE, line, "")


@_m("C_ROLES_CONSISTENCY")
def _bad_roles_consistency(docs):
    # 从 HUMAN §6 角色清单里删除一个权威角色，制造跨文档不一致
    joined = "、".join(f"`{r}`" for r in ROLES)
    old = "- 角色 (8): " + joined
    drop = "`Documentation Agent`、"
    assert drop in old, f"anchor not found: {drop!r}"
    new = old.replace(drop, "")
    return _mutate(docs, cc.DOC_HUMAN, old, new)


@_m("C_ROLE_FIELDS_COUNT")
def _bad_role_fields_count(docs):
    # §2 字段表删除一个字段行
    line = "| 14 | 是否允许请求用户授权 (Request Auth?) | 含义说明 |"
    return _mutate(docs, cc.DOC_ROLE, line, "")


@_m("C_ROLE_FIELDS_PER_ROLE")
def _bad_role_fields_per_role(docs):
    # 给 Coding Worker 详细表删掉一个字段行
    line = "| 是否允许请求用户授权 | Coding Worker 的是否允许请求用户授权定义（引用 Hermes Master / Boss 与 Planner / Scheduler 均使用完整名）。 |"
    return _mutate(docs, cc.DOC_ROLE, line, "")


@_m("C_EVENTS_COUNT")
def _bad_events_count(docs):
    line = "| 21 | `TASK_COMPLETED` | 任务达终态（须 FINAL_ACCEPTANCE 完成后方可触发，不得早于最终验收） | success | issue |"
    return _mutate(docs, cc.DOC_ACTIVITY, line, "")


@_m("C_EVENTS_FORBIDDEN")
def _bad_events_forbidden(docs):
    # 在事件目录插入一个目录外事件
    anchor = "| 21 | `TASK_COMPLETED`"
    insert = "| 22 | `TEST_PASSED` | 禁止的目录外事件 | success | check |"
    role_doc = docs[cc.DOC_ACTIVITY]
    assert anchor in role_doc
    return _mutate(docs, cc.DOC_ACTIVITY, anchor, insert + "\n" + anchor)


@_m("C_UAR_COUNT")
def _bad_uar_count(docs):
    line = "| `PRODUCTION_RELEASE` | PRODUCTION_RELEASE 含义 | Hermes Master / Boss |"
    return _mutate(docs, cc.DOC_ACTIVITY, line, "")


@_m("C_CHANNELS")
def _bad_channels(docs):
    old = "默认频道 (Default Channels): " + " ".join(f"`{c}`" for c in DEFAULT_CHANNELS)
    new = old.replace("`#qa`", "")
    return _mutate(docs, cc.DOC_ROADMAP, old, new)


@_m("C_CHANNELS_CONSISTENCY")
def _bad_channels_consistency(docs):
    old = "频道: " + " ".join(f"`{c}`" for c in DEFAULT_CHANNELS) + "，临时 `#task-<task-id>`"
    new = old.replace("`#qa`", "")
    return _mutate(docs, cc.DOC_HUMAN, old, new)


@_m("C_CANVAS")
def _bad_canvas(docs):
    # 枚举行删除一个字段
    old = " / ".join(f"`{c}`" for c in CANVAS)
    new = " / ".join(f"`{c}`" for c in CANVAS[:-1])
    return _mutate(docs, cc.DOC_ROADMAP, old, new)


@_m("C_ACP")
def _bad_acp(docs):
    line = "| `terminate_session()` | terminate_session 职责 |"
    return _mutate(docs, cc.DOC_ROADMAP, line, "")


@_m("C_MCP")
def _bad_mcp(docs):
    old = " / ".join(f"`{t}`" for t in MCP)
    new = " / ".join(f"`{t}`" for t in MCP[:-1])
    return _mutate(docs, cc.DOC_ROADMAP, old, new)


@_m("C_PHASES")
def _bad_phases(docs):
    line = "| `Phase G` | " + PHASES["G"] + " | 目标 | 是 |"
    return _mutate(docs, cc.DOC_ROADMAP, line, "")


@_m("S_PHASE_A_DEF")
def _bad_phase_a_def(docs):
    old = "本批 5 份文档（PR #8）即 Phase A 的交付物（协作外壳静态设计）。修改范围：仅新增 docs/architecture/*，不改动 .py。"
    new = "本阶段实现运行时代码。修改范围：新增 docs/architecture/* 并实现运行时代码。"
    return _mutate(docs, cc.DOC_ROADMAP, old, new)


@_m("S_DRAFT_PR_RESP")
def _bad_draft_pr_resp(docs):
    old = "Draft PR 的创建唯一由 DeliveryController.deliver() 负责，Release Agent 不得重复声明创建 Draft PR。"
    new = "Draft PR 的创建由 Release Agent 负责创建，Release Agent 重复声明创建 Draft PR。"
    return _mutate(docs, cc.DOC_ROLE, old, new)


@_m("S_LIFECYCLE_ORDER")
def _bad_lifecycle(docs):
    old = "Issue → Plan → Code/Test → Commit → controlled delivery (push + Draft PR via DeliveryController.deliver()) → CI → Independent Review → rework on same PR (若 REQUEST_CHANGES，受 MAX_ROUNDS=2) → FINAL_ACCEPTANCE (Human Owner) → TASK_COMPLETED"
    new = old.replace(
        "controlled delivery (push + Draft PR via DeliveryController.deliver()) → CI → Independent Review",
        "Independent Review → controlled delivery (push + Draft PR via DeliveryController.deliver()) → CI",
    )
    return _mutate(docs, cc.DOC_ACTIVITY, old, new)


@_m("S_REVIEW_BEFORE_PR")
def _bad_review_before_pr(docs):
    # 与生命周期重排同源：Review 被移到 Draft PR 之前
    return _bad_lifecycle(docs)


@_m("S_TASK_COMPLETED_STATUS")
def _bad_task_completed_status(docs):
    old = "任务达终态（须 FINAL_ACCEPTANCE 完成后方可触发，不得早于最终验收）"
    new = "任务达终态（待最终验收）"
    return _mutate(docs, cc.DOC_ACTIVITY, old, new)


@_m("S_FINAL_ACCEPTANCE_BLOCKED")
def _bad_final_acceptance_blocked(docs):
    old = "| 19 | `USER_ACTION_REQUIRED` | 说明（USER_ACTION_REQUIRED 说明） | blocked | issue |"
    new = old.replace("blocked", "success")
    return _mutate(docs, cc.DOC_ACTIVITY, old, new)


@_m("S_TEMP_CHANNEL_ARCHIVE")
def _bad_temp_channel_archive(docs):
    # 破坏两处绑定终验的归档声明（§3.1 频道拓扑 + §3.3 生命周期）
    d = dict(docs)
    rm = d[cc.DOC_ROADMAP]
    a = "任务 TASK_COMPLETED 且验收后归档"
    b = "FINAL_ACCEPTANCE 完成后方可触发 TASK_COMPLETED 并归档"
    assert a in rm and b in rm
    rm = rm.replace(a, "任务结束即可归档").replace(b, "任务结束后即可归档")
    d[cc.DOC_ROADMAP] = rm
    return d


@_m("S_BUZZ_GATE")
def _bad_buzz_gate(docs):
    old = "1. Buzz 后端已部署或提供可达实例；\n2. 账号与 Workspace / 频道 / 连接已建立；\n3. 凭据经服务端 .env；\n4. 连接与通知链路经冒烟验证。"
    new = "1. 凭据经服务端 .env；"
    return _mutate(docs, cc.DOC_ROADMAP, old, new)


@_m("S_PR_ORDER")
def _bad_pr_order(docs):
    old = "1. PR #7（审计）先合并进入共同基准 d4-delivery-layer；\n2. PR #8（设计）随后 rebase / merge 到更新后的基准再合并；\n3. 合并前 PR #8 须 rebase 到 PR #7 合并后的 HEAD。"
    new = "1. PR #7 与 PR #8 并行合并；"
    return _mutate(docs, cc.DOC_ROADMAP, old, new)


@_m("S_TABLE_ROLE_ABBREV")
def _bad_table_role_abbrev(docs):
    # 在角色总览表里插入一个歧义简称
    old = "| `Release Agent` | 控制层/执行器 | — | #release |"
    new = "| `Release Agent` | 控制层/交付 | — | #release（由 Master 监督） |"
    return _mutate(docs, cc.DOC_ROLE, old, new)


@_m("S_NO_SECOND_TRUTH_BUZZ")
def _bad_no_second_truth_buzz(docs):
    old = "本文档为总纲。Buzz、Canvas、Activity Feed 均为协作展示层，不得创建第二事实源。GitHub 是唯一权威事实源。"
    new = "本文档为总纲。GitHub 是唯一权威事实源。"
    return _mutate(docs, cc.DOC_HUMAN, old, new)


@_m("S_NO_SECOND_TRUTH_CANVAS")
def _bad_no_second_truth_canvas(docs):
    old = "约束：Canvas 不得成为唯一证据源；Decisions / Acceptance Criteria / Evidence 必须同步到 GitHub。"
    new = "约束：Decisions / Acceptance Criteria / Evidence 必须同步到 GitHub。"
    return _mutate(docs, cc.DOC_ROADMAP, old, new)


@_m("S_NO_SECOND_TRUTH_ACTIVITY")
def _bad_no_second_truth_activity(docs):
    old = "事件由 Hermes 控制层生产并存储。Buzz Workspace 仅订阅并展示 Activity Feed，不得作为事件权威源。Activity Feed 是协作展示层，非唯一证据源。"
    new = "事件由 Hermes 控制层生产并存储。Buzz Workspace 仅订阅并展示 Activity Feed。"
    return _mutate(docs, cc.DOC_ACTIVITY, old, new)


@_m("S_PROTECTED_REPO")
def _bad_protected_repo(docs):
    old = "ALLOWED_GITHUB_REPOS = {" + ALLOWED_REPO + "}"
    new = "ALLOWED_GITHUB_REPOS = {" + ALLOWED_REPO + ", " + PROTECTED_REPO + "}"
    return _mutate(docs, cc.DOC_ROADMAP, old, new)


def build_bad_docs(rule_id: str) -> dict:
    if rule_id not in _MUTATORS:
        raise KeyError(f"no mutator for rule {rule_id}")
    return _MUTATORS[rule_id](build_good_docs())


# 全部需要覆盖的规则
RULE_IDS = [
    "C_ROLES_COUNT", "C_ROLES_CONSISTENCY", "C_ROLE_FIELDS_COUNT", "C_ROLE_FIELDS_PER_ROLE",
    "C_EVENTS_COUNT", "C_EVENTS_FORBIDDEN", "C_UAR_COUNT", "C_CHANNELS", "C_CHANNELS_CONSISTENCY",
    "C_CANVAS", "C_ACP", "C_MCP", "C_PHASES",
    "S_PHASE_A_DEF", "S_DRAFT_PR_RESP", "S_LIFECYCLE_ORDER", "S_REVIEW_BEFORE_PR",
    "S_TASK_COMPLETED_STATUS", "S_FINAL_ACCEPTANCE_BLOCKED", "S_TEMP_CHANNEL_ARCHIVE",
    "S_BUZZ_GATE", "S_PR_ORDER", "S_TABLE_ROLE_ABBREV",
    "S_NO_SECOND_TRUTH_BUZZ", "S_NO_SECOND_TRUTH_CANVAS", "S_NO_SECOND_TRUTH_ACTIVITY",
    "S_PROTECTED_REPO",
]


if __name__ == "__main__":
    findings = cc.run_checks(build_good_docs())
    s = cc.summarize(findings)
    print("good docs:", s["passed"], "passed /", s["failed"], "failed")
    for f in findings:
        if not f.ok:
            print("  FAIL:", f.rule_id, f.found)
