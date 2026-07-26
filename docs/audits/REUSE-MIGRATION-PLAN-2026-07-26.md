# 复用迁移计划 (Reuse Migration Plan)

**Audit date:** 2026-07-26
**Reviewer:** gstack-product-reviewer (analysis + docs only; no install/build/run/commit)
**Hermes HEAD:** `8806010ca3291be79d886ba5b70c946c19585299`
**Buzz HEAD:** `dd222a5` (v0.4.26) · **Openship HEAD:** `529ff19` (v0.3.0) — 均为 Apache-2.0

## 0. 核心约束

- **Hermes Learning OS 开发可尽早继续，不必等待整个协作平台完善。** 早期步骤必须**非阻塞**。
- **零 REPLACE**：本计划只做"冻结 / 保持 / 可选接入"，不做任何"停止自研改用外部"。
- **不引入第二事实源、不复制外部源码、不 fork、不 commit**（由 team-lead 提交）。
- 所有"接入"步骤均为**可选**或**延后 MVP-0 后**，且可独立回滚。

---

## 步骤总览（执行顺序）

| 序 | 步骤 | 阻塞 Hermes Learning OS? | 可选/延后 |
|----|------|------------------------|----------|
| S0 | 冻结通用平台扩张（已做） | 否 | — |
| S1 | 用现有 GitHub 中心循环继续交付 Hermes Learning OS | 否 | — |
| S2 | 标记 KEEP 核心为"冻结接口契约"，建立事实源边界守卫 | 否 | — |
| S3 | 补齐 Reviewer/复审/Scheduler/CI 等缺引擎能力的自研路线（按需求） | 否 | 按需 |
| S4 | （可选）若需第二 agent 运行时，经 ACP 适配器接 `buzz-agent` | 否 | 可选 |
| S5 | （可选）若需沙箱工具面扩展，经 MCP 接 `buzz-dev-mcp` | 否 | 可选 |
| S6 | （延后 MVP-0 后）若需真实部署，Openship 作 scoped-MCP 服务 | 否（对当前开发） | 延后 |
| S7 | 永久不采纳：Buzz Nostr/Postgres 主干 + Openship 源码复制 | 否 | 禁项 |

---

## S0. 冻结通用平台扩张（已完成）

- **目标**：停止把协作架构当作独立平台演进。
- **修改范围**：无代码改动；决策冻结。
- **前置条件**：本审计结论 + team-lead 批准。
- **验收标准**：无新"平台化"模块进入规划；新需求优先 GitHub 中心方案。
- **回滚方法**：N/A（决策层）。
- **是否需用户操作**：否。
- **是否阻塞 Hermes Learning OS 继续开发**：**否**。

---

## S1. 用现有 GitHub 中心循环继续交付 Hermes Learning OS

- **目标**：以当前已离线测试通过的控制平面 + 投递层 + Docker 沙箱 + 上游 Open SWE agent，持续开发/测试/评审 Hermes Learning OS。
- **修改范围**：无（沿用 `webhook_receiver` → `control_plane` → `worker` → `sandbox` → Open SWE → `delivery` → Draft PR 流程）。
- **前置条件**：`8806010` 离线测试 107 passed（2 个 `test_redact` 预存失败已知）。
- **验收标准**：每个 Hermes Learning OS 迭代仍走 GitHub Issue → Draft PR → 人类合并；人类最终合并边界 intact。
- **回滚方法**：N/A（现行流程）。
- **是否需用户操作**：否（开发者照常提 Issue/Review）。
- **是否阻塞 Hermes Learning OS 继续开发**：**否**（这正是主干路径）。

---

## S2. 标记 KEEP 核心为"冻结接口契约" + 事实源边界守卫

- **目标**：把 20 项 KEEP 能力的关键接口（webhook 验签、任务状态机、Draft PR、审计 `event_id` 幂等、人类不合并）固化为契约，防止后续误引第二事实源。
- **修改范围**：仅文档/测试增强 —— 在 `AGENTS.md` / `OPERATIONS-RUNBOOK.md` 标注"事实源边界禁项"；可为 `control_plane.claim` / `event_id` 幂等补契约测试。
- **前置条件**：S1 在跑。
- **验收标准**：文档明确列出 5 类禁项（第二任务状态 / Review 事实 / 部署历史 / Agent 身份 / 审计日志）；CI 含契约测试守护。
- **回滚方法**：仅文档/测试改动，git revert 即可。
- **是否需用户操作**：否。
- **是否阻塞 Hermes Learning OS 继续开发**：**否**。

---

## S3. 缺引擎能力自研路线（按需求触发，非强制）

- **目标**：对 NOT_TESTED 的策略/缺引擎能力（#4 Scheduler、#12 Reviewer、#13 Review 反馈、#14 自动返工、#15 二轮复审、#23 云端校验），在**确有需求时**自建 Hermes 控制层引擎，不引外部。
- **修改范围**：新增 Hermes 源码模块（如 `control_plane` 内 scheduler/reviewer 子模块）；不动外部。
- **前置条件**：业务侧明确需要该能力（如多仓并发需 Scheduler、自动复审需 Reviewer）。
- **验收标准**：新引擎离线测试通过，且**不创建第二事实源**（状态仍落 SQLite/GitHub）。
- **回滚方法**：模块独立，git revert 单模块。
- **是否需用户操作**：否（除非涉及新 Review 流程，需人类确认验收标准）。
- **是否阻塞 Hermes Learning OS 继续开发**：**否**（按需，缺省维持策略态）。

