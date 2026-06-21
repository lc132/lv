#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股每日盘前短线标的智能筛选 v6.9.11
36步完整执行流程 | 腾讯一级 | 新浪二级 | pytdx历史K线 | 东方财富财务 | 31行业覆盖 | 17策略匹配 | P2修复
"""
import urllib.request, urllib.error, urllib.parse, json, os, time, shutil, subprocess, html
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from openpyxl import load_workbook

BUILTIN_VERSION = "v6.9.17"
GITHUB_REPO = "lc132/lv"
beijing_now = None; beijing_date = None; beijing_weekday = None
data_date = None; prediction_date = None; pred_yyyymmdd = None
file_version = BUILTIN_VERSION; params = {}
market_condition = "震荡"; position_pct = 55
index_data = {}  # 三大指数行情(供HTML使用)
MIN_POSITION_PCT = 20  # v6.8.7: 全局仓位下限

def _load_credential(env_key, file_path, fallback=""):
    if env_key in os.environ: return os.environ[env_key]
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r') as f: return f.read().strip()
        except Exception: pass
    return fallback

GITHUB_TOKEN = _load_credential("GITHUB_TOKEN", "/workspace/.github_token")
FEISHU_WEBHOOK = _load_credential("FEISHU_WEBHOOK", "/workspace/.feishu_webhook")

DEFAULT_PARAMS = {
    "search_budget": 25, "northbound_threshold": 3000, "consecutive_weeks": 2,
    "win_rate_drop_threshold": 10, "limit_down_threshold": 100,
    "max_adjust_params": 3, "confidence_position_enabled": True,
    "strategy_concentration_pct": 30,
    "data_tier_l2_skip_on_unavailable": True,
    "data_tier_l3_downgrade_to_signal": True, "strategy_a_weak_market": "closed"
}

# ============================================================
# 工具函数
# ============================================================
def log_alert(level, module, message, timestamp=None):
    if timestamp is None: timestamp = datetime.now()
    ts = timestamp.strftime('%Y-%m-%d %H:%M:%S') if hasattr(timestamp, 'strftime') else str(timestamp)
    with open('/workspace/系统告警.log', 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] [{level}] {module}: {message}\n")
    print(f"[{level}] {module}: {message}")

def safe_read_json(path, default=None):
    try:
        if not os.path.exists(path): return default if default is not None else []
        with open(path, 'r', encoding='utf-8') as f: return json.load(f)
    except: return default if default is not None else []

def safe_write_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e: log_alert("ERROR", "safe_write_json", f"{path}: {str(e)[:80]}")

def safe_append_json(path, record):
    data = safe_read_json(path); data.append(record); safe_write_json(path, data)

# ============================================================
# 腾讯行情API (v6.6.27: 替代新浪)
# ============================================================
TENCENT_API = "http://qt.gtimg.cn/q="

def _parse_tencent_field(raw, idx, default=None):
    """安全解析腾讯API字段，返回 float 或 default"""
    try:
        if idx >= len(raw): return default
        v = raw[idx]
        if v in ('', '-', None): return default
        return float(v)
    except: return default

def fetch_tencent_index(codes):
    """拉取指数行情，返回 {code: {name,price,prev_close,change_pct,change_amount}}"""
    result = {}
    try:
        url = f"{TENCENT_API}{','.join(codes)}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=5)
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
            except Exception: pass
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
            resp = urllib.request.urlopen(req, timeout=10)
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
                    # 腾讯API字段: [37]=amount(万元) [38]=turnover(%) [39]=pe_ttm [43]=amplitude(%) [44]=total_cap(亿元) [45]=high(冗余) [46]=low(冗余) [49]=volume_ratio
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
                        "total_cap": _parse_tencent_field(raw, 44, None) * 1e8 if _parse_tencent_field(raw, 44, None) else None,  # 亿元→元
                        "main_inflow": None,  # 腾讯基础API不提供主力资金流向
                    })
                except Exception: pass
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
    global beijing_now, beijing_date, beijing_weekday, data_date, prediction_date, pred_yyyymmdd
    for api_url in ['https://timeapi.io/api/time/current/zone?timeZone=Asia/Shanghai',
                     'https://worldtimeapi.org/api/timezone/Asia/Shanghai',
                     'http://worldclockapi.com/api/json/cst/now']:
        try:
            req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=5)
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
            break
        except Exception: continue
    if beijing_now is None:
        beijing_now = datetime.now()
        log_alert("WARNING", "北京时间", "所有API不可达，降级为系统时间(假设Asia/Shanghai)")
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
    h = ["2026-01-01","2026-01-02","2026-02-16","2026-02-17","2026-02-18","2026-02-19","2026-02-20",
         "2026-04-06","2026-05-01","2026-06-19","2026-06-20","2026-06-21","2026-10-01","2026-10-02","2026-10-05","2026-10-06","2026-10-07"]
    # data_date若为节假日，回退到上一个交易日
    if data_date in h:
        dd_dt = datetime.strptime(data_date, '%Y-%m-%d')
        for _ in range(10):
            dd_dt -= timedelta(days=1)
            candidate = dd_dt.strftime('%Y-%m-%d')
            if candidate not in h and dd_dt.weekday() < 5:
                data_date = candidate
                log_alert("INFO", "节假日", f"data_date回退至{data_date}")
                break
    # prediction_date若为节假日，推进到下一个交易日
    if prediction_date in h:
        pd_dt = datetime.strptime(prediction_date, '%Y-%m-%d')
        for _ in range(10):
            pd_dt += timedelta(days=1)
            candidate = pd_dt.strftime('%Y-%m-%d')
            if candidate not in h and pd_dt.weekday() < 5:
                prediction_date = candidate
                log_alert("INFO", "节假日", f"prediction_date推进至{prediction_date}")
                break
    pred_yyyymmdd = prediction_date.replace('-', '')
    log_alert("INFO", "北京时间", f"beijing={beijing_date} data={data_date} pred={prediction_date}")

# ============================================================
# 步骤0A：拉取持仓跟踪
# ============================================================
def step0A_pull_holdings():
    try:
        repo_dir = "/tmp/lv_holdings_pull"
        if os.path.exists(repo_dir): shutil.rmtree(repo_dir, ignore_errors=True)
        repo_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"
        subprocess.run(["git", "clone", "--depth", "1", "--branch", "main", repo_url, repo_dir],
                       capture_output=True, text=True, timeout=30, check=True)
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
    h = ["2026-01-01","2026-01-02","2026-02-16","2026-02-17","2026-02-18","2026-02-19","2026-02-20",
         "2026-04-06","2026-05-01","2026-06-19","2026-06-20","2026-06-21","2026-10-01","2026-10-02","2026-10-05","2026-10-06","2026-10-07"]
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
                resp = urllib.request.urlopen(req, timeout=5)
                text = resp.read().decode('gbk')
                if '=""' in text: all_down = False; break
                chg = float(text.split('"')[1].split(',')[1]) if ',' in text.split('"')[1] else 0
                if chg > -2: all_down = False; break
            except: api_failures += 1; continue  # 单指数失败不中断，继续检查其他
        if api_failures >= 2:
            log_alert("WARNING", "外围市场", f"新浪API {api_failures}/3 不可达，跳过美股检测")
            all_down = False  # 数据不可达时不触发弱市
        if all_down: position_pct = min(position_pct, 30); market_condition = "弱市(美股暴跌)"
    except Exception: pass

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
    except Exception: log_alert("INFO", "大盘代理", "数据不可得，跳过")

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
        hist_file = f"/workspace/推荐历史_{data_date.replace('-', '')}.json"
        safe_write_json(hist_file, safe_read_json(hist_file) + recs)
    return recs

def step4B_sync_holdings_xlsx(holdings):
    try:
        p = "/workspace/持仓跟踪.xlsx"
        if not os.path.exists(p): return
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
            ws.cell(row=row, column=11).value = round(float(h.get('pnl_pct', 0)), 4)
            ws.cell(row=row, column=12).value = data_date; up += 1
        if up: wb.save(p); log_alert("INFO", "持仓跟踪", f"已更新{up}只")
    except Exception as e: log_alert("WARNING", "持仓跟踪", f"{str(e)[:80]}")

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
            if code.startswith("8") and len(str(code)) == 6: triggers.append("北交所")
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
        hf = f"/workspace/推荐历史_{data_date.replace('-', '')}.json"
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
    index_data = idx  # 保存供HTML使用
    if idx:
        sh = idx.get("sh000001", {})
        cur = sh.get("price", 0); chg = sh.get("change_pct", 0)
        log_alert("INFO", "大盘环境", f"上证{cur:.0f} 涨跌{chg:.2f}%")
    # 尝试pytdx获取均线
    try:
        from pytdx.hq import TdxHq_API
        api = TdxHq_API()
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
                    api.disconnect()
                    break
                api.disconnect()
    except Exception: pass
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
                resp = urllib.request.urlopen(req, timeout=5)
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
                            "volume_ratio": None, "main_inflow": None, "total_cap": None,
                        })
                    except Exception: continue
                if i % (batch_size * 10) == 0: time.sleep(0.02)
            except Exception: continue
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
                except Exception: pass
        return stocks, "pytdx"
    except Exception as e:
        log_alert("ERROR", "行情采集", f"三级数据源均不可达")
        raise RuntimeError("行情数据获取失败")
    finally:
        if api is not None:
            try: api.disconnect()
            except Exception: pass

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
    """行业查表：先查硬编码覆盖，再查代码段映射"""
    if code in HARDCODED_INDUSTRY:
        return HARDCODED_INDUSTRY[code]
    ci = int(code)
    for k, v in INDUSTRY_MAP.items():
        lo, hi = k.split('-')
        if int(lo) <= ci <= int(hi): return v
    return "未知"

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
                volumes = [b.get('volume', 0) for b in bars]
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
                        days_listed = (datetime.now() - fd).days
                    except: pass
                kline_data[code] = {
                    'ma5': ma5, 'ma10': ma10, 'ma20': ma20,
                    'dif': dif, 'dea': dea_val, 'macd_hist': macd_hist,
                    'rsi14': rsi14, 'k': k_val, 'd': d_val, 'j': j_val,
                    'boll_upper': boll_upper, 'boll_mid': boll_mid, 'boll_lower': boll_lower,
                    'boll_width': boll_width, 'wr14': wr14, 'obv': obv,
                    'pdi': pdi, 'mdi': mdi, 'adx': adx,
                    'high20': high20, 'low20': low20,
                    'days_listed': days_listed, 'limit_up_days': limit_up_days,
                    'closes': closes, 'highs': highs, 'lows': lows, 'volumes': volumes
                }
            except Exception: kline_data[code] = {}
        try: api.disconnect()
        except: pass
        log_alert("INFO", "K线拉取", f"获取{len(kline_data)}只历史K线(KDJ迭代+BOLL)")
    except Exception as e:
        log_alert("WARNING", "K线拉取", f"pytdx不可用: {str(e)[:60]}")
    return kline_data

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
    """使用F10单股API拉取ROE/净利润，仅对通过硬排除的候选标的"""
    fundamental_data = {}
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://emweb.securities.eastmoney.com/'}
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
            r = json.loads(urllib.request.urlopen(req, timeout=8).read().decode('utf-8-sig'))
            items = r.get('data', [])
            if items:
                latest = items[0]  # 最新一期报告
                fundamental_data[code] = {
                    'roe': latest.get('ROEJQ'),
                    'net_profit': latest.get('PARENTNETPROFIT'),
                    'revenue': latest.get('TOTALOPERATEREVE'),
                    'eps': latest.get('EPSJB'),
                    'report_date': latest.get('REPORT_DATE', ''),
                }
                fetched += 1
        except Exception:
            errors += 1
            continue
        # 每50只短暂休息，避免被限流
        if fetched % 50 == 0 and fetched > 0:
            time.sleep(0.3)
    log_alert("INFO", "财务数据", f"F10基本面: {fetched}只成功, {errors}只失败")
    return fundamental_data

# ============================================================
# 步骤11：硬排除
# ============================================================
def step11_hard_exclude(candidates, all_holdings_codes, kline_data=None, pledge_data=None, goodwill_data=None, unlock_data=None, fundamental_data=None):
    """v6.9.5: 去重PE<0与ROE<0，精简为16项硬排除"""
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
        if code in recent_7d_count and code not in all_holdings_codes:
            c['_recent_7d'] = recent_7d_count[code]
            # v6.6.44: 附带历史策略信息，按日期排序
            c['_recent_7d_strategies'] = recent_7d_strategies.get(code, {})
        if code in all_holdings_codes:
            reason = "当前持仓"
        elif code.startswith('688'): reason = "科创板"
        elif code.startswith('8'): reason = "北交所"
        elif close < 5: reason = f"股价<5元"
        elif close > 100: reason = f"股价>100元"
        elif c.get('name', '').startswith('ST') or c.get('name', '').startswith('*ST'): reason = "ST/*ST"
        elif chg > 7: reason = f"涨幅>7%"
        elif close <= 0: reason = "停牌"
        # v6.8.8: 修复字段名 — pe→pe_ttm, market_cap→total_cap, amount阈值1000→10_000_000
        elif c.get('pe_ttm') is not None and c.get('pe_ttm', 0) < 0: reason = "PE负值(亏损)"
        elif c.get('total_cap') is not None and c.get('total_cap', 0) < 1_000_000_000: reason = "市值<10亿"
        elif c.get('amount') is not None and c.get('amount', 0) < 10_000_000: reason = "成交额<1000万"
        # v6.9.0: 上市天数
        kd = kline_data.get(code, {})
        if not reason and kd.get('days_listed') is not None and kd['days_listed'] < 60:
            reason = "上市不足60天"
        # v6.9.4: 质押比例>50%
        if not reason and code in pledge_data and pledge_data[code] > 50:
            reason = f"质押过高({pledge_data[code]:.0f}%)"
        # v6.9.4: 商誉/净资产>30%
        if not reason and code in goodwill_data:
            gw, na, ratio = goodwill_data[code]
            if ratio > 0.30: reason = f"商誉占比{ratio:.0%}"
        # v6.9.4: 近期大额解禁（解禁比例>10%）
        if not reason and code in unlock_data:
            for ud, ratio in unlock_data[code]:
                if ratio > 10:
                    reason = f"解禁{ratio:.0f}%({ud})"
                    break
        # v6.9.5: PE<0已在上方排除(行1185)，ROE/净利润仅作参考不重复排除
        if reason: er[reason.split('(')[0]] += 1; excluded.append(c)
        else: passed.append(c)
    # 统计7日内推荐数量（仅统计通过且被标注的）
    recent_7d_count = sum(1 for c in passed if c.get('_recent_7d'))
    log_alert("INFO", "硬排除", f"通过{len(passed)}只 排除{len(excluded)}只 7日推荐{recent_7d_count}只")
    return passed, excluded, er

# ============================================================
# 步骤12：信号过滤
# ============================================================
def step12_signal_filter(candidates, kline_data=None, fundamental_data=None):
    """v6.9.15: 恢复fundamental_data参数，新增净利润亏损过滤"""
    if kline_data is None: kline_data = {}
    if fundamental_data is None: fundamental_data = {}
    passed, excluded = [], []
    for c in candidates:
        chg = c.get('change_pct', 0); close = c.get('close', 0); op = c.get('open', 0)
        high = c.get('high', 0); low = c.get('low', 0); amp = c.get('amplitude', 0)
        vr = c.get('volume_ratio'); to = c.get('turnover', 0)
        kd = kline_data.get(c.get('code', ''), {})
        reason = None
        # 1. 假动量：高开>3%后回落超2%
        if op > 0 and c.get('prev_close', op) > 0:
            pc = c.get('prev_close', op)
            if (op - pc) / pc > 0.03 and close < op * 0.98: reason = "假动量"
        # 2. 诱多：冲高>5%后回落至开盘附近
        if not reason and high > 0 and op > 0:
            pc = c.get('prev_close', 0)
            if pc > 0 and (high - pc) / pc > 0.05 and close < op * 1.01: reason = "诱多"
        # 3. 缩量涨停：涨幅>5%+量比<0.5
        if not reason and chg > 5 and vr is not None and vr < 0.5: reason = "缩量涨停"
        # 4. 振幅过大：>15%
        if not reason and amp > 15: reason = f"振幅>{amp:.1f}%"
        # 5. 跌停板边缘：chg<-9%+振幅>12%
        if not reason and chg < -9 and amp > 12: reason = "跌停板异动"
        # 6. 缩量下跌：chg<-3%+量比<0.3
        if not reason and chg < -3 and vr is not None and vr < 0.3: reason = "缩量下跌"
        # 7. 高换手低涨幅：换手>20%+涨跌幅<2%
        if not reason and to > 20 and abs(chg) < 2: reason = "高换手低涨幅"
        # 8. 首阴标记（不排除，仅加分）
        if not reason and -3 < chg < 0 and to > 3: c['_first_yin'] = True
        # 9. 均线空头排列（MA5<MA10<MA20）
        if not reason:
            ma5 = kd.get('ma5', 0); ma10 = kd.get('ma10', 0); ma20 = kd.get('ma20', 0)
            if ma5 > 0 and ma10 > 0 and ma20 > 0 and ma5 < ma10 < ma20:
                reason = "均线空头排列"
        # 10. MACD顶背离
        if not reason:
            high20 = kd.get('high20', 0); dif = kd.get('dif', 0)
            closes_h = kd.get('closes', [])
            if high20 > 0 and dif != 0 and len(closes_h) >= 20:
                difs_list = []
                ema12 = closes_h[0]; ema26 = closes_h[0]
                for pr in closes_h[1:]:
                    ema12 = ema12 * 11/13 + pr * 2/13
                    ema26 = ema26 * 25/27 + pr * 2/27
                    difs_list.append(ema12 - ema26)
                dif_20d_max = max(difs_list[-20:]) if len(difs_list) >= 20 else dif
                if high >= high20 * 0.995 and dif < dif_20d_max * 0.9:
                    reason = "MACD顶背离"
        # 11. RSI超买（RSI(14)>80）
        if not reason:
            rsi14 = kd.get('rsi14', 50)
            if rsi14 > 80: reason = f"RSI超买({rsi14:.0f})"
        # 12. 缩量反弹（v6.9.3: 连续3日量能递减+当日反弹>2%）
        if not reason and chg > 2:
            vols = kd.get('volumes', [])
            if len(vols) >= 4 and vr is not None and vr < 0.6:
                if vols[-4] > vols[-3] > vols[-2] and vols[-1] > 0:
                    reason = "缩量反弹"
        # 13. KDJ高位死叉（v6.9.3: J值>100后下穿K/D线）
        if not reason:
            j_val = kd.get('j', 50); k_val = kd.get('k', 50)
            if j_val > 100 and j_val < k_val:
                reason = f"KDJ死叉(J={j_val:.0f})"
        # 14. 涨停次日高开低走（v6.9.3: 前日涨停+当日高开低走收阴）
        if not reason:
            closes_h = kd.get('closes', []); highs_h = kd.get('highs', [])
            if len(closes_h) >= 3 and closes_h[-2] > 0 and highs_h[-2] > 0:
                yday_chg = (closes_h[-2] - closes_h[-3]) / closes_h[-3] if closes_h[-3] > 0 else 0
                yday_limit = yday_chg >= 0.095 and closes_h[-2] >= highs_h[-2] * 0.98
                if yday_limit and op > 0 and close < op and chg < 0:
                    reason = "涨停次日高开低走"
        # 15. 布林带收窄突破失败（v6.9.3: 带宽<5%+当日放量但收阴）
        if not reason:
            boll_width = kd.get('boll_width', 999)
            if boll_width < 0.05 and vr is not None and vr >= 1.5 and close < op:
                reason = f"布林突破失败(带宽{boll_width:.1%})"
        # 16. 20日涨幅>45%风控（v6.9.5: 防止追高爆炒股）
        if not reason:
            closes_h = kd.get('closes', [])
            if len(closes_h) >= 20 and closes_h[-20] > 0:
                rally_20d_v2 = (close - closes_h[-20]) / closes_h[-20]
                if rally_20d_v2 > 0.45:
                    reason = f"20日涨幅{rally_20d_v2:.0%}>45%"
        # 17. 放量不涨（v6.9.10: 量比>2+涨跌<1%→放量不涨，疑似出货）
        if not reason and vr is not None and vr > 2 and 0 < chg < 1: reason = "放量不涨"
        # 18. 放量滞跌（v6.9.10: 量比>1.5+微跌+收阴→放量滞跌，下跌中继）
        if not reason and vr is not None and vr > 1.5 and -1 < chg < 0 and close < op: reason = "放量滞跌"
        # 19. 高位长上影线（v6.9.11: 涨>5%+上影线>实体2倍→高位抛压）
        if not reason and chg > 5 and high > max(close, op) and low > 0:
            body = abs(close - op); upper_shadow = high - max(close, op)
            if upper_shadow > body * 2 and upper_shadow / close > 0.03:
                reason = "长上影线"
        # 20. 连续缩量（v6.9.11: 量比<0.4+涨跌<1%→无人气横盘）
        if not reason and vr is not None and vr < 0.4 and abs(chg) < 1: reason = "连续缩量"
        # 21. 净利润亏损（v6.9.15: 基于F10单股API数据，排除ROE<0或净利润<0的标的）
        if not reason:
            fd = fundamental_data.get(c.get('code', ''), {})
            roe = fd.get('roe')
            np_val = fd.get('net_profit')
            if roe is not None and np_val is not None:
                try:
                    if float(roe) < 0 or float(np_val) < 0:
                        reason = f"净利润亏损(ROE={float(roe):.1f}%)"
                except (ValueError, TypeError):
                    pass
        if reason: excluded.append(c)
        else: passed.append(c)
    log_alert("INFO", "信号过滤", f"通过{len(passed)}只 排除{len(excluded)}只")
    return passed, excluded

# ============================================================
# 步骤13：十七策略匹配（ABCDEFGHIJKLMNOPQMNO按优先级顺序，v6.9.3新增M/N/O）
# ============================================================
def step13_strategy_match(candidates, kline_data=None):
    if kline_data is None: kline_data = {}
    matched = []
    for c in candidates:
        chg = c.get('change_pct', 0); amp = c.get('amplitude', 0)
        amt = c.get('amount', 0); vr = c.get('volume_ratio'); to = c.get('turnover', 0)
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
        # ── B 超跌反弹（v6.9.5: 收紧，amp>5且有实体内反弹确认）──
        if not s and -9.5 <= chg <= -3:
            if amp > 5 and close > low * 1.02:
                s = "B"; reason = f"超跌反弹:跌{chg:.1f}%+振幅{amp:.1f}%+反弹确认"; score = 7
            elif amp > 8:
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
        # ── D 回调企稳 (v6.9.17: 弱市时上限扩展至7%兜底A策略关闭后的(6,7]区间，但弱市下D权重降低) ──
        if not s and 3 <= chg <= (7 if market_condition == "弱市" else 6):
            if 2 <= amp <= 8 and close > op:
                s = "D"; reason = f"回调企稳:涨{chg:.1f}%+阳线+振幅{amp:.1f}%"; score = 8 if market_condition != "弱市" else 7
        # ── E 资金埋伏 (v6.9.17: 弱市下score-1) ──
        if not s and 0 <= chg <= 1:
            mi = c.get('main_inflow')
            wm_penalty = -1 if market_condition == "弱市" else 0
            if mi is not None and mi > 3000:
                s = "E"; reason = f"资金埋伏:涨{chg:.1f}%+主力流入{mi:.0f}万"; score = 6 + wm_penalty
            elif mi is None and close > op:
                # 代理兜底：阳线+放量或高换手
                if vr is not None and vr >= 0.8 and to is not None and to >= 1.0:
                    s = "E"; reason = f"资金埋伏(代理):涨{chg:.1f}%+量比{vr:.1f}+换手{to:.1f}%"; score = 5 + wm_penalty
                elif vr is None and to is not None and to >= 1.5:
                    s = "E"; reason = f"资金埋伏(代理):涨{chg:.1f}%+换手{to:.1f}%"; score = 4 + wm_penalty
        # ── F 北向资金（v6.9.17: 弱市下score-1）──
        if s == "E":
            mi = c.get('main_inflow')
            wm_penalty = -1 if market_condition == "弱市" else 0
            if mi is not None and mi > 5000:
                # 主力资金>5000万 → 升级为F
                nb_days = 0
                for fname in sorted(os.listdir('/workspace')):
                    if fname.startswith('推荐历史_') and fname.endswith('.json'):
                        for r in safe_read_json(os.path.join('/workspace', fname)):
                            if r.get('code') == c.get('code') and r.get('type') == 'recommendation':
                                rd = r.get('date', '')
                                if rd >= (datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=5)).strftime('%Y-%m-%d'):
                                    nb_days += 1
                                    break
                if nb_days >= 3:
                    s = "F"; reason = f"北向资金:涨{chg:.1f}%+主力流入{mi:.0f}万+持续{nb_days}日"; score = 5 + wm_penalty
            elif mi is None and vr is not None and vr >= 1.0 and to is not None and to >= 2.0:
                # 代理兜底：量比≥1.0+换手≥2% → 资金活跃度升级
                nb_days = 0
                for fname in sorted(os.listdir('/workspace')):
                    if fname.startswith('推荐历史_') and fname.endswith('.json'):
                        for r in safe_read_json(os.path.join('/workspace', fname)):
                            if r.get('code') == c.get('code') and r.get('type') == 'recommendation':
                                rd = r.get('date', '')
                                if rd >= (datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=5)).strftime('%Y-%m-%d'):
                                    nb_days += 1
                                    break
                if nb_days >= 2:
                    s = "F"; reason = f"北向资金(代理):涨{chg:.1f}%+量比{vr:.1f}+换手{to:.1f}%+持续{nb_days}日"; score = 4 + wm_penalty
        # ── G 横盘突破 (v6.9.11: chg 1.0-4.0%, vr≥1.2) ──
        if not s and 1.0 <= chg < 4.0 and close > op:
            if amp is not None and 1.5 <= amp <= 6:
                if vr is not None and vr >= 1.2:
                    s = "G"; reason = f"横盘突破:涨{chg:.1f}%+振幅{amp:.1f}%+量比{vr:.1f}"; score = 8
                elif vr is None and to is not None and to >= 3:
                    s = "G"; reason = f"横盘突破(代理):涨{chg:.1f}%+振幅{amp:.1f}%+换手{to:.1f}%"; score = 7
        # ── H 地量见底 (v6.9.10: vr<0.85, body/close<0.008) ──
        if not s and -3 <= chg < 0 and close >= op:
            is_hammer = False
            if high > low and low > 0:
                body = abs(close - op)
                lower_shadow = min(close, op) - low
                min_shadow = max(body * 1.5, 0.001 * close)  # body=0时至少0.1%影线
                if lower_shadow >= min_shadow:
                    is_hammer = True
            vr_ok = (vr is not None and vr < 0.85) or (vr is None and to is not None and to < 0.85)
            if vr_ok and (is_hammer or (close > 0 and body / close < 0.008)):
                s = "H"; reason = f"地量见底:{chg:+.1f}%+量比{vr or 0:.1f}+锤子线"; score = 5
        # ── I 均线粘合突破（v6.9.17: 放宽粘合<3%+vr≥1.2）──
        if not s:
            kd = kline_data.get(c.get('code', ''), {})
            ma5 = kd.get('ma5', 0); ma10 = kd.get('ma10', 0); ma20 = kd.get('ma20', 0)
            if ma5 > 0 and ma10 > 0 and ma20 > 0:
                ma_max = max(ma5, ma10, ma20); ma_min = min(ma5, ma10, ma20)
                convergence = (ma_max - ma_min) / ma_min if ma_min > 0 else 999
                if convergence < 0.03 and close >= ma_max * 0.99 and close > op:
                    if vr is not None and vr >= 1.2:
                        s = "I"; reason = f"均线粘合突破:价{close:.2f}>均线+量比{vr:.1f}(粘合{convergence:.1%})"; score = 9
                    elif vr is None and to is not None and to >= 3:
                        s = "I"; reason = f"均线粘合突破(代理):价{close:.2f}>均线+换手{to:.1f}%"; score = 8
        # ── J 龙回头（v6.9.17: 放宽vr<0.85）──
        if not s:
            kd = kline_data.get(c.get('code', ''), {})
            closes_h = kd.get('closes', [])
            highs_h = kd.get('highs', [])
            if len(closes_h) >= 20 and close > 0:
                max20 = max(highs_h[-20:])
                min20 = min(lows_h[-20:]) if kd.get('lows') else 0
                rally_20d = (max20 - closes_h[-20]) / closes_h[-20] if closes_h[-20] > 0 else 0
                pullback = (max20 - close) / max20 if max20 > 0 else 0
                if rally_20d > 0.05 and 0.08 <= pullback <= 0.22:
                    if vr is not None and vr < 0.85 and close >= op:
                        s = "J"; reason = f"龙回头:涨{rally_20d:.1%}→回调{pullback:.1%}+缩量+收阳"; score = 8
        # ── K 缺口回补（v6.9.17: 放宽缺口1-7%）──
        if not s:
            kd = kline_data.get(c.get('code', ''), {})
            closes_h = kd.get('closes', [])
            highs_h = kd.get('highs', [])
            if len(closes_h) >= 3 and close > 0 and op > 0:
                yest_close = closes_h[-2] if len(closes_h) >= 2 else 0
                yest_high = highs_h[-2] if len(highs_h) >= 2 else 0
                if yest_close > 0 and yest_high > 0:
                    gap_up = op > yest_high * 1.01  # 跳空高开>1%
                    gap_size = (op - yest_high) / yest_high if yest_high > 0 else 0
                    if gap_up and 0.01 <= gap_size <= 0.07:
                        # 回踩缺口上沿: low触达yest_high下方(±0.5%)才算真正回补
                        if low <= yest_high * 0.995 and close >= op:
                            s = "K"; reason = f"缺口回补:跳空{gap_size:.1%}→回踩确认+收阳"; score = 8
        # ── L 黄金坑（v6.9.10: 跌≥6%→反弹≥3%）──
        if not s:
            kd = kline_data.get(c.get('code', ''), {})
            closes_h = kd.get('closes', [])
            if len(closes_h) >= 6 and close > 0:
                pre5 = closes_h[-6] if len(closes_h) >= 6 else closes_h[-1]
                min5 = min(closes_h[-5:]) if len(closes_h) >= 5 else close
                if pre5 > 0 and min5 > 0:
                    drop = (min5 - pre5) / pre5  # 5日内最大跌幅（负值）
                    rebound = (close - min5) / min5  # 从最低点反弹
                    if drop <= -0.06 and rebound >= 0.03 and close > op:
                        # 急跌≥6%+反弹≥3%+收阳
                        vr_ok = (vr is not None and vr >= 1.2) or (to is not None and to >= 3)
                        if vr_ok:
                            s = "L"; reason = f"黄金坑:跌{abs(drop):.1%}→反弹{rebound:.1%}+放量+收阳"; score = 9
        # ── M 涨停回调（v6.9.10: pullback 5-20%, vr<0.85）──
        if not s:
            kd = kline_data.get(c.get('code', ''), {})
            if kd.get('limit_up_days', 0) >= 1 and close > 0:
                closes_h = kd.get('closes', [])
                highs_h = kd.get('highs', [])
                for i in range(len(closes_h) - 2, max(0, len(closes_h) - 7), -1):
                    if i > 0 and closes_h[i-1] > 0 and highs_h[i] > 0:
                        day_chg = (closes_h[i] - closes_h[i-1]) / closes_h[i-1]
                        if day_chg >= 0.095 and closes_h[i] >= highs_h[i] * 0.98:
                            limit_price = closes_h[i]
                            pullback_pct = (limit_price - close) / limit_price if limit_price > 0 else 0
                            if 0.05 <= pullback_pct <= 0.20 and close >= op:
                                if vr is not None and vr < 0.85:
                                    s = "M"; reason = f"涨停回调:涨停{day_chg:.1%}→回调{pullback_pct:.1%}+缩量+收阳"; score = 7
                                    break
        # ── N 新高突破（v6.9.17: 放宽vr≥1.2）──
        if not s:
            kd = kline_data.get(c.get('code', ''), {})
            high20 = kd.get('high20', 0)
            if high20 > 0 and close >= high20 * 0.995 and close > op:
                if vr is not None and vr >= 1.2:
                    s = "N"; reason = f"新高突破:价{close:.2f}=20日高+量比{vr:.1f}+阳线"; score = 9
                elif vr is None and to is not None and to >= 3:
                    s = "N"; reason = f"新高突破(代理):价{close:.2f}=20日高+换手{to:.1f}%"; score = 8
        # ── O 强势股回踩均线（v6.9.3: 60日涨幅>20%+回踩MA20±1%+缩量收阳）──
        if not s:
            kd = kline_data.get(c.get('code', ''), {})
            closes_h = kd.get('closes', [])
            ma20 = kd.get('ma20', 0)
            if len(closes_h) >= 60 and ma20 > 0 and close > 0:
                rally_60d = (close - closes_h[-60]) / closes_h[-60] if closes_h[-60] > 0 else 0
                dist_to_ma20 = (close - ma20) / ma20  # 距MA20的距离
                if rally_60d > 0.20 and -0.02 <= dist_to_ma20 <= 0.02 and close >= op:
                    if vr is not None and vr < 0.85:
                        s = "O"; reason = f"回踩均线:涨{rally_60d:.1%}→回踩MA20({dist_to_ma20:+.1%})+缩量+收阳"; score = 8
        # ── P 地量反弹（v6.9.17: 放宽vr≥1.5+chg≥1.5%）──
        if not s:
            kd = kline_data.get(c.get('code', ''), {})
            vols = kd.get('volumes', [])
            if len(vols) >= 4 and vr is not None:
                if vols[-4] > vols[-3] > vols[-2] and vr >= 1.5 and 1.5 <= chg <= 5 and close > op:
                    s = "P"; reason = f"地量反弹:3日缩量+放量{vr:.1f}x+涨{chg:.1f}%"; score = 7
        # ── Q W底形态（v6.9.17: 放宽vr≥1.2+颈线突破≥0.5%）──
        if not s:
            kd = kline_data.get(c.get('code', ''), {})
            closes_h = kd.get('closes', []); lows_h = kd.get('lows', [])
            if len(closes_h) >= 20 and close > 0:
                # 找两个底: 前10日最低点和后10日最低点
                l1 = min(lows_h[-20:-10]) if len(lows_h) >= 20 else 0
                l2 = min(lows_h[-10:]) if len(lows_h) >= 10 else 0
                if l1 > 0 and l2 > 0 and 0.95 < l2 / l1 < 1.05:  # 两底相差<5%
                    # 颈线: 两底之间最高点（取整个20日区间，因为两底横跨前后10日）
                    neck = max(highs_h[-20:]) if len(highs_h) >= 20 else 0
                    if neck > 0 and close > neck * 1.005 and close > op:
                        if vr is not None and vr >= 1.2:
                            s = "Q"; reason = f"W底突破:两底{l1:.2f}/{l2:.2f}+突破颈线{neck:.2f}+放量"; score = 9
        if s: c['strategy'] = s; c['score'] = score; matched.append(c)
    log_alert("INFO", "策略匹配", f"匹配{len(matched)}只")
    return matched

# ============================================================
# 步骤14-17：评分+行业限制
# ============================================================
def step14_scoring(candidates):
    # v6.9.10: 先计算_tie_score（原在step16），再融入最终score
    so = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4, 'F': 5, 'G': 6, 'H': 7, 'I': 8, 'J': 9, 'K': 10, 'L': 11, 'M': 12, 'N': 13, 'O': 14, 'P': 15, 'Q': 16}
    sector_ad = defaultdict(list)
    for c in candidates:
        if c.get('strategy') in ('A', 'D', 'G', 'I', 'K', 'N'):
            sector_ad[c.get('industry', '')].append(c)
    sector_bonus = {}
    for ind, clist in sector_ad.items():
        if len(clist) >= 3:
            for c in clist:
                sector_bonus[c.get('code', '')] = 0.10
    for c in candidates:
        vr = c.get('volume_ratio') or 0; to = c.get('turnover') or 0; chg = c.get('change_pct') or 0
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
        elif s == 'G': cs = max(0, 1.0 - abs(chg - 2.5) / 1.0)
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
        c['_tie_score'] = max(0, vs * 0.30 + ts * 0.30 + cs * 0.30 + (1.0 - so.get(s, 99) / 10.0) * 0.10 + sector_bonus.get(code, 0) + ma_bonus)
        # 融入最终score
        sc = c.get('score', 0) * 2
        sc += round(c['_tie_score'] * 5)  # v6.9.11: _tie_score 0~1 → 0~5分浮动，扩大区分度
        if c.get('_first_yin'): sc += 2
        c['score'] = max(0, sc)
        if c['score'] >= 18: c['confidence'] = '★★★'
        elif c['score'] >= 12: c['confidence'] = '★★'
        else: c['confidence'] = '★'
    return candidates

def step17_industry_limit(candidates):
    # v6.6.46: 保留 step16 综合评分排序(_tie_score)，五级二次评估打破平局
    so = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4, 'F': 5, 'G': 6, 'H': 7, 'I': 8, 'J': 9, 'K': 10, 'L': 11, 'M': 12, 'N': 13, 'O': 14, 'P': 15, 'Q': 16}
    def _tie_key(c):
        vr = c.get('volume_ratio') or 0
        to = c.get('turnover') or 0
        to_penalty = abs(to - 10) if to > 0 else 99
        return (-c.get('score', 0), so.get(c.get('strategy', 'Z'), 99), -(c.get('_tie_score', 0)),
                -vr, to_penalty)
    ig = defaultdict(list)
    for c in candidates: ig[c.get('industry', '未知')].append(c)
    limited = []
    elastic_added = 0
    for g in ig.values():
        g.sort(key=_tie_key)
        limited.extend(g[:3])
        # v6.9.10: 弹性规则 — 第4只_tie_score≥第3只95%则保留
        if len(g) >= 4:
            t3 = g[2].get('_tie_score', 0)
            t4 = g[3].get('_tie_score', 0)
            if t3 > 0 and t4 / t3 >= 0.95:
                limited.append(g[3])
                elastic_added += 1
    max_s = max(1, len(limited) * params.get('strategy_concentration_pct', 30) // 100)
    sg = defaultdict(list)
    for c in limited: sg[c.get('strategy', 'Z')].append(c)
    final = []
    for g in sg.values():
        g.sort(key=_tie_key)
        final.extend(g[:max_s])
    final.sort(key=lambda c: (so.get(c.get('strategy', 'Z'), 99), -c.get('score', 0)))
    log_alert("INFO", "行业限制", f"通过{len(final)}只 (原始{len(candidates)}只, 弹性+{elastic_added})")
    return final

def step18_news_screening(candidates):
    """步骤18：新闻筛查 — 对最终标的检测近5日利空新闻（v6.8.3: 假阳性过滤+东方财富优先）"""
    if not candidates:
        return candidates, 0
    
    NEGATIVE_KW = [
        '立案调查', '行政处罚', '监管函', '问询函', '业绩修正', '预亏', '预减',
        '大股东减持', '控股股东减持', '质押平仓', '商誉减值', '退市风险',
        '重大诉讼', '债务违约', '暂停上市', '终止上市', '限售股解禁',
        '业绩变脸', '财务造假', '信披违规', '内幕交易', '操纵市场',
        '强制退市', '破产重整', '资不抵债', '审计非标',
        '违规担保', '资金占用', '重组失败', '定增终止', 'ST warning',
        '净利润下滑', '营收下滑', '毛利率下滑', '评级下调', '目标价下调',  # v6.9.1
        '应收账款', '坏账计提', '存货跌价', '资产减值', '内控缺陷', '证监会立案', '通报批评'  # v6.9.4
    ]
    # 假阳性否定词：匹配到关键词但上下文中包含这些词时忽略
    FALSE_POSITIVE_NEGATORS = [
        '终止减持', '不减持', '解除质押', '回复', '整改完成', '撤销',
        '上调', '大幅增长', '扭亏', '摘帽', '恢复正常', '已消除',
        '不立案', '不处罚', '不予', '驳回', '和解', '撤回',
        '募集资金', '增持', '回购', '承诺不',
        '减持完毕', '解除异常', '无违规'  # v6.9.3
    ]
    
    excluded = []
    passed = []
    # v6.8.8: 仅对评分前20只做个体搜索，保持原始策略优先级排序
    search_limit = min(20, len(candidates))
    top20_codes = {c['code'] for c in sorted(candidates, key=lambda c: -c.get('score', 0))[:search_limit]}
    
    for c in candidates:
        if c.get('code', '') not in top20_codes:
            passed.append(c)
            continue
        
        code = c.get('code', '')
        name = c.get('name', '')
        has_neg = False
        neg_reason = ''
        
        # 方式1: 东方财富新闻API（优先，标题匹配更精准）
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
                        if kw not in title:
                            continue
                        # 假阳性过滤：检查否定词
                        is_false_positive = any(neg in title for neg in FALSE_POSITIVE_NEGATORS)
                        if not is_false_positive:
                            has_neg = True
                            neg_reason = kw
                            break
                    if has_neg:
                        break
        except Exception:
            pass
        
        # 方式2: Bing搜索（备用，带假阳性过滤）
        if not has_neg:
            try:
                query = f'{name} {code} 利空 公告'
                url = f'https://www.bing.com/search?q={urllib.parse.quote(query)}'
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    'Accept-Language': 'zh-CN,zh;q=0.9'
                })
                with urllib.request.urlopen(req, timeout=4) as resp:
                    html = resp.read().decode('utf-8', errors='ignore')
                    for kw in NEGATIVE_KW:
                        if kw not in html:
                            continue
                        is_false_positive = any(neg in html for neg in FALSE_POSITIVE_NEGATORS)
                        if not is_false_positive:
                            has_neg = True
                            neg_reason = kw
                            break
            except Exception:
                pass
        
        if has_neg:
            c['_news_reason'] = neg_reason
            excluded.append(c)
        else:
            passed.append(c)
    
    nex = len(excluded)
    if nex > 0:
        details = ", ".join(f"{c.get('name','')}({c.get('_news_reason','?')})" for c in excluded[:5])
        if nex > 5:
            details += f" 等{nex}只"
        log_alert("WARNING", "新闻筛查", f"排除{nex}只: {details}")
    else:
        log_alert("INFO", "新闻筛查", "全部通过，未发现利空")
    
    return passed, nex

def step16_comprehensive_score(candidates):
    # v6.9.10: _tie_score已在step14计算并融入score，step16仅负责排序
    so = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4, 'F': 5, 'G': 6, 'H': 7, 'I': 8, 'J': 9, 'K': 10, 'L': 11, 'M': 12, 'N': 13, 'O': 14, 'P': 15, 'Q': 16}
    def _tie_key(c):
        vr = c.get('volume_ratio') or 0
        to = c.get('turnover') or 0
        to_penalty = abs(to - 10) if to > 0 else 99
        return (-c.get('score', 0), so.get(c.get('strategy', 'Z'), 99), -(c.get('_tie_score', 0)),
                -vr, to_penalty)
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
        # 优先参考当日低点作为支撑，在低点和收盘价之间取合理位置
        if low > 0 and close > low:
            # 若尾盘有回升迹象(收盘在低点上方>1%)，在低点上方1%进场
            if close > low * 1.01 and pos > 0.3:
                entry = low + (close - low) * 0.3  # 低点上方30%分位
            elif chg < -5:
                # 深度超跌，次日可能惯性低开，在收盘价-1%挂单
                entry = close * (1 - atr_pct * 0.3)
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
        # 横盘突破(v6.7.0): 放量突破平台，次日大概率高开惯性冲高
        # 在收盘价上方0.5%挂单，追涨为主
        if high > close and close > 0:
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

# ============================================================
# 步骤20：Markdown输出
# ============================================================
def step20_output_markdown(candidates, total_raw, ae, asig, astr, aind, anew, er):
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
        f"| ②硬排除 | {ae} | {total_raw - ae} | 14项(持仓/科创/北交/低价/高价/ST/涨幅/停牌/PE/市值/成交额/上市天数/质押商誉解禁已废弃) |",
        f"| ③信号过滤 | {asig} | {ae - asig} | 21项(假动量/诱多/缩量涨停/振幅/跌停异动/缩量下跌/高换手低涨幅/首阴/均线空头/MACD顶背离/RSI超买/缩量反弹/KDJ死叉/涨停次日高开低走/布林突破失败/20日涨幅>45%/放量不涨/放量滞跌/长上影线/连续缩量/净利润亏损) |",
        f"| ④策略匹配 | {astr} | {asig - astr} | ABCDEFGHIJKLMNOPQ十七策略 |",
        f"| ⑤行业+同策略限制 | {aind} | {astr - aind} | 同行业≤3只+同策略≤30% |",
        f"| ⑥新闻筛查 | {aind - anew} | {anew} | Bing/东方财富双源利空检测 |",
        f"| ★最终推荐 | {len(candidates)} | {aind - anew - len(candidates)} | 评分门控+降级 |", "",
    ]
    if candidates:
        lines.append("## 推荐标的\n")
        lines.append("| # | 策略 | 标的 | 代码 | 行业 | 涨跌幅 | 开盘 | 收盘 | 振幅 | 7日 | 评分 | 置信 | 进场 | 止损 | 止盈 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for idx, c in enumerate(candidates, 1):
            code = c.get('code', ''); name = c.get('name', '')
            s = c.get('strategy', '?'); ind = c.get('industry', '未知')
            chg = c.get('change_pct', 0); op = c.get('open', 0) or 0
            close = c.get('close', 0) or 0; amp = c.get('amplitude', 0) or 0
            score = c.get('score', 0); conf = c.get('confidence', '★')
            chg_e = "🔴" if chg >= 0 else "🟢"
            entry = calc_entry_price(c)
            sl = round(entry * 0.96, 2); tp = round(entry * 1.05, 2)
            r7d = str(c.get('_recent_7d')) if c.get('_recent_7d') else ""
            # v6.6.44: 7日列附带历史策略标注
            r7s = c.get('_recent_7d_strategies', {})
            if r7d and r7s:
                # 按日期排序，取策略列表（去重）如 "1(A,B)" 表示1天前，策略A/B
                sorted_dates = sorted(r7s.keys())
                strats = [r7s[d] for d in sorted_dates]
                # 去重并保持顺序
                seen = set(); uniq_s = []
                for s_ in strats:
                    if s_ not in seen: seen.add(s_); uniq_s.append(s_)
                r7d = f"{r7d} ({','.join(uniq_s)})"
            url = f"https://quote.eastmoney.com/sh{code}.html" if code.startswith('6') else f"https://quote.eastmoney.com/sz{code}.html"
            lines.append(f"| {idx} | {s} | [{name}]({url}) | {code} | {ind} | {chg_e}{chg:+.2f}% | {op:.2f} | {close:.2f} | {amp:.2f}% | {r7d} | {score} | {conf} | {entry:.2f} | {sl:.2f} | {tp:.2f} |")
    sd = Counter(c.get('strategy') for c in candidates)
    sn = {'A': '动量延续', 'B': '超跌反弹', 'C': '事件驱动', 'D': '回调企稳', 'E': '资金埋伏', 'F': '北向资金', 'G': '横盘突破', 'H': '地量见底', 'I': '均线突破', 'J': '龙回头', 'K': '缺口回补', 'L': '黄金坑', 'M': '涨停回调', 'N': '新高突破', 'O': '回踩均线', 'P': '地量反弹', 'Q': 'W底突破'}
    lines.append("\n## 策略分布")
    for s in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q']:
        if sd.get(s, 0) > 0: lines.append(f"- {s} {sn.get(s, '')}: {sd[s]}只")
    lines.append("\n## 硬排除 TOP5")
    for r, cnt in er.most_common(5): lines.append(f"- {r}: {cnt}只")
    lines.append(f"\n\n> ⚠️ 免责声明：本报告仅供研究参考，不构成任何投资建议。\n> 版本: {file_version} | 生成: {beijing_date}")
    with open(mp, 'w', encoding='utf-8') as f: f.write('\n'.join(lines))
    log_alert("INFO", "Markdown", f"已输出至 {mp}")
    return mp

# ============================================================
# 步骤20B：HTML报告（v6.6.27 含指数行情）
# ============================================================
def step20B_generate_html(candidates, total_raw, ae, asig, astr, aind, anew, er, crisis_alerts):
    hd = f"/workspace/ashare-screening-{pred_yyyymmdd}"
    os.makedirs(hd, exist_ok=True)
    hp = f"{hd}/ashare-screening-{pred_yyyymmdd}.html"
    sd = Counter(c.get('strategy') for c in candidates)
    sn = {'A': '动量延续', 'B': '超跌反弹', 'C': '事件驱动', 'D': '回调企稳', 'E': '资金埋伏', 'F': '北向资金', 'G': '横盘突破', 'H': '地量见底', 'I': '均线突破', 'J': '龙回头', 'K': '缺口回补', 'L': '黄金坑', 'M': '涨停回调', 'N': '新高突破', 'O': '回踩均线', 'P': '地量反弹', 'Q': 'W底突破'}
    sc = {'A': '#22c55e', 'B': '#3b82f6', 'C': '#8b5cf6', 'D': '#f59e0b', 'E': '#ec4899', 'F': '#06b6d4', 'G': '#10b981', 'H': '#f97316', 'I': '#14b8a6', 'J': '#ef4444', 'K': '#a855f7', 'L': '#eab308', 'M': '#f472b6', 'N': '#84cc16', 'O': '#38bdf8', 'P': '#fb923c', 'Q': '#22d3ee'}
    fc = len(candidates)
    
    # 指数卡片HTML
    idx_names = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指"}
    index_cards = ""
    for code, name in idx_names.items():
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
    for idx, c in enumerate(candidates, 1):
        code = c.get('code', ''); name = c.get('name', ''); s = c.get('strategy', '?')
        ind = c.get('industry', '未知'); chg = c.get('change_pct', 0)
        op = c.get('open', 0) or 0; close = c.get('close', 0) or 0
        amp = c.get('amplitude', 0) or 0; score = c.get('score', 0); conf = c.get('confidence', '★')
        entry = calc_entry_price(c)
        sl = round(entry * 0.96, 2); tp = round(entry * 1.05, 2)
        r7d_html = str(c.get('_recent_7d')) if c.get('_recent_7d') else ""
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
        r7_cls = "recent-7d" if c.get('_recent_7d') else ""
        url = f"https://quote.eastmoney.com/sh{code}.html" if code.startswith('6') else f"https://quote.eastmoney.com/sz{code}.html"
        rows_html += f"""<tr class="{scl} {r7_cls}"><td>{idx}</td><td><span class="badge {scl}">{s}</span></td>
        <td><a href="{url}" target="_blank">{html.escape(name)}</a></td><td>{code}</td><td>{ind}</td>
        <td class="{chg_cls}">{chg:+.2f}%</td><td>{op:.2f}</td><td>{close:.2f}</td>
        <td>{amp:.2f}%</td><td>{r7d_html}</td><td>{score}</td><td class="conf {conf_cls}">{conf}</td>
        <td class="entry">{entry:.2f}</td><td>{sl:.2f}</td><td>{tp:.2f}</td></tr>"""
    
    seg_html = ""; legend_html = ""
    total_m = sum(sd.values())
    if total_m > 0:
        for s in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q']:
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
    
    stages = [("原始标的池", total_raw), ("硬排除(14项)", ae), ("信号过滤(21项)", asig),
              ("策略匹配(17策略)", astr), ("行业+同策略限制", aind), ("新闻筛查", aind - anew), ("最终推荐", fc)]
    max_f = max(s[1] for s in stages)
    funnel_html = ""
    for i, (name, count) in enumerate(stages):
        w = max(12, int(count / max(max_f, 1) * 100))
        cls = "funnel-last" if i == len(stages) - 1 else ""
        funnel_html += f'<div class="funnel-step {cls}" style="width:{w}%">{name}: {count}只</div>'
    
    strat_bars = ""
    for s in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J']:
        cnt = sd.get(s, 0)
        bp = cnt / max(max(sd.values()), 1) * 100
        strat_bars += f'<div class="bar-row"><div class="bar-label">{s} {sn.get(s, "")}</div><div class="bar-track"><div class="bar-fill" style="width:{bp}%;background:{sc[s]}">{cnt}</div></div></div>'
    
    alerts_html = ""
    if crisis_alerts:
        for a in crisis_alerts:
            alerts_html += f'<div class="alert-item"><span class="alert-level warning">WARNING</span><span class="alert-msg">{a}</span></div>'
    else:
        alerts_html = '<div class="alert-item"><span class="alert-level info">INFO</span><span class="alert-msg">今日无异常告警</span></div>'
    
    html_content = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>A股短线标的筛选 — {prediction_date}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}body{{font-family:'Noto Sans CJK SC','WenQuanYi Micro Hei',sans-serif;background:#0f172a;color:#e2e8f0;line-height:1.6}}
.header{{background:linear-gradient(135deg,#1e3a5f 0%,#0f2744 100%);padding:2rem;text-align:center}}
.header h1{{font-size:clamp(1.2rem,2.5vw,1.8rem);color:#f0f9ff}}.header .sub{{color:#94a3b8;font-size:.9rem;margin-top:.3rem}}
.container{{max-width:1200px;margin:0 auto;padding:1rem}}
.meta-row{{display:flex;flex-wrap:wrap;gap:.8rem;justify-content:center;margin:1rem 0}}
.meta-card{{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:.6rem 1.2rem;text-align:center;min-width:100px}}
.meta-card .label{{font-size:.7rem;color:#94a3b8}}.meta-card .value{{font-size:1.1rem;font-weight:bold;color:#38bdf8}}
.index-row{{display:flex;flex-wrap:wrap;gap:1rem;justify-content:center;margin:1rem 0}}
.index-card{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:1rem 1.5rem;text-align:center;min-width:140px;flex:1}}
.index-card .idx-name{{font-size:.85rem;color:#cbd5e1}}.index-card .idx-price{{font-size:1.5rem;font-weight:bold;color:#f8fafc}}
.index-card .idx-chg{{font-size:.9rem;font-weight:bold}}
.index-card .idx-amt{{font-size:1.1rem;display:block}} .index-card .idx-pct{{font-size:.75rem;color:#94a3b8;font-weight:normal}}
.up{{color:#ef4444}}.down{{color:#22c55e}}
section{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:1.5rem;margin:1.2rem 0}}
section h2{{font-size:1.2rem;color:#38bdf8;margin-bottom:1rem;border-bottom:2px solid #334155;padding-bottom:.5rem}}
.funnel{{display:flex;flex-direction:column;align-items:center;gap:.3rem}}
.funnel-step{{background:linear-gradient(90deg,#6366f1,#8b5cf6);color:#fff;text-align:center;padding:.5rem;border-radius:6px;font-size:.8rem}}
.funnel-last{{background:linear-gradient(90deg,#3b82f6,#06b6d4);border:2px solid #38bdf8;font-weight:bold}}
.seg-bar{{display:flex;height:32px;border-radius:6px;overflow:hidden;margin:.5rem 0}}
.seg{{display:flex;align-items:center;justify-content:center;color:#fff;font-weight:bold;font-size:.8rem}}
.legend{{display:flex;flex-wrap:wrap;gap:1rem;margin:.5rem 0;font-size:.8rem}}
.legend-item{{display:flex;align-items:center;gap:.3rem}}
.legend-dot{{width:12px;height:12px;border-radius:3px;display:inline-block}}
.bar-row{{display:flex;align-items:center;margin:.4rem 0;gap:.5rem}}
.bar-label{{width:200px;font-size:.8rem;color:#cbd5e1;text-align:right;flex-shrink:0}}
.bar-track{{flex:1;background:#334155;border-radius:4px;height:24px;overflow:hidden}}
.bar-fill{{height:100%;border-radius:4px;display:flex;align-items:center;justify-content:flex-end;padding:0 .5rem;color:#fff;font-size:.75rem;font-weight:bold;min-width:30px}}
.chart-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem}}
table{{width:100%;border-collapse:collapse;font-size:.8rem}}
th{{background:#334155;padding:.5rem;text-align:left;color:#38bdf8;position:sticky;top:0;white-space:nowrap}}
td{{padding:.4rem .5rem;border-bottom:1px solid #334155;white-space:nowrap}}
tr:hover{{background:#2d3b4f}}
.badge{{padding:2px 8px;border-radius:4px;font-size:.7rem;font-weight:bold}}
.strat_a{{background:#14532d;color:#22c55e}}.strat_b{{background:#1e3a5f;color:#3b82f6}}.strat_c{{background:#3b1f6e;color:#8b5cf6}}
.strat_d{{background:#5c3d0e;color:#f59e0b}}.strat_e{{background:#5c1648;color:#ec4899}}.strat_f{{background:#0f4c5c;color:#06b6d4}}.strat_g{{background:#0e4c3d;color:#10b981}}.strat_h{{background:#4c1d0e;color:#f97316}}.strat_i{{background:#0e3d3d;color:#14b8a6}}.strat_j{{background:#5c1515;color:#ef4444}}.strat_k{{background:#3b1f3b;color:#a855f7}}.strat_l{{background:#4c3d0e;color:#eab308}}.strat_m{{background:#4c1d3b;color:#f472b6}}.strat_n{{background:#1e3d0e;color:#84cc16}}.strat_o{{background:#0e2e4c;color:#38bdf8}}.strat_p{{background:#4c2e0e;color:#fb923c}}.strat_q{{background:#0e3e4c;color:#22d3ee}}
tr.strat_a{{background:rgba(34,197,94,0.05)}}tr.strat_b{{background:rgba(59,130,246,0.05)}}tr.strat_c{{background:rgba(139,92,246,0.05)}}
tr.strat_d{{background:rgba(245,158,11,0.05)}}tr.strat_e{{background:rgba(236,72,153,0.05)}}tr.strat_f{{background:rgba(6,182,212,0.05)}}tr.strat_g{{background:rgba(16,185,129,0.05)}}tr.strat_h{{background:rgba(249,115,22,0.05)}}tr.strat_i{{background:rgba(20,184,166,0.05)}}tr.strat_j{{background:rgba(239,68,68,0.05)}}tr.strat_k{{background:rgba(168,85,247,0.05)}}tr.strat_l{{background:rgba(234,179,8,0.05)}}tr.strat_m{{background:rgba(244,114,182,0.05)}}tr.strat_n{{background:rgba(132,204,22,0.05)}}tr.strat_o{{background:rgba(56,189,248,0.05)}}tr.strat_p{{background:rgba(251,146,60,0.05)}}tr.strat_q{{background:rgba(34,211,238,0.05)}}
tr.recent-7d{{background:rgba(251,146,60,0.12)}} tr.recent-7d:hover{{background:rgba(251,146,60,0.2)}}
.conf{{font-weight:bold}}.conf.high{{color:#22c55e}}.conf.mid{{color:#f59e0b}}.conf.low{{color:#ef4444}}
.entry{{color:#38bdf8;font-weight:bold}}
.alert-item{{display:flex;gap:.8rem;padding:.4rem 0;border-bottom:1px solid #334155;font-size:.8rem}}
.alert-level{{padding:2px 10px;border-radius:4px;font-weight:bold;font-size:.7rem;white-space:nowrap}}
.alert-level.warning{{background:#5c3d0e;color:#f59e0b}}.alert-level.info{{background:#1e3a5f;color:#3b82f6}}
.footer{{text-align:center;padding:2rem;color:#64748b;font-size:.8rem}}
.footer .disclaimer{{color:#ef4444;font-weight:bold;margin-top:.5rem}}
a{{color:#38bdf8;text-decoration:none}}a:hover{{text-decoration:underline}}
@media(max-width:768px){{.chart-grid{{grid-template-columns:1fr}}.container{{padding:.5rem}}th,td{{font-size:.7rem;padding:.3rem}}}}
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
<div><h3 style="font-size:.9rem;color:#cbd5e1;margin-bottom:.5rem">概述</h3><div style="font-size:.8rem;color:#cbd5e1">全市场→{total_raw}只入围→{ae}只通过硬排除→{asig}只通过信号过滤→{astr}只匹配策略→{aind}只通过行业限制→{aind - anew}只通过新闻筛查→<strong style="color:#38bdf8">最终{fc}只</strong></div></div>
</div></section>
<section><h2>系统告警</h2><div class="alert-list">{alerts_html}</div></section>
<section><h2>最终推荐标的</h2><div style="overflow-x:auto"><table>
<thead><tr><th>#</th><th>策略</th><th>标的</th><th>代码</th><th>行业</th><th>涨跌幅</th><th>开盘</th><th>收盘</th><th>振幅</th><th>7日</th><th>评分</th><th>置信</th><th>进场</th><th>止损</th><th>止盈</th></tr></thead>
<tbody>{rows_html if rows_html else '<tr><td colspan="15" style="text-align:center;color:#94a3b8;padding:2rem">无合适标的</td></tr>'}</tbody></table></div></section>
<section><h2>策略说明</h2><table>
<thead><tr><th style="width:18%">策略</th><th style="width:48%">条件</th><th style="width:16%">仓位(震荡)</th><th style="width:18%">仓位(弱市)</th></tr></thead>
<tbody>
<tr><td><span class="badge strat_a">A动量延续</span></td><td style="white-space:normal;word-break:break-all">涨3-7%+量比1.5-3.0+弱市关闭</td><td>12-17%</td><td>0%(关闭)</td></tr>
<tr><td><span class="badge strat_b">B超跌反弹</span></td><td style="white-space:normal;word-break:break-all">涨-9.5~-3%+振幅>3%或下影线</td><td>10-13%</td><td>12-15%</td></tr>
<tr><td><span class="badge strat_c">C事件驱动</span></td><td style="white-space:normal;word-break:break-all">涨1-2%+量比≥1.0或财报季</td><td>8-10%</td><td>5-8%</td></tr>
<tr><td><span class="badge strat_d">D回调企稳</span></td><td style="white-space:normal;word-break:break-all">涨3-6%+振幅2-8%+阳线</td><td>12-15%</td><td>8-12%</td></tr>
<tr><td><span class="badge strat_e">E资金埋伏</span></td><td style="white-space:normal;word-break:break-all">涨0-1%+主力流入>3000万</td><td>5-8%</td><td>3-5%</td></tr>
<tr><td><span class="badge strat_f">F北向资金</span></td><td style="white-space:normal;word-break:break-all">涨0-1%+主力流入>5000万+近5日持续≥3日</td><td>3-5%</td><td>3-5%</td></tr>
<tr><td><span class="badge strat_g">G横盘突破</span></td><td style="white-space:normal;word-break:break-all">涨2-3%+振幅1.5-6%+量比>1.5阳线突破</td><td>8-10%</td><td>5-8%</td></tr>
<tr><td><span class="badge strat_h">H地量见底</span></td><td style="white-space:normal;word-break:break-all">跌0~3%+量比<0.5+锤子线/十字星阳线</td><td>5-8%</td><td>3-5%</td></tr>
<tr><td><span class="badge strat_i">I均线突破</span></td><td style="white-space:normal;word-break:break-all">MA5/10/20粘合<2%+放量阳线突破均线</td><td>5-8%</td><td>3-5%</td></tr>
<tr><td><span class="badge strat_j">J龙回头</span></td><td style="white-space:normal;word-break:break-all">20日强势股+回调10-20%+缩量收阳企稳</td><td>8-10%</td><td>5-8%</td></tr>
<tr><td><span class="badge strat_k">K缺口回补</span></td><td style="white-space:normal;word-break:break-all">前日跳空高开1-5%+回踩缺口上沿确认+收阳</td><td>5-8%</td><td>3-5%</td></tr>
<tr><td><span class="badge strat_l">L黄金坑</span></td><td style="white-space:normal;word-break:break-all">5日急跌≥8%+V型反弹≥4%+放量收阳</td><td>8-10%</td><td>5-8%</td></tr>
<tr><td><span class="badge strat_m">M涨停回调</span></td><td style="white-space:normal;word-break:break-all">近5日涨停+回调5-15%+缩量收阳企稳</td><td>5-8%</td><td>3-5%</td></tr>
<tr><td><span class="badge strat_n">N新高突破</span></td><td style="white-space:normal;word-break:break-all">收盘价=20日新高+放量阳线突破</td><td>8-10%</td><td>5-8%</td></tr>
<tr><td><span class="badge strat_o">O回踩均线</span></td><td style="white-space:normal;word-break:break-all">60日涨>20%+回踩MA20±1%+缩量收阳</td><td>5-8%</td><td>3-5%</td></tr>
<tr><td><span class="badge strat_p">P地量反弹</span></td><td style="white-space:normal;word-break:break-all">连续3日缩量至地量+当日放量≥2x+涨2-5%阳线</td><td>5-8%</td><td>3-5%</td></tr>
<tr><td><span class="badge strat_q">Q W底突破</span></td><td style="white-space:normal;word-break:break-all">20日内两底相差<5%+放量突破颈线+阳线</td><td>8-10%</td><td>5-8%</td></tr>
</tbody></table></section></div>
<div class="footer"><p>版本: {file_version} | 生成时间: {beijing_date}</p><p style="color:#fb923c;margin-top:.3rem">★ 7日 = 近7日内已推荐标的（橙色高亮行），可持续关注但不建议重复建仓</p><p class="disclaimer">⚠️ 免责声明：本报告仅供研究参考，不构成任何投资建议。投资有风险，入市需谨慎。</p></div></body></html>"""
    
    with open(hp, 'w', encoding='utf-8') as f: f.write(html_content)
    log_alert("INFO", "HTML报告", f"已生成至 {hp}")
    return hp

