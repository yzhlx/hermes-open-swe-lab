# D3 能力缺口审计（集成后、补齐前）

> 对照 `D3-IMPLEMENTATION-PLAN.md` 与 `d3-integration` 真实代码（三分支合并后，HEAD `9809448`）。
> 状态图例：
> - ✅ 已实现（REAL，离线验证过）
> - 🟡 部分实现（有基础设施/半成品，主闭环未接通）
> - ⬜ 未实现（代码中完全没有）
> - 📄 仅设计（只在 D3-IMPLEMENTATION-PLAN.md，无代码）
> - 🚫 真实环境 NOT_TESTED（代码存在但需凭据/服务器/密钥，本机无法跑）

## 关键结论

主 D3 闭环（事件 → 任务 → Worker → Sandbox → Agent → Push → Draft PR → CI → Reviewer → round-2 → 复审 → 用户验收）**此前是设计态**（`D3-IMPLEMENTATION-PLAN.md` 第 1 行即 "DESIGN ONLY. NOT IMPLEMENTED."）。

`d3-design` 分支落地的是**底层安全硬门**（验签/重放/原子 claim/keepalive/脱敏/allowlist），不是主闭环编排。
`provider-live` 落地的是 Relay Adapter（离线 P1–P6）。
`d3-deployment` 落地的是部署资产（systemd / Nginx / 备份 / 日志轮转 / Control Plane App）。

**25 项能力逐项：**

| # | 能力 | 状态 | 证据 / 缺口 |
|---|------|------|------|
| 1 | GitHub Issue Webhook 转任务 | 🟡 部分 | `webhook_receiver.py` 对任意 allowed-repo 事件建通用 job，但**不按事件类型路由**，**未强制 (repo,issue_number) 任务幂等** |
| 2 | issue_comment 后续指令转任务 | ⬜ 未实现 | 无 comment→follow-up 逻辑 |
| 3 | delivery 幂等去重 | ✅ 已实现 | `deliveries` 表 UNIQUE，`webhook_receiver` 去重（test_03d/03h 验证） |
| 4 | 仅接受 smoke-test 仓库 | 🟡 部分 | webhook 收口有 `ALLOWED_REPOS` 白名单；但 **push 侧未校验远端 URL 是否为白名单仓库** |
| 5 | GitHub App JWT 生成 | ⬜ 未实现 | 无 `github_app.py` / JWT 签发 |
| 6 | 短期 Installation Token 按任务签发 | ⬜ 未实现 | 无 Token Broker |
| 7 | Token 仅发给持有任务租约的 Worker | ⬜ 未实现 | 无租约门控的 Token 端点 |
| 8 | Token 不落 SQLite/JSONL/日志 | 🟡 部分 | `docker_sandbox._redact` 已覆盖命令内嵌密钥；但无 Broker，且 `events` 表 `command` 列写入的是 `redact(cmd)`（已脱敏）。新增 Token 路径需保证同样不落库 |
| 9 | Docker 内 clone 测试仓库 | 🟡 部分 | `docker_sandbox.git_clone` 存在，但无 agent 流程调用它 |
| 10 | Agent 修改文件和运行测试 | 🟡 部分 | `EchoSandboxBackend.write_file` / `docker_sandbox` edit 存在，但无 agent 执行流 |
| 11 | Push 独立任务分支 | 🟡 部分 | `docker_sandbox.push(use_token=True)` 已支持；无编排调用 |
| 12 | 创建 Draft PR | ⬜ 未实现 | 无 PR 创建逻辑 |
| 13 | 获取 CI 状态 | ⬜ 未实现 | 无 CI 状态查询 |
| 14 | CI 失败状态处理 | ⬜ 未实现 | 无 |
| 15 | 独立 Reviewer 运行 | ⬜ 未实现 | 无 reviewer 模块 |
| 16 | Reviewer 与实现 Agent 角色隔离 | ⬜ 未实现 | 无 |
| 17 | Scheduler 独占 round-2 标签设置权 | ⬜ 未实现 | 无 scheduler |
| 18 | 第二轮复用同一 PR | ⬜ 未实现 | 无 |
| 19 | 第二轮产生新的 Commit SHA | ⬜ 未实现 | 无 |
| 20 | 第二轮 Reviewer 复审 | ⬜ 未实现 | 无 |
| 21 | 禁止自动合并 | 📄 约定 | 无 merge 代码（靠"不实现"保证）；但无显式 guard/断言 |
| 22 | 用户最终验收 | 📄 设计 | 手动，未实现等待态 |
| 23 | Worker 断线恢复 | 🟡 部分 | `claim` 幂等重认领 + `reap_expired_leases` + `keepalive`（test_04b 验证）。但无"任务级断点续跑"（agent 中途失败需从同一 job 续跑，而非新建 job） |
| 24 | Installation Token 过期重签 | ⬜ 未实现 | 无 |
| 25 | Webhook 重复投递不产生重复 PR | 🟡 部分 | delivery 去重已防同 delivery 重复 job；但**任务幂等未强制**，不同 delivery 同 Issue 会建多个 job → 潜在多 PR |

## 必须补齐的能力（进入实现）

- A. 事件路由：按 `issues/issue_comment/pull_request/check_run` 路由；严格白名单；无关事件安全忽略；**任务幂等 (repo,issue_number)**。
- B. GitHub App Token Broker：JWT 生成、Installation Token 按需签发、内存缓存+到期前刷新、不落库不落日志、仅白名单仓库、租约门控、不进审计导出。
- C. 工作流状态机：幂等可恢复，每阶段写 Event Store，同 Issue 不重复任务，同任务不重复 PR，round-2 复用同 PR，禁止自动 Merge。
- D. Agent 与 Reviewer：不同 role；Reviewer 不复用 Agent 结论；结构化 verdict；blocking→round-2；non-blocking 不无限返工；最多两轮；两轮后仍 blocking 则停等用户。
- E. Relay 集成接入真实 Agent 调用路径：`use_responses_api=false`、禁 WebSocket Responses、禁跨 Provider 回退、LangSmith Gateway=false、Token/耗时写证据（API Key 不进证据）。

## 真实环境限制（本机无法验证，需服务器/凭据）

- 🚫 真实 GitHub App JWT/Installation Token 签发（需 `GITHUB_APP_PRIVATE_KEY_PATH` + 服务器）
- 🚫 真实 Webhook（需公网/隧道 + `GITHUB_WEBHOOK_SECRET`）
- 🚫 真实 Relay 调用（需 `/opt/hermes-open-swe-lab/.env` 三变量）
- 🚫 真实 Docker 生命周期（本机无 daemon；D2.5 已在有 daemon 环境 PASS）
- 🚫 云端 systemd/nginx/备份恢复（需登录云服务器）
- 🚫 `hermes-learning-os` 永远不访问

> 补齐后的代码路径全部具备，离线用 Fake 全部可验证；真实路径代码存在但标记 🚫 NOT_TESTED，不阻塞集成。
