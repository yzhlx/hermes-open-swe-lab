# Hermes 智能体工程工作台进展

## 开工回执
- 2026-07-28：根线程 ACTIVE goal 已确认，当前工作以用户正式产品定义为唯一目标来源。
- 权威工作区：`F:\Users\user\Documents\协作平台`。
- 当前分支：`codex/hermes-workbench-mvp`；基线 HEAD：`3612b5263419`。
- 本阶段只完成现状核验与实施计划，不修改生产代码或测试。
- 复用边界：现有 ControlPlane、SQLite Event Store、Scheduler、Worker 与 GitHub Adapter。
- GitHub 继续是唯一工程事实源；Event Store 只保存命令、编排事件和投影。
- 自动 Merge 永久禁止；最终 Merge 仅由 Human Owner 在 GitHub 手工执行。
- Learning OS 当前未授权，保持 `BLOCKED_USER_AUTHORIZATION`。

## Task 0：基线、环境与真实性核验

| 状态 | 证据 |
|---|---|
| PASS | 共收集 `127` 项测试。 |
| PASS | Windows 非部署基线：`python -B -m pytest -q -p no:cacheprovider --ignore=tests/deployment`，`106 passed`。 |
| PASS | 核心目标测试 `test_d3_closed_loop.py`、`test_d3_security.py`、`test_codex_host_flow.py`：`43 passed`、`0 skipped`、`15.566s`。 |
| PASS | Windows Full 中 `124 passed`；另外 3 项 deployment 测试失败，原因单一且已定位为 Windows 无 Unix `id` 命令。 |
| FAIL | Windows Full 不能声称全绿：`3 deployment failed`。这些失败不得隐藏或升级为 PASS。 |
| PASS | Docker Client/Server 均为 `29.2.1`。 |
| PASS | 端口 `4175` 当前空闲；仅证明端口可用，不证明工作台在线。 |
| PASS | WSL2 Kernel `6.6.87.2`、Python `3.10.12`、Codex `0.114.0` 可见。 |
| KNOWN LIMITATION | WSL 当前缺少 pytest，尚不能在 WSL 重跑 deployment/full suite。 |
| KNOWN LIMITATION | 当前实现只有 Worker HTTP 表面，没有 Operator API、网页命令入口或真实人工状态机。 |
| KNOWN LIMITATION | `set_state()` 无迁移 Guard；现有 Scheduler 可接受调用者注入 CI，且没有绑定 PR Head SHA。 |
| KNOWN LIMITATION | 现有 Review 返工循环没有把 Reviewer 完整意见送回 Coding Agent。 |
| KNOWN LIMITATION | Host Codex 的 `PR_CREATED` 目前按终态写 `ended_at`，与后续自动 Review/返工不兼容。 |
| BLOCKED | 三个 GitHub App 必需环境变量未继承；未执行真实 GitHub 写入或 Live 闭环。 |
| BLOCKED_USER_AUTHORIZATION | Hermes Learning OS 仍在保护边界内，未进行 Clone、修改、Push、Issue 或 PR 操作。 |
| PASS | 外部 Secret 文件只确认存在性；未读取、未写入、未在文档记录任何值。 |

## 下一步

从实施计划 Task 1 开始：先写 Schema/状态统一红测，再做可重复增量迁移。每个 Task 完成后追加真实命令、结果与证据，不覆盖 Task 0 历史。

## 首批 TDD 与内核实现批次

| 状态 | 事实 |
|---|---|
| NOT RERUN | 首批工作台测试已新增到 `tests/workbench/`；当前没有可引用的 RED 或 GREEN 运行结果。 |
| NOT RERUN | 首批验收工具已新增到 `tools/acceptance/`；当前没有可引用的验收执行结果。 |
| NOT RERUN | 首批生产实现已落地，且精确限制为 `hermes_worker/db.py`、`hermes_worker/control_plane.py`、`hermes_worker/scheduler.py`。 |
| BLOCKED_TOOLING | RED 测试执行被当前 Windows sandbox helper 异常阻断，不能声称红测已按预期失败。 |
| BLOCKED_TOOLING | GREEN 测试和回归执行同样被当前 Windows sandbox helper 异常阻断，不能声称实现通过。 |
| BLOCKED_SCOPE | `hermes_worker/github_client.py` 的 Merge callable 仍在，但该文件不在本批生产修改白名单内；无 Merge 硬边界尚未完成。 |

