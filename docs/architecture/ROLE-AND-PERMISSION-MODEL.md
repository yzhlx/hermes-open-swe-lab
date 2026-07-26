# ROLE-AND-PERMISSION-MODEL.md

- **日期 (Date):** 2026-07-26
- **场景 (Scenario):** 架构设计 (Architecture Design)
- **STATUS:** `COLLABORATION_V1_ARCHITECTURE_DESIGNED`
- **关联文档:** `HUMAN-AGENT-WORKSPACE-ARCHITECTURE.md`、`ACTIVITY-AND-INTERVENTION-EVENT-MODEL.md`、`BUZZ-INTEGRATION-BOUNDARY.md`、`COLLABORATION-V1-IMPLEMENTATION-ROADMAP.md`

---

## 0. 权威声明 (Authority Statement)

1. **Hermes Role Definition 是权威角色定义。** 本文档定义的逻辑角色 (`Human Owner`、`Hermes Master / Boss`、`Planner / Scheduler`、`Coding Worker`、`Independent Reviewer`、`QA Agent`、`Documentation Agent`、`Release Agent`) 是系统内**唯一权威身份事实源**。
2. **Buzz Persona / Team 仅作为角色展示与运行容器。** 它们从 Hermes 控制层拉取权威角色定义进行展示与运行，**不得**在 Buzz Persona 数据库中存储或派生权威身份属性。
3. **不得创建第二身份事实源。** 任何层（Buzz、Canvas、搜索、Activity Feed）都不得定义或覆盖角色权限；如需调整权限，必须回到本文档（并同步 `constants.py` 的角色常量）。
4. 与 `constants.py` 的对齐：
   - `Coding Worker` ↔ `ROLE_CODING_AGENT`
   - `Independent Reviewer` ↔ `ROLE_REVIEWER`
   - `Planner / Scheduler` ↔ `ROLE_SCHEDULER`
   - 其余 5 个角色为 V1 新增逻辑角色，后续如需代码常量化，应在 `constants.py` 中登记，不得另起一套身份表。

---

## 1. 角色总览 (Role Overview)

| 角色 (Role) | 类别 | 映射到 constants.py | 主要工作空间 |
| --- | --- | --- | --- |
| `Human Owner` | 人类 | — | 所有频道（只读/决策/授权） |
| `Hermes Master / Boss` | 控制层 | — | `#control-room` |
| `Planner / Scheduler` | 控制层 | `ROLE_SCHEDULER` | `#planning` `#control-room` |
| `Coding Worker` | 执行器 | `ROLE_CODING_AGENT` | `#implementation` `#task-<task-id>` |
| `Independent Reviewer` | 控制层/审查 | `ROLE_REVIEWER` | `#review` |
| `QA Agent` | 执行器 | — | `#qa` |
| `Documentation Agent` | 执行器 | — | `#planning` `#implementation` |
| `Release Agent` | 控制层/交付 | — | `#release` |

> **频道归属细节**见 `COLLABORATION-V1-IMPLEMENTATION-ROADMAP.md` 的"工作空间设计"章节（哪些角色可进入、可发言、只读、任务频道何时创建/归档）。

---

## 2. 角色定义字段规范 (Field Spec)

每个角色定义以下字段（与 `HUMAN-AGENT-WORKSPACE-ARCHITECTURE.md` §6 标识符一致）。布尔类字段统一取值：`是` / `否` / `仅经授权`(only-with-authorization)。

| # | 字段 (Field) | 含义 |
|---| --- | --- |
| 1 | 唯一职责 (Unique Responsibility) | 该角色不可被其他角色替代的核心职责 |
| 2 | 允许读取的数据 (Readable Data) | 可访问的数据源与最小必要范围 |
| 3 | 允许调用的工具 (Allowed Tools) | 可调用工具 / 接口 / MCP 工具 |
| 4 | 允许写入的位置 (Writable Locations) | 可写目标（必须落在允许仓库范围内） |
| 5 | 禁止操作 (Forbidden Operations) | 明确红线 |
| 6 | 输入契约 (Input Contract) | 启动所需的输入与契约格式 |
| 7 | 输出契约 (Output Contract) | 输出格式与必须由谁消费 |
| 8 | 任务开始条件 (Start Conditions) | 何时被唤醒/启动 |
| 9 | 完成条件 (Completion Conditions) | 何种状态视为完成 |
| 10 | 失败和升级路径 (Failure & Escalation) | 失败态、重试上限、升级到谁 |
| 11 | 是否允许修改代码 (Modify Code?) | 能否改动仓库源码 |
| 12 | 是否允许操作 GitHub (Operate GitHub?) | 能否直接调用 GitHub API |
| 13 | 是否允许接触凭据 (Touch Credentials?) | 能否接触任何凭据/令牌 |
| 14 | 是否允许请求用户授权 (Request Auth?) | 能否发起 `USER_ACTION_REQUIRED` |

