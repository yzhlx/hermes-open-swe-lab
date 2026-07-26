# ACTIVITY-AND-INTERVENTION-EVENT-MODEL.md

- **日期 (Date):** 2026-07-26
- **场景 (Scenario):** 架构设计 (Architecture Design)
- **STATUS:** `COLLABORATION_V1_ARCHITECTURE_DESIGNED`
- **关联文档:** `HUMAN-AGENT-WORKSPACE-ARCHITECTURE.md`、`ROLE-AND-PERMISSION-MODEL.md`、`BUZZ-INTEGRATION-BOUNDARY.md`、`COLLABORATION-V1-IMPLEMENTATION-ROADMAP.md`

---

## 0. 权威声明 (Authority Statement)

1. **事件由 Hermes 控制层生产并存储**（与 `MVP-0-ARCHITECTURE.md` 的 Event Store `runtime/events.db` 一致）。`Buzz Workspace` 仅**订阅并展示** Activity Feed，不得作为事件权威源。
2. **人工介入由 Hermes 控制层控制**。`Buzz Workspace` 仅负责**显示与通知** `USER_ACTION_REQUIRED`；控制层负责创建请求、绑定 `task_id`、记录所需操作、记录安全提示、等待用户结果、恢复原任务、防止重复发送。
3. **GitHub 或 Hermes 状态存储仍是权威事实源。** 不得将 Buzz 当前**未完整接通**的 Workflow Approval Executor 作为生产核心门禁。
4. 与 `constants.py` / `delivery.py` 一致：事件须可追溯到 `ALLOWED_GITHUB_REPOS` 的 Issue/Commit/PR/Check/Review；凭据隔离、fail-closed 纪律不变。

---

## 1. Agent Activity Feed 事件模型 (Event Model)

### 1.1 统一事件字段 (Unified Event Fields)

每条 Activity 事件必须包含以下字段（所有事件通用）：

| 字段 (Field) | 类型 | 说明 |
| --- | --- | --- |
| `event_id` | string (UUID) | 全局唯一；用于幂等去重（与 Worker API `event_id` 去重一致） |
| `task_id` | string | 关联任务标识；须可映射到 GitHub Issue / 控制层任务 |
| `actor_role` | enum | 产生事件的逻辑角色，取值见 `ROLE-AND-PERMISSION-MODEL.md`（8 角色） |
| `timestamp` | ISO-8601 | 事件发生时间（UTC） |
| `concise_summary` | string | 一句话摘要（≤120 字），默认展示内容 |
| `status` | enum | 事件状态（如 `pending`/`running`/`success`/`failure`/`blocked`） |
| `evidence_links` | list[url] | 指向权威记录的证据链接（GitHub / 控制层 / 运行报告） |
| `github_refs` | object | GitHub 引用：`issue` / `commit` / `pr` / `check` / `review`（可空） |
| `requires_user_attention` | bool | 是否需要用户关注（通常为 `USER_ACTION_REQUIRED` 时为真） |
| `contains_sensitive_info` | bool | 是否包含敏感信息（若为 true，默认**不展示**细节，仅提示） |
| `raw_log_location` | string | 原始日志位置（如 `runtime/runs/<task>.jsonl` 路径或控制层存储键） |

### 1.2 事件清单 (Event Catalog — 21 events)

以下事件名**全文档一致**，任何层不得改名：

