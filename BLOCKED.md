# Hermes 智能体工程工作台阻塞项

更新日期：2026-07-28

## 1. GitHub App 环境未继承

- 状态：`BLOCKED_EXTERNAL_CREDENTIALS`
- 事实：当前执行上下文没有继承 Live GitHub 闭环所需的三个 GitHub App 环境变量。
- 影响：不能真实创建或刷新 Issue、Push、Draft PR、CI、Review；Task 11 Live 验收不得运行。
- 解锁条件：由 Human Owner 在受控运行环境提供最小权限配置，并先通过零写入 Preflight。
- 禁止替代：不得读取外部 Secret 文件内容，不得把 FakeGitHubClient、静态 JSON、`simulated` SHA 或手工 PR 记录当作 Live PASS。
- 保密：只记录变量未继承这一事实，不记录变量值、Token、Private Key 或 Secret 文件内容。

## 2. Hermes Learning OS 未授权

- 状态：`BLOCKED_USER_AUTHORIZATION`
- 事实：`yzhlx/hermes-learning-os` 当前在 `PROTECTED_REPOS` 中，本轮没有获得解除保护和写入该仓库的明确授权。
- 影响：Task 12 不得 Clone、修改、Push、创建 Issue、创建 PR 或运行写操作。
- 解锁条件：Human Owner 明确确认目标 Repo、Base、低风险功能、Worktree、文件范围、验收标准和是否允许 Draft PR。
- 禁止替代：不得静默修改 allowlist/protected list，不得把 smoke-test 仓库结果升级为 Learning OS 主项目验收，不得自动 Merge。

## 3. Deployment / WSL 最新状态

- 状态：`WINDOWS_OFFLINE_TEST_PASS / WSL_NOT_TESTED`
- 当前事实：Windows 当前环境执行 `tests/deployment` 为 `21 passed`，Exit `0`。
- 仍有限制：本批未运行 WSL Smoke，也未在 WSL 重跑 full suite；不得据此声称 Linux/WSL PASS。
- 禁止替代：不得删除测试、放宽断言，或把 Windows 离线合同结果升级为真实 Linux 部署验收。

## 4. Shell / Sandbox Helper 当前状态

- 状态：`RESOLVED_CURRENT_SESSION`
- 当前事实：本会话已能在授权工作区执行只读命令、定向修改、pytest 与 PowerShell acceptance；本轮 RED/GREEN 和回归均有当前进程原始输出。
- 历史事实：早期子任务曾受 helper setup/refresh 异常阻断，该记录不再代表当前状态。
- 禁止替代：仍不得绕过 Sandbox、扩大文件权限或读取未授权路径。

## 5. 外部技能读取当前状态

- 状态：`RESOLVED_CURRENT_SESSION`
- 当前事实：本会话已能读取获准的本机技能说明并按其约束执行；早期审批额度门禁为历史状态。
- 仍有限制：技能可读不扩大项目文件、Secret、仓库或远程操作授权。
- 禁止替代：不得借技能读取绕过工作区和安全边界。

## 6. 无 Merge 自动化能力（历史阻塞已解除）

- 状态：`RESOLVED_OFFLINE_TEST_PASS`
- 当前事实：生产 GitHub Client、Operator Action 与 Agent 工具均不公开 Merge callable；相关离线合同测试通过。
- 仍有限制：未执行真实 GitHub 写入；最终 Merge 继续只能由 Human Owner 在 GitHub 手工完成。
- 禁止替代：不得重新引入 Merge/Auto-merge API，不得用“当前没有调用”替代能力移除。

## 7. hermes_workbench Packaging 最新状态

- 状态：`STATIC_PASS / WHEEL_INSTALL_NOT_TESTED`
- 当前事实：`pyproject.toml` 已包含 `hermes_workbench*`，并声明 `web` 数据文件；源码树内导入与离线合同测试通过。
- 仍有限制：尚未构建 Wheel 并在隔离环境安装、导入，因此不得升级为发行包 PASS。
- 禁止替代：不得用当前工作目录或临时 `PYTHONPATH` 代替 Wheel 安装验证。

## 8. Hallmark / UI 最新状态

- 状态：`PARTIAL_OFFLINE_PASS / BROWSER_VALIDATION_NOT_TESTED`
- 当前事实：`.hallmark` 记录、`web/**` 与静态 UI/路径约束测试已存在；工作区外路径、编码穿越、未知文件与目录索引读取均被拒绝。
- 仍有限制：没有自动化 Playwright 交互、截图与 clean-console 证据；不得升级为浏览器验收 PASS。
- 禁止替代：不得用静态字符串测试、旧截图或硬编码 Agent 台词伪装真实浏览器交互。

