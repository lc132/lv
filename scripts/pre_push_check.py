#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pre_push_check.py — 发版前轻量质量门禁（治理整改#5）

检查项:
  1) 版本号一致性: scripts/sync_version.py --check (VERSION 与 SKILL.md 一致)
  2) 语法编译: python -m py_compile ashare_screener.py
  3) 静态检查: 关键函数/常量已定义（文本扫描，避免重导入 lib 依赖）
用法:
  python3 scripts/pre_push_check.py
退出码: 0 通过 / 1 未通过
"""
import os, sys, subprocess, re
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def main():
    ok = True
    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "sync_version.py"), "--check"],
                       capture_output=True, text=True)
    out = (r.stdout or "").strip()
    if out:
        print(out)
    if r.returncode != 0:
        ok = False
    r2 = subprocess.run([sys.executable, "-m", "py_compile", os.path.join(REPO, "ashare_screener.py")],
                        capture_output=True, text=True)
    if r2.returncode != 0:
        print("py_compile 失败: " + r2.stderr)
        ok = False
    else:
        print("py_compile OK")
    try:
        src = open(os.path.join(REPO, "ashare_screener.py"), encoding="utf-8").read()
        required = ("def step0B_sync_industry_cache", "def _load_industry_cache",
                    "def lookup_industry", "def _is_valid_cache_file",
                    "BOT_AUTHOR_NAME", "BOT_AUTHOR_EMAIL")
        missing = [s for s in required if s not in src]
        assert not missing, "缺少定义: " + ", ".join(missing)
        ver = open(os.path.join(REPO, "VERSION")).read().strip()
        assert re.search(r"BUILTIN_VERSION\s*=\s*_load_builtin_version\(\)", src), \
            "BUILTIN_VERSION 未改为运行时读取 VERSION"
        print(f"静态检查 OK, VERSION={ver}, 关键定义齐全")
    except Exception as e:
        print("静态检查失败: " + str(e))
        ok = False
    print("✅ 质量门禁通过" if ok else "❌ 质量门禁未通过")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
