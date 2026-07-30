# Hermes 智能体工程工作台实施计划

日期：2026-07-28
基线：`codex/hermes-workbench-mvp` @ `3612b5263419`
状态：历史计划快照；当前实现与阻塞状态以根目录 `PROGRESS.md`、`BLOCKED.md` 为准

## 1. 目标与事实边界

本计划服务于 Hermes Open SWE Lab 的 smoke-test-only 工作台：在不另造第二套控制面或工程事实源的前提下，验证隔离的 Open SWE 工程闭环。Learning OS 继续保持未授权。

唯一目标链路：

```text
Human Owner 命令
  -> 现有 ControlPlane / jobs / events / workers
  -> 真实 Coding Agent / Reviewer / QA / Release
  -> GitHub Issue / Branch / Commit / Draft PR / CI / Review
  -> Human 最终验收
  -> Human 在 GitHub 手动 Merge
```

必须保持：

- `jobs` 是现有任务队列和当前投影，不新建第二个任务库。
- `events` 是现有编排事件流，不使用 `agents.json`、浏览器本地消息或静态 JSON 作为运行事实。
- `workers.last_heartbeat` 是 Worker 心跳观测源；在线状态由时间阈值动态计算。
- GitHub 是 Issue、Branch、Commit、PR、CI、Review、Merge 的唯一工程事实源。
- 工作台不得提供、调用或间接触发 Merge。
- Mock、Fake、Fixture、结构测试和 Demo 只能作为测试证据，不能升级为真实运行验收。
- `yzhlx/hermes-learning-os` 当前仍受保护；未获得明确授权前状态固定为 `BLOCKED_USER_AUTHORIZATION`。
- 任何外部凭据缺失都必须返回 `BLOCKED`，不得用模拟 Commit、PR、CI 或 Agent 台词补位。

## 2. 当前内核复用地图

| 现有路径 | 复用职责 | 必须修正的最小问题 |
|---|---|---|
| `hermes_worker/db.py` | `jobs/events/workers/issue_tasks`、SQLite WAL | 增量迁移；显式事务；事件来源字段 |
| `hermes_worker/control_plane.py` | Job 创建、Claim、Lease、Event、读取 | 收口任意 `set_state`；原子状态+事件；Operator Action |
| `hermes_worker/protocol.py`、`constants.py` | Worker 与状态常量 | 统一散落状态；保留兼容映射 |
| `hermes_worker/scheduler.py` | CI、Reviewer、返工轮次 | GitHub 实时验收门；Reviewer 意见真实回流；可恢复单步调度 |
| `hermes_worker/codex_job_runner.py` | 真实 Host Codex、测试、Commit、Push、Draft PR | `PR_CREATED` 不得等于任务完成；安全暂停检查点 |
| `hermes_worker/github_client.py` | GitHub 读写适配 | 移除生产 Merge 能力；工程事实带外部 ID/Head SHA |
| `hermes_worker/worker_api_server.py` | Worker 认证、nonce、keepalive、token broker | 与 Cloud 入口收敛，不能保留强弱两套生产 API |
| `deploy/cloud/control_plane_app.py` | Loopback HTTP、health/readiness、部署入口 | 增加同源 Operator API；继承已有安全门 |

## 3. 执行规则

每个 Task 均按以下顺序执行：

1. 只写该 Task 指定的红测，确认因目标能力缺失而失败。
2. 提交最小实现，不提前实现下一 Task。
3. 跑目标测试至绿。
4. 跑 Windows 非部署回归：

```powershell
python -B -m pytest -q -p no:cacheprovider --ignore=tests/deployment
```

5. 记录真实证据到 `PROGRESS.md`；出现门禁则更新 `BLOCKED.md`。
6. 不得通过放宽断言、伪造 GitHub 数据、硬编码 Agent 消息或跳过失败测试变绿。

## 4. 可执行 TDD 任务表