本批结论：代码与测试文件存在不等于功能通过；在取得真实 RED、目标 GREEN 和回归结果前，整体状态保持 `NOT RERUN`，不得升级为 PASS。

## 首批实现后的最新验证

以下结果晚于上方 `NOT RERUN` 历史记录；旧记录保留用于说明当时的真实状态。

| 状态 | 当前实跑证据 |
|---|---|
| PASS | HTTP 负向请求体回归：五类拒绝路径共 `100/100` 个新连接稳定返回预期状态，无 TCP Reset。 |
| PASS | Operator API：`7 passed`。 |
| PASS | Fake Head 验收门：`2 passed`。 |
| PASS | D3/Core 目标回归：`43 passed`。 |
| PASS | Provenance 合同：`6 passed`。 |
| PASS | Human Control 合同：`5 passed`。 |
| PASS | Versioned Operator Action HTTP 合同：`5 passed`。 |
| FAIL | Workbench 汇总：`43 passed, 3 failed`。三项失败均指向 `hermes_worker/github_client.py` 仍公开 `merge_pr` callable。 |
| FAIL | Windows 非部署回归：`149 passed, 3 failed`。失败集合与 Workbench 汇总相同，没有新增失败类型。 |
| PASS | `git diff --check`：`0` 个 whitespace/error 命中。 |
| BLOCKED_SCOPE | 上述 3 个失败所需的 `hermes_worker/github_client.py` 不在当前生产修改白名单内，不能越权修复或把失败改写为 PASS。 |
| BLOCKED_SCOPE_PACKAGING | `pyproject.toml` 当前没有包含 `hermes_workbench` package，且该文件不在当前修改白名单；源码可测试不等于安装包可导入。 |
| NOT STARTED | Hallmark/UI 工作尚未开始；没有网页、截图、Playwright 或视觉验收证据。 |

当前阶段结论：Operator、状态机、溯源和人工控制目标测试已取得绿色证据；整体候选仍因 3 个 Merge capability 失败和 Packaging 门禁不能标记 PASS。

## Operator Runtime 与控制面最新验证

以下结果晚于“首批实现后的最新验证”，因此代表当前验证真相；上方记录继续保留为历史快照。

| 状态 | 当前实跑证据 |
|---|---|
| PASS | Static Runtime：`6 passed`。 |
| PASS | Operator API：`7 passed`；其中包含五类拒绝路径合计 `100/100` 个新连接无 TCP Reset 的回归。 |
| PASS | Versioned Human Actions HTTP：`5 passed`。 |
| PASS | Core/D3：`43 passed`。 |
| FAIL | Workbench 汇总：`49 passed, 3 failed`。 |
| FAIL | Windows 非部署汇总：`155 passed, 3 failed`。 |
| FAIL | 上述两个汇总中的 3 项失败均为 `hermes_worker/github_client.py` 仍公开 `merge_pr` callable；失败不得改写为 PASS。 |
| PASS | `git diff --check`：`0` 个 whitespace/error 命中。 |
| PASS | `127.0.0.1:4175` 真实 loopback Smoke 已通过；该证据仅覆盖本次启动、请求与受控停止。服务随后已停止、临时文件已清理、端口已释放，历史 PID 不代表当前在线。 |
| NOT COVERED | `hermes_workbench/__main__.py`、`hermes_workbench/runtime.py`、`hermes_workbench/operator_api.py` 已存在于工作区；文件存在是结构证据，不证明 Wheel、计划任务、持久在线或生产部署通过。 |

### 历史快照：当时未完成范围（已被后续批次部分覆盖）

