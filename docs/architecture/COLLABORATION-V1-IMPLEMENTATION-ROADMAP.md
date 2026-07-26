# COLLABORATION-V1-IMPLEMENTATION-ROADMAP.md

- **日期 (Date):** 2026-07-26
- **场景 (Scenario):** 架构设计 (Architecture Design)
- **STATUS:** `COLLABORATION_V1_ARCHITECTURE_DESIGNED`
- **关联文档:** `HUMAN-AGENT-WORKSPACE-ARCHITECTURE.md`、`ROLE-AND-PERMISSION-MODEL.md`、`ACTIVITY-AND-INTERVENTION-EVENT-MODEL.md`、`BUZZ-INTEGRATION-BOUNDARY.md`

---

## 0. 目的与权威声明 (Purpose & Authority)

本文档给出 **Collaboration V1** 的实施路线（Phase A–G）与 5 项专题设计。本阶段**仅做架构设计**：不修改运行时代码、不提交/推送/建 PR（由主理人统一执行）。所有标识符（分层、角色、频道、事件、USER_ACTION_REQUIRED 原因、Canvas 字段、搜索优先级、ACP 接口、MCP 工具名、Phase 名）与另外 4 份文档**完全一致**。

**复用基线:** 0 REPLACE；21 纯 KEEP + 2 可选 INTEGRATE(#6 沙箱→`buzz-dev-mcp`, #7 Agent→`buzz-agent` ACP) + 1 延后 INTEGRATE(#22 部署→Openship scoped-MCP，MVP-0 后)。Buzz 主干事实源永久不采纳；Hermes Learning OS Activation Gate 6 前置、本阶段不开发。

---

## 1. Phase 总览 (Phase Overview)

| Phase | 名称 | 目标 | 阻塞下一阶段? |
| --- | --- | --- | --- |
| `Phase A` | 工作空间、角色、频道、Canvas、Activity Feed 设计 | 定义协作外壳与展示契约 | 是 |
| `Phase B` | Hermes 控制层 Scheduler / Reviewer / Rework 闭环 | 控制层闭环（权限/状态机/返工） | 是 |
| `Phase C` | Buzz 工作空间适配器 | Buzz 仅作交互/展示接入 | 否（可并行部分） |
| `Phase D` | ACP Runtime Adapter | 可替换 Agent 运行时接口 | 是（执行前需完成） |
| `Phase E` | MCP Workspace Tools | 受限 MCP 工具与角色能力过滤 | 是 |
| `Phase F` | Smoke Test 真实闭环 | 在 `ALLOWED_GITHUB_REPOS` 跑通端到端 | 是 |
| `Phase G` | 用户体验试用和迭代 | 人类+多 Agent 协作试用与迭代 | 否（持续） |

每个 Phase 的字段：`目标 / 修改范围 / 前置条件 / 验收标准 / 测试 / 风险 / 回滚 / 是否需要用户操作 / 是否阻塞下一阶段`。

---

## 2. Phase 详细设计 (Phase Details)

### `Phase A` — 工作空间、角色、频道、Canvas、Activity Feed 设计

- **目标:** 完成协作外壳的静态设计：频道拓扑、角色→频道归属、Canvas 12 字段模板、21 类 Activity 事件契约（见 §3–§7 专题）。
- **修改范围:** 仅新增 `docs/architecture/*`（本批文档）；不改动 `.py`。
- **前置条件:** 本批 5 份架构文档通过主理人评审；复用结论已确认。
- **验收标准:** 所有标识符在 5 份文档间一致；频道/角色/Canvas/事件契约可映射到 `ROLE-AND-PERMISSION-MODEL.md` 与 `ACTIVITY-AND-INTERVENTION-EVENT-MODEL.md`。
- **测试:** 文档一致性自检（标识符交叉核对清单）；无代码测试。
- **风险:** 标识符漂移 → 主理人收口时统一校验。
- **回滚:** 文档未提交，删除即可。
- **是否需要用户操作:** 否（仅评审）。
- **是否阻塞下一阶段:** 是。
- **本 PR 定位:** 本批 5 份文档（PR #8）即 **Phase A 的交付物**（协作外壳静态设计）。评审通过并合并后 Phase A 视为完成；**下一阶段为 Phase B**（Hermes 控制层 Scheduler/Reviewer/Rework 闭环实现），不在本 PR 内开展 Phase B。

### `Phase B` — Hermes 控制层 Scheduler / Reviewer / Rework 闭环

- **目标:** 在既有 `MVP-0-ARCHITECTURE.md` 控制层之上，落实 `Planner / Scheduler`、`Independent Reviewer`、返工闭环（`MAX_ROUNDS=2`、`ROUND2_LABEL="round-2"`），并接入人工介入中心。
- **修改范围:** `hermes_worker/` 控制层（Scheduler/Reviewer 编排、状态机、USER_ACTION_REQUIRED 创建/去重/恢复）；复用 `delivery.py` 不变。
- **前置条件:** Phase A 角色与事件契约定稿；`constants.py` 角色常量就绪。
- **验收标准:** 任务可经 Scheduler 派发→Coding→CI→Review→(rework)→User Acceptance；返工达上限升级 `Human Owner`；`USER_ACTION_REQUIRED` 对同一 `(task_id, reason)` 不重复发送。
- **测试:** 离线单测覆盖状态机转移、round-2 标签仅 Scheduler 可加、返工上限、介入去重、fail-closed（沿用 `tests/` 基线）。
- **风险:** 状态机与既有 `HOST_WORKER_ACTIVE_STATES`/`TERMINAL_STATES` 冲突 → 需对齐常量。
- **回滚:** 分支化开发；失败保留 Draft PR 供审查，不合并。
- **是否需要用户操作:** 是（仅在 `USER_ACTION_REQUIRED` 情形，如 `FINAL_ACCEPTANCE`）。
- **是否阻塞下一阶段:** 是。

### `Phase C` — Buzz 工作空间适配器

- **目标:** 实现 Buzz 作为交互/展示层适配器，承载频道、角色展示、Activity Feed 显示、Canvas 展示、搜索聚合、通知（见 `BUZZ-INTEGRATION-BOUNDARY.md` §2）。
- **修改范围:** 新增 Buzz 适配器模块（仅接口/订阅/渲染）；**不安装/不运行/不 Fork Buzz 后端**，仅完成边界与契约（contract-only）；借鉴 Buzz"分支即频道"的交互设计但**不作为权威状态**。真实 Buzz 实例的部署/账号/连接由独立的 **Buzz 接入门禁** 控制（见下文）。
- **前置条件:** Phase A 展示契约定稿；Buzz 边界文档确认。
- **验收标准:** Buzz 仅展示来自 Hermes 控制层/GitHub 的权威数据；不持有凭据；不引入第二事实源；`USER_ACTION_REQUIRED` 通知可达 `Human Owner`。
- **测试:** 适配器契约测试（数据来源断言、脱敏断言、无写 GitHub 断言）。
- **风险:** 误把 Buzz 当权威 → 由边界文档与测试双重约束；Buzz Workflow Approval Executor 不得作生产门禁。
- **回滚:** 适配器为新增模块，删除/禁用即可。
- **是否需要用户操作:** 否（真实 Buzz 部署/账号/连接由 **Buzz 接入门禁** 控制，需 Human Owner 操作，见下文）。
- **是否阻塞下一阶段:** 否（可与 D/E 并行；但显示能力依赖 A/B 数据）。

### Buzz 接入门禁 (Buzz Integration Gate)

弥合 **Phase C（仅契约/适配器边界，不部署真实 Buzz 后端）** 与 **Phase G（真实 Buzz 工作空间试用）** 之间的缺口。Phase C 不部署真实 Buzz；在进入 Phase G 真实试用前，必须满足 `BUZZ_ENV_READY` 门禁（需 Human Owner 操作，原因 `EXTERNAL_ACCOUNT_SETUP`）：

1. Buzz 后端已部署或提供可达实例（部署/连接账号已就绪）；
2. 账号与 Workspace / 频道 / 连接已建立；
3. 凭据经服务端 `.env`（不在聊天 / Issue / PR 传递）；
4. 连接与通知链路经冒烟验证（`USER_ACTION_REQUIRED` 可达 `Human Owner`）。

- 门禁未通过前，Phase G 不得开始真实 Buzz 试用（可用静态 / 模拟契约验证替代）。
- 明确：**本设计分支（PR #8）不部署 Buzz**；Buzz 真实接入属后续实施阶段，受此门禁控制。

### `Phase D` — ACP Runtime Adapter

- **目标:** 定义并实现 `AgentRuntimeAdapter` 接口，第一版运行时 `Open SWE`；完成 `buzz-agent` 等候选的兼容性评估（**不接入真实 buzz-agent**）。
- **修改范围:** 新增 `AgentRuntimeAdapter` 抽象与 `Open SWE` 实现；不改动既有 Open SWE 上游（保持 `ed12bb8d…` 基线）。
- **前置条件:** Phase A/B 控制层闭环可派发任务。
- **验收标准:** 控制层经 `AgentRuntimeAdapter` 启动/提交/恢复/取消/取活动/请求权限/收结果/终止会话；运行时可替换；可取消/超时/清理子进程/恢复上下文。
- **测试:** 适配器接口契约测试（8 方法齐备、超时/取消/清理路径）、`Open SWE` 集成冒烟（离线或受控沙箱）。
- **风险:** 依赖单一 Agent 私有格式 → 通过 ACP 接口解耦规避；子进程泄漏 → 明确 terminate/cleanup 路径。
- **回滚:** 适配器为新增；运行时切换回退到 `Open SWE`。
- **是否需要用户操作:** 否（运行时凭据经既有服务端 `.env`）。
- **是否阻塞下一阶段:** 是（执行前需运行时可用）。

### `Phase E` — MCP Workspace Tools

- **目标:** 实现 9 个受限 `workspace_*` 工具，并按 Agent 角色进行能力过滤（见 §7）。
- **修改范围:** 新增 MCP 工具服务器与角色能力过滤中间件；调用既有权限/状态接口。
- **前置条件:** Phase B 权限模型与 Phase D 运行时可用。
- **验收标准:** 9 工具齐备；按角色过滤生效；默认**不提供**高危能力（宿主机 Shell/任意 FS/任意 GitHub 仓库/生产部署/持久化密钥/自动合并/改默认分支/绕过门禁）。
- **测试:** 每个工具的授权与拒绝用例；角色过滤矩阵测试；越权尝试被拒（fail-closed）。
- **风险:** 工具越权 → 默认拒绝 + 角色过滤 + 审计日志。
- **回滚:** 工具为新增服务，禁用即可。
- **是否需要用户操作:** 否。
- **是否阻塞下一阶段:** 是（闭环执行依赖工具安全接入）。

### `Phase F` — Smoke Test 真实闭环

- **目标:** 在 `ALLOWED_GITHUB_REPOS`（`yzhlx/hermes-open-swe-smoke-test`）跑通人类+多 Agent 协作的完整闭环，统一生命周期：
  `Issue → Plan → Code/Test → Commit → controlled delivery (push + Draft PR via `DeliveryController.deliver()`) → CI → Independent Review → rework on same PR (若 `REQUEST_CHANGES`，受 `MAX_ROUNDS=2` 约束) → FINAL_ACCEPTANCE (Human Owner) → TASK_COMPLETED`。
  （注意：Draft PR 必须先于 Independent Review 存在，Review 在既有 PR 上进行，不在开 PR 之前。）
- **修改范围:** 端到端编排；严格沿用 `delivery.py` 纪律（Draft PR 强制、永不 merge、幂等）；不触碰 `PROTECTED_REPOS`。
- **前置条件:** Phase B/D/E 完成且测试绿；凭据已落服务端 `.env`（用户操作）。
- **验收标准:** 真实 Draft PR 创建于 smoke-test 仓库；`main` 不变；无自动 merge；证据齐全可溯源；`USER_ACTION_REQUIRED` 正确触发并去重。
- **测试:** 既有 smoke-contract（round-1 基线、round-2 标签门、feedback marker）+ 协作层新增断言（事件/Canvas/搜索可溯源）。
- **风险:** 凭据暴露 → 沿用 B5/B11 脱敏与 `.gitignore`；越权写 → `ALLOWED_GITHUB_REPOS`/`PROTECTED_REPOS` 双重校验。
- **回滚:** 仅 Draft PR 未合并；保留证据，关闭 PR 即回滚。
- **是否需要用户操作:** 是（`SECRET_ENTRY`/`EXTERNAL_ACCOUNT_SETUP`/`FINAL_ACCEPTANCE` 等）。
- **是否阻塞下一阶段:** 是（验收通过方可进 G）。

### `Phase G` — 用户体验试用和迭代

- **目标:** 人类与多个 Agent 在 Buzz 工作空间真实试用，收集体验反馈并迭代协作外壳（频道/Canvas/Activity Feed/搜索）。
- **修改范围:** 以配置/模板/展示优化为主；不破坏权威边界。
- **前置条件:** Phase F 真实闭环通过，且 **Buzz 接入门禁（`BUZZ_ENV_READY`）通过**（真实 Buzz 实例可达、账号 / 连接就绪）。
- **验收标准:** 人类可在 `#user-action-required` 完成授权/验收；多角色协作无歧义；搜索与 Canvas 溯源清晰。
- **测试:** 用户体验走查 + 关键路径回归（不放松既有测试）。
- **风险:** 体验优化误改权威路径 → 任何权威变更须回 Phase A/B 评审。
- **回滚:** 配置/模板级变更，易回退。
- **是否需要用户操作:** 是（持续试用与反馈）。
- **是否阻塞下一阶段:** 否（持续迭代）。

---

## 3. 工作空间设计 (Workspace Design)

### 3.1 频道拓扑 (Channel Topology)

**默认频道 (Default Channels):**
`#control-room` `#planning` `#implementation` `#review` `#qa` `#release` `#user-action-required`

**临时频道 (Temporary Channels):**
`#task-<task-id>` —— 任务派发时创建，任务 `TASK_COMPLETED` 且验收后归档。

### 3.2 角色 × 频道 权限矩阵 (Role × Channel Matrix)

| 频道 | 可进入 (Enter) | 可发言 (Speak) | 只读 (Read-only) |
| --- | --- | --- | --- |
| `#control-room` | `Human Owner`, `Hermes Master / Boss`, `Planner / Scheduler` | 上述三者 | 其他角色（观察） |
| `#planning` | 全部 8 角色 | `Human Owner`, `Hermes Master / Boss`, `Planner / Scheduler`, `Documentation Agent` | 其余（观察） |
| `#implementation` | 全部 | `Coding Worker`, `QA Agent`, `Documentation Agent`, `Planner / Scheduler` | 其余（观察） |
| `#review` | 全部 | `Independent Reviewer`, `Planner / Scheduler`, `Human Owner` | 其余（观察，含 `Coding Worker` 不得自审发言） |
| `#qa` | 全部 | `QA Agent`, `Coding Worker`, `Planner / Scheduler` | 其余（观察） |
| `#release` | 全部 | `Release Agent`, `Planner / Scheduler`, `Human Owner` | 其余（观察） |
| `#user-action-required` | `Human Owner` + 发起角色 | `Human Owner`（响应）、发起角色（说明） | 其余（观察） |
| `#task-<task-id>` | 该任务相关角色（`Coding Worker`/`QA Agent`/`Independent Reviewer`/`Planner / Scheduler`/`Hermes Master / Boss`/`Human Owner`） | 任务相关角色 | 其他（观察） |

### 3.3 生命周期与映射 (Lifecycle & Mapping)

- **临时频道创建:** `TASK_STARTED` 时由控制层申请创建 `#task-<task-id>`。
- **归档:** `FINAL_ACCEPTANCE` 完成后方可触发 `TASK_COMPLETED` 并归档；归档前内容须已同步权威记录。
- **频道消息 → GitHub 映射:**
  - 计划/范围讨论 → `PLAN_UPDATED` 事件 + GitHub Issue 备注（须同步）。
  - 代码/测试结论 → `FILE_MODIFIED`/`TEST_FINISHED`/`COMMIT_CREATED` 事件 + GitHub Commit/Check。
  - 审查意见 → `REVIEW_FINDING` 事件 + GitHub Review 评论（须同步）。
  - 发布/验收 → `USER_ACTION_REQUIRED`(`FINAL_ACCEPTANCE`/`PRODUCTION_RELEASE`) + GitHub PR/Issue。
- **仅作协作记录 (Collaboration-only):** 过程性闲聊、临时澄清、未定结论——可留 Buzz/Canvas，但**不**写入 GitHub 权威。
- **必须同步进入 GitHub 权威记录 (Must sync to GitHub):** 架构决策、验收结论、最终证据、Review 结论、Draft PR、CI 结果。任何"必须同步"项若仅在 Buzz/Canvas 而缺 GitHub 记录，视为不完整。
- **不采用 Buzz"分支即频道"作为权威状态:** 可借鉴其交互设计，但频道生命周期与任务状态由 Hermes 控制层决定，不以 Buzz 分支状态为真相。

---

## 4. Canvas 模板 (Canvas Template)

Canvas 是**协作展示层**，12 字段如下（英文原名原样，全文档一致）：

`Goal` / `Scope` / `Out of Scope` / `Acceptance Criteria` / `Assigned Roles` / `Current Status` / `Evidence` / `Review Findings` / `Risks` / `User Actions Required` / `Decisions` / `Next Step`

**约束:**
- **协作展示层:** Canvas 便于人类与 Agent 共享动态视图，但**不得成为唯一证据源**。
- **同步要求:** `Decisions`（架构决策）、`Acceptance Criteria`/`Current Status` 中的验收结论、`Evidence` 中的最终证据 **必须同步**到 GitHub 文档/Issue/PR/Review。
- **可追溯更新:** Agent 更新 Canvas **必须**留 `actor`（来自 8 角色之一）与 `timestamp`；人类更新标注 `Human Owner`。
- **可重建性:** 重要内容应支持从 GitHub 权威记录**重新构建** Canvas（单向依赖：GitHub → Canvas，而非相反）。

---

## 5. 统一搜索与项目记忆 (Unified Search & Project Memory)

**覆盖源 (Sources):**
- Buzz 消息和线程 (`#channels`、线程)
- Canvas（12 字段）
- Hermes 状态事件（21 类 Activity 事件）
- GitHub：`Issue` / `Commit` / `Draft PR` / `CI Check` / `Review`
- 审计文档（位于复用审计分支 **PR #7** 的 `docs/audits/*`；本设计分支（PR #8）基于更早的 `d4-delivery-layer`，已**内联**复用结论，不重复依赖该路径。集成顺序见下文"PR 堆叠与 Rebase 顺序"。）
- 运行报告（`runtime/runs/*.jsonl`、交付/评审报告）

**检索证据优先级 (Evidence Priority — 全文档一致):**
`GitHub 真实记录 > Hermes 结构化状态 > Buzz 协作记录 > Agent 自然语言汇报`

**展示要求:**
- 每条搜索结果必须显示：**来源 (source)**、**时间 (time)**、**权限范围 (permission scope)**、**证据链接 (evidence link)**。
- **不得创建与 GitHub 冲突的第二套任务真相**：搜索索引是只读聚合，其权威值以 GitHub / Hermes 控制层为准；若 Buzz/Canvas 与 GitHub 不一致，以 GitHub 为准并标注差异。

---

## 6. ACP Agent Runtime Adapter 接口 (ACP Agent Runtime Adapter)

**接口名:** `AgentRuntimeAdapter`

**方法（英文原名原样，全文档一致）:**

| 方法 | 职责 |
| --- | --- |
| `start_session()` | 启动 Agent 运行时会话 |
| `submit_task()` | 派发任务（含受控授权与上下文） |
| `resume_session()` | 恢复任务上下文（断点续跑） |
| `cancel_task()` | 取消任务（可取消） |
| `get_activity()` | 拉取 Activity 事件（供 Feed） |
| `request_permission()` | 向控制层请求权限/用户授权 |
| `collect_result()` | 收集执行结果 |
| `terminate_session()` | 终止会话并清理子进程 |

**运行时 (Runtimes):**
- **第一版运行时:** `Open SWE`（保持上游基线 `langchain-ai/open-swe@ed12bb8d…`）。
- **预留候选:** `Codex`、`Claude Code`、`buzz-agent`、其他 ACP 兼容 Agent。

**约束:**
- Hermes 控制层**不得依赖单一 Agent 私有格式**；经 `AgentRuntimeAdapter` 解耦。
- 运行时必须：**可替换**、**可取消**、**可超时**、**可清理子进程**、**可恢复任务上下文**。
- **本轮不接入真实 `buzz-agent`**：仅完成接口定义与候选兼容性评估（对应 #7 可选 INTEGRATE）。

---

## 7. 受限 MCP Workspace Tools

**工具名（英文原名原样，全文档一致）:**

`workspace_get_task` / `workspace_get_context` / `workspace_post_update` / `workspace_update_canvas` / `workspace_search` / `workspace_get_pending_actions` / `workspace_request_user_action` / `workspace_post_review_result` / `workspace_get_evidence`

**职责速查:**

| 工具 | 用途 |
| --- | --- |
| `workspace_get_task` | 获取当前任务定义与状态 |
| `workspace_get_context` | 获取任务上下文（最小必要） |
| `workspace_post_update` | 发布进度更新（→ Activity 事件） |
| `workspace_update_canvas` | 更新 Canvas 字段（须带 actor/time） |
| `workspace_search` | 统一搜索（遵循 §5 优先级） |
| `workspace_get_pending_actions` | 获取待处理人工介入项 |
| `workspace_request_user_action` | 发起 `USER_ACTION_REQUIRED`（8 原因之一） |
| `workspace_post_review_result` | 提交 Review 结论 |
| `workspace_get_evidence` | 获取证据链接/溯源 |

**默认禁止提供 (Prohibited by Default):**
- 任意宿主机 Shell (`arbitrary host shell`)
- 任意文件系统访问 (`arbitrary filesystem access`)
- 任意 GitHub 仓库访问 (`arbitrary GitHub repo access` → 仅 `ALLOWED_GITHUB_REPOS`)
- 生产部署 (`production deployment` → Openship V1 不接入)
- 读取持久化密钥 (`reading persisted secrets`)
- 自动合并 (`auto-merge`)
- 修改默认分支 (`modifying default branch`)
- 绕过 Hermes 权限门禁 (`bypassing Hermes permission gates`)

**能力过滤:** MCP 工具**必须按 Agent 角色进行能力过滤**（见 `ROLE-AND-PERMISSION-MODEL.md` 权限矩阵）；例如 `workspace_post_review_result` 仅 `Independent Reviewer` 可写，`workspace_request_user_action` 仅具"请求授权"权限的角色可发，`workspace_update_canvas` 所有执行/控制角色可写但须带 actor。

---

## 8. 与既有边界的一致性 (Consistency)

- **仓库范围:** 所有 GitHub 访问受 `ALLOWED_GITHUB_REPOS` / `PROTECTED_REPOS` 约束；`yzhlx/hermes-learning-os` 永不自动化访问。
- **交付纪律:** Phase F 经 `delivery.py`：fail-closed、Draft PR 强制、永不 merge、幂等。
- **返工上限:** `REWORK_STARTED` 受 `MAX_ROUNDS=2` 与 `ROUND2_LABEL` 约束。
- **凭据隔离:** 令牌仅 push 步骤局部注入并丢弃；MCP 默认不暴露密钥。
- **无第二事实源:** 频道/Canvas/搜索/Buzz 均为协作/聚合层；权威结论回 GitHub 或控制层。
- **不开发 HLO:** Hermes Learning OS 当前未激活（Activation Gate 6 前置），本阶段不开发。

## 9. PR 堆叠与 Rebase 顺序 (Stacked PR Integration Order)

本设计（PR #8）与复用审计（PR #7）为**堆叠 PR**，共同基准为 `d4-delivery-layer`：

1. **PR #7（审计）先合并**进入共同基准 `d4-delivery-layer`；
2. **PR #8（设计）随后 rebase / merge 到更新后的基准**再合并；
3. 合并前 PR #8 须 rebase 到 PR #7 合并后的 `d4-delivery-layer` HEAD，解决任何冲突；
4. PR #6（`d4-delivery-layer`→`d3-design`）、PR #7、PR #8 均保持 **Draft / 不合并**，直至主理人按此顺序推进。

设计文档已内联审计结论，故 PR #8 不硬依赖 `docs/audits/*` 路径存在；若需引用原始审计文档，须先完成步骤 1–2。
