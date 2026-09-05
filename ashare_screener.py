#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股每日盘前短线标的智能筛选 v6.22.27
37步完整执行流程 | 腾讯一级行情 | 腾讯HTTP一级K线 | iTick二级K线 | 行业缓存读取 | 行业缓存根治(schema校验+完整性自检+L2禁写) | 21策略 | 29信号 | 13项硬排除 | 微观结构过滤 | AI策略分析 | MACD+K线评分 | 多因子共振 | 资金去向 | 基本面PK维度(成长性/盈利能力/估值/资产质量/现金流/筹码/热度) | 个股深度研判👑冠军 | 同策略+跨策略冠军PK | 冠军始终进入深度分析(@since v6.14.0) | 极端行情修复监测(@since v6.15.0) | CLS电报v2(@since v6.16.0) | 麦蕊智数涨停/跌停/公告(@since v6.16.1) | 新闻筛查修复(@since v6.16.16) | 五项整改(@since v6.16.35)
"""
import sys, urllib.request, urllib.error, urllib.parse, json, os, math, time, shutil, subprocess, html, gzip, re, hashlib, ssl, socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict

_axdata_available = False
_AXDATA_CLIENT = None
try:
    import axdata as _ax
    _AXDATA_CLIENT = _ax.AxDataClient()
    _axdata_available = True
except Exception:
    pass

import http.client

socket.setdefaulttimeout(12)

_SSL_CTX = ssl._create_unverified_context()

_HTTP_RETRY_DEFAULT = 2
_HTTP_RETRY_BACKOFF_BASE = 1.5

def _http_retry(url, timeout=10, retries=_HTTP_RETRY_DEFAULT, label="HTTP"):
    import http.client as _hc
    last_error = None
    for attempt in range(retries):
        try:
            resp = urllib.request.urlopen(url, timeout=timeout, context=_SSL_CTX)
            status = resp.getcode()
            if 400 <= status < 600:
                if attempt < retries - 1:
                    if status == 429:
                        retry_after = resp.getheader('Retry-After')
                        if retry_after:
                            try:
                                wait = float(retry_after)
                            except ValueError:
                                wait = _HTTP_RETRY_BACKOFF_BASE ** (attempt + 1)
                        else:
                            wait = _HTTP_RETRY_BACKOFF_BASE ** (attempt + 1)
                    else:
                        wait = _HTTP_RETRY_BACKOFF_BASE ** (attempt + 1)
                    print(f"  ⏳ {label}重试{attempt+1}/{retries-1}({wait:.1f}s): HTTP {status}")
                    time.sleep(wait)
                    continue
                raise urllib.error.URLError(f"HTTP {status} (max retries)")
            return resp
        except (socket.timeout, urllib.error.URLError, ConnectionResetError, TimeoutError) as e:
            last_error = e
            if attempt < retries - 1:
                wait = _HTTP_RETRY_BACKOFF_BASE ** (attempt + 1)
                print(f"  ⏳ {label}重试{attempt+1}/{retries-1}({wait:.1f}s): {str(e)[:40]}")
                time.sleep(wait)
        except OSError as e:
            if isinstance(e, (BrokenPipeError, ConnectionResetError)):
                last_error = e
                if attempt < retries - 1:
                    wait = _HTTP_RETRY_BACKOFF_BASE ** (attempt + 1)
                    print(f"  ⏳ {label}重试{attempt+1}/{retries-1}({wait:.1f}s): {type(e).__name__}")
                    time.sleep(wait)
            else:
                raise
        except _hc.CannotSendRequest as e:
            last_error = e
            if attempt < retries - 1:
                wait = _HTTP_RETRY_BACKOFF_BASE ** (attempt + 1)
                print(f"  ⏳ {label}重试{attempt+1}/{retries-1}({wait:.1f}s): CannotSendRequest")
                time.sleep(wait)
        except _hc.BadStatusLine as e:
            last_error = e
            if attempt < retries - 1:
                wait = _HTTP_RETRY_BACKOFF_BASE ** (attempt + 1)
                print(f"  ⏳ {label}重试{attempt+1}/{retries-1}({wait:.1f}s): BadStatusLine")
                time.sleep(wait)
    raise last_error

from openpyxl import load_workbook
from lib.factor import compute_main_force_position, compute_short_term_breakout, resonance_check
from lib.microstructure import microstructure_filter
from lib.analyst import generate_ai_report, generate_candidate_analysis
from lib.backtest import run_backtest, generate_backtest_report, generate_backtest_html, push_backtest_to_feishu, _build_backtest_lookup
from lib.core import DATA_DIR
from lib.session import init_session, save_step, finish_session, get_progress

def _load_builtin_version():
    _HERE = os.path.dirname(os.path.abspath(__file__))
    for _p in (
        os.path.join(_HERE, "VERSION"),
        os.path.join(_HERE, "..", "VERSION"),
        os.path.join(os.getcwd(), "VERSION"),
    ):
        try:
            with open(_p, "r", encoding="utf-8") as _f:
                _v = _f.read().strip()
                if _v:
                    return _v
        except OSError:
            continue
    return "v6.22.27"

BUILTIN_VERSION = _load_builtin_version()
GITHUB_REPO = "lc132/lv"
INDUSTRY_CACHE_REPO = "lc132/lv-data"
BOT_AUTHOR_NAME = "ashare-screener"
BOT_AUTHOR_EMAIL = "72593777+ashare-screener@users.noreply.github.com"
beijing_now = None; beijing_date = None; beijing_weekday = None
_beijing_api_ok = False
data_date = None; prediction_date = None; pred_yyyymmdd = None
_CN_HOLIDAYS_2026 = [
    "2026-01-01","2026-01-02","2026-02-16","2026-02-17","2026-02-18","2026-02-19","2026-02-20",
    "2026-04-06","2026-05-01","2026-06-19","2026-06-20","2026-06-21",
    "2026-09-25",
    "2026-10-01","2026-10-02","2026-10-05","2026-10-06","2026-10-07"
]
file_version = BUILTIN_VERSION; params = {}
_pl_sorted = []
market_condition = "震荡"; position_pct = 55
index_data = {}
MIN_POSITION_PCT = 20
_step_status = []

def _load_credential(env_key, file_path, fallback=""):
    if env_key in os.environ: return os.environ[env_key]
    try:
        with open(file_path, 'r', encoding='utf-8') as f: return f.read().strip()
    except (FileNotFoundError, PermissionError): pass
    return fallback

GITHUB_TOKEN = _load_credential("GITHUB_TOKEN", "/workspace/.github_token")
FEISHU_WEBHOOK = _load_credential("FEISHU_WEBHOOK", "/workspace/.feishu_webhook")

if GITHUB_TOKEN and not (GITHUB_TOKEN.startswith("ghp_") or GITHUB_TOKEN.startswith("github_pat_")):
    log_alert("WARNING", "凭证校验", "GITHUB_TOKEN格式异常，推送可能失败")
if FEISHU_WEBHOOK and not FEISHU_WEBHOOK.startswith("https://open.feishu.cn/open-apis/bot/v2/hook/"):
    log_alert("WARNING", "凭证校验", "FEISHU_WEBHOOK格式异常，推送可能失败")

MAIRUI_LICENCE = _load_credential("MAIRUI_LICENCE", "/workspace/.mairui_licence")
MAIRUI_BASE = 'http://api.mairui.club'
MAIRUI_BASE_V2 = 'https://a.mairuiapi.com'

def _cls_sign(params_dict):
    sorted_keys = sorted(params_dict.keys())
    raw = '&'.join(f'{k}={params_dict[k]}' for k in sorted_keys if params_dict[k] is not None)
    sha1_hash = hashlib.sha1(raw.encode()).hexdigest()
    return hashlib.md5(sha1_hash.encode()).hexdigest()

_cls_telegraph_cache = None

def _fetch_cls_telegraphs(pages=3):
    global _cls_telegraph_cache
    if _cls_telegraph_cache is not None: return _cls_telegraph_cache
    all_items = []
    for page in range(1, pages + 1):
        try:
            ts = int(time.time())
            params = {'app': 'CailianpressWeb', 'os': 'web', 'sv': '8.4.6'}
            if page > 1: params['page'] = str(page)
            params['sign'] = _cls_sign(params)
            url = f'https://www.cls.cn/v3/depth/list/1003?{urllib.parse.urlencode(params)}'
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://www.cls.cn/'})
            with _http_retry(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                items = data.get('data', [])
                if isinstance(items, list): all_items.extend(items)
        except Exception as e:
                log_alert("DEBUG", "CLS电报", f"第{page}页失败: {type(e).__name__}")
    _cls_telegraph_cache = all_items
    return all_items

def _is_limit_up(code, chg):
    if chg is None: return False
    code = str(code)
    if code.startswith(('82', '83', '87', '88', '92', '43')): return chg >= 29.5
    if code.startswith(('300', '301', '688')): return chg >= 19.5
    return chg >= 9.5

def _is_limit_down(code, chg):
    if chg is None: return False
    code = str(code)
    if code.startswith(('82', '83', '87', '88', '92', '43')): return chg <= -29.5
    if code.startswith(('300', '301', '688')): return chg <= -19.5
    return chg <= -9.5

_mairui_dt_cache = None
_mairui_zt_cache = None
_self_dt_cache = None

def _mairui_fetch_dt_pool(date_str=None, licence=None):
    global _mairui_dt_cache
    if _mairui_dt_cache is not None: return _mairui_dt_cache
    if licence is None: licence = MAIRUI_LICENCE
    if not licence: return []
    if date_str is None:
        if beijing_date is None: return []
        date_str = beijing_date
    try:
        url = f'https://api.mairuiapi.com/hslt/dtgc/{date_str}/{licence}'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.mairui.club/'})
        with _http_retry(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            _mairui_dt_cache = data if isinstance(data, list) else data.get('data', [])
            return _mairui_dt_cache
    except Exception: return []

def _mairui_fetch_zt_pool(date_str=None, licence=None):
    global _mairui_zt_cache
    if _mairui_zt_cache is not None: return _mairui_zt_cache
    if licence is None: licence = MAIRUI_LICENCE
    if not licence: return []
    if date_str is None:
        if beijing_date is None: return []
        date_str = beijing_date
    try:
        url = f'https://api.mairuiapi.com/hslt/ztgc/{date_str}/{licence}'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.mairui.club/'})
        with _http_retry(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            _mairui_zt_cache = data if isinstance(data, list) else data.get('data', [])
            return _mairui_zt_cache
    except Exception: return []

def _mairui_longhubang_for_top10(code):
    if not MAIRUI_LICENCE: return ''
    try:
        zt_pool = _mairui_fetch_zt_pool()
        if not zt_pool: return ''
        for item in zt_pool:
            item_code = str(item.get('dm', '') or '')
            if code != item_code: continue
            zf = item.get('zf', 0) or 0
            fbt = item.get('fbt', '') or ''
            lbc = item.get('lbc', 0) or 0
            fbt_str = f'封板{fbt}' if fbt else ''
            lbc_str = f'{lbc}连板' if lbc and lbc > 1 else ''
            parts = [p for p in [f'涨停+{zf:.1f}%', fbt_str, lbc_str] if p]
            return ' '.join(parts)
        return ''
    except Exception: return ''

def _mairui_announcements(code, licence=None):
    if licence is None: licence = MAIRUI_LICENCE
    if not licence: return None
    try:
        url = f'https://a.mairuiapi.com/hsstock/announcement/{code}/{licence}'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.mairui.club/'})
        with _http_retry(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            return data if isinstance(data, list) else data.get('data', data.get('result', []))
    except Exception: return None

def record_step_status(step_name, status, detail=""):
    _step_status.append({"step": step_name, "status": status, "detail": detail})
    save_step(step_name, status, detail)

def print_step_status_summary():
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

INDUSTRY_CACHE_FILE = "/workspace/行业缓存.json"
_industry_cache = {}
SUB_INDUSTRY_CACHE_FILE = "/workspace/二级行业缓存.json"
_sub_industry_cache = {}

_ZJH_TO_SHENWAN = {
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
    '采矿业-煤炭开采和洗选业': '煤炭',
    '采矿业-石油和天然气开采业': '石油石化',
    '采矿业-黑色金属矿采选业': '钢铁',
    '采矿业-有色金属矿采选业': '有色金属',
    '采矿业-开采辅助活动': '石油石化',
    '采矿业-其他采矿业': '有色金属',
    '金融业-货币金融服务': '银行',
    '金融业-资本市场服务': '非银金融',
    '金融业-保险业': '非银金融',
    '金融业-其他金融业': '非银金融',
    '房地产业': '房地产',
}