#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lint_commit_msg.py — git commit-msg 钩子：规范提交信息（治理整改#6 / P0 Task 1）

统一委托 scripts/commit_gate.py::validate_commit_message，确保与 CI 自动 commit、
脚本内自动提交、push_p0.sh 共用同一套门禁规则（双 v 拒绝 + 格式 + 版本单调性）。

安装: cp scripts/lint_commit_msg.py .git/hooks/commit-msg && chmod +x .git/hooks/commit-msg
"""
import os
import sys

# 解析仓库根（本钩子位于 <repo>/.git/hooks/commit-msg）
_HERE = os.path.abspath(__file__)
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
sys.path.insert(0, os.path.join(_REPO, "scripts"))

try:
    import commit_gate as cg
except ImportError:
    # 兜底：直接按路径加载（兼容 hook 被复制到其他位置）
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "commit_gate", os.path.join(_REPO, "scripts", "commit_gate.py"))
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        cg = _mod
    except Exception:
        # 门禁脚本缺失则放行（不阻断正常提交），避免基础设施问题阻塞流水线
        sys.exit(0)

msg_file = sys.argv[1] if len(sys.argv) > 1 else "COMMIT_EDITMSG"
try:
    txt = open(msg_file, encoding="utf-8").read()
except OSError:
    sys.exit(0)
first = txt.strip().splitlines()[0] if txt.strip() else ""
ok, reason = cg.validate_commit_message(first)
if not ok:
    print("❌ " + reason, file=sys.stderr)
    sys.exit(1)
sys.exit(0)