---

## 3. 角色详细定义 (Detailed Role Definitions)

### 3.1 `Human Owner`

| 字段 | 定义 |
| --- | --- |
| 唯一职责 | 最终授权与验收；对 `AUTHORIZATION`/`SECRET_ENTRY`/`PAYMENT`/`SECURITY_INCIDENT`/`ARCHITECTURE_DECISION`/`EXTERNAL_ACCOUNT_SETUP`/`FINAL_ACCEPTANCE`/`PRODUCTION_RELEASE` 类请求做决策；唯一拥有 merge / 生产发布决策权的人类。 |
| 允许读取的数据 | 全部工作空间内容、GitHub Issue/PR/Review/CI、Canvas、Activity Feed、审计与运行报告。 |
| 允许调用的工具 | Buzz Workspace 交互界面；GitHub UI / `gh`；人工授权与验收操作。 |
| 允许写入的位置 | GitHub（通过 UI/gh 手动 merge 与批准）；Canvas 的 `Decisions`/`User Actions Required` 字段（需标注 actor=Human Owner）。 |
| 禁止操作 | 不得绕过系统安全门禁；不得暴露凭据到聊天/Issue/PR。 |
| 输入契约 | 来自人工介入中心的 `USER_ACTION_REQUIRED` 通知（含 `task_id`、所需操作、安全提示）。 |
| 输出契约 | 授权/拒绝对结果；"已完成授权"等明确信号；验收结论须同步回 GitHub Issue/PR/Review。 |
| 任务开始条件 | 收到需人工决策的 `USER_ACTION_REQUIRED`；或主动发起架构/验收决策。 |
| 完成条件 | 决策已记录并回流到 Hermes 控制层与 GitHub 权威记录。 |
| 失败和升级路径 | 无（自身为最终决策节点）；若无法决策则任务挂起于 `USER_ACTION_REQUIRED`。 |
| 是否允许修改代码 | 否（通过 GitHub 手动 merge 间接生效，非直接改工作树） |
| 是否允许操作 GitHub | 是（手动 merge/批准，自动化不代行） |
| 是否允许接触凭据 | 仅经授权（凭据写入服务端 `.env`，不在聊天中传递） |
| 是否允许请求用户授权 | 是（自身即授权主体） |

### 3.2 `Hermes Master / Boss`

| 字段 | 定义 |
| --- | --- |
| 唯一职责 | 顶层 Master 决策：任务分配、跨角色协调、Phase 推进、冲突仲裁；对 `Hermes Engineering Controller` 负总责。 |
| 允许读取的数据 | 全部 Hermes 控制层状态、Activity Feed 聚合、GitHub 同步状态、Canvas 摘要。 |
| 允许调用的工具 | 控制层内部分发器、`Planner / Scheduler`、`Independent Reviewer` 编排接口、人工介入中心。 |
| 允许写入的位置 | 控制层状态存储；Canvas 的 `Goal`/`Scope`/`Assigned Roles`/`Current Status`/`Decisions` 字段（标注 actor）。 |
| 禁止操作 | 不得直接改代码；不得直接 push GitHub；不得持有/转发凭据；不得绕过 `delivery.py` 纪律。 |
| 输入契约 | 来自 `Human Owner` 的目标与约束；GitHub Issue 作为任务来源。 |
| 输出契约 | 任务计划、角色分配、升级决策；均须可追溯到 GitHub Issue。 |
| 任务开始条件 | 收到 `Human Owner` 目标或新 GitHub Issue。 |
| 完成条件 | 任务进入可被后续角色消费的明确状态，并写入权威记录。 |
| 失败和升级路径 | 无法决策时升级到 `Human Owner`（发起 `ARCHITECTURE_DECISION` 或 `USER_ACTION_REQUIRED`）。 |
| 是否允许修改代码 | 否 |
| 是否允许操作 GitHub | 否（仅通过控制层受控写入，且受 `delivery.py` 约束） |
| 是否允许接触凭据 | 否 |
| 是否允许请求用户授权 | 是（代表控制层向 `Human Owner` 发起） |

### 3.3 `Planner / Scheduler`

