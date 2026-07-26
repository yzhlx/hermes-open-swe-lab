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
LIFECYCLE_ANCHORS_ORDER = [
    "issue",
    "plan",
    "code",
    "commit",
    "draft_pr",
    "ci",
    "review",
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
        return {
            "rule_id": self.rule_id,
            "doc": self.doc,
            "ok": self.ok,
            "severity": self.severity,
            "message": self.message,
            "found": self.found,
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


def check_draft_pr_responsibility(f: List[Finding], doc: Dict[str, str]) -> None:
    role_text = doc.get(DOC_ROLE, "")
    responsible: List[str] = []
    release_ok = True
    for role_name, body in iter_role_subsections(role_text):
        if "Draft PR" in body and ("唯一负责方" in body or "唯一负责" in body):
            responsible.append(role_name)
        if role_name == "Release Agent":
            # Release Agent 不得重复声明"创建 Draft PR"
            if "创建 Draft PR" in body and "不得" not in body:
                release_ok = False
    ok = (responsible == ["Coding Worker"]) and release_ok
    f.append(
        Finding(
            "S_DRAFT_PR_RESP",
            DOC_ROLE,
            ok,
            message="Draft PR 创建唯一责任方为 DeliveryController.deliver()（Coding Worker 触发，Release Agent 不重复声明）"
            if ok else "Draft PR 责任归属冲突或 Release Agent 重复声明创建",
            found=f"roles_claiming_unique_responsibility={responsible}; release_disclaims={release_ok}",
        )
    )


def _extract_lifecycle(text: str) -> Optional[str]:
    # 优先从 ACTIVITY §1.2.1，其次 ROADMAP Phase F
    for src in (text,):
        m = re.search(r"Issue\s*→.*?TASK_COMPLETED", src, re.S)
        if m:
            return m.group(0)
    return None


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
    check_task_completed_status(f, doc_contents)
    check_final_acceptance_blocked(f, doc_contents)
    check_temp_channel_archive(f, doc_contents)
    check_buzz_gate(f, doc_contents)
    check_pr_order(f, doc_contents)
    check_table_role_abbrev(f, doc_contents)
    check_no_second_truth(f, doc_contents)
    check_protected_repo(f, doc_contents)
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
        path = os.path.join(args.docs_dir, dk)
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
