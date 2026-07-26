# 推荐目标架构 (Recommended Target Architecture)

**Audit date:** 2026-07-26
**Reviewer:** gstack-product-reviewer (analysis + docs only; no install/build/run/commit)
**Hermes HEAD:** `8806010ca3291be79d886ba5b70c946c19585299`
**Buzz HEAD:** `dd222a5` (v0.4.26) · **Openship HEAD:** `529ff19` (v0.3.0) — 均为 Apache-2.0

## 0. 总原则

Hermes Learning OS 是**最终产品**。协作架构的**唯一目的**是加速 Hermes Learning OS 的开发/测试/评审/部署/迭代，**不应演化为独立平台**。集成外部能力必须满足：不创建第二事实源、安全边界可接受、可隔离部署、有回滚计划、迁移成本低于继续自研成本。

基于复用矩阵（见 `BUZZ-OPENSHIP-REUSE-MATRIX-2026-07-26.md`），**结论：0 项 REPLACE；全部 KEEP，少数可选/延后 INTEGRATE。**

---

## 1. 保留哪些自研核心 (KEEP 清单)

以下能力由 Hermes 控制层持续维护，**不外包、不复制外部源码、不引入外部事实源主干**：

1. **GitHub Issue 任务入口**（`webhook_receiver.py` → `control_plane.create_job`）— HMAC 验签 + allowlist + TLS fail-closed。
2. **任务状态机**（`protocol.JobState` + `control_plane.jobs` + `delivery.DeliveryState`）— lease 模型 + fail-closed 终态。
3. **Webhook 接收 + 去重**（`webhook_receiver.py`）— delivery-id 幂等去重。
4. **调度器策略**（`constants.ROLE_SCHEDULER` + `AGENTS.md §15`）— 策略态；引擎后续自建，不引 Buzz。
5. **Worker**（`hermes_worker/worker.py`）— 仅出站 HTTPS，无入站端口、无 docker.sock。
6. **沙箱协议 + Docker 沙箱**（`protocol.SandboxBackend` + `echo_sandbox.py` + `docker_sandbox.py`）— 单 workdir 绑定 + path-escape 守卫。
7. **Agent 运行时包装**（上游 Open SWE 经 `protocol.py` 运行）— 可替换执行器，Hermes 仅持有协议包装。
8. **GitHub App + 短期 Token Broker**（`github_app.GitHubAppTokenBroker`）— lease-gated、内存缓存、投递后 zeroize、**不持久化**。
9. **Commit / Push / Draft PR**（`repository.py` + `github_client.py` + `delivery.py`）— AskPass + 单仓库 allowlist + 单 ref 正则 + **无 merge**。
10. **CI Gate 委托**（GitHub Actions 为 CI 事实源；`jobs.ci_status` 仅记录）。
11. **Reviewer / Review 反馈 / 自动返工 / 二轮复审 策略**（`constants`, `OPERATIONS-RUNBOOK.md`）— 引擎后续自建。
12. **人类最终合并边界**（`delivery.py`/`github_client.merge_pr` 抛错）— 结构性 fail-closed 不合并（用户授权边界）。
13. **证据记录**（`scripts/export_run_evidence.py` + `db.py` + `runtime/events.db`）— secret-free 导出包。
14. **审计日志**（`db.py events` 唯一 `event_id` 幂等 + `delivery_state`）— 简单事件存，**不采纳** Buzz hash-chain。
15. **失败恢复**（`control_plane.reap_expired_leases` + 幂等 claim/complete/fail + 投递续投）。
16. **并发控制**（`control_plane.claim` 原子 `UPDATE…RETURNING` + MVP 并发=1 + retry budget）。
17. **密钥脱敏**（`redact.py` ×2 + `secret_scan.py`）— 含 GitHub-token / URL 嵌入凭证规则。
18. **部署 Runbook（文档态）**（`OPERATIONS-RUNBOOK.md` + `ROLLBACK-PLAN.md` + `runtime/d2-real-smoke/`）— smoke-scoped；真实部署延后。
19. **云端校验策略**（`AGENTS.md §9`，出 MVP-0）。
20. **Provider 适配器**（`hermes_open_swe_relay/` + `scripts/provider_preflight.py`）— 薄 opt-in relay 适配器。

> 以上 20 项 = KEEP。**没有稳定 OSS 覆盖 Hermes 特有任务/验收规则、用户授权边界、Worker/Reviewer 角色分离、证据门、人类合并边界、GitHub 唯一代码事实源**，故必须自研维持。

---

## 2. 接入哪些 Buzz 能力 (INTEGRATE Buzz — 可选/延后)

**仅两个薄适配器 seam 可行，且均为可选**（接入会重叠 Hermes 现有 agent + 沙箱，故非必须）：

| 能力 | 接入方式 | 接口 | 状态 | 条件 / 风险 |
|------|---------|------|------|-----------|
| `buzz-agent`（ACP 合规 agent bin，min deps，无 Nostr） | 经 **ACP 适配器**挂为可选第二 agent 运行时 | ACP | **可选** | 会复制上游 Open SWE agent；仅当确需第二 agent 运行时；Buzz pre-1.0 快速迭代 |
| `buzz-dev-mcp`（rmcp 工具服：file/grep/shell/edit） | 经 **MCP** 作为 Hermes Docker 沙箱的工具面互补 | MCP | **可选** | Hermes 已有沙箱；仅作工具面扩展 |

**明确不采纳**（会引入 Nostr/Postgres 第二事实源，违反事实源边界）：
- `buzz-acp` 生命周期池 / 4759 行任务队列（第二任务状态源）
- `buzz-workflow` 工作流 + `RequestApproval` 审批门（第二状态/审批源）
- `buzz-pubsub` 事件总线（第二总线）
- `buzz-audit` hash-chain（第二审计源 — **禁止**）
- `buzz-auth` NIP-42/98 + git-via-Nostr（与 GitHub App 安装令牌模型冲突）
- `buzz-persona` Agent 身份（第二 Agent 身份源）