---

## S4. （可选）ACP 适配器接 `buzz-agent`

- **目标**：若确需第二 agent 运行时（如异构模型/隔离会话），经 ACP 协议将 `buzz-agent`（min deps, 无 Nostr）挂为可选执行器。
- **修改范围**：新增薄 ACP 适配器（Hermes 侧），`buzz-agent` 以**独立二进制/服务**运行，**不复制其源码、不引 Nostr**。
- **前置条件**：① 确需第二 agent；② Buzz 升到稳定版（目前 v0.4.26 pre-1.0，需评估 churn）；③ 接受与上游 Open SWE 的功能重叠。
- **验收标准**：Hermes 控制层可经 ACP 派发任务给 `buzz-agent`，结果仍经 Hermes 投递入 GitHub Draft PR；不引入 Nostr/PG。
- **回滚方法**：移除 ACP 适配器（薄层），恢复单 agent（Open SWE）；`buzz-agent` 进程停止即隔离。
- **是否需用户操作**：否。
- **是否阻塞 Hermes Learning OS 继续开发**：**否**（完全可选）。

---

## S5. （可选）MCP 工具面接 `buzz-dev-mcp`

- **目标**：若需扩展 agent 工具面（file/grep/shell/edit），经 MCP 将 `buzz-dev-mcp` 作为 Hermes Docker 沙箱的工具面互补。
- **修改范围**：Hermes agent 配置增加 MCP server 指向 `buzz-dev-mcp`（独立进程）；**不复制源码**。
- **前置条件**：① Hermes 现有 Docker 沙箱不足以覆盖某工具场景；② 接受 pre-1.0 风险。
- **验收标准**：工具执行结果仍落 Hermes 沙箱/投递链路；无 Nostr 依赖进入 Hermes 主干。
- **回滚方法**：从 MCP server 列表移除 `buzz-dev-mcp`，恢复纯 Docker 沙箱。
- **是否需用户操作**：否。
- **是否阻塞 Hermes Learning OS 继续开发**：**否**。

---

## S6. （延后 MVP-0 后）Openship 作 scoped-MCP 部署服务

- **目标**：当 Hermes 退出 MVP-0 且确需真实部署时，将 Openship 作为**独立服务**运行，Hermes 经其**一等 MCP** 调用部署能力。
- **修改范围**：① 部署并独立运行 Openship 服务（docker compose，Apache-2.0，不复制源码、不 fork）；② Hermes 增加 scoped-MCP 客户端配置；③ 签发 **只读 MCP token**（status-view）与**受限 token 仅授 preview/test 项目**（test-deploy）。
- **前置条件**：① prod 部署出 MVP-0 范围，须到 MVP-0 后才触发；② Openship 升到可接受成熟度；③ 主机权限面评估（docker on host + DNS/TLS）。
- **验收标准**：Agent 经 MCP 仅能 status-view / test-deploy；**无生产项目 grant → 结构性无生产访问**；部署回滚经 Openship `rollback-orchestrator` 可用；Hermes 不持有第二部署真相。
- **回滚方法**：撤销 MCP token + 停止 Openship 服务；Hermes 退回 `OPERATIONS-RUNBOOK.md` 文档态，无代码依赖残留。
- **是否需用户操作**：是（运维部署 Openship 服务、签发 scoped token、确认不授生产 grant）。
- **是否阻塞 Hermes Learning OS 继续开发**：**否**（MVP-0 内根本不需要；延后）。

---

## S7. 永久不采纳项（禁项守卫）

- **目标**：永久禁止引入 Buzz Nostr/Postgres 主干（task queue / workflow / pubsub / audit hash-chain / auth / persona）与 Openship 源码复制。
- **修改范围**：无（决策 + 文档禁项 + code review 守卫）。
- **前置条件**：S2 事实源边界文档已落。
- **验收标准**：任何 PR 引入 Nostr/Postgres 作 Hermes 事实源主干，或被审议复制 Openship 源码时，被 review 拦截。
- **回滚方法**：N/A。
- **是否需用户操作**：否。
- **是否阻塞 Hermes Learning OS 继续开发**：**否**。

---

## 迁移风险与依赖

| 风险 | 缓解 | 关联步骤 |
|------|------|---------|
| 缺引擎能力（Reviewer/Scheduler 等）长期 NOT_TESTED | S3 按需自建，不引外部 | S3 |
| Buzz pre-1.0 快速迭代导致可选适配器失稳 | S4/S5 设为可选，接入前评估版本；薄适配器易撤 | S4/S5 |
| Openship 主机权限面大（docker on host + DNS/TLS） | S6 强制 scoped MCP token；不授生产 grant | S6 |
| 误引第二事实源 | S2 边界文档 + S7 禁项守卫 + CI 契约测试 | S2/S7 |

---

## 结论

> **Hermes Learning OS 现在就能继续开发（S0+S1 已就位且非阻塞）。** 所有外部接入均为可选或延后，且每一步都可独立回滚、不引入第二事实源。无任何 REPLACE、无 fork、无源码复制。
