#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_version.py — 单一版本号真相源(SSOT)同步器

VERSION 文件是唯一版本来源。本脚本在每次发版时运行，将 VERSION 中的版本号
同步到以下锚点位置（它们不再独立硬编码）：
  - ashare_screener.py   模块 docstring 首行版本标记 / 兜底常量
  - pre-check-version.py 兜底常量
  - SKILL.md              frontmatter description / H1 标题
  - lib/backtest.py       模块头注释 / 兜底常量
  - 策略调整记录.json     在头部插入一条新版本记录（version/date/params/changes）

用法:
  python3 scripts/sync_version.py            # 按 VERSION 同步全部
  python3 scripts/sync_version.py --check     # 仅校验一致性，不写文件
"""
import os
import re
import json
import argparse
import sys
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_PATH = os.path.join(REPO, "VERSION")
_VER_RE = re.compile(r"v\d+\.\d+\.\d+")

# 锚定正则表：(相对仓库根的路径, 正则模式, 描述)
# 每条正则必须恰好命中 1 处，且该命中内必须包含 1 个版本号 token。
ANCHORS = [
    ("ashare_screener.py",
     r"(?m)^(A股每日盘前短线标的智能筛选 )v\d+\.\d+\.\d+",
     "模块 docstring 首行"),
    ("ashare_screener.py",
     r'(return ")v\d+\.\d+\.\d+("  # 兜底版本（与发版时 VERSION 保持一致）)',
     "兜底常量"),
    ("pre-check-version.py",
     r'(?m)^(    return ")v\d+\.\d+\.\d+("$)',
     "兜底常量"),
    ("SKILL.md",
     r"(?m)^(description: A股每日盘前短线标的智能筛选\()v\d+\.\d+\.\d+(\))",
     "frontmatter description"),
    ("SKILL.md",
     r"(?m)^(# A股盘前短线标的筛选 )v\d+\.\d+\.\d+",
     "H1 标题"),
    ("lib/backtest.py",
     r"(?m)^(# A股短线筛选 — 历史回测模块 )v\d+\.\d+\.\d+",
     "模块头注释"),
    ("lib/backtest.py",
     r'(return ")v\d+\.\d+\.\d+("  # 兜底版本（由 sync_version.py 锚定同步）)',
     "兜底常量"),
    ("sunday_industry_pull.py",
     r"(?m)^(周日行业补全拉取 )v\d+\.\d+\.\d+",
     "docstring 首行"),
]


def read_version():
    with open(VERSION_PATH, "r", encoding="utf-8") as f:
        return f.read().strip()


def apply_anchor(text, pat, ver, path, desc, check_only):
    """对单个锚点做替换或校验。
    返回 (new_text, already_ok, error)。"""
    rx = re.compile(pat)
    hits = list(rx.finditer(text))
    if len(hits) != 1:
        return text, False, f"{path} [{desc}] 锚点命中 {len(hits)} 次（应为 1），锚点已失效"
    match = hits[0]
    cur_match_text = match.group(0)
    cur_ver = _VER_RE.search(cur_match_text)
    if cur_ver is None:
        return text, False, f"{path} [{desc}] 锚点内未找到版本号"
    cur_ver = cur_ver.group(0)
    if cur_ver == ver:
        return text, True, None
    if check_only:
        line_no = text[:match.start()].count("\n") + 1
        return text, False, f"{path}:{line_no} [{desc}] {cur_ver} != VERSION {ver}"
    # 只替换锚点匹配段内的那个版本号
    new_seg = _VER_RE.sub(ver, cur_match_text, count=1)
    return text[:match.start()] + new_seg + text[match.end():], False, None


def sync_adjust_record(ver, changes, check_only):
    """在头部插入新版本记录，使 adj[0] 永远为最新（与 ashare_screener.py 对齐）。"""
    p = os.path.join(REPO, "策略调整记录.json")
    rec = json.load(open(p, encoding="utf-8"))
    if rec and rec[0].get("version") == ver:
        return None
    if check_only:
        cur = rec[0].get("version") if rec else None
        return f"策略调整记录.json[0].version={cur} != VERSION {ver}"
    params = rec[0].get("params", {}) if rec else {}
    rec.insert(0, {"version": ver, "date": date.today().isoformat(),
                   "params": params, "changes": changes})
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="仅校验一致性，不写文件")
    ap.add_argument("--changes", nargs="*",
                    default=["版本同步：由 sync_version.py 从 VERSION 写入"],
                    help="追加到策略调整记录的变更说明")
    args = ap.parse_args()

    ver = read_version()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", ver):
        print(f"❌ VERSION 格式非法: {ver!r}")
        sys.exit(1)
    print("VERSION =", ver)

    errors = []
    changed_files = []
    by_file = {}
    for rel, pat, desc in ANCHORS:
        by_file.setdefault(rel, []).append((pat, desc))

    for rel, items in by_file.items():
        p = os.path.join(REPO, rel)
        with open(p, encoding="utf-8") as f:
            text = f.read()
        orig = text
        for pat, desc in items:
            text, ok, err = apply_anchor(text, pat, ver, rel, desc, args.check)
            if err:
                errors.append(err)
        if not args.check and text != orig:
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            changed_files.append(rel)

    err = sync_adjust_record(ver, args.changes, args.check)
    if err:
        errors.append(err)

    if errors:
        for e in errors:
            print("❌ " + e)
        sys.exit(1)

    if args.check:
        print("✅ 所有版本锚点与 VERSION 一致")
    else:
        print("✅ 已同步: " + (", ".join(changed_files) if changed_files else "无变更"))
    sys.exit(0)


if __name__ == "__main__":
    main()
