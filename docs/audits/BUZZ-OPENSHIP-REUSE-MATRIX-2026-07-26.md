# Buzz / Openship 复用矩阵 (Hermes Reuse Matrix)

**Audit date:** 2026-07-26
**Reviewer:** gstack-product-reviewer (analysis + docs only; no install/build/run/commit)
**Hermes HEAD:** `8806010ca3291be79d886ba5b70c946c19585299` (branch `audit/buzz-openship-reuse-20260726`)
**Buzz HEAD:** `dd222a509b156ba52ed3219e895d7bf1cf322c92` (main, v0.4.26, Apache-2.0)
**Openship HEAD:** `529ff198e16a2db5a5afd8b1b3b4a2b9eb51d202` (main, v0.3.0, Apache-2.0)

## 0. 判定总则 (Classification Rules — strictly applied)

- **KEEP** — 由 Hermes 控制层持续维护。用于 Hermes 特有的任务/验收规则、用户授权边界、Worker/Reviewer 角色分离、证据门、人类最终合并、GitHub 作为唯一代码事实源，以及没有稳定 OSS 覆盖的核心能力。
- **INTEGRATE** — 保留 Hermes 控制逻辑，通过标准接口调用外部能力。优先级：现有 API/MCP/ACP → 独立服务/二进制 → 薄适配器 → 小 fork → 复制源码(最后)。
- **REPLACE** — 停止自研被 OSS 高度重复且达标的模块。仅当：外部存在、接口+依赖已验证、安全边界可接受、有测试、可隔离部署、有回滚计划、不创建第二事实源、迁移成本 < 继续成本。本审计中 **无一满足 REPLACE**。
- **不维护两个权威源**：不出现第二个 GitHub 任务状态、第二个 GitHub Review 事实、第二个部署历史、第二个 Agent 身份事实、第二个不可同步的审计日志。外部运行日志仅作辅助，权威必须清晰。

> 事实源主干对比：Hermes = GitHub + SQLite（失败即关闭 fail-closed）；Buzz = Nostr relays + Postgres；Openship = Docker/Postgres PaaS。三者事实源主干不同，任何集成都会引入"第二事件/状态/审计源"。

---

## 1. 主表 — 24 项 Hermes 能力分类

列说明：`当前完成度` 取值 OFFLINE_TEST_PASS / PASS / MOCK_PASS / NOT_TESTED(策略/文档/缺引擎)；`重合程度` HIGH/MED/LOW/NONE；`建议` KEEP/INTEGRATE/REPLACE；`迁移复杂度` LOW/MED/HIGH/—(保持)。