| Task | 文件、函数或表 | 红测命令 | 最小实现 | 验证命令 | 回滚或阻塞标准 |
|---|---|---|---|---|---|
| **0. 基线与边界冻结（已完成）** | 只读核对 `db.py`、`control_plane.py`、`scheduler.py`、`protocol.py`、Cloud 入口、测试与运行环境 | 不新增红测；使用现有收集和反向探针 | 不改生产代码；把真实现状、失败和门禁写入文档 | `python -B -m pytest -q -p no:cacheprovider --ignore=tests/deployment`；三组核心目标测试；环境只读探针 | HEAD、分支或工作树不符立即停止；不得在 Task 0 修改生产代码 |
| **1. Schema 增量迁移与统一状态定义** | `hermes_worker/db.py::init_db`、`SCHEMA`；`protocol.py::JobState`；`constants.py`；`jobs/events` | `python -B -m pytest -q -p no:cacheprovider tests/test_operator_state_machine.py -k "migration or schema or canonical_states"` | 用 `PRAGMA user_version` 做可重复迁移；`jobs` 增加 `version/updated_at/control_state/requirements_revision/workflow_kind`；`events` 增加 nullable 的 actor/source/correlation/schema 字段；统一现有状态并保留旧值兼容 | 目标测试；随后跑非部署 106 项基线命令 | 旧 DB 不能无损打开、迁移不可重复、现有 Job 丢失或另建第二 DB 时回滚；禁止只改 `CREATE TABLE IF NOT EXISTS` 假装完成迁移 |
| **2. 原子状态迁移与事件写入** | `control_plane.py::set_state/update_job/create_issue_task/post_events/append_event/get_events`；新增 `transition_job()`、`get_events_after()` | `python -B -m pytest -q -p no:cacheprovider tests/test_operator_state_machine.py -k "illegal_transition or atomic or stale_version or idempotent or cursor"` | `BEGIN IMMEDIATE` 内完成状态校验、唯一事件、CAS `version` 更新和提交；修复 `create_issue_task(task_id=...)` 未落库；时间线以 `events.id` 为 Cursor，停止把每批重置的 `seq` 当全局顺序 | 目标测试；`tests/test_d3_closed_loop.py tests/test_d3_security.py tests/test_codex_host_flow.py`；非部署回归 | 任意字符串仍能进入 `state`、状态与事件可分裂、重复请求产生双事件、现有 Claim 幂等性退化时回滚 |
| **3. Operator 任务创建与只读投影 API** | 新增薄应用层 `hermes_worker/operator_api.py`；`control_plane.py::create_operator_task/get_task_projection/list_worker_projections`；`deploy/cloud/control_plane_app.py` | `python -B -m pytest -q -p no:cacheprovider tests/test_operator_api.py -k "auth or create_task or list_tasks or get_task or event_cursor or repo_allowlist"` | 增加 `POST /operator/v1/tasks`、任务列表/详情/增量事件、Worker 列表；只调用现有 `ControlPlane` 和同一 SQLite；返回 `202 + task_id/job_id/version/event_cursor`；未知 GitHub 关联明确显示 pending，不伪造 Issue/PR | 目标 API 测试；非部署回归；确认无新 JSON 状态文件 | API 绕过 ControlPlane、Operator 和 Worker 共用凭据、未授权 Repo 可入队、页面缓存成为事实源时回滚 |
| **4. Pause / Resume / Supplement 真实控制** | `control_plane.py::apply_operator_action/ack_pause/keepalive/claim/claim_job`；`codex_job_runner.py::_transition/_lease_loop/run`；`scheduler.py` 检查点 | `python -B -m pytest -q -p no:cacheprovider tests/test_operator_actions.py -k "pause or resume or supplement or lease or safe_checkpoint"` | 独立 `control_state: active -> pause_requested -> paused -> active`；运行中 Pause 先返回 `202`，Worker 安全检查点确认后才释放 Lease；Supplement 追加事件并增加 Requirements Revision，不覆盖原命令；Claim 排除 paused Job | 目标测试；D3 Security 与 Codex Host Flow；非部署回归 | 子进程仍运行却显示“已暂停”、Resume 从非安全阶段重放 Push、Supplement 未进入下一轮有效指令时回滚；MVP 不具备中断能力时只能标记“暂停请求中” |
| **5. Reject / Approve 与集中 Acceptance Gate** | `scheduler.py::review_phase`；新增 `AcceptanceGate.evaluate()`；`control_plane.py::apply_operator_action`；`reviewer.py` | `python -B -m pytest -q -p no:cacheprovider tests/test_acceptance_gate.py -k "stale_head or ci or blocking_finding or independent_reviewer or approve or reject"` | Reject 仅从 `await_user/ready_for_manual_merge` 回到同一 PR 的返工态；Approve 先写请求事件，再由 Gate 读取 GitHub 当前 PR Head、同 SHA CI、同 SHA 独立 Review 且无 blocking finding，成功后进入 `ready_for_manual_merge` | 目标测试；`tests/test_d3_closed_loop.py`；非部署回归 | 客户端可直接写验收态、调用者注入 `ci_status=success` 可绕过 GitHub、APPROVE 携带 blocking finding 仍通过时回滚 |
| **6. 真实 Codex、Reviewer 与同 PR 返工闭环** | `scheduler.py::WorkerAgent.run_phase/D3Orchestrator.run_job`；建议收敛为可恢复 `run_once()`；`codex_job_runner.py::finish_state` 调用点；`agent_runner.py` | `python -B -m pytest -q -p no:cacheprovider tests/test_workbench_closed_loop.py -k "real_event_flow or review_feedback or same_pr or crash_resume or retry_budget"` | 指定 Job 使用 `claim_job()`；`PR_CREATED` 只结束 Worker 阶段，不写任务 `ended_at`；Reviewer 完整意见和 Human Supplement 进入下一轮 Agent 指令；同 Branch、同 Draft PR 更新 Head；CI 失败有预算并可恢复 | 目标闭环测试；三组既有核心测试；非部署回归 | 第二个 PR、第二套队列、重新使用原始提示词而忽略 Review、进程重启后轮次回到 1、模拟 SHA 被当真时回滚 |
| **7. GitHub 工程事实投影与无 Merge 硬边界** | `github_client.py`；`webhook_receiver.py`；`event_router.py`；`constants.py`；GitHub 证据投影 | `python -B -m pytest -q -p no:cacheprovider tests/test_github_fact_boundary.py -k "head_sha or check_run or review_id or merged_webhook or no_merge_capability"` | 每个 GitHub 观测事件带外部 ID、Repo、Head SHA、URL、`observed_at`；任务只在 `pull_request` Webhook 确认 `merged=true` 后进入 `completed`；生产 Client、Operator API 和 Agent 工具均不暴露 Merge；保留 Repo allowlist | 离线 Fake 合同测试；无凭据时 Live 测试必须返回 BLOCKED 且零请求；非部署回归 | 发现 `merge_pr` 可调用路径、Worker `/complete` 可写 TASK_COMPLETED、缓存 CI 被当当前事实、GitHub App 权限未就绪时阻塞 |
| **8. 在线、心跳、阶段与缓存语义** | `control_plane.py::heartbeat/keepalive/list_worker_projections`；`deploy/cloud/control_plane_app.py` 状态端点；投影测试 | `python -B -m pytest -q -p no:cacheprovider tests/test_operator_status_projection.py -k "heartbeat_age or partial_online or disconnected or cached or current_step"` | 分离连接状态和执行状态；动态返回来源、`observed_at`、最后心跳、心跳年龄、Lease、当前 Job/Step；后端实时 DB、GitHub 观测和前端缓存分别标注；禁止 `live_partial` 显示成离线缓存 | 目标测试；冻结时间边界测试；非部署回归 | 仅凭端口判在线、终态 Job 仍显示当前任务、无时间戳或缓存年龄、状态名互相覆盖时回滚 |
| **9. 中文 Buzz 风格工作台 UI** | 新增 `web/workbench/index.html`、`app.js`、`styles.css`；`deploy/cloud/control_plane_app.py` 同源静态入口；`tests/test_workbench_static.py`、`tests/playwright/test_workbench.py` | `python -B -m pytest -q -p no:cacheprovider tests/test_workbench_static.py tests/playwright/test_workbench.py` | 真实命令输入；任务线程；七个频道投影；Agent 真实事件；GitHub 证据链接；在线/缓存信息；Pause、Resume、Supplement、Reject、Approve 控件；刷新后从 Event Cursor 恢复；无数据时显示诚实空状态 | Python 静态/API 测试；Playwright Chromium 截图、交互和 Console Error 检查；非部署回归 | 使用硬编码 Agent 台词、截图背景、浏览器本地状态冒充持久化、控件未调用真实 API、无障碍或中文主路径不可用时回滚 |
| **10. 单一强化服务入口与稳定访问** | `worker_api_server.py::make_handler/run_server`；`deploy/cloud/control_plane_app.py::load_config/guard_startup/make_handler`；部署契约与部署测试 | `python -B -m pytest -q -p no:cacheprovider tests/test_operator_security.py tests/deployment -k "loopback or auth or replay or allowlist or body_limit or no_secret_log"` | 收敛 Worker 与 Operator 路由到同一强化 Handler；保留 loopback、TLS 反代、nonce、Worker allowlist、keepalive、token broker；Operator 独立身份、同源和请求大小限制；浏览器关闭不终止后台任务 | Windows 可运行安全测试；Linux/WSL 运行 deployment 测试；健康、就绪和重启恢复验证 | 出现强弱两套生产 Server、公开明文监听、凭据进入日志/响应、后台依赖浏览器、WSL 测试环境未就绪时阻塞相关验收 |
| **11. Smoke Repo 真实纵向验收** | 环境门禁；Live 测试；现有白名单 `yzhlx/hermes-open-swe-smoke-test` | `python -B -m pytest -q -p no:cacheprovider -m live tests/live/test_workbench_smoke_loop.py` | 真实命令创建任务；真实 Agent Commit；Draft PR；CI；独立 Review 提出至少一项问题；同 PR 返工；再次 Review；到达 `await_user/ready_for_manual_merge`；明确不 Merge | 保存 Issue、Commit SHA、Draft PR、Check Run、Review 和事件 Cursor 证据；确认 Merge 未发生 | 三个 GitHub App 环境变量未继承时状态必须是 BLOCKED 且零外部写入；任何 Mock/`simulated` SHA、自动 Merge 或第二 PR 均判 FAIL |
| **12. Hermes Learning OS 低风险试点** | `constants.py::ALLOWED_GITHUB_REPOS/PROTECTED_REPOS`；独立 worktree；经用户确认的目标文件与验收测试 | 授权前只运行边界测试：`python -B -m pytest -q -p no:cacheprovider tests/test_scope_boundaries.py -k "learning_os_protected"` | **当前不实施。** 获得 Human Owner 明确授权后，先固定 Repo、Base、功能范围、Worktree、验收标准和回滚；再仅对白名单分支完成一条真实闭环并停在手工 Merge 前 | 授权后的功能目标测试、全量相关测试、Playwright、真实 GitHub 证据链；Human 最终验收 | 当前固定为 `BLOCKED_USER_AUTHORIZATION`；未经授权不得删除保护项、Clone、Push、创建 Issue/PR 或修改 Learning OS |