# ============================================================
# 步骤21-22：验证 + 推荐历史
# ============================================================
def step21_final_verify(mp, fc):
    if os.path.exists(mp):
        with open(mp, 'r', encoding='utf-8') as f: content = f.read()
        tr = sum(1 for l in content.split('\n') if l.strip().startswith('| ') and l.split('|')[1].strip().isdigit())
        if tr != fc: log_alert("ERROR", "数量校验", f"概况{fc}≠MD表格{tr}")
        else: log_alert("INFO", "最终验证", f"通过（{fc}只）")

def step22_write_history(candidates):
    hf = f"/workspace/推荐历史_{data_date.replace('-', '')}.json"
    for c in candidates:
        entry = calc_entry_price(c)
        safe_append_json(hf, {"type": "recommendation", "code": c.get('code'), "name": c.get('name'),
            "strategy": c.get('strategy'), "industry": c.get('industry'),
            "score": c.get('score'), "confidence": c.get('confidence'),
            "entry": entry, "change_pct": c.get('change_pct'),
            "date": data_date, "prediction_date": prediction_date})
    log_alert("INFO", "推荐历史", f"已追加{len(candidates)}条")

# ============================================================
# 步骤26：GitHub同步
# ============================================================
def step26_github_sync(mp, hd, candidates):
    if not GITHUB_TOKEN: log_alert("WARNING", "GitHub同步", "无令牌"); return
    rd = None
    try:
        repo_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"
        rd = "/tmp/lv_sync"
        if os.path.exists(rd): shutil.rmtree(rd, ignore_errors=True)
        subprocess.run(["git", "clone", "--depth", "1", "--branch", "main", repo_url, rd],
                       capture_output=True, text=True, timeout=30, check=True)
        c15 = (datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=15)).strftime('%Y-%m-%d').replace('-', '')
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
        subprocess.run(["git", "-C", rd, "config", "user.email", "ashare-bot@github.com"], check=True)
        subprocess.run(["git", "-C", rd, "config", "user.name", "ashare-screener"], check=True)
        subprocess.run(["git", "-C", rd, "add", "."], check=True)
        subprocess.run(["git", "-C", rd, "commit", "-m", f"筛选结果 {prediction_date} (v{file_version})", "--allow-empty"], check=True)
        result = subprocess.run(["git", "-C", rd, "push", "origin", "main"], capture_output=True, text=True, timeout=30)
        if result.returncode == 0: log_alert("INFO", "GitHub同步", f"✅ {prediction_date} 已推送")
        else: log_alert("WARNING", "GitHub同步", f"推送失败: {result.stderr[:100].replace(GITHUB_TOKEN, '***')}")
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
        sn = {'A': '动量延续', 'B': '超跌反弹', 'C': '事件驱动', 'D': '回调企稳', 'E': '资金埋伏', 'F': '北向资金', 'G': '横盘突破', 'H': '地量见底', 'I': '均线突破', 'J': '龙回头', 'K': '缺口回补', 'L': '黄金坑', 'M': '涨停回调', 'N': '新高突破', 'O': '回踩均线', 'P': '地量反弹', 'Q': 'W底突破'}
        ss = " | ".join([f"{s}{sn.get(s,'')}:{sd.get(s,0)}只" for s in ['A','B','C','D','E','F','G','H','I','J','K','L','M','N','O','P','Q'] if sd.get(s, 0) > 0]) or "无推荐标的"
        pb = "https://lc132.github.io/lv"
        pr = f"{pb}/ashare-screening-{pred_yyyymmdd}/ashare-screening-{pred_yyyymmdd}.html"
        card = {"msg_type": "interactive", "card": {
            "header": {"title": {"tag": "plain_text", "content": f"📊 每日短线标的筛选 — {prediction_date}"}, "template": "blue"},
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**数据来源**: {data_date}  |  **市场环境**: {market_condition}  |  **建议仓位**: {position_pct}%"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"原始: **{total_raw}**只 → 硬排: **{ae}**只 → 信号: **{asig}**只 → 策略: **{astr}**只 → 行业: **{aind}**只 → 新闻: **{aind - anew}**只 → ★ 最终: **{fc}**只"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**策略分布**: {ss}"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"📈 [**查看完整可视化报告（GitHub Pages）**]({pr})\n📁 [**报告列表首页**]({pb})"}},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": "⚠️ 仅供参考，不构成投资建议"}]}]}}
        req = urllib.request.Request(FEISHU_WEBHOOK, data=json.dumps(card, ensure_ascii=False).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        resp = urllib.request.urlopen(req, timeout=10)
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
    
    print("\n[步骤0A] 拉取持仓..."); step0A_pull_holdings()
    
    print("\n[步骤1] 节假日...")
    if step1_holiday_check(): print("  节假日跳过"); return
    
    print("\n[步骤2] 极端行情...")
    if step2_extreme_market(): print("  极端行情跳过"); return
    
    print("\n[步骤3] 外围市场..."); step3_external_markets()
    print("\n[步骤3A] 大盘代理..."); step3A_domestic_index_check()
    
    print("\n[步骤4] 持仓行情..."); holdings = step4_holdings_sync()
    ahc = set(h.get('code') for h in holdings if h.get('code'))
    print(f"  持仓: {len(holdings)}只")
    
    print("\n[步骤4A] 做T评估..."); step4A_doT_eval(holdings)
    print("\n[步骤4B] 持仓跟踪..."); step4B_sync_holdings_xlsx(holdings)
    print("\n[步骤4C] 持仓危机..."); crisis_alerts = step4C_crisis_check(holdings)
    
    print("\n[步骤5] 清理..."); step5_history_clean()
    print("\n[步骤6] 初始化..."); step6_file_init()
    print("\n[步骤7] 财报季..."); step7_earnings_season()
    print("\n[步骤8] 大盘环境..."); step8_market_environment()
    print(f"  环境: {market_condition} | 仓位: {position_pct}%")
    
    print("\n[步骤10A] 全市场拉取..."); all_stocks, ds = step10A_fetch_all_stocks()
    update_data_source_monitor(ds)
    
    print("\n[步骤10B] 行业补全...")
    for s in all_stocks: s['industry'] = lookup_industry(s.get('code', ''))
    
    print("\n[步骤10C] 历史K线..."); kline_data = step10C_fetch_klines(all_stocks[:500])
    print("\n[步骤10D] 财务数据..."); pledge_data, goodwill_data, unlock_data = step10D_fetch_financials()
    
    raw_pool = [s for s in all_stocks if s.get('change_pct') is not None and s.get('change_pct') >= -9.5
                and s.get('close') is not None and s.get('close') > 0]
    if ds == 'tencent': raw_pool.sort(key=lambda x: ((x.get('turnover', 0) or 0), (x.get('amount', 0) or 0)), reverse=True)
    else: raw_pool.sort(key=lambda x: ((x.get('turnover', 0) or 0), (x.get('amount', 0) or 0)), reverse=True)  # v6.8.5: 统一使用turnover优先
    raw_pool = raw_pool[:500]
    total_raw = len(raw_pool)
    print(f"  原始池: {total_raw}只")
    
    print("\n[步骤11] 硬排除..."); ael, _, er = step11_hard_exclude(raw_pool, ahc, kline_data, pledge_data, goodwill_data, unlock_data, {}); ae = len(ael)
    print("\n[步骤10E] F10基本面..."); fundamental_data = step10E_fetch_fundamentals(ael)
    print("\n[步骤12] 信号过滤..."); asl, _ = step12_signal_filter(ael, kline_data, fundamental_data); asig = len(asl)
    print("\n[步骤13] 策略匹配..."); sm = step13_strategy_match(asl, kline_data); astr = len(sm)
    print("\n[步骤14] 评分..."); scored = step14_scoring(sm)
    print("\n[步骤15·16] 综合评分+平局打破..."); ranked = step16_comprehensive_score(scored)
    print("\n[步骤17] 行业限制..."); ail = step17_industry_limit(ranked); aind = len(ail)
    print("\n[步骤18] 新闻筛查..."); ail, anew = step18_news_screening(ail)
    print("\n[步骤19] 降级..."); final = step19_shortfall_handling(ail); fc = len(final)
    sd = Counter(c.get('strategy') for c in final)
    
    print("\n[步骤20] Markdown..."); mp = step20_output_markdown(final, total_raw, ae, asig, astr, aind, anew, er)
    print("\n[步骤20B] HTML..."); hd = f"/workspace/ashare-screening-{pred_yyyymmdd}"
    step20B_generate_html(final, total_raw, ae, asig, astr, aind, anew, er, crisis_alerts)
    
    print("\n[步骤21] 验证..."); step21_final_verify(mp, fc)
    print("\n[步骤22] 推荐历史..."); step22_write_history(final)
    
    print("\n" + "=" * 60)
    print("📊 筛选概况")
    print("=" * 60)
    print(f"prediction_date={prediction_date} (数据来源:{data_date})")
    print(f"①原始:N={total_raw} → ②硬排除:N={ae} → ③信号过滤:N={asig} → ④策略:N={astr} → ⑤行业限制:N={aind} → ⑥新闻筛查:N={aind - anew} → ★ 最终:N={fc}")
    sn = {'A': '动量延续', 'B': '超跌反弹', 'C': '事件驱动', 'D': '回调企稳', 'E': '资金埋伏', 'F': '北向资金', 'G': '横盘突破', 'H': '地量见底', 'I': '均线突破', 'J': '龙回头', 'K': '缺口回补', 'L': '黄金坑', 'M': '涨停回调', 'N': '新高突破', 'O': '回踩均线', 'P': '地量反弹', 'Q': 'W底突破'}
    print(f"策略分布: " + " ".join([f"{s}:{sd.get(s,0)}" for s in ['A','B','C','D','E','F','G','H','I','J','K','L','M','N','O','P','Q']]))
    print(f"排除TOP5: " + " ".join([f"{r}:{c}只" for r, c in er.most_common(5)]))
    print("=" * 60)
    
    if crisis_alerts:
        print("\n⚠️ 持仓危机:")
        for a in crisis_alerts: print(f"  {a}")
    
    print("\n[步骤26] GitHub同步..."); step26_github_sync(mp, hd, final)
    print("\n[步骤27] 飞书推送..."); step27_feishu_push(final, total_raw, ae, asig, astr, aind, anew, sd)
    print(f"\n✅ 完成！ {mp}")
    return final, mp

if __name__ == "__main__":
    main()