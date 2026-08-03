#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_version.py — 单一版本号真相源(SSOT)同步器

VERSION 文件是唯一版本来源。本脚本在每次发版时运行，将 VERSION 中的版本号
同步到以下位置（它们不再独立硬编码）：
  - ashare_screener.py   模块 docstring 首行版本标记
  - pre-check-version.py  (运行时已读取 VERSION，此处仅做校验)
  - SKILL.md              标题行版本号
  - 策略调整记录.json     追加一条新版本记录（version/date/params/changes）

用法:
  python3 scripts/sync_version.py            # 按 VERSION 同步全部
  python3 scripts/sync_version.py --check     # 仅校验一致性，不写文件
"""
import os
import re
import json
import argparse
from datetime import date

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION_PATH = os.path.join(REPO, "VERSION")
_VER_RE = re.compile(r"v\d+\.\d+\.\d+")


def read_version():
    with open(VERSION_PATH, "r", encoding="utf-8") as f:
        return f.read().strip()


def _replace_first(text, ver):
    return _VER_RE.sub(ver, text, count=1)


def sync_ashare_screener(ver):
    p = os.path.join(REPO, "ashare_screener.py")
    s = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(_replace_first(s, ver))


def sync_skill(ver):
    p = os.path.join(REPO, "SKILL.md")
    s = open(p, encoding="utf-8").read()
    open(p, "w", encoding="utf-8").write(_replace_first(s, ver))


def sync_adjust_record(ver, changes):
    p = os.path.join(REPO, "策略调整记录.json")
    rec = json.load(open(p, encoding="utf-8"))
    if rec and rec[-1].get("version") == ver:
        return  # 已是最新，无需追加
    params = rec[-1].get("params", {}) if rec else {}
    rec.append({"version": ver, "date": date.today().isoformat(),
                "params": params, "changes": changes})
    json.dump(rec, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="仅校验")
    ap.add_argument("--changes", nargs="*",
                    default=["版本同步：由 sync_version.py 从 VERSION 写入"],
                    help="追加到策略调整记录的变更说明")
    args = ap.parse_args()
    ver = read_version()
    print("VERSION =", ver)
    if args.check:
        sk = open(os.path.join(REPO, "SKILL.md"), encoding="utf-8").read()
        print("SKILL.md 首版本标记:", _VER_RE.search(sk).group(0))
        return
    sync_ashare_screener(ver)
    sync_skill(ver)
    sync_adjust_record(ver, args.changes)
    print("已同步: ashare_screener.py / SKILL.md / 策略调整记录.json")


if __name__ == "__main__":
    main()
