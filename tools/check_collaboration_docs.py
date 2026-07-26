#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Collaboration V1 架构文档自动语义一致性检查器。

本工具用于防止架构文档只做"数量一致"检查，却漏掉生命周期与责任冲突。
它不依赖固定行号，而是基于 Markdown heading、表格与规范标识符进行解析。

校验两类规则：
  1) 标识符检查：8 角色 / 14 字段 / 21 事件 / 8 UAR 原因 / 7+1 频道 /
     12 Canvas 字段 / 8 ACP 方法 / 9 MCP 工具 / Phase A-G，以及跨文档一致性。
  2) 语义检查：Phase A 定位、Draft PR 唯一责任、生命周期顺序、Review 不先于
     Draft PR、TASK_COMPLETED 不含"待最终验收"、FINAL_ACCEPTANCE 未解决仍
     blocked、临时频道终验前不归档、Buzz 真实试用前门禁、PR#7/#8 堆叠顺序、
     机器可消费表格禁歧义简称、Buzz/Canvas/Activity Feed 非第二事实源、
     hermes-learning-os 永久 protected。

退出码：发现任意失败规则返回 1，全部通过返回 0。

用法：
    python tools/check_collaboration_docs.py [--docs-dir DIR] [--json] [--quiet]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 规范基准（Single Source of Truth for the checker）
# 这些值来自 5 份架构文档中明确定义的权威标识符；任何偏离即视为不一致。
# ---------------------------------------------------------------------------

# 文档键（与仓库 docs/architecture/ 下文件名一致）
DOC_HUMAN = "HUMAN-AGENT-WORKSPACE-ARCHITECTURE.md"
DOC_ROLE = "ROLE-AND-PERMISSION-MODEL.md"
DOC_ACTIVITY = "ACTIVITY-AND-INTERVENTION-EVENT-MODEL.md"
DOC_BUZZ = "BUZZ-INTEGRATION-BOUNDARY.md"
DOC_ROADMAP = "COLLABORATION-V1-IMPLEMENTATION-ROADMAP.md"

ALL_DOCS = [DOC_HUMAN, DOC_ROLE, DOC_ACTIVITY, DOC_BUZZ, DOC_ROADMAP]

# 8 个权威角色完整名称
CANONICAL_ROLES: List[str] = [
    "Human Owner",
    "Hermes Master / Boss",
    "Planner / Scheduler",
    "Coding Worker",
    "Independent Reviewer",
    "QA Agent",
    "Documentation Agent",
    "Release Agent",
]

# 14 个角色字段（中文名，与 ROLE §2 一致）
CANONICAL_ROLE_FIELDS: List[str] = [
    "唯一职责",
    "允许读取的数据",
    "允许调用的工具",
    "允许写入的位置",
    "禁止操作",
    "输入契约",
    "输出契约",
    "任务开始条件",
    "完成条件",
    "失败和升级路径",
    "是否允许修改代码",
    "是否允许操作 GitHub",
    "是否允许接触凭据",
    "是否允许请求用户授权",
]

# 21 个权威 Activity Event
CANONICAL_EVENTS: List[str] = [
    "TASK_STARTED",
    "PLAN_UPDATED",
    "FILE_READ",
    "FILE_MODIFIED",
    "COMMAND_STARTED",
    "COMMAND_FINISHED",
    "TEST_STARTED",
    "TEST_FINISHED",
    "COMMIT_CREATED",
    "PUSH_COMPLETED",
    "DRAFT_PR_CREATED",
    "CI_PENDING",
    "CI_PASSED",
    "CI_FAILED",
    "REVIEW_STARTED",
    "REVIEW_FINDING",
    "REWORK_STARTED",
    "REVIEW_PASSED",
    "USER_ACTION_REQUIRED",
    "TASK_BLOCKED",
    "TASK_COMPLETED",
]

# 目录外（禁止作为权威事件名）的示例事件
FORBIDDEN_EVENTS: List[str] = ["TEST_PASSED", "TEST_FAILED"]

# 8 个 USER_ACTION_REQUIRED 原因
CANONICAL_UAR_REASONS: List[str] = [
    "AUTHORIZATION",
    "SECRET_ENTRY",
    "PAYMENT",
    "SECURITY_INCIDENT",
    "ARCHITECTURE_DECISION",
    "EXTERNAL_ACCOUNT_SETUP",
    "FINAL_ACCEPTANCE",
    "PRODUCTION_RELEASE",
]

# 7 个默认频道
CANONICAL_DEFAULT_CHANNELS: List[str] = [
    "#control-room",
    "#planning",
    "#implementation",
    "#review",
    "#qa",
    "#release",
    "#user-action-required",
]
# 1 个临时频道模板
CANONICAL_TEMP_CHANNEL = "#task-<task-id>"

# 12 个 Canvas 字段
CANONICAL_CANVAS_FIELDS: List[str] = [
    "Goal",
    "Scope",
    "Out of Scope",
    "Acceptance Criteria",
    "Assigned Roles",
    "Current Status",
    "Evidence",
    "Review Findings",
    "Risks",
    "User Actions Required",
    "Decisions",
    "Next Step",
]

# 8 个 ACP 方法
CANONICAL_ACP_METHODS: List[str] = [
    "start_session",
    "submit_task",
    "resume_session",
    "cancel_task",
    "get_activity",
    "request_permission",
    "collect_result",
    "terminate_session",
]

# 9 个 MCP Workspace Tools
CANONICAL_MCP_TOOLS: List[str] = [
    "workspace_get_task",
    "workspace_get_context",
    "workspace_post_update",
    "workspace_update_canvas",
    "workspace_search",
    "workspace_get_pending_actions",
    "workspace_request_user_action",
    "workspace_post_review_result",
    "workspace_get_evidence",
]

# Phase A-G 名称（用于一致性核对）
CANONICAL_PHASES: Dict[str, str] = {
    "A": "工作空间、角色、频道、Canvas、Activity Feed 设计",
    "B": "Hermes 控制层 Scheduler / Reviewer / Rework 闭环",
    "C": "Buzz 工作空间适配器",
    "D": "ACP Runtime Adapter",
    "E": "MCP Workspace Tools",
    "F": "Smoke Test 真实闭环",
    "G": "用户体验试用和迭代",
}

# 机器可消费表格中禁用的歧义角色简称
FORBIDDEN_ROLE_ABBREV: List[str] = ["Master", "Scheduler", "Reviewer"]

# 受保护仓库 & 允许仓库
PROTECTED_REPO = "yzhlx/hermes-learning-os"
ALLOWED_REPO = "yzhlx/hermes-open-swe-smoke-test"

# 生命周期锚点（用于顺序校验）
# 顺序必须与冻结的 PR #8 文档（ACTIVITY §1.2.1 / ROADMAP Phase F）一致：
#   Issue → Plan → Code/Test → Commit → [Release Agent] controlled delivery
#   (push + Draft PR via DeliveryController.deliver()) → CI → Independent Review
#   → rework on same PR (MAX_ROUNDS=2) → FINAL_ACCEPTANCE → TASK_COMPLETED
# 冻结文档未把 "Planner/Scheduler handoff" 与 "Release Agent 受控交付" 拆成独立
# 箭头，二者合并为 "controlled delivery" 一步，故锚点仅取文档实际保证的段。
LIFECYCLE_ANCHORS_ORDER = [
    "issue",
    "plan",
    "code",
    "commit",
    "draft_pr",
    "ci",
    "review",
    "rework",
    "final_acceptance",
    "task_completed",
]

# Phase A 是"架构文档交付"的正向标记；不得被定义为未来运行时实现
PHASE_A_DOC_DELIVERY_MARKERS = ["交付物", "docs/architecture", "静态设计", "架构文档"]
PHASE_A_RUNTIME_IMPL_MARKERS = ["运行时代码", "代码实现", "runtime implementation", "运行时实现"]


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    rule_id: str
    doc: str
    ok: bool
    severity: str = "error"
    message: str = ""
    found: str = ""

    def as_dict(self) -> Dict[str, object]:
        # 文档作不可信数据：输出前对可能外泄的凭据/令牌做脱敏，避免 checker
        # 把真实密钥回显到 stdout / JSON（B5/B11 脱敏纪律）。
        return {
            "rule_id": self.rule_id,
            "doc": self.doc,
            "ok": self.ok,
            "severity": self.severity,
            "message": redact(self.message),
            "found": redact(self.found),
        }


# ---------------------------------------------------------------------------
# Markdown 解析辅助（基于 heading / 表格 / 标识符，不依赖行号）
# ---------------------------------------------------------------------------

