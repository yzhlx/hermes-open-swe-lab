# Draft PR Delivery Code Reconnaissance

> 只读侦察报告 · 目标工作区 `F:/project/hermes-open-swe-lab_codex`
> 目的：为"Host Worker GitHub Draft PR 受控交付层"主力实现提供可复用点，判断是否重复造轮子。
> 侦察方式：仅读取，未修改任何文件、未创建 commit/push/PR、未切换分支、未运行 WSL Smoke。

---

## 1. Repository State

- **Branch:** `codex-primary-agent-policy`
- **HEAD:** `3612b5263419609a709f21c46c427ff036c397da` (`feat: run codex executor through wsl2`)
- **Workspace clean:** `nothing to commit, working tree clean` ✅
- Ahead of `origin/d3-integration` by 5 commits (本地领先，未推送——侦察未触碰)。
- 关键现有包 `hermes_worker/` 已经有完整的 **Issue→Job→Codex→Docker test→commit→push→Draft PR→Reviewer→round-2** 闭环（`scheduler.py` + `codex_job_runner.py`）。**主力 Agent 极可能已经存在大量可直接复用的交付能力，重复造轮子的风险很高。**

---

## 2. Existing Reusable Components

### Git and repository validation
- **File:** `hermes_worker/repository.py`
- **Lines / Symbols:**
  - `TOKEN_ENV = "HERMES_GIT_INSTALLATION_TOKEN"` — L24
  - `_GIT_ENV_ALLOWLIST` — L25–41（严格白名单环境隔离）
  - `RepositoryPreparer.prepare()` — L196–307（git init + 认证浅 fetch；失败即清理工作树）
  - `HostGitOperations.changed_files()` — L311–334（`git status --porcelain=v1 -z`，过滤 `.hermes/`）
  - `HostGitOperations.commit()` — L336–364（`git add --all` + 无 staged 变更则 `CommitResult.created=False` + 校验 40 位 SHA）
  - `HostGitOperations.push()` — L366–388（AskPass 一次性凭证，URL/参数/配置均不含 token）
  - `_askpass()` — L154–193（临时 helper，finally 中清零并删除）
- **Current behavior:** 凭证仅驻留内存与一次性 AskPass 脚本，强制 `GIT_CONFIG_NOSYSTEM=1`、`GIT_CONFIG_GLOBAL=/dev/null`；ref 名正则校验（`_SAFE_REF_RE`）；目标目录不能在已有 git 仓库内；fetch 失败整体清理。
- **Reuse recommendation:** ✅ **直接复用**。`RepositoryPreparer` + `HostGitOperations` 已是完全实现的受控 Git 层，比任何新写版本都更安全。
- **Risks:** `RealGitHubClient.clone/push/create_draft_pr` 已被显式禁用（L148–163），只能走 `RepositoryPreparer`/`HostGitOperations` + `GitHubRestClient`，不要回退到旧路径。

### GitHub access
- **File:** `hermes_worker/github_client.py`
- **Lines / Symbols:**
  - `GitHubClient`（接口）— L27–46
  - `FakeGitHubClient` — L49–132（离线闭环测试用，含 `create_draft_pr`/`get_pr`/`get_ci_status`/`has_label`/`ci_map`/`label_map`/`pr_numbers`）
  - `RealGitHubClient` — L135–186（legacy，clone/push/PR 全部 raise禁用）
  - `GitHubRestClient.create_draft_pr()` — L187–231（**真实生产路径**：GitHub REST `POST /repos/{repo}/pulls`，draft=True，token 仅作 header）
  - `_check_repo()` — L65–67（allowlist 兜底）
- **Current behavior:** `GitHubRestClient` 是唯一真实 Draft PR 创建入口；内部再次校验 `repo not in ALLOWED_GITHUB_REPOS`。
- **Reuse recommendation:** ✅ **直接复用** `GitHubRestClient`。`FakeGitHubClient` 用于测试。
- **Risks:** `RealGitHubClient` 是历史包袱，**不应复用**（已禁用）。`create_draft_pr` 返回后 `codex_job_runner` 会二次断言 `pr.get("draft")==True`（L504）——这一 fail-closed 检查应保留。