> **判定：Buzz 平台主干（Nostr+Postgres+全 Buzz）永不采纳。**

---

## 3. 接入哪些 Openship 能力 (INTEGRATE Openship — 延后 MVP-0 后)

| 能力 | 接入方式 | 接口 | 状态 | 条件 / 风险 |
|------|---------|------|------|-----------|
| 完整部署面（git 驱动部署 / 构建 / 容器部署 / 环境变量 / 日志 / 健康检查 / 版本历史 / **回滚** / Preview-Staging-Prod / 域名 / TLS / 数据卷 / RBAC / REST / **一等 MCP**） | 作为**独立 scoped-MCP 服务**运行，**不复制源码、不 fork** | MCP（路由生成、consent 门控） | **延后 MVP-0 后** | Hermes 当前不需要（prod 部署出 MVP-0）；`HARD_DENY=[tokens,auth,mcp]` + `filterToolsForPrincipal` 天然限制 Agent 仅 status-view / test-deploy |

**安全接入约束（来自 Openship MCP 层）：**
- Agent 拿**只读 MCP token** → 仅 GET → status-view。
- Agent 拿**受限 token，且仅授 preview/test 项目** → test-deploy。
- **不授生产项目任何 grant** → Agent 结构性无法触生产（`tools/list`/`tools/call` 拒绝，wildcard 拒受限主体）。
- 必配 scoped MCP token（Openship 跑 docker on host + 管 DNS/TLS，主机权限面大）。

---

## 4. GitHub 的职责 (唯一代码事实源)

- **GitHub 是代码 / commit / PR / CI / Review 的唯一权威事实源。**
- Issue = 任务入口；Draft PR = 交付物；GitHub Actions = CI 真相；Review = 人类/agent 评审真相。
- Hermes 控制层只读/写 GitHub（经 GitHub App 安装令牌），**不建立第二 GitHub 任务状态、第二 Review 事实、第二部署历史**。
- 外部系统（Buzz/Openship）的运行日志仅作辅助，权威归各自服务，且不与 GitHub 真相冲突。

---

## 5. Agent Runtime 的职责

- **可替换执行器**：Hermes 当前用上游 `langchain-ai/open-swe`（pin `ed12bb8d`）经 `protocol.py` 沙箱协议运行。
- Hermes **不实现 agent 循环**，仅持有协议包装与投递控制。
- **可选后续**：若需第二 agent 运行时，经 ACP 适配器接 `buzz-agent`（见 §2），但会复制 Open SWE，故非必须。

---

## 6. 部署服务的职责 (未来)

- **MVP-0 内：无真实部署服务**；仅 `OPERATIONS-RUNBOOK.md` smoke-scoped 手册 + `runtime/d2-real-smoke/`。
- **MVP-0 后（若需真实部署）：Openship 作独立 scoped-MCP 服务**，Hermes 经 MCP 调用，不复制其源码。
- Hermes **不持有部署真相的第二个副本**；部署权威明确归 Openship，运行日志辅助。

---

## 7. 事实源边界 (Fact-Source Boundary)

**禁止出现两个权威源：**
- ❌ 第二 GitHub 任务状态（不用 Buzz task queue / Nostr）
- ❌ 第二 GitHub Review 事实（不引外部评审真相）
- ❌ 第二部署历史（部署权威唯一归 Openship，若采用）
- ❌ 第二 Agent 身份事实（不用 Buzz persona）
- ❌ 第二不可同步审计日志（不采纳 Buzz hash-chain）

✅ 外部运行日志可作为辅助，前提是权威清晰、可独立验证、不与 GitHub 真相冲突。

---

## 8. 权限边界 (Permission Boundary)

- **用户 = 最终权威 / 验收方**：Hermes 自动化永不合并（fail-closed 不合并，#16）。
- **Agent 经 GitHub App 安装令牌** 受限作用域运行；令牌 lease-gated、内存缓存、投递后 zeroize。
- **未来 Openship Agent 调用**：经 scoped MCP token —— 只读→status-view；受限且仅授非生产项目→test-deploy；**无生产 grant 即无生产访问**。
- **永不**：授予 Agent 生产部署权限、持久化 GitHub 令牌、或让外部系统成为 GitHub 真相的裁决者。

---

## 9. 失败恢复路径 (Failure Recovery)

- **Hermes 控制平面（当前主干）：**
  - `reap_expired_leases` 回收超时 lease；
  - 幂等 `claim` / `complete` / `fail`（原子 `UPDATE…RETURNING`，无 TOCTOU）；
  - `delivery` 在 PUSHED / PR_FAILED 态**仅续投 PR**（不重复 commit/push）；
  - keepalive + retry budget。
- **若采用 Openship（延后）：** 利用其 `rollback-orchestrator.ts`（archive/restore/purge，`rollbackWindow` 默认 5 最大 20）作部署层回滚；Hermes 不持有第二回滚真相。
- **不采纳** Buzz 的 `pool_lifecycle` / `buzz-workflow` 续步恢复（需 Nostr/PG）。

---

## 10. 目标架构一句话总结

> **GitHub 唯一真相 · Hermes 控制层自研维持 KEEP 核心 · Agent 可替换(上游 Open SWE) · Buzz 仅可选薄 ACP/MCP 适配器(不引 Nostr/PG 主干) · Openship 延后作 scoped-MCP 部署服务(不复制源码) · 永不维护第二事实源 · 用户为最终合并权威。**