## 9. 稳定 Windows 启动与持久在线入口未完成

- 状态：`BLOCKED_SCOPE_STARTUP`
- 事实：当前仍缺 `scripts/workbench_start.ps1`，也没有 Windows 计划任务或安装入口。
- 当前证据：`127.0.0.1:4175` loopback Smoke 曾真实通过，但服务随后已停止、临时文件已清理、端口已释放；历史 PID 只属于该次 Smoke 证据。
- 影响：不能声称工作台当前在线、浏览器关闭后后台仍持续运行，或用户已有无需终端的稳定入口。
- 解锁条件：把启动脚本与计划任务/安装入口纳入白名单，补充可重复安装、启动、心跳、重启与卸载测试，并确认只清理本工作台拥有的进程。
- 禁止替代：不得把一次性测试进程、已停止的 PID、端口可用或手工 Python 命令升级为持久在线 PASS。

## 10. 同一 Job / 同一 PR 返工最新状态

- 状态：`OFFLINE_TEST_PASS / GITHUB_REAL_WRITE_PASS=NOT_TESTED`
- 当前事实：Host Runner 已能读取受信 Scheduler review、复用同一任务分支与 Draft PR、更新 Head SHA，并对重复调度和部分信号恢复保持幂等；相关 Fake/Mock 离线测试通过。
- 仍阻塞：未执行真实 GitHub App scope 查询、真实 PR/Check/Review/label/round-2 push；禁止升级为 Live PASS。
- 禁止替代：不得用第二个 PR、模拟 SHA、Fake GitHub、Mock HTTP 或文字汇报代替真实同一 PR 证据。

## 11. 专用 Host Runner wiring 未完成

- 状态：`BLOCKED_SCOPE_HOST_RUNNER`
- 事实：现有后台 Worker 仍执行 smoke 路径并报告 `commit_sha="simulated"`；尚未把真实 Host Runner 作为工作台的专用受控执行路径接入。
- 影响：Operator API 和调度状态机的局部通过，不证明网页命令会触发真实 worktree、Commit、Push、Draft PR、CI、Review 与返工。
- 解锁条件：明确专用 Host Runner wiring 的入口、进程所有权、队列租约、心跳、取消与失败恢复边界，并以真实 Commit/PR/CI 证据完成受控验收。
- 禁止替代：不得把 smoke Worker、FakeGitHubClient、`simulated` SHA 或静态 Agent 消息升级为真实工程闭环。

## 12. Operator API Human Owner 身份认证未纳入本批

- 状态：`BLOCKED_SCOPE_OPERATOR_AUTH`
- 事实：当前 loopback session + Origin + CSRF 只提供请求来源约束，不构成独立 Human Owner 身份认证。
- 影响：本批不得把本地 Operator API 升级为已认证的 Human Owner 控制面。
- 解锁条件：Human Owner 单独批准身份来源、凭据生命周期、首次配置、恢复与撤销策略后，以新任务实施并验收。
- 禁止替代：不得把 loopback、Cookie 或 CSRF 当作用户身份；不得在本批静默引入新长期 Secret。

## 13. 最终人工 Approval 缺少实时 GitHub 事实复验

- 状态：`BLOCKED_SCOPE_LIVE_ACCEPTANCE`
- 事实：本批未实现 approve 前重新读取当前 PR Head、CI 和同 Head 独立 Review 的 live gate。
- 影响：当前 `ready_for_manual_merge` 只能作为本地控制状态，不能升级为真实 GitHub acceptance PASS。
- 解锁条件：单独批准 Acceptance Gate 的事实来源与失败策略，并以真实 GitHub Head/Check/Review 证据验收。
- 禁止替代：不得用 SQLite 缓存字段、Mock 或历史 Review 代替当前 GitHub 事实。

## 14. 同一 PR 返工状态索引

- 当前状态已统一记录在第 10 节；本节不再维护重复事实，避免出现相互矛盾的当前态。

## 阻塞处理规则

- `BLOCKED` 不得改写为 PASS、DONE 或“基本可用”。
- 阻塞解除必须补充当前时间、解锁人、原始验证命令和结果。
- 外部凭据、用户授权和生产部署属于独立门禁；一个门禁解除不代表其他门禁自动解除。
- 所有 Merge 仍由 Human Owner 手工完成，任何阻塞解除都不授权自动 Merge。