| # | 当前模块 | 当前代码路径 | 当前完成度 | 当前测试证据 | Buzz 对应能力 | Openship 对应能力 | 重合程度 | 建议 | 推荐接入方式 | 是否需 Fork | 安全风险 | 许可证义务 | 上游成熟度 | 迁移复杂度 | 判断证据 |
|---|---------|-------------|-----------|-------------|--------------|------------------|---------|------|-------------|-----------|---------|-----------|-----------|-----------|----------|
| 1 | GitHub Issue 任务入口 | `hermes_worker/webhook_receiver.py` → `control_plane.create_job` | OFFLINE_TEST_PASS (无真实 webhook 验证) | `test_d3_security` 03c–03g | NONE（Buzz 不以 GitHub Issue 为任务源） | NONE | NONE | **KEEP** | 保持自研；GitHub 为中心，allowlist + HMAC fail-closed | 否 | 低；HMAC 验签 + ALLOWED_REPOS + require_tls，fail-closed | 外部未使用源码，不触发传染 | Hermes MVP-0 离线通过 | — | §1#1；allowlist `yzhlx/hermes-open-swe-smoke-test` |
| 2 | 任务状态机 | `protocol.JobState` + `control_plane.jobs` + `delivery.DeliveryState` | OFFLINE_TEST_PASS | `test_d1`–`test_d4` job/delivery 流 | 仅 `buzz-acp` task queue（需 Nostr，**第二任务状态源**） | NONE | LOW（概念重叠，主干冲突） | **KEEP** | 保持自研；lease 模型 + fail-closed 终态 | 否 | 低；SQLite 串行写，无 TOCTOU | 同 #1 | Hermes MVP-0 | — | §1#2；lease + fail-closed terminals |
| 3 | Webhook 接收 + 去重 | `webhook_receiver.py` (`verify_signature`, delivery-id 去重) | OFFLINE_TEST_PASS | `test_d3_security` 03c–03g | NONE | NONE | NONE | **KEEP** | 保持自研 | 否 | 低；HMAC + delivery-id INSERT 去重 + TLS 强制 | 同 #1 | Hermes MVP-0 | — | §1#3 |
| 4 | 调度器 Scheduler | 无代码模块；`constants.ROLE_SCHEDULER` + `AGENTS.md §15` 策略 | NOT_TESTED (策略/文档) | 无 | `buzz-workflow` cron（需 Postgres + 全 Buzz） | NONE | LOW（若用需整 Buzz） | **KEEP** | 保持自研策略；Hermes 控制层后续自建，不引入 Buzz | 否 | 低（策略态） | 同 #1 | Buzz pre-1.0 | — | §1#4；引擎缺，策略 only |
| 5 | Worker | `hermes_worker/worker.py` (`HermesWorker` poll/claim/run/complete) | OFFLINE_TEST_PASS (无真实 CP 往返) | `test_d1_offline`, `test_d3` 05a/b | `buzz-acp` pool（需 Nostr，**第二**） | NONE | LOW | **KEEP** | 保持自研；仅出站 HTTPS，无入站端口、无 docker.sock | 否 | 低–中；出站-only + lease keepalive | 同 #1 | Hermes MVP-0 | — | §1#5 |
| 6 | 沙箱 Sandbox | `protocol.SandboxBackend` + `echo_sandbox.py` + `docker_sandbox.py` | OFFLINE_TEST_PASS (真实 Docker 未验证) | `test_d1_offline`(7), `test_d2_sandbox`(11) | `buzz-dev-mcp`（MCP 工具服，可选互补） | NONE | MED（工具面互补） | **KEEP** + 可选 INTEGRATE | 保持自研 Docker 沙箱；**可选**后续经 MCP 挂 `buzz-dev-mcp` 作文件/grep/shell 工具面 | 否 | 中；真实 Docker 生命周期未测；path-escape 守卫已建 | 同 #1 | Hermes MVP-0；Buzz pre-1.0 | LOW(可选) | §1#6；§2 `buzz-dev-mcp` |
| 7 | Agent 运行时 | 上游 `langchain-ai/open-swe` (pin `ed12bb8d`) 经 `protocol.py` 沙箱协议运行 | NOT_TESTED (外部依赖，不在本仓) | 经沙箱/投递间接 | `buzz-agent`（ACP bin，min deps，无 Nostr） | NONE | MED（会复制 Open SWE） | **KEEP** + 可选 INTEGRATE | 用上游 Open SWE；**可选**若需第二 agent 运行时，经 ACP 适配器接 `buzz-agent`（注意重复） | 否 | 低（包装层）；中若接 buzz-agent（Rust+pre-1.0） | Apache-2.0 仅服务调用不传染 | 上游稳定；Buzz pre-1.0 | MED(可选) | §1#7；§2 `buzz-agent` |
| 8 | GitHub App | `hermes_worker/github_app.py` (`GitHubAppTokenBroker`, `RealAppApiClient`) | OFFLINE_TEST_PASS (真实 PyJWT+PEM 未跑) | `test_d3_security`, `test_d4_delivery` | `git-credential-nostr`/`git-sign-nostr`（**与 GitHub App 模型冲突**） | NONE | LOW（冲突不采纳） | **KEEP** | 保持自研；**不采纳** Buzz git-via-Nostr | 否 | 低；lease-gated delivery + in-mem cache，不持久化 token | 同 #1 | Hermes MVP-0 | — | §1#8；§2 git-via-Nostr 冲突 |
| 9 | 短期 Token Broker | `github_app.GitHubAppTokenBroker` (`mint_installation_token`, lease-gated, 投递后清零) | OFFLINE_TEST_PASS | 同 #8 | 同 #8（冲突） | NONE | LOW | **KEEP** | 保持自研 | 否 | 低；ttl + 缓存 + 投递后 zeroize | 同 #1 | Hermes MVP-0 | — | §1#9 |
| 10 | Commit/Push/Draft PR | `repository.py` + `github_client.py` + `delivery.py` | OFFLINE_TEST_PASS (无真实 push/Draft PR) | `test_d4_delivery`(54) | NONE | NONE（Openship 管部署非 PR 写） | NONE | **KEEP** | 保持自研；AskPass + 单仓库 allowlist + 单 ref 正则 + fail-closed + **无 merge** | 否 | 中；真实 git push/Draft PR 未测；fail-closed 门已建 | 同 #1 | Hermes MVP-0 | — | §1#10 |
| 11 | CI Gate | 无 Hermes 引擎；`jobs.ci_status` + `gh pr checks` 薄包装 | NOT_TESTED (委托 GitHub Actions) | `test_d4` 仅断言字段 | NONE（Buzz 无 CI Agent） | NONE | NONE | **KEEP** | 保持委托 GitHub Actions 为 CI 事实源 | 否 | 低；CI 契约在外部 smoke-test 仓 | 同 #1 | Hermes MVP-0 | — | §1#11 |
| 12 | Reviewer | 无代码；`constants.ROLE_REVIEWER`, `MAX_ROUNDS=2` 策略 | NOT_TESTED (策略) | 仅常量导入测试 | NONE（Buzz 无 Reviewer 组件） | NONE | NONE | **KEEP** | 保持自研（Hermes 自有）；后续自建或借人类/agent | 否 | 低（策略态） | 同 #1 | 引擎缺 | — | §1#12；Buzz 无 reviewer |
| 13 | Review 反馈 | 无代码；`OPERATIONS-RUNBOOK.md §9b` | NOT_TESTED | 无 | NONE | NONE | NONE | **KEEP** | 保持自研（缺） | 否 | 低 | 同 #1 | 缺 | — | §1#13 |
| 14 | 自动返工 Auto rework | 无代码；`MAX_ROUNDS=2` 常量 | NOT_TESTED | 无 | NONE | NONE | NONE | **KEEP** | 保持自研（缺） | 否 | 低 | 同 #1 | 缺 | — | §1#14 |
| 15 | 二轮复审 | 无代码；round-2 label 门（文档） | NOT_TESTED | 无 | NONE | NONE | NONE | **KEEP** | 保持自研（缺） | 否 | 低 | 同 #1 | 缺 | — | §1#15 |
| 16 | 人类最终合并 | `delivery.py`/`github_client.merge_pr` 抛错，自动化永不合并 | PASS (by design) | `test_d4` "auto-merge never called" | NONE | NONE | NONE | **KEEP** | 保持缺省；用户授权边界（fail-closed 不合并） | 否 | 低；结构性保证 | 同 #1 | Hermes MVP-0 | — | §1#16 |
| 17 | 证据记录 | `scripts/export_run_evidence.py` + `db.py` + `runtime/events.db` | OFFLINE_TEST_PASS (读 SQLite schema) | 无专用；schema 覆盖 | `buzz-audit`（Postgres hash-chain，**第二审计源**） | NONE | LOW（冲突不采纳） | **KEEP** | 保持自研 secret-free 证据包 | 否 | 低；secret-free 导出 | 同 #1 | Hermes MVP-0 | — | §1#17；§2 `buzz-audit` 冲突 |
| 18 | 审计日志 | `db.py events`(唯一 event_id→幂等) + `delivery_state` | OFFLINE_TEST_PASS | `test_d3` 06 幂等；delivery-state | `buzz-audit`（**第二审计源**，禁止） | NONE | LOW（冲突不采纳） | **KEEP** | 保持自研；**不采纳** Buzz hash-chain（会成第二事实源） | 否 | 低；简单事件存，幂等 | 同 #1 | Hermes MVP-0 | — | §1#18；§2 `buzz-audit` |
| 19 | 失败恢复 | `control_plane.reap_expired_leases` + 幂等 claim/complete/fail + delivery 续投 | OFFLINE_TEST_PASS | `test_d3` 05a/b；`test_d4` 恢复 | `buzz-acp pool_lifecycle` / `buzz-workflow` 续步（需 Nostr/PG） | NONE | LOW | **KEEP** | 保持自研 CP reap/lease/idempotent 投递 | 否 | 中；真实恢复路径未测 | 同 #1 | Hermes MVP-0 | — | §1#19 |
| 20 | 并发控制 | `control_plane.claim` (atomic UPDATE…RETURNING, SQLite 锁串行) | OFFLINE_TEST_PASS | `test_d3` 06_atomic_claim_no_double | `buzz-acp` queue（**第二任务状态源**） | NONE | LOW | **KEEP** | 保持自研原子 claim + MVP 并发=1 + retry budget | 否 | 低；无 TOCTOU | 同 #1 | Hermes MVP-0 | — | §1#20 |
| 21 | 密钥脱敏 | `hermes_worker/redact.py` + `hermes_open_swe_relay/redact.py` + `secret_scan.py` | OFFLINE_TEST_PASS (2 预存失败) | `test_redact`(部分), `test_d4` | NONE（Buzz 无对应） | NONE | NONE | **KEEP** | 保持自研；含 GitHub-token / URL 嵌入凭证规则 | 否 | 低；`re` 规则；真实密钥未练 | 同 #1 | Hermes MVP-0 | — | §1#21 |
| 22 | 部署 Runbook | `OPERATIONS-RUNBOOK.md` + `ROLLBACK-PLAN.md` + `runtime/d2-real-smoke/run_real_smoke.py` | MOCK_PASS/DOCUMENTED (D2.5 未跑) | D2.5 脚本未执行 | NONE | **HIGH**（完整 PaaS 直接重叠） | HIGH | **KEEP**(运行手册) + **INTEGRATE**(Openship 延后) | 现保持自研 smoke-scoped 手册；**MVP-0 后**若需真实部署，Openship 作独立 scoped-MCP 服务接入 | 否 | 中（手册）；高若接 Openship（docker on host + DNS/TLS） | Apache-2.0 服务调用不传染；分发须保 NOTICE | Openship v0.3.0 pre-1.0 | MED(延后) | §1#22；§3 完整部署面 |
| 23 | 云端校验 Cloud validation | 无代码；`AGENTS.md §9` 云服务器=控制层 only | NOT_TESTED (出 MVP-0) | 无 | NONE | NONE | NONE | **KEEP** | 保持自研策略（blue/green/canary 缓建） | 否 | 低（策略态） | 同 #1 | 缺 | — | §1#23 |
| 24 | Provider 适配器 | `hermes_open_swe_relay/` + `scripts/provider_preflight.py` (P1–P6) | OFFLINE_TEST_PASS (`test_adapter` 17 通过) | `test_adapter` | NONE | NONE（Openship 非 provider 网关） | NONE | **KEEP** | 保持自研薄 opt-in relay 适配器 | 否 | 低；懒加载 SDK + 环境变量 + 脱敏 | 同 #1 | Hermes MVP-0 | — | §1#24 |

