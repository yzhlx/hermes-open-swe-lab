# BUZZ-INTEGRATION-BOUNDARY.md

- **日期 (Date):** 2026-07-26
- **场景 (Scenario):** 架构设计 (Architecture Design)
- **STATUS:** `COLLABORATION_V1_ARCHITECTURE_DESIGNED`
- **关联文档:** `HUMAN-AGENT-WORKSPACE-ARCHITECTURE.md`、`ROLE-AND-PERMISSION-MODEL.md`、`ACTIVITY-AND-INTERVENTION-EVENT-MODEL.md`、`COLLABORATION-V1-IMPLEMENTATION-ROADMAP.md`

---

## 0. 目的与范围 (Purpose & Scope)

本文档定义 V1 阶段 **Buzz 与 Hermes 的集成边界**：哪些 Buzz 能力**明确不采用**为 Hermes 权威机制，哪些 Buzz 职责**允许**作为交互/展示层，以及 Hermes 自身的权威边界。所有结论与 `AGENTS.md`（第 4、8 节）、复用结论（0 REPLACE；21 KEEP + 2 可选 INTEGRATE + 1 延后 INTEGRATE）、以及 `constants.py` / `delivery.py` 完全一致。

> **操作约束:** 本阶段**不安装 / 不运行 / 不 Fork Buzz**。本文档仅定义接口与边界，供主理人后续接入。严禁将任何 Buzz(`.rs`) / Openship(`.ts`) 源码复制进仓库（仅可引用其设计作为"借鉴/不采纳"依据）。

---

## 1. V1 明确不采用 (NOT Adopted in V1 — 英文原名原样)

以下 Buzz / Openship 能力**不作为** Hermes 的权威机制或核心路径：

| # | 不采用项 (NOT Adopted) | 说明 / 替代权威 |
|---| --- | --- |
| 1 | **Buzz Git 托管** | Git 权威仍在 GitHub；Hermes 经 `delivery.py` 受控推送/开 Draft PR。 |
| 2 | **Buzz Nostr 身份作为 Hermes 权威身份** | 身份权威为 Hermes Role Definition（`ROLE-AND-PERMISSION-MODEL.md`）；Buzz Persona 仅展示/运行容器。 |
| 3 | **Buzz 任务队列作为权威状态** | 任务权威状态在 Hermes 控制层（`MVP-0-ARCHITECTURE.md` 的 SQLite 任务队列 + Event Store）。 |
| 4 | **Buzz Workflow 作为核心状态机** | 核心状态机在 Hermes 控制层（任务生命周期、返工循环、`MAX_ROUNDS=2`）。 |
| 5 | **Buzz Audit Hash Chain 作为权威审计源** | 权威审计为 GitHub 记录 + Hermes 控制层 Event Store / 运行报告（`runtime/events.db`、`runtime/runs/*.jsonl`）。 |
| 6 | **Buzz Issue 系统** | Issue 权威为 GitHub Issue（`ALLOWED_GITHUB_REPOS`）。 |
| 7 | **Buzz Merge Coordinator** | 合并决策仅 `Human Owner`（手动）；自动化永不 merge（`delivery.py` 无 merge 方法）。 |
| 8 | **Buzz 自动审批门** | 审批/授权门禁在 Hermes 控制层 + GitHub；Buzz 当前未完整接通的 Workflow Approval Executor **不得**作生产核心门禁。 |
| 9 | **Buzz Mesh** | V1 不采用分布式 Mesh；单控制层 + 单 Worker 模型延续。 |
| 10 | **Huddles** | V1 不采用语音/实时会议；协作经频道/Canvas/Activity Feed 异步进行。 |
| 11 | **Buzz 移动端** | V1 不开发/不接入移动端（与 `AGENTS.md` §3 移动适配 out-of-scope 一致）。 |
| 12 | **Openship 运行时接入** | 部署执行层 V1 不接入；属延后 `#22 INTEGRATE`（Openship scoped-MCP），MVP-0 后。 |

> **补充（Buzz 主干永久不采纳为事实源）：** Buzz 主干的 Nostr / Postgres / 任务队列 / Workflow / Audit Hash Chain / Issue / Merge Coordinator / 自动审批 均**永久不**作为 Hermes 权威事实源。

---

## 2. Buzz 在 V1 中的允许职责 (Allowed Responsibilities — 交互/展示层)

Buzz 仅作为**人机交互与协作展示层**，允许承担以下职责（详见 `HUMAN-AGENT-WORKSPACE-ARCHITECTURE.md` §1.1）：