### Credential provider
- **File:** `hermes_worker/github_app.py`
- **Lines / Symbols:**
  - `GitHubAppTokenBroker` — L32–134
  - `mint_installation_token()` — L80–97（进程内缓存 + 刷新）
  - `get_token_for_job()` — L102–121（**lease-gated 交付**：校验 worker 持有lease + job 处于 active 态 + repo allowlist，交付后立即从缓存清零）
  - `RealAppApiClient.exchange_installation_token()` — L144–184（JWT→Installation Token，真实网络）
  - `FakeAppApiClient` — L187–206（离线）
  - `redact_for_export()` — L132–134（token 永不出导出）
- **Current behavior:** 短时效、按 repo 作用域的 Installation Token；token 永不入 SQLite/JSONL/日志；交付后零化。
- **Reuse recommendation:** ✅ **直接复用** `GitHubAppTokenBroker.get_token_for_job` 作为唯一凭证发放点。
- **Risks:** 依赖 `ControlPlane._check_token` / `_get_job` 私有方法（L108–109）——复用需接受对 `control_plane` 内部 API 的耦合，或请主力把这两个方法提升为公共接口。

### Authorization gate
- **File:** `hermes_worker/github_app.py` + `hermes_worker/control_plane.py`
- **Lines / Symbols:**
  - `get_token_for_job` 的 lease 校验 — `github_app.py` L102–121
  - `ControlPlane._check_token()` — `control_plane.py` L114–120（未知 token → `unknown_worker_token`）
  - `ControlPlane.keepalive()` / `claim_job()` 的 `job_not_owned_by_worker` 校验 — L240–256、L324–355
  - **当前"用户授权"模型是 fail-closed 的"事后授权"**：PR 创建后 job 进入 `await_user`/`PR_CREATED` 终态，**绝不自动 merge**（见 `scheduler.py` L175–179 与 `test_10_no_merge_called`）。
- **Current behavior:** 写操作授权 = "worker 持有 job lease 且 job 处于 active 态"。用户审批是"留给用户手动 merge"，不是代码内 pre-PR gate。
- **Reuse recommendation:** ✅ lease-gated 授权可直接复用。**但若主力要的是"创建 Draft PR 之前需要显式用户授权 token"这种 pre-PR 闸门，目前不存在**，需新增（见 §3）。
- **Risks:** 无显式 pre-PR 用户授权 gate；`await_user` 仅是终态占位。

### Repository allowlist
- **File:** `hermes_worker/constants.py` + 多处 `_check_repo`
- **Lines / Symbols:**
  - `ALLOWED_GITHUB_REPOS = {"yzhlx/hermes-open-swe-smoke-test"}` — L12
  - `PROTECTED_REPOS = {"yzhlx/hermes-learning-os"}` — L15（**仅文档性声明**，未在任何运行时校验函数里被引用）
  - 校验点：`repository.py` L212、L289；`github_client.py` L62、L199；`github_app.py` L81、L115；`event_router.py` L57；`webhook_receiver.py` L31、L95
- **Current behavior:** 默认拒绝——任何不在 `ALLOWED_GITHUB_REPOS` 的 repo 直接抛 `repo_not_allowed`。保护仓库因不在白名单而被拒（靠默认拒绝，而非显式保护集校验）。
- **Reuse recommendation:** ✅ **直接复用**单一 allowlist 常量，不要新增第二份。
- **Risks:** `PROTECTED_REPOS` 未被运行时引用——若主力想"硬失败 + 明确审计保护仓库触碰"，需补一个显式守卫（见 §3）。当前靠默认拒绝已足够安全，但行为不完整（无显式拦截日志）。

