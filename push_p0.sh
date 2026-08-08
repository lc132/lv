#!/usr/bin/env bash
# ============================================================================
# push_p0.sh — 将 P0 整改原子推送到 lc132/lv (main)
#
# PO-2 整改：终结逐文件提交（曾一次变更拆 6-8 条同 message 提交，累计 37 条重复，
#   污染历史/revert 失效/放大 CI 失败观感/触发 65 次 Pages cancelled）。
# P2-4: 主仓 main 已加「Require PR」保护，代码禁止直推
#   → 原子提交落到 feature 分支并开 PR（带变更动机 body），由维护者自评审后合并。
#   blobs -> tree(base_tree+变更项) -> commit(统一身份) -> 建 feature 分支 -> 开 PR
# 所有变更文件合并为单条提交，幂等跳过未变更文件。
# 数据类（行业缓存/推荐历史→lv-data、制品→gh-pages）由 sunday_industry_pull.py / step26 直推，不经本脚本。
#
# 用法:  GITHUB_TOKEN=xxx bash push_p0.sh ["自定义提交信息"]
#        （或已授权环境直接 bash push_p0.sh）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 0) 幂等锁 (PO-2): 防并发重入, 杜绝重复提交
LOCK="/tmp/push_p0.lock"
if [ -e "$LOCK" ]; then
  OLD_PID="$(cat "$LOCK" 2>/dev/null || true)"
  if [ -n "${OLD_PID:-}" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "ℹ️ 已有 push_p0.sh 运行中 (PID $OLD_PID)，本次退出以避免重复提交"; exit 0
  fi
  echo "⚠️ 发现过期锁 (PID ${OLD_PID:-?} 已不在)，清理后继续"; rm -f "$LOCK"
fi
echo "$$" > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

# 1) token
if [ -n "${GITHUB_TOKEN:-}" ]; then
  echo "ℹ️ 使用环境中已有的 GITHUB_TOKEN（长度 ${#GITHUB_TOKEN}）"
else
  TOKEN_SH=/root/.codebuddy/skills/github-connector/scripts/get_token.sh
  # shellcheck disable=SC1090
  source "$TOKEN_SH" github
  if [ -z "${GITHUB_TOKEN:-}" ]; then
    echo "❌ 未获取到 GITHUB_TOKEN" >&2; exit 1
  fi
fi

OWNER=lc132; REPO=lv; BRANCH=main
API="https://api.kkgithub.com/repos/$OWNER/$REPO"

# 2) 提交信息（必须过 commit_gate：type: 前缀 / 无双 v / 版本单调）
MSG="${1:-chore: P0整改 原子提交(SSOT锚定同步/质量门禁/原子push)}"
# P2-4: 第2参数作为 PR body（变更动机）；缺省时脚本基于 MSG+变更文件自动生成
BODY="${2:-}"

echo "=== 预检: 运行质量门禁 ==="
python3 "$SCRIPT_DIR/scripts/pre_push_check.py" >/dev/null 2>&1 || { echo "❌ 门禁未通过"; exit 1; }
echo "✅ 门禁通过"

echo "=== 提交信息门禁(commit_gate) ==="
python3 "$SCRIPT_DIR/scripts/commit_gate.py" "$MSG" || { echo "❌ 提交信息门禁未过，中止"; exit 1; }
echo "✅ 提交信息通过门禁"

# 3) 原子提交主体：blobs -> tree -> commit -> 建 feature 分支 -> 开 PR（P2-4）
python3 - "$SCRIPT_DIR" "$MSG" "$BRANCH" "$BODY" <<'PY'
import sys, os, json, base64, hashlib, requests
from datetime import datetime, timezone

SCRIPT_DIR, MSG, BRANCH, BODY = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
TOK = os.environ["GITHUB_TOKEN"]
API = "https://api.kkgithub.com/repos/lc132/lv"
H = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}

# 纳入原子提交的文件（治理整改批次统一原子推送清单）
FILES = [
    "scripts/commit_gate.py", "scripts/lint_commit_msg.py", "scripts/sync_version.py",
    "scripts/pre_push_check.py", "pre-check-version.py", "lib/backtest.py", "lib/sync.py",
    "lib/runtime.py", "lib/__init__.py", ".github/workflows/quality-gate.yml",
    "ashare_screener.py", "sunday_industry_pull.py", "SKILL.md", "VERSION",
    "策略调整记录.json", "_meta.json", "push_p0.sh", ".gitignore",
    "hooks/commit-msg", "hooks/pre-commit",
]