| # | 允许职责 (Allowed) | 说明 |
|---| --- | --- |
| 1 | **频道 (Channels)** | 承载默认频道 `#control-room #planning #implementation #review #qa #release #user-action-required` 与临时频道 `#task-<task-id>`（归属规则见路线图"工作空间设计"）。 |
| 2 | **角色展示 (Role Display)** | 以 Buzz Persona / Team 形式展示 Hermes 逻辑角色；**仅展示**，权威定义见 `ROLE-AND-PERMISSION-MODEL.md`。 |
| 3 | **Activity Feed 显示 (Activity Feed Display)** | 订阅并渲染 Hermes 控制层生产的 21 类事件（摘要级，见 `ACTIVITY-AND-INTERVENTION-EVENT-MODEL.md`）。 |
| 4 | **Canvas 展示 (Canvas Display)** | 渲染 12 字段 Canvas 模板；Canvas 为协作展示层，非唯一证据源。 |
| 5 | **搜索聚合 (Search Aggregation)** | 聚合 Buzz 消息/线程、Canvas、Hermes 状态事件、GitHub 记录、审计/运行报告；展示时标注来源/时间/权限/证据链接。 |
| 6 | **通知 (Notifications)** | 显示并推送 `USER_ACTION_REQUIRED` 等人工介入提醒；@ 相关 `Human Owner`。 |

> **核心原则：** Buzz 的所有展示内容必须可追溯到 GitHub 或 Hermes 控制层权威记录；Buzz 自身不得产生与 GitHub 冲突的第二任务真相。

---

## 3. Hermes 权威边界 (Hermes Authority Boundary)

与 `AGENTS.md` 完全一致：

1. **GitHub 唯一权威事实源 (Single Source of Truth):** Issue / Commit / Branch / Draft PR / CI / Review 的权威记录全部在 GitHub（`ALLOWED_GITHUB_REPOS = {"yzhlx/hermes-open-swe-smoke-test"}`）。
2. **不创建第二任务真相 (No Second Task Truth):** Buzz/Canvas/搜索/Activity Feed 均为协作/聚合层；任何权威结论须可同步回 GitHub 或 Hermes 控制层存储。`yzhlx/hermes-learning-os` 属 `PROTECTED_REPOS`，永不出现于自动化写入。
3. **凭据隔离 (Credential Isolation):** 控制器/控制层从不持有长期令牌；令牌仅在 `delivery.py` 的 push 步骤局部注入并丢弃；Buzz 不持有或转发凭据。
4. **fail-closed:** 所有交付与授权经 `delivery.py`：显式 `DeliveryAuthorization`（task+repo+commit 绑定）、Draft PR 强制（`draft=True`）、永不 merge、幂等（`idempotency_key`）；任何缺失授权/越权/冲突均 fail-closed 终止。
5. **身份权威唯一:** Hermes Role Definition 为唯一身份事实源；Buzz Persona 数据库不得成为第二身份事实源。

---

## 4. 飞书边界复述 (Feishu Boundary — restated)

- 开发协作层 Buzz **取代**原飞书开发协作入口；开发协作走 Buzz。
- Learning OS 产品层是否保留飞书学习消息入口属未来独立产品决策，本轮不删不改。
- 严格区分"开发 Hermes"与"使用 Hermes Learning OS"；本架构仅覆盖前者。Hermes Learning OS 当前未激活（Activation Gate 6 前置），属 `PROTECTED_REPOS`，本阶段不开发。

---

## 5. 复用结论落地 (Reuse Conclusions Applied)

- **0 REPLACE:** 不替换既有 MVP-0 组件；本架构在其之上叠加协作层。
- **21 纯 KEEP:** 既有代码/组件保持不动。
- **2 可选 INTEGRATE:** `#6 沙箱 → buzz-dev-mcp`、`#7 Agent → buzz-agent ACP`（仅接口/兼容性评估，本轮不接入真实 `buzz-agent`）。
- **1 延后 INTEGRATE:** `#22 部署 → Openship scoped-MCP`（MVP-0 后；V1 不接入）。
- **Buzz 主干事实源:** Nostr/Postgres/任务队列/Workflow/Audit Hash Chain/Issue/Merge Coordinator/自动审批 永久不采纳为 Hermes 权威事实源。

---

## 6. 与既有安全文档一致性 (Consistency with SECURITY-BOUNDARIES.md)

- **B1** `yzhlx/hermes-learning-os` 保护 → 本文 §1/§3 重申永不自动化访问。
- **B4** PR-only 交付 → Buzz Merge Coordinator 不采用（§1-#7）。
- **B5/B11** 凭据/脱敏 → Buzz 不持有凭据（§3-3）；Activity 敏感字段脱敏（见事件文档）。
- **B7/B15/B16** 沙箱/Worker pull/Workspace 隔离 → Buzz 不引入旁路。
- **无第二事实源** → 贯穿 §1/§2/§3。