### Job state machine
- **File:** `hermes_worker/constants.py` + `hermes_worker/control_plane.py`
- **Lines / Symbols:**
  - `HOST_WORKER_ACTIVE_STATES` — `constants.py` L31–38（running / REPOSITORY_PREPARING / CODEX_RUNNING / TESTING / COMMITTING / PUSHING）
  - `HOST_WORKER_TERMINAL_STATES` — `constants.py` L40–46（CODEX_FAILED / CODEX_NO_CHANGES / TEST_FAILED / PR_CREATED / BLOCKED）
  - `ControlPlane.finish_state()` — L428–446（**fail-closed**：非终态直接抛 `invalid_terminal_state`；`ended_at` 已置则幂等返回）
  - `ControlPlane.set_state()` — L208–211
  - 异常捕获 → `BLOCKED`：`codex_job_runner.py` L267–272、L525–530
- **Current behavior:** 明确的状态枚举 + 终态门禁 + 异常统一降级为 `BLOCKED`（fail-closed）。
- **Reuse recommendation:** ✅ **直接复用并扩展**状态常量；新增交付态时加入同一枚举即可。
- **Risks:** `finish_state` 校验的是 `HOST_WORKER_TERMINAL_STATES`，若主力新增"受控交付"专属终态（如 `DELIVERY_APPROVED`）必须登记到该元组，否则 `finish_state` 会拒绝。

### Idempotency and persistence
- **File:** `hermes_worker/db.py` + `hermes_worker/control_plane.py`
- **Lines / Symbols:**
  - `issue_tasks` 表 PK `(repo, issue_number)` — `db.py` L83–89（**一个 issue 一个 job**，重复 webhook 折叠）
  - `deliveries` 表 PK `delivery_id` — `db.py` L71–74（`record_delivery()` 去重，L134–148）
  - `events.event_id` UNIQUE — `db.py` L52（`post_events` 幂等，L357–384）
  - `nonces` 表 PK — `db.py` L66–69（replay 防护，`check_replay` L89–112）
  - `create_issue_task()` 返回 `(job_id, created)` — L167–196（原子 upsert）
  - `claim()` 重领同一 job — L281–321；`complete()`/`fail()` 终态幂等 — L448–463
  - `codex_job_runner.run()` 的 `expected_delivery` 校验 — L297–303（delivery 不匹配 → `BLOCKED`）
- **Current behavior:** 多层幂等：issue 维度、delivery 维度、event 维度、nonce 维度、终态维度。
- **Reuse recommendation:** ✅ **直接复用**整套幂等基设；"受控交付层"的重复执行保护已现成。
- **Risks:** `run()` 的 delivery 校验只比对 `payload.delivery_id`，若主力改用新的交付触发键，需同步 `create_issue_task` 的入参契约。

### Secret handling
- **File:** `hermes_worker/redact.py` + `hermes_worker/codex_cli_runner.py`
- **Lines / Symbols:**
  - `redact()` — `redact.py` L43–65（URL 内凭证 / GitHub PAT / App PAT / Bearer / sk- / 通用赋值 全量脱敏）
  - `redact_headers()` — L68–80；`redact_secret_env_value()` — L83–94
  - `CodexCliRunner._child_environment()` + `FORBIDDEN_ENV_MARKERS` — `codex_cli_runner.py` L169–182、L32–43（子进程环境显式白名单 + 危险变量名剔除）
  - `CodexCliRunner._sanitize_json()` — L98–111（事件流敏感键脱敏）
  - `RepositoryPreparer` 的 AskPass 清零 + 临时目录删除 — `repository.py` L154–193、L278–307
- **Current behavior:** 日志/命令/子进程环境三层脱敏；凭证仅在内存与一次性 AskPass 文件。
- **Reuse recommendation:** ✅ **直接复用** `redact` 全家桶与 `CodexCliRunner` 的环境隔离。
- **Risks:** **没有对 PR diff 本身做主动 secret scan**。`reviewer.py` 的 `secrets_in_diff` 只是一个由调用方传入的 evidence 标志（`scheduler.py` L162，目前硬编码 `False`），并非真实扫描器。若主力交付层要"扫描 diff 中的密钥并阻断"，这是缺口（见 §3）。

