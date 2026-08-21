#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_version.py — 单一版本号真相源(SSOT)同步器

VERSION 文件是唯一版本来源。本脚本在每次发版时运行，将 VERSION 中的版本号
同步到以下锚点位置（它们不再独立硬编码）：
  - ashare_screener.py   模块 docstring 首行版本标记 / 兜底常量
  - pre-check-version.py 兜底常量
  - SKILL.md              frontmatter description / H1 标题 / ## 版本历史 最新条目
  - lib/backtest.py       模块头注释 / 兜底常量
  - _meta.json            version 字段（对外发布展示版本，曾长期失同步）
  - 策略调整记录.json     在头部插入一条新版本记录（version/date/params/changes）

SKILL.md 的「## 版本历史」同步：确保该章节最新一条 == VERSION。缺失时从
策略调整记录.json 回填（仅插入 > 当前最新且 <= VERSION 的条目，按记录顺序），
幂等：已存在则跳过。这是发版（含自动整改）必须同步的三处 SKILL.md 声明点之一。

用法:
  python3 scripts/sync_version.py            # 按 VERSION 同步全部（含 SKILL.md 版本历史）
  python3 scripts/sync_version.py --check     # 仅校验一致性，不写文件
  python3 scripts/sync_version.py --changes "..." --params '{"k":2}'  # 指定变更说明/参数
"""
import os
import re
import json
import argparse
import sys
import subprocess
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
    ("_meta.json",
     r'(?m)("version":\s*")v\d+\.\d+\.\d+(")',
     "version 字段（对外发布展示版本）"),
]


def read_version():
    with open(VERSION_PATH, "r", encoding="utf-8") as f:
        return f.read().strip()


def _ver_tuple(v):
    """'v6.22.11' -> (6, 22, 11)，非法返回 None。"""
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)$", v or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def _find_section(text, header):
    """定位 Markdown 二级章节。返回 (区块起点索引[含 header 行后的换行], 区块文本, 区块终点索引)。
    找不到返回 (None, None, None)。区块 = header 行到下一个 # 标题之间的内容。"""
    m = re.search(r"^" + re.escape(header) + r"\s*$", text, re.M)
    if not m:
        return None, None, None
    start = m.end()  # header 行 '\n' 之后
    rest = text[start:]
    nxt = re.search(r"^#{1,3} ", rest, re.M)
    end = start + nxt.start() if nxt else len(text)
    return start, text[start:end], end


def sync_skill_history(ver, changes, check_only, params=None):
    """SKILL.md 「## 版本历史」同步：确保最新一条 == VERSION。

    - 已为 VERSION：幂等跳过。
    - 缺失：从 策略调整记录.json 回填（仅插入版本号 > 当前最新 且 <= VERSION 的条目，
      按记录顺序 newest-first），保证不污染既有历史、不重复。
    - --check 模式：仅校验，不一致返回错误字符串。
    """
    p = os.path.join(REPO, "SKILL.md")
    if not os.path.exists(p):
        return f"SKILL.md 不存在"
    text = open(p, encoding="utf-8").read()
    s_start, block, _ = _find_section(text, "## 版本历史")
    if s_start is None:
        return f"SKILL.md 未找到 '## 版本历史' 章节"
    bm = re.search(r"^- \*\*v(\d+\.\d+\.\d+)\*\*", block, re.M)
    top_ver = ("v" + bm.group(1)) if bm else None
    if top_ver == ver:
        return None  # 已同步，幂等

    if check_only:
        if top_ver is None:
            return f"SKILL.md 版本历史为空，缺 VERSION {ver} 条目"
        return f"SKILL.md 版本历史最新={top_ver} != VERSION {ver}"

    # --- 写模式：构建待插入条目 ---
    new_entries = []
    try:
        rec = json.load(open(os.path.join(REPO, "策略调整记录.json"), encoding="utf-8"))
    except Exception:
        rec = []
    vt = _ver_tuple(ver)
    ttop = _ver_tuple(top_ver) if top_ver else None
    for r in rec:
        rv = r.get("version", "")
        if not rv:
            continue
        rvt = _ver_tuple(rv)
        if rvt is None:
            continue
        if ttop is not None and rvt <= ttop:
            continue  # 已有/更旧的条目不回填
        if rvt > vt:
            continue  # 超过当前 VERSION 的条目不插入
        desc = (r.get("changes") or [""])[0] if r.get("changes") else ""
        new_entries.append(f"- **{rv}**: {desc}")
    # 兜底：记录里没有当前版本(异常)时用 --changes 补一条
    if not any(ver in e for e in new_entries):
        summary = "; ".join(changes) if changes else "版本同步：由 sync_version.py 从 VERSION 写入"
        new_entries.append(f"- **{ver}**: {summary}")

    insert_block = "\n".join(new_entries)
    new_text = text[:s_start].rstrip("\n") + "\n" + insert_block + "\n\n" + text[s_start:].lstrip("\n")
    open(p, "w", encoding="utf-8").write(new_text)
    return None


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