| 字段 | 定义 |
| --- | --- |
| 唯一职责 | 计划与调度：将目标拆解为任务、排程、状态机推进、返工循环编排（`ROUND2_LABEL="round-2"` 仅本角色可加）、`MAX_ROUNDS=2` 上限控制。 |
| 允许读取的数据 | GitHub Issue、控制层任务状态、Agent 活动事件、Canvas 内容、CI/Review 结果。 |
| 允许调用的工具 | 控制层状态机、`AgentRuntimeAdapter`（`submit_task()`/`resume_session()`/`cancel_task()`）、受限 MCP `workspace_*` 工具（读取类）。 |
| 允许写入的位置 | 控制层任务状态；GitHub Issue（计划/标签，非代码）；Canvas 的 `Scope`/`Out of Scope`/`Acceptance Criteria`/`Assigned Roles`/`Current Status`/`Next Step`。 |
| 禁止操作 | 不得自行改代码；不得 merge；不得绕过返工上限；不得向非允许仓库写。 |
| 输入契约 | Master 分配的目标 + GitHub Issue；遵循 `constants.py` 的 `ROLE_SCHEDULER`。 |
| 输出契约 | 计划、任务派发、标签变更、`round-2` 信号；均落 GitHub/控制层权威记录。 |
| 任务开始条件 | Master 分配或新 Issue 入队。 |
| 完成条件 | 任务达终态或交付给下一角色；状态已持久化。 |
| 失败和升级路径 | 达 `MAX_ROUNDS` 仍失败 → 升级 `Human Owner`（发 `USER_ACTION_REQUIRED` 或 `TASK_BLOCKED`）。 |
| 是否允许修改代码 | 否 |
| 是否允许操作 GitHub | 仅经授权（经 `delivery.py` 受控、非代码推送；标签/Issue 更新） |
| 是否允许接触凭据 | 否 |
| 是否允许请求用户授权 | 是（经 Master 或代表控制层发起） |

### 3.4 `Coding Worker`

| 字段 | 定义 |
| --- | --- |
| 唯一职责 | 实际代码实现：在沙箱内编辑目标仓库工作树、运行测试、提交、推送分支、开 Draft PR（经 `delivery.py`）。映射到 `ROLE_CODING_AGENT`。 |
| 允许读取的数据 | 目标仓库源码、GitHub Issue/PR diff、CI 结果、Canvas 的 `Acceptance Criteria`/`Evidence`、受限 MCP 提供的任务上下文。 |
| 允许调用的工具 | `AgentRuntimeAdapter` 运行时（`Open SWE` 第一版）、受限 MCP `workspace_*` 工具、沙箱内 git/测试命令（经 `HermesDockerSandboxBackend`）。 |
| 允许写入的位置 | 仅 `ALLOWED_GITHUB_REPOS`（`yzhlx/hermes-open-swe-smoke-test`）的目标分支；Canvas 的 `Current Status`/`Evidence` 字段。 |
| 禁止操作 | 不得触碰 `PROTECTED_REPOS`（`yzhlx/hermes-learning-os`）；不得 push `main`、force-push、merge；不得持有 GitHub 令牌（仅 push 步骤被注入）；不得绕过 `delivery.py`。 |
| 输入契约 | `AgentRuntimeAdapter.submit_task()` 派发的任务 + 受控授权 `DeliveryAuthorization`（`GRANT_PUSH_AND_DRAFT_PR`，task+repo+commit 绑定）。 |
| 输出契约 | 提交 SHA、Draft PR URL、测试结果；通过 Activity 事件（`COMMIT_CREATED`/`PUSH_COMPLETED`/`DRAFT_PR_CREATED` 等）回流。 |
| 任务开始条件 | Scheduler 派发且获得有效 `DeliveryAuthorization`。 |
| 完成条件 | Draft PR 已创建且 CI 触发；状态写入控制层与 GitHub。 |
| 失败和升级路径 | CI/Review 失败 → 进入 `REWORK_STARTED`（受 `MAX_ROUNDS` 约束）；超限升级 `Human Owner`。 |
| 是否允许修改代码 | 是（仅允许仓库、仅目标分支） |
| 是否允许操作 GitHub | 仅经授权（经 `delivery.py`：push + Draft PR，永不 merge） |
| 是否允许接触凭据 | 否（令牌仅在 push 步骤局部注入并丢弃） |
| 是否允许请求用户授权 | 否（须经 Scheduler/Master 代为发起） |

### 3.5 `Independent Reviewer`