| 状态 | 剩余门禁 |
|---|---|
| BLOCKED_SCOPE | 稳定入口仍缺 `scripts/workbench_start.ps1` 以及 Windows 计划任务/安装入口。 |
| BLOCKED_SCOPE_PACKAGING | `pyproject.toml` 尚未包含 `hermes_workbench` package。 |
| BLOCKED_SCOPE | 无 Merge 硬边界仍需同时处理 `hermes_worker/github_client.py` 与旧合同 `tests/test_d3_closed_loop.py`。 |
| BLOCKED_NO_READ | 真实同一 Job、同一 PR 的 Host 返工测试尚未新增；本批没有获得读取相关生产实现的授权。 |
| BLOCKED_SCOPE | 当时同一 PR 返工仍需处理旧 Host job runner 与 `hermes_worker/repository.py`，并补齐专用 Host Runner wiring。 |
| NOT STARTED | `web/**` 已在 UI 白名单内，但 Hallmark/SKILL 读取仍为 `BLOCKED_SKILL_ACCESS`，因此 UI 仍是 `NOT STARTED`；没有网页、截图、Playwright 或视觉验收证据。 |

当前结论：Operator Runtime 与版本化人工动作已有局部绿色证据，但候选整体仍为 `FAIL/BLOCKED`，不是可交付 MVP；GitHub 继续是唯一工程事实源，Merge 继续只能由 Human Owner 手工执行。

## 2026-07-28 授权窄范围返工批次（Fix pass 1 历史快照）

以下结果晚于上方历史快照，代表本批当前证据；未执行真实 GitHub 写入。

| 状态 | 当前实跑证据 |
|---|---|
| OFFLINE_TEST_PASS | 混合 `seq/id` 事件顺序下，Host Runner 能按 durable event ID 找到未消费的 round-2 review。 |
| OFFLINE_TEST_PASS | Scheduler 重试复用已持久化的 `REQUEST_CHANGES`，不会重复调用 Reviewer 或在 round-2 Agent 运行前提前 escalated；round-2 状态与事件在单一 SQLite 事务内记录。 |
| OFFLINE_TEST_PASS | Host `GitHubRestClient` 已提供 Scheduler 所需 `get_pr`、`get_ci_status`、`add_label`，每次通过回调取得短期 Token，不在 Client 对象保存 Token，且仍无 Merge API。 |
| OFFLINE_TEST_PASS | GitHub App scope gate 对 owner/repo、GitHub URL、大小写和 `.git` 后缀规范化后，只接受精确单一 `yzhlx/hermes-open-swe-smoke-test`；缺失、错误、额外仓库或查询失败均 fail closed。真实 Installation 查询仍为 `NOT_TESTED`。 |
| OFFLINE_TEST_PASS | 新工作台/Host 的 goal、scope、acceptance、reason、requirements、review payload 在 fingerprint、SQLite、Event/API echo 前递归脱敏；结构化非敏感字段保留。 |
| OFFLINE_TEST_PASS | `python -B -m pytest -p no:cacheprovider --ignore=tests/deployment --no-header`：`180 passed`。 |
| OFFLINE_TEST_PASS | `tools/acceptance/run-workbench.ps1`：Workbench `57`、baseline `43`、host-rework-security `17`、redact `10`，全部 `0 failed / 0 errors / 0 skipped`，Exit `0`。 |
| PASS | `git diff --check`：Exit `0`；仅有既有 LF→CRLF 提示。 |
| BLOCKED_SCOPE | Operator API Human Owner 本地身份认证未纳入本批；loopback session/CSRF 不升级为身份认证 PASS。 |
| BLOCKED_SCOPE | 最终人工 approval 前的实时 GitHub Head/CI/Review 复验未纳入本批；现有 approval 不能升级为 live acceptance PASS。 |
| NOT_TESTED | 未访问真实 Secret，未调用真实 GitHub API，未 Push、未创建/更新 PR、未部署。 |

## 2026-07-28 Inline Fix Pass 2 与最终验证

以下记录晚于 Fix pass 1，代表当前工作树最新离线证据；review-loop 已达到 3 个独立审查轮次上限。

