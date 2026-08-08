#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
commit_gate.py — 提交信息门禁 SSOT（治理整改#6 / P0 漏洞修复 Task 1）

所有提交入口（git commit-msg 钩子、CI 自动 commit、脚本内自动提交、push_p0.sh）
统一调用 validate_commit_message，确保「双 v(vv)」与「版本回退」被一并拦截，
杜绝自动提交 / kkgithub 推送绕过 quality-gate 门禁。

规则:
  1) 拒绝双 v 前缀: 主题含 'vv'（大小写不敏感），如 (vvX.Y.Z) 双写形式
  2) 格式: ^(fix|feat|data|docs|chore|refactor|test|build)(\(.+\))?:\s.+
  3) 版本单调性: 主题中出现的版本号必须 >= 当前 VERSION（baseline），
     低于基线一律拒绝（防 vv* 与回退版本号入库）。[version-exempt] 放行版本检查。

用法:
  python3 scripts/commit_gate.py "fix: 修复某某"
  退出码 0 通过 / 1 未通过（stderr 打印原因）/ 2 用法错误
"""
import os
import re
import sys

ALLOWED = ("fix", "feat", "data", "docs", "chore", "refactor", "test", "build")
PAT = re.compile(r"^(?:" + "|".join(ALLOWED) + r")(\(.+\))?:\s.+")


def _vt(ver):
    """'v6.20.6' -> (6, 20, 6)，非法返回 None。"""
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)$", ver or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def _repo_root():
    """commit_gate.py 位于 <repo>/scripts/，故 repo 根为上级目录。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_version():
    """读取仓库根 VERSION，缺失返回空串。"""
    p = os.path.join(_repo_root(), "VERSION")
    try:
        return open(p, encoding="utf-8").read().strip()
    except OSError:
        return ""


def validate_commit_message(msg, current_version=None):
    """校验单条提交信息。返回 (ok: bool, reason: str)。

    - msg: 提交信息（可含多行，仅取首行为主题）
    - current_version: 当前 VERSION（如 'v6.20.12'）；为 None 时尝试读取仓库
      VERSION 文件。传 None 且文件缺失 -> 跳过版本单调性检查（不误杀）。
    """
    if not msg or not msg.strip():
        return False, "提交信息为空"
    first = msg.strip().splitlines()[0]

    # [version-exempt] 仅放行版本单调性检查（仍保留格式 / vv 校验）
    exempt = "[version-exempt]" in first

    # 1) 拒绝双 v 前缀
    if "vv" in first.lower():
        return False, "提交信息含双 v 前缀(vv)，请修正为单个 v（如 v6.20.12）"

    # 2) 格式校验
    if not PAT.match(first):
        return False, ("提交信息格式应为: type(scope): 描述\n"
                       "   允许类型: " + "/".join(ALLOWED))

    # 3) 版本单调性（主题版本 >= 当前 VERSION，禁止回退）
    if not exempt:
        cur = _vt(current_version) if current_version else _vt(_read_version())
        if cur is not None:
            for m in re.finditer(r"v(\d+)\.(\d+)\.(\d+)", first):
                vt = tuple(int(x) for x in m.groups())
                if vt < cur:
                    ref = current_version or _read_version() or "基线"
                    return False, (f"提交主题版本 {m.group(0)} < 当前 VERSION {ref}，"
                                   "禁止版本回退（[version-exempt] 可放行）")
    return True, ""


def main():
    if len(sys.argv) < 2:
        print("用法: python3 scripts/commit_gate.py \"<提交信息>\" [<当前版本>]",
              file=sys.stderr)
        sys.exit(2)
    msg = sys.argv[1]
    # 可选的第二个参数：显式传入当前版本（避免依赖文件解析）
    ver = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2].startswith("v") else None
    ok, reason = validate_commit_message(msg, current_version=ver)
    if not ok:
        print("❌ 提交信息未过门禁: " + reason, file=sys.stderr)
        sys.exit(1)
    print("✅ 提交信息通过门禁")
    sys.exit(0)


if __name__ == "__main__":
    main()