| # | 事件名 (Event) | 触发时机 | 典型 `status` | `github_refs` 关联 |
|---| --- | --- | --- | --- |
| 1 | `TASK_STARTED` | 任务被 Scheduler 派发、Agent 会话启动 | `running` | `issue` |
| 2 | `PLAN_UPDATED` | 计划/范围/验收标准变更（如 Canvas `Scope`/`Acceptance Criteria`） | `success` | `issue` |
| 3 | `FILE_READ` | Agent 读取仓库文件 | `success` | `commit` |
| 4 | `FILE_MODIFIED` | Agent 修改工作树文件 | `success` | `commit`（待） |
| 5 | `COMMAND_STARTED` | 沙箱内命令开始（测试/构建/lint） | `running` | — |
| 6 | `COMMAND_FINISHED` | 命令结束（含退出码） | `success`/`failure` | — |
| 7 | `TEST_STARTED` | 测试套件开始 | `running` | `check` |
| 8 | `TEST_FINISHED` | 测试结束（通过/失败计数） | `success`/`failure` | `check` |
| 9 | `COMMIT_CREATED` | 提交生成 | `success` | `commit` |
| 10 | `PUSH_COMPLETED` | 分支推送完成（经 `delivery.py`） | `success`/`failure` | `commit` |
| 11 | `DRAFT_PR_CREATED` | Draft PR 已创建（强制 draft，永不 merge） | `success` | `pr` |
| 12 | `CI_PENDING` | CI 检查排队/进行中 | `pending`/`running` | `check` |
| 13 | `CI_PASSED` | CI 全部通过 | `success` | `check` |
| 14 | `CI_FAILED` | CI 失败 | `failure` | `check` |
| 15 | `REVIEW_STARTED` | 独立审查开始（CI green 后） | `running` | `review` |
| 16 | `REVIEW_FINDING` | 审查发现（含 `REQUEST_CHANGES` 反馈） | `success`/`failure` | `review` |
| 17 | `REWORK_STARTED` | 返工开始（受 `MAX_ROUNDS=2` 约束） | `running` | `pr`（同 PR 新 head） |
| 18 | `REVIEW_PASSED` | 审查通过 | `success` | `review` |
| 19 | `USER_ACTION_REQUIRED` | 需人工介入（见 §2 原因枚举） | `blocked` | 视原因（`pr`/`issue`/—） |
| 20 | `TASK_BLOCKED` | 任务阻塞（无法自动继续） | `blocked` | `issue` |
| 21 | `TASK_COMPLETED` | 任务达终态（含待用户验收） | `success` | `pr`/`issue` |

> 说明：事件名的英文原名原样保留；中文注释仅作说明。`REWORK` 复用 `ROUND2_LABEL="round-2"` 信号（仅 `Planner / Scheduler` 可加标签）。

### 1.3 默认展示策略 (Default Display Policy)

- **默认显示摘要 (`concise_summary`)**：Activity Feed 默认仅渲染 `concise_summary` + 关键 `github_refs` + `status` + 时间。
- **默认不暴露：**
  - Agent 完整思考过程 / 推理链；
  - 秘密 (`secrets`)、环境变量 (`env vars`)、令牌；
  - 大段终端输出（仅展示 `COMMAND_FINISHED` 的退出码与若干行摘要，`raw_log_location` 提供溯源入口）。
- **敏感保护：** 当 `contains_sensitive_info=true`，Feed 仅显示"存在敏感内容，已脱敏"提示，详情仅在有权限且经审计的溯源视图中可见（遵循 `SECURITY-BOUNDARIES.md` B5/B11 脱敏纪律）。
- **溯源原则：** 任何摘要点击可展开至 `evidence_links` 与 `raw_log_location`，但原始敏感字段仍受脱敏约束。

---

## 2. 人工介入中心 (Human Intervention Center)

### 2.1 `USER_ACTION_REQUIRED` 原因枚举 (Reason Enum — 8 reasons)

人工介入请求**只允许**以下 8 类原因（英文原名原样，全文档一致）：

| 原因 (Reason) | 含义 | 典型发起角色 |
| --- | --- | --- |
| `AUTHORIZATION` | 需显式授权（如 `DeliveryAuthorization` 类操作） | `Release Agent` / `Planner / Scheduler` |
| `SECRET_ENTRY` | 需用户写入凭据（服务端 `.env`，不在聊天传递） | `Hermes Master / Boss` |
| `PAYMENT` | 需付费/账单批准（任何可能产生费用的动作前停止） | `Hermes Master / Boss` |
| `SECURITY_INCIDENT` | 安全事件需人工裁决 | `Hermes Master / Boss` |
| `ARCHITECTURE_DECISION` | 需主观/架构决策 | `Hermes Master / Boss` |
| `EXTERNAL_ACCOUNT_SETUP` | 需配置外部账号（如 GitHub App、LangSmith、relay） | `Hermes Master / Boss` |
| `FINAL_ACCEPTANCE` | 最终验收（merge 决策前） | `Release Agent` |
| `PRODUCTION_RELEASE` | 生产发布（Openship V1 不接入，仅预留） | `Release Agent` |

> 与 `AGENTS.md` §14 用户联系场景一致：仅在上述必要情形联系 `Human Owner`。