### 主表汇总

- **KEEP：21 项**（#1,#2,#3,#4,#5,#8,#9,#10,#11,#12,#13,#14,#15,#16,#17,#18,#19,#20,#21,#23,#24）
- **KEEP + 可选 INTEGRATE：2 项**（#6 沙箱→`buzz-dev-mcp` MCP 互补；#7 Agent→`buzz-agent` ACP 可选）
- **KEEP + INTEGRATE(延后)：1 项**（#22 部署 Runbook→Openship scoped-MCP，MVP-0 后）
- **REPLACE：0 项**

> **统计口径（统一，与 `RECOMMENDED-TARGET-ARCHITECTURE-2026-07-26.md` 一致）**：24 项 Hermes 能力，**0 项 REPLACE**；**21 项纯 KEEP**（无任何外部接入）+ **2 项 KEEP+可选 INTEGRATE**（#6、#7）+ **1 项 KEEP+INTEGRATE 延后**（#22）。即「全部 24 项能力均保持自研 KEEP 基底，仅 3 项叠加外部接入（2 可选 + 1 延后）」。文档中「KEEP 20」指 KEEP 核心叙述清单行数（含 #6/#7/#22 作为自研核心，其外部接入见 §2/§3），与「21 项纯 KEEP」不矛盾。