# base commit / tree
ref = requests.get(f"{API}/git/ref/heads/{BRANCH}", headers=H, timeout=30).json()
base_commit = ref["object"]["sha"]
base_tree = requests.get(f"{API}/git/commits/{base_commit}", headers=H, timeout=30).json()["tree"]["sha"]

# 远端当前树 path->sha（幂等跳过未变更文件）
rt = requests.get(f"{API}/git/trees/{base_tree}?recursive=1", headers=H, timeout=30).json()
remote_sha = {t["path"]: t["sha"] for t in rt.get("tree", []) if t["type"] == "blob"}

def local_blob_sha(data):
    return hashlib.sha1(("blob %d\0" % len(data)).encode() + data).hexdigest()

entries, changed = [], []
for f in FILES:
    p = os.path.join(SCRIPT_DIR, f)
    if not os.path.exists(p):
        print(f"  ⏭️ 本地缺失，跳过: {f}")
        continue
    data = open(p, "rb").read()
    if remote_sha.get(f) == local_blob_sha(data):
        continue  # 与远端一致 -> 不计入本次原子提交
    r = requests.post(f"{API}/git/blobs", headers=H,
                      data=json.dumps({"content": base64.b64encode(data).decode(), "encoding": "base64"}),
                      timeout=60)
    if r.status_code != 201:
        print(f"  ❌ blob 失败 {f}: {r.status_code} {r.text[:120]}"); sys.exit(1)
    mode = "100755" if f.endswith(".sh") or f.startswith("hooks/") else "100644"
    entries.append({"path": f, "mode": mode, "type": "blob", "sha": r.json()["sha"]})
    changed.append(f)

if not entries:
    print("⏭️ 无变更文件，跳过提交")
    sys.exit(0)

# tree（以 base_tree 为基底，仅叠加变更项）
t = requests.post(f"{API}/git/trees", headers=H,
                  data=json.dumps({"base_tree": base_tree, "tree": entries}), timeout=60)
if t.status_code != 201:
    print(f"  ❌ tree 失败: {t.status_code} {t.text[:200]}"); sys.exit(1)
tree_sha = t.json()["sha"]

# commit（统一机器人身份，巩固 P0-4）
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
author = {"name": "ashare-screener", "email": "72593777+ashare-screener@users.noreply.github.com", "date": now}  # @since P0-1: GitHub 可识别 noreply 格式
c = requests.post(f"{API}/git/commits", headers=H,
                  data=json.dumps({"message": MSG, "tree": tree_sha,
                                   "parents": [base_commit], "author": author, "committer": author}),
                  timeout=60)
if c.status_code != 201:
    print(f"  ❌ commit 失败: {c.status_code} {c.text[:200]}"); sys.exit(1)
commit_sha = c.json()["sha"]

# P2-4: 主仓 main 已加「Require PR」保护，代码禁止直推。
# 改为：提交落到 feature 分支 + 开 PR（带变更动机 body），由维护者自评审后合并。
ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
branch = f"bot/auto-{ts}"
r = requests.post(f"{API}/git/refs", headers=H,
                  data=json.dumps({"ref": f"refs/heads/{branch}", "sha": commit_sha}), timeout=60)
if r.status_code not in (200, 201):
    print(f"  ❌ 分支创建失败 {branch}: {r.status_code} {r.text[:200]}"); sys.exit(1)
print(f"✅ 分支 {branch} 已创建（提交 {commit_sha[:10]}，{len(changed)} 文件）")

body = BODY or (MSG + "\n\n## 变更文件\n" + "\n".join(f"- {f}" for f in changed))
pr = requests.post(f"{API}/pulls", headers=H,
                   data=json.dumps({"title": MSG, "head": branch, "base": "main", "body": body}), timeout=60)
if pr.status_code in (200, 201):
    pr_url = pr.json().get("html_url", "")
    print(f"✅ 已开 PR（待自评审合并）: {pr_url}")
    print(f"   变更文件: {len(changed)} 个")
    for f in changed:
        print(f"   - {f}")
else:
    print(f"  ⚠️ PR 创建失败 {pr.status_code} {r.text[:200]}")
    print(f"  分支已就绪，请手动开 PR: https://github.com/lc132/lv/pull/new/{branch}")
    sys.exit(1)
PY

echo "查看: https://github.com/lc132/lv/pulls"