## 5. 事件与投影合同

Operator 事件响应的最小字段：

```json
{
  "cursor": 123,
  "event_id": "operator:request-id",
  "schema_version": 1,
  "task_id": "task_xxx",
  "job_id": 42,
  "type": "review.completed",
  "channel": "review",
  "occurred_at": 1785200000.0,
  "actor": {"type": "agent", "id": "wk_xxx", "role": "reviewer"},
  "source": {
    "kind": "github",
    "ref": "review:12345",
    "authority_domain": "github_review",
    "observed_at": 1785200000.0
  },
  "correlation_id": "task_xxx",
  "causation_id": "event_xxx",
  "task_version": 12,
  "payload": {},
  "evidence": []
}
```

频道只做确定性投影，不新建消息事实库：

| 频道 | 事件类型示例 |
|---|---|
| `control-room` | task created、state changed、escalated |
| `planning` | plan、acceptance criteria、requirements added |
| `implementation` | Agent run、changed files、Commit、Push |
| `review` | Review started、finding、verdict、re-review |
| `qa` | local test、CI Check Run、quality gate |
| `release` | Branch、Draft PR、ready for manual merge、merged observation |
| `user-action-required` | authorization、ambiguity、final acceptance、blocked |

## 6. 最终验收条件

只有同时满足以下条件，平台 MVP 才能标记 PASS：

- 稳定中文网页可输入真实任务，不依赖终端、VBS 或手工 JSON 同步。
- 任务进入现有 ControlPlane，并产生持久 `task_id/job_id`。
- 至少 Hermes Master、Coding Agent、Independent Reviewer、Release/QA 的真实事件可见。
- Commit、Draft PR、CI、Review、返工均有 GitHub 外部证据。
- Reviewer 意见真实回到 Coding Agent，并在同一 PR 更新。
- Pause、Resume、Supplement、Reject、Approve 均通过合法状态迁移。
- 在线、部分在线、中断、缓存、等待 Agent、等待 Human 可明确区分。
- 最终状态停在等待 Human 手工 Merge；系统没有任何自动 Merge 能力。
- Smoke Repo 完成一次真实闭环。
- Learning OS 试点只有在单独授权后才能计入最终主项目验证。
