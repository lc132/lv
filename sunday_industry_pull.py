#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周日行业补全拉取 v6.13.6
每周日执行：全量拉取东方财富HTTP行业分类（一级+二级），更新缓存文件并推送到GitHub。
"""
import urllib.request, json, os, time, subprocess, sys, tempfile, shutil

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
if not GITHUB_TOKEN:
    # 尝试从文件读取
    token_file = "/workspace/.github_token"
    try:
        with open(token_file, 'r', encoding='utf-8') as f:
            GITHUB_TOKEN = f.read().strip()
    except (FileNotFoundError, PermissionError):
        pass
if not GITHUB_TOKEN:
    print("ERROR: 未找到GitHub Token，请设置GITHUB_TOKEN环境变量或创建/workspace/.github_token文件")
    sys.exit(1)
GITHUB_REPO = "lc132/lv"
WORK_DIR = "/tmp/sunday_industry_pull"

def _git_with_token(cmd_args, timeout=60, check=True):
    """使用 GIT_ASKPASS 安全传递 Token，避免 Token 出现在进程列表中"""
    askpass_script = None
    try:
        fd, askpass_script = tempfile.mkstemp(prefix='git_askpass_', suffix='.sh')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write('#!/bin/bash\necho "$GIT_TOKEN"\n')
        os.chmod(askpass_script, 0o700)
        env = os.environ.copy()
        env['GIT_ASKPASS'] = askpass_script
        env['GIT_TOKEN'] = GITHUB_TOKEN
        return subprocess.run(cmd_args, capture_output=True, text=True, timeout=timeout, env=env, check=check)
    finally:
        if askpass_script and os.path.exists(askpass_script):
            os.remove(askpass_script)

# 证监会行业 → 申万一级行业映射表（与 ashare_screener.py 保持一致）
_ZJH_TO_SHENWAN = {
    # 制造业（子类映射）
    '制造业-计算机、通信和其他电子设备制造业': '电子',
    '制造业-电气机械和器材制造业': '电力设备',
    '制造业-专用设备制造业': '机械设备',
    '制造业-通用设备制造业': '机械设备',
    '制造业-仪器仪表制造业': '机械设备',
    '制造业-金属制品业': '机械设备',
    '制造业-化学原料和化学制品制造业': '基础化工',
    '制造业-化学纤维制造业': '基础化工',
    '制造业-橡胶和塑料制品业': '基础化工',
    '制造业-医药制造业': '医药生物',
    '制造业-汽车制造业': '汽车',
    '制造业-食品制造业': '食品饮料',
    '制造业-酒、饮料和精制茶制造业': '食品饮料',
    '制造业-农副食品加工业': '食品饮料',
    '制造业-纺织业': '纺织服饰',
    '制造业-纺织服装、服饰业': '纺织服饰',
    '制造业-皮革、毛皮、羽毛及其制品和制鞋业': '纺织服饰',
    '制造业-非金属矿物制品业': '建筑材料',
    '制造业-有色金属冶炼和压延加工业': '有色金属',
    '制造业-黑色金属冶炼和压延加工业': '钢铁',
    '制造业-铁路、船舶、航空航天和其他运输设备制造业': '国防军工',
    '制造业-造纸和纸制品业': '轻工制造',
    '制造业-印刷和记录媒介复制业': '轻工制造',
    '制造业-文教、工美、体育和娱乐用品制造业': '轻工制造',
    '制造业-家具制造业': '轻工制造',
    '制造业-木材加工和木、竹、藤、棕、草制品业': '轻工制造',
    '制造业-石油加工、炼焦和核燃料加工业': '石油石化',
    '制造业-石油、煤炭及其他燃料加工业': '石油石化',
    '制造业-废弃资源综合利用业': '环保',
    '制造业-金属制品、机械和设备修理业': '机械设备',
    '制造业-其他制造业': '综合',
    # 采矿业
    '采矿业-煤炭开采和洗选业': '煤炭',
    '采矿业-石油和天然气开采业': '石油石化',
    '采矿业-黑色金属矿采选业': '钢铁',
    '采矿业-有色金属矿采选业': '有色金属',
    '采矿业-开采辅助活动': '石油石化',
    '采矿业-其他采矿业': '有色金属',
    # 金融业
    '金融业-货币金融服务': '银行',
    '金融业-资本市场服务': '非银金融',
    '金融业-保险业': '非银金融',
    '金融业-其他金融业': '非银金融',
    # 大类直映射
    '房地产业': '房地产',
    '建筑业': '建筑装饰',
    '批发和零售业': '商贸零售',
    '交通运输、仓储和邮政业': '交通运输',
    '电力、热力、燃气及水生产和供应业': '公用事业',
    '住宿和餐饮业': '社会服务',
    '租赁和商务服务业': '社会服务',
    '科学研究和技术服务业': '社会服务',
    '水利、环境和公共设施管理业': '环保',
    '居民服务、修理和其他服务业': '社会服务',
    '教育': '社会服务',
    '卫生和社会工作': '医药生物',
    '文化、体育和娱乐业': '传媒',
    '农、林、牧、渔业': '农林牧渔',
    '综合': '综合',
    # 信息传输细分
    '信息传输、软件和信息技术服务业-软件和信息技术服务业': '计算机',
    '信息传输、软件和信息技术服务业-电信、广播电视和卫星传输服务': '通信',
    '信息传输、软件和信息技术服务业-互联网和相关服务': '传媒',
    # 大类兜底（用于无子类映射时的前缀匹配回退）
    '信息传输、软件和信息技术服务业': '计算机',
    '金融业': '非银金融',
    '采矿业': '有色金属',
}

def _zjh_to_shenwan(zjh):
    if not zjh: return None
    if zjh in _ZJH_TO_SHENWAN: return _ZJH_TO_SHENWAN[zjh]
    if '-' in zjh:
        broad = zjh.split('-')[0]
        if broad in _ZJH_TO_SHENWAN: return _ZJH_TO_SHENWAN[broad]
    return None

def _fetch_industry(code):
    """通过东方财富HTTP API获取行业分类（使用默认SSL验证）"""
    try:
        market = 'SH' if code.startswith(('6', '9')) else 'SZ'
        secode = f'{market}{code}'
        url = f'https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/CompanySurveyAjax?code={secode}'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://emweb.securities.eastmoney.com/'
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            jbzl = data.get('jbzl', {})
            zjh = jbzl.get('sszjhhy', '')
            sshy = jbzl.get('sshy', '')
            return _zjh_to_shenwan(zjh), sshy
    except Exception:
        return None, None

def main():
    print("=" * 60)
    print("周日行业补全拉取 v6.13.6")
    print("=" * 60)
    
    # 1. Clone repo
    print("\n[1] 拉取仓库...")
    if os.path.exists(WORK_DIR):
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    repo_url = f"https://github.com/{GITHUB_REPO}.git"
    result = _git_with_token(
        ["git", "clone", "--depth", "1", "--branch", "main", repo_url, WORK_DIR],
        timeout=60
    )
    if result.returncode != 0:
        print(f"ERROR: git clone失败: {result.stderr}")
        sys.exit(1)
    print("  克隆成功")
    
    # 2. Load existing caches
    print("\n[2] 加载现有缓存...")
    cache_file = f"{WORK_DIR}/行业缓存.json"
    sub_cache_file = f"{WORK_DIR}/二级行业缓存.json"
    
    industry_cache = {}
    sub_industry_cache = {}
    if os.path.exists(cache_file):
        with open(cache_file, 'r', encoding='utf-8') as f:
            industry_cache = json.load(f)
        print(f"  一级行业缓存: {len(industry_cache)} 条")
    if os.path.exists(sub_cache_file):
        with open(sub_cache_file, 'r', encoding='utf-8') as f:
            sub_industry_cache = json.load(f)
        print(f"  二级行业缓存: {len(sub_industry_cache)} 条")
    
    # 3. Build code list (all A-share stocks)
    print("\n[3] 构建全量代码列表...")
    codes = []
    for i in range(600000, 610000): codes.append(f"{i:06d}")
    for i in range(1, 5000): codes.append(f"{i:06d}")
    for i in range(300000, 302000): codes.append(f"{i:06d}")
    print(f"  共 {len(codes)} 个代码")
    
    # 4. Find missing codes
    to_fetch = []
    for code in codes:
        if code not in industry_cache or code not in sub_industry_cache:
            to_fetch.append(code)
    print(f"  缺一级: {len([c for c in codes if c not in industry_cache])} 只")
    print(f"  缺二级: {len([c for c in codes if c not in sub_industry_cache])} 只")
    print(f"  需拉取: {len(to_fetch)} 只")
    
    if not to_fetch:
        print("\n  全部命中，无需拉取")
        return
    
    # 5. Fetch industry data
    print(f"\n[4] 开始拉取 {len(to_fetch)} 只股票行业分类...")
    new_primary = 0; new_secondary = 0; fail_count = 0
    batch_size = 50
    
    for i in range(0, len(to_fetch), batch_size):
        batch = to_fetch[i:i+batch_size]
        for code in batch:
            primary, secondary = _fetch_industry(code)
            if primary and code not in industry_cache:
                industry_cache[code] = primary
                new_primary += 1
            if secondary and code not in sub_industry_cache:
                sub_industry_cache[code] = secondary
                new_secondary += 1
            if not primary and not secondary:
                fail_count += 1
            time.sleep(0.15)
        progress = min(i+batch_size, len(to_fetch))
        print(f"  进度: {progress}/{len(to_fetch)} (一级+{new_primary}, 二级+{new_secondary}, 失败{fail_count})")
    
    print(f"\n[5] 拉取完成: 一级{len(industry_cache)}条, 二级{len(sub_industry_cache)}条, 失败{fail_count}条")
    
    # 6. Save caches
    print("\n[6] 保存缓存文件...")
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(industry_cache, f, ensure_ascii=False, indent=2)
    with open(sub_cache_file, 'w', encoding='utf-8') as f:
        json.dump(sub_industry_cache, f, ensure_ascii=False, indent=2)
    print(f"  行业缓存: {cache_file}")
    print(f"  二级行业缓存: {sub_cache_file}")
    
    # 7. Push to GitHub
    print("\n[7] 推送到GitHub...")
    os.chdir(WORK_DIR)
    subprocess.run(["git", "config", "user.email", "bot@trae.ai"], capture_output=True, timeout=10)
    subprocess.run(["git", "config", "user.name", "Trae Bot"], capture_output=True, timeout=10)
    subprocess.run(["git", "add", "行业缓存.json", "二级行业缓存.json"], capture_output=True, timeout=10)
    
    result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True, timeout=10)
    if result.returncode == 0:
        print("  无变更，跳过推送")
        return
    
    subprocess.run(["git", "commit", "-m", f"周日行业补全 v6.13.6 (一级{new_primary}+二级{new_secondary})"], capture_output=True, timeout=10)
    push_result = _git_with_token(["git", "push", "origin", "main"], timeout=60, check=False)
    if push_result.returncode == 0:
        print("  ✅ 推送成功")
    else:
        print(f"  ⚠️ 推送失败: {push_result.stderr[:200]}")
    
    print("\n✅ 周日行业补全完成！")

if __name__ == "__main__":
    main()