### Test utilities
- **File:** `tests/deployment/conftest.py` + 多个 `tests/test_*.py` + `Fake*` 类
- **Lines / Symbols:**
  - `free_port()` / `start_control_plane()` / `wait_for_health()` / `build_git_repo()` / `commit()` — `conftest.py` L17–105
  - `FakeGitHubClient`（`github_client.py`）、`FakeAppApiClient`（`github_app.py` L187）、`FakeAgentRunner`（`agent_runner.py` L39）、`EchoSandboxBackend`（`echo_sandbox.py`）
  - `TrackingWorker` / `CountingReviewer` 测试辅助 — `test_d3_closed_loop.py` L327–385
  - 大量离线断言样例（D3 闭环 13 条不变量）— `test_d3_closed_loop.py` L110–324
- **Current behavior:** 完整的离线 Fake 套件 + 闭环测试脚手架，无需真实 GitHub/网络/凭证。
- **Reuse recommendation:** ✅ **直接复用**所有 Fake 与 `conftest` 工具，新交付层测试应沿用同一离线范式。
- **Risks:** 无。

---

## 3. Missing Components

以下为当前代码**确实没有**、主力实现"受控交付层"若需要则必须新增的能力：

1. **Pre-PR 显式用户授权闸门**：当前只有"事后 await_user（用户手动 merge）"，没有"创建 Draft PR 之前必须持有一个显式 user-approval token / 标志"的代码内 gate。
2. **PR diff 主动 secret scanner**：`secrets_in_diff` 是传入标志而非真实扫描；缺少"读取 diff 文本并用 `redact.py` 规则判定是否含密钥并阻断"的函数/模块。
3. **与 Codex 解耦的独立"受控交付"入口**：`CodexJobRunner.run()`（L277–533）把 codex→test→commit→push→PR 全耦合在一起。若主力想要"给定已准备好的 repo + commit_sha，仅做受控 push + Draft PR（带 pre-PR 闸门与幂等）"的独立可调用层，目前不存在。
4. **显式 PROTECTED_REPOS 运行时守卫**：`constants.py` L15 的 `PROTECTED_REPOS` 未被任何函数引用；保护仓库靠 allowlist 默认拒绝生效，但没有"命中保护仓库 → 明确审计/硬失败"的显式分支。
5. **交付层专属状态/标签**（如 `DELIVERY_APPROVED`、`pre_pr_gate` 标签）需要在 `constants.py` 注册才能被 `finish_state` 接受。

> 注：纯"GitHub 写操作 / push / Draft PR / 幂等 / 凭证 / 脱敏 / 状态机"均已存在，**不要重写**。缺口集中在"pre-PR 授权"与"diff secret scan"两点。

---

## 4. Likely Conflict Files

主力 Agent 最可能修改的现有文件及原因：

| 文件 | 冲突原因 |
|------|----------|
| `hermes_worker/codex_job_runner.py` | 交付主流程在此；新增"受控交付"很可能在此插入 pre-PR 闸门或抽取独立交付函数，与现有 `run()` L445–524 重叠。 |
| `hermes_worker/repository.py` | 若主力想加"diff secret scan"或改造 push 前的检查，可能在此动刀——但应优先复用，仅扩展。 |
| `hermes_worker/constants.py` | 需新增交付态/标签到 `HOST_WORKER_TERMINAL_STATES` 与可能的 `PROTECTED_REPOS` 守卫；多 Agent 并发改此文件易冲突。 |
| `hermes_worker/control_plane.py` | 若新增 pre-PR 授权态或显式保护仓库校验，会触及 `finish_state` / `create_issue_task` / 私有 `_check_token`。 |
| `hermes_worker/scheduler.py` | 若"受控交付"被纳入 orchestrator 闭环，会与此处 `WorkerAgent.run_phase` / `Scheduler.review_phase` 重叠。 |
| `hermes_worker/github_client.py` | 可能想加真实 PR 描述/label/注解；注意 `RealGitHubClient` 已禁用，只能扩展 `GitHubRestClient`。 |
| `hermes_worker/redact.py` 或 新建 `secret_scan.py` | diff secret scan 的落点。 |