> 结论：**无任何 REPLACE**。所有能力要么由 Hermes 维持（KEEP），要么仅以标准接口（MCP/ACP/独立服务）可选/延后接入外部（INTEGRATE），且接入优先级严格遵守 现有接口 → 服务/二进制 → 薄适配器 → fork → 复制源码(最后)。

---

## 2. 附表 — Buzz / Openship 平台原生能力（Hermes 当前无对应实现）

这些能力 Hermes 源码中没有引擎（或仅是策略文档），由 Buzz/Openship 平台原生提供。**核心判断：除 Openship 部署面（MVP-0 后 INTEGRATE）与两个薄适配器 seam（buzz-agent / buzz-dev-mcp）外，其余 Buzz 平台原生能力均会引入 Nostr/Postgres 第二事实源，按"不维护两个权威源"原则不采纳。**

### 2.1 Buzz 平台原生能力（Hermes 无对应）

| 能力 | Buzz 模块（证据） | 与 Hermes 关系 | 建议 | 理由 / 风险 | 是否需 Fork | 上游成熟度 | 迁移复杂度 |
|------|------------------|--------------|------|-----------|-----------|-----------|-----------|
| Agent 生命周期池 | `crates/buzz-acp` (`pool.rs`,`queue.rs` 4759 行) | 概念重叠任务调度 | **不采纳**（可选 ACP 接 `buzz-agent` 单点） | 需 Nostr relay → **第二任务状态源**；Rust+pre-1.0 | 否（仅薄适配器可选） | pre-1.0 快速迭代 | MED(可选) |
| 任务队列 | `crates/buzz-acp/src/queue.rs` | 重叠 #2 状态机 | **不采纳** | 需 Nostr/relay → **第二任务状态源** | 否 | pre-1.0 | HIGH |
| 工作流引擎 | `crates/buzz-workflow` (`ActionDef`, YAML, cron) | Hermes 无引擎 | **不采纳** | 需 Postgres → **第二状态源**；Hermes 自建更轻 | 否 | pre-1.0 | HIGH |
| 审批/人类门 | `buzz-workflow` `RequestApproval` (4h timeout) | 重叠人类合并边界 | **不采纳** | 运行态存 PG → **第二审批事实源**；与 Hermes #16 用户边界冲突 | 否 | pre-1.0 | HIGH |
| 消息/事件总线 | `crates/buzz-pubsub` + Nostr relay | 重叠 #2/#18 | **不采纳** | 即"第二总线"；Nostr 主干与 GitHub 冲突 | 否 | pre-1.0 | HIGH |
| 审计 hash-chain | `crates/buzz-audit` (Postgres 追加-only 哈希链) | 重叠 #17/#18 | **不采纳（禁止）** | 会成**第二不可同步审计源**，直接违反事实源边界 | 否 | pre-1.0 | HIGH |
| 权限模型 | `crates/buzz-auth` (NIP-42/98 + `git_perms`) | 重叠 #8/#16 | **不采纳** | Nostr pubkey 鉴权 + git-via-Nostr 与 GitHub App 安装令牌模型**冲突** | 否 | pre-1.0 | HIGH |
| Agent 身份 | `crates/buzz-persona` | 重叠 Agent 身份事实 | **不采纳** | 会成**第二 Agent 身份事实源** | 否 | pre-1.0 | HIGH |
| ACP Agent bin | `crates/buzz-agent` (ACP 合规, min deps, 无 Nostr) | 重叠 #7 | **可选 INTEGRATE**（ACP 适配器） | 会复制 Hermes 上游 Open SWE agent；仅当需第二 agent 运行时 | 否（薄适配器） | pre-1.0 | MED |
| MCP 工具服 | `crates/buzz-dev-mcp` (rmcp: file/grep/shell/edit + git-via-nostr) | 重叠 #6 | **可选 INTEGRATE**（MCP 工具面） | 可作 Hermes Docker 沙箱的工具面互补；Hermes 已有沙箱 | 否（薄适配器） | pre-1.0 | LOW |