def sync_adjust_record(ver, changes, check_only, params=None):
    """在头部插入新版本记录，使 adj[0] 永远为最新（与 ashare_screener.py 对齐）。
    params: 指定本版本实际变更的参数字典（自动整改传入）；None 时回退为上一版本 params。
    """
    p = os.path.join(REPO, "策略调整记录.json")
    rec = json.load(open(p, encoding="utf-8"))
    if rec and rec[0].get("version") == ver:
        return None
    if check_only:
        cur = rec[0].get("version") if rec else None
        return f"策略调整记录.json[0].version={cur} != VERSION {ver}"
    eff_params = params if params is not None else (rec[0].get("params", {}) if rec else {})
    rec.insert(0, {"version": ver, "date": date.today().isoformat(),
                   "params": eff_params, "changes": changes})
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return None


def create_release_tag(ver):
    """发版锚点（P1-2）：在 REPO 仓库创建并推送 vX.Y.Z 标签，保证可回滚到确定版本。

    幂等：标签已存在则跳过。打标签失败（如无推送权限/CI 环境）仅告警，不影响版本同步。
    """
    tag = ver if ver.startswith("v") else "v" + ver
    try:
        have = subprocess.run(["git", "-C", REPO, "tag", "-l", tag],
                              capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception:
        have = ""
    if have == tag:
        print(f"⏭️ 标签 {tag} 已存在，跳过")
        return
    try:
        subprocess.run(["git", "-C", REPO, "tag", "-a", tag, "-m", f"Release {tag}"],
                       check=True, capture_output=True, text=True, timeout=15)
        subprocess.run(["git", "-C", REPO, "push", "origin", tag],
                       check=True, capture_output=True, text=True, timeout=30)
        print(f"✅ 已创建并推送发版标签 {tag}")
    except Exception as e:
        print(f"⚠️ 创建/推送标签 {tag} 失败（不影响版本同步，可稍后手动 git push origin {tag}）: {str(e)[:120]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="仅校验一致性，不写文件")
    ap.add_argument("--release", action="store_true",
                    help="同步版本锚点后，创建并推送 vX.Y.Z 发版标签（P1-2 发版流程）")
    ap.add_argument("--tag-only", action="store_true",
                    help="仅创建并推送发版标签（不重新同步版本锚点）")
    ap.add_argument("--changes", nargs="*",
                    default=["版本同步：由 sync_version.py 从 VERSION 写入"],
                    help="追加到策略调整记录/SKILL.md版本历史的变更说明")
    ap.add_argument("--params", default=None,
                    help="本版本实际变更的参数(JSON字符串)，如 '{\"k\":2}'；"
                         "不传则回退为上一版本 params（手动发版场景）")
    args = ap.parse_args()

    ver = read_version()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", ver):
        print(f"❌ VERSION 格式非法: {ver!r}")
        sys.exit(1)
    print("VERSION =", ver)

    if args.tag_only:
        create_release_tag(ver)
        sys.exit(0)

    params = None
    if args.params:
        try:
            params = json.loads(args.params)
        except Exception as e:
            print(f"❌ --params JSON 解析失败: {e}")
            sys.exit(1)

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

    # SKILL.md 版本历史同步（写/校验，发版必同步点之一）
    err = sync_skill_history(ver, args.changes, args.check)
    if err:
        errors.append(err)
    elif not args.check:
        changed_files.append("SKILL.md(版本历史)")

    err = sync_adjust_record(ver, args.changes, args.check, params)
    if err:
        errors.append(err)

    if errors:
        for e in errors:
            print("❌ " + e)
        sys.exit(1)

    if args.check:
        print("✅ 所有版本锚点(含 SKILL.md 版本历史)与 VERSION 一致")
    else:
        print("✅ 已同步: " + (", ".join(sorted(set(changed_files))) if changed_files else "无变更"))

    if args.release:
        create_release_tag(ver)
    sys.exit(0)


if __name__ == "__main__":
    main()