**最高冲突风险：`codex_job_runner.py` 与 `scheduler.py`**——两者已包含完整的 commit/push/PR/round-2 逻辑，主力最可能在这两个文件"再包一层"，务必先复用而非重写。

---

## 5. Minimal Implementation Boundary

最小合理新增文件范围（**不写代码，仅界定边界**）：

1. `hermes_worker/draft_pr_delivery.py`（新建）
   - 单一受控交付函数：输入 `(cp, job_id, worker_token, repo, base, task_branch, commit_sha, prepared_repo_path, broker, github_client)`。
   - 内部按顺序：**repo allowlist 校验 → lease/active 态校验 →（可选）pre-PR user-approval 校验 → diff secret scan → 幂等检查（按 repo+issue 已有 pr_number 复用）→ push（AskPass）→ GitHubRestClient.create_draft_pr → 终态登记**。
   - 直接从 `repository.HostGitOperations` / `github_app.GitHubAppTokenBroker` / `github_client.GitHubRestClient` import，**不重复实现 Git/凭证/PR 调用**。

2. `hermes_worker/secret_scan.py`（新建，极小）
   - `scan_diff_for_secrets(diff_text: str) -> list[str]`：复用 `redact.redact` 判定是否触发，返回命中的脱敏后摘要。供交付层在 push 前阻断。

3. `tests/test_draft_pr_delivery.py`（新建）
   - 沿用 `FakeGitHubClient` + `FakeAppApiClient` + `conftest.build_git_repo` 离线范式，断言：pre-PR 闸门拒绝/放行、secret scan 阻断、幂等复用同一 PR、token 不入 DB。

4. `constants.py` 小幅扩展（非新建文件）
   - 在 `HOST_WORKER_TERMINAL_STATES` 增加交付专属终态；如采用显式保护仓库守卫，则在某运行时校验点引用 `PROTECTED_REPOS`。

**不应修改**：`worker.py`（Echo 协议测试）、`webhook_receiver.py` / `event_router.py`（入口路由已稳定）、`db.py` SCHEMA（幂等表已齐备，除非确需新表）、`reviewer.py`（独立审查角色，与交付层正交）、`protocol.py` / `docker_sandbox.py` / `echo_sandbox.py`（沙箱隔离，无关）、`codex_cli_runner.py`（环境隔离已完备）。

---

## 6. Safety Confirmation

- **Files modified:** 0（本侦察未修改 `hermes-open-swe-lab_codex` 内任何文件；本报告写入主工作区 `hermes-open-swe-lab/DRAFT-PR-DELIVERY-RECON.md`，非侦察目标仓库，且未提交。）
- **Commit created:** 否
- **Push performed:** 否
- **PR modified:** 否
- **WSL Smoke executed:** 否（未运行任何命令触发 smoke）
- **Protected repository accessed:** 否（`yzhlx/hermes-learning-os` 仅作为 `PROTECTED_REPOS` 文档常量被读取，未 clone/访问/修改）

---

### 结论（给主力 Agent）

**高度可能重复造轮子。** 受控交付所需的 Git 层、Installation Token Broker、Draft PR 创建、allowlist、状态机、多层幂等、脱敏与子进程环境隔离**全部已实现且生产可用**。主力真正需要新增的只有两类小件：(1) **pre-PR 显式用户授权闸门**，(2) **diff secret scanner**；以及把现有 `CodexJobRunner.run()` 里 inline 的 push+PR 抽成独立、可被非 Codex 路径调用的受控交付函数。请优先复用 `RepositoryPreparer` / `HostGitOperations` / `GitHubAppTokenBroker.get_token_for_job` / `GitHubRestClient` / `ControlPlane` 全家桶，不要重写。
