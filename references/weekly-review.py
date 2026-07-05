#!/usr/bin/env python3
# A股每周短线标的复盘 v6.13.4
import subprocess, os, json, shutil
from datetime import datetime, timedelta

# 从 GitHub 拉取本周所有短线标的文件
# 读取认证令牌（若仓库改为私有，缺少令牌则回退到公开URL）
token = None
token_path = "/workspace/.github_token"
try:
    with open(token_path, 'r', encoding='utf-8') as f:
        token = f.read().strip()
except (FileNotFoundError, PermissionError):
    pass
github_repo = "https://github.com/lc132/lv.git"
temp_dir = "/tmp/lv_weekly_review"
try:
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
    # 使用 GIT_ASKPASS 安全传递 Token
    import tempfile
    askpass_script = None
    try:
        fd, askpass_script = tempfile.mkstemp(prefix='git_askpass_', suffix='.sh')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write('#!/bin/bash\necho "$GIT_TOKEN"\n')
        os.chmod(askpass_script, 0o700)
        git_env = os.environ.copy()
        git_env['GIT_ASKPASS'] = askpass_script
        git_env['GIT_TOKEN'] = token
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", "main", github_repo, temp_dir],
            capture_output=True, text=True, timeout=30, check=True, env=git_env
        )
    finally:
        if askpass_script and os.path.exists(askpass_script):
            os.remove(askpass_script)
    # 列出所有短线标的文件
    md_files = []
    for f in os.listdir(temp_dir):
        if f.startswith("短线标的_") and f.endswith(".md"):
            md_files.append((f, os.path.join(temp_dir, f)))
    if not md_files:
        log_alert("INFO", "每周复盘", "本周无推荐文件，跳过")
        return
    # 排序按日期
    md_files.sort()
    log_alert("INFO", "每周复盘", f"拉取到 {len(md_files)} 个推荐文件")
    # 汇总统计...
    # ...（完整统计逻辑在复盘任务中执行）
except Exception as e:
    log_alert("WARNING", "每周复盘", f"拉取失败: {str(e)[:100]}")
finally:
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
