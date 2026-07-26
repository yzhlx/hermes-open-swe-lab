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