### 2.2 Openship 平台原生能力（Hermes 无对应，均属部署面）

| 能力 | Openship 模块（证据） | 与 Hermes 关系 | 建议 | 理由 / 风险 | 是否需 Fork | 上游成熟度 | 迁移复杂度 |
|------|---------------------|--------------|------|-----------|-----------|-----------|-----------|
| 完整部署能力（git 驱动部署 / 构建 / 容器部署 / 环境变量 / 日志 / 健康检查 / 版本历史 / **回滚** / Preview-Staging-Prod / 域名 / TLS / 数据卷 / RBAC / REST API（源码已检视，pre-1.0 未构建/未实测验证） / **一等 MCP 服务**） | `packages/core/*`,`apps/api/modules/*`,`apps/api/src/modules/mcp/` (`HARD_DENY=[tokens,auth,mcp]`, `filterToolsForPrincipal` 只读/角色/受限授权, `annotations` 标 destructive) | 直接重叠 #22 未来部署 Runbook | **INTEGRATE（延后 MVP-0 后）** | Hermes 当前不需要（prod 部署出 MVP-0 范围）；作为**独立 scoped-MCP 服务**接入，不复制源码；`HARD_DENY`+ 授权域天然限制 Agent 仅 status-view / test-deploy，**不授生产 grant 即结构性禁生产** | 否（运行服务，Apache-2.0） | v0.3.0 pre-1.0 | MED（延后） |
| Agent 部署调用 | 经 MCP：`mcp-tools.ts` 每路由→工具，经真实 Hono app 派发 | 重叠 #22 接入方式 | **INTEGRATE（随上，延后）** | 只读 MCP token→status-view；受限 token 仅授 preview/test 项目→test-deploy | 否 | pre-1.0 | MED |

