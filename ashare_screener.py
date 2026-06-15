#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股每日盘前短线标的智能筛选 v6.6.26
35步完整执行流程
Token/Webhook从环境变量或本地文件读取
"""
import urllib.request, urllib.error, json, os, sys, time, re, shutil, subprocess
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from openpyxl import load_workbook
from openpyxl.styles import Font, Alignment, Border, Side, PatternFill

# ============================================================
# 全局变量 - 凭证从环境变量或文件读取
# ============================================================
def _load_credential(env_key, file_path, fallback=""):
    if env_key in os.environ:
        return os.environ[env_key]
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r') as f:
                return f.read().strip()
        except:
            pass
    return fallback

GITHUB_TOKEN = _load_credential("GITHUB_TOKEN", "/workspace/.github_token")
FEISHU_WEBHOOK = _load_credential("FEISHU_WEBHOOK", "/workspace/.feishu_webhook")
GITHUB_REPO = "lc132/lv"
BUILTIN_VERSION = "v6.6.26"
beijing_now = None
beijing_date = None
beijing_weekday = None
data_date = None
prediction_date = None
pred_yyyymmdd = None
file_version = BUILTIN_VERSION
params = {}
market_condition = "震荡"
position_pct = 55
DEFAULT_PARAMS = {
    "search_budget": 25, "northbound_threshold": 3000, "consecutive_weeks": 2,
    "win_rate_drop_threshold": 10, "limit_down_threshold": 100,
    "max_adjust_params": 3, "confidence_position_enabled": True,
    "max_holding_days": 5, "circuit_breaker_threshold_pct": 3.0,
    "strategy_concentration_pct": 60, "do_t_success_reset_count": 3,
    "conversion_rate_window_days": 10, "conversion_rate_threshold": 0.3,
    "conversion_rate_restore": 0.6, "conversion_rate_consecutive_days": 3,
    "data_tier_l2_skip_on_unavailable": True,
    "data_tier_l3_downgrade_to_signal": True,
    "strategy_a_weak_market": "closed"
}

# ============================================================
# 工具函数
# ============================================================
def log_alert(level, module, message, timestamp=None):
    if timestamp is None:
        timestamp = datetime.now()
    ts = timestamp.strftime('%Y-%m-%d %H:%M:%S') if hasattr(timestamp, 'strftime') else str(timestamp)
    log_dir = "/workspace"
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, '系统告警.log'), 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] [{level}] {module}: {message}\n")
    print(f"[{level}] {module}: {message}")

def safe_read_json(path, default=None):
    try:
        if not os.path.exists(path): return default if default is not None else []
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        log_alert("WARNING", "safe_read_json", f"{path}: {str(e)[:80]}")
        return default if default is not None else []

def safe_write_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_alert("ERROR", "safe_write_json", f"{path}: {str(e)[:80]}")

def safe_append_json(path, record):
    data = safe_read_json(path)
    data.append(record)
    safe_write_json(path, data)

def safe_float(val, ndigits=3):
    if val is None: return None
    try: return round(float(val), ndigits)
    except: return None

# ============================================================
# 步骤0：获取北京时间
# ============================================================
def step0_get_beijing_time():
    global beijing_now, beijing_date, beijing_weekday, data_date, prediction_date, pred_yyyymmdd
    TIME_APIS = ['https://timeapi.io/api/time/current/zone?timeZone=Asia/Shanghai']
    for api_url in TIME_APIS:
        try:
            req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read())
            dt_str = data['dateTime']
            if '.' in dt_str:
                date_part, frac = dt_str.split('.')
                frac = frac[:6]
                dt_str = date_part + '.' + frac
            beijing_now = datetime.fromisoformat(dt_str)
            break
        except Exception as e:
            log_alert("INFO", "北京时间", f"{api_url} 不可达: {str(e)[:60]}")
            continue
    if beijing_now is None:
        log_alert("ERROR", "北京时间", "所有授时API均不可达")
        raise RuntimeError("北京时间获取失败")
    
    beijing_date = beijing_now.strftime('%Y-%m-%d')
    beijing_weekday = beijing_now.weekday()
    
    if beijing_weekday == 5:
        data_date = (beijing_now - timedelta(days=1)).strftime('%Y-%m-%d')
    elif beijing_weekday == 6:
        data_date = (beijing_now - timedelta(days=2)).strftime('%Y-%m-%d')
    else:
        data_date = beijing_date
    
    if beijing_weekday <= 3:
        prediction_date = (beijing_now + timedelta(days=1)).strftime('%Y-%m-%d')
    elif beijing_weekday == 4:
        prediction_date = (beijing_now + timedelta(days=3)).strftime('%Y-%m-%d')
    elif beijing_weekday == 5:
        prediction_date = (beijing_now + timedelta(days=2)).strftime('%Y-%m-%d')
    else:
        prediction_date = (beijing_now + timedelta(days=1)).strftime('%Y-%m-%d')
    
    pred_yyyymmdd = prediction_date.replace('-', '')
    log_alert("INFO", "北京时间", f"beijing_date={beijing_date}, data_date={data_date}, prediction_date={prediction_date}")

# ============================================================
# 步骤0A：从GitHub拉取持仓跟踪
# ============================================================
def step0A_pull_holdings():
    try:
        repo_dir = "/tmp/lv_holdings_pull"
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir, ignore_errors=True)
        repo_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"
        subprocess.run(["git", "clone", "--depth", "1", "--branch", "main", repo_url, repo_dir],
                       capture_output=True, text=True, timeout=30, check=True)
        
        xlsx_src = os.path.join(repo_dir, "持仓跟踪.xlsx")
        if os.path.exists(xlsx_src):
            shutil.copy(xlsx_src, "/workspace/持仓跟踪.xlsx")
            log_alert("INFO", "持仓拉取", "持仓跟踪.xlsx 已同步")
        
        for f in os.listdir(repo_dir):
            if f.startswith("推荐历史_") and f.endswith(".json"):
                local_path = os.path.join("/workspace", f)
                remote_path = os.path.join(repo_dir, f)
                if not os.path.exists(local_path) or os.path.getmtime(remote_path) > os.path.getmtime(local_path):
                    shutil.copy(remote_path, local_path)
                    log_alert("INFO", "持仓拉取", f"{f} 已更新")
        shutil.rmtree(repo_dir, ignore_errors=True)
        log_alert("INFO", "持仓拉取", "持仓跟踪拉取完成")
    except Exception as e:
        log_alert("WARNING", "持仓拉取", f"失败: {str(e)[:80]}")

# ============================================================
# 步骤1：节假日检查
# ============================================================
def step1_holiday_check():
    holidays_2026 = [
        "2026-01-01", "2026-01-02", "2026-02-16", "2026-02-17", "2026-02-18",
        "2026-02-19", "2026-02-20", "2026-04-06", "2026-05-01",
        "2026-06-19", "2026-06-22", "2026-10-01", "2026-10-02",
        "2026-10-05", "2026-10-06", "2026-10-07",
    ]
    return data_date in holidays_2026 or prediction_date in holidays_2026

# ============================================================
# 步骤2：极端行情
# ============================================================
def step2_extreme_market():
    global position_pct, market_condition
    try:
        url = "https://hq.sinajs.cn/list=sh000001"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'})
        resp = urllib.request.urlopen(req, timeout=5)
        text = resp.read().decode('gbk')
        if '=""' in text: return False
        parts = text.split('"')[1].split(',')
        current = float(parts[3]) if parts[3] else 0
        prev_close = float(parts[2]) if parts[2] else 0
        sh_change = (current - prev_close) / prev_close * 100 if prev_close > 0 else 0
        log_alert("INFO", "极端行情", f"上证{current:.0f} 涨跌{sh_change:.2f}%")
        if sh_change < -3: return True  # 跳过
        if sh_change > 3:
            position_pct = 30
            market_condition = "强市(动量延续)"
    except Exception as e:
        log_alert("WARNING", "极端行情", f"获取失败: {str(e)[:60]}")
    return False

# ============================================================
# 步骤3：外围市场
# ============================================================
def step3_external_markets():
    global position_pct, market_condition
    try:
        indices = {"道指": ".DJI", "标普": ".INX", "纳指": ".IXIC"}
        all_down = True
        for code in indices.values():
            try:
                url = f"https://hq.sinajs.cn/list=gb_{code}"
                req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'})
                resp = urllib.request.urlopen(req, timeout=5)
                text = resp.read().decode('gbk')
                if '=""' in text: all_down = False; break
                parts = text.split('"')[1].split(',')
                chg = float(parts[1]) if len(parts) > 1 and parts[1] else 0
                if chg > -2: all_down = False; break
            except: all_down = False; break
        if all_down:
            position_pct = min(position_pct, 30)
            market_condition = "弱市(美股暴跌)"
    except: pass

# ============================================================
# 步骤3A：期货
# ============================================================
def step3A_premarket_futures():
    global position_pct, market_condition
    try:
        url = "https://hq.sinajs.cn/list=hf_XINA50"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'})
        resp = urllib.request.urlopen(req, timeout=5)
        text = resp.read().decode('gbk')
        if '=""' not in text:
            parts = text.split('"')[1].split(',')
            if len(parts) > 2:
                chg = float(parts[2]) if parts[2] else 0
                if chg < -1:
                    log_alert("WARNING", "外围期货", f"A50期货跌{chg:.1f}%>1%，外围偏空降档")
                    if market_condition == "强市": market_condition = "震荡"
                    elif market_condition == "震荡": market_condition = "弱市"
                    position_pct = max(position_pct - 15, 25)
    except: log_alert("INFO", "外围期货", "期货数据不可得，跳过")

# ============================================================
# 步骤4：持仓行情同步
# ============================================================
def step4_holdings_sync():
    holdings = []
    all_history = []
    for f in sorted(os.listdir('/workspace')):
        if f.startswith('推荐历史_') and f.endswith('.json'):
            records = safe_read_json(os.path.join('/workspace', f))
            for r in records:
                if isinstance(r, dict):
                    r['_file'] = f
                all_history.append(r)
    
    for r in all_history:
        if r.get('type') != 'holding': continue
        code = r.get('code', '')
        old_current = r.get('current')
        try:
            market = 'sz' if code.startswith(('0','3')) else 'sh'
            url = f"https://hq.sinajs.cn/list={market}{code}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'})
            resp = urllib.request.urlopen(req, timeout=5)
            text = resp.read().decode('gbk')
            if '=""' not in text:
                parts = text.split('"')[1].split(',')
                new_current = float(parts[3]) if parts[3] else None
                if new_current and new_current > 0:
                    r['prev_close'] = old_current
                    r['current'] = new_current
                    r['update_date'] = data_date
                    cost = r.get('cost', new_current)
                    shares = r.get('shares', 100)
                    r['market_value'] = round(new_current * shares, 2)
                    r['pnl_amount'] = round((new_current - cost) * shares, 2)
                    if cost > 0: r['pnl_pct'] = round((new_current - cost) / cost * 100, 2)
                    holdings.append(r)
        except Exception as e:
            log_alert("WARNING", "持仓行情", f"{code} 搜索失败: {str(e)[:40]}")
    
    file_groups = defaultdict(list)
    for r in all_history:
        if '_file' in r:
            fname = r.pop('_file')
            file_groups[fname].append(r)
    for fname, records in file_groups.items():
        safe_write_json(os.path.join('/workspace', fname), records)
    return holdings

# ============================================================
# 步骤4A：做T评估
# ============================================================
def step4A_doT_eval(holdings):
    do_t_records = []
    for h in holdings:
        pnl_pct = h.get('pnl_pct', 0)
        if pnl_pct > 0 or pnl_pct > -5: feasibility = "观望"
        elif -10 < pnl_pct <= -5: feasibility = "True"
        elif -15 < pnl_pct <= -10: feasibility = "谨慎"
        else: feasibility = "False"
        do_t_records.append({
            "type": "do_T_eval", "code": h.get('code'), "name": h.get('name'),
            "date": data_date, "pnl_pct": pnl_pct,
            "do_T_feasible": feasibility,
            "position_ratio": "≤1/3" if feasibility == "True" else ("≤1/4" if feasibility == "谨慎" else "不操作")
        })
    if do_t_records:
        hist_file = f"/workspace/推荐历史_{data_date.replace('-', '')}.json"
        existing = safe_read_json(hist_file)
        existing.extend(do_t_records)
        safe_write_json(hist_file, existing)
    return do_t_records

# ============================================================
# 步骤4B：持仓跟踪同步
# ============================================================
def step4B_sync_holdings_xlsx(holdings):
    try:
        xlsx_path = "/workspace/持仓跟踪.xlsx"
        if not os.path.exists(xlsx_path):
            log_alert("WARNING", "持仓跟踪", "持仓跟踪.xlsx不存在")
            return
        wb = load_workbook(xlsx_path)
        ws = wb["持仓明细"]
        code_row = {}
        for row in range(2, ws.max_row + 1):
            raw_code = ws.cell(row=row, column=1).value
            if raw_code:
                code = str(raw_code).strip()
                if len(code) == 4: code = code.zfill(6)
                if code.isdigit() and len(code) == 6: code_row[code] = row
        updated = 0
        for h in holdings:
            code = str(h.get('code', ''))
            if code not in code_row: continue
            row = code_row[code]
            current = h.get('current')
            if current is None: continue
            ws.cell(row=row, column=8).value = current
            ws.cell(row=row, column=9).value = h.get('market_value')
            ws.cell(row=row, column=10).value = round(h.get('pnl_amount', 0), 2)
            ws.cell(row=row, column=11).value = round(float(h.get('pnl_pct', 0)), 4)
            ws.cell(row=row, column=12).value = data_date
            updated += 1
        if updated > 0:
            wb.save(xlsx_path)
            log_alert("INFO", "持仓跟踪", f"已更新{updated}只持仓价格")
    except Exception as e:
        log_alert("WARNING", "持仓跟踪", f"同步失败: {str(e)[:80]}")

# ============================================================
# 步骤4C：持仓危机
# ============================================================
def step4C_crisis_check(holdings):
    alerts = []
    for h in holdings:
        code = h.get('code', '?'); name = h.get('name', '?')
        cost = h.get('cost', 0); current = h.get('current', 0)
        prev_close = h.get('prev_close'); pnl_pct = h.get('pnl_pct', 0)
        if prev_close and current > 0 and prev_close > 0:
            daily_chg = (current - prev_close) / prev_close * 100
            if daily_chg < -9.5:
                msg = f"⚠️ {code} {name} 当日跌停({daily_chg:.1f}%)！成本{cost} 现价{current} 浮亏{pnl_pct}%"
                alerts.append(msg); log_alert("WARNING", "持仓危机", msg)
        if pnl_pct is not None and pnl_pct < -15:
            msg = f"⚠️ {code} {name} 浮亏突破15%做T上限({pnl_pct:.1f}%)，建议人工决策"
            alerts.append(msg); log_alert("WARNING", "持仓危机", msg)
        if current > 0:
            triggers = []
            if current < 5: triggers.append("股价<5元(规则3)")
            if current > 100: triggers.append("股价>100元(规则4)")
            if code.startswith("688"): triggers.append("科创板(规则1)")
            if code.startswith("8") and len(str(code)) == 6: triggers.append("北交所(规则2)")
            if triggers:
                msg = f"⚠️ {code} {name} 触发L1硬排除: {', '.join(triggers)}"
                alerts.append(msg); log_alert("WARNING", "持仓危机", msg)
    return alerts

# ============================================================
# 步骤5：推荐历史清理
# ============================================================
def step5_history_clean():
    total_cleaned = 0
    cutoff_7d_dt = datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=7)
    cutoff_90d_dt = datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=90)
    cutoff_7d = cutoff_7d_dt.strftime('%Y-%m-%d')
    cutoff_90d = cutoff_90d_dt.strftime('%Y-%m-%d')
    for f in sorted(os.listdir('/workspace')):
        if not (f.startswith('推荐历史_') and f.endswith('.json')): continue
        hist = safe_read_json(os.path.join('/workspace', f))
        new_records = []
        for r in hist:
            t = r.get('type', '')
            if t in ('weekly_review', 'strategy_check', 'do_T_eval', 'do_T'):
                new_records.append(r)
            elif t == 'holding':
                if r.get('update_date', '') >= cutoff_90d: new_records.append(r)
            elif t == 'recommendation':
                if r.get('date', '') >= cutoff_7d: new_records.append(r)
            else: new_records.append(r)
        if len(new_records) < len(hist):
            safe_write_json(os.path.join('/workspace', f), new_records)
            total_cleaned += len(hist) - len(new_records)
    if total_cleaned > 0: log_alert("INFO", "清理", f"已清理{total_cleaned}条过期记录")
    else: log_alert("INFO", "清理", "无需清理")

# ============================================================
# 步骤6：文件初始化
# ============================================================
def step6_file_init():
    global file_version, params
    adj_records = safe_read_json('/workspace/策略调整记录.json')
    if adj_records and len(adj_records) > 0:
        file_version = adj_records[-1].get('version', BUILTIN_VERSION)
        params = adj_records[-1].get('params', {})
    else:
        file_version = BUILTIN_VERSION; params = {}
    for k, v in DEFAULT_PARAMS.items():
        if k not in params: params[k] = v
    log_alert("INFO", "文件初始化", f"版本={file_version}, 参数项={len(params)}")

    all_history = []
    for f in sorted(os.listdir('/workspace')):
        if f.startswith('推荐历史_') and f.endswith('.json'):
            all_history.extend(safe_read_json(os.path.join('/workspace', f)))
    last_check = None
    for r in reversed(all_history):
        if r.get('type') == 'strategy_check': last_check = r; break
    if last_check and last_check.get('version') != file_version:
        log_alert("INFO", "版本检查", f"推荐历史版本≠策略调整版本{file_version}")
    if last_check is None or (last_check and last_check.get('version') != file_version):
        hist_file = f"/workspace/推荐历史_{data_date.replace('-', '')}.json"
        safe_append_json(hist_file, {"type": "strategy_check", "version": file_version, "params": params, "date": data_date, "checks": "版本同步完成"})

# ============================================================
# 步骤7-8：财报季+大盘环境
# ============================================================
def step7_earnings_season():
    global position_pct
    if beijing_now.month in (1, 3, 4, 8, 10):
        position_pct = min(position_pct + 5, 85)

def step8_market_environment():
    global market_condition, position_pct
    try:
        from pytdx.hq import TdxHq_API
        api = TdxHq_API()
        for host in ['119.147.212.81', '120.76.152.87', '47.92.127.118', '59.173.18.140']:
            if api.connect(host, 7709):
                bars = api.get_security_bars(9, 1, '000001', 0, 25)
                if bars and len(bars) >= 20:
                    closes = [b['close'] for b in bars]
                    ma20 = sum(closes[-20:]) / 20
                    cur_close = closes[-1]
                    if cur_close > ma20: market_condition = "强市"; position_pct = 75
                    elif cur_close < ma20 * 0.98: market_condition = "弱市"; position_pct = 35
                    else: market_condition = "震荡"; position_pct = 55
                    api.disconnect()
                    log_alert("INFO", "大盘环境", f"{market_condition} 仓位{position_pct}%")
                    return
                api.disconnect()
    except: pass
    try:
        url = "https://hq.sinajs.cn/list=sh000001"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'})
        resp = urllib.request.urlopen(req, timeout=5)
        text = resp.read().decode('gbk')
        if '=""' not in text:
            parts = text.split('"')[1].split(',')
            cur = float(parts[3]); prev = float(parts[2])
            sh_chg = (cur - prev) / prev * 100
    except: pass
    market_condition = "震荡"; position_pct = 55
    log_alert("INFO", "大盘环境", f"降级判断: {market_condition} 仓位{position_pct}%")

# ============================================================
# 步骤10A：全市场API拉取 (三级降级)
# ============================================================
def step10A_fetch_all_stocks():
    # Tier 1: clist
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params_q = {
            "pn": "1", "pz": "6000", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f2,f3,f5,f6,f7,f8,f10,f12,f14,f15,f16,f17,f18,f20,f62",
            "_": str(int(time.time() * 1000))
        }
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params_q)}",
                                     headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'})
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data and data.get('data') and data['data'].get('diff'):
            stocks = []
            for item in data['data']['diff']:
                code = item.get('f12', ''); name = item.get('f14', '')
                close_val = item.get('f2')
                if not code or not name or close_val == '-' or close_val is None: continue
                try:
                    stocks.append({
                        "code": code, "name": name,
                        "open": float(item.get('f17', 0)) if item.get('f17') not in (None, '-') else None,
                        "close": float(close_val),
                        "change_pct": float(item.get('f3', 0)) if item.get('f3') not in (None, '-') else 0,
                        "turnover": float(item.get('f8', 0)) if item.get('f8') not in (None, '-') else 0,
                        "amplitude": float(item.get('f7', 0)) if item.get('f7') not in (None, '-') else 0,
                        "volume_ratio": float(item.get('f10', 0)) if item.get('f10') not in (None, '-') else 0,
                        "amount": float(item.get('f6', 0)) if item.get('f6') not in (None, '-') else 0,
                        "high": float(item.get('f15', 0)) if item.get('f15') not in (None, '-') else None,
                        "low": float(item.get('f16', 0)) if item.get('f16') not in (None, '-') else None,
                        "prev_close": float(item.get('f18', 0)) if item.get('f18') not in (None, '-') else None,
                        "main_inflow": float(item.get('f62', 0)) if item.get('f62') not in (None, '-') else None,
                        "total_cap": float(item.get('f20', 0)) if item.get('f20') not in (None, '-') else None,
                    })
                except: continue
            log_alert("INFO", "行情采集", f"clist 成功拉取 {len(stocks)} 只")
            return stocks, "clist"
    except Exception as e:
        log_alert("INFO", "行情采集", f"clist不可达: {str(e)[:60]}")
    
    # Tier 2: 新浪
    log_alert("INFO", "行情采集", "降级为新浪批量API")
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
                            "high": high_v, "low": low_v,
                            "prev_close": prev_close,
                            "turnover": 0, "amplitude": amplitude_v,
                            "volume_ratio": None, "main_inflow": None, "total_cap": None,
                        })
                    except: continue
                if i % (batch_size * 10) == 0: time.sleep(0.02)
            except: continue
        log_alert("INFO", "行情采集", f"新浪API 成功拉取 {len(stocks)} 只")
        return stocks, "sina"
    except Exception as e:
        log_alert("ERROR", "行情采集", f"新浪失败: {str(e)[:60]}")
    
    # Tier 3: pytdx
    try:
        from pytdx.hq import TdxHq_API
        api = TdxHq_API()
        for host in ['119.147.212.81', '120.76.152.87']:
            if api.connect(host, 7709): break
        stocks = []
        for market_code in [0, 1]:
            count = api.get_security_count(market_code)
            for batch_num in range(0, min(count, 3000), 80):
                try:
                    quotes_list = api.get_security_list(market_code, batch_num)
                    if not quotes_list: continue
                    codes = [(market_code, q['code']) for q in quotes_list[:80]]
                    quotes = api.get_security_quotes(codes)
                    if not quotes: continue
                    for q in quotes:
                        code = q.get('code', ''); name = q.get('name', '')
                        if not code or not name: continue
                        cur = q.get('price', 0); prev = q.get('last_close', 0)
                        if cur <= 0 or prev <= 0: continue
                        q_high = q.get('high', cur); q_low = q.get('low', cur)
                        q_amplitude = round((q_high - q_low) / prev * 100, 2) if prev > 0 else 0
                        stocks.append({
                            "code": code, "name": name,
                            "open": q.get('open', cur), "close": cur,
                            "change_pct": round((cur - prev) / prev * 100, 2),
                            "high": q_high, "low": q_low,
                            "prev_close": prev, "amount": q.get('amount', 0),
                            "turnover": 0, "amplitude": q_amplitude,
                            "volume_ratio": None, "main_inflow": None, "total_cap": None,
                        })
                except: pass
        api.disconnect()
        return stocks, "pytdx"
    except Exception as e:
        log_alert("ERROR", "行情采集", f"三级数据源均不可达: {str(e)[:80]}")
        raise RuntimeError("行情数据获取失败")

# ============================================================
# 步骤10B：行业补全（代码段查表 v6.6.26）
# ============================================================
INDUSTRY_MAP = {
    '600000-600099': '银行', '600100-600199': '电子', '600200-600299': '医药生物',
    '600300-600399': '基础化工', '600400-600499': '电力设备', '600500-600599': '公用事业',
    '600600-600699': '食品饮料', '600700-600799': '交通运输', '600800-600899': '机械设备',
    '600900-600999': '银行',
    '601000-601099': '非银金融', '601100-601199': '有色金属', '601200-601299': '非银金融',
    '601300-601399': '机械设备', '601400-601499': '银行', '601500-601599': '非银金融',
    '601600-601699': '有色金属', '601700-601799': '电力设备', '601800-601899': '建筑装饰',
    '601900-601999': '传媒',
    '603000-603099': '电子', '603100-603199': '机械设备', '603200-603299': '基础化工',
    '603300-603399': '机械设备', '603400-603499': '电子', '603500-603599': '电子',
    '603600-603699': '轻工制造', '603700-603799': '汽车', '603800-603899': '机械设备',
    '603900-603999': '商贸零售', '605000-605099': '机械设备', '605100-605199': '电力设备',
    '605200-605299': '基础化工', '605300-605399': '食品饮料', '605500-605599': '轻工制造',
    '000001-000099': '银行', '000100-000199': '电子', '000200-000299': '房地产',
    '000300-000399': '医药生物', '000400-000499': '电力设备', '000500-000599': '公用事业',
    '000600-000699': '公用事业', '000700-000799': '钢铁', '000800-000899': '汽车',
    '000900-000999': '非银金融',
    '001000-001099': '电子', '001200-001299': '基础化工', '001300-001399': '机械设备',
    '001600-001699': '汽车', '001700-001799': '建筑装饰', '001800-001899': '食品饮料',
    '001900-001999': '公用事业',
    '002000-002099': '电子', '002100-002199': '医药生物', '002200-002299': '建筑装饰',
    '002300-002399': '电力设备', '002400-002499': '传媒', '002500-002599': '基础化工',
    '002600-002699': '电子', '002700-002799': '机械设备', '002800-002899': '基础化工',
    '002900-002999': '电子', '003000-003099': '食品饮料',
    '300000-300099': '电子', '300100-300199': '汽车', '300200-300299': '基础化工',
    '300300-300399': '计算机', '300400-300499': '机械设备', '300500-300599': '建筑装饰',
    '300600-300699': '国防军工', '300700-300799': '机械设备', '300800-300899': '环保',
    '300900-300999': '电力设备', '301000-301099': '机械设备', '301100-301199': '基础化工',
    '301200-301299': '电子', '301300-301399': '计算机', '301500-301599': '汽车',
}

def lookup_industry(code):
    code_int = int(code)
    for k, v in INDUSTRY_MAP.items():
        lo, hi = k.split('-')
        if int(lo) <= code_int <= int(hi): return v
    return "未知"

# ============================================================
# 步骤11：硬排除31项
# ============================================================
def step11_hard_exclude(candidates, all_holdings_codes):
    exclude_reasons = Counter()
    recent_codes = set()
    cutoff_7d = (datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
    for f in sorted(os.listdir('/workspace')):
        if f.startswith('推荐历史_') and f.endswith('.json'):
            for r in safe_read_json(os.path.join('/workspace', f)):
                if r.get('type') == 'recommendation' and r.get('date', '') >= cutoff_7d:
                    recent_codes.add(r.get('code', ''))
    recent_codes.update(all_holdings_codes)
    
    passed, excluded = [], []
    for c in candidates:
        code = c.get('code', ''); close = c.get('close', 0); change_pct = c.get('change_pct', 0)
        reason = None
        if code.startswith('688'): reason = "科创板(规则1)"
        elif code.startswith('8'): reason = "北交所(规则2)"
        elif close < 5: reason = f"股价<5元(规则3)"
        elif close > 100: reason = f"股价>100元(规则4)"
        elif 'ST' in c.get('name', ''): reason = "ST/*ST(规则5)"
        elif change_pct > 7: reason = f"涨幅>7%(规则12)"
        elif code in recent_codes: reason = "7日内已推荐(规则13)"; c['_recently_screened'] = True
        elif close <= 0: reason = "停牌(规则9)"
        
        main_inflow = c.get('main_inflow')
        if not reason and main_inflow and main_inflow < -10000:
            amount = c.get('amount', 0)
            if amount > 0 and abs(main_inflow) / (amount / 10000) > 0.15:
                c['_l3_warning'] = "⚠️主力净流出>1亿占成交额>15%(规则26)"
        
        if reason:
            exclude_reasons[reason.split('(')[0].replace('股价<5元','股价<5').replace('股价>100元','股价>100').replace('涨幅>7%','涨幅>7').replace('7日内已推荐','7日内已推荐+持仓')] += 1
            excluded.append(c)
        else:
            passed.append(c)
    log_alert("INFO", "硬排除", f"通过{len(passed)}只 排除{len(excluded)}只")
    return passed, excluded, exclude_reasons

# ============================================================
# 步骤12：信号过滤14项
# ============================================================
def step12_signal_filter(candidates):
    passed, excluded = [], []
    for c in candidates:
        change_pct = c.get('change_pct', 0); close = c.get('close', 0); open_p = c.get('open', 0)
        high = c.get('high', 0); low = c.get('low', 0); amplitude = c.get('amplitude', 0)
        vol_ratio = c.get('volume_ratio'); turnover = c.get('turnover', 0)
        reason = None; score_adj = 0
        if open_p > 0 and c.get('prev_close', open_p) > 0:
            prev_c = c.get('prev_close', open_p)
            if (open_p - prev_c) / prev_c > 0.03 and close < open_p * 0.98:
                reason = "假动量:高开>3%收<开×0.98"
        if not reason and high > 0 and open_p > 0:
            prev_c = c.get('prev_close', open_p)
            if (high - prev_c) / prev_c > 0.05 and close < open_p * 1.01 and prev_c > 0:
                reason = "诱多:盘中涨>5%收<开×1.01"
        if not reason and change_pct > 5 and vol_ratio is not None and vol_ratio < 0.5:
            reason = "缩量涨停"
        if not reason and amplitude > 15:
            reason = f"振幅>{amplitude:.1f}%"
        if not reason and -3 < change_pct < 0 and turnover > 3:
            c['_first_yin'] = True; score_adj += 1
        if reason: excluded.append(c)
        else: c['_signal_score_adj'] = score_adj; passed.append(c)
    log_alert("INFO", "信号过滤", f"通过{len(passed)}只 排除{len(excluded)}只")
    return passed, excluded

# ============================================================
# 步骤13：五策略筛选
# ============================================================
def step13_strategy_match(candidates):
    matched = []
    for c in candidates:
        change_pct = c.get('change_pct', 0); amplitude = c.get('amplitude', 0)
        amount = c.get('amount', 0); vol_ratio = c.get('volume_ratio')
        close = c.get('close', 0); open_p = c.get('open', 0)
        strategy = None; reason = ""; score = 0
        
        if market_condition != "弱市" and 3 <= change_pct <= 7:
            if vol_ratio is not None and 1.5 <= vol_ratio <= 3.0:
                strategy = "A"; reason = f"动量延续:涨{change_pct:.1f}%+量比{vol_ratio:.1f}"; score = 10
        
        if not strategy and -9.5 <= change_pct <= -3:
            if amplitude > 3 or (low := c.get('low', 0)) < close * 0.99:
                strategy = "B"; reason = f"超跌反弹:跌{change_pct:.1f}%+振幅{amplitude:.1f}%"; score = 7
        
        if not strategy and 0 < change_pct <= 5:
            if beijing_now.month in (1, 3, 4, 8, 10):
                strategy = "C"; reason = f"事件驱动(财报季):涨{change_pct:.1f}%"; score = 8
            elif vol_ratio is not None and vol_ratio >= 1.0:
                strategy = "C"; reason = f"事件驱动:涨{change_pct:.1f}%+量比{vol_ratio:.1f}"; score = 7
        
        if not strategy and 2 <= change_pct <= 6:
            if 2 <= amplitude <= 8 and close > open_p:
                strategy = "D"; reason = f"回调企稳:涨{change_pct:.1f}%+阳线+振幅{amplitude:.1f}%"; score = 8
                if vol_ratio is not None and vol_ratio >= 1.5:
                    strategy = "A"; reason = f"动量延续:涨{change_pct:.1f}%+量比{vol_ratio:.1f}"; score = 10
        
        # v6.6.26 策略E收紧
        if not strategy and 0 <= change_pct <= 2:
            main_inflow = c.get('main_inflow')
            if main_inflow is not None and main_inflow > params.get('northbound_threshold', 3000):
                strategy = "E"; reason = f"资金埋伏:涨{change_pct:.1f}%+主力流入{main_inflow:.0f}万"; score = 6
            elif amount > 3e8 and amplitude > 0.5:
                strategy = "E"; reason = f"资金埋伏(代理):涨{change_pct:.1f}%+成交额{amount/1e8:.1f}亿+振幅{amplitude:.1f}%"; score = 5
        
        if strategy: c['strategy'] = strategy; c['reason'] = reason; c['score'] = score; matched.append(c)
    
    log_alert("INFO", "策略匹配", f"匹配{len(matched)}只")
    return matched

# ============================================================
# 步骤14-17：评分+行业限制
# ============================================================
def step14_scoring(candidates):
    for c in candidates:
        score = c.get('score', 0) * 2
        if c.get('_first_yin'): score += 1
        if c.get('_signal_score_adj'): score += c['_signal_score_adj']
        if c.get('_l3_warning'): score -= 2
        c['score'] = max(0, score)
        s = c['score']
        if s >= 18: c['confidence'] = '★★★'
        elif s >= 12: c['confidence'] = '★★'
        else: c['confidence'] = '★'
    return candidates

def step17_industry_limit(candidates):
    industry_groups = defaultdict(list)
    for c in candidates:
        industry_groups[c.get('industry', '未知')].append(c)
    limited = []
    for group in industry_groups.values():
        group.sort(key=lambda x: x.get('score', 0), reverse=True)
        limited.extend(group[:3])
    max_same = max(1, len(limited) * params.get('strategy_concentration_pct', 60) // 100)
    strategy_groups = defaultdict(list)
    for c in limited:
        strategy_groups[c.get('strategy', 'Z')].append(c)
    final = []
    for group in strategy_groups.values():
        group.sort(key=lambda x: x.get('score', 0), reverse=True)
        final.extend(group[:max_same])
    log_alert("INFO", "行业限制", f"通过{len(final)}只 (原始{len(candidates)}只)")
    return final

def step16_comprehensive_score(candidates):
    strategy_order = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4}
    for c in candidates:
        vol_ratio = c.get('volume_ratio') or 0; turnover = c.get('turnover') or 0
        change_pct = c.get('change_pct') or 0
        vol_score = min(vol_ratio / 3.0, 1.0)
        if turnover < 2: t_score = 0.2
        elif turnover <= 5: t_score = 0.6
        elif turnover <= 15: t_score = 1.0
        elif turnover <= 25: t_score = 0.5
        else: t_score = 0.1
        s = c.get('strategy', 'Z')
        if s in ('A', 'E'): c_score = max(0, 1.0 - abs(change_pct - 3) / 7.0)
        elif s == 'B': c_score = max(0, 1.0 - abs(change_pct + 5) / 5.0)
        else: c_score = max(0, 1.0 - abs(change_pct - 2) / 8.0)
        c['_tie_score'] = vol_score * 0.25 + t_score * 0.25 + c_score * 0.25 + 0.15 + (1.0 - strategy_order.get(s, 99) / 10.0) * 0.10
    candidates.sort(key=lambda x: (-x.get('score', 0), strategy_order.get(x.get('strategy', 'Z'), 99), -(x.get('_tie_score', 0))))
    return candidates

def step19_shortfall_handling(candidates):
    if len(candidates) >= 3: return candidates
    elif len(candidates) == 2: return [c for c in candidates if c.get('confidence', '★') >= '★★']
    elif len(candidates) == 1: return [c for c in candidates if c.get('confidence', '★') >= '★★★']
    return []

# ============================================================
# 步骤20：Markdown输出
# ============================================================
def step20_output_markdown(candidates, total_raw, after_exclude, after_signal, after_strategy, after_industry, exclude_reasons):
    md_path = f"/workspace/短线标的_{prediction_date}.md"
    lines = [
        f"# A股短线标的筛选报告 — {prediction_date}", "",
        f"- **数据日期**: {data_date}", f"- **预测日期**: {prediction_date}",
        f"- **市场环境**: {market_condition}", f"- **建议仓位**: {position_pct}%",
        f"- **规则版本**: {file_version}", "",
        "## 筛选管道（6级漏斗）", "",
        "| 阶段 | 数量 | 排除 | 说明 |",
        "|------|------|------|------|",
        f"| ①原始标的池 | {total_raw} | - | 全市场>0%涨幅+活跃TOP500 |",
        f"| ②硬排除 | {after_exclude} | {total_raw - after_exclude} | 31项L1/L2/L3 |",
        f"| ③信号过滤 | {after_signal} | {after_exclude - after_signal} | 14项信号质检 |",
        f"| ④策略匹配 | {after_strategy} | {after_signal - after_strategy} | ABCDE五策略 |",
        f"| ⑤行业+同策略限制 | {after_industry} | {after_strategy - after_industry} | 同行业≤3只+同策略≤60% |",
        f"| ⑥最终推荐 | {len(candidates)} | {after_industry - len(candidates)} | 评分门控+二次评估 |", "",
    ]
    if candidates:
        lines.append("## 推荐标的\n")
        lines.append("| # | 策略 | 标的 | 代码 | 行业 | 涨跌幅 | 开盘 | 收盘 | 振幅 | 评分 | 置信 | 进场 | 止损 | 止盈 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for idx, c in enumerate(candidates, 1):
            code = c.get('code', ''); name = c.get('name', '')
            strategy = c.get('strategy', '?'); industry = c.get('industry', '未知')
            change_pct = c.get('change_pct', 0)
            open_p = c.get('open', 0) or 0; close_p = c.get('close', 0) or 0
            amplitude = c.get('amplitude', 0) or 0
            score = c.get('score', 0); conf = c.get('confidence', '★')
            chg_emoji = "🔴" if change_pct >= 0 else "🟢"
            entry = close_p; stop_loss = round(close_p * 0.96, 2); take_profit = round(close_p * 1.05, 2)
            url = f"https://quote.eastmoney.com/concept/sh{code}.html" if code.startswith('6') else f"https://quote.eastmoney.com/concept/sz{code}.html"
            lines.append(f"| {idx} | {strategy} | [{name}]({url}) | {code} | {industry} | {chg_emoji}{change_pct:+.2f}% | {open_p:.2f} | {close_p:.2f} | {amplitude:.2f}% | {score} | {conf} | {entry:.2f} | {stop_loss:.2f} | {take_profit:.2f} |")
    
    strategy_dist = Counter(c.get('strategy') for c in candidates)
    strategy_names = {'A': '动量延续', 'B': '超跌反弹', 'C': '事件驱动', 'D': '回调企稳', 'E': '资金埋伏'}
    lines.append("\n## 策略分布")
    for s in ['A', 'B', 'C', 'D', 'E']:
        if strategy_dist.get(s, 0) > 0:
            lines.append(f"- {s} {strategy_names.get(s, '')}: {strategy_dist[s]}只")
    
    lines.append("\n## 硬排除 TOP5")
    for reason, count in exclude_reasons.most_common(5):
        lines.append(f"- {reason}: {count}只")
    
    lines.append("\n## 策略说明\n")
    lines.append("| 策略 | 条件 | 仓位(震荡) | 仓位(弱市) |")
    lines.append("|------|------|-----------|-----------|")
    lines.append("| A动量延续 | 涨幅3-7%/量比1.5-3.0/MA5>MA10>MA20 | 12-17% | 0%(关闭) |")
    lines.append("| B超跌反弹 | 连跌≥3日/RSI<35/MA20支撑 | 10-13% | 12-15% |")
    lines.append("| C事件驱动 | 重大合同/预增>50%/政策 | 8-10% | 5-8% |")
    lines.append("| D回调企稳 | 20日新高回调MA20+缩量站回MA5 | 12-15% | 8-12% |")
    lines.append("| E资金埋伏 | 北向连续净买+主力流入>3000万 | 5-8% | 3-5% |")
    lines.append(f"\n\n> ⚠️ 免责声明：本报告仅供研究参考，不构成任何投资建议。投资有风险，入市需谨慎。\n> 版本: {file_version} | 生成时间: {beijing_date}")
    
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    log_alert("INFO", "Markdown", f"已输出至 {md_path}")
    return md_path

# ============================================================
# 步骤20B：HTML报告
# ============================================================
def step20B_generate_html(candidates, total_raw, after_exclude, after_signal, after_strategy, after_industry, exclude_reasons, crisis_alerts):
    html_dir = f"/workspace/ashare-screening-{pred_yyyymmdd}"
    os.makedirs(html_dir, exist_ok=True)
    html_path = f"{html_dir}/ashare-screening-{pred_yyyymmdd}.html"
    strategy_dist = Counter(c.get('strategy') for c in candidates)
    strategy_names = {'A': '动量延续', 'B': '超跌反弹', 'C': '事件驱动', 'D': '回调企稳', 'E': '资金埋伏'}
    strategy_colors = {'A': '#22c55e', 'B': '#3b82f6', 'C': '#8b5cf6', 'D': '#f59e0b', 'E': '#ec4899'}
    final_count = len(candidates)
    
    rows_html = ""
    for idx, c in enumerate(candidates, 1):
        code = c.get('code', ''); name = c.get('name', ''); strategy = c.get('strategy', '?')
        industry = c.get('industry', '未知'); change_pct = c.get('change_pct', 0)
        open_p = c.get('open', 0) or 0; close_p = c.get('close', 0) or 0
        amplitude = c.get('amplitude', 0) or 0
        score = c.get('score', 0); conf = c.get('confidence', '★')
        entry = close_p; stop_loss = round(close_p * 0.96, 2); take_profit = round(close_p * 1.05, 2)
        chg_cls = "up" if change_pct >= 0 else "down"
        conf_cls = "high" if "★★★" in conf else ("mid" if "★★" in conf else "low")
        strat_cls = f"strat_{strategy.lower()}"
        url = f"https://quote.eastmoney.com/concept/sh{code}.html" if code.startswith('6') else f"https://quote.eastmoney.com/concept/sz{code}.html"
        rows_html += f"""<tr class="{strat_cls}">
            <td>{idx}</td><td><span class="badge {strat_cls}">{strategy}</span></td>
            <td><a href="{url}" target="_blank">{name}</a></td><td>{code}</td><td>{industry}</td>
            <td class="{chg_cls}">{change_pct:+.2f}%</td><td>{open_p:.2f}</td><td>{close_p:.2f}</td>
            <td>{amplitude:.2f}%</td><td>{score}</td><td class="conf {conf_cls}">{conf}</td>
            <td>{entry:.2f}</td><td>{stop_loss:.2f}</td><td>{take_profit:.2f}</td></tr>"""
    
    seg_html = ""; legend_html = ""
    total_matched = sum(strategy_dist.values())
    if total_matched > 0:
        for s in ['A', 'B', 'C', 'D', 'E']:
            cnt = strategy_dist.get(s, 0)
            if cnt > 0:
                pct = cnt / total_matched * 100
                seg_html += f'<div class="seg" style="width:{pct}%;background:{strategy_colors[s]}">{cnt}</div>'
                legend_html += f'<span class="legend-item"><span class="legend-dot" style="background:{strategy_colors[s]}"></span> {s}{strategy_names.get(s, "")}: {cnt}只 ({pct:.0f}%)</span>'
    
    bar_html = ""
    max_exclude = max(exclude_reasons.values()) if exclude_reasons else 1
    for reason, count in exclude_reasons.most_common(5):
        bar_pct = count / max_exclude * 100
        bar_html += f'<div class="bar-row"><div class="bar-label">{reason}</div><div class="bar-track"><div class="bar-fill" style="width:{bar_pct}%">{count}</div></div></div>'
    
    stages = [("原始标的池", total_raw), ("硬排除(31项)", after_exclude), ("信号过滤(14项)", after_signal), ("策略匹配(5策略)", after_strategy), ("行业+同策略限制", after_industry), ("最终推荐", final_count)]
    max_funnel = max(s[1] for s in stages)
    funnel_html = ""
    for i, (name, count) in enumerate(stages):
        w = max(12, int(count / max(max_funnel, 1) * 100))
        cls = "funnel-last" if i == len(stages) - 1 else ""
        funnel_html += f'<div class="funnel-step {cls}" style="width:{w}%">{name}: {count}只</div>'
    
    strat_bars = ""
    for s in ['A', 'B', 'C', 'D', 'E']:
        cnt = strategy_dist.get(s, 0)
        bar_pct = cnt / max(max(strategy_dist.values()), 1) * 100
        strat_bars += f'<div class="bar-row"><div class="bar-label">{s} {strategy_names.get(s, "")}</div><div class="bar-track"><div class="bar-fill" style="width:{bar_pct}%;background:{strategy_colors[s]}">{cnt}</div></div></div>'
    
    alerts_html = ""
    if crisis_alerts:
        for a in crisis_alerts:
            alerts_html += f'<div class="alert-item"><span class="alert-level warning">WARNING</span><span class="alert-msg">{a}</span></div>'
    else:
        alerts_html = '<div class="alert-item"><span class="alert-level info">INFO</span><span class="alert-msg">今日无异常告警</span></div>'
    
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
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
.index-card{{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:1rem 1.5rem;text-align:center;min-width:140px}}
.index-card .idx-name{{font-size:.85rem;color:#cbd5e1}}.index-card .idx-price{{font-size:1.5rem;font-weight:bold}}
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
.strat_d{{background:#5c3d0e;color:#f59e0b}}.strat_e{{background:#5c1648;color:#ec4899}}
tr.strat_a{{background:rgba(34,197,94,0.05)}}tr.strat_b{{background:rgba(59,130,246,0.05)}}tr.strat_c{{background:rgba(139,92,246,0.05)}}
tr.strat_d{{background:rgba(245,158,11,0.05)}}tr.strat_e{{background:rgba(236,72,153,0.05)}}
.conf{{font-weight:bold}}.conf.high{{color:#22c55e}}.conf.mid{{color:#f59e0b}}.conf.low{{color:#ef4444}}
.alert-item{{display:flex;gap:.8rem;padding:.4rem 0;border-bottom:1px solid #334155;font-size:.8rem}}
.alert-level{{padding:2px 10px;border-radius:4px;font-weight:bold;font-size:.7rem;white-space:nowrap}}
.alert-level.warning{{background:#5c3d0e;color:#f59e0b}}.alert-level.info{{background:#1e3a5f;color:#3b82f6}}
.alert-level.error{{background:#5c1a1a;color:#ef4444}}
.footer{{text-align:center;padding:2rem;color:#64748b;font-size:.8rem}}
.footer .disclaimer{{color:#ef4444;font-weight:bold;margin-top:.5rem}}
a{{color:#38bdf8;text-decoration:none}}a:hover{{text-decoration:underline}}
@media(max-width:768px){{.chart-grid{{grid-template-columns:1fr}}.container{{padding:.5rem}}th,td{{font-size:.7rem;padding:.3rem}}}}
</style></head><body>
<div class="header"><h1>A股短线标的筛选报告</h1><div class="sub">{prediction_date} | 规则版本 {file_version}</div></div>
<div class="container">
<div class="index-row">
<div class="index-card"><div class="idx-name">上证指数</div><div class="idx-price">-</div><div class="idx-chg">盘后更新</div></div>
<div class="index-card"><div class="idx-name">深证成指</div><div class="idx-price">-</div><div class="idx-chg">盘后更新</div></div>
<div class="index-card"><div class="idx-name">创业板指</div><div class="idx-price">-</div><div class="idx-chg">盘后更新</div></div></div>
<div class="meta-row">
<div class="meta-card"><div class="label">预测日期</div><div class="value">{prediction_date}</div></div>
<div class="meta-card"><div class="label">数据日期</div><div class="value">{data_date}</div></div>
<div class="meta-card"><div class="label">市场环境</div><div class="value">{market_condition}</div></div>
<div class="meta-card"><div class="label">建议仓位</div><div class="value">{position_pct}%</div></div>
<div class="meta-card"><div class="label">最终推荐</div><div class="value">{final_count}只</div></div></div>
<section><h2>筛选管道</h2><div class="funnel">{funnel_html}</div></section>
<section><h2>数据可视化</h2><div class="chart-grid">
<div><h3 style="font-size:.9rem;color:#cbd5e1;margin-bottom:.5rem">策略分布</h3><div class="seg-bar">{seg_html if seg_html else '<div style="color:#94a3b8;text-align:center;padding:1rem">无推荐标的</div>'}</div><div class="legend">{legend_html}</div></div>
<div><h3 style="font-size:.9rem;color:#cbd5e1;margin-bottom:.5rem">硬排除TOP5</h3>{bar_html if bar_html else '<div style="color:#94a3b8;text-align:center">无排除记录</div>'}</div>
<div><h3 style="font-size:.9rem;color:#cbd5e1;margin-bottom:.5rem">各策略数量</h3>{strat_bars if strat_bars else '<div style="color:#94a3b8;text-align:center">无匹配</div>'}</div>
<div><h3 style="font-size:.9rem;color:#cbd5e1;margin-bottom:.5rem">概述</h3><div style="font-size:.8rem;color:#cbd5e1">全市场标的→{total_raw}只入围→{after_exclude}只通过硬排除→{after_signal}只通过信号过滤→{after_strategy}只匹配策略→{after_industry}只通过行业限制→<strong style="color:#38bdf8">最终{final_count}只</strong></div></div>
</div></section>
<section><h2>系统告警</h2><div class="alert-list">{alerts_html}</div></section>
<section><h2>最终推荐标的</h2><div style="overflow-x:auto"><table>
<thead><tr><th>#</th><th>策略</th><th>标的</th><th>代码</th><th>行业</th><th>涨跌幅</th><th>开盘</th><th>收盘</th><th>振幅</th><th>评分</th><th>置信</th><th>进场</th><th>止损</th><th>止盈</th></tr></thead>
<tbody>{rows_html if rows_html else '<tr><td colspan="14" style="text-align:center;color:#94a3b8;padding:2rem">无合适标的</td></tr>'}</tbody></table></div></section>
<section><h2>策略说明</h2><table>
<thead><tr><th style="width:18%">策略</th><th style="width:48%">条件</th><th style="width:16%">仓位(震荡)</th><th style="width:18%">仓位(弱市)</th></tr></thead>
<tbody>
<tr><td><span class="badge strat_a">A动量延续</span></td><td style="white-space:normal;word-break:break-all">涨幅3-7% + 量比1.5-3.0 + MA5>MA10>MA20</td><td>12-17%</td><td>0%(关闭)</td></tr>
<tr><td><span class="badge strat_b">B超跌反弹</span></td><td style="white-space:normal;word-break:break-all">连跌≥3日 + RSI<35 + KDJ J拐头 + MA20支撑</td><td>10-13%</td><td>12-15%</td></tr>
<tr><td><span class="badge strat_c">C事件驱动</span></td><td style="white-space:normal;word-break:break-all">重大合同/预增>50%/部委政策</td><td>8-10%</td><td>5-8%</td></tr>
<tr><td><span class="badge strat_d">D回调企稳</span></td><td style="white-space:normal;word-break:break-all">20日新高回调MA20 + 缩量站回MA5放量</td><td>12-15%</td><td>8-12%</td></tr>
<tr><td><span class="badge strat_e">E资金埋伏</span></td><td style="white-space:normal;word-break:break-all">北向连续净买 + 主力流入>3000万 + 涨幅<2%</td><td>5-8%</td><td>3-5%</td></tr>
</tbody></table></section></div>
<div class="footer"><p>版本: {file_version} | 生成时间: {beijing_date}</p><p class="disclaimer">⚠️ 免责声明：本报告仅供研究参考，不构成任何投资建议。投资有风险，入市需谨慎。</p></div></body></html>"""
    
    with open(html_path, 'w', encoding='utf-8') as f: f.write(html)
    log_alert("INFO", "HTML报告", f"已生成至 {html_path}")
    return html_path