| 状态 | 当前实跑证据 |
|---|---|
| OFFLINE_TEST_PASS | Worker API 在写入前拒绝 Scheduler/Reviewer 所有的 `review`、`round2_label`、`round2_push`、`pr_created`、`await_user`、`escalated` 与 `ci_*` 事件；round-2 lookup 同时校验 Scheduler/Reviewer provenance。 |
| OFFLINE_TEST_PASS | 第一轮 `REQUEST_CHANGES` 重试继续复用 round 2；第二轮拒绝与 crash-recovery 保持 `escalated`，不会反转为 rework 或超过 `MAX_ROUNDS`。 |
| OFFLINE_TEST_PASS | CI 查询绑定首次观测 Head；CI 后和 Reviewer 回调后均重新读取 PR Head，任一变化均 fail closed 为 `head_mismatch`，不持久化 stale verdict。 |
| OFFLINE_TEST_PASS | Operator request ID 以 SHA-256 durable key 保存、仅回显脱敏值；Worker complete/fail/result/error 在 SQLite binding 前递归脱敏。 |
| OFFLINE_TEST_PASS | Secret scanner 覆盖 staged、unstaged、untracked delivery source；真实样式与 synthetic fixture 按匹配 token 分类，missing/read/Git 错误 fail closed，输出仅含数量和路径。 |
| OFFLINE_TEST_PASS | Full non-deployment：`197 passed / 0 failed / 0 errors / 0 skipped`，Exit `0`。 |
| OFFLINE_TEST_PASS | Windows `tests/deployment`：`21 passed / 0 failed / 0 errors / 0 skipped`，Exit `0`；WSL 未运行。 |
| OFFLINE_TEST_PASS | `tools/acceptance/run-workbench.ps1`：Workbench `63`、baseline `43`、host-rework-security `28`、redact `10`，全部 `0 failed / 0 errors / 0 skipped`，Exit `0`。 |
| PASS | Secret scan：`41` files scanned、`0` violations、`0` read/Git errors；artifact：`C:\\Windows\\Temp\\hermes-workbench-acceptance-84c1839de04547339bf1efcf4ee4adeb\\secret-scan.json`。 |
| PASS | `git diff --check`：Exit `0`；仅既有 LF→CRLF 提示。 |
| BLOCKED_SCOPE | Operator API Human Owner 本地身份认证仍未纳入本批。 |
| BLOCKED_SCOPE | 最终人工 approval 前的 live GitHub Head/CI/Review 复验仍未纳入本批。 |
| NOT_TESTED | 真实 GitHub App scope、Push、Draft PR、Check、Review、Label、round-2 write、Provider、WSL 与云端部署均未执行。 |

## 2026-07-29 Phase 2 C1 独立 Lab Control Plane 本地实现

本节晚于上方最终验证，代表当前 C1 工作树证据。Lab Draft PR #14 已存在，但 C1 尚未 Commit/Push。

| 状态 | 当前实跑证据 |
|---|---|
| OFFLINE_TEST_PASS | 当时新增 value-safe `ControlPlaneHttpClient`、lease-gated `RemoteTokenBroker` 与专用 Host runner；privileged Host routes 使用独立 worker-token-hash allowlist，普通 Worker 不能调用。 |
| OFFLINE_TEST_PASS | 当时 Host job runner 保留本地 SQLite 路径，并可通过能力检测使用远程 transition、线程安全 keepalive 与受信 handoff。 |
| OFFLINE_TEST_PASS | 新增隔离 `Dockerfile.phase2` / `docker-compose.phase2.yml` contract：独立路径、`127.0.0.1:18080`、非 root、无 privileged/Docker socket/Provider/target-repo mount、restart=no。 |
| OFFLINE_TEST_PASS | C1 narrow：`14 passed`，Exit `0`。 |
| OFFLINE_TEST_PASS | Host rework、D1/D3 security、cloud/worker deployment compatibility：`78 passed`，Exit `0`。 |
| OFFLINE_TEST_PASS | Full non-deployment：`208 passed`，Exit `0`。 |
| OFFLINE_TEST_PASS | Windows deployment：`25 passed`，Exit `0`；WSL 未运行。 |
| OFFLINE_TEST_PASS | Unified acceptance：Workbench `63`、baseline `43`、host-rework-security `28`、redact `10`，全部 `0 failed / 0 errors / 0 skipped`，Exit `0`。 |
| PASS | Compose config synthetic/value-free validation Exit `0`；dedicated runner dry-run Exit `0`，报告 `github_writes=false / cloud_writes=false`，且不存在的 token file 未被读取。 |
| PASS | Secret scan：`19` files、`0` violations、`0` read/Git errors；artifact：`C:\\Windows\\Temp\\hermes-workbench-acceptance-eeca5c36c0b741aebd1daddf2370ea22\\secret-scan.json`。 |
| PASS | `git diff --check` 与 cached check 均 Exit `0`。 |
| USER_ATTESTED | 受影响 Provider Key 已在聊天外吊销/轮换；本地配置仅做 value-free `PRESENT` 检查。不得升级为 `PROVIDER_LIVE_PASS`。 |
| PASS | Independent Haiku C1 review 完成：无 BLOCKING / IMPORTANT finding；artifact：`.pi-subagents/reviews/phase2-c1-final-haiku-review.md`。此前 Haiku/GPT-5.5 quota 403 失败运行仅保留为历史，不计入最终 review。 |
| NOT_TESTED | C1 未 build image、未运行新 container、未部署、未请求 Installation Token、未写 smoke repo、未执行 Provider live。 |
| BLOCKED_C2_AUTHORIZATION | 只有 C1 完成独立 review、Commit SHA、镜像/源包计划和新的 C2 精确授权后，才允许创建独立云端路径/镜像/容器。现有 `/opt/hermes-cloud` 必须保持不变。 |

