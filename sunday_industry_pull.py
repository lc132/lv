#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周日行业补全拉取 v6.13.39
每周日执行：全量拉取东方财富HTTP行业分类（一级+二级），更新缓存文件并推送到GitHub。
v6.13.39: 用东方财富clist真实A股清单替换暴力枚举代码区间（根治超时——旧方案枚举16999个代码仅~4900真实，
          其余~12000个不存在代码永不在缓存→每次都进to_fetch→顺序抓取数小时超时）；抓取改为并发(max_workers=20)
          + 墙钟上限(25min)兜底，避免任何情况下无限运行。
"""
import urllib.request, json, os, time, subprocess, sys, tempfile, shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

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

def _fetch_all_a_codes():
    """v6.13.39: 获取全部真实A股6位代码（东方财富clist优先，新浪批量降级）。
    返回真实代码列表（约5000只）。根治超时：旧方案暴力枚举16999个代码区间，
    其中仅~4900为真实股票，其余~12000个不存在代码永不在缓存→每次都进to_fetch→顺序抓取数小时超时。
    改用真实清单后 to_fetch≈0，脚本秒级完成。"""
    # 方案一：东方财富 clist（一次性全量，效率最高）
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1", "pz": "6000", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f12", "_": str(int(time.time() * 1000))
        }
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if data and data.get('data') and data['data'].get('diff'):
            codes = [it.get('f12', '') for it in data['data']['diff'] if it.get('f12')]
            if codes:
                return codes
    except Exception:
        pass
    # 方案二：新浪批量（按代码区间拉实时行情，仅保留有成交的真实标的）
    try:
        ranges = []
        for i in range(600000, 606000): ranges.append(f"sh{i}")
        for i in range(688000, 690000): ranges.append(f"sh{i}")
        for i in range(1, 5000): ranges.append(f"sz{i:06d}")
        for i in range(300000, 302000): ranges.append(f"sz{i}")
        codes = []
        for i in range(0, len(ranges), 80):
            batch = ranges[i:i+80]
            try:
                url = f"https://hq.sinajs.cn/list={','.join(batch)}"
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Referer': 'https://finance.sina.com.cn'
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    text = resp.read().decode('gbk')
                for line in text.strip().split('\n'):
                    if not line or '=""' in line:
                        continue
                    try:
                        parts = line.split('"')[1].split(',')
                        if len(parts) < 6:
                            continue
                        header = line.split('="')[0]
                        raw = header.split('_')[-1] if '_' in header else header[-6:]
                        code = raw if len(raw) == 6 else raw[-6:]
                        cur = float(parts[3]) if parts[3] else 0
                        prev = float(parts[2]) if parts[2] else 0
                        if cur > 0 and prev > 0:
                            codes.append(code)
                    except (ValueError, IndexError):
                        continue
            except Exception:
                continue
        if codes:
            return codes
    except Exception:
        pass
    return []

def _is_valid_industry(val):
    """落盘前 schema 校验（P0#1 根治行业缓存缺陷）：行业名须为纯中文非空字符串、
    长度合理、无控制字符、不含数字（申万行业名均为纯中文，含数字即非法/证监会代码）。"""
    if not isinstance(val, str):
        return False
    s = val.strip()
    if not s or len(s) > 20:
        return False
    if any(ord(c) < 32 for c in s):
        return False
    if any(ch.isdigit() for ch in s):
        return False
    return True

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
    print("周日行业补全拉取 v6.13.39")
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
    
    # 3. Build code list (real A-share stocks only)
    print("\n[3] 构建全量代码列表(真实A股)...")
    codes = _fetch_all_a_codes()
    if not codes:
        print("ERROR: 无法获取真实A股代码列表，中止")
        sys.exit(1)
    print(f"  共 {len(codes)} 个真实代码")
    
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
    
    # 5. Fetch industry data（并发，设墙钟上限防超时）
    print(f"\n[4] 开始并发拉取 {len(to_fetch)} 只股票行业分类...")
    new_primary = 0; new_secondary = 0; fail_count = 0
    start = time.time()
    WALL = 1500  # 总墙钟上限 25 分钟，到点即停并保存已有成果（根治超时兜底）
    executor = ThreadPoolExecutor(max_workers=20)
    try:
        futures = {executor.submit(_fetch_industry, code): code for code in to_fetch}
        done = 0
        for future in as_completed(futures):
            code = futures[future]
            try:
                primary, secondary = future.result()
                if primary and code not in industry_cache:
                    industry_cache[code] = primary
                    new_primary += 1
                if secondary and code not in sub_industry_cache:
                    sub_industry_cache[code] = secondary
                    new_secondary += 1
                if not primary and not secondary:
                    fail_count += 1
            except Exception:
                fail_count += 1
            done += 1
            if done % 500 == 0:
                elapsed = time.time() - start
                print(f"  进度: {done}/{len(to_fetch)} (一级+{new_primary}, 二级+{new_secondary}, 失败{fail_count}, 用时{elapsed:.0f}s)")
                if elapsed > WALL:
                    print(f"  ⚠️ 已达墙钟上限 {WALL}s，停止剩余 {len(to_fetch)-done} 只拉取")
                    break
    finally:
        executor.shutdown(wait=False)
    
    print(f"\n[5] 拉取完成: 一级{len(industry_cache)}条, 二级{len(sub_industry_cache)}条, 失败{fail_count}条")

    # 5.5 落盘前 schema 校验（P0#1）：剔除非法条目，防止坏数据进入缓存被 step0B 同步污染筛选
    print("\n[5.5] 落盘前 schema 校验...")
    bad_p = [c for c, v in industry_cache.items() if not _is_valid_industry(v)]
    bad_s = [c for c, v in sub_industry_cache.items() if not _is_valid_industry(v)]
    for c in bad_p:
        del industry_cache[c]
    for c in bad_s:
        del sub_industry_cache[c]
    if bad_p or bad_s:
        print(f"  [schema校验] 剔除非法一级 {len(bad_p)} 条、二级 {len(bad_s)} 条")
    else:
        print("  [schema校验] 全部合法")

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
    
    subprocess.run(["git", "commit", "-m", f"周日行业补全 v6.13.39 (一级{new_primary}+二级{new_secondary})"], capture_output=True, timeout=10)
    push_result = _git_with_token(["git", "push", "origin", "main"], timeout=60, check=False)
    if push_result.returncode == 0:
        print("  ✅ 推送成功")
    else:
        print(f"  ⚠️ 推送失败: {push_result.stderr[:200]}")
    
    print("\n✅ 周日行业补全完成！")

if __name__ == "__main__":
    main()