### 2.2 控制层职责 (Hermes Control Responsibilities)

`USER_ACTION_REQUIRED` 事件由 Hermes 控制层（而非 Buzz）生产与控制：

1. **创建请求 (create):** 生成 `USER_ACTION_REQUIRED` 事件，绑定 `task_id` 与 `reason`（取自 §2.1 枚举）。
2. **绑定 task_id (bind):** 事件 `task_id` 必须与受影响任务一致，便于恢复原任务。
3. **记录具体所需操作 (record operation):** `concise_summary` + 结构化"你需要完成"清单（如写入哪个服务端路径、点击哪个 GitHub 操作）。
4. **记录安全提示 (record security hint):** 明确"不要在聊天/Issue/PR 发送密钥"等提示；敏感细节不进 Feed 摘要。
5. **等待用户结果 (wait):** 暂停该任务（进入 `blocked`），不自动推进。
6. **恢复原任务 (resume):** 收到 `Human Owner` 明确信号（如"已完成授权"）后，依据 `task_id` 恢复对应任务上下文。
7. **防止重复发送 (dedupe):** 对同一 `(task_id, reason)` 未关闭的请求，**不得重复发送**；以控制层状态存储中的未关闭请求为准（幂等）。

### 2.3 Buzz 职责边界 (Buzz Responsibility Boundary)

- Buzz **负责**：在 `#user-action-required` 频道展示 `USER_ACTION_REQUIRED` 通知、@ 相关 `Human Owner`、提供跳转链接。
- Buzz **不负责**：审批执行、状态判定、凭据存储、作为生产门禁。
- **禁止将 Buzz Workflow Approval Executor 作为生产核心门禁**：因其当前未完整接通，核心授权/验收门禁必须以 GitHub 或 Hermes 控制层状态存储为权威（与 `BUZZ-INTEGRATION-BOUNDARY.md` 一致）。

---

## 3. 事件→权威记录映射 (Event → Authority Mapping)

| 事件 | 权威记录位置 | 可重建性 |
| --- | --- | --- |
| `COMMIT_CREATED` / `PUSH_COMPLETED` | GitHub Commit / Branch | 高（GitHub 为权威） |
| `DRAFT_PR_CREATED` | GitHub Draft PR（`delivery.py`） | 高 |
| `CI_PASSED` / `CI_FAILED` | GitHub Check Run | 高 |
| `REVIEW_STARTED` / `REVIEW_FINDING` / `REVIEW_PASSED` | GitHub Review | 高 |
| `USER_ACTION_REQUIRED` | Hermes 控制层状态存储 + GitHub Issue 备注 | 高 |
| `TASK_*` / `PLAN_UPDATED` | Hermes 控制层状态 + Canvas（协作层） | 中（Canvas 可重建自 GitHub） |
| `FILE_READ` / `FILE_MODIFIED` / `COMMAND_*` / `TEST_*` | 控制层 Event Store + `runtime/runs/*.jsonl` | 中（日志溯源） |

> **原则：** Activity Feed 是**协作展示层**；所有权威结论须可同步回 GitHub 或 Hermes 控制层存储。Buzz/Canvas 不得成为唯一证据源（见 `COLLABORATION-V1-IMPLEMENTATION-ROADMAP.md` 的 Canvas 与搜索章节）。

---

## 4. 与既有边界的一致性 (Consistency)

- **Event Store 纪律:** 事件存储延续 `MVP-0-ARCHITECTURE.md` 的 `runtime/events.db` + JSONL；不得写入 API Key / Token / PEM / `.env`（B5/B11）。
- **脱敏:** 命令/日志经 `hermes_worker.redact` 脱敏（B21）；`contains_sensitive_info` 字段与脱敏策略联动。
- **返工上限:** `REWORK_STARTED` 受 `MAX_ROUNDS=2` 与 `ROUND2_LABEL` 约束；超限转 `TASK_BLOCKED` → 升级 `Human Owner`。
- **交付纪律:** `DRAFT_PR_CREATED` / `PUSH_COMPLETED` 经 `delivery.py`：fail-closed、Draft 强制、永不 merge、幂等。
- **仓库范围:** 所有 `github_refs` 落在 `ALLOWED_GITHUB_REPOS`；`PROTECTED_REPOS` 永不出现。
