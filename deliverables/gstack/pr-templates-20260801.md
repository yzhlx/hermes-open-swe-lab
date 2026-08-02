# Phase B 交付清单(8 个 Draft PR 模板)

> 每个 phase-b 分支对应一个 Draft PR,均指向 `canonical/phase-b-baseline-20260726`。
> 纪律:一任务一分支一 PR;Draft 状态;不 merge(Human Owner 授权后)。

## PR 模板(通用)

**Scope**: Phase B 任务实现,本地离线测试全绿(231 passed),未做真实 GitHub 写操作。
**Changed files**: 见各分支 diff。
**Commands executed**: `.venv/bin/pytest -q --ignore=tests/deployment` → 231 passed。
**Tests**: 178 基线 + 新增 PB 测试(每任务见下)。
**Security implications**: 无密钥提交;交付纪律(push/PR 仅限 `delivery.py`;ReleaseAgent 唯一 `deliver()` 调用者)已通过结构性测试。
**Known limitations**: 真实端到端(D3)未跑;需要 LangSmith Sandboxes 权限 + relay 凭据。
**Rollback**: revert 分支;删除新增文件。
**User actions still required**: H-MERGE 授权;真实 CI 验证;LangSmith/relay 凭据。

---

## PR 1 — `phase-b/pb1-pb4-release-agent-critical-path-20260727` (fe87138)
- PB-1: 收敛交付 → ReleaseAgent → `delivery.py::deliver()`(杀双路径)
- PB-4: Coding-Worker 本地提交 + ReleaseAgent 交接
- 新增: `release_delivery_coordinator.py`, `tests/test_pb1_release_agent_delivery.py`, `tests/test_pb4_coding_worker_handoff.py`

## PR 2 — `demo/pb23-production-final-acceptance-20260727` (f4dd1fc)
- PB-2/3: FINAL_ACCEPTANCE 状态机 + `complete()` 门禁
- 新增: `tests/test_pb23_production_final_acceptance.py`

## PR 3 — `snapshot/pb5-e71a308` (e71a308)
- PB-5: ALLOWED_WORKER_TOKENS fail-closed
- 新增: `tests/test_pb5_worker_tokens_fail_closed.py`

## PR 4 — `demo/production-entrypoints-fail-closed-20260727` (af4bb50)
- PB-6: GitHub repo allowlist(reviewer/CI/label 路径)+ 生产入口 fail-closed
- 新增: `tests/test_pb6_github_repo_allowlist.py`, `tests/test_production_control_plane_entrypoints.py`

## PR 5 — `phase-b/pb7-escalation-planner-20260801` (b069daf)
- PB-7: MAX_ROUNDS 升级 → `USER_ACTION_REQUIRED(TASK_BLOCKED)` + Planner 派发硬化
- 新增: `tests/test_pb7_escalation_planner.py`(4 测试)

## PR 6 — `phase-b/pb8-test-gaps-20260801` (aaddc8f)
- PB-8: 补 7 项强制测试缺口(gap 3/5/6/7/8/9/11/12)
- 新增: `tests/test_pb_gaps_delivery.py`, `tests/test_pb_gaps_lifecycle.py`

## PR 7 — `monitor/phase-b-integration-20260801` (bd8c367)
- PB-1~8 全量集成分支(建议合并源头)
- 全量 231 passed;含 nightshift 交付报告

## PR 8 — `tooling/collaboration-doc-semantic-checker` (bcd8dc8)
- 语义检查器加固(F-01/F-02/F-03/B1/B2/CQ1 六项安全修复)
- 本地验证全绿;真实双轨 CI 待跑
