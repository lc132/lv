#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lint_commit_msg.py — git commit-msg 钩子：规范提交信息（治理整改#6）

规则: ^(fix|feat|data|docs|chore|refactor|test|build)(\(.+\))?:\s.+
拒绝: 双 v 前缀(vv)、空描述
安装: cp scripts/lint_commit_msg.py .git/hooks/commit-msg && chmod +x .git/hooks/commit-msg
"""
import sys, re
ALLOWED = ("fix", "feat", "data", "docs", "chore", "refactor", "test", "build")
PAT = re.compile(r"^(?:" + "|".join(ALLOWED) + r")(\(.+\))?:\s.+")
msg_file = sys.argv[1] if len(sys.argv) > 1 else "COMMIT_EDITMSG"
try:
    txt = open(msg_file, encoding="utf-8").read()
except OSError:
    sys.exit(0)
first = txt.strip().splitlines()[0] if txt.strip() else ""
if "vv" in first:
    print("❌ 提交信息含双 v 前缀(vv)，请修正为单个 v"); sys.exit(1)
if not PAT.match(first):
    print("❌ 提交信息格式应为: type(scope): 描述")
    print("   允许类型: " + "/".join(ALLOWED)); sys.exit(1)
sys.exit(0)