| 字段 | 定义 |
| --- | --- |
| 唯一职责 | 独立审查：读取 PR diff 与沙箱证据，输出 `APPROVE` / `REQUEST_CHANGES`；与 `Coding Worker` 进程隔离、不得自审。映射到 `ROLE_REVIEWER`。 |
| 允许读取的数据 | PR diff、CI check、Agent 活动事件、Canvas 的 `Review Findings`/`Evidence`、控制层任务状态。 |
| 允许调用的工具 | 受限 MCP `workspace_post_review_result`、读取类 MCP 工具、`AgentRuntimeAdapter.get_activity()`。 |
| 允许写入的位置 | GitHub PR Review 评论；Canvas 的 `Review Findings` 字段（标注 actor）。 |
| 禁止操作 | 不得改代码、不得 merge、不得自审、不得访问非允许仓库。 |
| 输入契约 | `REVIEW_STARTED` 事件 + 已达 CI green 的 Draft PR（与 `D3-IMPLEMENTATION-PLAN.md` §4.10 一致）。 |
| 输出契约 | Review 结论经 `workspace_post_review_result` 与 `REVIEW_FINDING`/`REVIEW_PASSED` 事件回流；`REQUEST_CHANGES` 触发 `round-2`。 |
| 任务开始条件 | CI 通过且 Scheduler 触发 `REVIEW_STARTED`。 |
| 完成条件 | `REVIEW_PASSED` 或 `REVIEW_FINDING`（含反馈）已记录并回流。 |
| 失败和升级路径 | 审查无法判定 → 升级 `Human Owner`（`ARCHITECTURE_DECISION`/`USER_ACTION_REQUIRED`）。 |
| 是否允许修改代码 | 否 |
| 是否允许操作 GitHub | 仅经授权（PR Review 评论，非代码/merge） |
| 是否允许接触凭据 | 否 |
| 是否允许请求用户授权 | 否（经 Scheduler/Master 发起） |

### 3.6 `QA Agent`

| 字段 | 定义 |
| --- | --- |
| 唯一职责 | 质量保障：补充/执行测试、验证验收标准（非代码实现的独立验证视角）、记录测试证据。 |
| 允许读取的数据 | 目标仓库、PR diff、CI 结果、Canvas 的 `Acceptance Criteria`/`Evidence`、`TEST_*` 事件。 |
| 允许调用的工具 | 沙箱内测试命令、受限 MCP `workspace_post_update`/`workspace_get_evidence`、读取类工具。 |
| 允许写入的位置 | 仅 `ALLOWED_GITHUB_REPOS` 的目标分支（如追加测试）；Canvas 的 `Evidence`/`Current Status`。 |
| 禁止操作 | 不得触碰 `PROTECTED_REPOS`；不得 merge；不得持有凭据；不得绕过 `delivery.py`。 |
| 输入契约 | Scheduler 派发的 QA 任务 + 受控授权。 |
| 输出契约 | 测试结论、证据链接（`workspace_get_evidence`）；通过 `TEST_PASSED`/`TEST_FAILED`（映射到 `TEST_FINISHED` 状态）事件回流。 |
| 任务开始条件 | Master/Scheduler 派发 QA 阶段任务。 |
| 完成条件 | 验收标准验证完毕且证据已记录。 |
| 失败和升级路径 | 验收不通过 → 升级 Scheduler 触发返工；阻塞则升级 `Human Owner`。 |
| 是否允许修改代码 | 仅经授权（追加测试，限允许仓库目标分支） |
| 是否允许操作 GitHub | 仅经授权（经 `delivery.py`） |
| 是否允许接触凭据 | 否 |
| 是否允许请求用户授权 | 否（经 Scheduler/Master 发起） |

### 3.7 `Documentation Agent`

| 字段 | 定义 |
| --- | --- |
| 唯一职责 | 文档：生成/更新与变更配套的文档、Canvas 内容、运行报告、审计文档。 |
| 允许读取的数据 | 目标仓库、PR diff、Activity 事件、Canvas、控制层状态。 |
| 允许调用的工具 | 受限 MCP `workspace_update_canvas`/`workspace_post_update`/`workspace_get_evidence`、读取类工具。 |
| 允许写入的位置 | 仅 `ALLOWED_GITHUB_REPOS` 的文档文件（限目标分支）；Canvas 的 `Decisions`/`Next Step`/`Risks` 等协作字段。 |
| 禁止操作 | 不得改代码逻辑（仅文档）；不得 merge；不得触碰 `PROTECTED_REPOS`；不得持有凭据。 |
| 输入契约 | Scheduler 派发的文档任务 + 变更上下文。 |
| 输出契约 | 文档/报告/Canvas 更新；通过 `PLAN_UPDATED`/Canvas 更新事件回流。 |
| 任务开始条件 | Master/Scheduler 派发或关联代码任务完成后触发。 |
| 完成条件 | 文档与代码变更同步且证据齐全。 |
| 失败和升级路径 | 文档与代码冲突 → 升级 Scheduler。 |
| 是否允许修改代码 | 否（仅文档文件，限允许仓库） |
| 是否允许操作 GitHub | 仅经授权（经 `delivery.py`，仅文档） |
| 是否允许接触凭据 | 否 |
| 是否允许请求用户授权 | 否（经 Scheduler/Master 发起） |