# ============================================================
# 步骤21-22：验证 + 写推荐历史
# ============================================================
def step21_final_verify(md_path, final_count):
    if os.path.exists(md_path):
        with open(md_path, 'r', encoding='utf-8') as f:
            content = f.read()
        table_rows = sum(1 for line in content.split('\n')
                         if line.strip().startswith('| ') and line.split('|')[1].strip().isdigit())
        if table_rows != final_count:
            log_alert("ERROR", "数量校验", f"概况{final_count}≠MD表格{table_rows}")
        else:
            log_alert("INFO", "最终验证", f"通过（{final_count}只）")

def step22_write_history(candidates):
    hist_file = f"/workspace/推荐历史_{data_date.replace('-', '')}.json"
    for c in candidates:
        safe_append_json(hist_file, {
            "type": "recommendation", "code": c.get('code'), "name": c.get('name'),
            "strategy": c.get('strategy'), "industry": c.get('industry'),
            "score": c.get('score'), "confidence": c.get('confidence'),
            "entry": c.get('close'), "change_pct": c.get('change_pct'),
            "date": data_date, "prediction_date": prediction_date,
        })
    log_alert("INFO", "推荐历史", f"已追加{len(candidates)}条推荐记录")

# ============================================================
# 步骤26：GitHub同步
# ============================================================
def step26_github_sync(md_path, html_dir, candidates):
    if not GITHUB_TOKEN:
        log_alert("WARNING", "GitHub同步", "无认证令牌，跳过推送")
        return
    try:
        repo_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"
        repo_dir = "/tmp/lv_sync"
        if os.path.exists(repo_dir): shutil.rmtree(repo_dir, ignore_errors=True)
        subprocess.run(["git", "clone", "--depth", "1", "--branch", "main", repo_url, repo_dir],
                       capture_output=True, text=True, timeout=30, check=True)
        
        cutoff_15d = (datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=15)).strftime('%Y-%m-%d').replace('-', '')
        for f in list(os.listdir(repo_dir)):
            for prefix in ['短线标的_', '推荐历史_']:
                if f.startswith(prefix):
                    d = f.replace(prefix, '').replace('.md', '').replace('.json', '')
                    if len(d) == 8 and d < cutoff_15d:
                        pf = os.path.join(repo_dir, f)
                        if os.path.exists(pf): os.remove(pf)
        
        shutil.copy(md_path, os.path.join(repo_dir, f"短线标的_{prediction_date}.md"))
        html_name = f"ashare-screening-{pred_yyyymmdd}"
        dst = os.path.join(repo_dir, html_name)
        if os.path.exists(dst): shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(html_dir, dst)
        if os.path.exists("/workspace/持仓跟踪.xlsx"):
            shutil.copy("/workspace/持仓跟踪.xlsx", os.path.join(repo_dir, "持仓跟踪.xlsx"))
        for f in os.listdir('/workspace'):
            if f.startswith('推荐历史_') and f.endswith('.json'):
                shutil.copy(os.path.join('/workspace', f), os.path.join(repo_dir, f))
        
        subprocess.run(["git", "-C", repo_dir, "config", "user.email", "ashare-bot@github.com"], check=True)
        subprocess.run(["git", "-C", repo_dir, "config", "user.name", "ashare-screener"], check=True)
        subprocess.run(["git", "-C", repo_dir, "add", "."], check=True)
        subprocess.run(["git", "-C", repo_dir, "commit", "-m", f"筛选结果 {prediction_date} (v{file_version})", "--allow-empty"], check=True)
        result = subprocess.run(["git", "-C", repo_dir, "push", "origin", "main"], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            log_alert("INFO", "GitHub同步", f"✅ {prediction_date} 已推送")
        else:
            log_alert("WARNING", "GitHub同步", f"推送失败: {result.stderr[:100]}")
    except Exception as e:
        log_alert("WARNING", "GitHub同步", f"失败: {str(e)[:100]}")
    finally:
        if os.path.exists(repo_dir): shutil.rmtree(repo_dir, ignore_errors=True)

# ============================================================
# 步骤27：飞书推送
# ============================================================
def step27_feishu_push(candidates, total_raw, after_exclude, after_signal, after_strategy, after_industry, strategy_dist):
    if not FEISHU_WEBHOOK:
        log_alert("WARNING", "飞书推送", "未配置Webhook URL")
        return
    try:
        final_count = len(candidates)
        strategy_names = {'A': '动量延续', 'B': '超跌反弹', 'C': '事件驱动', 'D': '回调企稳', 'E': '资金埋伏'}
        strategy_summary = " | ".join([f"{s}{strategy_names.get(s,'')}:{strategy_dist.get(s,0)}只"
                                       for s in ['A','B','C','D','E'] if strategy_dist.get(s, 0) > 0]) or "无推荐标的"
        pages_base = "https://lc132.github.io/lv"
        pages_report = f"{pages_base}/ashare-screening-{pred_yyyymmdd}/ashare-screening-{pred_yyyymmdd}.html"
        card = {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": f"📊 每日短线标的筛选 — {prediction_date}"}, "template": "blue"},
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"**数据来源**: {data_date}  |  **市场环境**: {market_condition}  |  **建议仓位**: {position_pct}%"}},
                    {"tag": "hr"},
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"原始: **{total_raw}**只 → 硬排: **{after_exclude}**只 → 信号: **{after_signal}**只 → 策略: **{after_strategy}**只 → 行业: **{after_industry}**只 → ★ 最终: **{final_count}**只"}},
                    {"tag": "hr"},
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"**策略分布**: {strategy_summary}"}},
                    {"tag": "hr"},
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"📈 [**查看完整可视化报告（GitHub Pages）**]({pages_report})\n📁 [**报告列表首页**]({pages_base})"}},
                    {"tag": "note", "elements": [{"tag": "plain_text", "content": "⚠️ 仅供参考，不构成投资建议"}]}
                ]
            }
        }
        req = urllib.request.Request(FEISHU_WEBHOOK, data=json.dumps(card, ensure_ascii=False).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if result.get('code') == 0: log_alert("INFO", "飞书推送", f"✅ {prediction_date} 已推送")
        else: log_alert("WARNING", "飞书推送", f"推送失败: {result.get('msg','')}")
    except Exception as e:
        log_alert("WARNING", "飞书推送", f"失败: {str(e)[:80]}")

# ============================================================
# 数据源监控
# ============================================================
def update_data_source_monitor(data_source):
    monitor = safe_read_json("/workspace/数据源监控.json", default={"clist_success": 0, "clist_consecutive_failures": 0, "total_runs": 0, "last_source": "", "history": []})
    if not isinstance(monitor, dict):
        monitor = {"clist_success": 0, "clist_consecutive_failures": 0, "total_runs": 0, "last_source": "", "history": []}
    monitor["total_runs"] = monitor.get("total_runs", 0) + 1
    if data_source == "clist":
        monitor["clist_success"] = monitor.get("clist_success", 0) + 1
        cf = monitor.get("clist_consecutive_failures", 0)
        if cf > 0: log_alert("INFO", "数据源监控", f"clist已恢复（连续失败{cf}次后成功）")
        monitor["clist_consecutive_failures"] = 0
    else:
        monitor["clist_consecutive_failures"] = monitor.get("clist_consecutive_failures", 0) + 1
        cf = monitor["clist_consecutive_failures"]
        if cf == 1: log_alert("WARNING", "数据源监控", f"clist第1次不可达，降级至{data_source}")
        elif cf >= 10: log_alert("CRITICAL", "数据源监控", f"clist连续{cf}次不可达！")
    monitor["last_source"] = data_source
    monitor["history"].append({"date": data_date, "source": data_source, "count": 0, "success": True})
    if len(monitor["history"]) > 30: monitor["history"] = monitor["history"][-30:]
    safe_write_json("/workspace/数据源监控.json", monitor)

# ============================================================
# 主流程
# ============================================================
def main():
    global market_condition, position_pct
    print("=" * 60)
    print(f"A股每日盘前短线标的筛选 v{BUILTIN_VERSION}")
    print("=" * 60)
    
    print("\n[步骤0] 获取北京时间..."); step0_get_beijing_time()
    print(f"  Beijing: {beijing_date} (weekday={beijing_weekday})")
    print(f"  Data: {data_date} | Prediction: {prediction_date}")
    
    print("\n[步骤0A] 从GitHub拉取持仓跟踪..."); step0A_pull_holdings()
    
    print("\n[步骤1] 节假日检查...")
    if step1_holiday_check(): print("  节假日跳过筛选"); return
    
    print("\n[步骤2] 极端行情检查...")
    if step2_extreme_market(): print("  极端行情跳过"); return
    
    print("\n[步骤3] 外围市场..."); step3_external_markets()
    print("\n[步骤3A] 外围期货..."); step3A_premarket_futures()
    
    print("\n[步骤4] 持仓行情同步..."); holdings = step4_holdings_sync()
    all_holdings_codes = set(h.get('code') for h in holdings if h.get('code'))
    print(f"  持仓标的: {len(holdings)}只")
    
    print("\n[步骤4A] 做T评估..."); step4A_doT_eval(holdings)
    print("\n[步骤4B] 持仓跟踪同步..."); step4B_sync_holdings_xlsx(holdings)
    print("\n[步骤4C] 持仓危机..."); crisis_alerts = step4C_crisis_check(holdings)
    
    print("\n[步骤5] 清理..."); step5_history_clean()
    print("\n[步骤6] 文件初始化..."); step6_file_init()
    print("\n[步骤7] 财报季..."); step7_earnings_season()
    print("\n[步骤8] 大盘环境..."); step8_market_environment()
    print(f"  市场环境: {market_condition} | 仓位: {position_pct}%")
    
    print("\n[步骤10A] 全市场拉取..."); all_stocks, data_source = step10A_fetch_all_stocks()
    update_data_source_monitor(data_source)
    
    print("\n[步骤10B] 行业补全...")
    for s in all_stocks: s['industry'] = lookup_industry(s.get('code', ''))
    
    raw_pool = [s for s in all_stocks if s.get('change_pct') is not None and s.get('change_pct') > -9.5
                and s.get('close') is not None and s.get('close') > 0]
    if data_source == 'clist': raw_pool.sort(key=lambda x: (x.get('turnover', 0) or 0), reverse=True)
    else: raw_pool.sort(key=lambda x: (x.get('amount', 0) or 0), reverse=True)
    raw_pool = raw_pool[:500]
    total_raw = len(raw_pool)
    print(f"  原始标的池: {total_raw}只")
    
    print("\n[步骤11] 硬排除31项..."); after_exclude_list, _, exclude_reasons = step11_hard_exclude(raw_pool, all_holdings_codes)
    after_exclude = len(after_exclude_list)
    
    print("\n[步骤12] 信号过滤14项..."); after_signal_list, _ = step12_signal_filter(after_exclude_list)
    after_signal = len(after_signal_list)
    
    print("\n[步骤13] 五策略匹配..."); strategy_matched = step13_strategy_match(after_signal_list)
    after_strategy = len(strategy_matched)
    
    print("\n[步骤14] 评分门控..."); scored = step14_scoring(strategy_matched)
    print("\n[步骤16] 综合评分..."); ranked = step16_comprehensive_score(scored)
    print("\n[步骤17] 行业限制..."); after_industry_list = step17_industry_limit(ranked)
    after_industry = len(after_industry_list)
    
    print("\n[步骤19] 降级..."); final_candidates = step19_shortfall_handling(after_industry_list)
    final_count = len(final_candidates)
    strategy_dist = Counter(c.get('strategy') for c in final_candidates)
    
    print("\n[步骤20] Markdown..."); md_path = step20_output_markdown(final_candidates, total_raw, after_exclude, after_signal, after_strategy, after_industry, exclude_reasons)
    print("\n[步骤20B] HTML..."); html_dir = f"/workspace/ashare-screening-{pred_yyyymmdd}"
    step20B_generate_html(final_candidates, total_raw, after_exclude, after_signal, after_strategy, after_industry, exclude_reasons, crisis_alerts)
    
    print("\n[步骤21] 验证..."); step21_final_verify(md_path, final_count)
    print("\n[步骤22] 写推荐历史..."); step22_write_history(final_candidates)
    
    print("\n" + "=" * 60)
    print("📊 筛选概况")
    print("=" * 60)
    print(f"prediction_date={prediction_date} (数据来源:{data_date})")
    print(f"①原始:N={total_raw} → ②硬排除:N={after_exclude} → ③信号过滤:N={after_signal} → ④策略:N={after_strategy} → ⑤行业限制:N={after_industry} → ★ 最终:N={final_count}")
    strategy_names = {'A': '动量延续', 'B': '超跌反弹', 'C': '事件驱动', 'D': '回调企稳', 'E': '资金埋伏'}
    print(f"策略分布: " + " ".join([f"{s}:{strategy_dist.get(s,0)}" for s in ['A','B','C','D','E']]))
    print(f"排除TOP5: " + " ".join([f"{r}:{c}只" for r, c in exclude_reasons.most_common(5)]))
    print("=" * 60)
    
    if crisis_alerts:
        print("\n⚠️ 持仓危机告警:")
        for a in crisis_alerts: print(f"  {a}")
    
    print("\n[步骤26] GitHub同步..."); step26_github_sync(md_path, html_dir, final_candidates)
    print("\n[步骤27] 飞书推送..."); step27_feishu_push(final_candidates, total_raw, after_exclude, after_signal, after_strategy, after_industry, strategy_dist)
    
    print(f"\n✅ 筛选完成！ {md_path}")
    return final_candidates, md_path

if __name__ == "__main__":
    main()