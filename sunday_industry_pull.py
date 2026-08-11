#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
周日行业补全拉取 v6.21.2
每周日执行：全量拉取东方财富HTTP行业分类（一级+二级），更新缓存文件并推送到GitHub。
@since v6.13.39: 用东方财富clist真实A股清单替换暴力枚举代码区间（根治超时——旧方案枚举16999个代码仅~4900真实，
          其余~12000个不存在代码永不在缓存→每次都进to_fetch→顺序抓取数小时超时）；抓取改为并发(max_workers=20)
          + 墙钟上限(25min)兜底，避免任何情况下无限运行。
@since v6.20.12(维护更新): 新增行业分类校正白名单 _INDUSTRY_CORRECTION，强制覆盖东方财富
          API (jbzl.sszjhhy / jbzl.sshy) 返回的明显错误分类（一级+二级）；每周日运行时
          强制回写所有白名单代码的缓存，覆盖历史已缓存的错误值。白名单值经 schema 校验防脏数据。
@since v6.20.12 治理(P0-4): 白名单配套治理机制——(1)条目强制 source/effective_date/ttl_days；
          (2)多源交叉校验(东方财富+申万+同花顺，两源一致才采信，不一致查白名单并告警)；
          (3)月度自动复核(>=30天)：上游已自修正条目自动摘除(removed)、TTL过期标记需复核；
          复核记录落盘 行业白名单复核记录.json。白名单仍为兜底而非根治，防无限膨胀。
"""
import urllib.request, json, os, time, subprocess, sys, tempfile, shutil
import ssl, random, re
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

_EM_CTX = ssl._create_unverified_context()  # 东方财富/push2 部分节点需关闭证书校验

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
GITHUB_REPO = "lc132/lv"            # 主仓（代码 / SKILL.md）
DATA_REPO = "lc132/lv-data"        # @since P2-2: 行业缓存等数据迁独立仓，避免主仓膨胀
WORK_DIR = "/tmp/sunday_industry_pull"

# 统一提交身份常量（SSOT: ashare_screener.py v6.20.2 定义，治理整改#4 / P0-4）
# sunday_industry_pull.py 为独立脚本，不直接 import ashare_screener（避免触发其重型副作用），
# 此处镜像同一对常量值；pre_push_check.py 静态校验确保 ashare_screener.py 中的定义存在。
# 历史出现的 bot@trae.ai / "Trae Bot" 为无法关联 GitHub 的虚构域名，已在此收敛（P0-4）。
BOT_AUTHOR_NAME = "ashare-screener"
BOT_AUTHOR_EMAIL = "72593777+ashare-screener@users.noreply.github.com"  # @since P0-1: GitHub 可识别 noreply 格式, 消除 author=null

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

# ─────────────────────────────────────────────────────────────────────────────
# 行业分类校正白名单 + 治理机制（@since v6.20.12 治理增强，对应 P0-4）
# 白名单不再是"无限膨胀的兜底"，而是受 TTL + 来源 + 复核记录 治理：
#   1) 每条目强制字段：primary(一级)/secondary(二级)/source(来源)/effective_date(生效日期)/ttl_days(TTL)
#   2) 多源交叉校验：东方财富 + 申万(push2 f127) + 同花顺(最佳努力)；两源一致才采信，
#      不一致才查白名单并告警；下游已自修正的条目由月度复核自动摘除。
#   3) 月度自动复核(>=30天)：上游已与白名单一致 → 自动摘除(记录 removed，运行时抑制)；
#      TTL 过期 → 标记需人工复核(保留应用+告警)。复核记录落盘 行业白名单复核记录.json。
# 维护：新增条目请在 _INDUSTRY_CORRECTION 追加一条含 source/effective_date/ttl_days 的记录；
#       命中月度复核"upstream_corrected"摘除后，可同步删除本处对应条目以保持单一事实源。
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_TTL_DAYS = 90
_REVIEW_LOG_FILE = "行业白名单复核记录.json"   # 与缓存同目录(仓库根)
_AUDIT_SAMPLE = 60                            # 月度审计抽样非白名单标的数

_INDUSTRY_CORRECTION = {
    '600519': {'primary': '食品饮料', 'secondary': '白酒',   'source': '人工核定:贵州茅台2025年报+申万指数成份', 'effective_date': '2026-08-06', 'ttl_days': 90},
    '300750': {'primary': '电力设备', 'secondary': '电池',   'source': '人工核定:宁德时代2025年报+申万指数成份', 'effective_date': '2026-08-06', 'ttl_days': 90},
    '002594': {'primary': '汽车',     'secondary': '乘用车', 'source': '人工核定:比亚迪2025年报+申万指数成份',   'effective_date': '2026-08-06', 'ttl_days': 90},
    '601318': {'primary': '非银金融', 'secondary': '保险',   'source': '人工核定:中国平安2025年报+申万指数成份', 'effective_date': '2026-08-06', 'ttl_days': 90},
    '000595': {'primary': '公用事业', 'secondary': '电力行业', 'source': '人工核定:新能股份2026一季报(发电业务占比100%)', 'effective_date': '2026-08-11', 'ttl_days': 90},
}

def _is_date(s):
    try:
        datetime.strptime(s, '%Y-%m-%d')
        return True
    except Exception:
        return False

def _valid_corr_entry(code, entry):
    """白名单条目 schema 校验：primary/source/effective_date 必填且合法，secondary 可选。"""
    if not isinstance(entry, dict):
        return False
    if not entry.get('primary') or not _is_valid_industry(entry['primary']):
        return False
    if not isinstance(entry.get('source'), str) or not entry['source'].strip():
        return False
    if not _is_date(entry.get('effective_date', '')):
        return False
    sec = entry.get('secondary', '')
    if sec and not _is_valid_industry(sec):
        return False
    return True

def _corr_tuple(entry):
    return entry.get('primary'), entry.get('secondary', '')

def _is_expired(entry, today_str):
    try:
        ed = datetime.strptime(entry.get('effective_date', ''), '%Y-%m-%d')
        td = datetime.strptime(today_str, '%Y-%m-%d')
        return (td - ed).days > int(entry.get('ttl_days', _DEFAULT_TTL_DAYS))
    except Exception:
        return False

def _load_review_state(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d, dict):
            d.setdefault('removed', {})
            d.setdefault('reviews', [])
            d.setdefault('last_review_date', '')
            return d
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {'removed': {}, 'reviews': [], 'last_review_date': ''}

def _save_review_state(path, state):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ── 多源抓取（东方财富 + 申万 + 同花顺，最佳努力）──────────────────────────────
def _fetch_em_industry(code):
    """源1 东方财富 CompanySurvey（证监会行业→申万 + 二级行业），复用现有实现。"""
    return _fetch_industry(code)

def _fetch_shenwan_industry(code):
    """源2 申万（东方财富 push2 f127/f128 直出申万一/二级），最佳努力，失败返回 None。"""
    try:
        secid = ('1.' if code.startswith(('6', '9')) else '0.') + code
        url = (f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}"
               f"&fields=f127,f128&_={int(time.time() * 1000)}")
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'})
        with urllib.request.urlopen(req, timeout=3, context=_EM_CTX) as resp:
            d = json.loads(resp.read().decode())
        data = d.get('data') or {}
        f127 = (data.get('f127') or '').strip()
        f128 = (data.get('f128') or '').strip()
        if f127 and _is_valid_industry(f127):
            return f127, f128
    except Exception:
        pass
    return None, None

def _fetch_ths_industry(code):
    """源3 同花顺（最佳努力，失败返回 None 不参与校验）。"""
    try:
        url = (f"https://d.10jqka.com.cn/v4/stock/handle/api.php?token=105cbf3b4b1c1d6b6a8d4d0c4c2d8b0c"
               f"&code={code}&type=stock_api&_={int(time.time() * 1000)}")
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://stockpage.10jqka.com.cn/'})
        with urllib.request.urlopen(req, timeout=3, context=_EM_CTX) as resp:
            t = resp.read().decode('utf-8', 'ignore')
        m = re.search(r'"hy"\s*:\s*"([^"]+)"', t) or re.search(r'行业[":\s]+([一-龥]+)', t)
        if m:
            val = m.group(1).strip()
            if _is_valid_industry(val):
                return val, ''
    except Exception:
        pass
    return None, None

def _cross_validate(code):
    """返回可用源列表 [(源名, primary, secondary)]；不可达源返回 None 不计入。"""
    out = []
    em_p, em_s = _fetch_em_industry(code)
    if em_p:
        out.append(('东方财富', em_p, em_s))
    sw_p, sw_s = _fetch_shenwan_industry(code)
    if sw_p:
        out.append(('申万', sw_p, sw_s))
    ths_p, ths_s = _fetch_ths_industry(code)
    if ths_p:
        out.append(('同花顺', ths_p, ths_s))
    return out

def _consensus(results):
    """两源一致(同一(primary,secondary)>=2源)即采信，返回 (primary, secondary, agree)。"""
    pairs = {}
    for _, p, s in results:
        key = (p, s)
        pairs[key] = pairs.get(key, 0) + 1
    for (p, s), cnt in pairs.items():
        if cnt >= 2:
            return p, s, True
    if len(results) == 1:
        return results[0][1], results[0][2], False
    return None, None, False

def _apply_whitelist(code, primary, secondary, today_str, review_state):
    """查白名单：命中且未被月度摘除 → 强制覆盖；返回 (primary, secondary, applied, expired)。"""
    if code in review_state.get('removed', {}):
        return primary, secondary, False, False
    entry = _INDUSTRY_CORRECTION.get(code)
    if not entry or not _valid_corr_entry(code, entry):
        return primary, secondary, False, False
    expired = _is_expired(entry, today_str)
    c_pri = entry['primary'] if _is_valid_industry(entry['primary']) else primary
    c_sec = entry['secondary'] if (entry.get('secondary') and _is_valid_industry(entry['secondary'])) else secondary
    return c_pri, c_sec, True, expired

def _resolve_industry(code, em_p, em_s, today_str, review_state, xv):
    """多源交叉校验 + 白名单决策（热循环用：东方财富已抓取，仅补抓申万/同花顺）。
    xv: 失败探针 {'sw':bool,'ths':bool,'sw_fail':int,'ths_fail':int}，某源连续失败达阈值即全局禁用，
        避免不可达源拖垮墙钟。返回 (primary, secondary, meta)。"""
    meta = {'applied_whitelist': False, 'expired': False, 'agree': False, 'alert': None}
    # 1) 白名单优先（受 removed/TTL 治理约束）
    wp, ws, applied, expired = _apply_whitelist(code, em_p, em_s, today_str, review_state)
    if applied:
        meta['applied_whitelist'] = True
        meta['expired'] = expired
        if expired:
            meta['alert'] = f"白名单条目过期(TTL): {code} 仍强制覆盖 {wp}/{ws}，请人工复核"
        return wp, ws, meta
    # 2) 多源交叉校验（申万 + 同花顺，最佳努力）
    results = [('东方财富', em_p, em_s)]
    if xv['sw']:
        sw_p, sw_s = _fetch_shenwan_industry(code)
        if sw_p:
            results.append(('申万', sw_p, sw_s))
        else:
            xv['sw_fail'] += 1
            if xv['sw_fail'] >= 20:
                xv['sw'] = False
    if xv['ths']:
        ths_p, ths_s = _fetch_ths_industry(code)
        if ths_p:
            results.append(('同花顺', ths_p, ths_s))
        else:
            xv['ths_fail'] += 1
            if xv['ths_fail'] >= 20:
                xv['ths'] = False
    prim, sec, agree = _consensus(results)
    if agree and prim:
        meta['agree'] = True
        return prim, sec, meta  # 多源一致 → 采信共识
    if len(results) >= 2:
        meta['alert'] = (f"多源不一致且无白名单: {code} 东方财富={em_p}/{em_s} "
                         f"申万={results[1][1] if len(results) > 1 else 'N/A'} "
                         f"建议评估补白名单")
    return em_p, em_s, meta

def _monthly_review(today_str, review_state):
    """月度自动复核(>=30天)：上游已自修正→自动摘除(removed)；TTL过期→标记需复核。返回 (state, actions)。"""
    last = review_state.get('last_review_date', '')
    if last and _is_date(last):
        try:
            if (datetime.strptime(today_str, '%Y-%m-%d') - datetime.strptime(last, '%Y-%m-%d')).days < 30:
                return review_state, []
        except Exception:
            pass
    removed = dict(review_state.get('removed', {}))
    actions = []
    for code, entry in _INDUSTRY_CORRECTION.items():
        if not _valid_corr_entry(code, entry):
            continue
        results = _cross_validate(code)
        prim, sec, agree = _consensus(results)
        wp, ws = entry['primary'], entry.get('secondary', '')
        upstream_ok = agree and prim == wp and (ws == '' or sec == ws)
        expired = _is_expired(entry, today_str)
        if upstream_ok and code not in removed:
            removed[code] = {'date': today_str, 'reason': 'upstream_corrected',
                             'upstream': f"{prim}/{sec}"}
            actions.append({'code': code, 'action': 'remove', 'reason': 'upstream_corrected',
                            'upstream': f"{prim}/{sec}"})
        elif expired:
            actions.append({'code': code, 'action': 'expired', 'reason': 'ttl_expired'})
        else:
            actions.append({'code': code, 'action': 'keep'})
    review_state['removed'] = removed
    review_state['last_review_date'] = today_str
    review_state.setdefault('reviews', []).append({'date': today_str, 'actions': actions})
    return review_state, actions

def _audit_sample(codes, today_str, n=_AUDIT_SAMPLE):
    """月度审计抽样：对非白名单标的做多源交叉校验，发现不一致→返回候选告警(建议补白名单)。"""
    alerts = []
    cand = [c for c in codes if c not in _INDUSTRY_CORRECTION]
    if not cand:
        return alerts
    random.seed(today_str)
    sample = random.sample(cand, min(n, len(cand)))
    for code in sample:
        results = _cross_validate(code)
        prim, sec, agree = _consensus(results)
        if not agree and len(results) >= 2:
            em = dict((r[0], (r[1], r[2])) for r in results).get('东方财富', (None, None))
            alerts.append(f"审计抽样不一致: {code} 源={[r[0] for r in results]} "
                          f"东方财富={em[0]}/{em[1]} 建议评估是否补白名单")
    return alerts

def _fetch_all_a_codes():
    """@since v6.13.39: 获取全部真实A股6位代码（东方财富clist优先，新浪批量降级）。
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
    print("周日行业补全拉取 v6.20.12")
    print("=" * 60)

    today_str = datetime.now().strftime('%Y-%m-%d')

    # 1. Clone repo
    print("\n[1] 拉取仓库...")
    if os.path.exists(WORK_DIR):
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    repo_url = f"https://github.com/{DATA_REPO}.git"
    result = _git_with_token(
        ["git", "clone", "--depth", "1", "--branch", "main", repo_url, WORK_DIR],
        timeout=60
    )
    if result.returncode != 0:
        print(f"ERROR: git clone失败: {result.stderr}")
        sys.exit(1)
    print("  克隆成功")

    # 2. Load existing caches + 白名单复核状态
    print("\n[2] 加载现有缓存...")
    cache_file = f"{WORK_DIR}/行业缓存.json"
    sub_cache_file = f"{WORK_DIR}/二级行业缓存.json"
    review_file = f"{WORK_DIR}/{_REVIEW_LOG_FILE}"

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

    review_state = _load_review_state(review_file)
    print(f"  白名单复核状态: 已摘除 {len(review_state.get('removed', {}))} 条, "
          f"上次复核 {review_state.get('last_review_date') or '无'}")
    
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
    print(f"\n[4] 开始并发拉取 {len(to_fetch)} 只股票行业分类(东方财富+多源交叉校验)...")
    new_primary = 0; new_secondary = 0; fail_count = 0
    alerts = []   # 多源不一致/白名单过期 告警
    xv = {'sw': True, 'ths': True, 'sw_fail': 0, 'ths_fail': 0}  # 多源失败探针
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
                # 多源交叉校验 + 白名单治理（东方财富已抓取，补抓申万/同花顺，失败探针防拖垮墙钟）
                primary, secondary, meta = _resolve_industry(
                    code, primary, secondary, today_str, review_state, xv)
                if meta.get('alert'):
                    alerts.append(meta['alert'])
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

    if not xv['sw']:
        print("  [多源] 申万源连续失败，本次运行已禁用申万交叉校验(仅信任东方财富+白名单)")
    if not xv['ths']:
        print("  [多源] 同花顺源连续失败，本次运行已禁用同花顺交叉校验")

    print(f"\n[5] 拉取完成: 一级{len(industry_cache)}条, 二级{len(sub_industry_cache)}条, 失败{fail_count}条")

    # 5.3 白名单治理：月度自动复核 + 审计抽样（>=30天触发；保护墙钟，仅月度执行）
    print("\n[5.3] 白名单治理(月度复核+审计)...")
    review_state, review_actions = _monthly_review(today_str, review_state)
    if review_actions:
        removed = [a for a in review_actions if a['action'] == 'remove']
        expired = [a for a in review_actions if a['action'] == 'expired']
        kept = [a for a in review_actions if a['action'] == 'keep']
        print(f"  [月度复核] 自动摘除 {len(removed)} 条, TTL过期需复核 {len(expired)} 条, 保留 {len(kept)} 条")
        for a in removed:
            print(f"    🗑 摘除 {a['code']} (上游已自修正: {a.get('upstream')})")
        for a in expired:
            print(f"    ⏰ TTL过期 {a['code']} 仍保留应用，请人工复核/摘除")
        # 自动摘除后，从运行期白名单抑制已摘除条目（review_state['removed'] 已记录）
    else:
        print("  [月度复核] 未到月度窗口(>=30天)，跳过")
    audit_alerts = _audit_sample(codes, today_str)
    if audit_alerts:
        print(f"  [审计抽样] 发现 {len(audit_alerts)} 处多源不一致(候选补白名单):")
        for a in audit_alerts[:10]:
            print(f"    ⚠ {a}")
        alerts.extend(audit_alerts)

    # 5.4 校正白名单强制回写（覆盖已缓存的错误分类，保证白名单代码始终正确；跳过已摘除条目）
    print("\n[5.4] 校正白名单强制回写...")
    removed_set = set(review_state.get('removed', {}).keys())
    if _INDUSTRY_CORRECTION:
        corr_p = corr_s = 0
        for code, entry in _INDUSTRY_CORRECTION.items():
            if code in removed_set:
                continue  # 月度复核已判定上游自修正→抑制，不强制回写
            if not _valid_corr_entry(code, entry):
                continue
            # 不在缓存中（如已退市）则跳过，避免向缓存注入脏数据
            if code not in industry_cache and code not in sub_industry_cache:
                continue
            c_pri = entry['primary'] if _is_valid_industry(entry['primary']) else None
            c_sec = entry['secondary'] if (entry.get('secondary') and _is_valid_industry(entry['secondary'])) else None
            if c_pri and industry_cache.get(code) != c_pri:
                industry_cache[code] = c_pri
                corr_p += 1
            if c_sec and sub_industry_cache.get(code) != c_sec:
                sub_industry_cache[code] = c_sec
                corr_s += 1
        if corr_p or corr_s:
            print(f"  [白名单校正] 强制回写一级 {corr_p} 条、二级 {corr_s} 条")
        else:
            print("  [白名单校正] 白名单代码均已正确，无需回写")
    else:
        print("  [白名单校正] 白名单为空，跳过")

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

    # 6. Save caches + 复核状态
    print("\n[6] 保存缓存文件...")
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(industry_cache, f, ensure_ascii=False, indent=2)
    with open(sub_cache_file, 'w', encoding='utf-8') as f:
        json.dump(sub_industry_cache, f, ensure_ascii=False, indent=2)
    _save_review_state(review_file, review_state)
    print(f"  行业缓存: {cache_file}")
    print(f"  二级行业缓存: {sub_cache_file}")
    print(f"  白名单复核记录: {review_file}")

    # 7. Push to GitHub
    print("\n[7] 推送到GitHub...")
    os.chdir(WORK_DIR)
    subprocess.run(["git", "config", "user.email", BOT_AUTHOR_EMAIL], capture_output=True, timeout=10)
    subprocess.run(["git", "config", "user.name", BOT_AUTHOR_NAME], capture_output=True, timeout=10)
    subprocess.run(["git", "add", "行业缓存.json", "二级行业缓存.json", _REVIEW_LOG_FILE],
                   capture_output=True, timeout=10)

    # 打印累计告警
    if alerts:
        print(f"\n  ⚠️ 本次告警 {len(alerts)} 条:")
        for a in alerts[:20]:
            print(f"    - {a}")

    result = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True, timeout=10)
    if result.returncode == 0:
        print("  无变更，跳过推送")
        return

    wl_note = ""
    if review_actions:
        n_rm = len([a for a in review_actions if a['action'] == 'remove'])
        if n_rm:
            wl_note = f" | 白名单自动摘除{n_rm}"
    # @since v6.20.12 治理(P0 Task 1): 自动提交信息前置门禁，并统一 type: 前缀格式
    _commit_msg = f"data: 周日行业补全 (一级{new_primary}+二级{new_secondary}){wl_note}"
    try:
        import importlib.util as _ilu
        _ver = ""
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "VERSION"),
                      encoding="utf-8") as _vf:
                _ver = _vf.read().strip()
        except OSError:
            _ver = ""
        _spec = _ilu.spec_from_file_location(
            "commit_gate",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "commit_gate.py"))
        _cg = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_cg)
        _ok, _reason = _cg.validate_commit_message(_commit_msg, current_version=_ver or None)
        if not _ok:
            print(f"  ❌ 提交信息未过门禁，跳过推送: {_reason}")
            return
    except Exception as _e:
        print(f"  ❌ 门禁校验异常，保守跳过推送: {str(_e)[:80]}")
        return
    subprocess.run(["git", "commit", "-m", _commit_msg], capture_output=True, timeout=10)
    push_result = _git_with_token(["git", "push", "origin", "main"], timeout=60, check=False)
    if push_result.returncode == 0:
        print("  ✅ 推送成功")
    else:
        print(f"  ⚠️ 推送失败: {push_result.stderr[:200]}")

    print("\n✅ 周日行业补全完成！")

if __name__ == "__main__":
    main()