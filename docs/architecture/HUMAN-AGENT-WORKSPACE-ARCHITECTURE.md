# HUMAN-AGENT-WORKSPACE-ARCHITECTURE.md

- **日期 (Date):** 2026-07-26
- **场景 (Scenario):** 架构设计 (Architecture Design)
- **STATUS:** `COLLABORATION_V1_ARCHITECTURE_DESIGNED`
- **关联分支 (Branch):** `design/buzz-human-agent-workspace-v1` （基于 `d4-delivery-layer` @ `8806010`）
- **复用基线 (Reuse Baseline):** 0 REPLACE；21 纯 KEEP + 2 可选 INTEGRATE(#6 沙箱→`buzz-dev-mcp`, #7 Agent→`buzz-agent` ACP) + 1 延后 INTEGRATE(#22 部署→Openship scoped-MCP，MVP-0 后)；Buzz 主干 (Nostr/Postgres/任务队列/Workflow/Audit Hash Chain/Issue/Merge Coordinator/自动审批) 永久不采纳为 Hermes 权威事实源；Hermes Learning OS Activation Gate 6 前置。

---

## 0. 文档目的与权威声明

本文档定义 Hermes Open SWE Lab 的 **Human-Agent 协作工作空间 (V1)** 顶层系统分层，以及 8 项已确认 V1 能力到分层的映射。本文件是后续 4 份架构文档 (`ROLE-AND-PERMISSION-MODEL.md`、`ACTIVITY-AND-INTERVENTION-EVENT-MODEL.md`、`BUZZ-INTEGRATION-BOUNDARY.md`、`COLLABORATION-V1-IMPLEMENTATION-ROADMAP.md`) 的总纲，**所有标识符必须与本文及它们完全一致**。

**单一事实源原则 (Single Source of Truth):** 与本仓库 `AGENTS.md` 第 4、8 节一致 ——
GitHub 是 Issue / Commit / Branch / Draft PR / CI / Review 的**唯一权威事实源**；系统**不得创建第二个任务真相或第二身份事实源**。Buzz、Canvas、Activity Feed、统一搜索都是**协作展示/聚合层**，其权威状态必须可追溯到 GitHub 或 Hermes 控制层状态存储。

**本阶段边界 (Scope Boundary):**
- 仅做架构设计。**不修改任何运行时代码** (`.py`)、不提交、不推送、不建 PR。
- 不开发 Hermes Learning OS (HLO)。
- 不安装 / 运行 / Fork Buzz。
- 不接入 Openship。

---

## 1. 系统分层 (System Layers)

V1 工作空间由 6 个逻辑层组成。每一层都有明确的职责边界与"必须不做什么"。

### 1.1 `Buzz Workspace` —— 人机交互与协作展示层

**职责 ( owns ):**
- 人机交互界面（人类与多个 Agent 共用工作空间）
- 频道 (`channels`)：默认频道 `#control-room #planning #implementation #review #qa #release #user-action-required`，临时频道 `#task-<task-id>`
- 角色展示 (`role display`)：以 Buzz Persona / Team 形式展示 Hermes 逻辑角色，但**不持有权威角色定义**
- `Agent Activity Feed` 展示（事件订阅与渲染，见 `ACTIVITY-AND-INTERVENTION-EVENT-MODEL.md`）
- `Canvas` 共享动态文档展示（协作展示层，非唯一证据源）
- 统一搜索与项目协作记忆的**聚合展示**入口
- 通知 (`notifications`)：授权请求、CI 结果、人工介入提醒

**必须不做 ( must NOT ):**
- 不得作为任务状态、身份、审计的权威事实源
- 不得执行 Git 托管、合并协调、自动审批
- 不得持有或转发长期凭据
- 不得在本阶段运行真实 Buzz 后端（仅做接口/边界设计，主理人后续接入）

### 1.2 `Hermes Engineering Controller` —— 工程控制层（权威大脑）

**职责 ( owns ):**
- `Hermes Master / Boss` 顶层决策（对应角色 `Hermes Master / Boss`）
- `Planner / Scheduler` 调度（对应角色 `Planner / Scheduler`，与 `constants.py` 的 `ROLE_SCHEDULER` 对齐）
- 权限 (`permissions`) 与授权门禁 (`authorization gates`)
- 状态机 (`state machine`)：任务生命周期、返工循环
- `Independent Reviewer` 循环（对应角色 `Independent Reviewer`，与 `constants.py` 的 `ROLE_REVIEWER` 对齐；`MAX_ROUNDS=2`、`ROUND2_LABEL="round-2"`）
- 返工 (`rework`) 编排
- 证据门禁 (`evidence gates`)：每次 PASS 必须可追溯到 GitHub 记录/SHA/check-run
- 人工介入中心 (`intervention center`)：创建并绑定 `USER_ACTION_REQUIRED` 请求（见 `ACTIVITY-AND-INTERVENTION-EVENT-MODEL.md`）

**必须不做 ( must NOT ):**
- 不得绕过 `delivery.py` 的 fail-closed 授权、Draft PR 强制、永不 merge、幂等纪律
- 不得让 Buzz Persona 数据库成为第二身份事实源
- 不得在未获 `Human Owner` 显式授权的情况下执行保护操作

### 1.3 `GitHub` —— 唯一权威事实源

**职责 ( owns ):**
- Issue / Commit / Branch / Draft PR / CI (Checks) / Review 的**唯一权威记录**
- 受控交付层 (`delivery.py`) 的全部 git push 与 Draft PR 创建都落到此层
- `ALLOWED_GITHUB_REPOS = {"yzhlx/hermes-open-swe-smoke-test"}` 是唯一允许自动化写入的仓库
- `PROTECTED_REPOS = {"yzhlx/hermes-learning-os"}` 永远不可被自动化访问/写入

**必须不做 ( must NOT ):**
- 不得出现第二个与 GitHub 冲突的任务真相
- 自动化**永不 merge**（详见 `delivery.py` 的 `NO merge method` 纪律）
- 不得向 `main` 直接推送、`force-push`、删除保护分支

### 1.4 `Open SWE / ACP Agent` —— 可替换执行器

**职责 ( owns ):**
- 实际代码修改、测试、提交等执行工作（对应角色 `Coding Worker`，与 `constants.py` 的 `ROLE_CODING_AGENT` 对齐）
- 通过统一的 `AgentRuntimeAdapter` 接口接入，第一版运行时为 `Open SWE`；预留候选 `Codex`、`Claude Code`、`buzz-agent`、其他 ACP 兼容 Agent（接口见 `COLLABORATION-V1-IMPLEMENTATION-ROADMAP.md`）

**必须不做 ( must NOT ):**
- 不得依赖单一 Agent 私有格式（Hermes 控制层通过 ACP 接口解耦）
- 不得自行持有 GitHub 凭据（令牌仅在 push 步骤由控制层注入）
- 不得绕过 Hermes 权限门禁（受限 MCP Workspace Tools 见文档 5）

### 1.5 `Openship` —— 未来部署执行层（本阶段不接入）

**职责 ( owns ):**
- 计划中的部署执行层；仅作为未来 `#22 INTEGRATE`（Openship scoped-MCP）的目标

**必须不做 ( must NOT )：**
- **V1 不接入**，不安装、不调用、不配置运行时
- 不得在本阶段把 Openship 当作状态机或事实源

### 1.6 `Human Owner` —— 最终授权与验收者

**职责 ( owns ):**
- 最终授权 (`final authorization`) 与验收 (`final acceptance`)
- 处理 `USER_ACTION_REQUIRED` 中的 `AUTHORIZATION` / `SECRET_ENTRY` / `PAYMENT` / `SECURITY_INCIDENT` / `ARCHITECTURE_DECISION` / `EXTERNAL_ACCOUNT_SETUP` / `FINAL_ACCEPTANCE` / `PRODUCTION_RELEASE` 类请求
- 唯一拥有 merge 决策权的人类角色（见 `AGENTS.md` §14、§15）

**必须不做 ( must NOT ):**
- 系统不得代替 `Human Owner` 做出最终合并/生产发布决策

---

## 2. 数据流向 (Data Flow Overview)

```text
Human Owner ──授权/验收──▶ Hermes Engineering Controller
                                │ (Hermes Master / Boss / Planner / Scheduler / Independent Reviewer / Rework / Evidence Gate)
                                │
            ┌───────────────────┼───────────────────────────┐
            ▼                   ▼                            ▼
   Buzz Workspace          Open SWE / ACP Agent        GitHub (权威事实源)
   (展示/交互/通知)          (可替换执行器)              (Issue/Commit/DraftPR/CI/Review)
            │                   │                            ▲
            │   Activity Feed   │   ACP Runtime Adapter      │ delivery.py
            │   展示/搜索聚合   │   (start_session/           │ (fail-closed,
            │                   │    submit_task/...)        │  Draft PR 强制,
            │                   ▼                            │  永不 merge,
            │           受限 MCP Workspace Tools             │  幂等)
            │           (workspace_* 工具, 角色能力过滤)      │
            └─────────── 通知/人工介入中心 ◀──────────────────┘
                              (USER_ACTION_REQUIRED)

   Openship ──(V1 不接入)──▶ 未来部署执行层 (#22 INTEGRATE, MVP-0 后)
```

---

## 3. 8 项 V1 能力到分层的映射 (Capability → Layer Mapping)

| # | V1 能力 | 主负责层 | 协作层 | 说明 |
|---|---------|---------|--------|------|
| 1 | 人类与多个 Agent 共用工作空间 | `Buzz Workspace` + `Hermes Engineering Controller` | `Human Owner` | 人类与 Agent 在同一频道/Canvas 协作；权威角色定义仍在 Hermes 控制层 |
| 2 | Agent Persona 与 Agent Team 角色分工 | `Hermes Engineering Controller`（权威定义） | `Buzz Workspace`（仅展示/运行容器） | Persona/Team 是展示与运行容器；`ROLE-AND-PERMISSION-MODEL.md` 定义权威 14 字段（见其 §2 字段规范 #1–#14） |
| 3 | `Agent Activity Feed` | `Hermes Engineering Controller`（事件生产/存储） | `Buzz Workspace`（展示） | 21 类事件模型见 `ACTIVITY-AND-INTERVENTION-EVENT-MODEL.md` |
| 4 | 人工介入与授权中心 | `Hermes Engineering Controller`（控制） | `Buzz Workspace`（显示/通知） | `USER_ACTION_REQUIRED` 模型；GitHub/Hermes 为权威 |
| 5 | `Canvas` 共享动态文档 | `Hermes Engineering Controller`（内容权威/可重建） | `Buzz Workspace`（展示） | Canvas 12 字段模板见路线文档；不得为唯一证据源 |
| 6 | 统一搜索与项目协作记忆 | `Hermes Engineering Controller`（索引/优先级） | `Buzz Workspace`（聚合展示） | 证据优先级：`GitHub 真实记录 > Hermes 结构化状态 > Buzz 协作记录 > Agent 自然语言汇报` |
| 7 | `ACP Agent Runtime Adapter` | `Hermes Engineering Controller` + `Open SWE / ACP Agent` | — | 接口 `AgentRuntimeAdapter`（`start_session()` 等 8 方法） |
| 8 | 受限 `MCP Workspace Tools` | `Hermes Engineering Controller`（能力过滤/门禁） | `Buzz Workspace`（调用入口） | 9 个 `workspace_*` 工具，按角色过滤，禁止默认提供高危能力 |

---

## 4. 飞书边界 (Feishu Boundary)

本阶段对原计划的飞书集成做出明确切分，避免概念混淆：

1. **开发协作层 (Developmental Collaboration Layer):** Buzz **取代**原计划的"飞书开发协作入口"。即：人类与 Agent 的开发协作（频道、角色展示、Activity Feed、Canvas、搜索、通知）统一走 `Buzz Workspace`，不再依赖飞书作为开发协作通道。
2. **Learning OS 产品层 (Product Layer):** Hermes Learning OS 自身产品如保留"飞书学习消息入口"作为学习触达通道，属于**未来独立产品决策**。本轮**不删、不改**现有飞书学习入口设计；是否保留由未来产品决策裁定。
3. **概念隔离 (Concept Isolation):** 严格区分两个命题 ——
   - "**开发 Hermes**"（本仓库的工程自动化研发活动，走 Buzz 协作层）
   - "**使用 Hermes Learning OS**"（终端用户使用学习产品，可能涉及飞书学习入口）
   
   二者不得混淆；本架构仅覆盖前者。Hermes Learning OS 当前**未激活**（Activation Gate 6 前置），且属于 `PROTECTED_REPOS`，不得在本阶段访问或开发。

---

## 5. 与既有安全边界的一致性 (Consistency with AGENTS.md / constants.py / delivery.py)

- **仓库范围:** 仅 `yzhlx/hermes-open-swe-smoke-test` 可自动化写入；`yzhlx/hermes-learning-os` 永久保护 (`PROTECTED_REPOS`)。
- **交付纪律:** 所有交付经 `delivery.py` 的 `DeliveryController.deliver()`：显式 `DeliveryAuthorization`（task+repo+commit 绑定、fail-closed）、Draft PR 强制 (`draft=True`，无法被绕过)、永不 merge（无 merge 方法）、幂等 (`idempotency_key`)。
- **状态机:** 返工循环遵循 `MAX_ROUNDS=2` 与 `ROUND2_LABEL="round-2"`（仅 Scheduler 可加标签），与 `constants.py` 一致。
- **凭据隔离:** 控制器/控制层**从不持有长期令牌**；令牌仅在 push 步骤局部注入并丢弃（`delivery.py` 纪律）。Buzz 不持有或转发凭据。
- **无第二事实源:** Buzz/Canvas/搜索均不得产生与 GitHub 冲突的任务真相；所有权威结论必须可同步回 GitHub Issue/PR/Review/文档。

---

## 6. 文档间标识符一致性清单 (Cross-Doc Identifier Contract)

以下标识符在全部 5 份文档中**完全一致**，任何修改须同步：

- **分层:** `Buzz Workspace` / `Hermes Engineering Controller` / `GitHub` / `Open SWE / ACP Agent` / `Openship` / `Human Owner`
- **角色 (8):** `Human Owner`、`Hermes Master / Boss`、`Planner / Scheduler`、`Coding Worker`、`Independent Reviewer`、`QA Agent`、`Documentation Agent`、`Release Agent`
- **频道:** `#control-room #planning #implementation #review #qa #release #user-action-required`，临时 `#task-<task-id>`
- **Activity 事件 (21):** 见 `ACTIVITY-AND-INTERVENTION-EVENT-MODEL.md`
- **USER_ACTION_REQUIRED 原因 (8):** 见 `ACTIVITY-AND-INTERVENTION-EVENT-MODEL.md`
- **Canvas 字段 (12):** 见 `COLLABORATION-V1-IMPLEMENTATION-ROADMAP.md`
- **搜索证据优先级:** `GitHub 真实记录 > Hermes 结构化状态 > Buzz 协作记录 > Agent 自然语言汇报`
- **ACP 接口:** `AgentRuntimeAdapter`(`start_session()`/`submit_task()`/`resume_session()`/`cancel_task()`/`get_activity()`/`request_permission()`/`collect_result()`/`terminate_session()`)；运行时 `Open SWE`、候选 `Codex`/`Claude Code`/`buzz-agent`/其他 ACP 兼容
- **MCP 工具 (9):** `workspace_get_task`、`workspace_get_context`、`workspace_post_update`、`workspace_update_canvas`、`workspace_search`、`workspace_get_pending_actions`、`workspace_request_user_action`、`workspace_post_review_result`、`workspace_get_evidence`
- **Phase:** `Phase A`–`Phase G`
- **STATUS:** `COLLABORATION_V1_ARCHITECTURE_DESIGNED`