## 2026-07-29 Phase 2 最新状态：Pi Agent supersession

本节晚于上方 C1 记录，代表当前权威状态。

| 状态 | 当前实跑证据 |
|---|---|
| PASS | A3 已将现有 Draft PR #14 fast-forward 到 `577765eb4d79e1ffe404770ab02a0d427d5bbe8d`，PR 保持 OPEN/DRAFT；无 Merge/Ready/Force Push。 |
| CLOUD_SYNTHETIC_REHEARSAL_PASS | C2B 使用 exact source/image 在隔离云路径完成 synthetic up/health/runtime/SSH-forward 验证并强制 rollback；受保护 `/opt/hermes-cloud` baseline 前中后完全一致，隔离资源零残留。 |
| LOCAL_IMPLEMENTATION_TESTED | Human Owner 指示以 Pi Agent 替代 Codex。已新增 contained workspace guard/extension、`PiCliRunner`、provider-neutral `HostAgentJobRunner` 与 `pi_worker_runner`；Codex CLI runtime 已从当前代码删除。 |
| OFFLINE_TEST_PASS | Pi workspace guard Node tests：`5 passed`；Pi/Host focused：`63 passed`；full non-deployment：`217 passed`；deployment：`27 passed`。均无 live Provider call。 |
| OFFLINE_TEST_PASS | Unified acceptance：Workbench `63`、baseline-host-agent `36`、host-agent-rework-security `26`、redact `10`；全部 PASS，Secret scan `32` candidates、`0` violations/errors。 |
| PASS | Pi CLI `0.82.1` 存在；contained extension 以 `PI_OFFLINE=1 --list-models` 成功加载；dedicated runner dry-run 报告 `provider_calls=false / github_writes=false / cloud_writes=false`。 |
| PASS | Independent Haiku full review 与 metadata-only incremental review 均无 BLOCKING / IMPORTANT finding。 |
| IMAGE_REBUILD_REQUIRED | C2A/C2B 的旧 image/tar 对应 Commit `577765e`，已被 Pi migration source supersede。Dockerfile/context 已收紧为只 COPY `deploy/cloud`；`docker build --check` PASS，但未 build 新 image。 |
| MODEL_NOT_AUTHORIZED | Parent session 观察值为 `deepkey/gpt-5.6-sol`、reasoning `high`，仅记录事实，不是 Host Worker 模型授权。Host Worker Provider/Model/thinking 必须显式批准。 |
| CLOUD_NOT_DEPLOYED | C2B 已 rollback；真实 file-based Secrets、持久 Control Plane、Host Pi Worker registration、Installation Token、smoke write 和 end-to-end 仍未执行。 |
