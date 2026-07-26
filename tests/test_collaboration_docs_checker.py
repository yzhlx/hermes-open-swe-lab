# -*- coding: utf-8 -*-
"""Collaboration V1 文档语义一致性检查器的测试。

覆盖：
  * good fixture 全绿（38/38 通过）—— 验证检查器在文档合规时不误报；
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
    mutate_allowed_repos_line,
)

REAL_DOCS_DIR = os.path.join(REPO_ROOT, "docs", "architecture")

SECRET_MARKERS = ("sk-[A-Za-z0-9]{20,}", "sk_live_", "pk_live_", "ghp_", "github_pat_",
                   "-----BEGIN", "xoxb-", "xoxp-", "xoxo-", "AKIA", "AIza", "ya29", "npm_", "eyJ")


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
    # §9 修复：真实文档缺失时须失败而非静默跳过（CI 信任自举）。
    assert os.path.isdir(REAL_DOCS_DIR), "docs/architecture missing: checker cannot validate real docs"
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
    # §9 修复：真实文档缺失时须失败而非静默跳过。
    assert os.path.isdir(REAL_DOCS_DIR), "docs/architecture missing: checker cannot validate real docs"
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


# ---------------------------------------------------------------------------
# CQ1 覆盖率元测试：38 条规则全部具备正向（good 通过）+ 反向（bad 失败）断言
# ---------------------------------------------------------------------------

def test_checker_registers_all_38_rules_and_all_pass_on_good():
    fm = findings_map(build_good_docs())
    # 检查器实际产出 38 条规则结论
    assert len(fm) == 38, f"expected 38 rules, got {len(fm)}"
    failed = [rid for rid, x in fm.items() if not x.ok]
    assert not failed, f"good docs unexpectedly failed: {failed}"
    # RULE_IDS 必须覆盖全部 38 条（缺一条即缺 fail/mutation 测试）
    assert len(RULE_IDS) == 38, f"RULE_IDS should list 38 rules, got {len(RULE_IDS)}"
    missing = [rid for rid in fm if rid not in RULE_IDS]
    assert not missing, f"rules missing from RULE_IDS: {missing}"


# ---------------------------------------------------------------------------
# F-02 PoC：ALLOWED_GITHUB_REPOS 跨行/多格式绕过必须被捕获
# ---------------------------------------------------------------------------

def _s_protected_repo_fails(docs):
    fm = findings_map(docs)
    return not fm["S_PROTECTED_REPO"].ok


def test_poc_protected_repo_multiline_set_leak():
    docs = mutate_allowed_repos_line(
        build_good_docs(),
        "ALLOWED_GITHUB_REPOS = {\n  yzhlx/hermes-open-swe-smoke-test,\n  yzhlx/hermes-learning-os\n}",
    )
    assert _s_protected_repo_fails(docs)


def test_poc_protected_repo_yaml_list_leak():
    docs = mutate_allowed_repos_line(
        build_good_docs(),
        "ALLOWED_GITHUB_REPOS:\n  - yzhlx/hermes-learning-os\n  - yzhlx/hermes-open-swe-smoke-test",
    )
    assert _s_protected_repo_fails(docs)


def test_poc_protected_repo_bullet_leak():
    docs = mutate_allowed_repos_line(
        build_good_docs(),
        "ALLOWED_GITHUB_REPOS: yzhlx/hermes-learning-os",
    )
    assert _s_protected_repo_fails(docs)


def test_poc_protected_repo_html_comment_split_leak():
    docs = mutate_allowed_repos_line(
        build_good_docs(),
        "ALLOWED_GITHUB_REPOS = {<!-- -->yzhlx/hermes-learning-os}",
    )
    assert _s_protected_repo_fails(docs)


def test_poc_protected_repo_fullwidth_normalized_leak():
    docs = mutate_allowed_repos_line(
        build_good_docs(),
        "ＡＬＬＯＷＥＤ＿ＧＩＴＨＵＢ＿ＲＥＰＯＳ = {ｙｚｈｌｘ／ｈｅｒｍｅｓ－ｌｅａｒｎｉｎｇ－ｏｓ}",
    )
    assert _s_protected_repo_fails(docs)


# ---------------------------------------------------------------------------
# F-03 PoC：交付职责检测必须抵抗中文近义 / 全角 / 零宽绕过
# ---------------------------------------------------------------------------

def test_poc_cw_chinese_push_synonym():
    role_text = (
        "### 3.4 `Coding Worker`\n"
        "Coding Worker 负责推送分支到 GitHub 并创建 Draft PR。\n\n"
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_poc_cw_fullwidth_push():
    role_text = (
        "### 3.4 `Coding Worker`\n"
        "Ｃｏｄｉｎｇ Ｗｏｒｋｅｒ 执行 ｐｕｓｈ 到 GitHub。\n\n"
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_poc_cw_zerowidth_push():
    role_text = (
        "### 3.4 `Coding Worker`\n"
        "Coding Worker 执行 pus\u200bh 到 GitHub。\n\n"
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_poc_cw_exact_push_code_and_create_draft_pr():
    # §十二 #2：Coding Worker 负责推送代码到 GitHub 远端并创建 Draft PR
    role_text = (
        "### 3.4 `Coding Worker`\n"
        "Coding Worker 负责推送代码到 GitHub 远端并创建 Draft PR。\n\n"
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_poc_cw_html_comment_split_keyword():
    # §十二 #5：HTML 注释拆分关键词 pu<!-- -->sh => push，必须被捕获
    role_text = (
        "### 3.4 `Coding Worker`\n"
        "Coding Worker 执行 pu<!-- -->sh 到 GitHub 并创建 Draft PR。\n\n"
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_poc_ra_second_draft_pr_exact():
    # §十二 #6：rework 期间创建第 2 个 Draft PR（精确"第 2 个"）
    role_text = (
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。"
        "rework 期间创建第 2 个 Draft PR 重新提交。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_poc_ra_redeliver_after_review():
    role_text = (
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。"
        "Review 通过后再次调用 DeliveryController.deliver() 重新交付。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_poc_ra_second_draft_pr_synonym():
    role_text = (
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。"
        "rework 期间创建另一个 Draft PR 重新提交。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


# ---------------------------------------------------------------------------
# §9 PoC：MAX_ROUNDS=20 不得被误判为 =2
# ---------------------------------------------------------------------------

def test_poc_max_rounds_20_must_fail():
    d = dict(build_good_docs())
    for key in (cc.DOC_ACTIVITY, cc.DOC_ROADMAP):
        if key in d:
            d[key] = d[key].replace("MAX_ROUNDS=2", "MAX_ROUNDS=20")
    fm = findings_map(d)
    assert not fm["S_MAX_ROUNDS"].ok


# ---------------------------------------------------------------------------
# B1 PoC：所有输出路径（human / JSON / redact）必须脱敏
# ---------------------------------------------------------------------------

def test_poc_redact_masks_token_types():
    samples = [
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        "sk_live_abcdefghijklmnopqrstuvwx",
        "pk_live_abcdefghijklmnopqrstuvwx",
        "AKIAIOSFODNN7EXAMPLE",
        "AIzaSyA1234567890abcdefghijklmnopqrstuvw",
        "xoxb-1234567890-1234567890-abcdefghijkl",
        "ya29.abcdefghijklmnopqrstuvwxyz0123456789",
        "npm_abcdefghijklmnopqrstuvwxyz0123456789abcdef",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
    ]
    for s in samples:
        redacted = cc.redact(s)
        # 原始令牌值不得原样出现在脱敏结果中
        assert s not in redacted, f"secret leaked through redact(): {s!r}"
        assert "[REDACTED]" in redacted


def test_poc_redaction_wired_in_human_output(capsys):
    f = [cc.Finding("X_RULE", "DOC", False,
                    message="see ghp_ABCDEFGHIJKLMNOPQRSTUVWX in doc",
                    found="raw sk_live_abcdefghijklmnopqrstuvwx here")]
    cc._print_human({"total_rules": 1, "passed": 0, "failed": 1, "ok": False,
                     "by_rule": {}, "failures": []}, f, ["DOC"])
    out = capsys.readouterr().out
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWX" not in out
    assert "sk_live_abcdefghijklmnopqrstuvwx" not in out
    assert "[REDACTED]" in out


def test_poc_redaction_wired_in_json():
    import json as _json
    f = cc.Finding("X_RULE", "DOC", False,
                   message="token ghp_ABCDEFGHIJKLMNOPQRSTUVWX present",
                   found="token sk_live_abcdefghijklmnopqrstuvwx present")
    data = _json.loads(_json.dumps(f.as_dict()))
    blob = _json.dumps(data)
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWX" not in blob
    assert "sk_live_abcdefghijklmnopqrstuvwx" not in blob
    assert "[REDACTED]" in blob


# ---------------------------------------------------------------------------
# F-02 补充负向测试：单行 / Python tuple / Python list / 零宽字符变体
# （spec §六 要求至少覆盖这 8 种形式）
# ---------------------------------------------------------------------------

def test_poc_protected_repo_singleline_leak():
    docs = mutate_allowed_repos_line(
        build_good_docs(),
        "ALLOWED_GITHUB_REPOS = {yzhlx/hermes-learning-os}",
    )
    assert _s_protected_repo_fails(docs)


def test_poc_protected_repo_python_tuple_leak():
    docs = mutate_allowed_repos_line(
        build_good_docs(),
        "ALLOWED_GITHUB_REPOS = (\n  yzhlx/hermes-learning-os,\n)",
    )
    assert _s_protected_repo_fails(docs)


def test_poc_protected_repo_python_list_leak():
    docs = mutate_allowed_repos_line(
        build_good_docs(),
        "ALLOWED_GITHUB_REPOS = [\n  yzhlx/hermes-learning-os,\n]",
    )
    assert _s_protected_repo_fails(docs)


def test_poc_protected_repo_zerowidth_leak():
    # 零宽字符插入但保留真实连字符；归一化剥离零宽后应重建出受保护仓库名。
    docs = mutate_allowed_repos_line(
        build_good_docs(),
        "ALLOWED_GITHUB_REPOS = {yzhlx/hermes-\u200blearning-os}",
    )
    assert _s_protected_repo_fails(docs)


# ---------------------------------------------------------------------------
# §八 / §十二：否定语义双重否定与例外结构必须判为"正向授权"（违规）
# 旧的"前 50 字符有'不'即否定"会错误放过以下情形，新子句级判定必须捕获。
# ---------------------------------------------------------------------------

def test_poc_cw_except_clause_push():
    # §十二 #3："不得做任何事，除了 push" = 授权 push => 违规
    role_text = (
        "### 3.4 `Coding Worker`\n"
        "Coding Worker 不得做任何事情，除了 push 并创建 Draft PR。\n\n"
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_poc_cw_double_neg_create_draft_pr():
    # §十二 #4 / §八："并非不能创建 Draft PR" 双重否定 = 授权 => 违规
    role_text = (
        "### 3.4 `Coding Worker`\n"
        "Coding Worker 并非不能创建 Draft PR。\n\n"
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_poc_cw_unrestricted_may_push():
    # §八："不受限制，可以推送" 反向强调 = 授权 => 违规
    role_text = (
        "### 3.4 `Coding Worker`\n"
        "Coding Worker 不受限制，可以推送代码到 GitHub。\n\n"
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_poc_cw_have_to_deliver():
    # §八："不得不调用 deliver()" 双重否定 = 授权 => 违规
    role_text = (
        "### 3.4 `Coding Worker`\n"
        "Coding Worker 不得不调用 DeliveryController.deliver() 推送代码。\n\n"
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色。\n"
    )
    assert not _s_draft_pr_resp(role_text).ok


def test_poc_ra_uncontrolled_delivery():
    # §十二 #11："不受控交付" 不得被当作"受控" => 两阶段职责失败
    role_text = (
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色；"
        "编排不受控交付（push + Draft PR）。\n"
    )
    fm = findings_map({cc.DOC_ROLE: role_text})
    assert not fm["S_RELEASE_AGENT_TWO_STAGE_DUTY"].ok


def test_poc_ra_non_first_stage():
    # §十二 #12："非首次交付" 不得被当作"首次" => 两阶段职责失败
    role_text = (
        "### 3.8 `Release Agent`\n"
        "Release Agent 是唯一允许调用 DeliveryController.deliver() 的角色；"
        "非首次执行受控 Push + 创建 Draft PR。\n"
    )
    fm = findings_map({cc.DOC_ROLE: role_text})
    assert not fm["S_RELEASE_AGENT_TWO_STAGE_DUTY"].ok


def test_poc_final_acceptance_have_to_trigger():
    # §十二 #13："不得不触发/归档" 双重否定 = 必须触发 => 阻塞声明失效
    d = build_good_docs()
    d[cc.DOC_ROADMAP] = d[cc.DOC_ROADMAP].replace(
        "TASK_COMPLETED 不得触发/归档", "TASK_COMPLETED 不得不触发/归档")
    fm = findings_map(d)
    assert not fm["S_FINAL_ACCEPTANCE_BLOCKING"].ok


# ---------------------------------------------------------------------------
# 性能 PoC：~5MB 文档下检查器应在合理时间内完成（防归一化导致 O(n^2) 退化）
# ---------------------------------------------------------------------------

def test_poc_large_doc_perf():
    import time
    docs = build_good_docs()
    # 单文档约 5MB 的无语义 benign 内容（不触发任何规则）
    big = ("无语义内容行。本行不含任何标识符或生命周期关键词。\n" * 60000)  # ~5MB
    docs[cc.DOC_ROADMAP] = big
    start = time.perf_counter()
    findings = cc.run_checks(docs)
    elapsed = time.perf_counter() - start
    assert len(findings) == 38
    assert elapsed < 30.0, f"checker too slow on 5MB doc: {elapsed:.2f}s"
