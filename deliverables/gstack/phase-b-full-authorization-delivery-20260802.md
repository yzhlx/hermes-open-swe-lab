# Phase B 全授权交付报告 — 2026-08-02(第二轮)

**状态**: `PHASE_B_IMPLEMENTED_AND_DEPLOYED`(代码全实现 + 云端部署 + 真实生命周期验证通过)
**执行**: Hermes(监工)+ Codex CLI(danger-full-access)

---

## 本轮完成(用户全授权后)

### 1. 生产接线(codex,commit `4575133`)— `phase-b/production-wiring-20260801`
| 模块 | 改动 |
|---|---|
| `worker.py` | `HERMES_SANDBOX_BACKEND=echo\|docker` 环境选择;默认 echo(保持离线测试);docker 用 D2 验证过的 `HermesDockerSandboxBackend`;`_run_job` 现在跑真实 agent(不再是硬编码 echo);保留 PB-4 真实本地提交 + 全部脱敏/keepalive |
| `agent_runner.py` | `HERMES_AGENT_BACKEND=fake\|relay` 选择;`RelayAgentRunner` 通过 `RelayClient`(use_responses=False)调模型,写回沙箱 |
| `reviewer.py` | `HERMES_REVIEWER_LLM=1` 开启 LLM 审查路径;失败/未启用时回退规则判定;allowlist 检查保持在最前 |
| `scheduler.py` | D3Orchestrator 按 env 构造 worker(sandbox+agent 后端) |
| `constants.py` | 新增 4 个 HERMES_* env 常量 + 默认值 |
| 测试 | `tests/test_production_wiring.py` 5 测试(后端选择、relay 注入、LLM approve、LLM 回退、env 默认) |

**独立复核**:236 测试全绿;未触碰 delivery.py/db.py/control_plane.py(后因 bug 修复触碰 control_plane.py 一次,见下)。

### 2. 云端控制面部署(真实环境)
- 集成分支源码 → 云端 `/opt/hermes-open-swe-lab/deploy`(SSH 129.211.0.213)
- venv 就绪(Python 3.12.3 + pytest)
- 229 个功能测试在云端全绿(2 个 structural_scan 失败为环境差异——部署目录无 .git,`git grep` 无法运行,非代码问题)
- systemd env 配置(fail-closed `ALLOWED_WORKER_TOKENS`)

### 3. 真实生命周期验证(云端,非 mock)
```
pending → agent_done → USER_ACTION_REQUIRED → FINAL_ACCEPTED → completed
事件链: FINAL_ACCEPTANCE → FINAL_ACCEPTED → TASK_COMPLETED ✅
```
- PB-3 门禁实跑:`complete()` 在 Human Owner 验收前被拒(`completion_before_acceptance`)✅
- HTTP 层冒烟:register 成功、无 token 请求被拒(`unknown_worker_token`,fail-closed)✅

### 4. 发现并修复真实 bug(commit `c22c1a1`)
- **`control_plane._finish` 空 updates SQL 错误**:`complete()` 不带 result 时生成 `SET state=?, ended_at=?,  WHERE` → 语法错误
- 修复:updates 为空时省略 SET 子句
- 新增回归测试 `PBFinishNoResultRegression`(此前 237 测试未覆盖此路径——这正是真实环境验证的价值)

---

## 累计状态(两次会话)

| 层 | 状态 |
|---|---|
| 12 点验收代码 | ✅ 100% 实现 |
| PB-1~8 任务 | ✅ 全部实现 |
| 测试 | ✅ 237 passed(本地)/ 229 功能测试(云端)|
| 生产接线(真实 sandbox + relay agent + LLM reviewer) | ✅ env-gated 就绪 |
| 云端部署 | ✅ 已部署,服务可启动 |
| 真实生命周期 | ✅ 验证通过(非 mock)|
| 真实端到端(Issue→真 LLM 干活→真 PR) | ⚠️ 需 relay 凭据 + LangSmith Sandboxes(403 未解)+ webhook 公网 |
| Draft PR(主仓库) | ⚠️ 需 GitHub token(App 仅装 smoke-test)|

---

## 仍需 Human Owner(3 项,均为外部凭据,无法自动绕过)

1. **GitHub token**(或把 App `hermes-open-swe-lab-yzhlx` 安装到主仓库)——建 8 个 Draft PR
2. **Relay 凭据**(`OPEN_SWE_OPENAI_BASE_URL/MODEL/API_KEY`)——真实 Coding Worker 调用 LLM
3. **LangSmith Sandboxes 权限**——之前 403,解锁后真实沙箱闭环

> 这三项是 GitHub/LangSmith/OpenAI 的安全机制,任何自动化都无法代替 owner 操作。

---

## 分支(全部已推送 GitHub)

```
monitor/phase-b-integration-20260801   ← 全部集成分支(c22c1a1)
phase-b/production-wiring-20260801     ← 生产接线(4575133)
phase-b/pb7-escalation-planner-20260801  phase-b/pb8-test-gaps-20260801
phase-b/pb1-pb4-...  demo/pb23-...  snapshot/pb5-...  demo/production-entrypoints-...
tooling/collaboration-doc-semantic-checker
```
