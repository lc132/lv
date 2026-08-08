#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pre_push_check.py — 发版前轻量质量门禁（治理整改#5 / P0-3）

检查项:
  1) 版本号一致性: scripts/sync_version.py --check (VERSION 与 SKILL.md 等下游一致)
  2) 语法编译: py_compile 覆盖 ashare_screener.py / lib/**/*.py / scripts/*.py / pre-check-version.py
  3) 静态检查: 关键函数/常量已定义（文本扫描，避免重导入 lib 依赖）
  4) 版本单调性(可选): 新 VERSION >= baseline (可选 --require-bump 要求严格 >)
  5) 提交信息检查(可选): 拒绝双 v、拒绝主题版本 < 当前 VERSION（[version-exempt] 放行）
  6) 提交者身份白名单(可选): baseline..HEAD 的 author email 必须 ∈ 统一身份白名单（P0-4）

用法:
  python3 scripts/pre_push_check.py
  python3 scripts/pre_push_check.py --baseline-ref origin/main --require-bump
退出码: 0 通过 / 1 未通过
"""
import os, sys, subprocess, re, glob, argparse

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def _vt(ver):
    """'v6.20.6' -> (6, 20, 6)，用于版本号元组比较；非法返回 None。"""
    m = re.match(r"^v(\d+)\.(\d+)\.(\d+)$", ver or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def _git(args):
    """在 REPO 内执行 git，返回 (returncode, stdout)。非 git 环境返回 (1, '')。"""
    try:
        r = subprocess.run(["git", "-C", REPO] + args,
                           capture_output=True, text=True, timeout=30)
        return r.returncode, (r.stdout or "")
    except Exception:
        return 1, ""


def _current_version():
    return open(os.path.join(REPO, "VERSION"), encoding="utf-8").read().strip()


# 统一提交者身份白名单（P0-4）
# 仅允许以下「可关联 GitHub」的真实身份。下一轮巡检提交者身份数须 ≤ 2。
# 机器人已统一为 GitHub noreply 格式（P0-1），旧 ashare-bot@github.com 无法关联账户→author=null，
# 仅保留用于兼容历史提交（不阻断门禁）；新提交一律使用 noreply 格式。
ALLOWED_AUTHOR_EMAILS = {
    "72593777+ashare-screener@users.noreply.github.com",  # 机器人 ashare-screener（P0-1 现行）
    "72593777+lc132@users.noreply.github.com",            # 人类维护者 lc132
    "ashare-bot@github.com",                             # 历史兼容（P0-1 前机器人邮箱，不关联账户）
}


def _check_sync_version():
    ok = True
    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "sync_version.py"), "--check"],
                       capture_output=True, text=True)
    out = (r.stdout or "").strip()
    if out:
        print(out)
    if r.returncode != 0:
        ok = False
    return ok


def _check_py_compile():
    ok = True
    targets = [os.path.join(REPO, "ashare_screener.py"),
               os.path.join(REPO, "pre-check-version.py")]
    targets += glob.glob(os.path.join(REPO, "lib", "**", "*.py"), recursive=True)
    targets += glob.glob(os.path.join(REPO, "scripts", "*.py"))
    failed = []
    for t in sorted(set(targets)):
        r = subprocess.run([sys.executable, "-m", "py_compile", t],
                           capture_output=True, text=True)
        if r.returncode != 0:
            failed.append((t, r.stderr.strip()))
    if failed:
        for t, err in failed:
            print(f"py_compile 失败 {os.path.relpath(t, REPO)}:\n{err}")
        ok = False
    else:
        print(f"py_compile OK ({len(set(targets))} 个文件)")
    return ok


def _check_static():
    ok = True
    try:
        src = open(os.path.join(REPO, "ashare_screener.py"), encoding="utf-8").read()
        required = ("def step0B_sync_industry_cache", "def _load_industry_cache",
                    "def lookup_industry", "def _is_valid_cache_file",
                    "BOT_AUTHOR_NAME", "BOT_AUTHOR_EMAIL")
        missing = [s for s in required if s not in src]
        assert not missing, "缺少定义: " + ", ".join(missing)
        assert re.search(r"BUILTIN_VERSION\s*=\s*_load_builtin_version\(\)", src), \
            "BUILTIN_VERSION 未改为运行时读取 VERSION"
        print("静态检查 OK, 关键定义齐全")
    except Exception as e:
        print("静态检查失败: " + str(e))
        ok = False
    return ok


def check_version_monotonic(baseline_ref, require_bump):
    """新 VERSION 必须 >= baseline（防止回退）；--require-bump 时要求严格 >。"""
    if not baseline_ref:
        print("⚠ 未指定 --baseline-ref，跳过版本单调性检查")
        return True
    rc, out = _git(["show", f"{baseline_ref}:VERSION"])
    if rc != 0 or not out.strip():
        print(f"⚠ 无法读取 baseline({baseline_ref}) 的 VERSION，跳过单调性检查")
        return True
    base = out.strip()
    new = _current_version()
    vt_new, vt_base = _vt(new), _vt(base)
    if vt_new is None or vt_base is None:
        print(f"⚠ 版本号格式异常 new={new} base={base}，跳过单调性检查")
        return True
    ver_ok = vt_new >= vt_base
    if require_bump:
        ver_ok = ver_ok and (vt_new > vt_base)
    if not ver_ok:
        op = ">=" if not require_bump else ">"
        print(f"❌ 版本单调性失败: 新 VERSION {new} 需 {op} baseline {base}"
              + (" (且必须 >，因 --require-bump)" if require_bump else ""))
        return False
    print(f"版本单调性 OK: {new} >= {base}" + (" (已 bump)" if require_bump else ""))
    return True


def _load_commit_gate():
    """加载 commit_gate 模块（SSOT）。优先 scripts 包导入，失败按路径加载。"""
    try:
        import scripts.commit_gate as cg
        return cg
    except ImportError:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "commit_gate", os.path.join(REPO, "scripts", "commit_gate.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        return _mod


def check_single_commit_message(msg, current_version=None):
    """校验单条提交信息（复用 commit_gate SSOT）。返回 (ok, reason)。
    供 CI 自动 commit、push_p0.sh 等单条场景调用。"""
    cg = _load_commit_gate()
    return cg.validate_commit_message(msg, current_version=current_version)


def check_commit_messages(baseline_ref):
    """检查 baseline..HEAD 的提交主题：拒绝双 v、拒绝格式不符、拒绝版本回退。
    P0 Task 1: 统一委托 commit_gate.validate_commit_message（与 commit-msg 钩子同源）。"""
    if not baseline_ref:
        print("⚠ 未指定 --baseline-ref，跳过提交信息检查")
        return True
    rc, out = _git(["log", f"{baseline_ref}..HEAD", "--pretty=%s"])
    if rc != 0:
        print(f"⚠ 无法读取 {baseline_ref}..HEAD 提交，跳过提交信息检查")
        return True
    subjects = [s for s in out.splitlines() if s.strip()]
    if not subjects:
        print("⚠ baseline..HEAD 无新提交，跳过提交信息检查")
        return True
    cur = _current_version()
    ok = True
    for s in subjects:
        good, reason = check_single_commit_message(s, current_version=cur)
        if not good:
            print(f"❌ 提交信息门禁未过: {reason}  | 主题: {s}")
            ok = False
    if ok:
        print(f"提交信息检查 OK ({len(subjects)} 个提交)")
    return ok


def check_file_version_rollback():
    """文件级版本回落检测（仅针对 SSOT「当前版本」声明点，不误杀历史 @since 注记）。

    P0-2: 覆盖全部 .py 文件含 sunday_industry_pull.py。实现说明：
    - 真正需要拦截的是「当前版本声明」回落到低于基线，该声明仅存在于 8 个 SSOT 锚点
      （sync_version.py::ANCHORS 已强制与 VERSION 一致），故本函数复用 sync_version 锚点校验。
    - .py 文档串/注释中的历史版本号（如 v6.13.38）属「引入版本」记录，按 @since 约定应写为
      `@since v6.13.38`；将其整体判为「回落」会误杀全仓（lib/*.py 多数为历史注记），故不扫描。
    - 提交信息级的版本回落（双 v / 主题版本 < 基线）由 commit_gate 在 commit-msg 钩子、
      CI commit-gate 步骤、以及脚本内自动提交（含 sunday_industry_pull.py）前置门禁中统一拦截。
    """
    # 复用 SSOT 幂等自校验（已覆盖全部锚点文件，含 sunday_industry_pull.py 的 L4/L481）
    return _check_sync_version()


def check_author_email_whitelist(baseline_ref):
    """提交者身份白名单校验（P0-4）。

    扫描 baseline..HEAD 全部提交的 author email，凡 ∉ ALLOWED_AUTHOR_EMAILS 一律 FAIL，
    以收敛残留提交身份，确保「下周巡检提交者身份数 ≤ 2」。
    非 git 环境 / 未指定 baseline_ref / 无新提交时优雅跳过。
    """
    if not baseline_ref:
        print("⚠ 未指定 --baseline-ref，跳过提交者身份白名单校验")
        return True
    rc, out = _git(["log", f"{baseline_ref}..HEAD", "--pretty=%ae"])
    if rc != 0:
        print(f"⚠ 无法读取 {baseline_ref}..HEAD 提交者，跳过身份白名单校验")
        return True
    emails = [e.strip() for e in out.splitlines() if e.strip()]
    if not emails:
        print("⚠ baseline..HEAD 无新提交，跳过提交者身份白名单校验")
        return True
    bad = sorted({e for e in emails if e not in ALLOWED_AUTHOR_EMAILS})
    if bad:
        print(f"❌ 提交者身份白名单未过（非统一身份共 {len(bad)} 个）: {bad}")
        print(f"   允许身份: {sorted(ALLOWED_AUTHOR_EMAILS)}")
        return False
    print(f"提交者身份白名单 OK ({len(set(emails))} 个身份，均合规)")
    return True


def main():
    p = argparse.ArgumentParser(description="发版前质量门禁")
    p.add_argument("--baseline-ref", default=None,
                   help="git ref 用于版本单调性/提交信息比较 (e.g. origin/main)")
    p.add_argument("--require-bump", action="store_true",
                   help="要求新 VERSION 严格大于 baseline")
    args = p.parse_args()

    checks = [
        ("版本一致性", _check_sync_version()),
        ("语法编译", _check_py_compile()),
        ("静态检查", _check_static()),
        ("版本单调性", check_version_monotonic(args.baseline_ref, args.require_bump)),
        ("提交信息", check_commit_messages(args.baseline_ref)),
        (".py 版本回落", check_file_version_rollback()),
        ("提交者身份白名单", check_author_email_whitelist(args.baseline_ref)),
    ]
    ok = all(passed for _, passed in checks)
    print("--- 门禁汇总 ---")
    for name, passed in checks:
        print(f"  [{'OK' if passed else 'FAIL'}] {name}")
    print("✅ 质量门禁通过" if ok else "❌ 质量门禁未通过")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