def iter_sections(text: str) -> List[Tuple[int, str, str]]:
    """返回 [(level, title, body), ...]，body 截止于同层或更高层级的下一个 heading。"""
    heading_re = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.M)
    matches = list(heading_re.finditer(text))
    out: List[Tuple[int, str, str]] = []
    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        start = m.end()
        end = len(text)
        for nxt in matches[i + 1:]:
            if len(nxt.group(1)) <= level:
                end = nxt.start()
                break
        out.append((level, title, text[start:end].strip()))
    return out


def find_section(text: str, *keywords: str) -> Optional[str]:
    """返回首个标题包含所有 keywords 的 section body。"""
    kws = [k.lower() for k in keywords]
    for _level, title, body in iter_sections(text):
        t = title.lower()
        if all(k in t for k in kws):
            return body
    return None


def iter_role_subsections(text: str) -> List[Tuple[str, str]]:
    """返回 [(role_name, body), ...]，匹配 '### 3.x `RoleName`'。"""
    out: List[Tuple[str, str]] = []
    for _level, title, body in iter_sections(text):
        m = re.match(r"^3\.\d+\s+`([^`]+)`", title.strip())
        if m:
            out.append((m.group(1), body))
    return out


def parse_tables(text: str) -> List[List[List[str]]]:
    """解析所有 Markdown 表格，返回表格列表，每个表格为行列表（含表头与分隔行）。"""
    tables: List[List[List[str]]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|") and i + 1 < len(lines) and _is_sep_row(lines[i + 1]):
            rows: List[List[str]] = []
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                rows.append(cells)
                j += 1
            if len(rows) >= 2:
                tables.append(rows)
            i = j
        else:
            i += 1
    return tables


def _is_sep_row(line: str) -> bool:
    s = line.strip().strip("|")
    if not s:
        return False
    return all(re.match(r"^:?-+:?$", c.strip()) for c in s.split("|"))


def table_data(table: List[List[str]]) -> Tuple[List[str], List[List[str]]]:
    """返回 (header, data_rows)，跳过分隔行。"""
    rows = [r for r in table if not _is_sep_row("|" + "|".join(r) + "|")]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def first_table_in(text: str) -> Optional[List[List[str]]]:
    tables = parse_tables(text)
    return tables[0] if tables else None


def all_tables(text: str) -> List[List[List[str]]]:
    return parse_tables(text)


def backticked(text: str) -> List[str]:
    return re.findall(r"`([^`]+)`", text)


def norm(s: str) -> str:
    return re.sub(r"[\s`()（）:：#\-/]+", "", s).lower()


# ---------------------------------------------------------------------------
# 安全加固：输出脱敏 + 路径安全
# ---------------------------------------------------------------------------

# 文档/报告作为不可信数据，checker 的输出（message / found）可能回显文档片段。
# 以下模式用于把真实凭据/令牌掩码，避免 checker 自身成为凭据泄露面。
_SECRET_PATTERNS: List["re.Pattern[str]"] = [
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"ghp_[A-Za-z0-9]{16,}"),
    re.compile(r"gho_[A-Za-z0-9]{16,}"),
    re.compile(r"ghu_[A-Za-z0-9]{16,}"),
    re.compile(r"ghs_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"xoxb-[A-Za-z0-9-]{12,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{16,}"),
]


def redact(s: str) -> str:
    """对可能含凭据的文本做掩码；结构稳定（仅替换值，不改键与长度语义）。"""
    if not isinstance(s, str):
        return s
    for p in _SECRET_PATTERNS:
        s = p.sub(lambda m: m.group(0)[:6] + "…[REDACTED]", s)
    return s


def _safe_doc_path(docs_dir: str, filename: str) -> str:
    """将 docs_dir 下的 filename 解析为真实路径，并防止路径穿越 / 符号链接越界。

    返回 realpath；若解析后路径落在 docs_dir 之外（如 filename='../secret'、
    或经符号链接逃逸），抛出 ValueError，由调用方转为退出码 2。checker 只读
    docs_dir 内的固定文件名，不读取目录外任何文件（AGENTS.md §8 不可信数据）。
    """
    base = os.path.realpath(docs_dir)
    candidate = os.path.realpath(os.path.join(base, filename))
    if candidate != base and not candidate.startswith(base + os.sep):
        raise ValueError(f"path traversal blocked: {filename!r} resolves outside {docs_dir!r}")
    return candidate


# ---------------------------------------------------------------------------
# 各规则检查实现
# ---------------------------------------------------------------------------

def _check_list(
    f: List[Finding],
    rule_id: str,
    doc: str,
    label: str,
    expected: List[str],
    actual: List[str],
) -> None:
    """通用计数 + 集合一致性检查。"""
    exp_set = set(expected)
    act_set = set(actual)
    missing = sorted(exp_set - act_set)
    extra = sorted(act_set - exp_set)
    ok = (len(actual) == len(expected)) and not missing and not extra
    found = f"found {len(actual)} {label}; missing={missing}; extra={extra}; values={actual}"
    if ok:
        msg = f"{label} 数量与集合均与规范一致（{len(expected)} 项）"
    else:
        msg = f"{label} 不一致：期望 {len(expected)} 项，实际 {len(actual)} 项"
    f.append(Finding(rule_id, doc, ok, message=msg, found=found))


# ---- 标识符检查 ----------------------------------------------------------

def check_roles(f: List[Finding], doc: Dict[str, str]) -> None:
    role_text = doc.get(DOC_ROLE, "")
    sec = find_section(role_text, "角色总览", "Role Overview") or role_text
    names = [t for t in backticked(sec) if t in CANONICAL_ROLES]
    _check_list(f, "C_ROLES_COUNT", DOC_ROLE, "角色", CANONICAL_ROLES, names)

    # 跨文档一致性：HUMAN §6 也必须枚举同样的 8 个角色
    human_sec = find_section(doc.get(DOC_HUMAN, ""), "标识符一致性清单", "Cross-Doc")
    human_names = [t for t in backticked(human_sec or "") if t in CANONICAL_ROLES]
    ok = set(human_names) == set(CANONICAL_ROLES)
    f.append(
        Finding(
            "C_ROLES_CONSISTENCY",
            f"{DOC_ROLE} + {DOC_HUMAN}",
            ok,
            message="8 角色在 ROLE 总览与 HUMAN 跨文档清单中一致"
            if ok else "8 角色在 ROLE 总览与 HUMAN 跨文档清单中不一致",
            found=f"human_cross_doc_roles={human_names}",
        )
    )


def check_role_fields(f: List[Finding], doc: Dict[str, str]) -> None:
    role_text = doc.get(DOC_ROLE, "")
    sec = find_section(role_text, "角色定义字段规范", "Field Spec")
    if not sec:
        f.append(Finding("C_ROLE_FIELDS_COUNT", DOC_ROLE, False,
                         message="未找到角色字段规范章节", found="section missing"))
        return
    tbl = first_table_in(sec)
    if not tbl:
        f.append(Finding("C_ROLE_FIELDS_COUNT", DOC_ROLE, False,
                         message="角色字段规范章节无表格", found="table missing"))
        return
    _h, rows = table_data(tbl)
    field_names = []
    for r in rows:
        if len(r) >= 2:
            # 去掉英文括号部分，仅取中文名
            cn = re.sub(r"\(.*?\)", "", r[1]).strip()
            field_names.append(cn)
    _check_list(f, "C_ROLE_FIELDS_COUNT", DOC_ROLE, "角色字段", CANONICAL_ROLE_FIELDS, field_names)

    # 每个角色详细定义表都必须包含 14 个字段
    bad: List[str] = []
    for role_name, body in iter_role_subsections(role_text):
        if role_name not in CANONICAL_ROLES:
            continue
        t = first_table_in(body)
        if not t:
            bad.append(f"{role_name}:no-table")
            continue
        _hh, rrows = table_data(t)
        cf = [re.sub(r"\(.*?\)", "", r[0]).strip() for r in rrows if r]
        missing = [x for x in CANONICAL_ROLE_FIELDS if x not in cf]
        if missing:
            bad.append(f"{role_name}:missing={missing}")
    ok = not bad
    f.append(
        Finding(
            "C_ROLE_FIELDS_PER_ROLE",
            DOC_ROLE,
            ok,
            message="每个角色详细定义均含 14 个字段" if ok else "部分角色定义缺少字段",
            found="; ".join(bad) if bad else "all 8 roles have 14 fields",
        )
    )


def check_events(f: List[Finding], doc: Dict[str, str]) -> None:
    act_text = doc.get(DOC_ACTIVITY, "")
    sec = find_section(act_text, "事件清单", "Event Catalog")
    if not sec:
        f.append(Finding("C_EVENTS_COUNT", DOC_ACTIVITY, False,
                         message="未找到事件清单章节", found="section missing"))
        return
    tbl = first_table_in(sec)
    if not tbl:
        f.append(Finding("C_EVENTS_COUNT", DOC_ACTIVITY, False,
                         message="事件清单章节无表格", found="table missing"))
        return
    _h, rows = table_data(tbl)
    events = [t for r in rows for t in backticked(r[1]) if t in CANONICAL_EVENTS] if rows and len(rows[0]) > 1 else []
    # 兜底：直接扫描整表反引号
    if not events:
        events = [t for r in rows for t in backticked(" | ".join(r)) if t in CANONICAL_EVENTS]
    _check_list(f, "C_EVENTS_COUNT", DOC_ACTIVITY, "Activity 事件", CANONICAL_EVENTS, events)

    # 目录外事件（如 TEST_PASSED/TEST_FAILED）不得作为事件目录的条目出现。
    # 仅扫描事件名称列（col1），避免把描述文本中的合法提及误判为事件名。
    forbidden_found = []
    for r in rows:
        if len(r) < 2:
            continue
        for tok in backticked(r[1]):
            if tok in FORBIDDEN_EVENTS:
                forbidden_found.append(tok)
    ok = not forbidden_found
    f.append(
        Finding(
            "C_EVENTS_FORBIDDEN",
            DOC_ACTIVITY,
            ok,
            message="事件目录中未出现目录外事件" if ok else "事件目录中出现目录外/未授权事件名",
            found=f"forbidden_or_unknown={forbidden_found}",
        )
    )


def check_uar(f: List[Finding], doc: Dict[str, str]) -> None:
    act_text = doc.get(DOC_ACTIVITY, "")
    sec = find_section(act_text, "原因枚举")
    if not sec:
        f.append(Finding("C_UAR_COUNT", DOC_ACTIVITY, False,
                         message="未找到 USER_ACTION_REQUIRED 原因枚举章节", found="section missing"))
        return
    tbl = first_table_in(sec)
    if not tbl:
        f.append(Finding("C_UAR_COUNT", DOC_ACTIVITY, False,
                         message="原因枚举章节无表格", found="table missing"))
        return
    _h, rows = table_data(tbl)
    reasons = [t for r in rows for t in backticked(r[0]) if t in CANONICAL_UAR_REASONS]
    _check_list(f, "C_UAR_COUNT", DOC_ACTIVITY, "USER_ACTION_REQUIRED 原因",
                CANONICAL_UAR_REASONS, reasons)


def check_channels(f: List[Finding], doc: Dict[str, str]) -> None:
    rm_text = doc.get(DOC_ROADMAP, "")
    sec = find_section(rm_text, "频道拓扑", "Channel Topology")
    if not sec:
        f.append(Finding("C_CHANNELS", DOC_ROADMAP, False,
                         message="未找到频道拓扑章节", found="section missing"))
        return
    chs = [t for t in backticked(sec) if t.startswith("#")]
    defaults = [c for c in chs if c != CANONICAL_TEMP_CHANNEL]
    temp = [c for c in chs if c == CANONICAL_TEMP_CHANNEL]
    ok_defaults = set(defaults) == set(CANONICAL_DEFAULT_CHANNELS)
    ok_temp = len(temp) == 1
    ok = ok_defaults and ok_temp
    f.append(
        Finding(
            "C_CHANNELS",
            DOC_ROADMAP,
            ok,
            message="7 默认频道 + 1 临时频道模板一致" if ok else "频道数量/集合不一致",
            found=f"defaults={defaults}; temp={temp}",
        )
    )

    # 跨文档一致性：HUMAN §1.1 / §6 频道必须一致
    human_sec = find_section(doc.get(DOC_HUMAN, ""), "标识符一致性清单", "Cross-Doc") or \
        find_section(doc.get(DOC_HUMAN, ""), "Buzz Workspace")
    human_chs = []
    for t in backticked(human_sec or ""):
        for part in t.split():
            if part.startswith("#"):
                human_chs.append(part)
    human_defaults = [c for c in human_chs if c != CANONICAL_TEMP_CHANNEL]
    ok_h = set(human_defaults) == set(CANONICAL_DEFAULT_CHANNELS)
    f.append(
        Finding(
            "C_CHANNELS_CONSISTENCY",
            f"{DOC_ROADMAP} + {DOC_HUMAN}",
            ok_h,
            message="频道在 ROADMAP 与 HUMAN 中一致" if ok_h else "频道在 ROADMAP 与 HUMAN 中不一致",
            found=f"human_defaults={human_defaults}",
        )
    )


def check_canvas(f: List[Finding], doc: Dict[str, str]) -> None:
    rm_text = doc.get(DOC_ROADMAP, "")
    sec = find_section(rm_text, "Canvas 模板", "Canvas Template")
    if not sec:
        f.append(Finding("C_CANVAS", DOC_ROADMAP, False,
                         message="未找到 Canvas 模板章节", found="section missing"))
        return
    # 仅取枚举行（含反引号最多的一行）中的字段，避免约束段落重复提及抬高计数
    fields = []
    for ln in sec.splitlines():
        toks = backticked(ln)
        if len(toks) >= 5:
            fields = toks
            break
    fields = [t for t in fields if t in CANONICAL_CANVAS_FIELDS]
    _check_list(f, "C_CANVAS", DOC_ROADMAP, "Canvas 字段", CANONICAL_CANVAS_FIELDS, fields)


def check_acp(f: List[Finding], doc: Dict[str, str]) -> None:
    rm_text = doc.get(DOC_ROADMAP, "")
    sec = find_section(rm_text, "ACP Agent Runtime Adapter 接口", "ACP Agent Runtime Adapter")
    def _norm_method(t: str) -> str:
        return t.replace("()", "").strip()

    methods: List[str] = []
    if sec:
        tbl = first_table_in(sec)
        if tbl:
            _h, rows = table_data(tbl)
            methods = [_norm_method(t) for r in rows for t in backticked(r[0])]
            methods = [m for m in methods if m in CANONICAL_ACP_METHODS]
    if not methods:
        # 兜底：HUMAN §6 内联列表
        human_sec = find_section(doc.get(DOC_HUMAN, ""), "ACP Agent Runtime Adapter") or doc.get(DOC_HUMAN, "")
        methods = [_norm_method(t) for t in backticked(human_sec)]
        methods = [m for m in methods if m in CANONICAL_ACP_METHODS]
    _check_list(f, "C_ACP", f"{DOC_ROADMAP}/{DOC_HUMAN}", "ACP 方法", CANONICAL_ACP_METHODS, methods)


def check_mcp(f: List[Finding], doc: Dict[str, str]) -> None:
    rm_text = doc.get(DOC_ROADMAP, "")
    sec = find_section(rm_text, "受限 MCP Workspace Tools")
    if not sec:
        f.append(Finding("C_MCP", DOC_ROADMAP, False,
                         message="未找到受限 MCP Workspace Tools 章节", found="section missing"))
        return
    # 仅取枚举行（含反引号最多的一行）中的工具，避免表格描述重复计入
    tools = []
    for ln in sec.splitlines():
        toks = backticked(ln)
        if len(toks) >= 5:
            tools = toks
            break
    tools = [t for t in tools if t in CANONICAL_MCP_TOOLS]
    _check_list(f, "C_MCP", DOC_ROADMAP, "MCP Workspace Tools", CANONICAL_MCP_TOOLS, tools)


def check_phases(f: List[Finding], doc: Dict[str, str]) -> None:
    rm_text = doc.get(DOC_ROADMAP, "")
    sec = find_section(rm_text, "Phase 总览", "Phase Overview")
    if not sec:
        f.append(Finding("C_PHASES", DOC_ROADMAP, False,
                         message="未找到 Phase 总览章节", found="section missing"))
        return
    tbl = first_table_in(sec)
    if not tbl:
        f.append(Finding("C_PHASES", DOC_ROADMAP, False,
                         message="Phase 总览章节无表格", found="table missing"))
        return
    _h, rows = table_data(tbl)
    present_letters = []
    for r in rows:
        for tok in backticked(r[0]):
            m = re.match(r"Phase\s+([A-G])", tok)
            if m:
                present_letters.append(m.group(1))
    ok = set(present_letters) == set(CANONICAL_PHASES.keys())
    f.append(
        Finding(
            "C_PHASES",
            DOC_ROADMAP,
            ok,
            message="Phase A-G 全部存在且一致" if ok else "Phase 集合不一致（应为 A-G）",
            found=f"letters={present_letters}",
        )
    )


# ---- 语义检查 ------------------------------------------------------------

def check_phase_a_def(f: List[Finding], doc: Dict[str, str]) -> None:
    rm_text = doc.get(DOC_ROADMAP, "")
    body = None
    for _lvl, title, b in iter_sections(rm_text):
        if re.match(r"^`?Phase A`?", title.strip()):
            body = b
            break
    if body is None:
        f.append(Finding("S_PHASE_A_DEF", DOC_ROADMAP, False,
                         message="未找到 Phase A 章节", found="section missing"))
        return
    has_delivery = any(m in body for m in PHASE_A_DOC_DELIVERY_MARKERS)
    has_runtime = any(m in body for m in PHASE_A_RUNTIME_IMPL_MARKERS)
    ok = has_delivery and not has_runtime
    f.append(
        Finding(
            "S_PHASE_A_DEF",
            DOC_ROADMAP,
            ok,
            message="Phase A 被定义为架构文档交付（非未来运行时实现）" if ok
            else "Phase A 未明确定义为架构文档交付，或混入了运行时实现描述",
            found=f"has_delivery_marker={has_delivery}; has_runtime_impl_marker={has_runtime}",
        )
    )


NEGATION_WORDS = ("不得", "MUST NOT", "不", "禁止", "不允许", "仅经授权")


def _has_affirmative(body: str, keyword: str,
                     negations: Tuple[str, ...] = NEGATION_WORDS,
                     window: int = 50, boundary: bool = False) -> bool:
    """判断 keyword 是否以"正向（非否定）"形式出现在 body 中。

    若 keyword 之前的 window 个字符内出现任一否定词，则视为否定表述，
    不计入正向。用于区分"Coding Worker 不得 push"（合法）与
    "Coding Worker 可 push"（违规），以及"不得再次调用 deliver()"（合法）
    与"再次调用 deliver()"（违规）。

    boundary=True 时要求 keyword 前后不是单词字符，以排除
    PUSH_COMPLETED / DRAFT_PR_CREATED 这类事件标识符中的子串匹配。
    """
    pat = re.escape(keyword)
    if boundary:
        pat = r"(?<![\w])" + pat + r"(?![\w])"
    for m in re.finditer(pat, body, re.IGNORECASE):
        before = body[max(0, m.start() - window):m.start()]
        if not any(neg in before for neg in negations):
            return True
    return False


def _contains_affirmative_deliver(body: str) -> bool:
    return _has_affirmative(body, "DeliveryController.deliver()")


# 仅当"创建/执行 Draft PR"等动作动词出现且无否定时，才算正向执行动作；
# 排除"评审 Draft PR"这类合法引用。
_CREATION_VERBS = ("创建", "执行", "负责", "发起", "生成", "建")


def _contains_affirmative_push_or_draft_pr(body: str) -> bool:
    """正向（非否定）地声明执行 push 或创建 Draft PR。

    排除事件标识符中的子串（如 PUSH_COMPLETED / DRAFT_PR_CREATED），
    以及"评审 Draft PR"这类仅引用、非执行的合法表述。
    """
    # "push" 仅作为独立单词匹配（排除 PUSH_COMPLETED 等标识符）
    if _has_affirmative(body, "push", boundary=True):
        return True
    # "Draft PR" 仅在伴随创建/执行动词且无否定时才计为执行动作
    for verb in _CREATION_VERBS:
        for m in re.finditer(verb + r"\s*Draft PR", body, re.IGNORECASE):
            before = body[max(0, m.start() - 50):m.start()]
            if not any(neg in before for neg in NEGATION_WORDS):
                return True
    return False


def _release_violates_post_review(ra_body: str) -> bool:
    """True 表示 Release Agent 被正向描述为 Review 之后复投 / 创建第二个 Draft PR。

    文档允许（且要求）显式禁止这些行为（"不得再次调用 deliver()、不得重复创建
    Draft PR"），故仅当"再次调用 deliver()/创建第二个 Draft PR"以正向形式出现时才判违。
    """
    if "创建第二个" in ra_body and "Draft PR" in ra_body:
        return True
    m = re.search(r"再次调用\s*`?DeliveryController\.deliver\(\)`?", ra_body)
    if m and "不得" not in ra_body[max(0, m.start() - 30):m.end()]:
        return True
    return False


def _release_delivery_after_review(ra_body: str) -> bool:
    """True 表示 Release Agent 被正向（非否定）描述为在 Review 之后才执行受控交付
    （push / 创建 Draft PR / deliver）。

    真实文档中"Review 通过后进入验收协调：仅发起 USER_ACTION_REQUIRED，
    不得再次调用 deliver()"属于合法的后置验收阶段——其中 deliver 为否定表述，
    不在本检查范围内。本检查仅当"Review 之后/通过后"紧随**非否定**的交付动词
    （push / 创建 Draft PR / deliver / 受控交付）时才判违。
    """
    for m in re.finditer(r"Review\s*(?:之后|通过后|完成后)", ra_body, re.IGNORECASE):
        after = ra_body[m.end():m.end() + 60]
        for vm in re.finditer(r"push|创建\s*Draft PR|deliver|受控交付|交付", after, re.IGNORECASE):
            before_v = after[max(0, vm.start() - 20):vm.start()]
            if not any(neg in before_v for neg in NEGATION_WORDS):
                return True
    return False


def check_draft_pr_responsibility(f: List[Finding], doc: Dict[str, str]) -> None:
    """S_DRAFT_PR_RESP — Draft PR / 受控交付责任模型（第二轮返工后冻结）。

    新模型：
      * Coding Worker = 仅负责「代码 + 测试 + 本地 Commit」。必须不 push、
        不创建 Draft PR、不调用 DeliveryController.deliver()、不接触 GitHub
        交付凭据。
      * Release Agent = **唯一**的逻辑调用方 of DeliveryController.deliver()。
        它在 Coding Worker 本地 Commit 之后、CI/Review **之前**启动；执行受控
        Push + 创建/幂等 Draft PR + 触发 CI。Review 之后仅发出
        USER_ACTION_REQUIRED(reason=FINAL_ACCEPTANCE)，不得 re-deliver、
        不得创建第二个 Draft PR。

    规则必须同时验证两点：
      (a) Release Agent 是 DeliveryController.deliver() 的唯一逻辑调用方；
      (b) DeliveryController.deliver() 是 Push + Draft PR 的唯一执行方法
          （即没有其他角色被描述成做 push/Draft PR，也没有其他交付机制被
          当作交付方法）。

    规则在以下情况必须 FAIL：
      * Coding Worker 小节声称 push / Draft PR / deliver / 交付凭据；
      * 除 Release Agent 外任何角色被描述为调用 deliver() / 作为交付调用方；
      * 断言了 DeliveryController.deliver() 之外的交付方法；
      * Release Agent 被描述为在 Review 之后启动 / re-deliver / 创建第二个 Draft PR。
    """
    role_text = doc.get(DOC_ROLE, "")
    bodies = {name: b for name, b in iter_role_subsections(role_text)}
    cw = bodies.get("Coding Worker")
    ra = bodies.get("Release Agent")

    problems: List[str] = []

    # (a) Release Agent 是 deliver() 的唯一逻辑调用方
    release_is_caller = bool(ra) and _contains_affirmative_deliver(ra)
    if not release_is_caller:
        problems.append("Release Agent 未被描述为 DeliveryController.deliver() 的唯一调用方")
    other_callers = [name for name, b in bodies.items()
                     if name != "Release Agent" and _contains_affirmative_deliver(b)]
    if other_callers:
        problems.append("除 Release Agent 外存在调用 deliver() 的角色: " + ", ".join(other_callers))

    # (b) DeliveryController.deliver() 是 Push + Draft PR 的唯一执行方法
    if cw is not None:
        if _contains_affirmative_push_or_draft_pr(cw):
            problems.append("Coding Worker 被描述为执行 push / 创建 Draft PR（违反仅本地 Commit 责任）")
        if _has_affirmative(cw, "交付凭据") or _has_affirmative(cw, "GitHub 交付凭据"):
            problems.append("Coding Worker 被描述为接触 GitHub 交付凭据")
    other_deliverers = [name for name, b in bodies.items()
                        if name not in ("Release Agent", "Coding Worker")
                        and _contains_affirmative_push_or_draft_pr(b)]
    if other_deliverers:
        problems.append("除 Release Agent 外存在执行 push/Draft PR 的角色: " + ", ".join(other_deliverers))

    # Release Agent 不得 Review 之后启动 / re-deliver / 创建第二个 Draft PR
    if ra is not None and _release_violates_post_review(ra):
        problems.append("Release Agent 被描述为 Review 之后启动 / 重复交付 / 创建第二个 Draft PR")
    # Release Agent 受控交付必须在 Review 之前（不得 Review 之后才交付）
    if ra is not None and _release_delivery_after_review(ra):
        problems.append("Release Agent 被描述为在 Review 之后才执行受控交付（违反交付先于 Review 的时序）")

    ok = not problems
    f.append(
        Finding(
            "S_DRAFT_PR_RESP",
            DOC_ROLE,
            ok,
            message="Draft PR/受控交付责任唯一归属 Release Agent（DeliveryController.deliver() 唯一执行方法，Coding Worker 仅本地 Commit）"
            if ok else "Draft PR 责任模型冲突: " + "; ".join(problems),
            found="; ".join(problems) if problems else
            f"release_is_caller={release_is_caller}; other_callers={other_callers}",
        )
    )


def _extract_lifecycle(text: str) -> Optional[str]:
    # 优先从 ACTIVITY §1.2.1，其次 ROADMAP Phase F
    for src in (text,):
        m = re.search(r"Issue\s*→.*?TASK_COMPLETED", src, re.S)
        if m:
            return m.group(0)
    return None


def check_max_rounds(f: List[Finding], doc: Dict[str, str]) -> None:
    """S_MAX_ROUNDS — 返工上限 MAX_ROUNDS = 2 必须在文档中明确声明。

    第二轮返工后冻结：rework 受 MAX_ROUNDS=2 约束（同 PR 复投上限 2）。
    检索规范生命周期串与权限模型中 "MAX_ROUNDS=2" / "review rounds: 2" /
    "re-review 上限 2"。文档实际保证 MAX_ROUNDS=2，故断言之。
    """
    all_text = "\n".join(doc.get(dk, "") for dk in ALL_DOCS)
    ok = bool(re.search(r"MAX_ROUNDS\s*=\s*2", all_text, re.IGNORECASE)) or \
         ("review rounds: 2" in all_text.lower()) or \
         ("re-review 上限 2" in all_text)
    f.append(
        Finding(
            "S_MAX_ROUNDS",
            "ALL",
            ok,
            message="返工上限 MAX_ROUNDS=2 已在文档中明确声明（rework 受 MAX_ROUNDS=2 约束）"
            if ok else "文档未声明返工上限 MAX_ROUNDS=2",
            found="MAX_ROUNDS=2 declared" if ok else "no MAX_ROUNDS=2 declaration found",
        )
    )


def check_lifecycle_review_on_pr(f: List[Finding], doc: Dict[str, str]) -> None:
    """S_LIFECYCLE_REVIEW_ON_PR — Review / rework 必须发生在『同一 PR』上。

    对应生命周期子检查：Review 仅能在已有 PR 上进行（生命周期须体现
    "同一 PR" / "同一 PR re-review"）。冻结文档生命周期串为
    "rework on same PR (… MAX_ROUNDS=2)"（ACTIVITY §1.2.1），断言之。
    """
    act_text = doc.get(DOC_ACTIVITY, "")
    lc = _extract_lifecycle(act_text)
    if not lc:
        f.append(Finding("S_LIFECYCLE_REVIEW_ON_PR", DOC_ACTIVITY, False,
                         message="未找到规范生命周期定义", found="lifecycle string missing"))
        return
    ok = ("same PR" in lc) or ("同一 PR" in lc) or ("同一PR" in lc)
    f.append(
        Finding(
            "S_LIFECYCLE_REVIEW_ON_PR",
            DOC_ACTIVITY,
            ok,
            message="生命周期体现 Review/rework 在『同一 PR』上进行（Review 不先于 PR 存在）"
            if ok else "生命周期未体现 Review/rework 在『同一 PR』上进行",
            found=f"lifecycle_has_same_pr={ok}",
        )
    )


def check_role_fullname(f: List[Finding], doc: Dict[str, str]) -> None:
    """S_ROLE_FULLNAME — 机器可消费区域仅使用 8 个 CANONICAL_ROLES 完整权威名。

    机器可消费区域（角色小节 §3.x、UAR 表格、事件/标识符表格、频道表格）必须
    仅使用完整权威名。复用 S_TABLE_ROLE_ABBREV 的手法：先把每个 canonical 全名
    替换成占位符（空格），再用词边界正则扫描禁用的歧义简称
    （FORBIDDEN_ROLE_ABBREV = Master / Scheduler / Reviewer）。

    关键：绝不能对完整名内部包含的单词误报（如 "Master" 出现在
    "Hermes Master / Boss" 内部，应先被全名替换消除）。
    """
    violations: List[str] = []
    # 收集机器可消费区域：角色小节 §3.x 正文 + ROLE/ACTIVITY/ROADMAP/HUMAN 的表格
    regions: List[Tuple[str, str]] = []
    role_text = doc.get(DOC_ROLE, "")
    for _name, body in iter_role_subsections(role_text):
        regions.append((DOC_ROLE, body))
    for dk in (DOC_ROLE, DOC_ACTIVITY, DOC_ROADMAP, DOC_HUMAN):
        text = doc.get(dk, "")
        for tbl in all_tables(text):
            for r in tbl:
                for cell in r:
                    regions.append((dk, cell))
    for dk, text in regions:
        s = text
        for name in CANONICAL_ROLES:
            s = s.replace(name, " ")
        for abbr in FORBIDDEN_ROLE_ABBREV:
            if re.search(r"(?<![A-Za-z])" + re.escape(abbr) + r"(?![A-Za-z])", s):
                violations.append(f"{dk}:contains forbidden role abbrev '{abbr}'")
    ok = not violations
    f.append(
        Finding(
            "S_ROLE_FULLNAME",
            "ALL",
            ok,
            message="机器可消费区域均使用完整权威角色名，无 Master/Scheduler/Reviewer 歧义简称"
            if ok else "机器可消费区域出现歧义角色简称（未使用完整权威名）",
            found="; ".join(violations[:10]) if violations else "no forbidden abbrev in machine-consumable regions",
        )
    )


def _map_segment(seg: str) -> Optional[str]:
    s = seg.lower()
    if "final_acceptance" in s:
        return "final_acceptance"
    if "task_completed" in s:
        return "task_completed"
    if "review" in s:
        return "review"
    if "draft pr" in s or "controlled delivery" in s or "delivery" in s:
        return "draft_pr"
    if "ci" in s:
        return "ci"
    if "commit" in s:
        return "commit"
    if "code" in s or "test" in s:
        return "code"
    if "plan" in s:
        return "plan"
    if "issue" in s:
        return "issue"
    if "rework" in s:
        return "rework"
    return None


def check_lifecycle(f: List[Finding], doc: Dict[str, str]) -> None:
    act_text = doc.get(DOC_ACTIVITY, "")
    lc = _extract_lifecycle(act_text)
    if not lc:
        f.append(Finding("S_LIFECYCLE_ORDER", DOC_ACTIVITY, False,
                         message="未找到规范生命周期定义", found="lifecycle string missing"))
        return
    segments = [s.strip() for s in re.split(r"→", lc)]
    anchors: List[str] = []
    for seg in segments:
        a = _map_segment(seg)
        if a:
            anchors.append(a)
    positions = {}
    for anchor in LIFECYCLE_ANCHORS_ORDER:
        idxs = [i for i, a in enumerate(anchors) if a == anchor]
        if idxs:
            positions[anchor] = idxs[0]
    missing = [a for a in LIFECYCLE_ANCHORS_ORDER if a not in positions]
    ordered = all(
        positions.get(LIFECYCLE_ANCHORS_ORDER[i], 10 ** 9) < positions.get(LIFECYCLE_ANCHORS_ORDER[i + 1], -1)
        for i in range(len(LIFECYCLE_ANCHORS_ORDER) - 1)
        if LIFECYCLE_ANCHORS_ORDER[i] in positions and LIFECYCLE_ANCHORS_ORDER[i + 1] in positions
    )
    ok = (not missing) and ordered
    f.append(
        Finding(
            "S_LIFECYCLE_ORDER",
            DOC_ACTIVITY,
            ok,
            message="生命周期阶段顺序正确（Issue→…→Draft PR→CI→Review→FINAL_ACCEPTANCE→TASK_COMPLETED）"
            if ok else "生命周期阶段缺失或顺序错误",
            found=f"anchors={anchors}; missing={missing}; ordered={ordered}",
        )
    )

    # Review 不得出现在 Draft PR 创建之前
    draft_idx = positions.get("draft_pr")
    review_idx = positions.get("review")
    if draft_idx is not None and review_idx is not None:
        ok_review = draft_idx < review_idx
    else:
        ok_review = False
    f.append(
        Finding(
            "S_REVIEW_BEFORE_PR",
            DOC_ACTIVITY,
            ok_review,
            message="Independent Review 在 Draft PR 创建之后进行（Review 不先于 PR）"
            if ok_review else "Review 出现在 Draft PR 创建之前（违反责任时序）",
            found=f"draft_pr_idx={draft_idx}; review_idx={review_idx}",
        )
    )


def check_task_completed_status(f: List[Finding], doc: Dict[str, str]) -> None:
    act_text = doc.get(DOC_ACTIVITY, "")
    sec = find_section(act_text, "事件清单", "Event Catalog")
    if not sec:
        f.append(Finding("S_TASK_COMPLETED_STATUS", DOC_ACTIVITY, False,
                         message="未找到事件清单", found="section missing"))
        return
    tbl = first_table_in(sec)
    if not tbl:
        f.append(Finding("S_TASK_COMPLETED_STATUS", DOC_ACTIVITY, False,
                         message="事件清单无表格", found="table missing"))
        return
    _h, rows = table_data(tbl)
    row_text = ""
    for r in rows:
        if "TASK_COMPLETED" in " | ".join(r):
            row_text = " | ".join(r)
            break
    ok = "待最终验收" not in row_text
    f.append(
        Finding(
            "S_TASK_COMPLETED_STATUS",
            DOC_ACTIVITY,
            ok,
            message="TASK_COMPLETED 未包含'待最终验收'状态" if ok
            else "TASK_COMPLETED 含有'待最终验收'状态（违反终态语义）",
            found=f"row_text={row_text[:80]}",
        )
    )


def check_final_acceptance_blocked(f: List[Finding], doc: Dict[str, str]) -> None:
    act_text = doc.get(DOC_ACTIVITY, "")
    sec = find_section(act_text, "事件清单", "Event Catalog")
    if not sec:
        f.append(Finding("S_FINAL_ACCEPTANCE_BLOCKED", DOC_ACTIVITY, False,
                         message="未找到事件清单", found="section missing"))
        return
    tbl = first_table_in(sec)
    if not tbl:
        f.append(Finding("S_FINAL_ACCEPTANCE_BLOCKED", DOC_ACTIVITY, False,
                         message="事件清单无表格", found="table missing"))
        return
    _h, rows = table_data(tbl)
    uar_row = ""
    for r in rows:
        if "USER_ACTION_REQUIRED" in " | ".join(r):
            uar_row = " | ".join(r)
            break
    ok = "blocked" in uar_row.lower()
    f.append(
        Finding(
            "S_FINAL_ACCEPTANCE_BLOCKED",
            DOC_ACTIVITY,
            ok,
            message="USER_ACTION_REQUIRED（含 FINAL_ACCEPTANCE）状态为 blocked，未解决仍阻塞"
            if ok else "USER_ACTION_REQUIRED 状态未声明为 blocked",
            found=f"uar_row_status_field={uar_row}",
        )
    )


def check_temp_channel_archive(f: List[Finding], doc: Dict[str, str]) -> None:
    rm_text = doc.get(DOC_ROADMAP, "")
    norm_rm = rm_text.replace("`", "")
    ok = ("FINAL_ACCEPTANCE 完成后方可触发 TASK_COMPLETED 并归档" in norm_rm) or \
         ("TASK_COMPLETED 且验收后归档" in norm_rm)
    f.append(
        Finding(
            "S_TEMP_CHANNEL_ARCHIVE",
            DOC_ROADMAP,
            ok,
            message="临时频道在 FINAL_ACCEPTANCE/验收后才归档（终验前不归档）" if ok
            else "临时频道归档未显式绑定到最终验收之后",
            found="archive-bound-to-final-acceptance" if ok else "no archive-after-acceptance statement",
        )
    )


def check_buzz_gate(f: List[Finding], doc: Dict[str, str]) -> None:
    rm_text = doc.get(DOC_ROADMAP, "")
    gate_sec = None
    for _lvl, title, b in iter_sections(rm_text):
        if "Buzz 接入门禁" in title or "BUZZ_ENV_READY" in title:
            gate_sec = b
            break
    has_gate = ("BUZZ_ENV_READY" in rm_text) or ("Buzz 接入门禁" in rm_text)
    has_deploy = bool(gate_sec) and (("部署" in gate_sec) or ("实例" in gate_sec))
    has_account = bool(gate_sec) and ("账号" in gate_sec)
    has_conn = bool(gate_sec) and ("连接" in gate_sec)
    ok = has_gate and has_deploy and has_account and has_conn
    f.append(
        Finding(
            "S_BUZZ_GATE",
            DOC_ROADMAP,
            ok,
            message="Buzz 真实试用前存在实例部署/账号/连接配置门禁（BUZZ_ENV_READY）" if ok
            else "Buzz 真实试用前缺少实例部署/账号/连接配置门禁",
            found=f"has_gate={has_gate}; deploy={has_deploy}; account={has_account}; conn={has_conn}",
        )
    )


def check_pr_order(f: List[Finding], doc: Dict[str, str]) -> None:
    rm_text = doc.get(DOC_ROADMAP, "")
    sec = find_section(rm_text, "PR 堆叠与 Rebase 顺序", "Stacked PR")
    if not sec:
        f.append(Finding("S_PR_ORDER", DOC_ROADMAP, False,
                         message="未找到 PR 堆叠与 Rebase 顺序章节", found="section missing"))
        return
    has_both = ("PR #7" in sec) and ("PR #8" in sec)
    has_order = (("先合并" in sec) and ("随后" in sec)) or ("rebase 到" in sec) or ("顺序" in sec)
    ok = has_both and has_order
    f.append(
        Finding(
            "S_PR_ORDER",
            DOC_ROADMAP,
            ok,
            message="PR #7 与 PR #8 声明了显式集成/堆叠顺序" if ok
            else "PR #7 与 PR #8 缺少显式集成顺序",
            found=f"has_both={has_both}; has_order={has_order}",
        )
    )


def check_table_role_abbrev(f: List[Finding], doc: Dict[str, str]) -> None:
    violations: List[str] = []
    for dk in ALL_DOCS:
        text = doc.get(dk, "")
        for tbl in all_tables(text):
            for r in tbl:
                for cell in r:
                    s = cell
                    for name in CANONICAL_ROLES:
                        s = s.replace(name, " ")
                    for abbr in FORBIDDEN_ROLE_ABBREV:
                        if re.search(r"(?<![A-Za-z])" + re.escape(abbr) + r"(?![A-Za-z])", s):
                            violations.append(f"{dk}:cell='{cell.strip()}' contains '{abbr}'")
    ok = not violations
    f.append(
        Finding(
            "S_TABLE_ROLE_ABBREV",
            "ALL",
            ok,
            message="机器可消费表格均使用完整权威角色名，无 Master/Scheduler/Reviewer 歧义简称"
            if ok else "机器可消费表格中出现歧义角色简称",
            found="; ".join(violations[:10]) if violations else "no abbrev in tables",
        )
    )


def check_no_second_truth(f: List[Finding], doc: Dict[str, str]) -> None:
    # Buzz
    human_sec0 = find_section(doc.get(DOC_HUMAN, ""), "文档目的与权威声明", "权威声明") or doc.get(DOC_HUMAN, "")
    buzz_ok = ("Buzz" in human_sec0) and (
        ("第二事实源" in human_sec0) or ("协作展示" in human_sec0) or ("不得" in human_sec0 and "权威" in human_sec0)
    )
    f.append(
        Finding(
            "S_NO_SECOND_TRUTH_BUZZ",
            DOC_HUMAN,
            buzz_ok,
            message="Buzz 被明确限定为协作展示/聚合层，非权威事实源" if buzz_ok
            else "Buzz 未被明确限定为非权威事实源",
            found=f"buzz_clause_present={buzz_ok}",
        )
    )

    # Canvas
    rm_text = doc.get(DOC_ROADMAP, "")
    canvas_sec = find_section(rm_text, "Canvas 模板", "Canvas Template") or rm_text
    canvas_ok = ("Canvas" in canvas_sec) and (
        ("不得成为唯一证据源" in canvas_sec) or ("非唯一证据源" in canvas_sec)
    )
    f.append(
        Finding(
            "S_NO_SECOND_TRUTH_CANVAS",
            DOC_ROADMAP,
            canvas_ok,
            message="Canvas 被明确限定为协作展示层，非唯一证据源" if canvas_ok
            else "Canvas 未被明确限定为非唯一证据源",
            found=f"canvas_clause_present={canvas_ok}",
        )
    )

    # Activity Feed
    act_text = doc.get(DOC_ACTIVITY, "")
    act_ok = ("Activity Feed" in act_text) and (
        ("不得作为事件权威源" in act_text) or ("非唯一证据源" in act_text)
    )
    f.append(
        Finding(
            "S_NO_SECOND_TRUTH_ACTIVITY",
            DOC_ACTIVITY,
            act_ok,
            message="Activity Feed 被明确限定为协作展示层，非唯一证据源" if act_ok
            else "Activity Feed 未被明确限定为非唯一证据源",
            found=f"activity_clause_present={act_ok}",
        )
    )


def check_protected_repo(f: List[Finding], doc: Dict[str, str]) -> None:
    all_text = "\n".join(doc.get(dk, "") for dk in ALL_DOCS)
    present = PROTECTED_REPO in all_text
    associated = ("PROTECTED_REPOS" in all_text and PROTECTED_REPO in all_text) or \
                 (PROTECTED_REPO in all_text and ("永久保护" in all_text or "保护" in all_text))
    # ALLOWED_GITHUB_REPOS 的赋值句中不得包含受保护仓库。逐行判断，仅当
    # 同一行内出现赋值式（= / : / {）后紧跟受保护仓库时才视为泄露，避免
    # "ALLOWED / PROTECTED 约束；hermes-learning-os 永不…"这类说明句被误判。
    leak = False
    for line in all_text.splitlines():
        if "ALLOWED_GITHUB_REPOS" in line and PROTECTED_REPO in line:
            if re.search(r"ALLOWED_GITHUB_REPOS\s*[=:{].*?" + re.escape(PROTECTED_REPO), line):
                leak = True
                break
    ok = present and associated and not leak
    f.append(
        Finding(
            "S_PROTECTED_REPO",
            "ALL",
            ok,
            message="yzhlx/hermes-learning-os 保持 protected，且未落入 ALLOWED_GITHUB_REPOS"
            if ok else "受保护仓库约束缺失或被错误放宽",
            found=f"present={present}; associated_with_protection={associated}; leaked_into_allowed={leak}",
        )
    )


# ---------------------------------------------------------------------------
# 第二轮补强：生命周期 / 责任冲突语义（9 条）
# 每条规则只验证一个明确的语义断言，定向变异能精确触发，不被其他冗余段落兜底。
# ---------------------------------------------------------------------------

def _role_bodies(doc: Dict[str, str]) -> Dict[str, str]:
    role_text = doc.get(DOC_ROLE, "")
    return {name: b for name, b in iter_role_subsections(role_text)}


def check_deliver_logical_role_unique(f: List[Finding], doc: Dict[str, str]) -> None:
    """S_DELIVER_LOGICAL_ROLE_UNIQUE — DeliveryController.deliver() 逻辑调用方唯一。

    Release Agent 必须是**唯一**以正向（非否定）形式声明调用
    DeliveryController.deliver() 的角色。任何其它角色正向声明调用 deliver()
    即判违（fail-closed：任何非预期调用方都破坏交付纪律）。
    """
    bodies = _role_bodies(doc)
    callers = [name for name, b in bodies.items() if _contains_affirmative_deliver(b)]
    ok = callers == ["Release Agent"]
    f.append(Finding(
        "S_DELIVER_LOGICAL_ROLE_UNIQUE", DOC_ROLE, ok,
        message="DeliveryController.deliver() 的逻辑调用方唯一归属 Release Agent"
        if ok else "deliver() 逻辑调用方不唯一或被非 Release Agent 角色正向声明调用",
        found=f"affirmative_callers={callers}",
    ))


def check_release_agent_start_order(f: List[Finding], doc: Dict[str, str]) -> None:
    """S_RELEASE_AGENT_START_ORDER — Release Agent 启动时点正确。

    正向约束：Release Agent 在 Coding Worker 本地 Commit 之后、CI 与
    Independent Reviewer **之前**启动。若文档描述 Release Agent 在 Review
    通过后才启动交付，则判违。
    """
    all_text = "\n".join(doc.get(dk, "") for dk in ALL_DOCS)
    ok_pos = bool(re.search(r"CI 与 Independent Reviewer 之前", all_text)) or \
             bool(re.search(r"Independent Reviewer.{0,30}之前", all_text))
    bad = "在 Review 通过后再启动交付" in all_text
    ok = ok_pos and not bad
    f.append(Finding(
        "S_RELEASE_AGENT_START_ORDER", "ALL", ok,
        message="Release Agent 在 CI 与 Independent Reviewer 之前启动（Coding Worker 本地 Commit 之后）"
        if ok else "Release Agent 启动时点违规（非 CI/Review 之前，或在 Review 通过后才启动）",
        found=f"ok_pos={ok_pos}; post_review_start={bad}",
    ))


def check_release_agent_two_stage_duty(f: List[Finding], doc: Dict[str, str]) -> None:
    """S_RELEASE_AGENT_TWO_STAGE_DUTY — Release Agent 两阶段职责标识。

    Release Agent 必须同时体现：受控交付（"受控"）+ 首次执行阶段（"首次"）。
    移除任一标识即无法区分首阶段受控 Push 与验收后协调。
    """
    bodies = _role_bodies(doc)
    ra = bodies.get("Release Agent")
    if ra is None:
        f.append(Finding("S_RELEASE_AGENT_TWO_STAGE_DUTY", DOC_ROLE, False,
                         message="未找到 Release Agent 角色小节", found="section missing"))
        return
    has_controlled = "受控" in ra
    has_first = "首次" in ra
    ok = has_controlled and has_first
    f.append(Finding(
        "S_RELEASE_AGENT_TWO_STAGE_DUTY", DOC_ROLE, ok,
        message="Release Agent 两阶段职责清晰（受控交付 + 首次执行阶段）"
        if ok else "Release Agent 两阶段职责标识缺失（缺'受控'或'首次'）",
        found=f"has_controlled={has_controlled}; has_first={has_first}",
    ))


def check_canonical_lifecycle_complete(f: List[Finding], doc: Dict[str, str]) -> None:
    """S_CANONICAL_LIFECYCLE_COMPLETE — 规范生命周期在 ACTIVITY 与 ROADMAP 双处完整。

    必须同时满足：ACTIVITY §1.2.1 与 ROADMAP Phase F 两份生命周期串均包含
    全部 10 个锚点且顺序正确（Issue→…→Draft PR→CI→Review→FINAL_ACCEPTANCE
    →TASK_COMPLETED，且 Review 在 Draft PR 之后）。任一处重排/缺失即判违。
    """
    problems: List[str] = []
    for dk, src in ((DOC_ACTIVITY, doc.get(DOC_ACTIVITY, "")),
                    (DOC_ROADMAP, doc.get(DOC_ROADMAP, ""))):
        lc = _extract_lifecycle(src)
        if not lc:
            problems.append(f"{dk}: lifecycle string missing")
            continue
        segments = [s.strip() for s in re.split(r"→", lc)]
        anchors: List[str] = []
        for seg in segments:
            a = _map_segment(seg)
            if a:
                anchors.append(a)
        positions = {}
        for anchor in LIFECYCLE_ANCHORS_ORDER:
            idxs = [i for i, a in enumerate(anchors) if a == anchor]
            if idxs:
                positions[anchor] = idxs[0]
        missing = [a for a in LIFECYCLE_ANCHORS_ORDER if a not in positions]
        ordered = all(
            positions.get(LIFECYCLE_ANCHORS_ORDER[i], 10 ** 9) <
            positions.get(LIFECYCLE_ANCHORS_ORDER[i + 1], -1)
            for i in range(len(LIFECYCLE_ANCHORS_ORDER) - 1)
            if LIFECYCLE_ANCHORS_ORDER[i] in positions and LIFECYCLE_ANCHORS_ORDER[i + 1] in positions
        )
        draft_idx = positions.get("draft_pr")
        review_idx = positions.get("review")
        review_after_pr = (draft_idx is not None and review_idx is not None and draft_idx < review_idx)
        if missing or not ordered or not review_after_pr:
            problems.append(
                f"{dk}: missing={missing}; ordered={ordered}; review_after_pr={review_after_pr}")
    ok = not problems
    f.append(Finding(
        "S_CANONICAL_LIFECYCLE_COMPLETE", "ALL", ok,
        message="规范生命周期在 ACTIVITY 与 ROADMAP 双处完整且顺序正确"
        if ok else "规范生命周期不完整/顺序错误: " + "; ".join(problems),
        found="; ".join(problems) if problems else "both lifecycle strings valid",
    ))


def check_phase_b_lifecycle_complete(f: List[Finding], doc: Dict[str, str]) -> None:
    """S_PHASE_B_LIFECYCLE_COMPLETE — ROADMAP Phase B 生命周期验收标准完整。

    Phase B 必须完整声明：受控交付（创建/幂等获取 Draft PR）、Coding Worker
    边界（不得创建 Draft PR）、rework 复用同 PR（不新建第 2 个 PR）、返工上限
    MAX_ROUNDS=2、FINAL_ACCEPTANCE 阻塞绑定。任一处缺失即判违。
    """
    rm_text = doc.get(DOC_ROADMAP, "")
    body = None
    for _lvl, title, b in iter_sections(rm_text):
        if re.match(r"^`?Phase B`?", title.strip()):
            body = b
            break
    if body is None:
        f.append(Finding("S_PHASE_B_LIFECYCLE_COMPLETE", DOC_ROADMAP, False,
                         message="未找到 Phase B 章节", found="section missing"))
        return
    required = {
        "受控交付创建 Draft PR": "创建/幂等获取 Draft PR" in body,
        "Coding Worker 不得创建 Draft PR": "不得创建 Draft PR" in body,
        "rework 复用同 PR": "不新建第 2 个 PR" in body,
        "MAX_ROUNDS=2": bool(re.search(r"MAX_ROUNDS\s*=\s*2", body, re.IGNORECASE)),
        "FINAL_ACCEPTANCE 绑定": "FINAL_ACCEPTANCE" in body,
        "受控交付标识": "受控" in body,
    }
    missing = [k for k, v in required.items() if not v]
    ok = not missing
    f.append(Finding(
        "S_PHASE_B_LIFECYCLE_COMPLETE", DOC_ROADMAP, ok,
        message="Phase B 生命周期验收标准完整（受控交付/rework 同 PR/MAX_ROUNDS=2/FINAL_ACCEPTANCE 绑定）"
        if ok else "Phase B 生命周期验收标准缺失: " + "; ".join(missing),
        found="; ".join(missing) if missing else "phase_b_complete",
    ))


def check_rework_same_pr(f: List[Finding], doc: Dict[str, str]) -> None:
    """S_REWORK_SAME_PR — rework 必须复用同一 Draft PR（不新建第 2 个 PR）。"""
    rm_text = doc.get(DOC_ROADMAP, "")
    ok = "不新建第 2 个 PR" in rm_text
    f.append(Finding(
        "S_REWORK_SAME_PR", DOC_ROADMAP, ok,
        message="rework 复用同一 Draft PR（不新建第 2 个 PR）"
        if ok else "未声明 rework 复用同 PR（不新建第 2 个 PR）",
        found="rework_same_pr_stated" if ok else "no '不新建第 2 个 PR' statement",
    ))


def check_final_acceptance_blocking(f: List[Finding], doc: Dict[str, str]) -> None:
    """S_FINAL_ACCEPTANCE_BLOCKING — FINAL_ACCEPTANCE 前 TASK_COMPLETED 不得触发/归档。

    要求文档明确以否定形式声明：TASK_COMPLETED 在 FINAL_ACCEPTANCE 完成前
    **不得**触发/归档（任务视为阻塞）。若改为"可以提前"则判违。
    """
    all_text = "\n".join(doc.get(dk, "") for dk in ALL_DOCS)
    ok = bool(re.search(r"TASK_COMPLETED[^。\n]{0,15}不得触发/归档", all_text)) or \
         bool(re.search(r"不得触发/归档[^。\n]{0,15}TASK_COMPLETED", all_text))
    f.append(Finding(
        "S_FINAL_ACCEPTANCE_BLOCKING", "ALL", ok,
        message="FINAL_ACCEPTANCE 完成前 TASK_COMPLETED 不得触发/归档（任务视为阻塞）"
        if ok else "未声明 FINAL_ACCEPTANCE 前 TASK_COMPLETED 不得触发/归档",
        found="final_acceptance_blocking_stated" if ok else "no blocking statement",
    ))


def check_role_contract_full_names(f: List[Finding], doc: Dict[str, str]) -> None:
    """S_ROLE_CONTRACT_FULL_NAMES — 机器可消费文本禁用裸角色简称。

    与 S_ROLE_FULLNAME / S_TABLE_ROLE_ABBREV 互补：扫描**全部文档正文**
    （含 §0 权威声明与 Phase B 等 prose），先移除 8 个完整权威名，再检查是否
    残留 Master / Scheduler / Reviewer 歧义简称。
    """
    violations: List[str] = []
    for dk in ALL_DOCS:
        text = doc.get(dk, "")
        s = text
        for name in CANONICAL_ROLES:
            s = s.replace(name, " ")
        for abbr in FORBIDDEN_ROLE_ABBREV:
            if re.search(r"(?<![A-Za-z])" + re.escape(abbr) + r"(?![A-Za-z])", s):
                violations.append(f"{dk}:contains forbidden role abbrev '{abbr}'")
    ok = not violations
    f.append(Finding(
        "S_ROLE_CONTRACT_FULL_NAMES", "ALL", ok,
        message="全部文档正文均使用完整权威角色名，无 Master/Scheduler/Reviewer 歧义简称"
        if ok else "文档正文出现歧义角色简称（未使用完整权威名）",
        found="; ".join(violations[:10]) if violations else "no forbidden abbrev in docs",
    ))


# ---------------------------------------------------------------------------
# 总调度
# ---------------------------------------------------------------------------

def run_checks(doc_contents: Dict[str, str]) -> List[Finding]:
    """对给定的文档内容（键为文档文件名）运行全部规则，返回 Finding 列表。"""
    f: List[Finding] = []
    check_roles(f, doc_contents)
    check_role_fields(f, doc_contents)
    check_events(f, doc_contents)
    check_uar(f, doc_contents)
    check_channels(f, doc_contents)
    check_canvas(f, doc_contents)
    check_acp(f, doc_contents)
    check_mcp(f, doc_contents)
    check_phases(f, doc_contents)
    check_phase_a_def(f, doc_contents)
    check_draft_pr_responsibility(f, doc_contents)
    check_lifecycle(f, doc_contents)
    check_max_rounds(f, doc_contents)
    check_lifecycle_review_on_pr(f, doc_contents)
    check_task_completed_status(f, doc_contents)
    check_final_acceptance_blocked(f, doc_contents)
    check_temp_channel_archive(f, doc_contents)
    check_buzz_gate(f, doc_contents)
    check_pr_order(f, doc_contents)
    check_table_role_abbrev(f, doc_contents)
    check_role_fullname(f, doc_contents)
    check_no_second_truth(f, doc_contents)
    check_protected_repo(f, doc_contents)
    # 第二轮补强：生命周期 / 责任冲突语义（9 条）
    check_deliver_logical_role_unique(f, doc_contents)
    check_release_agent_start_order(f, doc_contents)
    check_release_agent_two_stage_duty(f, doc_contents)
    check_canonical_lifecycle_complete(f, doc_contents)
    check_phase_b_lifecycle_complete(f, doc_contents)
    check_rework_same_pr(f, doc_contents)
    check_final_acceptance_blocking(f, doc_contents)
    check_role_contract_full_names(f, doc_contents)
    return f


def summarize(findings: List[Finding]) -> Dict[str, object]:
    total = len(findings)
    passed = sum(1 for x in findings if x.ok)
    failed = total - passed
    by_rule: Dict[str, Dict[str, int]] = {}
    for x in findings:
        r = by_rule.setdefault(x.rule_id, {"pass": 0, "fail": 0})
        r["pass" if x.ok else "fail"] += 1
    return {
        "total_rules": total,
        "passed": passed,
        "failed": failed,
        "ok": failed == 0,
        "by_rule": by_rule,
        "failures": [x.as_dict() for x in findings if not x.ok],
    }


def _print_human(summary: Dict[str, object], findings: List[Finding], docs: List[str]) -> None:
    print("=" * 72)
    print("Collaboration V1 架构文档语义一致性检查器")
    print("=" * 72)
    print(f"被检文档: {', '.join(docs)}")
    print(f"规则总数: {summary['total_rules']}  通过: {summary['passed']}  失败: {summary['failed']}")
    print("-" * 72)
    fails = [x for x in findings if not x.ok]
    if fails:
        print("失败规则:")
        for x in fails:
            print(f"  [FAIL] {x.rule_id}  @ {x.doc}")
            print(f"         {x.message}")
            print(f"         实际发现: {x.found}")
    else:
        print("所有规则通过 ✅")
    print("-" * 72)
    print(f"结论: {'PASS — 文档语义一致' if summary['ok'] else 'FAIL — 存在语义/标识符冲突'}")
    print(f"退出码: {0 if summary['ok'] else 1}")
    print("=" * 72)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Collaboration V1 架构文档语义一致性检查器")
    parser.add_argument("--docs-dir", default="docs/architecture",
                        help="包含 5 份架构文档的目录（默认 docs/architecture）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结构化摘要")
    parser.add_argument("--quiet", action="store_true", help="仅输出结论与失败项")
    args = parser.parse_args(argv)

    doc_contents: Dict[str, str] = {}
    missing: List[str] = []
    for dk in ALL_DOCS:
        # 路径安全：仅读取 docs_dir 内固定文件名，防穿越 / 符号链接越界。
        try:
            path = _safe_doc_path(args.docs_dir, dk)
        except ValueError as exc:
            if args.json:
                print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
            else:
                print("ERROR: " + str(exc))
            return 2
        if not os.path.isfile(path):
            missing.append(path)
            continue
        with open(path, "r", encoding="utf-8") as fh:
            doc_contents[dk] = fh.read()
    if missing:
        msg = f"缺少文档文件: {missing}"
        if args.json:
            print(json.dumps({"ok": False, "error": msg, "missing": missing}, ensure_ascii=False, indent=2))
        else:
            print("ERROR: " + msg)
        return 2

    findings = run_checks(doc_contents)
    summary = summarize(findings)

    if args.json:
        print(json.dumps({"summary": summary, "findings": [x.as_dict() for x in findings]},
                         ensure_ascii=False, indent=2))
    else:
        _print_human(summary, findings, ALL_DOCS)

    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