---

## 3. 事实源冲突速查 (Fact-Source Conflict Check)

| 拟引入的外部能力 | 是否创建第二事实源 | 判定 |
|----------------|------------------|------|
| Buzz Nostr relay / pubsub | 是（第二事件/状态总线） | **禁止** |
| Buzz Postgres workflow/queue/audit | 是（第二状态/审计源） | **禁止** |
| Buzz persona identity | 是（第二 Agent 身份源） | **禁止** |
| Buzz git-via-Nostr auth | 是（与 GitHub App 冲突） | **禁止** |
| Openship 作为部署服务（scoped-MCP） | 否（部署权威明确归 Openship，Hermes 不持有第二部署真相；运行日志辅助） | **允许（延后）** |
| buzz-agent 经 ACP（Hermes 侧运行） | 否（Agent 执行结果仍经 Hermes 控制层入 GitHub） | **可选** |
| buzz-dev-mcp 经 MCP（工具面） | 否（工具执行结果仍落 Hermes 沙箱/投递） | **可选** |

---

## 4. 与本仓 HEAD 一致性声明

- 全部 24 项 Hermes 能力判定基于 `8806010` 源码 + 离线测试证据（`test_d1`–`test_d4`,`test_redact`,`test_adapter`,`test_d3_security`）。
- Buzz/Openship 能力判定基于 `gh api` 只读远程源码检视（本地克隆目录为空，未 clone/build/run）；证据强度为"源码已验证但未构建"，低于本地构建+测试。
- 本矩阵未执行任何安装/构建/运行/提交；仅文档产出。
