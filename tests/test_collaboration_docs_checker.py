# -*- coding: utf-8 -*-
"""Collaboration V1 文档语义一致性检查器的测试。

覆盖：
  * good fixture 全绿（27/27 通过）—— 验证检查器在文档合规时不误报；
  * 每条关键规则至少一个"通过"测试与一个"失败"测试（定向变异）；
  * CLI 退出码契约（成功=0，失败=1）与结构化 / JSON 摘要；
  * 在真实 PR #8 文档上运行检查器并记录结果（不强制通过，仅记录）。

fixture 全部为合成文本，不含任何真实凭据 / 令牌 / 仓库密钥。
"""

import os
import sys
import re
import json
import tempfile

import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
for p in (TOOLS_DIR, FIXTURE_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import check_collaboration_docs as cc  # noqa: E402
from collab_docs_builder import (  # noqa: E402
    build_good_docs,
    build_bad_docs,
    RULE_IDS,
)

REAL_DOCS_DIR = os.path.join(REPO_ROOT, "docs", "architecture")

SECRET_MARKERS = ("sk-[A-Za-z0-9]{20,}", "ghp_", "github_pat_", "-----BEGIN", "xoxb-", "AKIA")


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def findings_map(docs):
    return {f.rule_id: f for f in cc.run_checks(docs)}


def _write_docs(d, docs):
    for k, v in docs.items():
        with open(os.path.join(d, k), "w", encoding="utf-8") as fh:
            fh.write(v)


# ---------------------------------------------------------------------------
# 通过 / 失败：每条关键规则各一对
# ---------------------------------------------------------------------------

def test_good_docs_all_pass():
    fm = findings_map(build_good_docs())
    failed = [rid for rid, x in fm.items() if not x.ok]
    assert not failed, f"good docs unexpectedly failed: {failed}"


@pytest.mark.parametrize("rule_id", RULE_IDS)
def test_good_docs_rule_passes(rule_id):
    fm = findings_map(build_good_docs())
    assert rule_id in fm, f"rule {rule_id} missing from findings"
    assert fm[rule_id].ok, f"{rule_id} should PASS on good docs: {fm[rule_id].found}"


@pytest.mark.parametrize("rule_id", RULE_IDS)
def test_bad_docs_rule_fails(rule_id):
    fm = findings_map(build_bad_docs(rule_id))
    assert rule_id in fm, f"rule {rule_id} missing from findings"
    assert not fm[rule_id].ok, f"{rule_id} should FAIL on bad docs (mutation did not trigger)"


# ---------------------------------------------------------------------------
# CLI 契约
# ---------------------------------------------------------------------------

def test_cli_exit_code_success(capsys):
    with tempfile.TemporaryDirectory() as td:
        _write_docs(td, build_good_docs())
        rc = cc.main(["--docs-dir", td])
        assert rc == 0
        out = capsys.readouterr().out
        assert "PASS" in out


def test_cli_exit_code_failure(capsys):
    with tempfile.TemporaryDirectory() as td:
        _write_docs(td, build_bad_docs("C_ROLES_COUNT"))
        rc = cc.main(["--docs-dir", td])
        assert rc == 1
        out = capsys.readouterr().out
        assert "FAIL" in out


def test_cli_structured_json(capsys):
    with tempfile.TemporaryDirectory() as td:
        _write_docs(td, build_good_docs())
        rc = cc.main(["--docs-dir", td, "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["summary"]["ok"] is True
        assert data["summary"]["total_rules"] == len(data["findings"])


def test_cli_missing_docs_returns_error_code():
    with tempfile.TemporaryDirectory() as td:
        # 不写入任何文档
        rc = cc.main(["--docs-dir", td])
        assert rc == 2


# ---------------------------------------------------------------------------
# fixture 不含真实凭据
# ---------------------------------------------------------------------------

def test_fixtures_contain_no_credentials():
    docs = build_good_docs()
    blob = "\n".join(docs.values())
    for pat in SECRET_MARKERS:
        assert not re.search(pat, blob), f"fixture contains secret-like marker: {pat}"


# ---------------------------------------------------------------------------
# 在真实 PR #8 文档上运行并记录（不强制通过）
# ---------------------------------------------------------------------------

def test_real_docs_runs_and_records():
    if not os.path.isdir(REAL_DOCS_DIR):
        pytest.skip("real docs/architecture dir not present")
    docs = {}
    for dk in cc.ALL_DOCS:
        p = os.path.join(REAL_DOCS_DIR, dk)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                docs[dk] = fh.read()
    findings = cc.run_checks(docs)
    summary = cc.summarize(findings)
    # 记录结构化摘要，供完成报告引用
    print("\nREAL_DOCS_CHECK_SUMMARY:", json.dumps(summary, ensure_ascii=False))
    failures = [f.rule_id for f in findings if not f.ok]
    print("REAL_DOCS_FAILURES:", failures)
    # 仅断言检查器可执行并返回完整结论（不强制文档当前通过）
    assert summary["total_rules"] == len(findings)
    assert "ok" in summary


# ---------------------------------------------------------------------------
# 第二轮返工后冻结责任模型：定向语义测试（新增规则）
# ---------------------------------------------------------------------------

def test_s_draft_pr_resp_new_model_passes_on_good():
    # 新模型：Release Agent 是唯一 deliver() 调用方，Coding Worker 仅本地 Commit。
    fm = findings_map(build_good_docs())
    assert fm["S_DRAFT_PR_RESP"].ok, fm["S_DRAFT_PR_RESP"].found


def test_s_draft_pr_resp_fails_when_coding_worker_calls_deliver():
    fm = findings_map(build_bad_docs("S_DRAFT_PR_RESP"))
    assert not fm["S_DRAFT_PR_RESP"].ok


def test_s_max_rounds_passes_on_good():
    fm = findings_map(build_good_docs())
    assert fm["S_MAX_ROUNDS"].ok, fm["S_MAX_ROUNDS"].found


def test_s_max_rounds_fails_when_not_two():
    fm = findings_map(build_bad_docs("S_MAX_ROUNDS"))
    assert not fm["S_MAX_ROUNDS"].ok


def test_s_lifecycle_review_on_pr_passes_on_good():
    fm = findings_map(build_good_docs())
    assert fm["S_LIFECYCLE_REVIEW_ON_PR"].ok, fm["S_LIFECYCLE_REVIEW_ON_PR"].found


def test_s_lifecycle_review_on_pr_fails_without_same_pr():
    fm = findings_map(build_bad_docs("S_LIFECYCLE_REVIEW_ON_PR"))
    assert not fm["S_LIFECYCLE_REVIEW_ON_PR"].ok


def test_s_role_fullname_passes_on_good():
    fm = findings_map(build_good_docs())
    assert fm["S_ROLE_FULLNAME"].ok, fm["S_ROLE_FULLNAME"].found


def test_s_role_fullname_fails_on_abbrev():
    fm = findings_map(build_bad_docs("S_ROLE_FULLNAME"))
    assert not fm["S_ROLE_FULLNAME"].ok


def test_real_docs_pass_all_rules():
    # 冻结的 PR #8 文档（db1b3db）必须全部通过（退出码 0）。
    if not os.path.isdir(REAL_DOCS_DIR):
        pytest.skip("real docs/architecture dir not present")
    docs = {}
    for dk in cc.ALL_DOCS:
        p = os.path.join(REAL_DOCS_DIR, dk)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as fh:
                docs[dk] = fh.read()
    findings = cc.run_checks(docs)
    failed = [f.rule_id for f in findings if not f.ok]
    assert not failed, f"real PR #8 docs failed rules: {failed}"


# ---------------------------------------------------------------------------
# 定向语义测试：S_DRAFT_PR_RESP 责任模型（按 PR #8 第二轮返工冻结模型，7 项）
# 每项用最小角色文档精确构造"真能打破目标语义"的 bad/good fixture；
# bad fixture 必须确实违反目标语义，而非误触发。
# ---------------------------------------------------------------------------

def _s_draft_pr_resp(role_text: str):
    f = []
    cc.check_draft_pr_responsibility(f, {cc.DOC_ROLE: role_text})
    return {x.rule_id: x for x in f}["S_DRAFT_PR_RESP"]


def test_accepts_release_agent_as_unique_deliver_caller():
    # 正确模型：RA 唯一调用 deliver() 且交付在 Review 之前；CW 仅本地 Commit（否定句）。
    role_text = (
        "### 3.4 `Coding Worker`\n"
        "Coding Worker 唯一职责：代码实现与本地 Commit。"
        "MUST NOT push、MUST NOT 创建 Draft PR、MUST NOT 调用 DeliveryController.deliver()、"
        "MUST NOT 接触 GitHub 交付凭据。\n\n"
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色；"
        "编排受控交付（push + Draft PR），在 CI 与 Review 之前启动。"
        "Review 通过后仅发起 USER_ACTION_REQUIRED(reason=FINAL_ACCEPTANCE)，"
        "不得再次调用 deliver()、不得重复创建 Draft PR。\n"
    )
    assert _s_draft_pr_resp(role_text).ok


def test_rejects_coding_worker_as_deliver_caller():
    # CW 正向声明调用 deliver() → 违反（CW 不得触碰交付）。
    role_text = (
        "### 3.4 `Coding Worker`\n"
        "Coding Worker 负责调用 DeliveryController.deliver() 创建 Draft PR 并 push 到 GitHub。\n\n"
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_rejects_coding_worker_push_permission():
    # CW 正向声明 push → 违反仅本地 Commit 责任。
    role_text = (
        "### 3.4 `Coding Worker`\n"
        "Coding Worker 执行 push 到 GitHub 并创建 Draft PR。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_rejects_two_roles_calling_deliver():
    # RA 之外另有角色（QA Agent）正向调用 deliver() → 违反唯一调用方。
    role_text = (
        "### 3.4 `Coding Worker`\n"
        "Coding Worker MUST NOT 调用 DeliveryController.deliver()。\n\n"
        "### 3.6 `QA Agent`\n"
        "QA Agent 调用 DeliveryController.deliver() 执行交付。\n\n"
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_rejects_release_agent_starting_after_review():
    # RA 被描述为 Review 之后才执行受控交付 → 违反交付先于 Review 的时序。
    role_text = (
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色，"
        "但在 Independent Review 之后才执行受控 Push 并创建 Draft PR。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_rejects_release_agent_redeliver_after_review():
    # RA 在 Review 通过后再次调用 deliver() → 违反后置阶段仅 USER_ACTION。
    role_text = (
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。"
        "Review 通过后再次调用 DeliveryController.deliver() 重新交付。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_rejects_second_draft_pr_during_rework():
    # RA 在 rework 期间创建第二个 Draft PR → 违反幂等/同 PR 约束。
    role_text = (
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。"
        "rework 期间创建第二个 Draft PR 重新提交。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok
