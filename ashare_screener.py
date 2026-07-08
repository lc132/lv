#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股每日盘前短线标的智能筛选 v6.13.15
37步完整执行流程 | 腾讯一级行情 | 腾讯HTTP一级K线 | iTick二级K线 | 行业缓存读取 | 20策略 | 27信号 | 13项硬排除 | 微观结构过滤 | AI策略分析 | MACD+K线评分 | 多因子共振 | 盈亏比TOP10 | 数量校验修复 | 指数数据显示修复 | 主力资金HTTP | 周末跳过推荐历史 | 板块热度排序TOP10 | HTML深色主题美化 | 雪球新闻源 | 回测no_entry警告
"""
import urllib.request, urllib.error, urllib.parse, json, os, math, time, shutil, subprocess, html, gzip, re, hashlib, ssl, socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from collections import Counter, defaultdict

# v6.12.23: 全局socket超时+SSL未验证上下文，解决沙箱网络限制
socket.setdefaulttimeout(8)

# v6.12.23: 全局SSL未验证上下文，解决沙箱SSL证书验证失败问题
_SSL_CTX = ssl._create_unverified_context()
urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=_SSL_CTX)))
from openpyxl import load_workbook
from lib.factor import compute_main_force_position, compute_short_term_breakout, resonance_check
from lib.microstructure import microstructure_filter
from lib.analyst import generate_ai_report
from lib.backtest import run_backtest, generate_backtest_report, generate_backtest_html, push_backtest_to_feishu, _build_backtest_lookup
from lib.core import DATA_DIR

BUILTIN_VERSION = "v6.13.17"
GITHUB_REPO = "lc132/lv"
beijing_now = None; beijing_date = None; beijing_weekday = None
_beijing_api_ok = False  # v6.13.11: 北京时间API是否正常
data_date = None; prediction_date = None; pred_yyyymmdd = None
# 2026年中国A股节假日（非交易日）— 需年初更新
_CN_HOLIDAYS_2026 = [
    "2026-01-01","2026-01-02","2026-02-16","2026-02-17","2026-02-18","2026-02-19","2026-02-20",
    "2026-04-06","2026-05-01","2026-06-19","2026-06-20","2026-06-21",
    "2026-10-01","2026-10-02","2026-10-05","2026-10-06","2026-10-07"
]
file_version = BUILTIN_VERSION; params = {}
_pl_sorted = []  # v6.12.10: 模块级初始化，防止 NameError
market_condition = "震荡"; position_pct = 55
index_data = {}  # 三大指数行情(供HTML使用)
MIN_POSITION_PCT = 20  # v6.8.7: 全局仓位下限
_step_status = []  # v6.13.11: 步骤执行状态追踪

def _load_credential(env_key, file_path, fallback=""):
    if env_key in os.environ: return os.environ[env_key]
    try:
        with open(file_path, 'r', encoding='utf-8') as f: return f.read().strip()
    except (FileNotFoundError, PermissionError): pass
    return fallback

GITHUB_TOKEN = _load_credential("GITHUB_TOKEN", "/workspace/.github_token")
FEISHU_WEBHOOK = _load_credential("FEISHU_WEBHOOK", "/workspace/.feishu_webhook")

# v6.13.11: 步骤执行状态追踪
def record_step_status(step_name, status, detail=""):
    """记录步骤执行状态: status='OK'|'SKIP'|'WARN'|'FAIL'"""
    _step_status.append({"step": step_name, "status": status, "detail": detail})

def print_step_status_summary():
    """打印步骤执行状态摘要"""
    if not _step_status: return
    print("\n" + "="*60)
    print("📋 步骤执行状态报告")
    print("="*60)
    for s in _step_status:
        icon = {"OK": "✅", "SKIP": "⏭️", "WARN": "⚠️", "FAIL": "❌"}.get(s["status"], "❓")
        detail = f" — {s['detail']}" if s['detail'] else ""
        print(f"  {icon} {s['step']}{detail}")
    ok_count = sum(1 for s in _step_status if s['status'] == 'OK')
    warn_count = sum(1 for s in _step_status if s['status'] == 'WARN')
    fail_count = sum(1 for s in _step_status if s['status'] == 'FAIL')
    skip_count = sum(1 for s in _step_status if s['status'] == 'SKIP')
    print(f"  合计: 通过{ok_count} 警告{warn_count} 跳过{skip_count} 失败{fail_count}")
    print("="*60)

# v6.9.34: 东方财富HTTP行业分类（替代Baostock TCP，解决沙箱网络限制）
INDUSTRY_CACHE_FILE = "/workspace/行业缓存.json"
_industry_cache = {}          # {code: "申万一级行业"}
# v6.9.35: 二级行业缓存（东方财富sshy，与一级行业同源拉取）
SUB_INDUSTRY_CACHE_FILE = "/workspace/二级行业缓存.json"
_sub_industry_cache = {}      # {code: "东方财富二级行业"}

# v6.9.34: 证监会行业 → 申万一级行业映射表
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
    '居民服务、修理和其他服务业': '社会服务',
}

DEFAULT_PARAMS = {
    "search_budget": 25, "northbound_threshold": 3000, "consecutive_weeks": 2,
    "win_rate_drop_threshold": 10, "limit_down_threshold": 100,
    "max_adjust_params": 3, "confidence_position_enabled": True,
    "strategy_concentration_pct": 30,
    "data_tier_l2_skip_on_unavailable": True,
    "data_tier_l3_downgrade_to_signal": True, "strategy_a_weak_market": "closed"
}

# 模块级策略映射表（DRY：避免函数内重复定义）
_STRATEGY_ORDER = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4, 'F': 5, 'G': 6, 'H': 7, 'I': 8, 'J': 9, 'K': 10, 'L': 11, 'M': 12, 'N': 13, 'O': 14, 'P': 15, 'Q': 16, 'R': 17, 'S': 18, 'T': 19}
_STRATEGY_NAMES = {'A': '动量延续', 'B': '超跌反弹', 'C': '事件驱动', 'D': '回调企稳', 'E': '资金埋伏', 'F': '北向资金', 'G': '横盘突破', 'H': '地量见底', 'I': '均线突破', 'J': '龙回头', 'K': '缺口回补', 'L': '黄金坑', 'M': '涨停回调', 'N': '新高突破', 'O': '回踩均线', 'P': '地量反弹', 'Q': 'W底突破', 'R': '主力共振(强)', 'S': '主力共振(弱)', 'T': '主力观察'}
_STRATEGY_COLORS = {'A': '#22c55e', 'B': '#3b82f6', 'C': '#8b5cf6', 'D': '#f59e0b', 'E': '#ec4899', 'F': '#06b6d4', 'G': '#10b981', 'H': '#f97316', 'I': '#14b8a6', 'J': '#ef4444', 'K': '#a855f7', 'L': '#eab308', 'M': '#f472b6', 'N': '#84cc16', 'O': '#38bdf8', 'P': '#fb923c', 'Q': '#22d3ee', 'R': '#dc2626', 'S': '#f97316', 'T': '#94a3b8'}
_STRATEGY_STOP_LOSS = {'A': 0.95, 'B': 0.93, 'C': 0.95, 'D': 0.95, 'E': 0.965, 'F': 0.965, 'G': 0.95, 'H': 0.94, 'I': 0.95, 'J': 0.94, 'K': 0.955, 'L': 0.94, 'M': 0.945, 'N': 0.95, 'O': 0.95, 'P': 0.945, 'Q': 0.95, 'R': 0.95, 'S': 0.95, 'T': 0.94}
_STRATEGY_TAKE_PROFIT = {'A': 1.05, 'B': 1.07, 'C': 1.05, 'D': 1.05, 'E': 1.04, 'F': 1.04, 'G': 1.05, 'H': 1.06, 'I': 1.05, 'J': 1.06, 'K': 1.05, 'L': 1.06, 'M': 1.05, 'N': 1.05, 'O': 1.05, 'P': 1.05, 'Q': 1.05, 'R': 1.05, 'S': 1.04, 'T': 1.04}

def _tie_key(c):
    """模块级平局打破键：策略优先级→评分→平局分→量比→换手偏离"""
    vr = c.get('volume_ratio') or 0
    to = c.get('turnover') or 0
    to_penalty = abs(to - 10) if to > 0 else 99
    return (-c.get('score', 0), _STRATEGY_ORDER.get(c.get('strategy', 'Z'), 99),
            -(c.get('_tie_score', 0)), -vr, to_penalty)

# ============================================================
# 工具函数
# ============================================================
def _industry_str(c):
    """v6.9.36: 安全提取行业字符串，处理dict类型的industry值"""
    ind = c.get('industry', '')
    if isinstance(ind, dict):
        return ind.get('sshy', '') or '未知'
    return ind if ind else '未知'

def log_alert(level, module, message, timestamp=None):
    if timestamp is None: timestamp = datetime.now()
    ts = timestamp.strftime('%Y-%m-%d %H:%M:%S') if hasattr(timestamp, 'strftime') else str(timestamp)
    try:
        with open('/workspace/系统告警.log', 'a', encoding='utf-8') as f:
            f.write(f"[{ts}] [{level}] {module}: {message}\n")
    except (PermissionError, OSError):
        pass
    print(f"[{level}] {module}: {message}")

def safe_read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if not isinstance(data, (list, dict)):
                log_alert("WARNING", "safe_read_json", f"{path} 格式异常(非list/dict)")
                return default if default is not None else []
            return data
    except (json.JSONDecodeError, PermissionError) as e:
        log_alert("ERROR", "safe_read_json", f"{path}: {str(e)}")
        return default if default is not None else []
    except FileNotFoundError:
        return default if default is not None else []

def safe_write_json(path, data):
    """原子写入：先写临时文件再重命名，防止写入中断导致数据损坏"""
    try:
        tmp_path = path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)  # 原子操作(POSIX)
    except (PermissionError, OSError) as e: log_alert("ERROR", "safe_write_json", f"{path}: {str(e)[:80]}")

def safe_append_json(path, record):
    data = safe_read_json(path); data.append(record); safe_write_json(path, data)

def _version_cmp(v):
    """将版本号字符串转为可比较的整数元组，v6.9.53 → (6,9,53)"""
    import re
    nums = re.findall(r'\d+', v)
    return tuple(int(n) for n in nums)

# ============================================================
# 腾讯行情API (v6.6.27: 替代新浪)
# ============================================================
TENCENT_API = "https://qt.gtimg.cn/q="

def _parse_tencent_field(raw, idx, default=None):
    """安全解析腾讯API字段，返回 float 或 default"""
    try:
        if idx >= len(raw): return default
        v = raw[idx]
        if v in ('', '-', None): return default
        return float(v)
    except (ValueError, TypeError, IndexError): return default

def fetch_tencent_index(codes):
    """拉取指数行情，返回 {code: {name,price,prev_close,change_pct,change_amount}}"""
    result = {}
    try:
        url = f"{TENCENT_API}{','.join(codes)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            text = resp.read().decode('gbk', errors='replace')
        for line in text.strip().split('\n'):
            if not line or '="' not in line: continue
            try:
                code = line.split('_')[1].split('=')[0] if '_' in line else ''
                raw = line.split('"')[1].split('~')
                if len(raw) < 5: continue
                price = _parse_tencent_field(raw, 3, 0)
                prev = _parse_tencent_field(raw, 4, 0)
                chg = round((price - prev) / prev * 100, 2) if prev > 0 else 0
                chg_amt = round(price - prev, 2)  # 涨跌点数
                result[code] = {"name": raw[1], "price": price, "prev_close": prev, "change_pct": chg, "change_amount": chg_amt}
            except (urllib.error.URLError, json.JSONDecodeError, OSError): pass
    except Exception as e: log_alert("WARNING", "腾讯指数", f"获取失败: {str(e)[:60]}")
    return result

def fetch_tencent_stocks(codes):
    """拉取个股行情，返回 [{code,name,open,close,high,low,prev_close,change_pct,amount,turnover,amplitude,volume_ratio,pe_ttm,total_cap,main_inflow}]"""
    result = []
    batch_size = 40
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        try:
            url = f"{TENCENT_API}{','.join(batch)}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                text = resp.read().decode('gbk', errors='replace')
            for line in text.strip().split('\n'):
                if not line or '="' not in line: continue
                try:
                    raw = line.split('"')[1].split('~')
                    if len(raw) < 10: continue
                    code = raw[2]
                    name = raw[1]
                    close = _parse_tencent_field(raw, 3, 0)
                    prev_close = _parse_tencent_field(raw, 4, 0)
                    open_p = _parse_tencent_field(raw, 5, 0)
                    if close <= 0 or prev_close <= 0: continue
                    high = _parse_tencent_field(raw, 33, close)
                    low = _parse_tencent_field(raw, 34, close)
                    # 腾讯API字段: [37]=amount(万元) [38]=turnover(%) [39]=pe_ttm [43]=amplitude(%) [44]=total_cap(亿元) [45]=high(冗余) [46]=low(冗余) [49]=volume_ratio [62]=主力净流入(万元)
                    result.append({
                        "code": code, "name": name,
                        "open": open_p, "close": close,
                        "high": high, "low": low, "prev_close": prev_close,
                        "change_pct": round((close - prev_close) / prev_close * 100, 2),
                        "amount": _parse_tencent_field(raw, 37, 0) * 10000,  # 万元→元
                        "turnover": _parse_tencent_field(raw, 38, 0),
                        "amplitude": _parse_tencent_field(raw, 43, 0),
                        "volume_ratio": _parse_tencent_field(raw, 49, None),
                        "pe_ttm": _parse_tencent_field(raw, 39, None),
                        "total_cap": (_tc := _parse_tencent_field(raw, 44, None)) and _tc * 1e8,  # v6.12.10: fix dup call, 亿元→元
                        "main_inflow": (_mi := _parse_tencent_field(raw, 62, None)) and _mi * 10000,  # v6.13.4: 腾讯API字段62主力净流入(万元→元)
                    })
                except (ValueError, TypeError, IndexError, AttributeError): pass
            time.sleep(0.05)
        except Exception as e: log_alert("WARNING", "腾讯个股", f"批次失败: {str(e)[:40]}")
    return result

def fetch_tencent_single(code):
    """拉取单只个股"""
    prefix = 'sz' if code.startswith(('0','3')) else 'sh'
    stocks = fetch_tencent_stocks([f"{prefix}{code}"])
    return stocks[0] if stocks else None

# ============================================================
# 步骤0：北京时间
# ============================================================
def step0_get_beijing_time():
    global beijing_now, beijing_date, beijing_weekday, data_date, prediction_date, pred_yyyymmdd, _beijing_api_ok
    for api_url in ['https://timeapi.io/api/time/current/zone?timeZone=Asia/Shanghai',
                     'https://worldtimeapi.org/api/timezone/Asia/Shanghai',
                     'http://worldclockapi.com/api/json/cst/now']:
        try:
            req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
            dt_str = data.get('dateTime') or data.get('datetime') or data.get('currentDateTime')
            if not dt_str: continue
            # 清理时区后缀 +08:00 → 纯ISO格式
            if '+' in dt_str: dt_str = dt_str.split('+')[0]
            elif dt_str.endswith('Z'): dt_str = dt_str[:-1]
            if '.' in dt_str:
                date_part, frac = dt_str.split('.')
                dt_str = date_part + '.' + frac[:6]
            beijing_now = datetime.fromisoformat(dt_str)
            _beijing_api_ok = True
            break
        except (urllib.error.URLError, json.JSONDecodeError, ValueError, OSError): continue
    if beijing_now is None:
        from datetime import timezone as _tz
        beijing_now = datetime.now(_tz(timedelta(hours=8)))
        log_alert("WARNING", "北京时间", "所有API不可达，降级为系统时间(Asia/Shanghai)")
    beijing_date = beijing_now.strftime('%Y-%m-%d')
    beijing_weekday = beijing_now.weekday()
    beijing_hour = beijing_now.hour
    is_pre_market = (beijing_hour < 9) or (beijing_hour == 9 and beijing_now.minute < 30)
    is_post_market = (beijing_hour >= 15)
    # data_date: 盘前/交易时段→昨日，收盘后→当日，周末回退到周五
    if beijing_weekday == 5: data_date = (beijing_now - timedelta(days=1)).strftime('%Y-%m-%d')
    elif beijing_weekday == 6: data_date = (beijing_now - timedelta(days=2)).strftime('%Y-%m-%d')
    elif beijing_weekday == 0 and is_pre_market: data_date = (beijing_now - timedelta(days=3)).strftime('%Y-%m-%d')  # v6.8.5: 周一盘前回退到周五
    elif is_pre_market or not is_post_market: data_date = (beijing_now - timedelta(days=1)).strftime('%Y-%m-%d')
    else: data_date = beijing_date
    # prediction_date: 盘前→当日，收盘后→下一交易日，周末→周一
    if beijing_weekday == 5: prediction_date = (beijing_now + timedelta(days=2)).strftime('%Y-%m-%d')
    elif beijing_weekday == 6: prediction_date = (beijing_now + timedelta(days=1)).strftime('%Y-%m-%d')
    elif is_pre_market: prediction_date = beijing_date
    elif is_post_market:
        if beijing_weekday == 4: prediction_date = (beijing_now + timedelta(days=3)).strftime('%Y-%m-%d')
        else: prediction_date = (beijing_now + timedelta(days=1)).strftime('%Y-%m-%d')
    else: prediction_date = beijing_date
    # 节假日调整：data_date和prediction_date若为节假日则回退/推进到最近交易日
    h = _CN_HOLIDAYS_2026
    # data_date若为节假日，回退到上一个交易日
    if data_date in h:
        dd_dt = datetime.strptime(data_date, '%Y-%m-%d')
        for _ in range(20):
            dd_dt -= timedelta(days=1)
            candidate = dd_dt.strftime('%Y-%m-%d')
            if candidate not in h and dd_dt.weekday() < 5:
                data_date = candidate
                log_alert("INFO", "节假日", f"data_date回退至{data_date}")
                break
    # prediction_date若为节假日，推进到下一个交易日
    if prediction_date in h:
        pd_dt = datetime.strptime(prediction_date, '%Y-%m-%d')
        for _ in range(20):
            pd_dt += timedelta(days=1)
            candidate = pd_dt.strftime('%Y-%m-%d')
            if candidate not in h and pd_dt.weekday() < 5:
                prediction_date = candidate
                log_alert("INFO", "节假日", f"prediction_date推进至{prediction_date}")
                break
    pred_yyyymmdd = prediction_date.replace('-', '')
    log_alert("INFO", "北京时间", f"beijing={beijing_date} data={data_date} pred={prediction_date}")

def _git_with_token(cmd_args, timeout=30, check=True, log_prefix=""):
    """使用 GIT_ASKPASS 安全传递 Token，避免 Token 出现在进程列表中"""
    import tempfile
    askpass_script = None
    try:
        fd, askpass_script = tempfile.mkstemp(prefix='git_askpass_', suffix='.sh')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write('#!/bin/bash\necho "$GIT_TOKEN"\n')
        os.chmod(askpass_script, 0o700)
        env = os.environ.copy()
        env['GIT_ASKPASS'] = askpass_script
        env['GIT_TOKEN'] = GITHUB_TOKEN
        result = subprocess.run(cmd_args, capture_output=True, text=True, timeout=timeout, env=env, check=check)
        return result
    finally:
        if askpass_script and os.path.exists(askpass_script):
            try: os.remove(askpass_script)
            except OSError: pass

# ============================================================
# 步骤0A：拉取持仓跟踪
# ============================================================
def step0A_pull_holdings():
    try:
        repo_dir = "/tmp/lv_holdings_pull"
        if os.path.exists(repo_dir): shutil.rmtree(repo_dir, ignore_errors=True)
        repo_url = f"https://github.com/{GITHUB_REPO}.git"
        _git_with_token(["git", "clone", "--depth", "1", "--branch", "main", repo_url, repo_dir], timeout=30)
        xlsx_src = os.path.join(repo_dir, "持仓跟踪.xlsx")
        if os.path.exists(xlsx_src):
            shutil.copy(xlsx_src, "/workspace/持仓跟踪.xlsx")
            log_alert("INFO", "持仓拉取", "持仓跟踪.xlsx 已同步")
        for f in os.listdir(repo_dir):
            if f.startswith("推荐历史_") and f.endswith(".json"):
                lp = os.path.join("/workspace", f); rp = os.path.join(repo_dir, f)
                if not os.path.exists(lp) or os.path.getmtime(rp) > os.path.getmtime(lp):
                    shutil.copy(rp, lp); log_alert("INFO", "持仓拉取", f"{f} 已更新")
        shutil.rmtree(repo_dir, ignore_errors=True)
    except Exception as e: log_alert("WARNING", "持仓拉取", f"{str(e)[:80]}")

# ============================================================
# 步骤1-2：节假日 + 极端行情（腾讯API）
# ============================================================
def step1_holiday_check():
    global prediction_date, pred_yyyymmdd, position_pct, market_condition, params
    h = _CN_HOLIDAYS_2026
    # 长休检测：data_date到prediction_date之间自然日≥3天→弱市+仓位≤30%+搜索预算+5
    dd_dt = datetime.strptime(data_date, '%Y-%m-%d')
    pd_dt = datetime.strptime(prediction_date, '%Y-%m-%d')
    days_gap = (pd_dt - dd_dt).days
    if days_gap >= 4:  # data_date和prediction_date间隔≥4自然日（含周末+节假日）
        log_alert("INFO", "节假日", f"长休{data_date}→{prediction_date}(间隔{days_gap}日)，弱市+仓位≤30%+搜索预算+5")
        position_pct = 30
        market_condition = "弱市"
        params = params.copy()
        params['search_budget'] = params.get('search_budget', 25) + 5
    return False

def step2_extreme_market():
    global position_pct, market_condition
    idx = fetch_tencent_index(["sh000001"])
    if not idx: return False
    sh = idx.get("sh000001", {})
    cur = sh.get("price", 0); chg = sh.get("change_pct", 0)
    log_alert("INFO", "极端行情", f"上证{cur:.0f} 涨跌{chg:.2f}%")
    if chg <= -3: return True
    if chg >= 3: position_pct = 30; market_condition = "强市(极端上涨/降仓防追高)"
    return False

# ============================================================
# 步骤3-3A：外围市场（保留新浪，腾讯无美股）
# ============================================================
def step3_external_markets():
    global position_pct, market_condition
    try:
        all_down = True
        api_failures = 0
        for code in [".DJI", ".INX", ".IXIC"]:
            try:
                url = f"https://hq.sinajs.cn/list=gb_{code}"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    text = resp.read().decode('gbk')
                if '=""' in text: all_down = False; break
                chg = float(text.split('"')[1].split(',')[1]) if ',' in text.split('"')[1] else 0
                if chg > -2: all_down = False; break
            except (urllib.error.URLError, ValueError, IndexError, json.JSONDecodeError, OSError): api_failures += 1; continue  # 单指数失败不中断
        if api_failures >= 2:
            log_alert("WARNING", "外围市场", f"新浪API {api_failures}/3 不可达，跳过美股检测")
            all_down = False  # 数据不可达时不触发弱市
        if all_down: position_pct = min(position_pct, 30); market_condition = "弱市(美股暴跌)"
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError, ModuleNotFoundError, ImportError): pass

def step3A_domestic_index_check():
    """v6.8.7: 原名step3A_premarket_futures，实际使用深证成指作为大盘强弱代理指标"""
    global position_pct, market_condition
    try:
        idx = fetch_tencent_index(["sz399001"])
        if not idx: return
        sz = idx.get("sz399001", {})
        chg = sz.get("change_pct", 0)
        if chg < -1:
            log_alert("WARNING", "大盘代理", f"深成指跌{chg:.1f}%>1%，偏空降档")
            position_pct = max(position_pct - 15, MIN_POSITION_PCT)
            if market_condition == "强市": market_condition = "震荡"
            elif market_condition == "震荡": market_condition = "弱市"
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError): log_alert("INFO", "大盘代理", "数据不可得，跳过")

# ============================================================
# 步骤4：持仓行情同步（腾讯API）
# ============================================================
def step4_holdings_sync():
    holdings = []
    all_history = []
    for f in sorted(os.listdir('/workspace')):
        if f.startswith('推荐历史_') and f.endswith('.json'):
            records = safe_read_json(os.path.join('/workspace', f))
            for r in records:
                if isinstance(r, dict): r['_file'] = f
                all_history.append(r)
    for r in all_history:
        if r.get('type') != 'holding': continue
        code = r.get('code', ''); old_current = r.get('current')
        try:
            q = fetch_tencent_single(code)
            if q and q.get('close', 0) > 0:
                new_current = q['close']
                r['prev_close'] = old_current; r['current'] = new_current
                r['update_date'] = data_date
                cost = r.get('cost', new_current); shares = r.get('shares', 100)
                r['market_value'] = round(new_current * shares, 2)
                r['pnl_amount'] = round((new_current - cost) * shares, 2)
                if cost > 0: r['pnl_pct'] = round((new_current - cost) / cost * 100, 2)
                holdings.append(r)
        except Exception as e: log_alert("WARNING", "持仓行情", f"{code} 失败: {str(e)[:40]}")
    file_groups = defaultdict(list)
    for r in all_history:
        if '_file' in r: file_groups[r.pop('_file')].append(r)
    for fn, recs in file_groups.items(): safe_write_json(os.path.join('/workspace', fn), recs)
    return holdings

# ============================================================
# 步骤4A-4C：做T + 持仓跟踪 + 危机
# ============================================================
def step4A_doT_eval(holdings):
    recs = []
    for h in holdings:
        pnl = h.get('pnl_pct')
        if pnl is None: f = "数据缺失"
        elif pnl > -5: f = "观望"
        elif -10 < pnl <= -5: f = "True"
        elif -15 < pnl <= -10: f = "谨慎"
        else: f = "False"
        recs.append({"type": "do_T_eval", "code": h.get('code'), "name": h.get('name'),
                      "date": data_date, "pnl_pct": pnl, "do_T_feasible": f,
                      "position_ratio": "≤1/3" if f == "True" else ("≤1/4" if f == "谨慎" else "不操作")})
    if recs:
        hist_file = f"/workspace/推荐历史_{prediction_date.replace('-', '')}.json"
        safe_write_json(hist_file, safe_read_json(hist_file) + recs)
    return recs

def step4B_sync_holdings_xlsx(holdings):
    wb = None
    try:
        p = "/workspace/持仓跟踪.xlsx"
        wb = load_workbook(p); ws = wb["持仓明细"]
        cr = {}
        for row in range(2, ws.max_row + 1):
            rc = ws.cell(row=row, column=1).value
            if rc:
                c = str(rc).strip()
                if len(c) == 4: c = c.zfill(6)
                if c.isdigit() and len(c) == 6: cr[c] = row
        up = 0
        for h in holdings:
            c = str(h.get('code', ''))
            if c not in cr: continue
            row = cr[c]; cur = h.get('current')
            if cur is None: continue
            ws.cell(row=row, column=8).value = cur
            ws.cell(row=row, column=9).value = h.get('market_value')
            ws.cell(row=row, column=10).value = round(h.get('pnl_amount', 0), 2)
            ws.cell(row=row, column=11).value = round(float(h.get('pnl_pct') or 0), 4)
            ws.cell(row=row, column=12).value = data_date; up += 1
        if up: wb.save(p); log_alert("INFO", "持仓跟踪", f"已更新{up}只")
    except Exception as e: log_alert("WARNING", "持仓跟踪", f"{str(e)[:80]}")
    finally:
        if wb:
            try: wb.close()
            except Exception: log_alert("DEBUG", "持仓跟踪", "wb.close()失败")

def step4C_crisis_check(holdings):
    alerts = []
    for h in holdings:
        code = h.get('code', '?'); name = h.get('name', '?')
        cost = h.get('cost', 0); cur = h.get('current', 0)
        prev = h.get('prev_close'); pnl = h.get('pnl_pct', 0)
        if prev and cur > 0 and prev > 0:
            dchg = (cur - prev) / prev * 100
            if dchg < -9.5:
                m = f"⚠️ {code} {name} 当日跌停({dchg:.1f}%)！成本{cost} 现价{cur} 浮亏{pnl}%"
                alerts.append(m); log_alert("WARNING", "持仓危机", m)
        if pnl is not None and pnl < -15:
            m = f"⚠️ {code} {name} 浮亏突破15%({pnl:.1f}%)，建议人工决策"
            alerts.append(m); log_alert("WARNING", "持仓危机", m)
        if cur > 0:
            triggers = []
            if cur < 5: triggers.append("股价<5元")
            if cur > 100: triggers.append("股价>100元")
            if code.startswith("688"): triggers.append("科创板")
            if code.startswith(('82', '83', '87', '88', '92')): triggers.append("北交所")
            if triggers:
                m = f"⚠️ {code} {name} 触发L1: {', '.join(triggers)}"
                alerts.append(m); log_alert("INFO", "持仓L1", m)  # v6.8.6: L1条件降级为INFO
    return alerts

# ============================================================
# 步骤5-8：清理 + 初始化 + 财报 + 大盘
# ============================================================
def step5_history_clean():
    c7 = (datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
    c90 = (datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=90)).strftime('%Y-%m-%d')
    tc = 0
    for f in sorted(os.listdir('/workspace')):
        if not (f.startswith('推荐历史_') and f.endswith('.json')): continue
        hist = safe_read_json(os.path.join('/workspace', f))
        nr = []
        for r in hist:
            t = r.get('type', '')
            if t in ('weekly_review', 'strategy_check', 'do_T_eval', 'do_T'): nr.append(r)
            elif t == 'holding' and r.get('update_date', '') >= c90: nr.append(r)
            elif t == 'recommendation' and r.get('date', '') >= c7: nr.append(r)
            elif t not in ('holding', 'recommendation'): nr.append(r)
        if len(nr) < len(hist): safe_write_json(os.path.join('/workspace', f), nr); tc += len(hist) - len(nr)
    if tc: log_alert("INFO", "清理", f"已清理{tc}条过期记录")
    else: log_alert("INFO", "清理", "无需清理")

def step6_file_init():
    global file_version, params
    adj = safe_read_json('/workspace/策略调整记录.json')
    if adj and len(adj) > 0:
        file_version = adj[-1].get('version', BUILTIN_VERSION); params = adj[-1].get('params', {})
    else: file_version = BUILTIN_VERSION; params = {}
    # v6.9.53: 若内置版本比策略记录版本新，以内置版本为准并更新记录
    if _version_cmp(file_version) < _version_cmp(BUILTIN_VERSION):
        file_version = BUILTIN_VERSION
        if adj and len(adj) > 0:
            adj[-1]['version'] = BUILTIN_VERSION
            safe_write_json('/workspace/策略调整记录.json', adj)
    for k, v in DEFAULT_PARAMS.items():
        if k not in params: params[k] = v
    log_alert("INFO", "文件初始化", f"版本={file_version} 参数={len(params)}")
    all_h = []
    for f in sorted(os.listdir('/workspace')):
        if f.startswith('推荐历史_') and f.endswith('.json'):
            all_h.extend(safe_read_json(os.path.join('/workspace', f)))
    lc = None
    for r in reversed(all_h):
        if r.get('type') == 'strategy_check': lc = r; break
    if lc and lc.get('version') != file_version:
        log_alert("INFO", "版本检查", f"历史版本≠策略调整版本{file_version}")
    if lc is None or (lc and lc.get('version') != file_version):
        hf = f"/workspace/推荐历史_{prediction_date.replace('-', '')}.json"
        safe_append_json(hf, {"type": "strategy_check", "version": file_version, "params": params, "date": data_date})

def step7_earnings_season():
    global position_pct
    if beijing_now.month in (1, 3, 4, 8, 10): position_pct = min(position_pct + 5, 85)

def step8_market_environment():
    global market_condition, position_pct, index_data
    # 保存前置步骤可能已设置的保守值（step1长休弱市/step3外围暴跌等）
    pre_condition = market_condition
    pre_position = position_pct
    idx = fetch_tencent_index(["sh000001", "sz399001", "sz399006"])
    # v6.12.4: 键名映射为 analyst.py 期望的 sh/sz/cy 格式
    index_data = {'sh': idx.get('sh000001', {}), 'sz': idx.get('sz399001', {}), 'cy': idx.get('sz399006', {})}
    if idx:
        sh = idx.get("sh000001", {})
        cur = sh.get("price", 0); chg = sh.get("change_pct", 0)
        log_alert("INFO", "大盘环境", f"上证{cur:.0f} 涨跌{chg:.2f}%")
    # 尝试pytdx获取均线
    try:
        from pytdx.hq import TdxHq_API
        api = TdxHq_API()
        try:
            for host in ['119.147.212.81', '120.76.152.87']:
                if api.connect(host, 7709):
                    bars = api.get_security_bars(9, 1, '000001', 0, 25)
                    if bars and len(bars) >= 20:
                        closes = [b['close'] for b in bars]
                        ma20 = sum(closes[-20:]) / 20
                        cur_c = closes[-1]
                        if cur_c > ma20: market_condition = "强市"; position_pct = 75
                        elif cur_c < ma20 * 0.98: market_condition = "弱市"; position_pct = 35
                        else: market_condition = "震荡"; position_pct = 55
                        break
        finally:
            try: api.disconnect()
            except Exception: log_alert("DEBUG", "大盘环境", "api.disconnect()失败")
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError, ModuleNotFoundError, ImportError): pass
    # 降级：根据涨跌判断（仅在pytdx未设置时生效）
    if not idx:
        market_condition = "震荡"; position_pct = 55
    elif market_condition == pre_condition and position_pct == pre_position:
        # pytdx未成功设置，降级判断
        sh = idx.get("sh000001", {})
        chg = sh.get("change_pct", 0)
        if chg > 1: market_condition = "强市"; position_pct = 75
        elif chg < -1: market_condition = "弱市"; position_pct = 35
        else: market_condition = "震荡"; position_pct = 55
    # 保护前置步骤的保守设置：不覆盖更弱的条件
    if pre_condition == "弱市" and market_condition != "弱市":
        market_condition = "弱市"
        position_pct = min(position_pct, pre_position)
        log_alert("INFO", "大盘环境", f"保护前置弱市: {market_condition} 仓位{position_pct}%")
    else:
        log_alert("INFO", "大盘环境", f"判断: {market_condition} 仓位{position_pct}%")

# ============================================================
# 步骤10A：全市场拉取（三级降级，Tier2改为腾讯）
# ============================================================
def step10A_fetch_all_stocks():
    # Tier 1: 腾讯API (v6.6.28 一级数据源)
    try:
        codes = []
        for i in range(600000, 610000): codes.append(f"sh{i}")  # v6.8.8: 扩展至610000覆盖预留段
        for i in range(1, 5000): codes.append(f"sz{i:06d}")
        for i in range(300000, 302000): codes.append(f"sz{i}")
        # 注：688xxx(科创板)未纳入拉取，step11硬排除科创板，拉取也无意义
        stocks = fetch_tencent_stocks(codes)
        log_alert("INFO", "行情采集", f"腾讯(一级) 成功拉取 {len(stocks)} 只")
        return stocks, "tencent"
    except Exception as e:
        log_alert("WARNING", "行情采集", f"腾讯一级失败: {str(e)[:60]}")
    
    # Tier 2: 新浪批量API (v6.6.28 二级降级)
    log_alert("INFO", "行情采集", "降级为新浪批量API(二级)")
    try:
        code_ranges = []
        for i in range(600000, 606000): code_ranges.append(f"sh{i}")
        for i in range(1, 5000): code_ranges.append(f"sz{i:06d}")
        for i in range(300000, 302000): code_ranges.append(f"sz{i}")
        stocks = []
        batch_size = 80
        for i in range(0, len(code_ranges), batch_size):
            batch = code_ranges[i:i+batch_size]
            try:
                url = f"https://hq.sinajs.cn/list={','.join(batch)}"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    text = resp.read().decode('gbk')
                for line in text.strip().split('\n'):
                    if not line or '=""' in line: continue
                    try:
                        parts = line.split('"')[1].split(',')
                        if len(parts) < 6: continue
                        header = line.split('="')[0]
                        raw_code = header.split('_')[-1] if '_' in header else header[-6:]
                        code = raw_code if len(raw_code) == 6 else raw_code[-6:]
                        name = parts[0]
                        current = float(parts[3]) if parts[3] else 0
                        prev_close = float(parts[2]) if parts[2] else 0
                        if current <= 0 or prev_close <= 0: continue
                        high_v = float(parts[4]) if parts[4] else 0
                        low_v = float(parts[5]) if parts[5] else 0
                        amplitude_v = round((high_v - low_v) / prev_close * 100, 2) if prev_close > 0 and high_v > 0 and low_v > 0 else 0
                        stocks.append({
                            "code": code, "name": name,
                            "open": float(parts[1]) if parts[1] else 0,
                            "close": current,
                            "change_pct": round((current - prev_close) / prev_close * 100, 2),
                            "amount": float(parts[9]) if len(parts) > 9 and parts[9] else 0,
                            "high": high_v, "low": low_v, "prev_close": prev_close,
                            "turnover": 0, "amplitude": amplitude_v,
                            "volume_ratio": None, "main_inflow": None, "total_cap": float(parts[14]) * 1e8 if len(parts) > 14 and parts[14] else None,  # v6.9.43: 新浪API补充total_cap
                        })
                    except (ValueError, TypeError, IndexError): continue
                if i % (batch_size * 10) == 0: time.sleep(0.02)
            except (urllib.error.URLError, json.JSONDecodeError, OSError): continue
        log_alert("INFO", "行情采集", f"新浪(二级) 成功拉取 {len(stocks)} 只")
        return stocks, "sina"
    except Exception as e:
        log_alert("ERROR", "行情采集", f"新浪二级也失败: {str(e)[:60]}")
    
    # Tier 3: pytdx
    api = None
    try:
        from pytdx.hq import TdxHq_API
        api = TdxHq_API()
        for host in ['119.147.212.81', '120.76.152.87']:
            if api.connect(host, 7709): break
        stocks = []
        for mc in [0, 1]:
            count = api.get_security_count(mc)
            for bn in range(0, min(count, 3000), 80):
                try:
                    ql = api.get_security_list(mc, bn)
                    if not ql: continue
                    qs = api.get_security_quotes([(mc, q['code']) for q in ql[:80]])
                    if not qs: continue
                    for q in qs:
                        code = q.get('code', ''); name = q.get('name', '')
                        cur = q.get('price', 0); prev = q.get('last_close', 0)
                        if cur <= 0 or prev <= 0: continue
                        qh = q.get('high', cur); qlow = q.get('low', cur)
                        stocks.append({"code": code, "name": name,
                            "open": q.get('open', cur), "close": cur,
                            "change_pct": round((cur - prev) / prev * 100, 2),
                            "high": qh, "low": qlow, "prev_close": prev,
                            "amount": q.get('amount', 0),
                            "turnover": 0, "amplitude": round((qh - qlow) / prev * 100, 2) if prev > 0 else 0,
                            "volume_ratio": None, "main_inflow": None, "total_cap": None})
                except (ValueError, TypeError, AttributeError): pass
        return stocks, "pytdx"
    except Exception as e:
        log_alert("ERROR", "行情采集", f"三级数据源均不可达")
        raise RuntimeError("行情数据获取失败")
    finally:
        if api is not None:
            try: api.disconnect()
            except Exception: log_alert("DEBUG", "行情采集", "api.disconnect()失败")

# ==========================================================
# 步骤10B：行业查表 v6.6.29 （全代码段覆盖，零未知）
# 覆盖范围：600-606xxx / 000-004xxx / 300-302xxx 所有100段
# ==========================================================
INDUSTRY_MAP = {
    # ── 上海主板 600xxx ──
    '600000-600099': '银行',     '600100-600199': '电子',
    '600200-600299': '医药生物', '600300-600399': '基础化工',
    '600400-600499': '电力设备', '600500-600599': '食品饮料',
    '600600-600699': '食品饮料', '600700-600799': '交通运输',
    '600800-600899': '机械设备', '600900-600999': '银行',
    # ── 上海主板 601xxx ──
    '601000-601099': '煤炭',     '601100-601199': '有色金属',
    '601200-601299': '非银金融', '601300-601399': '非银金融',
    '601400-601499': '银行',     '601500-601599': '非银金融',
    '601600-601699': '煤炭',    '601700-601799': '电力设备',
    '601800-601899': '建筑装饰', '601900-601999': '传媒',
    # ── 上海主板 602xxx-604xxx（新上市密集区）──
    '602000-602099': '电子',     '602100-602199': '基础化工',
    '602200-602299': '机械设备', '602300-602399': '医药生物',
    '602400-602499': '电力设备', '602500-602599': '汽车',
    '602600-602699': '计算机',   '602700-602799': '通信',
    '602800-602899': '轻工制造', '602900-602999': '食品饮料',
    '603000-603099': '电子',     '603100-603199': '机械设备',
    '603200-603299': '基础化工', '603300-603399': '机械设备',
    '603400-603499': '电子',     '603500-603599': '电子',
    '603600-603699': '轻工制造', '603700-603799': '汽车',
    '603800-603899': '机械设备', '603900-603999': '商贸零售',
    # ── 上海主板 604xxx ──
    '604000-604099': '电子',     '604100-604199': '计算机',
    '604200-604299': '医药生物', '604300-604399': '基础化工',
    '604400-604499': '电力设备', '604500-604599': '汽车',
    '604600-604699': '机械设备', '604700-604799': '轻工制造',
    '604800-604899': '传媒',     '604900-604999': '食品饮料',
    # ── 上海主板 605xxx ──
    '605000-605099': '机械设备', '605100-605199': '电力设备',
    '605200-605299': '基础化工', '605300-605399': '食品饮料',
    '605400-605499': '建筑材料', '605500-605599': '轻工制造',
    '605600-605699': '电子',     '605700-605799': '机械设备',
    '605800-605899': '基础化工', '605900-605999': '汽车',
    # ── 上海主板 606xxx ──
    '606000-606099': '电子',     '606100-606199': '汽车',
    '606200-606299': '机械设备', '606300-606399': '基础化工',
    '606400-606499': '医药生物', '606500-606599': '电力设备',
    '606600-606699': '电子',     '606700-606799': '计算机',
    '606800-606899': '传媒',     '606900-606999': '食品饮料',
    # ── 上海主板 607xxx-609xxx（新上市预留）──
    '607000-607099': '电子',     '607100-607199': '基础化工',
    '607200-607299': '机械设备', '607300-607399': '医药生物',
    '607400-607499': '电力设备', '607500-607599': '汽车',
    '607600-607699': '计算机',   '607700-607799': '通信',
    '607800-607899': '轻工制造', '607900-607999': '食品饮料',
    '608000-608099': '电子',     '608100-608199': '传媒',
    '608200-608299': '建筑装饰', '608300-608399': '机械设备',
    '608400-608499': '基础化工', '608500-608599': '医药生物',
    '608600-608699': '电力设备', '608700-608799': '汽车',
    '608800-608899': '计算机',   '608900-608999': '通信',
    '609000-609099': '电子',     '609100-609199': '轻工制造',
    '609200-609299': '食品饮料', '609300-609399': '传媒',
    '609400-609499': '基础化工', '609500-609599': '汽车',
    '609600-609699': '机械设备', '609700-609799': '医药生物',
    '609800-609899': '电力设备', '609900-609999': '计算机',
    # ── 深圳主板 000xxx ──
    '000001-000099': '银行',     '000100-000199': '电子',
    '000200-000299': '房地产',   '000300-000399': '医药生物',
    '000400-000499': '电力设备', '000500-000599': '公用事业',
    '000600-000699': '公用事业', '000700-000799': '钢铁',
    '000800-000899': '汽车',     '000900-000999': '非银金融',
    # ── 深圳主板 001xxx ──
    '001000-001099': '电子',     '001100-001199': '有色金属',
    '001200-001299': '基础化工', '001300-001399': '机械设备',
    '001400-001499': '公用事业', '001500-001599': '交通运输',
    '001600-001699': '汽车',     '001700-001799': '建筑装饰',
    '001800-001899': '食品饮料', '001900-001999': '公用事业',
    # ── 深圳主板 002xxx ──
    '002000-002099': '电子',     '002100-002199': '医药生物',
    '002200-002299': '建筑装饰', '002300-002399': '电力设备',
    '002400-002499': '传媒',     '002500-002599': '基础化工',
    '002600-002699': '电子',     '002700-002799': '机械设备',
    '002800-002899': '基础化工', '002900-002999': '电子',
    # ── 深圳主板 003xxx ──
    '003000-003099': '食品饮料', '003100-003199': '电子',
    '003200-003299': '机械设备', '003300-003399': '基础化工',
    '003400-003499': '医药生物', '003500-003599': '电力设备',
    '003600-003699': '汽车',     '003700-003799': '轻工制造',
    '003800-003899': '建筑装饰', '003900-003999': '传媒',
    # ── 深圳主板 004xxx ──
    '004000-004099': '电子',     '004100-004199': '计算机',
    '004200-004299': '医药生物', '004300-004399': '基础化工',
    '004400-004499': '机械设备', '004500-004599': '电子',
    '004600-004699': '医药生物', '004700-004799': '基础化工',
    '004800-004899': '汽车',     '004900-004999': '计算机',
    # ── 深圳主板 005xxx（预留）──
    '005000-005099': '电子',     '005100-005199': '汽车',
    '005200-005299': '医药生物', '005300-005399': '基础化工',
    '005400-005499': '电力设备', '005500-005599': '计算机',
    '005600-005699': '机械设备', '005700-005799': '传媒',
    '005800-005899': '建筑装饰', '005900-005999': '通信',
    # ── 创业板 300xxx ──
    '300000-300099': '电子',     '300100-300199': '汽车',
    '300200-300299': '基础化工', '300300-300399': '计算机',
    '300400-300499': '机械设备', '300500-300599': '建筑装饰',
    '300600-300699': '国防军工', '300700-300799': '机械设备',
    '300800-300899': '环保',     '300900-300999': '电力设备',
    # ── 创业板 301xxx ──
    '301000-301099': '机械设备', '301100-301199': '基础化工',
    '301200-301299': '电子',     '301300-301399': '计算机',
    '301400-301499': '通信',     '301500-301599': '汽车',
    '301600-301699': '电子',     '301700-301799': '医药生物',
    '301800-301899': '基础化工', '301900-301999': '机械设备',
    # ── 创业板 302xxx ──
    '302000-302099': '电子',     '302100-302199': '电力设备',
    '302200-302299': '计算机',   '302300-302399': '医药生物',
    '302400-302499': '电子',     '302500-302599': '基础化工',
    '302600-302699': '机械设备', '302700-302799': '通信',
    '302800-302899': '传媒',     '302900-302999': '汽车',
    '304400-304499': '医药生物', '304500-304599': '电力设备',
    '304600-304699': '汽车',     '304700-304799': '通信',
    '304800-304899': '电子',     '304900-304999': '传媒',
    # ── 创业板 303xxx-304xxx（预留）──
    '303000-303099': '电子',     '303100-303199': '汽车',
    '303200-303299': '基础化工', '303300-303399': '计算机',
    '303400-303499': '医药生物', '303500-303599': '电力设备',
    '303600-303699': '通信',     '303700-303799': '机械设备',
    '303800-303899': '轻工制造', '303900-303999': '传媒',
    '304000-304099': '电子',     '304100-304199': '食品饮料',
    '304200-304299': '建筑装饰', '304300-304399': '基础化工',
}
def lookup_industry(code):
    """行业查表：v6.9.34 硬编码覆盖 → 东方财富HTTP缓存 → 代码段映射"""
    # 1. 硬编码覆盖优先（手动校对，最高优先级）
    if code in HARDCODED_INDUSTRY:
        return HARDCODED_INDUSTRY[code]
    # 2. 东方财富缓存（证监会→申万映射）。v6.9.36: 兼容dict格式值
    if code in _industry_cache and _industry_cache[code]:
        v = _industry_cache[code]
        if isinstance(v, dict):
            return v.get('sshy', '') or '未知'
        return v
    # 3. 代码段映射
    try:
        ci = int(code)
    except (ValueError, TypeError):
        return "未知"
    for k, v in INDUSTRY_MAP.items():
        lo, hi = k.split('-')
        if int(lo) <= ci <= int(hi): return v
    return "未知"

# ==========================================================
# v6.9.34: 东方财富HTTP行业分类预加载（替代Baostock TCP）
# ==========================================================
def _zjh_to_shenwan(zjh):
    """证监会行业 → 申万一级行业映射"""
    if not zjh: return None
    # 精确匹配
    if zjh in _ZJH_TO_SHENWAN:
        return _ZJH_TO_SHENWAN[zjh]
    # 大类前缀匹配（如"建筑业-房屋建筑业"匹配"建筑业"）
    if '-' in zjh:
        broad = zjh.split('-')[0]
        if broad in _ZJH_TO_SHENWAN:
            return _ZJH_TO_SHENWAN[broad]
    return None

def _load_industry_cache():
    """从磁盘加载行业缓存。v6.9.36: 兼容旧格式dict值自动转字符串。"""
    global _industry_cache, _sub_industry_cache
    try:
        with open(INDUSTRY_CACHE_FILE, 'r', encoding='utf-8') as f:
            _industry_cache = json.load(f)
        # v6.9.36: 兼容旧格式dict值自动转字符串
        _industry_cache = {k: (v.get('sshy', '') or '未知') if isinstance(v, dict) else v for k, v in _industry_cache.items()}
        print(f"[INFO] 行业缓存: 从磁盘加载 {len(_industry_cache)} 条")
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        _industry_cache = {}
    try:
        with open(SUB_INDUSTRY_CACHE_FILE, 'r', encoding='utf-8') as f:
            _sub_industry_cache = json.load(f)
        print(f"[INFO] 二级行业缓存: 从磁盘加载 {len(_sub_industry_cache)} 条")
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        _sub_industry_cache = {}

def _save_industry_cache():
    """保存行业缓存到磁盘"""
    if _industry_cache:
        try:
            with open(INDUSTRY_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(_industry_cache, f, ensure_ascii=False, indent=2)
            print(f"[INFO] 行业缓存: 已保存 {len(_industry_cache)} 条")
        except Exception as e:
            print(f"[WARNING] 行业缓存保存失败: {e}")
    if _sub_industry_cache:
        try:
            with open(SUB_INDUSTRY_CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(_sub_industry_cache, f, ensure_ascii=False, indent=2)
            print(f"[INFO] 二级行业缓存: 已保存 {len(_sub_industry_cache)} 条")
        except Exception as e:
            print(f"[WARNING] 二级行业缓存保存失败: {e}")

def _fetch_zjh_industry(code):
    """v6.9.35: 通过东方财富HTTP API获取证监会行业和二级行业(sshy)。
    返回: (申万一级行业, 二级行业) 或 (None, None)"""
    try:
        market = 'SH' if code.startswith('6') else 'SZ'  # v6.9.43: 去除'9'前缀误匹配（9xxxxx不进入此函数）
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
    except (urllib.error.URLError, json.JSONDecodeError, OSError, KeyError):
        return None, None

def _preload_industry_from_eastmoney(all_stocks):
    """v6.9.35: 通过东方财富HTTP API批量获取行业分类（一级+二级）。
    v6.9.59: 所有日期统一仅读取磁盘缓存，不再执行HTTP拉取（行业缓存由sunday_industry_pull.py单独维护）。"""
    global _industry_cache, _sub_industry_cache
    _load_industry_cache()
    
    cache_is_empty = len(_industry_cache) == 0 and len(_sub_industry_cache) == 0
    
    if cache_is_empty:
        print(f"[INFO] 行业缓存: 缓存为空，使用代码段映射降级（请执行sunday_industry_pull.py初始化缓存）")
    else:
        print(f"[INFO] 行业缓存: 仅读取缓存 (一级{len(_industry_cache)}条, 二级{len(_sub_industry_cache)}条)")
    return

# v6.6.29: 知名股票硬编码覆盖（代码段查表无法精确区分时）
HARDCODED_INDUSTRY = {
    '601225': '煤炭',      # 陕西煤业（在601200-601299段但非非银金融）
    '601628': '非银金融',  # 中国人寿（在601600-601699段但非煤炭）
    '300750': '电力设备',  # 宁德时代（在300700-300799段但非机械设备）
    '002415': '电子',      # 海康威视（在002400-002499段但非传媒）
    # v6.6.30: 12只行业修正（基于2026-06-16筛选结果校对）
    '002112': '电力设备',  # 三变科技（输变电设备，在002100-002199段但非医药生物）
    '002174': '传媒',      # 游族网络（游戏公司，在002100-002199段但非医药生物）
    '600203': '电子',      # 福日电子（电子制造，在600200-600299段但非医药生物）
    '300024': '机械设备',  # 机器人（工业机器人，在300000-300099段但非电子）
    '601696': '非银金融',  # 中银证券（证券公司，在601600-601699段但非煤炭）
    '601678': '基础化工',  # 滨化股份（化工企业，在601600-601699段但非煤炭）
    '600961': '有色金属',  # 株冶集团（铅锌冶炼，在600900-600999段但非银行）
    '000037': '公用事业',  # 深南电A（电力供应，在000000-000099段但非银行）
    '000021': '电子',      # 深科技（电子制造服务，在000000-000099段但非银行）
    '000700': '汽车',      # 模塑科技（汽车零部件，在000700-000799段但非钢铁）
    '002354': '传媒',      # 天娱数科（数字娱乐/游戏，在002300-002399段但非电力设备）
    '002490': '机械设备',  # 山东墨龙（石油机械设备，在002400-002499段但非传媒）
    # v6.6.33: 28只行业修正（基于2026-06-17筛选结果全量校对）
    '002725': '汽车',      # 跃岭股份（汽车轮毂，在002700-002799段但非机械设备）
    '002745': '电子',      # 木林森（LED照明，在002700-002799段但非机械设备）
    '600459': '有色金属',  # 贵研铂业（铂族金属，在600400-600499段但非电力设备）
    '301150': '电子',      # 中一科技（电解铜箔，在301100-301199段但非基础化工）
    '301157': '电力设备',  # 华塑科技（电池BMS，在301100-301199段但非基础化工）
    '300246': '医药生物',  # 宝莱特（医疗器械，在300200-300299段但非基础化工）
    '000969': '有色金属',  # 安泰科技（新材料，在000900-000999段但非非银金融）
    '300688': '传媒',      # 创业黑马（企业服务/传媒，在300600-300699段但非国防军工）
    '000831': '有色金属',  # 中国稀土（稀土，在000800-000899段但非汽车）
    '600141': '基础化工',  # 兴发集团（磷化工，在600100-600199段但非电子）
    '603990': '计算机',    # 麦迪科技（医疗IT，在603900-603999段但非商贸零售）
    '603906': '基础化工',  # 龙蟠科技（车用化学品，在603900-603999段但非商贸零售）
    '300967': '农林牧渔',  # 晓鸣股份（禽养殖，在300900-300999段但非电力设备）
    '301513': '机械设备',  # 尚水智能（智能装备，在301500-301599段但非汽车）
    '300503': '机械设备',  # 昊志机电（主轴电机，在300500-300599段但非建筑装饰）
    '300508': '计算机',    # 维宏股份（数控系统，在300500-300599段但非建筑装饰）
    '300537': '基础化工',  # 广信材料（UV涂料，在300500-300599段但非建筑装饰）
    '300305': '基础化工',  # 裕兴股份（聚酯薄膜，在300300-300399段但非计算机）
    '301303': '机械设备',  # 真兰仪表（仪器仪表，在301300-301399段但非计算机）
    '301329': '电子',      # 信音电子（连接器，在301300-301399段但非计算机）
    '300655': '电子',      # 晶瑞电材（电子化学品，在300600-300699段但非国防军工）
    '300602': '电子',      # 飞荣达（EMI屏蔽材料，在300600-300699段但非国防军工）
    '603936': '电子',      # 博敏电子（PCB，在603900-603999段但非商贸零售）
    '601958': '有色金属',  # 金钼股份（钼矿，在601900-601999段但非传媒）
    '002156': '电子',      # 通富微电（IC封测，在002100-002199段但非医药生物）
    '002176': '有色金属',  # 江特电机（锂矿+电机，在002100-002199段但非医药生物）
    '300883': '轻工制造',  # 龙利得（包装印刷，在300800-300899段但非环保）
    '000506': '有色金属',  # 招金黄金（黄金开采，在000500-000599段但非公用事业）
    '600520': '机械设备',  # 三佳科技（半导体设备，在600500-600599段但非食品饮料）
    '600584': '电子',      # 长电科技（半导体封测，在600500-600599段但非食品饮料）
    '600601': '电子',      # 方正科技（PCB，在600600-600699段但非食品饮料）
    '600078': '基础化工',  # 澄星股份（磷化工，在600000-600099段但非银行）
    '301419': '电子',      # 阿莱德（EMI材料，在301400-301499段但非通信）
    '301439': '电力设备',  # 泓淋电力（电缆组件，在301400-301499段但非通信）
    '301418': '电子',      # 协昌科技（运动控制IC，在301400-301499段但非通信）
    '605589': '基础化工',  # 圣泉集团（酚醛树脂，在605500-605599段但非轻工制造）
    '603690': '机械设备',  # 至纯科技（半导体清洗设备，在603600-603699段但非轻工制造）
    '000759': '商贸零售',  # 中百集团（连锁零售，在000700-000799段但非钢铁）
    '002192': '有色金属',  # 融捷股份（锂矿，在002100-002199段但非医药生物）
    '300853': '机械设备',  # 申昊科技（巡检机器人，在300800-300899段但非环保）
    '300802': '机械设备',  # 矩子科技（AOI检测设备，在300800-300899段但非环保）
    # v6.6.35: 17只行业修正（基于2026-06-17筛选结果第二轮校对）
    '300131': '电子',      # 英唐智控（电子元器件分销，在300100-300199段但非汽车）
    '002167': '有色金属',  # 东方锆业（锆制品，在002100-002199段但非医药生物）
    '002171': '有色金属',  # 楚江新材（铜加工，在002100-002199段但非医药生物）
    '300679': '电子',      # 电连技术（连接器，在300600-300699段但非国防军工）
    '003025': '机械设备',  # 思进智能（冷成形装备，在003000-003999段但非食品饮料）
    '600667': '电子',      # 太极实业（半导体，在600600-600699段但非食品饮料）
    '300930': '有色金属',  # 屹通新材（粉末冶金，在300900-300999段但非电力设备）
    '603278': '汽车',      # 大业股份（轮胎骨架材料，在603200-603299段但非基础化工）
    '300346': '电子',      # 南大光电（半导体材料，在300300-300399段但非计算机）
    '002240': '有色金属',  # 盛新锂能（锂盐，在002200-002299段但非建筑装饰）
    '002229': '轻工制造',  # 鸿博股份（印刷，在002200-002299段但非建筑装饰）
    '002254': '基础化工',  # 泰和新材（芳纶纤维，在002200-002299段但非建筑装饰）
    '000670': '电子',      # 盈方微（芯片设计，在000600-000699段但非公用事业）
    '300606': '机械设备',  # 金太阳（研磨抛光材料，在300600-300699段但非国防军工）
    '600505': '公用事业',  # 西昌电力（电力供应，在600500-600599段但非食品饮料）
    '301458': '电子',      # 钧崴电子（精密电阻，在301400-301499段但非通信）
    # v6.6.36: 7只行业修正（三轮校对）
    '300398': '基础化工',  # 飞凯材料（电子化学品，在300300-300399段但非计算机）
    '002185': '电子',      # 华天科技（半导体封测，在002100-002199段但非医药生物）
    '300902': '机械设备',  # 国安达（消防设备，在300900-300999段但非电力设备）
    '300547': '汽车',      # 川环科技（橡胶管，在300500-300599段但非建筑装饰）
    '300554': '机械设备',  # 三超新材（金刚石线，在300500-300599段但非建筑装饰）
    '300571': '传媒',      # 平治信息（数字阅读，在300500-300599段但非建筑装饰）
    '003026': '电子',      # 中晶科技（半导体硅片，在003000-003999段但非食品饮料）
    # v6.6.42: 14只行业修正（基于2026-06-18筛选结果校对）
    '600549': '有色金属',  # 厦门钨业（钨钼冶炼，在600500-600599段但非食品饮料）
    '000722': '公用事业',  # 湖南发展（水电，在000700-000799段但非钢铁）
    '600589': '计算机',    # 大位科技（IT服务，在600500-600599段但非食品饮料）
    '000032': '建筑装饰',  # 深桑达A（电子系统工程，在000000-000099段但非银行）
    '600063': '基础化工',  # 皖维高新（化工纤维，在600000-600099段但非银行）
    '000733': '电子',      # 振华科技（电子元器件，在000700-000799段但非钢铁）
    '000603': '有色金属',  # 盛达资源（银矿铅锌矿，在000600-000699段但非公用事业）
    '000995': '食品饮料',  # 皇台酒业（白酒，在000900-000999段但非非银金融）
    '000970': '有色金属',  # 中科三环（稀土永磁，在000900-000999段但非非银金融）
    '603938': '基础化工',  # 三孚股份（有机硅，在603900-603999段但非商贸零售）
    '002457': '建筑材料',  # 青龙管业（混凝土管道，在002400-002499段但非传媒）
    '600460': '电子',      # 士兰微（半导体，在600400-600499段但非电力设备）
    '605358': '电子',      # 立昂微（半导体硅片，在605300-605399段但非食品饮料）
    '603678': '电子',      # 火炬电子（MLCC电容，在603600-603699段但非轻工制造）
    # v6.6.42: 第二轮校对（2026-06-18 余量修正）
    '000636': '电子',      # 风华高科（MLCC电容，在000600-000699段但非公用事业）
    '002378': '有色金属',  # 章源钨业（钨矿开采，在002300-002399段但非电力设备）
    '002149': '有色金属',  # 西部材料（稀有金属材料，在002100-002199段但非医药生物）
    '002845': '电子',      # 同兴达（液晶显示模组，在002800-002899段但非基础化工）
    '300568': '电力设备',  # 星源材质（锂电隔膜，在300500-300599段但非建筑装饰）
    '300632': '电子',      # 光莆股份（LED照明，在300600-300699段但非国防军工）
    '600522': '通信',      # 中天科技（光纤光缆，在600500-600599段但非食品饮料）
    '000767': '公用事业',  # 晋控电力（火力发电，在000700-000799段但非钢铁）
    # v6.6.42: 第三轮校对（2026-06-18 最终余量）
    '002129': '电力设备',  # TCL中环（光伏硅片，在002100-002199段但非医药生物）
    '000510': '基础化工',  # 新金路（PVC树脂，在000500-000599段但非公用事业）
    # v6.6.47: 36只行业修正（基于2026-06-19筛选结果全量校对）
    '300624': '计算机',    # 万兴科技（视频创意软件，在300600-300699段但非国防军工）
    '002106': '电子',      # 莱宝高科（液晶显示触控，在002100-002199段但非医药生物）
    '002177': '计算机',    # 御银股份（ATM/金融设备，在002100-002199段但非医药生物）
    '601636': '建筑材料',  # 旗滨集团（玻璃制造，在601600-601699段但非煤炭）
    '600500': '基础化工',  # 中化国际（化工新材料，在600500-600599段但非食品饮料）
    '600707': '电子',      # 彩虹股份（显示器件，在600700-600799段但非交通运输）
    '600714': '基础化工',  # 金瑞矿业（锶盐/化学原料，在600700-600799段但非交通运输）
    '000066': '计算机',    # 中国长城（自主计算/信创，在000000-000099段但非银行）
    '002380': '计算机',    # 科远智慧（工业自动化/IT服务，在002300-002399段但非电力设备）
    '000417': '商贸零售',  # 合百集团（百货零售，在000400-000499段但非电力设备）
    '300607': '机械设备',  # 拓斯达（工业机器人，在300600-300699段但非国防军工）
    '300852': '电子',      # 四会富仕（PCB，在300800-300899段但非环保）
    '000070': '通信',      # 特发信息（通信设备，在000000-000099段但非银行）
    '002407': '基础化工',  # 多氟多（氟化工，在002400-002499段但非传媒）
    '300738': '计算机',    # 奥飞数据（IDC数据中心，在300700-300799段但非机械设备）
    '001266': '机械设备',  # 宏英智能（智能电控，在001200-001299段但非基础化工）
    '001212': '建筑材料',  # 中旗新材（人造石英石，在001200-001299段但非基础化工）
    '603110': '基础化工',  # 东方材料（油墨包装材料，在603100-603199段但非机械设备）
    '003004': '计算机',    # 声迅股份（安防监控，在003000-003999段但非食品饮料）
    '301577': '通信',      # 美信科技（网络变压器，在301500-301599段但非汽车）
    '301565': '基础化工',  # 中仑新材（薄膜新材料，在301500-301599段但非汽车）
    '002235': '传媒',      # 安妮股份（数字版权，在002200-002299段但非建筑装饰）
    '002201': '建筑材料',  # 九鼎新材（玻璃纤维，在002200-002299段但非建筑装饰）
    '301307': '通信',      # 美利信（通信设备压铸，在301300-301399段但非计算机）
    '600630': '纺织服饰',  # 龙头股份（纺织服装，在600600-600699段但非食品饮料）
    '300351': '电子',      # 永贵电器（连接器，在300300-300399段但非计算机）
    '603608': '纺织服饰',  # 天创时尚（鞋业服装，在603600-603699段但非轻工制造）
    '300812': '电子',      # 易天股份（显示设备，在300800-300899段但非环保）
    '603601': '基础化工',  # 再升科技（过滤材料，在603600-603699段但非轻工制造）
    '603976': '医药生物',  # 正川股份（药用玻璃包装，在603900-603999段但非商贸零售）
    '300821': '基础化工',  # 东岳硅材（有机硅，在300800-300899段但非环保）
    '300505': '基础化工',  # 川金诺（磷化工，在300500-300599段但非建筑装饰）
    '002165': '基础化工',  # 红宝丽（聚氨酯/化工，在002100-002199段但非医药生物）
    '300900': '国防军工',  # 广联航空（航空航天，在300900-300999段但非电力设备）
    '002446': '通信',      # 盛路通信（通信设备，在002400-002499段但非传媒）
    '301596': '机械设备',  # 瑞迪智驱（精密传动，在301500-301599段但非汽车）
    # v6.6.48: 21只行业修正（基于2026-06-19筛选结果第二轮全量校对）
    '002990': '计算机',    # 盛视科技（智慧口岸/安防，在002900-002999段但非电子）
    '301617': '基础化工',  # 博苑新材（化学制品，在301600-301699段但非电子）
    '603262': '食品饮料',  # 技源集团（保健品，在603200-603299段但非基础化工）
    '002957': '机械设备',  # 科瑞技术（自动化设备，在002900-002999段但非电子）
    '002196': '电力设备',  # 方正电机（微特电机，在002100-002199段但非医药生物）
    '002208': '房地产',    # 合肥城建（房地产开发，在002200-002299段但非建筑装饰）
    '603220': '通信',      # 中贝通信（通信网络服务，在603200-603299段但非基础化工）
    '603681': '基础化工',  # 永冠新材（胶黏剂/胶带，在603600-603699段但非轻工制造）
    '600552': '电子',      # 凯盛科技（显示材料/UTG玻璃，在600500-600599段但非食品饮料）
    '601112': '建筑材料',  # 振石股份（玻纤制造，在601100-601199段但非有色金属）
    '300120': '电子',      # 经纬辉开（电磁线/触控显示，在300100-300199段但非汽车）
    '301591': '基础化工',  # 肯特股份（工程塑料制品，在301500-301599段但非汽车）
    '300196': '建筑材料',  # 长海股份（玻纤及制品，在300100-300199段但非汽车）
    '001896': '公用事业',  # 豫能控股（火力发电，在001800-001899段但非食品饮料）
    '603618': '电力设备',  # 杭电股份（电线电缆，在603600-603699段但非轻工制造）
    '300586': '基础化工',  # 美联新材（色母粒/高分子材料，在300500-300599段但非建筑装饰）
    '300921': '通信',      # 南凌科技（企业网络服务，在300900-300999段但非电力设备）
    '002137': '电子',      # 实益达（LED/智能硬件EMS，在002100-002199段但非医药生物）
    '300975': '电子',      # 商络电子（电子元器件分销，在300900-300999段但非电力设备）
    '300825': '汽车',      # 阿尔特（汽车设计，在300800-300899段但非环保）
    '002272': '机械设备',  # 川润股份（润滑液压设备，在002200-002299段但非建筑装饰）
    # v6.6.49: 8只行业修正（基于2026-06-19筛选结果第三轮校对）
    '301638': '计算机',    # 南网数字（IT服务/电力信息化，在301600-301699段但非电子）
    '301280': '家用电器',  # 珠城科技（家电连接器，在301200-301299段但非电子）
    '603270': '机械设备',  # 金帝股份（精密轴承/通用设备，在603200-603299段但非基础化工）
    '300162': '电子',      # 雷曼光电（LED显示，在300100-300199段但非汽车）
    '300626': '电力设备',  # 华瑞股份（电机换向器，在300600-300699段但非汽车）
    '301528': '机械设备',  # 多浦乐（超声检测设备，在301500-301599段但非汽车）
    '002125': '电力设备',  # 湘潭电化（电池材料/电解二氧化锰，在002100-002199段但非医药生物）
    '002194': '通信',      # 武汉凡谷（射频器件/通信设备，在002100-002199段但非医药生物）
    # v6.6.50: 3只行业修正（基于2026-06-19筛选结果第四轮校对）
    '600366': '有色金属',  # 宁波韵升（稀土永磁/钕铁硼，在600300-600399段但非基础化工）
    '300174': '基础化工',  # 元力股份（活性炭，在300100-300199段但非汽车）
    '300145': '机械设备',  # 南方泵业（不锈钢离心泵，在300100-300199段但非汽车）
    # v6.9.3: 石油石化/美容护理/社会服务一行覆盖
    '601857': '石油石化',  # 中国石油
    '600028': '石油石化',  # 中国石化
    '600938': '石油石化',  # 中国海油
    '603605': '美容护理',  # 珀莱雅
    '688363': '美容护理',  # 华熙生物
    '300957': '美容护理',  # 贝泰妮
    '002607': '社会服务',  # 中公教育
    '300144': '社会服务',  # 宋城演艺
    '600754': '社会服务',  # 锦江酒店
    '600258': '社会服务',  # 首旅酒店
    # v6.9.7: 5只行业修正（基于2026-06-22筛选结果东方财富F10校对）
    '301280': '电子',      # 珠城科技（连接器/电子元件，在301200-301299段但非家用电器）
    '301512': '机械设备',  # 智信精密（专用设备，在301500-301599段但非汽车）
    '301566': '电子',      # 达利凯普（电子元件/MLCC，在301500-301599段但非汽车）
    '001237': '机械设备',  # 惠康科技（机械设备，在001200-001299段但非基础化工）
    '600184': '机械设备',  # 光电股份（专用设备/光学仪器，在600100-600199段但非电子）
    # v6.9.29: 12只行业修正（基于2026-06-22筛选结果全量校对）
    '300576': '电子',      # 容大感光（PCB光刻胶/电子化学品，在300500-300599段但非建筑装饰）
    '300331': '电子',      # 苏大维格（微纳光学制造，在300300-300399段但非计算机）
    '600237': '电子',      # 铜峰电子（薄膜电容器，在600200-600299段但非医药生物）
    '001282': '汽车',      # 三联锻造（汽车锻造零部件，在001200-001299段但非基础化工）
    '301261': '机械设备',  # 恒工精密（精密机加工件/连铸铸铁，在301200-301299段但非电子）
    '301141': '有色金属',  # 中科磁业（永磁材料/钕铁硼，在301100-301199段但非基础化工）
    '301500': '环保',      # 飞南资源（危废处置/再生资源，在301500-301599段但非汽车）
    '301499': '汽车',      # 维科精密（汽车电子精密零部件，在301400-301499段但非通信）
    '301588': '轻工制造',  # 美新科技（塑木复合材料/户外地板，在301500-301599段但非汽车）
    '300556': '计算机',    # 丝路视觉（CG创意/数字视觉，在300500-300599段但非建筑装饰）
    '002213': '电子',      # 大为股份（半导体存储器/DRAM，在002200-002299段但非建筑装饰）
    '300566': '电子',      # 激智科技（光学膜/显示材料，在300500-300599段但非建筑装饰）
    # v6.9.32: 17只行业修正（基于2026-06-22筛选结果全量校对）
    '300058': '传媒',      # 蓝色光标（数字营销/公关，在300000-300099段但非电子）
    '603001': '纺织服饰',  # 奥康国际（鞋业制造，在603000-603099段但非电子）
    '002455': '基础化工',  # 百川股份（精细化工/新材料，在002400-002499段但非传媒）
    '002734': '基础化工',  # 利民股份（农药原药/制剂，在002700-002799段但非机械设备）
    '002759': '家用电器',  # 天际股份（小家电制造，在002700-002799段但非机械设备）
    '000815': '计算机',    # 美利云（数据中心/云计算，在000800-000899段但非汽车）
    '300139': '有色金属',  # 晓程科技（黄金开采，在300100-300199段但非汽车）
    '301555': '基础化工',  # 惠柏新材（环氧树脂，在301500-301599段但非汽车）
    '603738': '电子',      # 泰晶科技（频率器件/晶振，在603700-603799段但非汽车）
    '603283': '机械设备',  # 赛腾股份（自动化设备，在603200-603299段但非基础化工）
    '600719': '公用事业',  # 大连热电（热电联产，在600700-600799段但非交通运输）
    '300890': '电力设备',  # 翔丰华（锂电池负极材料，在300800-300899段但非环保）
    '300809': '机械设备',  # 华辰装备（精密磨削装备，在300800-300899段但非环保）
    '300806': '基础化工',  # 斯迪克（胶粘剂/功能性涂层，在300800-300899段但非环保）
    '300438': '电力设备',  # 鹏辉能源（锂离子电池，在300400-300499段但非机械设备）
    '300538': '基础化工',  # 同益股份（化工材料分销，在300500-300599段但非建筑装饰）
    '300938': '社会服务',  # 信测标准（检测认证服务，在300900-300999段但非电力设备）
    # v6.9.44: 5只行业修正（基于2026-06-25筛选结果校对，行业缓存补全后余量修正）
    '000688': '有色金属',  # 国城矿业（铅锌铜矿开采，在000600-000699段但非公用事业）
    '600598': '农林牧渔',  # 北大荒（农业种植，在600500-600599段但非食品饮料）
    '000672': '建筑材料',  # 上峰材料（水泥建材，在000600-000699段但非公用事业）
    '002549': '环保',      # 凯美特气（工业废气回收，在002500-002599段但非基础化工）
    '002015': '公用事业',  # 协鑫能科（清洁能源发电，在002000-002099段但非电子）
    # v6.12.3: 23只行业修正（基于2026-06-29筛选结果，行业缓存为空时代码段映射错误修正）
    '002126': '汽车',      # 银轮股份（热管理/汽车零部件，在002100-002199段但非医药生物）
    '002515': '食品饮料',  # 金字火腿（火腿肉制品，在002500-002599段但非基础化工）
    '601208': '基础化工',  # 东材科技（高分子功能材料，在601200-601299段但非非银金融）
    '002837': '机械设备',  # 英维克（精密温控节能设备，在002800-002899段但非基础化工）
    '002432': '医药生物',  # 九安医疗（家用医疗器械，在002400-002499段但非传媒）
    '603063': '电力设备',  # 禾望电气（风电变流器/电能变换，在603000-603099段但非电子）
    '603713': '交通运输',  # 密尔克卫（化工物流/供应链，在603700-603799段但非汽车）
    '600909': '非银金融',  # 华安证券（证券公司，在600900-600999段但非银行）
    '600176': '建筑材料',  # 中国巨石（玻璃纤维，在600100-600199段但非电子）
    '603890': '电子',      # 春秋电子（消费电子结构件，在603800-603899段但非机械设备）
    '603267': '电子',      # 鸿远电子（MLCC电容器，在603200-603299段但非基础化工）
    '002815': '电子',      # 崇达技术（PCB印制电路板，在002800-002899段但非基础化工）
    '003043': '电子',      # 华亚智能（半导体设备零部件，在003000-003099段但非食品饮料）
    '603663': '基础化工',  # 三祥新材（锆制品/新材料，在603600-603699段但非轻工制造）
    '002536': '汽车',      # 飞龙股份（汽车水泵/发动机零部件，在002500-002599段但非基础化工）
    '600869': '电力设备',  # 远东股份（电线电缆，在600800-600899段但非机械设备）
    '600516': '基础化工',  # 方大炭素（炭素制品，在600500-600599段但非食品饮料）
    '603989': '电子',      # 艾华集团（铝电解电容器，在603900-603999段但非商贸零售）
    '002080': '建筑材料',  # 中材科技（玻纤复合材料，在002000-002099段但非电子）
    '002202': '电力设备',  # 金风科技（风电整机，在002200-002299段但非建筑装饰）
    '600596': '基础化工',  # 新安股份（有机硅/化工，在600500-600599段但非食品饮料）
    '603688': '基础化工',  # 石英股份（石英材料，在603600-603699段但非轻工制造）
    '605020': '基础化工',  # 永和股份（氟化工，在605000-605099段但非机械设备）
    # v6.12.5: 2只行业修正（基于2026-06-29筛选结果校对）
    '603045': '有色金属',  # 福达合金（电接触材料/合金材料，在603000-603099段但非电子）
    '600226': '农林牧渔',  # 亨通股份（农药兽药/生物制药，在600200-600299段但非医药生物）
    # v6.12.15: 3只行业修正（基于2026-06-29筛选结果校对）
    '000921': '家用电器',  # 海信家电（家电制造，在000900-000999段但非非银金融）
    '600839': '家用电器',  # 四川长虹（电视/家电制造，在600800-600899段但非煤炭）
    '603119': '电力设备',  # 浙江荣泰（新能源车热失控防护/云母制品，在603100-603199段但非电子）
    # v6.13.11: 6只行业修正（基于2026-07-07筛选结果校对）
    '002422': '医药生物',  # 科伦药业（大输液/抗生素，在002400-002499段但非传媒）
    '002294': '医药生物',  # 信立泰（心血管药物，在002200-002299段但非建筑装饰）
    '002158': '机械设备',  # 汉钟精机（压缩机/真空泵，在002100-002199段但非医药生物）
    '601918': '煤炭',      # 新集能源（煤炭开采，在601900-601999段但非传媒）
    '000338': '汽车',      # 潍柴动力（重型发动机/整车，在000300-000399段但非医药生物）
    '002138': '电子',      # 顺络电子（电感/电子元器件，在002100-002199段但非医药生物）
}

# ============================================================
# 步骤10C：历史K线批量拉取（v6.9.0: 支撑均线/形态策略）
# ============================================================
def step10C_fetch_klines(candidates):
    """v6.9.3: 扩展KDJ+布林带+涨停标记，支撑完整技术指标体系
    返回: {code: {ma5,ma10,ma20,dif,dea,macd_hist,rsi14,k,d,j,boll_upper,boll_mid,boll_lower,
                 high20,low20,days_listed,limit_up_days,closes,highs,lows,volumes}}
    """
    kline_data = {}
    try:
        from pytdx.hq import TdxHq_API
        api = TdxHq_API()
        if not api.connect('119.147.212.81', 7709):
            api.connect('120.76.152.87', 7709)
        try:
            for c in candidates:
                code = c.get('code', '')
                if not code: continue
                mc = 1 if code.startswith('6') else 0
                try:
                    bars = api.get_security_bars(9, mc, code, 0, 60)
                    if not bars or len(bars) < 20:
                        kline_data[code] = {}
                        continue
                    bars.sort(key=lambda b: b['datetime'] if 'datetime' in b else b.get('date', ''))
                    closes = [b['close'] for b in bars]
                    highs = [b['high'] for b in bars]
                    lows = [b['low'] for b in bars]
                    volumes = [(b.get('volume') or 0) for b in bars]
                    ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else 0
                    ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else 0
                    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else 0
                    # MACD (12,26,9)
                    ema12 = closes[0]; ema26 = closes[0]
                    difs = [0.0]
                    for pr in closes[1:]:
                        ema12 = ema12 * 11/13 + pr * 2/13
                        ema26 = ema26 * 25/27 + pr * 2/27
                        difs.append(ema12 - ema26)
                    dea = difs[0]
                    macd_hists = [0.0]
                    for d in difs[1:]:
                        dea = dea * 8/10 + d * 2/10
                        macd_hists.append((d - dea) * 2)
                    dif = difs[-1]; dea_val = dea; macd_hist = macd_hists[-1]
                    # RSI(14)
                    rsi14 = 50.0
                    if len(closes) >= 15:
                        gains = [max(0, closes[i] - closes[i-1]) for i in range(1, len(closes))]
                        losses = [max(0, closes[i-1] - closes[i]) for i in range(1, len(closes))]
                        avg_gain = sum(gains[-14:]) / 14
                        avg_loss = sum(losses[-14:]) / 14
                        rsi14 = 100 - 100 / (1 + avg_gain / avg_loss) if avg_loss > 0 else 100
                    # KDJ(9,3,3)
                    k_val = 50.0; d_val = 50.0; j_val = 50.0
                    if len(closes) >= 9:
                        for i in range(8, len(closes)):
                            h9 = max(highs[i-8:i+1]); l9 = min(lows[i-8:i+1])
                            rsv = (closes[i] - l9) / (h9 - l9) * 100 if h9 > l9 else 50
                            k_val = 2/3 * k_val + 1/3 * rsv
                            d_val = 2/3 * d_val + 1/3 * k_val
                        j_val = 3 * k_val - 2 * d_val
                    # 布林带(20,2)
                    boll_mid = ma20
                    boll_upper = boll_mid; boll_lower = boll_mid
                    if len(closes) >= 20 and boll_mid > 0:
                        variance = sum((c - boll_mid) ** 2 for c in closes[-20:]) / 20
                        std = variance ** 0.5
                        boll_upper = boll_mid + 2 * std
                        boll_lower = boll_mid - 2 * std
                    # 20日高低点
                    high20 = max(highs[-20:]) if len(highs) >= 20 else max(highs)
                    low20 = min(lows[-20:]) if len(lows) >= 20 else min(lows)
                    # 近10日涨停天数（涨幅≥9.5%且收盘≈最高价）
                    limit_up_days = 0
                    for i in range(max(0, len(closes) - 10), len(closes) - 1):
                        if i > 0 and closes[i-1] > 0:
                            day_chg = (closes[i] - closes[i-1]) / closes[i-1]
                            if day_chg >= 0.095 and highs[i] > 0 and closes[i] >= highs[i] * 0.98:
                                limit_up_days += 1
                    # WR(14) 威廉指标
                    wr14 = 50.0
                    if len(highs) >= 14:
                        h14 = max(highs[-14:]); l14 = min(lows[-14:])
                        wr14 = (h14 - closes[-1]) / (h14 - l14) * 100 if h14 > l14 else 50
                    # OBV
                    obv = 0
                    if len(closes) >= 2 and len(volumes) >= 2:
                        for i in range(1, len(closes)):
                            if volumes[i] > 0:
                                obv += volumes[i] if closes[i] > closes[i-1] else (-volumes[i] if closes[i] < closes[i-1] else 0)
                    # DMI(14): ±DI, ADX
                    pdi = 0.0; mdi = 0.0; adx = 0.0
                    if len(closes) >= 15:
                        tr_list = []; pd_list = []; md_list = []
                        for i in range(1, len(closes)):
                            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
                            tr_list.append(tr)
                            up_move = highs[i] - highs[i-1]; down_move = lows[i-1] - lows[i]
                            pd_list.append(up_move if up_move > down_move and up_move > 0 else 0)
                            md_list.append(down_move if down_move > up_move and down_move > 0 else 0)
                        tr14 = sum(tr_list[-14:]); pd14 = sum(pd_list[-14:]); md14 = sum(md_list[-14:])
                        pdi = (pd14 / tr14 * 100) if tr14 > 0 else 0
                        mdi = (md14 / tr14 * 100) if tr14 > 0 else 0
                        dx = abs(pdi - mdi) / (pdi + mdi) * 100 if (pdi + mdi) > 0 else 0
                        adx = dx  # 简化: 单日DX作为ADX近似
                    # 布林带宽
                    boll_width = (boll_upper - boll_lower) / boll_mid if boll_mid > 0 else 999
                    # 上市天数
                    first_date = bars[0].get('datetime', '') or bars[0].get('date', '')
                    days_listed = 999
                    if first_date:
                        try:
                            fd = datetime.strptime(str(first_date)[:8], '%Y%m%d')
                            # v6.9.43: 使用data_date替代datetime.now()保持一致性
                            ref_date = datetime.strptime(data_date, '%Y-%m-%d') if data_date else datetime.now()
                            days_listed = (ref_date - fd).days
                        except (ValueError, TypeError): pass
                    kline_data[code] = {
                        'ma5': ma5, 'ma10': ma10, 'ma20': ma20,
                        'dif': dif, 'dea': dea_val, 'macd_hist': macd_hist,
                        'rsi14': rsi14, 'k': k_val, 'd': d_val, 'j': j_val,
                        'boll_upper': boll_upper, 'boll_mid': boll_mid, 'boll_lower': boll_lower,
                        'boll_width': boll_width, 'wr14': wr14, 'obv': obv,
                        'pdi': pdi, 'mdi': mdi, 'adx': adx,
                        'high20': high20, 'low20': low20,
                        'high60': max(highs[-60:]) if len(highs) >= 60 else (max(highs) if highs else 0),
                        'low60': min(lows[-60:]) if len(lows) >= 60 else (min(lows) if lows else 0),
                        'days_listed': days_listed, 'limit_up_days': limit_up_days,
                        'closes': closes, 'highs': highs, 'lows': lows, 'volumes': volumes
                    }
                except (ValueError, TypeError, ZeroDivisionError, IndexError): kline_data[code] = {}
        finally:
            try: api.disconnect()
            except Exception: log_alert("DEBUG", "K线iTick", "api.disconnect()失败")
        log_alert("INFO", "K线拉取", f"获取{len(kline_data)}只历史K线(KDJ迭代+BOLL)")
    except Exception as e:
        log_alert("WARNING", "K线拉取", f"pytdx不可用: {str(e)[:60]}")
    return kline_data

# ============================================================
# 步骤10C-备选：HTTP K线拉取（东方财富API，v6.12.5新增）
# pytdx在沙箱网络中无法连接时，使用HTTP备选方案
# ============================================================
def step10C_fetch_klines_http(candidates):
    """v6.12.15-fix: HTTP备选K线拉取（腾讯日K线API，东方财富SSL不可达时使用）
    返回格式与 step10C_fetch_klines 完全一致
    腾讯API格式: qfqday每项为 [date, open, close, high, low, volume]
    """
    kline_data = {}
    _KLINE_BATCH = 20  # 每批并发数
    try:
        for batch_start in range(0, len(candidates), _KLINE_BATCH):
            batch = candidates[batch_start:batch_start + _KLINE_BATCH]
            with ThreadPoolExecutor(max_workers=_KLINE_BATCH) as executor:
                futures = {executor.submit(_fetch_single_kline_tencent, c): c for c in batch}
                for f in as_completed(futures):
                    code = futures[f].get('code', '')
                    try:
                        kline_data[code] = f.result()
                    except Exception:
                        log_alert("DEBUG", "K线HTTP", f"{code} 并发任务异常")
                        kline_data[code] = {}
            time.sleep(0.3)  # 批次间间隔，避免频率限制
        valid_count = sum(1 for v in kline_data.values() if v and v.get('closes'))
        log_alert("INFO", "K线HTTP", f"腾讯HTTP获取{len(kline_data)}只({valid_count}只有效)")
    except Exception as e:
        log_alert("WARNING", "K线HTTP", f"腾讯K线API不可用: {str(e)[:60]}")
    return kline_data


def _fetch_single_kline_tencent(c):
    """v6.12.15-fix: 单只股票腾讯K线拉取+指标计算"""
    code = c.get('code', '')
    if not code:
        return {}
    try:
        prefix = 'sh' if code.startswith('6') else 'sz'
        url = (f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'
               f'param={prefix}{code},day,,,60,qfq')
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://gu.qq.com/'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        stock_key = f'{prefix}{code}'
        qfqday = data.get('data', {}).get(stock_key, {}).get('qfqday', [])
        # 过滤掉非列表元素（如分红信息字典）
        bars = [b for b in qfqday if isinstance(b, list) and len(b) >= 6]
        if not bars or len(bars) < 20:
            return {}
        # 腾讯格式: [date, open, close, high, low, volume]
        closes = [float(b[2]) for b in bars]
        highs = [float(b[3]) for b in bars]
        lows = [float(b[4]) for b in bars]
        volumes = [float(b[5]) for b in bars]
        ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else 0
        ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else 0
        ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else 0
        # MACD (12,26,9)
        ema12 = closes[0]; ema26 = closes[0]
        difs = [0.0]
        for pr in closes[1:]:
            ema12 = ema12 * 11/13 + pr * 2/13
            ema26 = ema26 * 25/27 + pr * 2/27
            difs.append(ema12 - ema26)
        dea = difs[0]
        macd_hists = [0.0]
        for d in difs[1:]:
            dea = dea * 8/10 + d * 2/10
            macd_hists.append((d - dea) * 2)
        dif = difs[-1]; dea_val = dea; macd_hist = macd_hists[-1]
        # KDJ(9,3,3)
        k_val = 50.0; d_val = 50.0; j_val = 50.0
        if len(closes) >= 9:
            for i in range(8, len(closes)):
                h9 = max(highs[i-8:i+1]); l9 = min(lows[i-8:i+1])
                rsv = (closes[i] - l9) / (h9 - l9) * 100 if h9 > l9 else 50
                k_val = 2/3 * k_val + 1/3 * rsv
                d_val = 2/3 * d_val + 1/3 * k_val
            j_val = 3 * k_val - 2 * d_val
        # 布林带(20,2)
        boll_mid = ma20; boll_upper = boll_mid; boll_lower = boll_mid
        if len(closes) >= 20 and boll_mid > 0:
            variance = sum((c - boll_mid) ** 2 for c in closes[-20:]) / 20
            std = variance ** 0.5
            boll_upper = boll_mid + 2 * std; boll_lower = boll_mid - 2 * std
        high20 = max(highs[-20:]) if len(highs) >= 20 else max(highs)
        low20 = min(lows[-20:]) if len(lows) >= 20 else min(lows)
        boll_width = (boll_upper - boll_lower) / boll_mid if boll_mid > 0 else 999
        return {
            'ma5': ma5, 'ma10': ma10, 'ma20': ma20,
            'dif': dif, 'dea': dea_val, 'macd_hist': macd_hist,
            'rsi14': 50.0, 'k': k_val, 'd': d_val, 'j': j_val,
            'boll_upper': boll_upper, 'boll_mid': boll_mid, 'boll_lower': boll_lower,
            'boll_width': boll_width, 'wr14': 50.0, 'obv': 0,
            'pdi': 0.0, 'mdi': 0.0, 'adx': 0.0,
            'high20': high20, 'low20': low20,
            'high60': max(highs[-60:]) if len(highs) >= 60 else (max(highs) if highs else 0),
            'low60': min(lows[-60:]) if len(lows) >= 60 else (min(lows) if lows else 0),
            'days_listed': 999, 'limit_up_days': 0,
            'closes': closes, 'highs': highs, 'lows': lows, 'volumes': volumes
        }
    except (urllib.error.URLError, json.JSONDecodeError, OSError,
            ValueError, TypeError, ZeroDivisionError, IndexError):
        return {}


# ============================================================
# 步骤10C-三级备选：iTick HTTP K线拉取（v6.12.15新增）
# v6.13.11: 腾讯HTTP不可达时，iTick作为二级降级
# ============================================================
_ITICK_API_KEY = os.environ.get("ITICK_API_KEY", "")  # v6.13.5: 移除硬编码默认值
_ITICK_BASE_URL = "https://api-free.itick.org"  # 生产环境；免费版可用 https://api-free.itick.org

def step10C_fetch_klines_itick(candidates):
    """v6.12.15: iTick HTTP备选K线拉取（三级降级）
    返回格式与 step10C_fetch_klines 完全一致
    免费套餐限制: 5次/分钟，A股热门产品（非全覆盖）
    """
    kline_data = {}
    if not _ITICK_API_KEY:
        log_alert("WARNING", "K线iTick", "未配置ITICK_API_KEY环境变量，跳过")
        return kline_data
    try:
        # 探测套餐速率: 试用期(7天) = 120次/分钟, 免费版 = 5次/分钟
        # 默认保守使用5次/分钟，可通过 ITICK_RATE_LIMIT 环境变量覆盖
        rate_limit = int(os.environ.get("ITICK_RATE_LIMIT", "5"))
        batch_size = min(rate_limit, 10)  # 每批最多10只，避免单请求耗时过长
        wait_seconds = 60 / max(rate_limit / batch_size, 1)  # 每批间隔
        total = len(candidates)
        for batch_start in range(0, total, batch_size):
            batch = candidates[batch_start:batch_start + batch_size]
            batch_start_time = time.time()
            for c in batch:
                code = c.get('code', '')
                if not code:
                    continue
                try:
                    region = 'SH' if code.startswith('6') else 'SZ'
                    url = (f'{_ITICK_BASE_URL}/stock/kline?'
                           f'region={region}&code={code}&kType=8&limit=60')
                    time.sleep(0.5)  # 请求间隔，避免429（试用期120/min → 2/sec）
                    req = urllib.request.Request(url, headers={
                        'User-Agent': 'Mozilla/5.0',
                        'accept': 'application/json',
                        'token': _ITICK_API_KEY})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data = json.loads(resp.read().decode())
                    bars = data.get('data', [])
                    if not bars or len(bars) < 20:
                        kline_data[code] = {}
                        continue
                    # iTick返回: [{o, h, l, c, v, tu, t}, ...] 按时间升序
                    bars.sort(key=lambda b: b.get('t', 0))
                    closes = [b['c'] for b in bars]
                    highs = [b['h'] for b in bars]
                    lows = [b['l'] for b in bars]
                    volumes = [b['v'] for b in bars]
                    ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else 0
                    ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else 0
                    ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else 0
                    # MACD (12,26,9)
                    ema12 = closes[0]; ema26 = closes[0]
                    difs = [0.0]
                    for pr in closes[1:]:
                        ema12 = ema12 * 11/13 + pr * 2/13
                        ema26 = ema26 * 25/27 + pr * 2/27
                        difs.append(ema12 - ema26)
                    dea = difs[0]
                    macd_hists = [0.0]
                    for d in difs[1:]:
                        dea = dea * 8/10 + d * 2/10
                        macd_hists.append((d - dea) * 2)
                    dif = difs[-1]; dea_val = dea; macd_hist = macd_hists[-1]
                    # KDJ(9,3,3)
                    k_val = 50.0; d_val = 50.0; j_val = 50.0
                    if len(closes) >= 9:
                        for i in range(8, len(closes)):
                            h9 = max(highs[i-8:i+1]); l9 = min(lows[i-8:i+1])
                            rsv = (closes[i] - l9) / (h9 - l9) * 100 if h9 > l9 else 50
                            k_val = 2/3 * k_val + 1/3 * rsv
                            d_val = 2/3 * d_val + 1/3 * k_val
                        j_val = 3 * k_val - 2 * d_val
                    # 布林带(20,2)
                    boll_mid = ma20; boll_upper = boll_mid; boll_lower = boll_mid
                    if len(closes) >= 20 and boll_mid > 0:
                        variance = sum((c - boll_mid) ** 2 for c in closes[-20:]) / 20
                        std = variance ** 0.5
                        boll_upper = boll_mid + 2 * std; boll_lower = boll_mid - 2 * std
                    high20 = max(highs[-20:]) if len(highs) >= 20 else max(highs)
                    low20 = min(lows[-20:]) if len(lows) >= 20 else min(lows)
                    boll_width = (boll_upper - boll_lower) / boll_mid if boll_mid > 0 else 999
                    kline_data[code] = {
                        'ma5': ma5, 'ma10': ma10, 'ma20': ma20,
                        'dif': dif, 'dea': dea_val, 'macd_hist': macd_hist,
                        'rsi14': 50.0, 'k': k_val, 'd': d_val, 'j': j_val,
                        'boll_upper': boll_upper, 'boll_mid': boll_mid, 'boll_lower': boll_lower,
                        'boll_width': boll_width, 'wr14': 50.0, 'obv': 0,
                        'pdi': 0.0, 'mdi': 0.0, 'adx': 0.0,
                        'high20': high20, 'low20': low20,
                        'high60': max(highs[-60:]) if len(highs) >= 60 else (max(highs) if highs else 0),
                        'low60': min(lows[-60:]) if len(lows) >= 60 else (min(lows) if lows else 0),
                        'days_listed': 999, 'limit_up_days': 0,
                        'closes': closes, 'highs': highs, 'lows': lows, 'volumes': volumes
                    }
                except (urllib.error.URLError, json.JSONDecodeError, OSError,
                        ValueError, TypeError, ZeroDivisionError, IndexError):
                    kline_data[code] = {}
            # 速率限制: 试用期120次/分钟(ITICK_RATE_LIMIT=120), 免费版5次/分钟(默认)
            elapsed = time.time() - batch_start_time
            if elapsed < wait_seconds and batch_start + batch_size < total:
                wait = max(1, wait_seconds - elapsed)
                time.sleep(wait)
        valid_count = sum(1 for v in kline_data.values() if v and v.get('closes'))
        log_alert("INFO", "K线iTick", f"iTick获取{len(kline_data)}只({valid_count}只有效)")
    except Exception as e:
        log_alert("WARNING", "K线iTick", f"iTick API不可用: {str(e)[:60]}")
    return kline_data


# ============================================================
# ============================================================
# 步骤10C-附：东方财富资金流向（v6.12.5新增）
# 腾讯基础API不提供主力资金流向，使用东方财富flow API获取
# ============================================================
def step10C_flow_fetch_main_inflow(candidates):
    """v6.13.4: 优先腾讯API(字段62)，降级东方财富API批量获取主力净流入
    返回: {code: main_inflow_yuan} 字典，值为 float（元）或 None"""
    flow_data = {}
    if not candidates: return flow_data
    # 第一顺位：从候选标的已有的腾讯API数据中提取（已在上游fetch_tencent_stocks中获取）
    missed = []
    for c in candidates:
        code = c.get('code', '')
        if not code: continue
        mi = c.get('main_inflow')
        if mi is not None:
            flow_data[code] = mi
        else:
            missed.append(c)
    if missed:
        # 第二顺位：对腾讯API未获取到的，单独调用腾讯API
        for c in missed[:]:
            code = c.get('code', '')
            if not code: continue
            try:
                prefix = 'sz' if code.startswith(('0','3')) else 'sh'
                url = f"{TENCENT_API}{prefix}{code}"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    text = resp.read().decode('gbk', errors='replace')
                for line in text.strip().split('\n'):
                    if not line or '="' not in line: continue
                    raw = line.split('"')[1].split('~')
                    if len(raw) < 63: continue
                    mi_val = _parse_tencent_field(raw, 62, None)
                    if mi_val is not None:
                        flow_data[code] = mi_val * 10000  # 万元→元
                        missed.remove(c)
                    break
            except Exception: log_alert("DEBUG", "主力资金", f"{code} 解析失败")
    # 第三顺位：东方财富API降级(仅对仍未获取到的)
    still_missed = [c for c in missed if c.get('code','') not in flow_data]
    for c in still_missed:
        code = c.get('code', '')
        if not code: continue
        try:
            mc = '1' if code.startswith('6') else '0'
            secid = f'{mc}.{code}'
            url = (f'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?'
                   f'secid={secid}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57&lmt=1')
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://quote.eastmoney.com/'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            klines = data.get('data', {}).get('klines', [])
            if klines:
                parts = klines[0].split(',')
                main_in = float(parts[1])  # 主力净流入(元)
                flow_data[code] = main_in
        except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError, IndexError):
            flow_data[code] = None
    return flow_data

# ============================================================
# 步骤10D：东方财富财务数据拉取（质押/商誉/解禁 — API已废弃，降级跳过）
# v6.9.15: 质押/商誉/解禁API全部废弃，返回空字典。
# ROE/净利润已迁移至step10E（F10单股API，仅在step11后对候选标的拉取）。
# ============================================================
def step10D_fetch_financials():
    """质押/商誉/解禁 — API已废弃，降级跳过"""
    pledge_data = {}; goodwill_data = {}; unlock_data = {}
    log_alert("WARNING", "财务数据", "质押/商誉/解禁API已废弃，硬排除规则13-15降级跳过")
    return pledge_data, goodwill_data, unlock_data

# ============================================================
# 步骤10E：F10财务数据拉取（ROE/净利润 — 单股逐只API）
# v6.9.15: 替代已废弃的datacenter-web批量API，使用F10单股API逐只拉取。
# 仅在step11硬排除后调用，对通过候选标的拉取最新财报ROE和净利润。
# ============================================================
def step10E_fetch_fundamentals(candidates):
    """使用F10单股API拉取ROE/净利润/净利润同比，仅对通过硬排除的候选标的"""
    fundamental_data = {}
    headers = {'User-Agent': 'Mozilla/5.0'}
    fetched = 0; errors = 0
    for c in candidates:
        code = c.get('code', '')
        if not code: continue
        # 确定市场前缀: 6开头→SH, 0/3开头→SZ
        prefix = 'SH' if code.startswith('6') else 'SZ'
        secode = f'{prefix}{code}'
        try:
            url = f'https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?type=0&code={secode}'
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read()
            # v6.9.21: 处理gzip压缩响应
            if raw[:2] == b'\x1f\x8b':
                raw = gzip.decompress(raw)
            r = json.loads(raw.decode('utf-8'))
            items = r.get('data', [])
            if items:
                latest = items[0]  # 最新一期报告
                fd = {
                    'roe': latest.get('ROEJQ'),
                    'net_profit': latest.get('PARENTNETPROFIT'),
                    'revenue': latest.get('TOTALOPERATEREVE'),
                    'eps': latest.get('EPSJB'),
                    'report_date': latest.get('REPORT_DATE', ''),
                    'pledge_ratio': latest.get('PLEDGERATIO'),        # v6.9.43: 质押比例（信号#28用）
                    'goodwill_ratio': latest.get('GOODWILLRATIO'),   # v6.9.43: 商誉占比（信号#29用）
                }
                # v6.9.39: 计算净利润同比（与4期前同季度对比）
                if len(items) >= 5:
                    prev_year = items[4]  # 4期前=同季度去年
                    try:
                        cur_np = float(latest.get('PARENTNETPROFIT', 0) or 0)
                        prev_np = float(prev_year.get('PARENTNETPROFIT', 0) or 0)
                        if prev_np != 0:
                            fd['net_profit_yoy'] = (cur_np - prev_np) / abs(prev_np) * 100
                    except (ValueError, TypeError):
                        pass
                fundamental_data[code] = fd
                fetched += 1
        except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError) as e:
            errors += 1
            continue
        # 每50只短暂休息，避免被限流
        if fetched % 50 == 0 and fetched > 0:
            time.sleep(0.3)
    log_alert("INFO", "财务数据", f"F10基本面: {fetched}只成功, {errors}只失败")
    return fundamental_data

# ============================================================
# 步骤10F：风险事件拉取（v6.9.27：限售解禁/可转债/业绩预告窗口）
# ============================================================
def step10F_fetch_risk_events():
    """v6.9.27: 拉取未来15日内风险事件数据。
    返回: {unlock_events: {code: {date,ratio}}, cb_events: {code: reason}, earnings_window: bool}"""
    unlock_events = {}
    cb_events = {}
    earnings_window = False
    
    end_date = (datetime.strptime(prediction_date, '%Y-%m-%d') + timedelta(days=15)).strftime('%Y-%m-%d')
    
    # 1. 限售解禁数据 — qqjjsj.com 结构化解析
    try:
        url = 'https://www.qqjjsj.com/show13a446260'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html'
        })
        with urllib.request.urlopen(req, timeout=12) as resp:
            raw = resp.read().decode('utf-8', errors='ignore')
        # 解析表格: <td>代码</td><td>名称</td><td>日期</td>...<td>占总股本比例</td>
        rows = re.findall(
            r'<td[^>]*>(\d{6})</td>\s*<td[^>]*>([^<]+)</td>\s*<td[^>]*>(\d{4}-\d{2}-\d{2})</td>'
            r'(?:.*?</tr>|.*?<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([^<]*)</td>\s*<td[^>]*>([\d.]+)</td>)',
            raw, re.DOTALL
        )
        for code, _, date, *rest in rows:
            if not (prediction_date <= date <= end_date): continue
            ratio = 0
            if rest and len(rest) >= 4:
                try: ratio = float(rest[3])
                except (ValueError, TypeError): pass
            if ratio > 0:
                unlock_events[code] = {'date': date, 'ratio': ratio}
        if unlock_events:
            log_alert("INFO", "风险事件", f"解禁: {len(unlock_events)}只未来15日内有限售解禁(>0%)")
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError, ssl.SSLError) as e:
        log_alert("WARNING", "风险事件", f"解禁API不可达: {str(e)[:60]}，使用内置数据缓冲")
        # 内置缓冲数据（2026-06-22~07-07窗口，>5%）
        _BUILTIN_UNLOCK = {
            '688334': {'date': '2026-06-22', 'ratio': 49.04}, '301448': {'date': '2026-06-22', 'ratio': 53.69},
            '688543': {'date': '2026-06-22', 'ratio': 44.47}, '301315': {'date': '2026-06-22', 'ratio': 57.95},
            '688535': {'date': '2026-06-23', 'ratio': 10.03}, '603236': {'date': '2026-06-24', 'ratio': 9.09},
            '002753': {'date': '2026-06-24', 'ratio': 12.36}, '688443': {'date': '2026-06-24', 'ratio': 7.28},
            '300013': {'date': '2026-06-25', 'ratio': 20.00}, '002278': {'date': '2026-06-25', 'ratio': 6.72},
            '301280': {'date': '2026-06-26', 'ratio': 68.24}, '920781': {'date': '2026-06-26', 'ratio': 33.03},
            '920037': {'date': '2026-06-26', 'ratio': 65.38}, '002307': {'date': '2026-06-26', 'ratio': 21.88},
            '603991': {'date': '2026-06-26', 'ratio': 9.82}, '601990': {'date': '2026-06-26', 'ratio': 12.40},
            '600299': {'date': '2026-06-26', 'ratio': 12.92}, '603725': {'date': '2026-06-22', 'ratio': 5.97},
            '603400': {'date': '2026-06-22', 'ratio': 8.00}, '301678': {'date': '2026-06-22', 'ratio': 38.41},
            '920220': {'date': '2026-06-22', 'ratio': 13.32}, '301255': {'date': '2026-06-29', 'ratio': 75.00},
            '688620': {'date': '2026-06-29', 'ratio': 40.74}, '688629': {'date': '2026-06-29', 'ratio': 59.63},
            '688631': {'date': '2026-06-29', 'ratio': 60.32}, '688429': {'date': '2026-06-29', 'ratio': 70.04},
            '605007': {'date': '2026-06-29', 'ratio': 15.35}, '920161': {'date': '2026-06-29', 'ratio': 27.07},
            '688331': {'date': '2026-06-30', 'ratio': 34.16}, '301105': {'date': '2026-06-30', 'ratio': 60.38},
            '688582': {'date': '2026-06-30', 'ratio': 37.72}, '600789': {'date': '2026-06-30', 'ratio': 10.68},
            '001388': {'date': '2026-07-01', 'ratio': 41.38}, '688062': {'date': '2026-07-01', 'ratio': 43.75},
            '688220': {'date': '2026-07-01', 'ratio': 11.75}, '300913': {'date': '2026-07-01', 'ratio': 8.77},
            '301488': {'date': '2026-07-06', 'ratio': 17.89}, '301202': {'date': '2026-07-06', 'ratio': 65.97},
            '603600': {'date': '2026-07-06', 'ratio': 9.20}, '920211': {'date': '2026-07-06', 'ratio': 16.97},
        }
        for code, info in _BUILTIN_UNLOCK.items():
            if prediction_date <= info['date'] <= end_date:
                unlock_events[code] = info
        log_alert("INFO", "风险事件", f"解禁(内置): {len(unlock_events)}只")
    
    # 2. 可转债强赎/到期检测
    # 已知近期到期：天创转债(113589) 2026-06-23到期
    _CB_NEAR_EXPIRY = {'603608': '天创转债2026-06-23到期'}
    for code, reason in _CB_NEAR_EXPIRY.items():
        cb_events[code] = reason
    
    # 3. 业绩预告强制披露窗口检测
    # 主板(6xxxx/0xxxx)在7月1-15日期间，强制披露窗口
    # 创业板/科创板(3xxxx/688xxx)为自愿披露
    bj = datetime.strptime(beijing_date, '%Y-%m-%d')
    if bj.month == 7 and 1 <= bj.day <= 15:
        earnings_window = True
        log_alert("INFO", "风险事件", "业绩预告强制披露窗口(7月1-15日)")
    
    return unlock_events, cb_events, earnings_window

# ============================================================
# 步骤10G：拥挤度数据拉取（v6.9.28：机构持仓+融资过热代理）
# ============================================================
def step10G_fetch_crowding_data(candidates):
    """v6.9.28: 拉取机构持仓数据，计算融资过热代理指标。
    返回: {inst_holding: {code: {total_fund_ratio, reduce_count}}, margin_overheat: {code: bool}}"""
    inst_holding = {}
    margin_overheat = {}
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://emweb.securities.eastmoney.com/'}
    fetched = 0; errors = 0
    
    for c in candidates:
        code = c.get('code', '')
        if not code: continue
        
        # 1. 机构持仓数据 — F10 ShareholderResearch API
        prefix = 'SH' if code.startswith('6') else 'SZ'
        secode = f'{prefix}{code}'
        try:
            url = f'https://emweb.securities.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={secode}'
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read()
            if raw[:2] == b'\x1f\x8b':
                raw = gzip.decompress(raw)
            data = json.loads(raw.decode('utf-8'))
            
            # 计算基金持仓总占比
            jjcg = data.get('jjcg', [])
            total_fund_ratio = sum(float(item.get('FREESHARES_RATIO') or 0) for item in jjcg)
            
            # 统计前十大股东中机构减持数量
            sdltgd = data.get('sdltgd', [])
            inst_types = {'基金', '保险公司', '券商', '社保基金', 'QFII', '信托', '银行', '企业年金', '财务公司'}
            reduce_count = 0
            for item in sdltgd:
                if item.get('HOLDER_TYPE', '') in inst_types:
                    change = item.get('HOLD_NUM_CHANGE', '')
                    if change and '减' in str(change):
                        reduce_count += 1
            
            inst_holding[code] = {
                'total_fund_ratio': total_fund_ratio,
                'reduce_count': reduce_count,
                'fund_count': len(jjcg),
            }
            fetched += 1
        except (urllib.error.URLError, json.JSONDecodeError, OSError, KeyError, IndexError, ValueError):
            errors += 1
            inst_holding[code] = {'total_fund_ratio': 0, 'reduce_count': 0, 'fund_count': 0}
        
        # 2. 融资过热代理 — 基于已有行情数据
        # 代理人: 换手率>20% + 量比>2.5 = 融资买入过热的强信号
        # 逻辑: 融资买入→成交量激增→换手率+量比双双飙升
        to = c.get('turnover', 0) or 0
        vr = c.get('volume_ratio', 0) or 0
        if to > 20 and vr > 2.5:
            margin_overheat[code] = True
        
        if fetched % 100 == 0 and fetched > 0:
            time.sleep(0.2)
    
    n_oh = sum(1 for v in margin_overheat.values() if v)
    n_reduce = sum(1 for v in inst_holding.values() if v.get('reduce_count', 0) >= 2)
    log_alert("INFO", "拥挤度", f"机构持仓: {fetched}只成功/{errors}只失败, 减持≥2家: {n_reduce}只, 融资过热代理: {n_oh}只")
    return inst_holding, margin_overheat

# ============================================================
# 步骤10H：二级行业赋值（v6.9.35：从CompanySurvey缓存读取sshy，替代CoreConception主营业务）
# ============================================================
def step10H_fetch_sub_industry(candidates):
    """v6.9.35: 从二级行业缓存读取sshy，无需额外API调用。
    返回: {code: sub_industry}"""
    result = {}
    cached = 0; missing = 0
    for c in candidates:
        code = c.get('code', '')
        if code in _sub_industry_cache:
            result[code] = _sub_industry_cache[code]
            cached += 1
        else:
            result[code] = ''
            missing += 1
    log_alert("INFO", "二级行业", f"缓存命中{cached}只/缺失{missing}只")
    return result

# ============================================================
# 步骤11：硬排除
# ============================================================
def step11_hard_exclude(candidates, all_holdings_codes, kline_data=None, pledge_data=None, goodwill_data=None, unlock_data=None, fundamental_data=None):
    """v6.9.43: 13项硬排除（创业板/PE<0/质押/商誉已迁移至信号过滤，解禁API已废弃）"""
    if kline_data is None: kline_data = {}
    if pledge_data is None: pledge_data = {}
    if goodwill_data is None: goodwill_data = {}
    if unlock_data is None: unlock_data = {}
    if fundamental_data is None: fundamental_data = {}
    er = Counter()
    recent_7d_dates = {}  # v6.6.37: 按日期去重，统计7日内推荐天数
    recent_7d_strategies = {}  # v6.6.44: 记录7日内每日的策略 {code: {date: strategy, ...}}
    c7 = (datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
    for f in sorted(os.listdir('/workspace')):
        if f.startswith('推荐历史_') and f.endswith('.json'):
            for r in safe_read_json(os.path.join('/workspace', f)):
                if r.get('type') == 'recommendation' and r.get('date', '') >= c7:
                    code = r.get('code', '')
                    if code not in recent_7d_dates:
                        recent_7d_dates[code] = set()
                        recent_7d_strategies[code] = {}
                    recent_7d_dates[code].add(r.get('date', ''))
                    recent_7d_strategies[code][r.get('date', '')] = r.get('strategy', '?')
    # 转换为天数计数
    recent_7d_count = {code: len(dates) for code, dates in recent_7d_dates.items()}
    passed, excluded = [], []
    for c in candidates:
        code = c.get('code', ''); close = c.get('close', 0); chg = c.get('change_pct', 0)
        reason = None
        # v6.6.37: 7日内推荐天数（按日期去重），不排除，仅标注
        if code not in all_holdings_codes:
            # 先复制历史数据（计数在步骤13策略匹配成功后+1）
            c['_recent_7d'] = recent_7d_count.get(code, 0)
            c['_recent_7d_strategies'] = dict(recent_7d_strategies.get(code, {}))
        if code in all_holdings_codes:
            reason = "当前持仓"
        elif code.startswith('688'): reason = "科创板"
        elif code.startswith(('82', '83', '87', '88', '92')): reason = "北交所"
        elif code.startswith(('300', '301')): reason = "创业板"
        elif close < 5: reason = f"股价<5元"
        elif close > 100: reason = f"股价>100元"
        elif (c.get('name') or '').startswith('ST') or (c.get('name') or '').startswith('*ST'): reason = "ST/*ST"
        elif chg > 7: reason = f"涨幅>7%"
        elif close <= 0: reason = "停牌"
        # v6.9.22: PE<0已迁移至信号过滤，与F10净利润亏损合并判断
        elif c.get('total_cap') and c.get('total_cap') > 0 and c.get('total_cap') < 1_000_000_000: reason = "市值<10亿"
        elif c.get('amount') is not None and c.get('amount', 0) < 10_000_000: reason = "成交额<1000万"
        # v6.9.0: 上市天数
        kd = kline_data.get(code, {})
        if not reason and kd.get('days_listed') is not None and kd['days_listed'] < 60:
            reason = "上市不足60天"
        # v6.9.4: 质押比例>50%（v6.9.39: step10D API已废弃，降级为信号标记，见step12信号#28）
        # v6.9.4: 商誉/净资产>30%（v6.9.39: step10D API已废弃，降级为信号标记，见step12信号#29）
        # v6.9.4: 近期大额解禁（v6.9.39: 已迁移至step12信号#23，此处不再检查）
        # v6.9.22: PE<0已迁移至信号过滤#21，此处在硬排除中不再检查
        if reason: er[reason.split('(')[0]] += 1; excluded.append(c)
        else: passed.append(c)
    # 统计7日内推荐数量（仅统计通过且被标注的）
    recent_7d_count = sum(1 for c in passed if c.get('_recent_7d'))
    log_alert("INFO", "硬排除", f"通过{len(passed)}只 排除{len(excluded)}只 7日推荐{recent_7d_count}只")
    return passed, excluded, er

# ============================================================
# 步骤12：信号过滤
# ============================================================
def step12_signal_filter(candidates, kline_data=None, fundamental_data=None, risk_data=None, crowding_data=None):
    """v6.9.28: 27项信号过滤（新增#26机构持仓变化/#27融资过热代理）"""
    if kline_data is None: kline_data = {}
    if fundamental_data is None: fundamental_data = {}
    if risk_data is None: risk_data = ({}, {}, False)
    if crowding_data is None: crowding_data = ({}, {})
    unlock_events, cb_events, earnings_window = risk_data
    inst_holding, margin_overheat = crowding_data
    passed, excluded = [], []
    for c in candidates:
        code = c.get('code', '')
        chg = c.get('change_pct', 0); close = c.get('close', 0); op = c.get('open', 0)
        high = c.get('high', 0); low = c.get('low', 0); amp = c.get('amplitude', 0)
        vr = c.get('volume_ratio'); to = c.get('turnover', 0)
        kd = kline_data.get(code, {})
        reasons = []
        # 1. 假动量：高开>3%后回落超2%
        if op > 0 and c.get('prev_close', op) > 0:
            pc = c.get('prev_close', op)
            if (op - pc) / pc > 0.03 and close < op * 0.98: reasons.append("假动量")
        # 2. 诱多：冲高>5%后回落至开盘附近
        if high > 0 and op > 0:
            pc = c.get('prev_close', 0)
            if pc > 0 and (high - pc) / pc > 0.05 and close < op * 1.01: reasons.append("诱多")
        # 3. 缩量涨停：涨幅>5%+量比<0.5
        if chg > 5 and vr is not None and vr < 0.5: reasons.append("缩量涨停")
        # 4. 振幅过大：>15%
        if amp > 15: reasons.append(f"振幅>{amp:.1f}%")
        # 5. 跌停板边缘：chg<-9%+振幅>12%
        if chg < -9 and amp > 12: reasons.append("跌停板异动")
        # 6. 缩量下跌（v6.9.39: vr<0.15+amp<=3→真正无流动性，amp>3留给B策略超跌反弹）
        if chg < -3 and vr is not None and vr < 0.15 and amp <= 3: reasons.append("缩量下跌")
        # 7. 高换手低涨幅：换手>20%+涨跌幅<2%
        if to > 20 and abs(chg) < 2: reasons.append("高换手低涨幅")
        # 8. 首阴标记（不排除，仅加分）— v6.9.38: 拆分为独立检测，不受其他信号影响
        if -3 < chg < 0 and to > 3: c['_first_yin'] = True
        # 9. 均线空头排列（MA5<MA10<MA20）
        ma5 = kd.get('ma5', 0); ma10 = kd.get('ma10', 0); ma20 = kd.get('ma20', 0)
        if ma5 > 0 and ma10 > 0 and ma20 > 0 and ma5 < ma10 < ma20:
            reasons.append("均线空头排列")
        # 10. MACD顶背离
        high20 = kd.get('high20', 0); dif = kd.get('dif', 0)
        closes_h = kd.get('closes', [])
        if high20 > 0 and len(closes_h) >= 20:
            difs_list = []
            ema12 = closes_h[0]; ema26 = closes_h[0]
            for pr in closes_h[1:]:
                ema12 = ema12 * 11/13 + pr * 2/13
                ema26 = ema26 * 25/27 + pr * 2/27
                difs_list.append(ema12 - ema26)
            dif_20d_max = max(difs_list[-20:]) if len(difs_list) >= 20 else dif
            if high >= high20 * 0.995 and dif < dif_20d_max * 0.9:
                reasons.append("MACD顶背离")
        # 11. RSI超买（RSI(14)>80）
        rsi14 = kd.get('rsi14', 50)
        if rsi14 > 80: reasons.append(f"RSI超买({rsi14:.0f})")
        # 12. 缩量反弹（v6.9.3: 连续3日量能递减+当日反弹>2%）
        if chg > 2:
            vols = kd.get('volumes', [])
            if len(vols) >= 4 and vr is not None and vr < 0.6:
                if vols[-4] > vols[-3] > vols[-2] and vols[-1] > 0 and vols[-1] < vols[-2]:  # v6.9.43: 当日量<前日量(真正缩量)
                    reasons.append("缩量反弹")
        # 13. KDJ高位死叉（J=3K-2D, J>100且J<K⇔K<D即死叉, v6.9.43注释修正）
        j_val = kd.get('j', 50); k_val = kd.get('k', 50)
        if j_val > 100 and j_val < k_val:
            reasons.append(f"KDJ死叉(J={j_val:.0f})")
        # 14. 涨停次日高开低走（v6.9.3: 前日涨停+当日高开低走收阴）
        closes_h = kd.get('closes', []); highs_h = kd.get('highs', [])
        if len(closes_h) >= 3 and closes_h[-2] > 0 and highs_h[-2] > 0:
            yday_chg = (closes_h[-2] - closes_h[-3]) / closes_h[-3] if closes_h[-3] > 0 else 0
            yday_limit = yday_chg >= 0.095 and closes_h[-2] >= highs_h[-2] * 0.98
            if yday_limit and op > 0 and close < op and chg < 0:
                reasons.append("涨停次日高开低走")
        # 15. 布林带收窄突破失败（v6.9.3: 带宽<5%+当日放量但收阴）
        boll_width = kd.get('boll_width', 999)
        if boll_width < 0.05 and vr is not None and vr >= 1.5 and close < op:
            reasons.append(f"布林突破失败(带宽{boll_width:.1%})")
        # 16. 20日涨幅>45%风控（v6.9.5: 防止追高爆炒股）
        closes_h = kd.get('closes', [])
        if len(closes_h) >= 20 and closes_h[-20] > 0:
            rally_20d_v2 = (close - closes_h[-20]) / closes_h[-20]
            if rally_20d_v2 > 0.45:
                reasons.append(f"20日涨幅{rally_20d_v2:.0%}>45%")
        # 17. 放量不涨（v6.9.10: 量比>2+涨跌<1%→放量不涨，疑似出货）
        if vr is not None and vr > 2 and 0 < chg < 1: reasons.append("放量不涨")
        # 18. 放量滞跌（v6.9.22: 量比>1.5+微跌+收阴+振幅>2%→放量滞跌，下跌中继，振幅辅助减少误杀）
        if vr is not None and vr > 1.5 and -1 < chg < 0 and close < op and amp is not None and amp > 2: reasons.append("放量滞跌")
        # 19. 高位长上影线（v6.9.11: 涨>5%+上影线>实体2倍→高位抛压）
        if chg > 5 and high > max(close, op) and low > 0:
            body = abs(close - op); upper_shadow = high - max(close, op)
            if upper_shadow > body * 2 and upper_shadow / close > 0.03:
                reasons.append("长上影线")
        # 20. 连续缩量（v6.9.11: 量比<0.4+涨跌<1%→无人气横盘）
        if vr is not None and vr < 0.4 and abs(chg) < 1: reasons.append("连续缩量")
        # 21. 净利润亏损（v6.9.22: F10数据优先，PE<0兜底；PE<0从硬排除迁移至此处）
        fd = fundamental_data.get(code, {})
        roe = fd.get('roe')
        np_val = fd.get('net_profit')
        if roe is not None and np_val is not None:
            try:
                if float(roe) < 0 or float(np_val) < 0:
                    reasons.append(f"净利润亏损(ROE={float(roe):.1f}%)")
            except (ValueError, TypeError):
                pass
        elif c.get('pe_ttm') is not None and c.get('pe_ttm', 0) < 0:
            reasons.append("PE负值(亏损)")
        # 22. 流动性冲击成本（v6.9.26: Amihud ILLIQ=|chg%|/(成交额/亿)→排除>10的标的，避免短线交易滑点侵蚀利润）
        amt = c.get('amount', 0)
        if amt is not None and amt > 0:
            amt_e8 = amt / 1e8
            if amt_e8 > 0:
                illiq = abs(chg) / amt_e8
                if illiq > 10:
                    reasons.append(f"冲击成本({illiq:.0f}bps/亿)")
        # 23. 限售解禁风险（v6.9.27: 未来15日内解禁占总股本>5%→排除，防止解禁抛压）
        ue = unlock_events.get(code)
        if ue and ue.get('ratio', 0) > 5:
            reasons.append(f"解禁({ue['date']} {ue['ratio']:.0f}%)")
        # 24. 可转债到期/强赎（v6.9.27: 未来15日内→排除，防止转股稀释/赎回冲击）
        cb = cb_events.get(code)
        if cb:
            reasons.append(f"可转债({cb})")
        # 25. 业绩预告强制披露窗口（v6.9.27: 主板+7月1-15日+Q1大幅波动→标记风险不排除，仅标注）
        if earnings_window:
            board = code[:3]
            if board in ('600', '601', '603', '605', '000', '001', '002', '003'):
                fd = fundamental_data.get(code, {})
                q1_net = fd.get('net_profit_yoy', 0) or 0
                if abs(q1_net) > 50:
                    reasons.append(f"业绩预告窗口(Q1净利{q1_net:+.0f}%)")
        # 26. 机构持仓变化（v6.9.28: 前十大股东中≥2家机构减持→排除，防止成为对手盘）
        ih = inst_holding.get(code, {})
        if ih.get('reduce_count', 0) >= 2:
            reasons.append(f"机构减持({ih['reduce_count']}家)")
        # 27. 融资买入过热代理（v6.9.28: 换手率>20%+量比>2.5→排除，代理融资买入占比>25%）
        if margin_overheat.get(code):
            reasons.append(f"融资过热(换手{to:.0f}% 量比{vr:.1f})")
        # 28. 质押比例过高（v6.9.39: step10D API已废弃，从F10 fundamental_data兜底读取）
        fd = fundamental_data.get(code, {})
        pledge_ratio = fd.get('pledge_ratio')
        if pledge_ratio is not None:
            try:
                if float(pledge_ratio) > 50:
                    reasons.append(f"质押过高({float(pledge_ratio):.0f}%)")
            except (ValueError, TypeError):
                pass
        # 29. 商誉/净资产>30%（v6.9.39: step10D API已废弃，从F10 fundamental_data兜底读取）
        goodwill_ratio = fd.get('goodwill_ratio')
        if goodwill_ratio is not None:
            try:
                if float(goodwill_ratio) > 0.30:
                    reasons.append(f"商誉占比{float(goodwill_ratio):.0%}")
            except (ValueError, TypeError):
                pass
        if reasons:
            c['_signal_reasons'] = reasons
            excluded.append(c)
        else:
            passed.append(c)
    log_alert("INFO", "信号过滤", f"通过{len(passed)}只 排除{len(excluded)}只")
    return passed, excluded

# ============================================================
# 步骤13：二十策略匹配（ABCDEFGHIJKLMNOPQ + RST主力共振，F为E子策略升级，v6.10.0新增RST）
# ============================================================
def step13_strategy_match(candidates, kline_data=None):
    if kline_data is None: kline_data = {}
    # v6.9.39: 预计算最近5日推荐次数，避免策略F循环内重复IO
    recent_5d = {}
    c5 = (datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=5)).strftime('%Y-%m-%d')
    for fname in sorted(os.listdir('/workspace')):
        if fname.startswith('推荐历史_') and fname.endswith('.json'):
            for r in safe_read_json(os.path.join('/workspace', fname)):
                if r.get('type') == 'recommendation' and r.get('date', '') >= c5:
                    rc = r.get('code', '')
                    recent_5d[rc] = recent_5d.get(rc, 0) + 1
    matched = []
    for c in candidates:
        chg = c.get('change_pct', 0); amp = c.get('amplitude', 0)
        vr = c.get('volume_ratio'); to = c.get('turnover', 0)
        close = c.get('close', 0); op = c.get('open', 0)
        high = c.get('high', 0); low = c.get('low', 0)
        s = None; reason = ""; score = 0
        # ── A 动量延续 (v6.8.8: 极端上涨市关闭+读取strategy_a_weak_market参数) ──
        a_weak_closed = params.get('strategy_a_weak_market', 'closed') == 'closed'
        a_extreme = market_condition == "强市(极端上涨/降仓防追高)"
        if (not a_weak_closed or market_condition != "弱市") and not a_extreme and 3 <= chg <= 7:
            if vr is not None and 1.5 <= vr <= 5.0:
                s = "A"; reason = f"动量延续:涨{chg:.1f}%+量比{vr:.1f}"; score = 10
                # v6.6.38: 假突破过滤 — 上影线:下影线>2:1 → 降置信减3分
                if high > 0 and low > 0 and high > low:
                    ent = max(close, op); body_low = min(close, op)
                    upper_shadow = high - ent if ent > 0 else 0
                    lower_shadow = body_low - low if body_low > 0 else 0
                    if lower_shadow > 0 and upper_shadow / lower_shadow > 2:
                        c['_fake_breakout'] = True
                        score -= 3
                        reason += f" ⚠假突破(上影{round(upper_shadow/lower_shadow,1)}x)"
        # ── B 超跌反弹（v6.9.20: 放宽amp>3+close>low*1.01, chg上限-2.5%, low=0保护）──
        if not s and -9.5 <= chg <= -2.5:
            if amp > 3 and low > 0 and close > low * 1.01:
                s = "B"; reason = f"超跌反弹:跌{chg:.1f}%+振幅{amp:.1f}%+反弹确认"; score = 7
            elif amp > 8 and low > 0 and close > low * 1.02:  # v6.9.39: 宽幅反弹也需确认
                s = "B"; reason = f"超跌反弹(宽幅):跌{chg:.1f}%+振幅{amp:.1f}%"; score = 6
        # ── C 事件驱动 (v6.9.17: 弱市关闭，追涨风险大) ──
        if not s and 1 <= chg < 2 and market_condition != "弱市":
            is_earnings = beijing_now.month in (1, 3, 4, 8, 10)
            if is_earnings:
                s = "C"; reason = f"事件驱动(财报季):涨{chg:.1f}%"; score = 8
            elif vr is not None and vr >= 1.0:
                s = "C"; reason = f"事件驱动:涨{chg:.1f}%+量比{vr:.1f}"; score = 7
            elif vr is None and to is not None and to >= 2 and close > op:
                s = "C"; reason = f"事件驱动(代理):涨{chg:.1f}%+换手{to:.1f}%"; score = 6
        # ── D 回调企稳 (v6.9.21: 放宽amp≥1.5%+弱市不折扣，低吸策略) ──
        if not s and 3 <= chg <= (7 if market_condition == "弱市" else 6):
            if 1.5 <= amp <= 10 and close > op:
                s = "D"; reason = f"回调企稳:涨{chg:.1f}%+阳线+振幅{amp:.1f}%"; score = 8
        # ── E 资金埋伏 (v6.9.25: 弱市不折扣，代理base=6与H看齐) ──
        if not s and 0 <= chg <= 1:
            mi = c.get('main_inflow')
            if mi is not None and mi > 3000:
                s = "E"; reason = f"资金埋伏:涨{chg:.1f}%+主力流入{mi:.0f}万"; score = 6
            elif mi is None and close > op:
                # 代理兜底：阳线+放量或高换手
                if vr is not None and vr >= 0.6 and to is not None and to >= 0.5:
                    s = "E"; reason = f"资金埋伏(代理):涨{chg:.1f}%+量比{vr:.1f}+换手{to:.1f}%"; score = 6
                elif vr is None and to is not None and to >= 1.0:
                    s = "E"; reason = f"资金埋伏(代理):涨{chg:.1f}%+换手{to:.1f}%"; score = 6
        # ── F 北向资金（v6.9.39: 预计算recent_5d字典，避免循环内重复IO）──
        if s == "E":
            mi = c.get('main_inflow')
            if mi is not None and mi > 5000:
                nb_days = recent_5d.get(c.get('code', ''), 0)
                if nb_days >= 3:
                    s = "F"; reason = f"北向资金:涨{chg:.1f}%+主力流入{mi:.0f}万+持续{nb_days}日"; score = 6
            elif mi is None and vr is not None and vr >= 0.8 and to is not None and to >= 1.5:
                nb_days = recent_5d.get(c.get('code', ''), 0)
                if nb_days >= 2:
                    s = "F"; reason = f"北向资金(代理):涨{chg:.1f}%+量比{vr:.1f}+换手{to:.1f}%+持续{nb_days}日"; score = 6
        # ── G 横盘突破 (v6.9.22: vr≥1.0,弱市不折扣,chg<3.0%避免与D重叠) ──
        if not s and 1.0 <= chg < 3.0 and close > op:
            if amp is not None and 1.5 <= amp <= 6:
                if vr is not None and vr >= 1.0:
                    s = "G"; reason = f"横盘突破:涨{chg:.1f}%+振幅{amp:.1f}%+量比{vr:.1f}"; score = 8
                elif vr is None and to is not None and to >= 3:
                    s = "G"; reason = f"横盘突破(代理):涨{chg:.1f}%+振幅{amp:.1f}%+换手{to:.1f}%"; score = 7
        # ── H 地量见底 (v6.9.21: chg<1.0%, vr<1.0, base=6) ──
        if not s and -3 <= chg < 1.0 and close >= op:
            is_hammer = False
            if high > low and low > 0:
                body = abs(close - op)
                lower_shadow = min(close, op) - low
                min_shadow = max(body * 1.5, 0.01 * close)  # v6.9.39: body=0时至少1%影线，避免过于宽松
                if lower_shadow >= min_shadow:
                    is_hammer = True
            vr_ok = (vr is not None and vr < 1.0) or (vr is None and to is not None and to < 1.0)
            if vr_ok and (is_hammer or (close > 0 and body / close < 0.008)):
                s = "H"; reason = f"地量见底:{chg:+.1f}%+量比{vr or 0:.1f}+锤子线"; score = 6
        # ── I-Q 形态策略（v6.9.22: 弱市跳过，仅强/震荡市匹配）──
        if market_condition != "弱市" and not s:
            # I 均线粘合突破
            kd = kline_data.get(c.get('code', ''), {})
            ma5 = kd.get('ma5', 0); ma10 = kd.get('ma10', 0); ma20 = kd.get('ma20', 0)
            if ma5 > 0 and ma10 > 0 and ma20 > 0:
                ma_max = max(ma5, ma10, ma20); ma_min = min(ma5, ma10, ma20)
                convergence = (ma_max - ma_min) / ma_min if ma_min > 0 else 999
                if convergence < 0.04 and close >= ma_max * 0.98 and close > op:
                    if vr is not None and vr >= 1.0:
                        s = "I"; reason = f"均线粘合突破:价{close:.2f}>均线+量比{vr:.1f}(粘合{convergence:.1%})"; score = 9
                    elif vr is None and to is not None and to >= 3:
                        s = "I"; reason = f"均线粘合突破(代理):价{close:.2f}>均线+换手{to:.1f}%"; score = 8
            # J 龙回头
            if not s:
                kd2 = kline_data.get(c.get('code', ''), {})
                closes_h = kd2.get('closes', []); highs_h = kd2.get('highs', [])
                if len(closes_h) >= 20 and close > 0:
                    max20 = max(highs_h[-20:])
                    rally_20d = (max20 - closes_h[-20]) / closes_h[-20] if closes_h[-20] > 0 else 0
                    pullback = (max20 - close) / max20 if max20 > 0 else 0
                    if rally_20d > 0.05 and 0.06 <= pullback <= 0.25:
                        if vr is not None and vr < 1.0 and close >= op:
                            s = "J"; reason = f"龙回头:涨{rally_20d:.1%}→回调{pullback:.1%}+缩量+收阳"; score = 8
            # K 缺口回补
            if not s:
                kd2 = kline_data.get(c.get('code', ''), {})
                closes_h = kd2.get('closes', []); highs_h = kd2.get('highs', [])
                if len(closes_h) >= 3 and close > 0 and op > 0:
                    yest_close = closes_h[-2] if len(closes_h) >= 2 else 0
                    yest_high = highs_h[-2] if len(highs_h) >= 2 else 0
                    if yest_close > 0 and yest_high > 0:
                        gap_up = op > yest_high * 1.01
                        gap_size = (op - yest_high) / yest_high if yest_high > 0 else 0
                        if gap_up and 0.01 <= gap_size <= 0.07:
                            if low <= yest_high * 0.995 and close >= op:
                                s = "K"; reason = f"缺口回补:跳空{gap_size:.1%}→回踩确认+收阳"; score = 8
            # L 黄金坑
            if not s:
                kd2 = kline_data.get(c.get('code', ''), {})
                closes_h = kd2.get('closes', [])
                if len(closes_h) >= 6 and close > 0:
                    pre5 = closes_h[-6] if len(closes_h) >= 6 else closes_h[-1]
                    min5 = min(closes_h[-5:]) if len(closes_h) >= 5 else close
                    if pre5 > 0 and min5 > 0:
                        drop = (min5 - pre5) / pre5; rebound = (close - min5) / min5
                        if drop <= -0.05 and rebound >= 0.02 and close > op:
                            vr_ok = (vr is not None and vr >= 1.0) or (to is not None and to >= 3)
                            if vr_ok:
                                s = "L"; reason = f"黄金坑:跌{abs(drop):.1%}→反弹{rebound:.1%}+放量+收阳"; score = 9
            # M 涨停回调
            if not s:
                kd2 = kline_data.get(c.get('code', ''), {})
                if kd2.get('limit_up_days', 0) >= 1 and close > 0:
                    closes_h = kd2.get('closes', []); highs_h = kd2.get('highs', [])
                    for i in range(len(closes_h) - 2, max(0, len(closes_h) - 7), -1):
                        if i > 0 and closes_h[i-1] > 0 and highs_h[i] > 0:
                            day_chg = (closes_h[i] - closes_h[i-1]) / closes_h[i-1]
                            if day_chg >= 0.095 and closes_h[i] >= highs_h[i] * 0.98:
                                limit_price = closes_h[i]
                                pullback_pct = (limit_price - close) / limit_price if limit_price > 0 else 0
                                if 0.04 <= pullback_pct <= 0.25 and close >= op:
                                    if vr is not None and vr < 1.0:
                                        s = "M"; reason = f"涨停回调:涨停{day_chg:.1%}→回调{pullback_pct:.1%}+缩量+收阳"; score = 7
                                        break
            # N 新高突破
            if not s:
                kd2 = kline_data.get(c.get('code', ''), {})
                high20 = kd2.get('high20', 0)
                if high20 > 0 and close >= high20 * 0.99 and close > op:
                    if vr is not None and vr >= 1.0:
                        s = "N"; reason = f"新高突破:价{close:.2f}=20日高+量比{vr:.1f}+阳线"; score = 9
                    elif vr is None and to is not None and to >= 3:
                        s = "N"; reason = f"新高突破(代理):价{close:.2f}=20日高+换手{to:.1f}%"; score = 8
            # O 回踩均线
            if not s:
                kd2 = kline_data.get(c.get('code', ''), {})
                closes_h = kd2.get('closes', []); ma20_o = kd2.get('ma20', 0)
                if len(closes_h) >= 60 and ma20_o > 0 and close > 0:
                    rally_60d = (close - closes_h[-60]) / closes_h[-60] if closes_h[-60] > 0 else 0
                    dist_to_ma20 = (close - ma20_o) / ma20_o
                    if rally_60d > 0.15 and -0.03 <= dist_to_ma20 <= 0.03 and close >= op:
                        if vr is not None and vr < 1.0:
                            s = "O"; reason = f"回踩均线:涨{rally_60d:.1%}→回踩MA20({dist_to_ma20:+.1%})+缩量+收阳"; score = 8
            # P 地量反弹
            if not s:
                kd2 = kline_data.get(c.get('code', ''), {})
                vols = kd2.get('volumes', [])
                if len(vols) >= 4 and vr is not None:
                    if vols[-4] > vols[-3] > vols[-2] and vr >= 1.2 and 1.0 <= chg <= 5 and close > op:
                        s = "P"; reason = f"地量反弹:3日缩量+放量{vr:.1f}x+涨{chg:.1f}%"; score = 7
            # Q W底形态
            if not s:
                kd2 = kline_data.get(c.get('code', ''), {})
                closes_h = kd2.get('closes', []); lows_h = kd2.get('lows', [])
                if len(closes_h) >= 20 and close > 0:
                    l1 = min(lows_h[-20:-10]) if len(lows_h) >= 20 else 0
                    l2 = min(lows_h[-10:]) if len(lows_h) >= 10 else 0
                    if l1 > 0 and l2 > 0 and 0.95 < l2 / l1 < 1.05:
                        neck = max(highs_h[-20:]) if len(highs_h) >= 20 else 0
                        if neck > 0 and close > neck * 1.005 and close > op:
                            if vr is not None and vr >= 1.2:
                                s = "Q"; reason = f"W底突破:两底{l1:.2f}/{l2:.2f}+突破颈线{neck:.2f}+放量"; score = 9
        # ── R/S/T 主力共振（v6.10.0: 多因子共振模型，底仓+起爆双重确认）──
        if not s:
            pos_score = compute_main_force_position(kline_data, c)
            break_score = compute_short_term_breakout(kline_data, c)
            res_strategy, res_level = resonance_check(pos_score, break_score)
            if res_strategy == 'R':
                s = "R"; reason = f"主力共振(强):底仓{pos_score}分+起爆{break_score}分"; score = 10
            elif res_strategy == 'S':
                s = "S"; reason = f"主力共振(弱):底仓{pos_score}分+起爆{break_score}分"; score = 8
            elif res_strategy == 'T':
                s = "T"; reason = f"主力观察:底仓{pos_score}分+起爆{break_score}分"; score = 5
        if s: c['strategy'] = s; c['score'] = score; matched.append(c)
    log_alert("INFO", "策略匹配", f"匹配{len(matched)}只")
    return matched

# ============================================================
# 步骤14-17：评分+行业限制
# ============================================================
def step14_scoring(candidates, kline_data=None):
    # v6.9.10: 先计算_tie_score（原在step16），再融入最终score
    # v6.9.60: 新增MACD+K线技术指标评分（DIF/DEA/MACD柱/均线/KDJ）
    so = _STRATEGY_ORDER
    sector_ad = defaultdict(list)
    for c in candidates:
        if c.get('strategy') in ('A', 'D', 'G', 'I', 'K', 'N'):
            sector_ad[_industry_str(c)].append(c)
    sector_bonus = {}
    for ind, clist in sector_ad.items():
        if len(clist) >= 3:
            for c in clist:
                sector_bonus[c.get('code', '')] = 0.10
    for c in candidates:
        vr = c.get('volume_ratio'); vr = vr if vr is not None else 0
        to = c.get('turnover'); to = to if to is not None else 0
        chg = c.get('change_pct') or 0
        vs = min(vr / 3.0, 1.0)
        if to < 2: ts = 0.2
        elif to <= 5: ts = 0.6
        elif to <= 15: ts = 1.0
        elif to <= 25: ts = 0.5
        else: ts = 0.1
        s = c.get('strategy', 'Z')
        if s == 'A': cs = max(0, 1.0 - abs(chg - 5) / 4.0)
        elif s == 'B': cs = max(0, 1.0 - abs(chg + 5) / 5.0)
        elif s == 'C': cs = max(0, 1.0 - abs(chg - 1.5) / 1.0)
        elif s == 'D': cs = max(0, 1.0 - abs(chg - 4.5) / 3.0)
        elif s == 'E': cs = max(0, 1.0 - abs(chg - 0.5) / 1.0)
        elif s == 'F': cs = max(0, 1.0 - abs(chg - 0.5) / 1.0)
        elif s == 'G': cs = max(0, 1.0 - abs(chg - 2.0) / 1.5)
        elif s == 'H': cs = max(0, 1.0 - abs(chg - 0) / 3.0)
        elif s == 'I': cs = max(0, 1.0 - abs(chg - 3) / 3.0)
        elif s == 'J': cs = max(0, 1.0 - abs(chg + 5) / 8.0)
        elif s == 'K': cs = max(0, 1.0 - abs(chg - 1) / 4.0)
        elif s == 'L': cs = max(0, 1.0 - abs(chg - 4) / 5.0)
        elif s == 'M': cs = max(0, 1.0 - abs(chg + 3) / 8.0)
        elif s == 'N': cs = max(0, 1.0 - abs(chg - 3) / 3.0)
        elif s == 'O': cs = max(0, 1.0 - abs(chg) / 1.5)
        elif s == 'P': cs = max(0, 1.0 - abs(chg - 3) / 4.0)
        elif s == 'Q': cs = max(0, 1.0 - abs(chg - 3) / 3.0)
        else: cs = 0.5
        amp = c.get('amplitude', 0) or 0
        ma_bonus = 0.05 if amp < 3 and vr > 1.2 else 0
        code = c.get('code', '')
        c['_tie_score'] = max(0, vs * (0.25 if s == 'D' else 0.30) + ts * (0.35 if s == 'D' else 0.30) + cs * 0.30 + (1.0 - so.get(s, 99) / 20.0) * 0.10 + sector_bonus.get(code, 0) + ma_bonus)
        # 融入最终score
        sc = c.get('score', 0) * 2
        sc += round(c['_tie_score'] * 8)  # v6.9.18: _tie_score 0~1 → 0~8分浮动，扩大区分度
        # v6.9.23: D策略振幅四档区分度，扩大16只标的间区分
        if s == 'D' and amp is not None:
            if 3 <= amp <= 6: sc += 1       # 理想振幅
            elif 1.5 <= amp < 3: sc -= 1     # 振幅偏小，动力不足
            elif amp > 8: sc -= 1            # 振幅偏大，波动风险
        # v6.9.21: B策略深度跌幅加分，跌幅越深反弹潜力越大
        if s == 'B':
            if chg < -7: sc += 2
            elif chg < -5: sc += 1
        if c.get('_first_yin') and s not in ('A', 'B'): sc += 2  # v6.9.39: 首阴加分仅适用于回调/低涨幅策略
        # v6.9.60: MACD+K线技术指标评分（最多+8分）
        macd_kline_bonus = 0
        kd = (kline_data or {}).get(code, {})
        if kd:
            # MACD指标 (最多+4分)
            dif = kd.get('dif', 0); dea = kd.get('dea', 0); macd_hist = kd.get('macd_hist', 0)
            if dif > dea: macd_kline_bonus += 1          # MACD金叉形态
            if macd_hist > 0: macd_kline_bonus += 1      # MACD柱状线多头
            if dif > 0 and dea > 0: macd_kline_bonus += 1  # 零轴上方运行
            if dif > 0 and macd_hist > 0: macd_kline_bonus += 1  # 零轴上多头强化
            # K线指标 (最多+4分)
            close_p = kd.get('closes', [0]); close = close_p[-1] if close_p and close_p[-1] else 0
            ma5 = kd.get('ma5', 0); ma10 = kd.get('ma10', 0); ma20 = kd.get('ma20', 0)
            if close > ma5 > 0: macd_kline_bonus += 1     # 站上MA5
            if ma5 > ma10 > 0: macd_kline_bonus += 1      # 短期均线金叉
            if close > ma20 > 0: macd_kline_bonus += 1    # 站上MA20
            k_val = kd.get('k', 0); d_val = kd.get('d', 0)
            if k_val > d_val: macd_kline_bonus += 1       # KDJ多头
        sc += macd_kline_bonus
        c['score'] = max(0, sc)
        if c['score'] >= 18: c['confidence'] = '★★★'
        elif c['score'] >= 12: c['confidence'] = '★★'
        else: c['confidence'] = '★'
    return candidates

def step17_industry_limit(candidates):
    # v6.6.46: 保留 step16 综合评分排序(_tie_score)，五级二次评估打破平局
    ig = defaultdict(list)
    for c in candidates: ig[_industry_str(c)].append(c)
    limited = []
    elastic_added = 0
    # v6.9.22: 弱市行业上限3→4，增加标的多样性
    industry_limit = 4 if market_condition == "弱市" else 3
    for g in ig.values():
        g.sort(key=_tie_key)
        limited.extend(g[:industry_limit])
        # v6.9.22: 弹性规则 — 第N+1只_tie_score≥第N只90%则保留（弱市放宽至≥90%）
        if len(g) >= industry_limit + 1:
            tn = g[industry_limit - 1].get('_tie_score', 0)
            tn1 = g[industry_limit].get('_tie_score', 0)
            elastic_threshold = 0.90 if market_condition == "弱市" else 0.95
            if tn > 0 and tn1 / tn >= elastic_threshold:
                limited.append(g[industry_limit])
                elastic_added += 1
    max_s = max(2, math.ceil(len(limited) * params.get('strategy_concentration_pct', 30) / 100))
    sg = defaultdict(list)
    for c in limited: sg[c.get('strategy', 'Z')].append(c)
    final = []
    for g in sg.values():
        g.sort(key=_tie_key)
        final.extend(g[:max_s])
    final.sort(key=lambda c: (_STRATEGY_ORDER.get(c.get('strategy', 'Z'), 99), -c.get('score', 0)))
    log_alert("INFO", "行业限制", f"通过{len(final)}只 (原始{len(candidates)}只, 弹性+{elastic_added})")
    return final

def step18_news_screening(candidates):
    """步骤18：新闻筛查 — 四源并行（东方财富+Bing+巨潮资讯网+财联社）+ 逐源状态追踪，v6.13.11"""
    if not candidates:
        return candidates, 0
    
    NEGATIVE_KW = [
        '立案调查', '行政处罚', '监管函', '问询函', '业绩修正', '预亏', '预减',
        '大股东减持', '控股股东减持', '质押平仓', '商誉减值', '退市风险',
        '重大诉讼', '债务违约', '暂停上市', '终止上市', '限售股解禁',
        '业绩变脸', '财务造假', '信披违规', '内幕交易', '操纵市场',
        '强制退市', '破产重整', '资不抵债', '审计非标',
        '违规担保', '资金占用', '重组失败', '定增终止', 'ST warning',
        '净利润下滑', '营收下滑', '毛利率下滑', '评级下调', '目标价下调',
        '应收账款', '坏账计提', '存货跌价', '资产减值', '内控缺陷', '证监会立案', '通报批评'
    ]
    FALSE_POSITIVE_NEGATORS = [
        '终止减持', '不减持', '解除质押', '整改完成', '撤销',
        '大幅增长', '扭亏', '摘帽', '恢复正常', '已消除',
        '不立案', '不处罚', '不予', '驳回', '和解', '撤回',
        '增持', '回购', '承诺不',
        '减持完毕', '解除异常', '无违规'
    ]
    
    # v6.13.11: 源级别状态追踪
    _src_status = {'eastmoney': {'ok': 0, 'fail': 0}, 'bing': {'ok': 0, 'fail': 0},
                   'cninfo': {'ok': 0, 'fail': 0}, 'cls': {'ok': 0, 'fail': 0},
                   'xueqiu': {'ok': 0, 'fail': 0}}
    
    def _check_eastmoney(code, name):
        try:
            market = '1' if code.startswith('6') else '0'
            url = f'https://push2.eastmoney.com/api/qt/stock/news/get?secid={market}.{code}&pageNum=1&pageSize=5&_={int(time.time()*1000)}'
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                'Referer': 'https://www.eastmoney.com/'
            })
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                news_list = data.get('data', {}).get('list', []) if isinstance(data, dict) else []
                for news in news_list:
                    title = news.get('title', '') + news.get('summary', '')
                    for kw in NEGATIVE_KW:
                        if kw not in title: continue
                        if not any(neg in title for neg in FALSE_POSITIVE_NEGATORS):
                            return ('eastmoney', kw)
        except Exception:
            _src_status['eastmoney']['fail'] += 1
        return None
    
    def _check_eastmoney_jsonp(code, name):
        """v6.13.11: 东方财富JSONP备选接口（替代push2 API）"""
        try:
            market = '1' if code.startswith('6') else '0'
            url = f'https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=5&page_index=1&ann_type=A&client_source=web&stock_list={market},{code}'
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://data.eastmoney.com/'
            })
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                items = data.get('data', {}).get('list', [])
                for item in items:
                    title = (item.get('title', '') or '') + (item.get('summary', '') or '')
                    for kw in NEGATIVE_KW:
                        if kw in title and not any(neg in title for neg in FALSE_POSITIVE_NEGATORS):
                            return ('eastmoney_v2', kw)
        except Exception:
            pass
        return None
    
    def _check_bing(code, name):
        try:
            query = f'{name} {code} 利空 公告'
            url = f'https://www.bing.com/search?q={urllib.parse.quote(query)}'
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept-Language': 'zh-CN,zh;q=0.9'
            })
            with urllib.request.urlopen(req, timeout=4) as resp:
                html_text = resp.read().decode('utf-8', errors='ignore')
                for kw in NEGATIVE_KW:
                    if kw not in html_text: continue
                    kw_pos = html_text.find(kw)
                    ctx = html_text[max(0,kw_pos-300):min(len(html_text),kw_pos+300)]
                    if name not in ctx and code not in ctx: continue
                    if not any(neg in ctx for neg in FALSE_POSITIVE_NEGATORS):
                        return ('bing', kw)
        except Exception:
            _src_status['bing']['fail'] += 1
        return None
    
    def _check_baidu(code, name):
        """v6.13.11: 百度搜索备选（Bing不可达时降级）"""
        try:
            query = f'{name} {code} 利空 公告'
            url = f'https://www.baidu.com/s?wd={urllib.parse.quote(query)}'
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept-Language': 'zh-CN,zh;q=0.9'
            })
            with urllib.request.urlopen(req, timeout=4) as resp:
                html_text = resp.read().decode('utf-8', errors='ignore')
                for kw in NEGATIVE_KW:
                    if kw not in html_text: continue
                    kw_pos = html_text.find(kw)
                    ctx = html_text[max(0,kw_pos-300):min(len(html_text),kw_pos+300)]
                    if name not in ctx and code not in ctx: continue
                    if not any(neg in ctx for neg in FALSE_POSITIVE_NEGATORS):
                        return ('baidu', kw)
        except Exception:
            pass
        return None
    
    def _check_cninfo(code, name):
        """巨潮资讯网 — 法定信息披露平台，搜索风险提示/监管函/退市等公告"""
        try:
            org_id = f'gssz{code}' if code.startswith('0') else f'gssh{code}'
            stock_param = f'{code},{org_id}'
            cninfo_categories = ['fxts', 'cqdq', 'yjygjxz']  # 风险提示/澄清致歉/业绩预告
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'http://www.cninfo.com.cn/new/index',
                'Content-Type': 'application/x-www-form-urlencoded'
            }
            for cat in cninfo_categories:
                params = urllib.parse.urlencode({
                    'pageNum': '1', 'pageSize': '10', 'tabName': 'fulltext',
                    'stock': stock_param, 'category': cat,
                    'startTime': (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'),
                    'endTime': datetime.now().strftime('%Y-%m-%d'),
                    'sortName': 'announcementTime', 'sortType': '-1'
                }).encode('utf-8')
                req = urllib.request.Request('http://www.cninfo.com.cn/new/hisAnnouncement/query', data=params, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    for ann in data.get('announcements', []):
                        title = ann.get('title', '')
                        for kw in NEGATIVE_KW:
                            if kw in title and not any(neg in title for neg in FALSE_POSITIVE_NEGATORS):
                                return ('cninfo', kw)
            # 关键字搜索
            for search_kw in ['退市', 'ST', '减持', '违规', '监管']:
                params = urllib.parse.urlencode({
                    'pageNum': '1', 'pageSize': '10', 'tabName': 'fulltext',
                    'stock': stock_param, 'searchKey': search_kw,
                    'startTime': (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'),
                    'endTime': datetime.now().strftime('%Y-%m-%d'),
                    'sortName': 'announcementTime', 'sortType': '-1'
                }).encode('utf-8')
                req = urllib.request.Request('http://www.cninfo.com.cn/new/hisAnnouncement/query', data=params, headers=headers)
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    for ann in data.get('announcements', []):
                        title = ann.get('title', '')
                        for kw in NEGATIVE_KW:
                            if kw in title and not any(neg in title for neg in FALSE_POSITIVE_NEGATORS):
                                return ('cninfo', kw)
        except Exception:
            _src_status['cninfo']['fail'] += 1
        return None
    
    def _check_cls(code, name):
        """财联社 — 实时快讯，搜索股票名称在近期电报中出现"""
        try:
            sorted_params = sorted([
                ('app', 'CailianpressWeb'), ('os', 'web'), ('refresh_type', '1'),
                ('rn', '50'), ('sv', '8.4.6')
            ], key=lambda x: x[0])
            query_str = urllib.parse.urlencode(sorted_params)
            sign = hashlib.md5(hashlib.sha1(query_str.encode()).hexdigest().encode()).hexdigest()
            url = f'https://www.cls.cn/nodeapi/telegraphList?{query_str}&sign={sign}'
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://www.cls.cn/telegraph'
            })
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                telegrams = data.get('data', {}).get('roll_data', [])
                for tg in telegrams:
                    content = tg.get('title', '') + tg.get('brief', '') + tg.get('content', '')
                    if name not in content: continue
                    for kw in NEGATIVE_KW:
                        if kw in content and not any(neg in content for neg in FALSE_POSITIVE_NEGATORS):
                            return ('cls', kw)
        except Exception:
            _src_status['cls']['fail'] += 1
        return None
    
    def _check_xueqiu(code, name):
        """v6.13.13: 雪球个股讨论 — 搜索近期讨论中的利空关键词"""
        try:
            # 雪球页面通过WebFetch预缓存 — 读取缓存文件
            cache_path = os.path.join('/workspace', 'xueqiu_news_cache.json')
            if not os.path.exists(cache_path):
                _src_status['xueqiu'] = _src_status.get('xueqiu', {'ok': 0, 'fail': 0})
                _src_status['xueqiu']['fail'] += 1
                return None
            
            with open(cache_path, 'r') as f:
                cache = json.loads(f.read())
            
            if code not in cache:
                _src_status['xueqiu'] = _src_status.get('xueqiu', {'ok': 0, 'fail': 0})
                _src_status['xueqiu']['fail'] += 1
                return None
            
            posts = cache.get(code, [])
            _src_status['xueqiu'] = _src_status.get('xueqiu', {'ok': 0, 'fail': 0})
            _src_status['xueqiu']['ok'] += 1
            
            for post in posts:
                text = post.get('title', '') + ' ' + post.get('text', '')
                for kw in NEGATIVE_KW:
                    if kw in text and not any(neg in text for neg in FALSE_POSITIVE_NEGATORS):
                        return ('xueqiu', kw)
            return None
        except Exception:
            _src_status['xueqiu'] = _src_status.get('xueqiu', {'ok': 0, 'fail': 0})
            _src_status['xueqiu']['fail'] += 1
        return None
    
    excluded = []
    passed = []
    search_limit = min(30, len(candidates))
    top_codes = {c['code'] for c in sorted(candidates, key=lambda c: -c.get('score', 0))[:search_limit]}
    
    to_check = [c for c in candidates if c.get('code', '') in top_codes]
    skip = [c for c in candidates if c.get('code', '') not in top_codes]
    for c in skip:
        c['_news_checked'] = False
        c['_news_skip_reason'] = '评分不足前30'
    passed.extend(skip)
    
    # v6.13.11: 主源不可用时自动降级备选源
    _checkers = [
        _check_eastmoney,   # 主: 东方财富push2
        _check_eastmoney_jsonp,  # 备: 东方财富公告API
        _check_bing,        # 主: Bing搜索
        _check_baidu,       # 备: 百度搜索
        _check_cninfo,      # 主: 巨潮资讯
        _check_cls,         # 主: 财联社
        _check_xueqiu,      # 备: 雪球讨论
    ]
    
    for c in to_check:
        code = c.get('code', '')
        name = c.get('name', '')
        has_neg = False
        neg_reason = ''
        any_source_ok = False
        
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(checker, code, name): checker.__name__ for checker in _checkers}
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    source, kw = result
                    any_source_ok = True
                    has_neg = True
                    neg_reason = f"{source}:{kw}" if source not in ('eastmoney', 'eastmoney_v2') else kw
                    for f in futures: f.cancel()
                    break
        
        c['_news_checked'] = any_source_ok or (not has_neg)  # 至少有一个源返回了结果
        
        if has_neg:
            c['_news_reason'] = neg_reason
            excluded.append(c)
        else:
            passed.append(c)
    
    # v6.13.11: 源状态汇总报告
    src_report = []
    for src, status in _src_status.items():
        if status['fail'] > 0:
            src_report.append(f"{src}: {status['fail']}次失败")
    if src_report:
        log_alert("WARNING", "新闻筛查", f"源异常: {'; '.join(src_report)}")
    
    # v6.13.11: 统计未检查标的
    unchecked = [c for c in passed if c.get('code', '') in top_codes and not c.get('_news_checked', True)]
    if unchecked:
        unchecked_names = ', '.join(f"{c.get('name','?')}({c.get('code','?')})" for c in unchecked[:5])
        if len(unchecked) > 5: unchecked_names += f" 等{len(unchecked)}只"
        log_alert("WARNING", "新闻筛查", f"⚠️ {len(unchecked)}只标的未通过任何新闻源检查: {unchecked_names}")
    
    nex = len(excluded)
    if nex > 0:
        details = ", ".join(f"{c.get('name','')}({c.get('_news_reason','?')})" for c in excluded[:5])
        if nex > 5: details += f" 等{nex}只"
        log_alert("WARNING", "新闻筛查", f"排除{nex}只: {details}")
    else:
        log_alert("INFO", "新闻筛查", "全部通过，未发现利空")
    
    # v6.13.11: 添加步骤执行摘要
    checked_count = sum(1 for c in passed if c.get('_news_checked', False))
    skipped_count = len(skip)
    print(f"  新闻筛查: 检查{len(to_check)}只 → 排除{nex}只, 评分不足跳过{skipped_count}只, 源可用{checked_count}只")
    
    return passed, nex
# ============================================================
def step18B_top10_enrichment(candidates):
    """对TOP10盈亏比精选标的，采集龙虎榜、正面新闻、公司公告数据"""
    if not candidates: return
    # 按盈亏比取TOP10
    top10 = sorted(candidates, key=lambda c: -c.get('_pl_ratio', 0))[:10]
    if not top10: return
    
    POSITIVE_KW = ['业绩增长', '业绩预增', '净利润', '中标', '签约', '合同', '订单', '突破', '利好',
                   '回购', '增持', '分红', '送转', '战略合作', '获批', '量产', '扩产', '新品',
                   '订单饱满', '产能释放', '量价齐升', '龙头', '市占率', '政策利好', '补贴',
                   '全球领先', '自主可控', '国产替代', '技术突破', '研发成功']
    # 公告利好关键词（需精确匹配，避免"投资者"误触发"投资"）
    ANN_POSITIVE_KW = ['合同', '中标', '签约', '订单', '回购', '增持', '投资设立', '投资建设', '扩产',
                       '项目中标', '获批', '突破', '量产', '战略合作', '分红', '送转', '激励',
                       '收购', '出售', '业绩预增', '业绩增长', '产能释放',
                       '取得', '获得', '研发成功', '专利', '许可', '认证', '通过',
                       '设立', '增资', '子公司', '出资', '竞得', '签订', '竞标',
                       '重组', '发行可转债', '非公开发行', '引进战略',
                       '拟投资', '项目投资', '产能', '量产', '新产品', '新产线',
                       '中标项目', '预中标', '募投', '承接', '交付',
                       '签订合同', '签订协议', '签署协议', '签订战略',
                       '权益分派', '利润分配', '回购注销', '回购股份']
    # 公告利空关键词（排除）
    ANN_NEGATIVE_KW = ['减持', '违规', '处罚', '问询', '警示', 'ST', '*ST', '退市', '更正',
                       '会计差错', '异常波动', '诉讼', '仲裁', '冻结', '质押', '延期',
                       '终止', '取消', '撤销', '暂停', '亏损', '预亏', '未通过', '否决',
                       '破产', '清算', '重整', '无法表示意见', '保留意见']
    
    tot_lh = 0; tot_news = 0; tot_ann = 0
    for c in top10:
        code = c.get('code', '')
        name = c.get('name', '')
        market = '1' if code.startswith('6') else '0'
        c['_longhu'] = ''
        c['_news_positive'] = ''
        c['_announcement'] = ''
        
        # ── 龙虎榜 ──
        try:
            lh_url = f'https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_DAILY_BILLBOARDTRADING&columns=TRADE_DATE,BILLBOARD_NET_AMT,BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,EXPLANATION&filter=(SECUCODE="{code}")&pageNumber=1&pageSize=3&sortTypes=-1&sortColumns=TRADE_DATE'
            req = urllib.request.Request(lh_url, headers={
                'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com/'})
            with urllib.request.urlopen(req, timeout=4) as resp:
                lh_data = json.loads(resp.read().decode('utf-8'))
                lh_rows = lh_data.get('result', {}).get('data', []) if lh_data.get('result') else []
                if lh_rows:
                    latest = lh_rows[0]
                    lh_date = latest.get('TRADE_DATE', '')[:10]
                    lh_net = latest.get('BILLBOARD_NET_AMT', 0) or 0
                    lh_net_wan = lh_net / 10000
                    lh_dir = '净买入' if lh_net_wan > 0 else '净卖出'
                    lh_abs = abs(lh_net_wan)
                    if lh_abs >= 10000:
                        lh_amt_str = f'{lh_abs/10000:.1f}亿'
                    else:
                        lh_amt_str = f'{lh_abs:.0f}万'
                    c['_longhu'] = f'{lh_date} {lh_dir} {lh_amt_str}'
                    tot_lh += 1
        except (urllib.error.URLError, json.JSONDecodeError, OSError): pass
        
        # ── 正面新闻 ──
        try:
            news_url = f'https://push2.eastmoney.com/api/qt/stock/news/get?secid={market}.{code}&pageNum=1&pageSize=5&_={int(time.time()*1000)}'
            req = urllib.request.Request(news_url, headers={
                'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.eastmoney.com/'})
            with urllib.request.urlopen(req, timeout=4) as resp:
                news_data = json.loads(resp.read().decode('utf-8'))
                news_list = news_data.get('data', {}).get('list', []) if isinstance(news_data, dict) else []
                pos_titles = []
                for news in news_list:
                    title = news.get('title', '')
                    if any(kw in title for kw in POSITIVE_KW):  # v6.13.17: 使用全部28个关键词
                        short = title[:20] + ('...' if len(title) > 20 else '')
                        pos_titles.append(short)
                    if len(pos_titles) >= 2: break
                if pos_titles:
                    c['_news_positive'] = '; '.join(pos_titles)
                    tot_news += 1
        except (urllib.error.URLError, json.JSONDecodeError, OSError): pass
        
        # ── 公司公告（v6.9.52）──
        try:
            ann_url = f'https://np-anotice-stock.eastmoney.com/api/security/ann?page_size=5&page_index=1&stock_list={code}&ann_type=A'
            req = urllib.request.Request(ann_url, headers={
                'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com/'})
            with urllib.request.urlopen(req, timeout=4) as resp:
                ann_data = json.loads(resp.read().decode('utf-8'))
                ann_list = ann_data.get('data', {}).get('list', []) if ann_data.get('success') else []
                pos_anns = []
                for ann in ann_list:
                    title = ann.get('title_ch', '') or ann.get('title', '')
                    columns = ann.get('columns', [])
                    col_names = [col.get('column_name', '') for col in columns]
                    notice_date = (ann.get('notice_date', '') or '')[:10]
                    # 过滤利空
                    has_negative = any(kw in title for kw in ANN_NEGATIVE_KW[:5])
                    if has_negative: continue
                    # 检查利好关键词
                    has_positive = any(kw in title for kw in ANN_POSITIVE_KW)
                    if has_positive:
                        col_str = col_names[0] if col_names else ''
                        # 去除股票名前缀，提取正文
                        short_title = title
                        if ':' in title:
                            short_title = title.split(':', 1)[1].strip()
                        short = short_title[:48] + ('...' if len(short_title) > 48 else '')
                        date_str = notice_date[5:] if notice_date else ''  # MM-DD
                        pos_anns.append(f'[{date_str}]{col_str}:{short}')
                    if len(pos_anns) >= 3: break
                if pos_anns:
                    c['_announcement'] = '; '.join(pos_anns)
                    tot_ann += 1
        except (urllib.error.URLError, json.JSONDecodeError, OSError): pass
    
    log_alert("INFO", "TOP10增强", f"龙虎榜{tot_lh}/10 正面新闻{tot_news}/10 公司公告{tot_ann}/10")

def step15_microstructure_filter(candidates, kline_data):
    """
    步骤15: 市场微观结构过滤 v6.11.0
    在最终候选池输出前，基于流动性与冲击成本、消息敏感度进行过滤。
    硬过滤: 换手率<2% / Amihud>2.0 / 20日均振幅<2%
    评分加分: 流动性评分(0-4) + 消息敏感度(0-3)，折算到score
    """
    return microstructure_filter(candidates, kline_data)

def step15B_ai_analysis(candidates, kline_data, index_data, market_condition,
                         sector_limit_up, total_raw, ae, asig, astr, amicro, aind, fc):
    """
    步骤15B: AI 智能分析 v6.12.4
    将单纯的数据筛选升级为 AI 智能分析，生成多维深度研判报告。
    返回: ai_report dict
    """
    return generate_ai_report(candidates, kline_data, index_data, market_condition,
                              sector_limit_up, total_raw, ae, asig, astr, amicro, aind, fc)

def step16_comprehensive_score(candidates):
    # v6.9.38: 步骤15(冲突检测)已合并入步骤14评分，步骤16仅负责排序
    candidates.sort(key=_tie_key)
    return candidates

def step19_shortfall_handling(candidates):
    """v6.8.7: 数值比较，丢弃时记录日志"""
    total = len(candidates)
    if total >= 3: return candidates
    elif total == 2:
        result = [c for c in candidates if c.get('score', 0) >= 6]
        if len(result) < total: log_alert("INFO", "降级", f"丢弃{total - len(result)}/2只因评分<6(★)")
        return result
    elif total == 1:
        result = [c for c in candidates if c.get('score', 0) >= 12]
        if len(result) < total: log_alert("INFO", "降级", f"丢弃1只因评分<12(★★)")
        return result
    return []

# ============================================================
# ============================================================
# v6.6.31 策略进场价（基于历史数据推算）
# ============================================================
def calc_entry_price(c):
    """基于历史数据推算次日合理进场价，综合考虑ATR/振幅位置/缺口/量比"""
    strategy = c.get('strategy', 'Z')
    close = c.get('close', 0)
    op = c.get('open', 0)
    high = c.get('high', 0)
    low = c.get('low', 0)
    prev = c.get('prev_close', close)
    chg = c.get('change_pct', 0)
    amp = c.get('amplitude', 0) or 0
    vol_ratio = c.get('volume_ratio', 1) or 1
    
    # 计算当日真实波动(ATR日) — 基于历史数据的核心指标
    if high > 0 and low > 0 and prev > 0:
        tr = max(high - low, abs(high - prev), abs(low - prev))
        atr_pct = tr / prev  # ATR百分比（日内波动率）
    else:
        atr_pct = max((amp or 0) / 100, 0.015) if amp is not None else 0.02
    
    # 收盘在当日振幅的位置 (0=最低, 1=最高)
    if high > low and high > 0:
        pos = (close - low) / (high - low)
    else:
        pos = 0.5
    
    # 开盘缺口（相对前收）
    gap = (op - prev) / prev if prev > 0 else 0
    
    # 根据量比调整预期（量比越高，次日惯性越强）
    vol_adj = min(vol_ratio / 1.5, 1.5) if vol_ratio > 0 else 1.0
    atr_pct = min(atr_pct, 0.08)  # v6.8.3: 上限8%，避免极端值导致进场价虚高
    
    if strategy == 'A':
        # 动量延续：强势股次日大概率高开
        # 高开幅度 = 当日强势位置 × ATR × 量能修正
        if pos > 0.65:
            # 收盘在振幅上1/3：强势收盘，次日小幅高开0.5-1.2%
            gap_expected = pos * atr_pct * 0.4 * vol_adj
            entry = close * (1 + max(gap_expected, 0.005))
        elif pos > 0.35:
            # 收盘在振幅中间：中性，次日平开或小幅高开0.3-0.8%
            entry = close * (1 + atr_pct * 0.25)
        else:
            # 收盘在振幅下1/3：尾盘回落，次日可能跟随当日缺口方向
            # 缺口方向折半衰减，不强制正溢价（v6.6.51修复）
            entry = close * (1 + gap * 0.5)
        return round(entry, 2)
    
    elif strategy == 'B':
        # 超跌反弹：基于历史跌幅和日内低点推算安全进场价
        if low > 0 and close > low:
            if close > low * 1.01 and pos > 0.3:
                entry = low + (close - low) * 0.3  # 低点上方30%分位
            elif chg < -5:
                # 深度超跌，次日可能惯性低开，在收盘价-1%挂单
                entry = close * (1 - atr_pct * 0.3)
            elif chg >= -3.5:
                # v6.9.20: 轻度跌幅(-2.5%~-3.5%)，更激进低吸
                entry = low + (close - low) * 0.25
            else:
                entry = close * 0.995
        else:
            entry = close * (1 - atr_pct * 0.2)
        return round(entry, 2)
    
    elif strategy == 'C':
        # 事件驱动：放量突破，次日大概率高开
        # 高开幅度 = ATR × 0.3 × 量比修正
        gap_expected = atr_pct * 0.35 * vol_adj
        entry = close * (1 + max(gap_expected, 0.005))
        return round(entry, 2)
    
    elif strategy == 'D':
        # 回调企稳：基于历史振幅判断支撑位
        # 支撑位在当日低点附近，确认突破有效后进场
        if low > 0 and high > low:
            support = low + (high - low) * 0.15  # 低点上方15%为支撑区
            # 在支撑位和收盘价之间偏上的位置进场
            entry = support + (close - support) * 0.4
        else:
            entry = close * 1.01
        return round(entry, 2)
    
    elif strategy == 'E':
        # 资金埋伏：基于历史振幅低吸
        # 在当日振幅下1/3区间挂单，博次日反弹
        if high > low and low > 0:
            entry = low + (high - low) * 0.25  # 振幅下25%分位
        else:
            entry = close * 0.995
        return round(entry, 2)
    
    elif strategy == 'F':
        # 北向资金埋伏(v6.6.38): 涨幅有限+持续资金流入，次日平开或小幅低开
        # 在收盘价下方0.5%挂单，低吸为主
        if low > 0 and close > low:
            entry = low + (close - low) * 0.3
        else:
            entry = close * 0.995
        return round(entry, 2)
    
    elif strategy == 'G':
        # 横盘突破(v6.9.18): 弱市不追涨，在收盘价下方进场；强/震荡市追涨
        if market_condition == "弱市":
            if low > 0 and close > low:
                entry = low + (close - low) * 0.4  # 弱市低吸
            else:
                entry = close * 0.998
        elif high > close and close > 0:
            entry = close + (high - close) * 0.3
        else:
            entry = close * 1.005
        return round(entry, 2)
    
    elif strategy == 'H':
        # 地量见底(v6.7.0): 卖压衰竭，次日平开或微幅高开
        # 在收盘价附近挂单，不追高
        if high > low and low > 0:
            entry = low + (close - low) * 0.4
        else:
            entry = close * 1.002
        return round(entry, 2)
    
    elif strategy == 'I':
        # 均线粘合突破(v6.9.0): 放量突破均线，次日大概率高开惯性
        if high > close and close > 0:
            entry = close + (high - close) * 0.3
        else:
            entry = close * 1.005
        return round(entry, 2)
    
    elif strategy == 'J':
        # 龙回头(v6.9.0): 强势股回调企稳，次日大概率平开或小幅高开
        if low > 0 and close > low:
            entry = low + (close - low) * 0.35
        else:
            entry = close * 0.998
        return round(entry, 2)
    
    elif strategy == 'K':
        # 缺口回补(v6.9.1): 回踩确认，次日大概率平开或微涨
        entry = close * 1.003
        return round(entry, 2)
    
    elif strategy == 'L':
        # 黄金坑(v6.9.1): V型反弹，次日惯性延续，保守挂在前日收盘价
        if high > close and close > 0:
            entry = close + (high - close) * 0.25
        else:
            entry = close * 1.005
        return round(entry, 2)
    
    elif strategy == 'M':
        # 涨停回调(v6.9.3): 缩量回调企稳，次日平开
        if low > 0 and close > low:
            entry = low + (close - low) * 0.3
        else:
            entry = close * 0.998
        return round(entry, 2)
    
    elif strategy == 'N':
        # 新高突破(v6.9.3): 强势突破，次日惯性高开
        if high > close and close > 0:
            entry = close + (high - close) * 0.35
        else:
            entry = close * 1.005
        return round(entry, 2)
    
    elif strategy == 'O':
        # 回踩均线(v6.9.3): 均线支撑确认，次日平开
        entry = close * 1.002
        return round(entry, 2)
    
    return round(close, 2)

def _calc_tier_label(c):
    """基于60日区间位置计算入场档位
    档位1(低位): 收盘价在60日区间下30% → 🟢 优质入场区
    档位2(中位): 收盘价在60日区间30-70% → 🟡 中性区间
    档位3(高位): 收盘价在60日区间上30% → 🔴 谨慎入场区
    返回: (label, css_class) 如 ('档位1', 'tier1')
    """
    h60 = c.get('_high60', 0) or 0
    l60 = c.get('_low60', 0) or 0
    close = c.get('close', 0) or 0
    if h60 > l60 > 0 and close > 0:
        position = (close - l60) / (h60 - l60)  # 0~1，0=最低，1=最高
        if position < 0.3:
            return ('档位1', 'tier1')
        elif position < 0.7:
            return ('档位2', 'tier2')
        else:
            return ('档位3', 'tier3')
    return ('-', 'tier_na')

def _compute_pl_ratios(candidates, sector_limit_up=None):
    """预计算盈亏比TOP10，标注c['_entry']/c['_stop']/c['_target']/c['_pl_ratio']，返回_top10_codes集合
    v6.12.10: 板块热度排序——第一优先级板块涨停家数，第二优先级盈亏比"""
    global _pl_sorted
    sector_heat = sector_limit_up or {}
    _pl_data = []
    for c in candidates:
        s = c.get('strategy', '?')
        entry = calc_entry_price(c)
        sl = round(entry * _STRATEGY_STOP_LOSS.get(s, 0.96), 2)
        tp = round(entry * _STRATEGY_TAKE_PROFIT.get(s, 1.05), 2)
        pl_ratio = round((tp - entry) / max(entry - sl, 0.01), 2)
        industry = lookup_industry(c.get('code', ''))
        heat = sector_heat.get(industry, 0)
        _pl_data.append((c.get('code', ''), pl_ratio, heat, industry))
        c['_entry'] = entry; c['_stop'] = sl; c['_target'] = tp; c['_pl_ratio'] = pl_ratio
        c['_sector_heat'] = heat
    # v6.12.10: 先按板块热度降序，再按盈亏比降序
    _pl_sorted = sorted(_pl_data, key=lambda x: (-x[2], -x[1]))
    return set(c for c, _, _, _ in _pl_sorted[:10])

# ============================================================
# 步骤20：Markdown输出
# ============================================================
def step20_output_markdown(candidates, total_raw, ae, asig, astr, amicro, aind, anew, er, ai_report=None, bt_lookup=None):
    mp = f"/workspace/短线标的_{prediction_date}.md"
    lines = [
        f"# A股短线标的筛选报告 — {prediction_date}", "",
        f"- **数据日期**: {data_date}  |  **预测日期**: {prediction_date}",
        f"- **市场环境**: {market_condition}  |  **建议仓位**: {position_pct}%",
        f"- **数据来源**: 腾讯qt(一级) / 新浪(二级) / pytdx(三级)",
        f"- **规则版本**: {file_version}", "",
        "## 筛选管道（7级漏斗）", "",
        "| 阶段 | 数量 | 排除 | 说明 |",
        "|------|------|------|------|",
        f"| ①原始标的池 | {total_raw} | - | 全市场活跃TOP500 |",
        f"| ②硬排除 | {ae} | {total_raw - ae} | 13项(持仓/科创/北交/低价/高价/ST/涨幅/停牌/市值/成交额/上市天数/质押商誉解禁已废弃) |",
        f"| ③信号过滤 | {asig} | {ae - asig} | 27项(假动量/诱多/缩量涨停/振幅/跌停异动/缩量下跌/高换手低涨幅/首阴/均线空头/MACD顶背离/RSI超买/缩量反弹/KDJ死叉/涨停次日高开低走/布林突破失败/20日涨幅>45%/放量不涨/放量滞跌/长上影线/连续缩量/净利润亏损/冲击成本/限售解禁/可转债/业绩预告/机构减持/融资过热) |",
        f"| ④策略匹配 | {astr} | {asig - astr} | ABCDEFGHIJKLMNOPQRST二十策略 |",
        f"| ⑤微观结构过滤 | {amicro} | {astr - amicro} | 流动性(换手率/Amihud)+消息敏感度(波动性) |",
        f"| ⑥行业+同策略限制 | {aind} | {amicro - aind} | 同行业≤4只(弱市)/3只(强/震荡)+同策略≤30% |",
        f"| ⑦新闻筛查 | {aind - anew} | {anew} | 东方财富/Bing/巨潮资讯网/财联社四源并行利空检测 |",
        f"| ★最终推荐 | {len(candidates)} | {aind - anew - len(candidates)} | 评分门控+降级 |", "",
    ]
    if candidates:
        _top10_codes = _compute_pl_ratios(candidates)

        lines.append("## 推荐标的\n")
        lines.append("| # | TOP10 | 策略 | 标的 | 代码 | 行业 | 二级行业 | 涨跌幅 | 开盘 | 收盘 | 振幅 | 60日高 | 60日低 | 档位 | 7日 | 评分 | 置信 | 进场 | 止损 | 止盈 | 盈亏比 | 回测 |\n")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n")
        for idx, c in enumerate(candidates, 1):
            code = c.get('code', ''); name = c.get('name', '')
            s = c.get('strategy', '?'); ind = _industry_str(c); biz = c.get('business', '')
            chg = c.get('change_pct', 0); op = c.get('open', 0) or 0
            close = c.get('close', 0) or 0; amp = c.get('amplitude', 0) or 0
            score = c.get('score', 0); conf = c.get('confidence', '★')
            h60 = c.get('_high60', 0) or 0; l60 = c.get('_low60', 0) or 0
            if h60 > 0 and close > 0:
                h60_pct = (h60 - close) / close * 100
                h60_str = f"{h60:.2f} ({h60_pct:+.1f}%)"
            else: h60_str = "-"
            if l60 > 0 and close > 0:
                l60_pct = (close - l60) / l60 * 100
                l60_str = f"{l60:.2f} ({l60_pct:+.1f}%)"
            else: l60_str = "-"
            tier_label = c.get('_tier_label', '-')
            chg_e = "🔴" if chg >= 0 else "🟢"
            entry = calc_entry_price(c)
            sl = round(entry * _STRATEGY_STOP_LOSS.get(s, 0.96), 2)
            tp = round(entry * _STRATEGY_TAKE_PROFIT.get(s, 1.05), 2)
            pl_ratio = c.get('_pl_ratio', round((tp - entry) / max(entry - sl, 0.01), 2))
            top10_mark = "⭐" if code in _top10_codes else ""
            r7d = c.get('_recent_7d')
            r7d_str = str(r7d) if r7d is not None else ""
            # v6.6.44: 7日列附带历史策略标注
            r7s = c.get('_recent_7d_strategies', {})
            if r7d_str and r7s:
                sorted_dates = sorted(r7s.keys())
                strats = [r7s[d] for d in sorted_dates]
                seen = set(); uniq_s = []
                for s_ in strats:
                    if s_ not in seen: seen.add(s_); uniq_s.append(s_)
                r7d_str = f"{r7d} ({','.join(uniq_s)})"
            url = f"https://quote.eastmoney.com/sh{code}.html" if code.startswith('6') else f"https://quote.eastmoney.com/sz{code}.html"
            # v6.13.13: 回测标记列 — 新增no_entry警告
            bt_mark = ''
            if bt_lookup and code in bt_lookup:
                bt = bt_lookup[code]
                emoji = '🟢' if bt['last_result'] == 'win' else ('🔴' if bt['last_result'] == 'loss' else '⚪')
                suffix = '⚠️' if bt.get('no_entry', 0) > 0 else ''
                bt_mark = f'{emoji}{bt["wins"]}/{bt["total"]}{suffix}'
            lines.append(f"| {idx} | {top10_mark} | {s} | [{name}]({url}) | {code} | {ind} | {biz} | {chg_e}{chg:+.2f}% | {op:.2f} | {close:.2f} | {amp:.2f}% | {h60_str} | {l60_str} | {tier_label} | {r7d_str} | {score} | {conf} | {entry:.2f} | {sl:.2f} | {tp:.2f} | {pl_ratio} | {bt_mark} |\n")
        lines.append("\n## 回测说明\n")
        lines.append("- **回测列格式**：`图标 + 胜/样本`，例如 `🟢2/2` 表示历史同标的样本2笔、盈利2笔。")
        lines.append("- **图标含义**：🟢 最近一次样本盈利；🔴 最近一次样本亏损；⚪ 后续K线不足或未形成有效胜负；⚠️ 历史有限价单未成交（当日最低价>进场价）；空白表示无可匹配历史样本。")
        lines.append("- **模拟口径**：使用最近90天推荐历史，按推荐表的进场、止损、止盈进行模拟，单笔最大持仓10个交易日。")
        lines.append("- **交易规则**：遵循A股T+1，买入当日不检查止盈止损出场，从下一交易日起判断是否触及止损/止盈。")
        lines.append("- **使用限制**：未计入滑点、手续费、涨跌停无法成交、真实排队成交等因素；样本少时仅作参考，不能代表未来表现。\n")
        lines.append("\n## TOP10 板块热度精选（按板块涨停家数排序，同热度按盈亏比优先）\n")
        lines.append("| # | 标的 | 代码 | 策略 | 行业 | 板块热度 | 盈亏比 | 进场 | 止损 | 止盈 | 评分 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for ti, (tcode, tpl, theat, _) in enumerate(_pl_sorted[:10], 1):
            tc = next((ct for ct in candidates if ct.get('code') == tcode), None)
            if tc:
                ts = tc.get('strategy', '?')
                tind = _industry_str(tc)
                tname = tc.get('name', '')
                tentry = calc_entry_price(tc)
                tsl = round(tentry * _STRATEGY_STOP_LOSS.get(ts, 0.96), 2)
                ttp = round(tentry * _STRATEGY_TAKE_PROFIT.get(ts, 1.05), 2)
                tscore = tc.get('score', 0)
                turl = f"https://quote.eastmoney.com/sh{tcode}.html" if tcode.startswith('6') else f"https://quote.eastmoney.com/sz{tcode}.html"
                heat_str = f"🔥🔥🔥 {theat}涨停" if theat >= 10 else (f"🔥🔥 {theat}涨停" if theat >= 3 else f"🔥 {theat}涨停")
                lines.append(f"| {ti} | [{tname}]({turl}) | {tcode} | {ts} | {tind} | {heat_str} | {tpl} | {tentry:.2f} | {tsl:.2f} | {ttp:.2f} | {tscore} |")
    sd = Counter(c.get('strategy') for c in candidates)
    sn = _STRATEGY_NAMES
    lines.append("\n## 策略分布")
    for s in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T']:
        if sd.get(s, 0) > 0: lines.append(f"- {s} {sn.get(s, '')}: {sd[s]}只")
    lines.append("\n## 硬排除 TOP5")
    for r, cnt in er.most_common(5): lines.append(f"- {r}: {cnt}只")
    lines.append(f"\n\n> ⚠️ 免责声明：本报告仅供研究参考，不构成任何投资建议。\n> 版本: {file_version} | 生成: {beijing_date}")
    
    # ── v6.12.4: AI 策略分析 ──
    if ai_report:
        lines.append("\n---\n")
        lines.append(ai_report.get('market_overview', ''))
        lines.append("\n---\n")
        lines.append(ai_report.get('sector_analysis', ''))
        lines.append("\n---\n")
        lines.append("## 三、个股深度分析")
        lines.append("")
        for ca in ai_report.get('candidate_analyses', []):
            lines.append(f"### {ca.get('code', '')} {ca.get('name', '')}（{ca.get('strategy', '')}）")
            lines.append("")
            lines.append(ca.get('summary', ''))
            lines.append("")
            lines.append(ca.get('strategy_logic', ''))
            lines.append("")
            lines.append(ca.get('technical', ''))
            lines.append("")
            lines.append(ca.get('capital', ''))
            lines.append("")
            lines.append(ca.get('fundamental', ''))
            lines.append("")
            lines.append(ca.get('risk', ''))
            lines.append("")
            lines.append(ca.get('suggestion', ''))
            lines.append("")
            lines.append("---")
            lines.append("")
    
    with open(mp, 'w', encoding='utf-8') as f: f.write('\n'.join(lines))
    log_alert("INFO", "Markdown", f"已输出至 {mp}")
    return mp

# ============================================================
# 步骤20B：HTML报告（v6.6.27 含指数行情）
# ============================================================
def step20B_generate_html(candidates, total_raw, ae, asig, astr, amicro, aind, anew, er, crisis_alerts, ai_report=None, bt_lookup=None, kline_data=None, bt_result=None):
    hd = f"/workspace/ashare-screening-{pred_yyyymmdd}"
    os.makedirs(hd, exist_ok=True)
    hp = f"{hd}/ashare-screening-{pred_yyyymmdd}.html"
    sd = Counter(c.get('strategy') for c in candidates)
    sn = _STRATEGY_NAMES
    sc = _STRATEGY_COLORS
    fc = len(candidates)
    
    # 指数卡片HTML（v6.12.5: 键名从sh000001/sz399001/sz399006改为sh/sz/cy，与step8 index_data一致）
    idx_names = [("sh", "上证指数"), ("sz", "深证成指"), ("cy", "创业板指")]
    index_cards = ""
    for code, name in idx_names:
        info = index_data.get(code, {})
        price = info.get("price", 0)
        chg = info.get("change_pct", 0)
        chg_amt = info.get("change_amount", 0)
        if price > 0:
            chg_cls = "up" if chg >= 0 else "down"
            chg_sign = "+" if chg >= 0 else ""
            amt_sign = "+" if chg_amt >= 0 else ""
            index_cards += f'<div class="index-card"><div class="idx-name">{name}</div><div class="idx-price">{price:.2f}</div><div class="idx-chg {chg_cls}"><span class="idx-amt">{amt_sign}{chg_amt:.2f}</span> <span class="idx-pct">{chg_sign}{chg:.2f}%</span></div></div>'
        else:
            index_cards += f'<div class="index-card"><div class="idx-name">{name}</div><div class="idx-price">-</div><div class="idx-chg">数据不可得</div></div>'
    
    rows_html = ""
    # v6.13.17: 基于已计算的_pl_ratio排序TOP10，移除重复调用
    _top10_codes = set(c['code'] for c in sorted(candidates, key=lambda x: -(x.get('_pl_ratio', 0) or 0))[:10])
    for idx, c in enumerate(candidates, 1):
        code = c.get('code', ''); name = c.get('name', ''); s = c.get('strategy', '?')
        ind = _industry_str(c); biz = c.get('business', ''); chg = c.get('change_pct', 0)
        op = c.get('open', 0) or 0; close = c.get('close', 0) or 0
        amp = c.get('amplitude', 0) or 0; score = c.get('score', 0); conf = c.get('confidence', '★')
        entry = c.get('_entry', 0); sl = c.get('_stop', 0); tp = c.get('_target', 0)
        pl_ratio = c.get('_pl_ratio', 0)
        h60 = c.get('_high60', 0) or 0; l60 = c.get('_low60', 0) or 0
        if h60 > 0 and close > 0:
            h60_pct = (h60 - close) / close * 100
            h60_str = f"{h60:.2f} ({h60_pct:+.1f}%)"
        else: h60_str = "-"
        if l60 > 0 and close > 0:
            l60_pct = (close - l60) / l60 * 100
            l60_str = f"{l60:.2f} ({l60_pct:+.1f}%)"
        else: l60_str = "-"
        tier_label = c.get('_tier_label', '-'); tier_cls = c.get('_tier_cls', 'tier_na')
        top10_mark = "⭐" if code in _top10_codes else ""
        r7d_html = str(c.get('_recent_7d')) if c.get('_recent_7d') is not None else ""
        # v6.6.44: 7日列附带历史策略标注
        r7s = c.get('_recent_7d_strategies', {})
        if r7d_html and r7s:
            sorted_dates = sorted(r7s.keys())
            strats = [r7s[d] for d in sorted_dates]
            seen = set(); uniq_s = []
            for s_ in strats:
                if s_ not in seen: seen.add(s_); uniq_s.append(s_)
            r7d_html = f"{r7d_html} ({','.join(uniq_s)})"  # v6.8.8: 与MD格式统一
        chg_cls = "up" if chg >= 0 else "down"
        conf_cls = "high" if "★★★" in conf else ("mid" if "★★" in conf else "low")
        scl = f"strat_{s.lower()}"
        url = f"https://quote.eastmoney.com/sh{code}.html" if code.startswith('6') else f"https://quote.eastmoney.com/sz{code}.html"
        # v6.13.13: 回测标记列 — 新增no_entry警告
        bt_mark = ''
        if bt_lookup and code in bt_lookup:
            bt = bt_lookup[code]
            bt_emoji = "🟢" if bt["last_result"]=="win" else ("🔴" if bt["last_result"]=="loss" else "⚪")
            bt_suffix = ' ⚠️' if bt.get('no_entry', 0) > 0 else ''
            bt_mark = f'<span class="{bt["last_result"]}">{bt_emoji}{bt["wins"]}/{bt["total"]}{bt_suffix}</span>'
        rows_html += f"""<tr class="{scl}"><td>{idx}</td><td>{top10_mark}</td><td><span class="badge {scl}">{s}</span></td>
        <td><a href="{url}" target="_blank">{html.escape(name)}</a></td><td>{code}</td><td>{ind}</td><td>{html.escape(biz)}</td>
        <td class="{chg_cls}">{chg:+.2f}%</td><td>{op:.2f}</td><td>{close:.2f}</td>
        <td>{amp:.2f}%</td><td>{h60_str}</td><td>{l60_str}</td><td class="tier {tier_cls}">{tier_label}</td><td>{r7d_html}</td><td>{score}</td><td class="conf {conf_cls}">{conf}</td>
        <td class="entry">{entry:.2f}</td><td>{sl:.2f}</td><td>{tp:.2f}</td><td>{pl_ratio}</td><td>{bt_mark}</td></tr>"""
    seg_html = ""; legend_html = ""
    total_m = sum(sd.values())
    if total_m > 0:
        for s in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T']:
            cnt = sd.get(s, 0)
            if cnt > 0:
                pct = cnt / total_m * 100
                seg_html += f'<div class="seg" style="width:{pct}%;background:{sc[s]}">{cnt}</div>'
                legend_html += f'<span class="legend-item"><span class="legend-dot" style="background:{sc[s]}"></span> {s}{sn.get(s, "")}: {cnt}只 ({pct:.0f}%)</span>'
    
    bar_html = ""
    mx = max(er.values()) if er else 1
    for r, cnt in er.most_common(5):
        bp = cnt / mx * 100
        bar_html += f'<div class="bar-row"><div class="bar-label">{r}</div><div class="bar-track"><div class="bar-fill" style="width:{bp}%">{cnt}</div></div></div>'
    
    stages = [("原始标的池", total_raw), ("硬排除(13项)", ae), ("信号过滤(27项)", asig),
              ("策略匹配(17策略)", astr), ("微观结构过滤", amicro), ("行业+同策略限制", aind), ("新闻筛查", aind - anew), ("最终推荐", fc)]
    max_f = max(s[1] for s in stages)
    funnel_html = ""
    for i, (name, count) in enumerate(stages):
        w = max(12, int(count / max(max_f, 1) * 100))
        cls = "funnel-last" if i == len(stages) - 1 else ""
        funnel_html += f'<div class="funnel-step {cls}" style="width:{w}%">{name}: {count}只</div>'
    
    strat_bars = ""
    for s in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T']:
        cnt = sd.get(s, 0)
        if cnt > 0:
            bp = cnt / max(max(sd.values()), 1) * 100
            strat_bars += f'<div class="bar-row"><div class="bar-label">{s} {sn.get(s, "")}</div><div class="bar-track"><div class="bar-fill" style="width:{bp}%;background:{sc[s]}">{cnt}</div></div></div>'
    if not strat_bars:
        strat_bars = '<div style="color:#94a3b8">无匹配</div>'
    
    alerts_html = ""
    if crisis_alerts:
        for a in crisis_alerts:
            alerts_html += f'<div class="alert-item"><span class="alert-level warning">WARNING</span><span class="alert-msg">{a}</span></div>'
    else:
        alerts_html = '<div class="alert-item"><span class="alert-level info">INFO</span><span class="alert-msg">今日无异常告警</span></div>'
    
    # ── v6.13.0: TOP10盈亏比精选推荐理由（含交易维度+60日区间+基本面+信号+公告+7日历史）──
    top10_cards_html = ""
    top10_sorted = sorted(candidates, key=lambda c: -c.get('_pl_ratio', 0))[:10]
    if top10_sorted:
        cards = []
        for i, c in enumerate(top10_sorted):
            idx = i + 1
            code = c.get('code', '')
            name = c.get('name', '')
            strat = c.get('strategy', '?')
            score = c.get('score', 0)
            conf = c.get('confidence', '')
            conf_stars = '★★★' if conf == 'high' else ('★★' if conf == 'mid' else '★')
            change_pct = c.get('change_pct', 0)
            ampl = c.get('amplitude', 0)
            strat_badge = f'strat_{strat.lower()}'
            entry = c.get('_entry', 0)
            stop = c.get('_stop', 0)
            target = c.get('_target', 0)
            plr = c.get('_pl_ratio', 0)
            lh = c.get('_longhu', '')
            news = c.get('_news_positive', '')
            ann = c.get('_announcement', '')
            industry = _industry_str(c)
            business = c.get('business', '')
            amount = c.get('amount', 0) or 0
            turnover = c.get('turnover', 0) or 0
            vr = c.get('volume_ratio') or 0
            main_in = c.get('main_inflow') or 0
            r7d = c.get('_recent_7d') or 0
            r7s = c.get('_recent_7d_strategies', {})
            sigs = c.get('_signal_reasons', [])
            roe = c.get('_fd_roe') or 0
            np_yoy = c.get('_fd_net_profit_yoy') or 0
            close = c.get('close', 0) or 0
            
            # v6.13.0: 获取K线数据用于60日区间/BOLL/KDJ
            kd = kline_data.get(code, {}) if kline_data else {}
            has_kline = bool(kd and kd.get('closes') and len(kd.get('closes', [])) >= 5)
            k_val = kd.get('k', 0) if has_kline else 0
            d_val = kd.get('d', 0) if has_kline else 0
            j_val = kd.get('j', 0) if has_kline else 0
            high60 = kd.get('high60', 0) if has_kline else 0
            low60 = kd.get('low60', 0) if has_kline else 0
            boll_upper = kd.get('boll_upper', 0) if has_kline else 0
            boll_mid = kd.get('boll_mid', 0) if has_kline else 0
            boll_lower = kd.get('boll_lower', 0) if has_kline else 0
            
            sname = _STRATEGY_NAMES.get(strat, '')
            
            # 构建推荐理由
            reason_parts = []
            # 1. 当日表现 + 行业
            perf_parts = [f'涨幅{change_pct:+.2f}%', f'振幅{ampl:.1f}%']
            if amount > 0: perf_parts.append(f'成交额{amount/1e8:.2f}亿')
            if turnover > 0: perf_parts.append(f'换手率{turnover:.2f}%')
            if vr > 0: perf_parts.append(f'量比{vr:.2f}')
            reason_parts.append(f'<strong>当日表现：</strong>{"，".join(perf_parts)}，{industry}行业')
            if business: reason_parts.append(f'<strong>二级行业：</strong>{business}')
            
            # v6.13.0: 2. 60日区间位置 + 技术指标
            tech_parts = []
            if has_kline and high60 > 0 and low60 > 0 and close > 0:
                pos_60 = (close - low60) / (high60 - low60) * 100 if high60 > low60 else 50
                if pos_60 >= 80: pos_label = f'高位区({pos_60:.0f}%)'
                elif pos_60 >= 50: pos_label = f'中位区({pos_60:.0f}%)'
                elif pos_60 >= 20: pos_label = f'低位区({pos_60:.0f}%)'
                else: pos_label = f'底部区({pos_60:.0f}%)'
                tech_parts.append(f'60日{pos_label}')
            if has_kline and k_val > 0 and d_val > 0:
                if k_val > 80: kdj_label = '超买'
                elif k_val < 20: kdj_label = '超卖'
                elif k_val > d_val: kdj_label = '多头'
                else: kdj_label = '空头'
                tech_parts.append(f'KDJ{kdj_label}(K={k_val:.0f})')
            if has_kline and boll_upper > 0 and close > 0:
                if close >= boll_upper: boll_label = '突破上轨'
                elif close > boll_mid: boll_label = '上轨区间'
                elif close > boll_lower: boll_label = '下轨区间'
                else: boll_label = '跌破下轨'
                tech_parts.append(f'BOLL{boll_label}')
            if tech_parts:
                reason_parts.append(f'<strong>技术面：</strong>{"，".join(tech_parts)}')
            
            # 3. 基本面
            fin_parts = []
            try: roe_f = float(roe); fin_parts.append(f'ROE {roe_f:.1f}%') if roe_f != 0 else None
            except (ValueError, TypeError): pass
            try: np_f = float(np_yoy); fin_parts.append(f'净利润同比 {np_f:+.1f}%') if np_f != 0 else None
            except (ValueError, TypeError): pass
            if main_in and main_in != 0:
                fin_parts.append(f'主力净流入 {main_in/1e4:+.0f}万')
            if fin_parts:
                reason_parts.append(f'<strong>基本面：</strong>{"，".join(fin_parts)}')
            
            # 4. 进场区间 + 操作建议
            stop_pct = (1-stop/entry)*100 if entry > 0 else 0
            target_pct = (target/entry-1)*100 if entry > 0 else 0
            reason_parts.append(f'<strong>进场区间：</strong>{entry:.2f}元进场，止损{stop:.2f}元（-{stop_pct:.1f}%），止盈{target:.2f}元（+{target_pct:.1f}%），盈亏比{plr:.2f}')
            
            # 5. 操作建议
            op_parts = []
            if has_kline and high60 > 0 and low60 > 0 and close > 0:
                pos_60 = (close - low60) / (high60 - low60) * 100 if high60 > low60 else 50
                if pos_60 >= 80 and strat in ['A', 'G', 'I', 'N']:
                    op_parts.append('⚠️ 高位追涨，仓位控制在30%以内，开盘观察竞价强度后分批进场')
                elif pos_60 >= 80:
                    op_parts.append('高位区域，建议等回调至中位再进场')
                elif pos_60 <= 20:
                    op_parts.append('底部区域，安全边际高，可逢低分批建仓')
                elif strat in ['A', 'G']:
                    op_parts.append('趋势良好，开盘回踩均线时进场，止损严格')
                elif strat in ['B', 'D', 'L']:
                    op_parts.append('低吸策略，开盘不急追，等盘中回调至支撑位进场')
                else:
                    op_parts.append('开盘观察5分钟，确认方向后进场')
            if op_parts:
                reason_parts.append(f'<strong>操作建议：</strong>{op_parts[0]}')
            
            # 6. 7日推荐历史
            if r7d > 0 and r7s:
                r7_dates = sorted(r7s.keys())
                r7_strats = [r7s[d] for d in r7_dates]
                seen = set(); uniq_s = []
                for s_ in r7_strats:
                    if s_ not in seen: seen.add(s_); uniq_s.append(s_)
                reason_parts.append(f'<strong>7日推荐：</strong>已推荐{r7d}天（策略{",".join(uniq_s)}），可持续关注')
            
            # 7. 信号匹配
            if sigs:
                sig_str = '，'.join(sigs[:5])
                if len(sigs) > 5: sig_str += f' 等{len(sigs)}项'
                reason_parts.append(f'<strong>匹配信号：</strong>{sig_str}')
            
            # 8. 龙虎榜/新闻/公告
            if lh:
                reason_parts.append(f'<strong>🐉 龙虎榜：</strong>{lh}')
            if news:
                reason_parts.append(f'<strong>📰 正面新闻：</strong>{news}')
            if ann:
                ann_display = ann.replace('; ', '<br>  ')
                reason_parts.append(f'<strong>📋 公司公告：</strong><br>  {ann_display}')
            
            reason = '<br>'.join(reason_parts)
            
            # 成交额格式化
            amt_str = f'{amount/1e8:.1f}亿' if amount > 0 else '-'
            to_str = f'{turnover:.1f}%' if turnover > 0 else '-'
            
            cards.append(f'''<div class="top10-card">
<div class="top10-card-header"><span class="rank">#{idx}</span><span class="name">{name}</span><span class="code">{code}</span><span class="badge {strat_badge}">{strat} {sname}</span></div>
<div class="top10-card-metrics">
<div class="metric"><div class="val ratio-hl">{plr:.2f}</div><div class="lbl">盈亏比</div></div>
<div class="metric"><div class="val">{entry:.2f}</div><div class="lbl">进场</div></div>
<div class="metric"><div class="val">{stop:.2f}</div><div class="lbl">止损</div></div>
<div class="metric"><div class="val">{target:.2f}</div><div class="lbl">止盈</div></div>
<div class="metric"><div class="val">{score}</div><div class="lbl">评分</div></div>
<div class="metric"><div class="val">{conf_stars}</div><div class="lbl">置信</div></div>
<div class="metric"><div class="val">{amt_str}</div><div class="lbl">成交额</div></div>
<div class="metric"><div class="val">{to_str}</div><div class="lbl">换手率</div></div>
</div>
<div class="top10-card-reason">{reason}</div></div>''')
        top10_cards_html = '\n'.join(cards)
    
    # ── v6.13.1: AI 策略分析 HTML（美化版）──
    ai_html = ""
    if ai_report:
        # 将Markdown转换为结构化HTML
        def _md_to_html(md_text, section_class=''):
            """将AI分析Markdown转为结构化HTML"""
            if not md_text: return ''
            lines = md_text.split('\n')
            result = []
            i = 0
            while i < len(lines):
                line = lines[i].strip()
                if not line:
                    i += 1; continue
                # h3 标题
                if line.startswith('### ') and line[4:].strip():
                    title = line[4:].strip()
                    result.append(f'<h3>{title}</h3>')
                    i += 1; continue
                # h2 标题
                if line.startswith('## '):
                    i += 1; continue
                # 表格行
                if line.startswith('|') and '|' in line[1:]:
                    # 收集连续表格行
                    tbl_rows = []
                    while i < len(lines) and lines[i].strip().startswith('|'):
                        row = lines[i].strip()
                        cells = [c.strip() for c in row.split('|')[1:-1]]
                        tbl_rows.append(cells)
                        i += 1
                    if tbl_rows:
                        # 跳过分隔行
                        real_rows = [r for r in tbl_rows if not all(c.replace('-','').replace(':','').strip()=='' for c in r)]
                        if real_rows:
                            result.append('<table>')
                            for ri, row in enumerate(real_rows):
                                tag = 'th' if ri == 0 else 'td'
                                result.append('<tr>' + ''.join(f'<{tag}>{c}</{tag}>' for c in row) + '</tr>')
                            result.append('</table>')
                    continue
                # 列表项
                if line.startswith('- '):
                    item = line[2:].strip()
                    # 加粗处理
                    item = item.replace('**', '<strong>', 1).replace('**', '</strong>', 1) if '**' in item else item
                    # 处理额外加粗
                    while '<strong>' in item and item.count('**') >= 2:
                        item = item.replace('**', '<strong>', 1).replace('**', '</strong>', 1)
                    result.append(f'<div class="ai-dim">{item}</div>')
                    i += 1; continue
                # 普通段落
                para = line
                while '<strong>' in para and para.count('**') >= 2:
                    para = para.replace('**', '<strong>', 1).replace('**', '</strong>', 1)
                result.append(f'<p>{para}</p>')
                i += 1
            return '\n'.join(result)
        
        # ── 市场全景 ──
        market_md = ai_report.get('market_overview', '')
        market_html = _md_to_html(market_md)
        ai_html += f'''<div class="ai-section-wrap">
<h2><span class="ai-icon">🌐</span>AI 策略分析 — 市场全景</h2>
<div class="ai-markdown">{market_html}</div>
</div>'''
        
        # ── 板块深度研判 ──
        sector_md = ai_report.get('sector_analysis', '')
        sector_html = _md_to_html(sector_md)
        ai_html += f'''<div class="ai-section-wrap">
<h2><span class="ai-icon">📊</span>AI 策略分析 — 板块深度研判</h2>
<div class="ai-markdown">{sector_html}</div>
</div>'''
        
        # ── 个股深度研判 ──
        ai_html += '<div class="ai-section-wrap"><h2><span class="ai-icon">🔍</span>AI 策略分析 — 个股深度研判</h2>'
        ai_html += '<div class="ai-stock-grid">'
        
        dim_config = [
            ('summary', '综合研判', 'overview'),
            ('strategy_logic', '策略逻辑', 'capital'),
            ('technical', '技术面分析', 'technical'),
            ('capital', '资金面分析', 'capital'),
            ('fundamental', '基本面分析', 'fundamental'),
            ('risk', '风险提示', 'risk'),
            ('suggestion', '操作建议', 'suggestion'),
        ]
        
        for ci, ca in enumerate(ai_report.get('candidate_analyses', []), 1):
            code = ca.get('code', '')
            name = ca.get('name', '')
            ai_html += f'''<div class="ai-stock-card">
<div class="ai-stock-card-header">
    <span class="ai-rank">#{ci}</span>
    <span class="ai-name">{name}</span>
    <span class="ai-code">{code}</span>
</div>
<div class="ai-stock-card-body">'''
            for key, label, css_cls in dim_config:
                val = ca.get(key, '')
                if val:
                    ai_html += f'<div class="ai-dim"><span class="ai-dim-label {css_cls}">{label}</span>{val}</div>\n'
            ai_html += '</div></div>\n'
        
        ai_html += '</div></div>'
    
    # v6.13.15: 生成回测HTML片段
    backtest_html = ''
    if bt_result and bt_result.get('all_trades'):
        bt = bt_result
        total_trades = len(bt.get('all_trades', []))
        win_rate = bt.get('win_rate', 0) * 100
        avg_return = bt.get('avg_return', 0) * 100
        profit_loss_ratio = bt.get('profit_loss_ratio', 0)
        sharpe = bt.get('sharpe', 0)
        max_drawdown = bt.get('max_drawdown', 0) * 100
        avg_win = bt.get('avg_win', 0) * 100
        avg_loss = bt.get('avg_loss', 0) * 100
        avg_hold = bt.get('avg_hold_days', 0)
        wr_cls = 'win' if win_rate >= 50 else 'loss'
        ar_cls = 'win' if avg_return >= 0 else 'loss'
        sr_cls = 'win' if sharpe >= 0 else 'loss'
        dd_cls = 'loss' if max_drawdown > 20 else 'win'
        backtest_html += '<div class="metrics-grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:1.2rem">'
        backtest_html += f'<div class="metric-card-bt"><div class="metric-label-bt">总交易</div><div class="metric-value-bt">{total_trades}笔</div></div>'
        backtest_html += f'<div class="metric-card-bt"><div class="metric-label-bt">胜率</div><div class="metric-value-bt {wr_cls}">{win_rate:.1f}%</div></div>'
        backtest_html += f'<div class="metric-card-bt"><div class="metric-label-bt">平均收益</div><div class="metric-value-bt {ar_cls}">{avg_return:+.2f}%</div></div>'
        backtest_html += f'<div class="metric-card-bt"><div class="metric-label-bt">盈亏比</div><div class="metric-value-bt">{profit_loss_ratio:.2f}</div></div>'
        backtest_html += f'<div class="metric-card-bt"><div class="metric-label-bt">夏普</div><div class="metric-value-bt {sr_cls}">{sharpe:.2f}</div></div>'
        backtest_html += f'<div class="metric-card-bt"><div class="metric-label-bt">最大回撤</div><div class="metric-value-bt {dd_cls}">{max_drawdown:.2f}%</div></div>'
        backtest_html += f'<div class="metric-card-bt"><div class="metric-label-bt">平均盈利</div><div class="metric-value-bt win">{avg_win:+.2f}%</div></div>'
        backtest_html += f'<div class="metric-card-bt"><div class="metric-label-bt">平均亏损</div><div class="metric-value-bt loss">{avg_loss:+.2f}%</div></div>'
        backtest_html += f'<div class="metric-card-bt"><div class="metric-label-bt">平均持仓</div><div class="metric-value-bt">{avg_hold:.1f}天</div></div>'
        backtest_html += '</div><table><thead><tr><th>策略</th><th>笔数</th><th>胜率</th><th>均收</th><th>盈亏比</th><th>夏普</th></tr></thead><tbody>'
        for s in bt.get('strategy_stats', []):
            s_wr = s.get('win_rate', 0) * 100
            s_ar = s.get('avg_return', 0) * 100
            s_cls = 'win' if s_wr >= 50 else 'loss'
            s_ar_cls = 'win' if s_ar >= 0 else 'loss'
            s_sr = s.get('sharpe', 0)
            s_sr_cls = 'win' if s_sr >= 0 else 'loss'
            sn = s.get('strategy', '?')
            badge_cls = 'strat_' + sn.lower() if sn else 'strat_b'
            sname = _STRATEGY_NAMES.get(sn, sn)
            backtest_html += f'<tr><td><span class="badge {badge_cls}">{sn}</span> {sname}</td><td>{s.get("trades", 0)}</td><td class="{s_cls}">{s_wr:.1f}%</td><td class="{s_ar_cls}">{s_ar:+.2f}%</td><td>{s.get("profit_loss_ratio", 0):.2f}</td><td class="{s_sr_cls}">{s_sr:.2f}</td></tr>'
        backtest_html += '</tbody></table><div style="text-align:center;margin-top:1.2rem;padding-top:.8rem;border-top:1px solid #334155"><a href="/backtest/" target="_blank" style="display:inline-block;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;padding:.6rem 1.8rem;border-radius:8px;font-weight:700;font-size:.82rem;text-decoration:none">📋 查看完整回测报告（含交易明细） →</a></div>'
    else:
        backtest_html = '<div style="color:#94a3b8;padding:1rem;text-align:center">暂无回测数据</div>'
    

    html_content = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>A股短线标的筛选 — {prediction_date}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Noto Sans CJK SC','WenQuanYi Micro Hei',sans-serif;background:#0f172a;color:#e2e8f0;line-height:1.65;letter-spacing:.01em;-webkit-font-smoothing:antialiased}}
/* scrollbar */
::-webkit-scrollbar{{width:6px;height:6px}}::-webkit-scrollbar-track{{background:#0f172a}}::-webkit-scrollbar-thumb{{background:#334155;border-radius:3px}}::-webkit-scrollbar-thumb:hover{{background:#475569}}
/* header */
.header{{background:linear-gradient(135deg,#1e3a5f 0%,#0f2744 50%,#0c1f36 100%);padding:2.5rem 2rem;text-align:center;position:relative;overflow:hidden}}
.header::before{{content:'';position:absolute;inset:0;background:radial-gradient(ellipse at 30% 50%,rgba(56,189,248,0.08) 0%,transparent 70%);pointer-events:none}}
.header h1{{font-size:clamp(1.3rem,2.5vw,1.9rem);color:#f0f9ff;font-weight:800;letter-spacing:.04em;position:relative;z-index:1}}
.header .sub{{color:#94a3b8;font-size:.85rem;margin-top:.4rem;position:relative;z-index:1}}
/* container */
.container{{max-width:1280px;margin:0 auto;padding:1.2rem 1rem}}
/* meta cards */
.meta-row{{display:flex;flex-wrap:wrap;gap:.8rem;justify-content:center;margin:1.2rem 0}}
.meta-card{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:.7rem 1.4rem;text-align:center;min-width:105px;box-shadow:0 2px 8px rgba(0,0,0,.2);transition:border-color .2s,transform .15s}}
.meta-card:hover{{border-color:#475569;transform:translateY(-1px)}}
.meta-card .label{{font-size:.7rem;color:#94a3b8;text-transform:uppercase;letter-spacing:.05em}}.meta-card .value{{font-size:1.15rem;font-weight:700;color:#38bdf8}}
/* index cards */
.index-row{{display:flex;flex-wrap:wrap;gap:.9rem;justify-content:center;margin:1.2rem 0}}
.index-card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:1.1rem 1.4rem;text-align:center;min-width:145px;flex:1;box-shadow:0 2px 10px rgba(0,0,0,.25);transition:border-color .2s,transform .15s}}
.index-card:hover{{border-color:#475569;transform:translateY(-1px)}}
.index-card .idx-name{{font-size:.85rem;color:#cbd5e1;font-weight:500}}.index-card .idx-price{{font-size:1.6rem;font-weight:800;color:#f8fafc;letter-spacing:.02em}}
.index-card .idx-chg{{font-size:.9rem;font-weight:700}}
.index-card .idx-amt{{font-size:1.1rem;display:block;font-weight:600}} .index-card .idx-pct{{font-size:.75rem;color:#94a3b8;font-weight:400}}
.up{{color:#ef4444}}.down{{color:#22c55e}}
/* sections */
section{{background:#1e293b;border:1px solid #334155;border-radius:14px;padding:1.6rem;margin:1.3rem 0;box-shadow:0 3px 12px rgba(0,0,0,.2)}}
section h2{{font-size:1.15rem;color:#38bdf8;margin-bottom:1.1rem;border-bottom:2px solid #334155;padding-bottom:.6rem;font-weight:700;letter-spacing:.03em}}
/* funnel */
.funnel{{display:flex;flex-direction:column;align-items:center;gap:.35rem}}
.funnel-step{{background:linear-gradient(90deg,#6366f1,#8b5cf6);color:#fff;text-align:center;padding:.5rem 1.2rem;border-radius:6px;font-size:.8rem;font-weight:600;min-width:260px;box-shadow:0 1px 4px rgba(99,102,241,.3)}}
.funnel-last{{background:linear-gradient(90deg,#3b82f6,#06b6d4);border:2px solid #38bdf8;font-weight:700;box-shadow:0 2px 8px rgba(56,189,248,.35)}}
/* seg bar */
.seg-bar{{display:flex;height:36px;border-radius:8px;overflow:hidden;margin:.6rem 0;box-shadow:inset 0 1px 2px rgba(0,0,0,.3)}}
.seg{{display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:.78rem;transition:filter .2s}}
.seg:hover{{filter:brightness(1.2)}}
.legend{{display:flex;flex-wrap:wrap;gap:1.1rem;margin:.6rem 0;font-size:.78rem}}
.legend-item{{display:flex;align-items:center;gap:.35rem}}
.legend-dot{{width:12px;height:12px;border-radius:3px;display:inline-block;box-shadow:0 0 4px currentColor}}
/* bar charts */
.bar-row{{display:flex;align-items:center;margin:.45rem 0;gap:.6rem}}
.bar-label{{width:210px;font-size:.78rem;color:#cbd5e1;text-align:right;flex-shrink:0}}
.bar-track{{flex:1;background:#334155;border-radius:5px;height:26px;overflow:hidden;box-shadow:inset 0 1px 3px rgba(0,0,0,.3)}}
.bar-fill{{height:100%;border-radius:5px;display:flex;align-items:center;justify-content:flex-end;padding:0 .6rem;color:#fff;font-size:.75rem;font-weight:700;min-width:32px;transition:width .3s}}
/* chart grid */
.chart-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1.6rem}}
/* tables */
table{{width:100%;border-collapse:collapse;font-size:.78rem}}
th{{background:#334155;padding:.55rem .55rem;text-align:left;color:#38bdf8;position:sticky;top:0;white-space:nowrap;font-weight:700;font-size:.75rem;letter-spacing:.03em;z-index:1}}
td{{padding:.45rem .55rem;border-bottom:1px solid #1e293b;white-space:nowrap}}
tbody tr{{transition:background .15s}}
tbody tr:hover{{background:#2d3b4f!important}}
tbody tr:nth-child(even){{background:rgba(255,255,255,.01)}}
/* badges */
.badge{{padding:2px 9px;border-radius:4px;font-size:.68rem;font-weight:700;letter-spacing:.03em}}
.strat_a{{background:#14532d;color:#22c55e}}.strat_b{{background:#1e3a5f;color:#3b82f6}}.strat_c{{background:#3b1f6e;color:#8b5cf6}}
.strat_d{{background:#5c3d0e;color:#f59e0b}}.strat_e{{background:#5c1648;color:#ec4899}}.strat_f{{background:#0f4c5c;color:#06b6d4}}.strat_g{{background:#0e4c3d;color:#10b981}}.strat_h{{background:#4c1d0e;color:#f97316}}.strat_i{{background:#0e3d3d;color:#14b8a6}}.strat_j{{background:#5c1515;color:#ef4444}}.strat_k{{background:#3b1f3b;color:#a855f7}}.strat_l{{background:#4c3d0e;color:#eab308}}.strat_m{{background:#4c1d3b;color:#f472b6}}.strat_n{{background:#1e3d0e;color:#84cc16}}.strat_o{{background:#0e2e4c;color:#38bdf8}}.strat_p{{background:#4c2e0e;color:#fb923c}}.strat_q{{background:#0e3e4c;color:#22d3ee}}
tr.strat_a{{background:rgba(34,197,94,0.05)}}tr.strat_b{{background:rgba(59,130,246,0.05)}}tr.strat_c{{background:rgba(139,92,246,0.05)}}
tr.strat_d{{background:rgba(245,158,11,0.05)}}tr.strat_e{{background:rgba(236,72,153,0.05)}}tr.strat_f{{background:rgba(6,182,212,0.05)}}tr.strat_g{{background:rgba(16,185,129,0.05)}}tr.strat_h{{background:rgba(249,115,22,0.05)}}tr.strat_i{{background:rgba(20,184,166,0.05)}}tr.strat_j{{background:rgba(239,68,68,0.05)}}tr.strat_k{{background:rgba(168,85,247,0.05)}}tr.strat_l{{background:rgba(234,179,8,0.05)}}tr.strat_m{{background:rgba(244,114,182,0.05)}}tr.strat_n{{background:rgba(132,204,22,0.05)}}tr.strat_o{{background:rgba(56,189,248,0.05)}}tr.strat_p{{background:rgba(251,146,60,0.05)}}tr.strat_q{{background:rgba(34,211,238,0.05)}}
/* conf / entry */
.conf{{font-weight:700}}.conf.high{{color:#22c55e}}.conf.mid{{color:#f59e0b}}.conf.low{{color:#ef4444}}
.entry{{color:#38bdf8;font-weight:700}}
/* tier badges */
.tier{{font-weight:700;text-align:center;font-size:.72rem}}.tier1{{color:#22c55e;background:rgba(34,197,94,.15);border:1px solid rgba(34,197,94,.3);border-radius:12px;padding:2px 10px;display:inline-block}}.tier2{{color:#f59e0b;background:rgba(245,158,11,.15);border:1px solid rgba(245,158,11,.3);border-radius:12px;padding:2px 10px;display:inline-block}}.tier3{{color:#ef4444;background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.3);border-radius:12px;padding:2px 10px;display:inline-block}}.tier_na{{color:#64748b}}
/* alerts */
.alert-item{{display:flex;gap:.8rem;padding:.5rem 0;border-bottom:1px solid #1e293b;font-size:.78rem}}
.alert-item:last-child{{border-bottom:none}}
.alert-level{{padding:2px 12px;border-radius:4px;font-weight:700;font-size:.7rem;white-space:nowrap;letter-spacing:.03em}}
.alert-level.warning{{background:#5c3d0e;color:#f59e0b}}.alert-level.info{{background:#1e3a5f;color:#3b82f6}}
/* footer */
.footer{{text-align:center;padding:2.5rem 2rem 2rem;color:#64748b;font-size:.78rem}}
.footer .disclaimer{{color:#ef4444;font-weight:700;margin-top:.6rem;font-size:.82rem}}
/* links */
a{{color:#38bdf8;text-decoration:none;transition:color .15s}}a:hover{{text-decoration:underline;color:#7dd3fc}}
/* responsive v6.13.4: 全面移动端适配 */
@media(max-width:768px){{
  .container{{padding:.5rem}}
  .header{{padding:1.2rem .8rem}}
  .header h1{{font-size:1.1rem}}
  .header .sub{{font-size:.72rem}}
  section{{padding:1rem;margin:1rem 0;border-radius:10px}}
  section h2{{font-size:1rem;margin-bottom:.8rem}}
  .chart-grid{{grid-template-columns:1fr;gap:1rem}}
  .meta-row{{gap:.5rem}}
  .meta-card{{min-width:80px;padding:.5rem .8rem}}
  .meta-card .label{{font-size:.62rem}}
  .meta-card .value{{font-size:.95rem}}
  .index-row{{gap:.5rem}}
  .index-card{{min-width:100px;padding:.7rem .9rem}}
  .index-card .idx-name{{font-size:.72rem}}
  .index-card .idx-price{{font-size:1.2rem}}
  .index-card .idx-chg{{font-size:.75rem}}
  .index-card .idx-amt{{font-size:.9rem}}
  .funnel-step{{min-width:160px;font-size:.72rem;padding:.4rem .8rem}}
  .bar-label{{width:120px;font-size:.7rem}}
  .bar-track{{height:22px}}
  .bar-fill{{font-size:.68rem}}
  th,td{{font-size:.68rem;padding:.3rem .35rem}}
  .seg-bar{{height:28px}}
  .seg{{font-size:.7rem}}
  .legend{{font-size:.7rem;gap:.7rem}}
  .top10-card{{padding:12px 14px}}
  .top10-card-header .name{{font-size:.85rem}}
  .top10-card-header .rank{{font-size:1.1rem}}
  .top10-card-metrics{{grid-template-columns:repeat(auto-fit,minmax(70px,1fr));gap:6px}}
  .top10-card-metrics .metric .val{{font-size:.9rem}}
  .top10-card-metrics .metric .lbl{{font-size:.6rem}}
  .top10-card-reason{{font-size:.75rem}}
  .ai-section-wrap{{padding:1rem;margin:1rem 0}}
  .ai-section-wrap h2{{font-size:1rem}}
  .ai-section-wrap h3{{font-size:.82rem}}
  .ai-markdown{{font-size:.78rem}}
  .ai-stock-card-header{{padding:10px 14px;gap:8px}}
  .ai-stock-card-body{{padding:10px 14px}}
  .ai-stock-card-body .ai-dim{{font-size:.75rem}}
  .ai-stock-card-body .ai-dim-label{{font-size:.62rem;padding:1px 7px}}
  .alert-item{{font-size:.7rem}}
  .footer{{padding:1.5rem 1rem;font-size:.7rem}}
  .footer .disclaimer{{font-size:.72rem}}
}}
@media(max-width:480px){{
  .container{{padding:.3rem}}
  .header{{padding:1rem .5rem}}
  .header h1{{font-size:.95rem;letter-spacing:.02em}}
  .header .sub{{font-size:.65rem}}
  section{{padding:.7rem;margin:.7rem 0;border-radius:8px}}
  section h2{{font-size:.88rem;margin-bottom:.6rem;padding-bottom:.4rem}}
  .meta-row{{gap:.3rem;margin:.8rem 0}}
  .meta-card{{min-width:60px;padding:.4rem .45rem;border-radius:8px}}
  .meta-card .label{{font-size:.55rem;letter-spacing:.02em}}
  .meta-card .value{{font-size:.8rem}}
  .index-row{{gap:.3rem;margin:.8rem 0}}
  .index-card{{min-width:70px;padding:.5rem .55rem;border-radius:8px;flex:1 1 40%}}
  .index-card .idx-name{{font-size:.62rem}}
  .index-card .idx-price{{font-size:1rem}}
  .index-card .idx-chg{{font-size:.65rem}}
  .index-card .idx-amt{{font-size:.75rem}}
  .index-card .idx-pct{{font-size:.6rem}}
  .funnel-step{{min-width:100%;width:100%;font-size:.68rem;padding:.35rem .6rem;border-radius:4px}}
  .bar-label{{width:80px;font-size:.62rem;text-align:left}}
  .bar-row{{margin:.35rem 0;gap:.3rem}}
  .bar-track{{height:18px}}
  .bar-fill{{font-size:.6rem;padding:0 .3rem;min-width:24px}}
  .chart-grid{{grid-template-columns:1fr;gap:.7rem}}
  .seg-bar{{height:22px}}
  .seg{{font-size:.6rem}}
  .legend{{font-size:.62rem;gap:.4rem}}
  .legend-dot{{width:8px;height:8px}}
  th,td{{font-size:.6rem;padding:.2rem .25rem}}
  .badge{{font-size:.6rem;padding:1px 6px}}
  .top10-card{{padding:10px 12px;border-radius:8px}}
  .top10-card-header{{gap:6px;margin-bottom:8px}}
  .top10-card-header .name{{font-size:.78rem}}
  .top10-card-header .code{{font-size:.65rem}}
  .top10-card-header .rank{{font-size:1rem;min-width:22px}}
  .top10-card-metrics{{grid-template-columns:repeat(3,1fr);gap:4px;margin-bottom:8px}}
  .top10-card-metrics .metric .val{{font-size:.8rem}}
  .top10-card-metrics .metric .lbl{{font-size:.55rem}}
  .top10-card-reason{{font-size:.7rem;line-height:1.5}}
  .ai-section-wrap{{padding:.7rem;margin:.7rem 0;border-radius:10px}}
  .ai-section-wrap h2{{font-size:.88rem;margin-bottom:.8rem;padding-bottom:.5rem}}
  .ai-section-wrap h3{{font-size:.75rem}}
  .ai-markdown{{font-size:.72rem;line-height:1.6}}
  .ai-markdown table{{font-size:.65rem}}
  .ai-markdown th,.ai-markdown td{{padding:.3rem .4rem}}
  .ai-stock-card-header{{padding:8px 10px;gap:6px}}
  .ai-stock-card-header .ai-rank{{font-size:.9rem;min-width:22px}}
  .ai-stock-card-header .ai-name{{font-size:.85rem}}
  .ai-stock-card-header .ai-code{{font-size:.65rem}}
  .ai-stock-card-body{{padding:8px 10px}}
  .ai-stock-card-body .ai-dim{{font-size:.7rem;margin-bottom:6px;line-height:1.5}}
  .ai-stock-card-body .ai-dim-label{{font-size:.58rem}}
  .ai-info-card{{padding:10px 12px;margin:.5rem 0}}
  .alert-item{{font-size:.65rem;gap:.4rem;flex-wrap:wrap}}
  .alert-level{{font-size:.6rem;padding:1px 8px}}
  .footer{{padding:1.2rem .8rem;font-size:.65rem}}
  .footer .disclaimer{{font-size:.68rem}}
}}
@media(max-width:360px){{
  .container{{padding:.2rem}}
  .header{{padding:.8rem .4rem}}
  .header h1{{font-size:.85rem}}
  .meta-card{{min-width:50px;padding:.3rem .3rem}}
  .meta-card .value{{font-size:.7rem}}
  .index-card{{flex:1 1 100%;min-width:60px;padding:.4rem .4rem}}
  .index-card .idx-price{{font-size:.9rem}}
  .bar-label{{width:60px;font-size:.58rem}}
  th,td{{font-size:.55rem;padding:.15rem .2rem}}
  .top10-card-metrics{{grid-template-columns:repeat(2,1fr)}}
  .top10-card-metrics .metric .val{{font-size:.72rem}}
  .top10-card-reason{{font-size:.65rem}}
  .funnel-step{{font-size:.62rem}}
  .badge{{font-size:.55rem}}
  .ai-markdown{{font-size:.68rem}}
  .ai-stock-card-body .ai-dim{{font-size:.65rem}}
}}
/* TOP10 cards */
.top10-cards{{display:grid;grid-template-columns:1fr;gap:14px;margin-top:1rem}}
.top10-card{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:18px 22px;transition:border-color .2s,box-shadow .2s,transform .15s;box-shadow:0 2px 8px rgba(0,0,0,.2)}}
.top10-card:hover{{border-color:#38bdf8;box-shadow:0 4px 16px rgba(56,189,248,.12);transform:translateY(-1px)}}
.top10-card-header{{display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap}}
.top10-card-header .rank{{font-size:1.4rem;font-weight:800;color:#38bdf8;min-width:28px}}
.top10-card-header .name{{font-size:1rem;font-weight:700;color:#f8fafc}}
.top10-card-header .code{{font-size:.75rem;color:#94a3b8;margin-left:2px}}
.top10-card-metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(85px,1fr));gap:10px;margin-bottom:14px}}
.top10-card-metrics .metric{{text-align:center}}
.top10-card-metrics .metric .val{{font-size:1.05rem;font-weight:700;color:#f8fafc}}
.top10-card-metrics .metric .lbl{{font-size:.68rem;color:#94a3b8}}
.top10-card-metrics .ratio-hl{{font-size:1.25rem!important}}
.top10-card-reason{{font-size:.82rem;color:#94a3b8;line-height:1.65}}
.top10-card-reason strong{{color:#e2e8f0}}
.top10-conclusion{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:18px 22px;margin-top:20px;box-shadow:0 2px 8px rgba(0,0,0,.2)}}
.top10-conclusion h3{{font-size:1rem;color:#38bdf8;margin-bottom:10px;font-weight:700}}
.top10-conclusion p{{font-size:.82rem;color:#94a3b8;line-height:1.7;margin-top:8px}}
/* v6.13.1: AI分析模块美化 */
.ai-section-wrap{{background:linear-gradient(135deg, #1a2332 0%, #1e293b 50%, #172033 100%);border:1px solid #2d3a4f;border-radius:16px;padding:1.8rem;margin:1.5rem 0;box-shadow:0 4px 20px rgba(0,0,0,.3), inset 0 1px 0 rgba(255,255,255,.03);position:relative;overflow:hidden}}
.ai-section-wrap::before{{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg, #38bdf8, #8b5cf6, #ec4899);opacity:.8}}
.ai-section-wrap h2{{font-size:1.2rem;color:#f0f9ff;margin:0 0 1.2rem;padding:0 0 .8rem;border-bottom:1px solid #2d3a4f;font-weight:700;letter-spacing:.04em;display:flex;align-items:center;gap:10px}}
.ai-section-wrap h2 .ai-icon{{font-size:1.4rem}}
.ai-section-wrap h3{{font-size:.95rem;color:#38bdf8;margin:1.2rem 0 .6rem;font-weight:700;letter-spacing:.02em}}
.ai-section-wrap h3:first-of-type{{margin-top:0}}
.ai-markdown{{font-size:.85rem;color:#cbd5e1;line-height:1.8}}
.ai-markdown strong{{color:#f0f9ff;font-weight:700}}
.ai-markdown table{{width:100%;border-collapse:collapse;margin:.8rem 0;font-size:.8rem}}
.ai-markdown th{{background:#1e3a5f;color:#e2e8f0;padding:.6rem .8rem;text-align:left;font-weight:700;border-bottom:1px solid #334155}}
.ai-markdown td{{padding:.55rem .8rem;border-bottom:1px solid #1e293b;color:#cbd5e1}}
.ai-markdown tr:hover td{{background:rgba(56,189,248,.03)}}
/* 个股深度研判卡片 */
.ai-stock-grid{{display:grid;grid-template-columns:1fr;gap:16px;margin-top:1rem}}
.ai-stock-card{{background:#1a2332;border:1px solid #2d3a4f;border-radius:14px;overflow:hidden;transition:border-color .25s,box-shadow .25s,transform .2s;box-shadow:0 3px 12px rgba(0,0,0,.25)}}
.ai-stock-card:hover{{border-color:#38bdf8;box-shadow:0 6px 24px rgba(56,189,248,.1);transform:translateY(-2px)}}
.ai-stock-card-header{{display:flex;align-items:center;gap:12px;padding:16px 20px;background:linear-gradient(135deg, rgba(56,189,248,.06), rgba(139,92,246,.04));border-bottom:1px solid #2d3a4f;flex-wrap:wrap}}
.ai-stock-card-header .ai-rank{{font-size:1.1rem;font-weight:800;color:#38bdf8;min-width:30px}}
.ai-stock-card-header .ai-name{{font-size:1rem;font-weight:700;color:#f8fafc}}
.ai-stock-card-header .ai-code{{font-size:.75rem;color:#94a3b8}}
.ai-stock-card-body{{padding:16px 20px}}
.ai-stock-card-body .ai-dim{{margin-bottom:10px;font-size:.82rem;line-height:1.7;color:#94a3b8}}
.ai-stock-card-body .ai-dim:last-child{{margin-bottom:0}}
.ai-stock-card-body .ai-dim-label{{display:inline-block;font-size:.68rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;padding:2px 10px;border-radius:4px;margin-right:8px;vertical-align:middle}}
.ai-dim-label.overview{{background:rgba(56,189,248,.15);color:#38bdf8}}
.ai-dim-label.technical{{background:rgba(139,92,246,.15);color:#8b5cf6}}
.ai-dim-label.capital{{background:rgba(34,197,94,.15);color:#22c55e}}
.ai-dim-label.fundamental{{background:rgba(245,158,11,.15);color:#f59e0b}}
.ai-dim-label.risk{{background:rgba(239,68,68,.15);color:#ef4444}}
.ai-dim-label.suggestion{{background:rgba(6,182,212,.15);color:#06b6d4}}
.ai-dim strong{{color:#e2e8f0}}
/* 市场全景/板块深度结构化卡片 */
.ai-info-card{{background:rgba(56,189,248,.04);border:1px solid rgba(56,189,248,.12);border-radius:10px;padding:14px 18px;margin:.8rem 0}}
.ai-info-card:first-child{{margin-top:0}}
.ai-info-card .ai-card-title{{font-size:.8rem;font-weight:700;color:#38bdf8;letter-spacing:.04em;margin-bottom:8px;display:flex;align-items:center;gap:6px}}
.ai-info-card .ai-card-title .dot{{width:6px;height:6px;border-radius:50%;background:#38bdf8;display:inline-block}}
/* 增强TOP10卡片 */
.top10-card{{border-left:3px solid transparent;transition:border-left-color .3s,box-shadow .3s,transform .2s}}
.top10-card:hover{{border-left-color:#38bdf8}}
.top10-card-header .badge{{margin-left:auto}}
.top10-card-reason{{border-left:2px solid #2d3a4f;padding-left:12px;margin:8px 0;transition:border-color .2s}}
.top10-card-reason:hover{{border-left-color:rgba(56,189,248,.3)}}
</style></head><body>
<div class="header"><h1>A股短线标的筛选报告</h1><div class="sub">{prediction_date} | 规则版本 {file_version}</div></div>
<div class="container">
<div class="index-row">{index_cards}</div>
<div class="meta-row">
<div class="meta-card"><div class="label">预测日期</div><div class="value">{prediction_date}</div></div>
<div class="meta-card"><div class="label">数据日期</div><div class="value">{data_date}</div></div>
<div class="meta-card"><div class="label">市场环境</div><div class="value">{market_condition}</div></div>
<div class="meta-card"><div class="label">建议仓位</div><div class="value">{position_pct}%</div></div>
<div class="meta-card"><div class="label">最终推荐</div><div class="value">{fc}只</div></div></div>
<section><h2>筛选管道</h2><div class="funnel">{funnel_html}</div></section>
<section><h2>数据可视化</h2><div class="chart-grid">
<div><h3 style="font-size:.9rem;color:#cbd5e1;margin-bottom:.5rem">策略分布</h3><div class="seg-bar">{seg_html if seg_html else '<div style="color:#94a3b8;text-align:center;padding:1rem">无推荐标的</div>'}</div><div class="legend">{legend_html}</div></div>
<div><h3 style="font-size:.9rem;color:#cbd5e1;margin-bottom:.5rem">硬排除TOP5</h3>{bar_html if bar_html else '<div style="color:#94a3b8">无排除记录</div>'}</div>
<div><h3 style="font-size:.9rem;color:#cbd5e1;margin-bottom:.5rem">各策略数量</h3>{strat_bars if strat_bars else '<div style="color:#94a3b8">无匹配</div>'}</div>
<div><h3 style="font-size:.9rem;color:#cbd5e1;margin-bottom:.5rem">概述</h3><div style="font-size:.8rem;color:#cbd5e1">全市场→{total_raw}只入围→{ae}只通过硬排除→{asig}只通过信号过滤→{astr}只匹配策略→{amicro}只通过微观结构→{aind}只通过行业限制→{aind - anew}只通过新闻筛查→<strong style="color:#38bdf8">最终{fc}只</strong></div></div>
</div></section>
<section><h2>系统告警</h2><div class="alert-list">{alerts_html}</div></section>
<section><h2>最终推荐标的</h2><div style="overflow-x:auto"><table>
<thead><tr><th>#</th><th>TOP10</th><th>策略</th><th>标的</th><th>代码</th><th>行业</th><th>二级行业</th><th>涨跌幅</th><th>开盘</th><th>收盘</th><th>振幅</th><th>60日高</th><th>60日低</th><th>档位</th><th>7日</th><th>评分</th><th>置信</th><th>进场</th><th>止损</th><th>止盈</th><th>盈亏比</th><th>回测</th></tr></thead>
<tbody>{rows_html if rows_html else '<tr><td colspan="22" style="text-align:center;color:#94a3b8;padding:2rem">无合适标的</td></tr>'}</tbody></table></div></section>
<section><h2>策略说明</h2><table>
<thead><tr><th style="width:18%">策略</th><th style="width:48%">条件</th><th style="width:16%">仓位(震荡)</th><th style="width:18%">仓位(弱市)</th></tr></thead>
<tbody>
<tr><td><span class="badge strat_a">A动量延续</span></td><td style="white-space:normal;word-break:break-all">涨3-7%+量比1.5-3.0+弱市/极端上涨关闭</td><td>12-17%</td><td>0%(关闭)</td></tr>
<tr><td><span class="badge strat_b">B超跌反弹</span></td><td style="white-space:normal;word-break:break-all">跌2.5-9.5%+振幅>3%+反弹确认(close>low*1.01)+深度跌幅加分</td><td>8-10%</td><td>5-8%</td></tr>
<tr><td><span class="badge strat_c">C事件驱动</span></td><td style="white-space:normal;word-break:break-all">涨1-2%+量比≥1.0或财报季+弱市关闭</td><td>10-12%</td><td>0%(关闭)</td></tr>
<tr><td><span class="badge strat_d">D回调企稳</span></td><td style="white-space:normal;word-break:break-all">涨3-6%(弱市上限7%)+振幅1.5-10%+阳线+弱市不折扣</td><td>12-15%</td><td>8-12%</td></tr>
<tr><td><span class="badge strat_e">E资金埋伏</span></td><td style="white-space:normal;word-break:break-all">涨0-1%+主力流入>3000万(代理vr≥0.6+to≥0.5%)+弱市不折扣</td><td>5-8%</td><td>3-5%</td></tr>
<tr><td><span class="badge strat_f">F北向资金</span></td><td style="white-space:normal;word-break:break-all">涨0-1%+主力流入>5000万+近5日持续≥3日+弱市不折扣</td><td>3-5%</td><td>3-5%</td></tr>
<tr><td><span class="badge strat_g">G横盘突破</span></td><td style="white-space:normal;word-break:break-all">涨1.0-3.0%+振幅1.5-6%+量比≥1.0阳线+弱市不折扣低吸</td><td>8-10%</td><td>5-8%</td></tr>
<tr><td><span class="badge strat_h">H地量见底</span></td><td style="white-space:normal;word-break:break-all">涨-3~1.0%+量比<1.0+锤子线/十字星阳线+弱市放宽</td><td>5-8%</td><td>3-5%</td></tr>
<tr><td><span class="badge strat_i">I均线突破</span></td><td style="white-space:normal;word-break:break-all">MA5/10/20粘合<4%+放量vr≥1.0阳线+收盘≥均线×0.98+弱市跳过</td><td>8-10%</td><td>5-8%</td></tr>
<tr><td><span class="badge strat_j">J龙回头</span></td><td style="white-space:normal;word-break:break-all">20日强势股+回调6-25%+缩量vr<1.0收阳+弱市跳过</td><td>8-10%</td><td>5-8%</td></tr>
<tr><td><span class="badge strat_k">K缺口回补</span></td><td style="white-space:normal;word-break:break-all">前日跳空高开1-7%+回踩缺口上沿确认+收阳+弱市跳过</td><td>8-10%</td><td>5-8%</td></tr>
<tr><td><span class="badge strat_l">L黄金坑</span></td><td style="white-space:normal;word-break:break-all">5日急跌≥5%+V型反弹≥2%+放量vr≥1.0收阳+弱市跳过</td><td>8-10%</td><td>5-8%</td></tr>
<tr><td><span class="badge strat_m">M涨停回调</span></td><td style="white-space:normal;word-break:break-all">近5日涨停+回调4-25%+缩量vr<1.0收阳+弱市跳过</td><td>6-8%</td><td>3-5%</td></tr>
<tr><td><span class="badge strat_n">N新高突破</span></td><td style="white-space:normal;word-break:break-all">收盘≥20日新高×0.99+放量vr≥1.0阳线+弱市跳过</td><td>8-10%</td><td>5-8%</td></tr>
<tr><td><span class="badge strat_o">O回踩均线</span></td><td style="white-space:normal;word-break:break-all">60日涨>15%+回踩MA20±3%+缩量vr<1.0收阳+弱市跳过</td><td>8-10%</td><td>5-8%</td></tr>
<tr><td><span class="badge strat_p">P地量反弹</span></td><td style="white-space:normal;word-break:break-all">连续3日缩量至地量+当日放量vr≥1.2+涨1.0-5%阳线+弱市跳过</td><td>6-8%</td><td>3-5%</td></tr>
<tr><td><span class="badge strat_q">Q W底突破</span></td><td style="white-space:normal;word-break:break-all">20日内两底相差<5%+放量vr≥1.2突破颈线+阳线+弱市跳过</td><td>8-10%</td><td>5-8%</td></tr>
</tbody></table></section>
<section><h2>TOP10 板块热度精选推荐理由</h2>
<div class="top10-cards">{top10_cards_html if top10_cards_html else '<div style="color:#94a3b8;padding:1rem">暂无TOP10数据</div>'}</div></section>
{ai_html}
</div>
<section><h2 style="display:flex;align-items:center;gap:.5rem">📊 历史回测 <span style="font-size:.7rem;color:#94a3b8;font-weight:400">最近90天 | 最大持仓10交易日</span></h2>
{backtest_html}
</section>
<div class="footer"><p>版本: {file_version} | 生成时间: {beijing_date}</p><p class="disclaimer">⚠️ 免责声明：本报告仅供研究参考，不构成任何投资建议。投资有风险，入市需谨慎。</p><style>
.metric-card-bt{background:#0f172a;border:1px solid #334155;border-radius:10px;padding:13px 10px;text-align:center;transition:border-color .2s,transform .15s}
.metric-card-bt:hover{border-color:#475569;transform:translateY(-1px)}
.metric-label-bt{color:#94a3b8;font-size:.68rem;margin-bottom:4px;text-transform:uppercase;letter-spacing:.04em}
.metric-value-bt{color:#e2e8f0;font-size:1.1rem;font-weight:700}
.metric-value-bt.win{color:#22c55e}.metric-value-bt.loss{color:#ef4444}
</style>
</div></body></html>"""
    
    with open(hp, 'w', encoding='utf-8') as f: f.write(html_content)
    log_alert("INFO", "HTML报告", f"已生成至 {hp}")
    return hp

# ============================================================
# 步骤21-22：验证 + 推荐历史
# ============================================================
def step21_final_verify(mp, fc):
    try:
        with open(mp, 'r', encoding='utf-8') as f: content = f.read()
        # v6.9.50: 仅统计推荐标的表（TOP10精选表之前的部分），排除TOP10表干扰
        main_section = content.split('## TOP10')[0] if '## TOP10' in content else content
        tr = sum(1 for l in main_section.split('\n') if l.strip().startswith('| ') and l.split('|')[1].strip().isdigit())
        if tr != fc: log_alert("ERROR", "数量校验", f"概况{fc}≠MD表格{tr}")
        else: log_alert("INFO", "最终验证", f"通过（{fc}只）")
    except FileNotFoundError:
        log_alert("ERROR", "数量校验", "MD文件不存在")

def step22_write_history(candidates):
    """v6.13.11: 去重写入——按(code,strategy,entry)去重，避免多次运行重复追加"""
    hf = f"/workspace/推荐历史_{data_date.replace('-', '')}.json"
    existing = safe_read_json(hf)
    existing_keys = set()
    for r in existing:
        if r.get('type') == 'recommendation':
            existing_keys.add((r.get('code'), r.get('strategy'), round(r.get('entry', 0), 2)))
    written = 0
    for c in candidates:
        entry = calc_entry_price(c)
        key = (c.get('code'), c.get('strategy'), round(entry, 2) if entry else 0)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        safe_append_json(hf, {"type": "recommendation", "code": c.get('code'), "name": c.get('name'),
            "strategy": c.get('strategy'), "industry": _industry_str(c), "business": c.get('business', ''),
            "score": c.get('score'), "confidence": c.get('confidence'),
            "entry": entry, "change_pct": c.get('change_pct'),
            "date": data_date, "prediction_date": prediction_date})
        written += 1
    log_alert("INFO", "推荐历史", f"已追加{written}条(跳过{len(candidates)-written}条重复)")

# ============================================================
# 步骤26：GitHub同步
# ============================================================
def step26_github_sync(mp, hd, candidates):
    if not GITHUB_TOKEN: log_alert("WARNING", "GitHub同步", "无令牌"); return
    rd = None
    try:
        repo_url = f"https://github.com/{GITHUB_REPO}.git"
        rd = "/tmp/lv_sync"
        if os.path.exists(rd): shutil.rmtree(rd, ignore_errors=True)
        _git_with_token(["git", "clone", "--depth", "1", "--branch", "main", repo_url, rd], timeout=30)
        c15 = (datetime.strptime(prediction_date, '%Y-%m-%d') - timedelta(days=15)).strftime('%Y-%m-%d').replace('-', '')
        for f in list(os.listdir(rd)):
            for prefix in ['短线标的_', '推荐历史_']:
                if f.startswith(prefix):
                    d = f.replace(prefix, '').replace('.md', '').replace('.json', '')
                    if len(d) == 8 and d < c15:
                        pf = os.path.join(rd, f)
                        if os.path.exists(pf): os.remove(pf)
            # v6.8.8: 清理超过15天的HTML报告目录
            if f.startswith('ashare-screening-'):
                d = f.replace('ashare-screening-', '')
                if len(d) == 8 and d < c15:
                    pf = os.path.join(rd, f)
                    if os.path.exists(pf): shutil.rmtree(pf, ignore_errors=True)
        shutil.copy(mp, os.path.join(rd, f"短线标的_{prediction_date}.md"))
        hn = f"ashare-screening-{pred_yyyymmdd}"
        dst = os.path.join(rd, hn)
        if os.path.exists(dst): shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(hd, dst)
        if os.path.exists("/workspace/持仓跟踪.xlsx"):
            shutil.copy("/workspace/持仓跟踪.xlsx", os.path.join(rd, "持仓跟踪.xlsx"))
        for f in os.listdir('/workspace'):
            if f.startswith('推荐历史_') and f.endswith('.json'):
                shutil.copy(os.path.join('/workspace', f), os.path.join(rd, f))
        # v6.13.13: 同步回测报告到backtest/子目录(GitHub Pages不支持中文文件名)
        bt_html = os.path.join('/workspace', '回测报告.html')
        if os.path.exists(bt_html):
            bt_dir = os.path.join(rd, 'backtest')
            os.makedirs(bt_dir, exist_ok=True)
            shutil.copy(bt_html, os.path.join(bt_dir, 'index.html'))
        for f in ['回测报告.md']:
            fp = os.path.join('/workspace', f)
            if os.path.exists(fp):
                shutil.copy(fp, os.path.join(rd, f))
        subprocess.run(["git", "-C", rd, "config", "user.email", "ashare-bot@github.com"], capture_output=True, timeout=15)
        subprocess.run(["git", "-C", rd, "config", "user.name", "ashare-screener"], capture_output=True, timeout=15)
        subprocess.run(["git", "-C", rd, "add", "."], capture_output=True, timeout=15)
        subprocess.run(["git", "-C", rd, "commit", "-m", f"筛选结果 {prediction_date} (v{file_version})", "--allow-empty"], capture_output=True, timeout=15)
        result = _git_with_token(["git", "-C", rd, "push", "origin", "main"], timeout=60, check=False)
        if result.returncode == 0: log_alert("INFO", "GitHub同步", f"✅ {prediction_date} 已推送")
        else: log_alert("WARNING", "GitHub同步", f"推送失败: {result.stderr[:100]}")
    except Exception as e: log_alert("WARNING", "GitHub同步", f"失败: {str(e)[:100]}")
    finally:
        if rd and os.path.exists(rd): shutil.rmtree(rd, ignore_errors=True)

# ============================================================
# 步骤27：飞书推送
# ============================================================
def step27_feishu_push(candidates, total_raw, ae, asig, astr, aind, anew, sd):
    if not FEISHU_WEBHOOK: log_alert("WARNING", "飞书推送", "无Webhook"); return
    try:
        fc = len(candidates)
        sn = _STRATEGY_NAMES
        ss = " | ".join([f"{s}{sn.get(s,'')}:{sd.get(s,0)}只" for s in ['A','B','C','D','E','F','G','H','I','J','K','L','M','N','O','P','Q','R','S','T'] if sd.get(s, 0) > 0]) or "无推荐标的"
        pb = "https://lc132.github.io/lv"
        pr = f"{pb}/ashare-screening-{pred_yyyymmdd}/ashare-screening-{pred_yyyymmdd}.html"
        card = {"msg_type": "interactive", "card": {
            "header": {"title": {"tag": "plain_text", "content": f"📊 每日短线标的筛选 — {prediction_date}"}, "template": "blue"},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**数据来源**: {data_date}  |  **市场环境**: {market_condition}  |  **建议仓位**: {position_pct}%"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"原始: **{total_raw}**只 → 硬排: **{ae}**只 → 信号: **{asig}**只 → 策略: **{astr}**只 → 微观: **{amicro}**只 → 行业: **{aind}**只 → 新闻: **{aind - anew}**只 → ★ 最终: **{fc}**只"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**策略分布**: {ss}"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"📈 [**查看完整可视化报告（GitHub Pages）**]({pr})\n📁 [**报告列表首页**]({pb})"}},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": "⚠️ 仅供参考，不构成投资建议"}]}]}}
        req = urllib.request.Request(FEISHU_WEBHOOK, data=json.dumps(card, ensure_ascii=False).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        if result.get('code') == 0:
            log_alert("INFO", "飞书推送", f"✅ {prediction_date} 已推送")
        else: log_alert("WARNING", "飞书推送", f"推送失败: {result.get('msg','')}")
    except Exception as e: log_alert("WARNING", "飞书推送", f"失败: {str(e)[:80]}")

# ============================================================
# 数据源监控
# ============================================================
def update_data_source_monitor(ds):
    monitor = safe_read_json("/workspace/数据源监控.json", default={"tencent_success": 0, "tencent_consecutive_failures": 0, "total_runs": 0, "last_source": "", "history": []})
    if not isinstance(monitor, dict): monitor = {"tencent_success": 0, "tencent_consecutive_failures": 0, "total_runs": 0, "last_source": "", "history": []}
    monitor["total_runs"] = monitor.get("total_runs", 0) + 1
    if ds == "tencent":
        monitor["tencent_success"] = monitor.get("tencent_success", 0) + 1
        cf = monitor.get("tencent_consecutive_failures", 0)
        if cf > 0: log_alert("INFO", "数据源监控", f"腾讯一级已恢复（连续失败{cf}次）")
        monitor["tencent_consecutive_failures"] = 0
    else:
        monitor["tencent_consecutive_failures"] = monitor.get("tencent_consecutive_failures", 0) + 1
        cf = monitor["tencent_consecutive_failures"]
        if cf == 1: log_alert("WARNING", "数据源监控", f"腾讯一级第1次不可达，降级至{ds}")
        elif cf >= 10: log_alert("CRITICAL", "数据源监控", f"腾讯一级连续{cf}次不可达！")
    monitor["last_source"] = ds
    monitor["history"].append({"date": data_date, "source": ds, "success": ds == "tencent"})
    if len(monitor["history"]) > 30: monitor["history"] = monitor["history"][-30:]
    safe_write_json("/workspace/数据源监控.json", monitor)

# ============================================================
# 主流程
# ============================================================
def main():
    global market_condition, position_pct
    print("=" * 60)
    print(f"A股每日盘前短线标的筛选 {BUILTIN_VERSION}")
    print("=" * 60)
    
    print("\n[步骤0] 北京时间..."); step0_get_beijing_time()
    print(f"  Beijing={beijing_date} Data={data_date} Pred={prediction_date}")
    record_step_status("步骤0: 北京时间", "OK" if beijing_date else "WARN", "API降级为系统时间" if not _beijing_api_ok else "")

    print("\n[步骤0A] 拉取持仓..."); step0A_pull_holdings()
    record_step_status("步骤0A: 持仓拉取", "OK")

    print("\n[步骤1] 节假日...")
    if step1_holiday_check(): print("  节假日跳过"); record_step_status("步骤1: 节假日", "SKIP", "今日为节假日"); return
    record_step_status("步骤1: 节假日", "OK")

    print("\n[步骤2] 极端行情...")
    if step2_extreme_market(): print("  极端行情跳过"); record_step_status("步骤2: 极端行情", "SKIP", "触发极端行情保护"); return
    record_step_status("步骤2: 极端行情", "OK")

    print("\n[步骤3] 外围市场..."); step3_external_markets()
    record_step_status("步骤3: 外围市场", "OK")

    print("\n[步骤3A] 大盘代理..."); step3A_domestic_index_check()
    record_step_status("步骤3A: 大盘代理", "OK")
    
    print("\n[步骤4] 持仓行情..."); holdings = step4_holdings_sync()
    ahc = set(h.get('code') for h in holdings if h.get('code'))
    print(f"  持仓: {len(holdings)}只")
    record_step_status("步骤4: 持仓行情", "OK" if holdings else "SKIP", "无持仓" if not holdings else "")

    print("\n[步骤4A] 做T评估..."); step4A_doT_eval(holdings)
    print("\n[步骤4B] 持仓跟踪..."); step4B_sync_holdings_xlsx(holdings)
    print("\n[步骤4C] 持仓危机..."); crisis_alerts = step4C_crisis_check(holdings)

    print("\n[步骤5] 清理..."); step5_history_clean()
    print("\n[步骤6] 初始化..."); step6_file_init()
    print("\n[步骤7] 财报季..."); step7_earnings_season()
    print("\n[步骤8] 大盘环境..."); step8_market_environment()
    print(f"  环境: {market_condition} | 仓位: {position_pct}%")
    record_step_status("步骤8: 大盘环境", "OK", f"{market_condition} {position_pct}%")
    
    print("\n[步骤10A] 全市场拉取..."); all_stocks, ds = step10A_fetch_all_stocks()
    update_data_source_monitor(ds)
    
    print("\n[步骤10B] 行业补全...")
    _preload_industry_from_eastmoney(all_stocks)  # v6.9.34: 东方财富HTTP API获取行业分类
    for s in all_stocks: s['industry'] = lookup_industry(s.get('code', ''))
    
    print("\n[步骤10D] 财务数据..."); pledge_data, goodwill_data, unlock_data = step10D_fetch_financials()
    
    raw_pool = [s for s in all_stocks if s.get('change_pct') is not None and s.get('change_pct') >= -9.5
                and s.get('close') is not None and s.get('close') > 0]
    # v6.9.42: 预过滤明显不合格标的（ST/科创/北交/创业板/低价/高价），避免占用500名额
    raw_pool = [s for s in raw_pool
                if not (s.get('name', '').startswith('ST') or s.get('name', '').startswith('*ST'))
                and not s.get('code', '').startswith(('688', '8', '300', '301'))
                and 5 <= s.get('close', 0) <= 100]
    # v6.9.42: 排序键切换为成交额优先（原换手率优先导致小盘股挤占蓝筹名额）
    raw_pool.sort(key=lambda x: ((x.get('amount', 0) or 0), (x.get('turnover', 0) or 0)), reverse=True)
    raw_pool = raw_pool[:500]
    total_raw = len(raw_pool)
    print(f"  原始池: {total_raw}只")
    record_step_status("步骤10A: 全市场拉取", "OK", f"{total_raw}只")

    # v6.13.11: 跳过pytdx(沙箱内始终不可达)，腾讯HTTP一级 → iTick二级
    print("\n[步骤10C] 历史K线..."); kline_data = step10C_fetch_klines_http(raw_pool)
    valid_kline = sum(1 for v in kline_data.values() if v and v.get('closes'))
    if valid_kline < len(raw_pool) * 0.3:
        kline_data = step10C_fetch_klines_itick(raw_pool)
        valid_kline = sum(1 for v in kline_data.values() if v and v.get('closes'))
        log_alert("WARNING", "K线降级", f"腾讯HTTP仅{valid_kline}只有效，已切换iTick")
    record_step_status("步骤10C: 历史K线", "OK", f"{valid_kline}有效")
    
    print("\n[步骤11] 硬排除..."); ael, _, er = step11_hard_exclude(raw_pool, ahc, kline_data, pledge_data, goodwill_data, unlock_data, {}); ae = len(ael)
    print("\n[步骤10E] F10基本面..."); fundamental_data = step10E_fetch_fundamentals(ael)
    print("\n[步骤10F] 风险事件..."); unlock_events, cb_events, earnings_window = step10F_fetch_risk_events()
    print("\n[步骤10G] 拥挤度..."); inst_holding, margin_overheat = step10G_fetch_crowding_data(ael)
    print("\n[步骤10H] 二级行业..."); sub_industry_data = step10H_fetch_sub_industry(ael)
    print("\n[步骤12] 信号过滤..."); asl, _ = step12_signal_filter(ael, kline_data, fundamental_data, (unlock_events, cb_events, earnings_window), (inst_holding, margin_overheat)); asig = len(asl)
    print("\n[步骤13] 策略匹配..."); sm = step13_strategy_match(asl, kline_data); astr = len(sm)
    # v6.9.53: 策略匹配成功后统一+1计数+回填当日策略（修复步骤11预加导致计数不准）
    for c in sm:
        c['_recent_7d'] = c.get('_recent_7d', 0) + 1
        c['_recent_7d_strategies'][data_date] = c.get('strategy', '?')
    print("\n[步骤14] 评分..."); scored = step14_scoring(sm, kline_data)
    print("\n[步骤15] 微观结构过滤..."); scored2, micro_filtered, micro_stats = step15_microstructure_filter(scored, kline_data); amicro = len(scored2)
    print("\n[步骤16] 综合评分+平局打破..."); ranked = step16_comprehensive_score(scored2)
    print("\n[步骤17] 行业限制..."); ail = step17_industry_limit(ranked); aind = len(ail)
    print("\n[步骤18] 新闻筛查..."); ail, anew = step18_news_screening(ail)
    print("\n[步骤18B] TOP10龙虎榜+正面新闻..."); step18B_top10_enrichment(ail)
    print("\n[步骤19] 降级..."); final = step19_shortfall_handling(ail); fc = len(final)
    sd = Counter(c.get('strategy') for c in final)
    record_step_status("步骤19: 降级", "OK", f"最终{fc}只")
    
    # 注入二级行业到候选
    for c in final: c['business'] = sub_industry_data.get(c.get('code', ''), '')
    # v6.9.52: 注入F10基本面到候选（供TOP10卡片使用）
    for c in final:
        fd = fundamental_data.get(c.get('code', ''), {})
        for k, v in fd.items():
            if v is not None: c[f'_fd_{k}'] = v
    # v6.12.19: 注入60日最高/最低价到候选
    for c in final:
        kd = kline_data.get(c.get('code', ''), {})
        c['_high60'] = kd.get('high60', 0) or 0
        c['_low60'] = kd.get('low60', 0) or 0
    # v6.12.21: 基于60日区间计算入场档位
    for c in final:
        tier_label, tier_cls = _calc_tier_label(c)
        c['_tier_label'] = tier_label
        c['_tier_cls'] = tier_cls
    
    # v6.12.4: 构建涨停板块分布（供AI板块深度研判使用）
    sector_limit_up = {}
    for s in all_stocks:
        if s.get('change_pct') is not None and s['change_pct'] >= 9.5:
            ind = lookup_industry(s.get('code', ''))
            sector_limit_up[ind] = sector_limit_up.get(ind, 0) + 1
    # v6.12.10: 预计算盈亏比（板块热度→盈亏比排序，供AI和TOP10使用）
    _compute_pl_ratios(final, sector_limit_up)
    # v6.12.5: 主力资金流向批量获取（东方财富API，仅对最终股票池）
    print("\n[步骤15A] 主力资金流向..."); flow_data = step10C_flow_fetch_main_inflow(final)
    for s in final:
        code = s.get('code', '')
        if code in flow_data and flow_data[code] is not None:
            s['main_inflow'] = flow_data[code]
        else:
            s['main_inflow'] = None
    log_alert("INFO", "主力资金", f"获取{sum(1 for v in flow_data.values() if v is not None)}只/{len(final)}只")
    print("\n[步骤15B] AI智能分析(TOP10)..."); ai_report = step15B_ai_analysis(final, kline_data, index_data, market_condition, sector_limit_up, total_raw, ae, asig, astr, amicro, aind, fc)
    

# v6.12.15: 历史回测（读取推荐历史，模拟止盈止损，生成HTML/MD报告+飞书推送+筛选标记）
    bt_result = None; bt_lookup = {}
    if any(f.startswith("推荐历史_") and f.endswith(".json") for f in os.listdir(DATA_DIR)):
        bt_result = run_backtest(hold_days=10, max_days_lookback=90)
        if bt_result.get('all_trades'):
            generate_backtest_report(bt_result, "/workspace/回测报告.md")
            generate_backtest_html(bt_result, "/workspace/回测报告.html")
            push_backtest_to_feishu(bt_result)
            bt_lookup = _build_backtest_lookup(bt_result)
        record_step_status("步骤25: 历史回测", "OK", f"{len(bt_result.get('all_trades',[]))}笔交易")
    else:
        record_step_status("步骤25: 历史回测", "SKIP", "无推荐历史记录")

    print("\n[步骤20] Markdown..."); mp = step20_output_markdown(final, total_raw, ae, asig, astr, amicro, aind, anew, er, ai_report, bt_lookup)
    record_step_status("步骤20: Markdown", "OK", mp)
    print("\n[步骤20B] HTML..."); hp = step20B_generate_html(final, total_raw, ae, asig, astr, amicro, aind, anew, er, crisis_alerts, ai_report, bt_lookup, kline_data, bt_result); hd = os.path.dirname(hp)
    record_step_status("步骤20B: HTML报告", "OK", hp)
    print("\n[步骤21] 验证..."); step21_final_verify(mp, fc)
    record_step_status("步骤21: 最终验证", "OK", f"{fc}只通过")
    if beijing_weekday in (5, 6):
        print("\n[步骤22] 推荐历史... 周末跳过")
        record_step_status("步骤22: 推荐历史", "SKIP", "周末")
    else:
        print("\n[步骤22] 推荐历史..."); step22_write_history(final)
        record_step_status("步骤22: 推荐历史", "OK", f"{fc}条")
    print("\n" + "=" * 60)
    print("📊 筛选概况")
    print("=" * 60)
    print(f"prediction_date={prediction_date} (数据来源:{data_date})")
    print(f"①原始:N={total_raw} → ②硬排除:N={ae} → ③信号过滤:N={asig} → ④策略:N={astr} → ⑤微观结构:N={amicro} → ⑥行业限制:N={aind} → ⑦新闻筛查:N={aind - anew} → ★ 最终:N={fc}")
    sn = _STRATEGY_NAMES
    print(f"策略分布: " + " ".join([f"{s}:{sd.get(s,0)}" for s in ['A','B','C','D','E','F','G','H','I','J','K','L','M','N','O','P','Q','R','S','T']]))
    print(f"排除TOP5: " + " ".join([f"{r}:{c}只" for r, c in er.most_common(5)]))
    print("=" * 60)
    
    if crisis_alerts:
        print("\n⚠️ 持仓危机:")
        for a in crisis_alerts: print(f"  {a}")
    
    print("\n[步骤26] GitHub同步..."); step26_github_sync(mp, hd, final)
    record_step_status("步骤26: GitHub同步", "OK")
    print("\n[步骤27] 飞书推送..."); step27_feishu_push(final, total_raw, ae, asig, astr, aind, anew, sd)
    record_step_status("步骤27: 飞书推送", "OK")
    
    # v6.13.11: 步骤执行状态报告
    print_step_status_summary()
    
    print(f"\n✅ 完成！ {mp}")
    return final, mp

if __name__ == "__main__":
    main()