### 3.8 `Release Agent`

| 字段 | 定义 |
| --- | --- |
| 唯一职责 | 交付与发布：编排 `delivery.py` 受控交付、Draft PR 生命周期、发布前检查；**不执行**生产部署（Openship V1 不接入）。 |
| 允许读取的数据 | Draft PR、CI/Review 状态、控制层交付状态、Canvas 的 `Current Status`/`Evidence`/`User Actions Required`。 |
| 允许调用的工具 | `delivery.py` 的 `DeliveryController.deliver()`、受限 MCP `workspace_get_pending_actions`/`workspace_request_user_action`、读取类工具。 |
| 允许写入的位置 | GitHub（Draft PR，经 `delivery.py`）；Canvas 的 `Current Status`/`User Actions Required`。 |
| 禁止操作 | 不得 merge；不得生产部署；不得触碰 `PROTECTED_REPOS`；不得持有长期凭据。 |
| 输入契约 | 已完成 Review 的任务 + 有效 `DeliveryAuthorization`。 |
| 输出契约 | 交付状态（`DRAFT_PR_CREATED`/幂等结果）、待用户动作（如 `FINAL_ACCEPTANCE`/`PRODUCTION_RELEASE`）经 `workspace_request_user_action` 回流。 |
| 任务开始条件 | Review 通过且 Scheduler 移交交付阶段。 |
| 完成条件 | Draft PR 已创建且状态持久化；若需发布则发 `USER_ACTION_REQUIRED` 给 `Human Owner`。 |
| 失败和升级路径 | 交付失败（凭据/分支/冲突）→ fail-closed 终止并升级 `Human Owner`。 |
| 是否允许修改代码 | 否 |
| 是否允许操作 GitHub | 仅经授权（经 `delivery.py`：Draft PR，永不 merge） |
| 是否允许接触凭据 | 否（令牌仅在 `deliver()` 内部局部注入并丢弃） |
| 是否允许请求用户授权 | 是（代控制层向 `Human Owner` 发起 `FINAL_ACCEPTANCE`/`PRODUCTION_RELEASE`） |

---

## 4. 权限矩阵速查 (Permission Matrix)

| 角色 | 修改代码 | 操作 GitHub | 接触凭据 | 请求用户授权 |
| --- | --- | --- | --- | --- |
| `Human Owner` | 否（手动 merge） | 是（手动） | 仅经授权 | 是 |
| `Hermes Master / Boss` | 否 | 否（经控制层） | 否 | 是 |
| `Planner / Scheduler` | 否 | 仅经授权 | 否 | 是 |
| `Coding Worker` | 是（限允许仓库） | 仅经授权（`delivery.py`） | 否 | 否 |
| `Independent Reviewer` | 否 | 仅经授权（Review） | 否 | 否 |
| `QA Agent` | 仅经授权（测试） | 仅经授权（`delivery.py`） | 否 | 否 |
| `Documentation Agent` | 否（仅文档） | 仅经授权（`delivery.py`） | 否 | 否 |
| `Release Agent` | 否 | 仅经授权（`delivery.py`） | 否 | 是 |

---

## 5. 与既有边界的一致性 (Consistency)

- **单一事实源:** 角色权限权威仅本文档 + `constants.py`；Buzz Persona/Team 仅展示与运行容器。
- **仓库范围:** 所有"操作 GitHub/修改代码"均受 `ALLOWED_GITHUB_REPOS` / `PROTECTED_REPOS` 约束（`yzhlx/hermes-learning-os` 永久保护）。
- **交付纪律:** 任何 GitHub 写入经 `delivery.py`：fail-closed 授权、Draft PR 强制、永不 merge、幂等。
- **凭据隔离:** 控制器/控制层从不持有长期令牌；令牌仅在 push 步骤局部注入并丢弃。
- **无第二身份事实源:** Buzz Persona 数据库不得存储或派生权威角色属性。
