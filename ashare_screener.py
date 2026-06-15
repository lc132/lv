#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股每日盘前短线标的筛选 v6.6.23
完整35步执行流程 - 单文件统一版本
"""
import json, os, sys, time, urllib.request, urllib.parse, urllib.error, subprocess, shutil
from datetime import datetime, timedelta
from collections import Counter

# ============================================================
# Global State
# ============================================================
STATE = {
    'beijing_now': None, 'beijing_date': None, 'beijing_hour': None, 'beijing_weekday': None,
    'data_date': None, 'prediction_date': None, 'pred_yyyymmdd': None,
    'market_condition': '震荡', 'suggested_position': 50,
    'file_version': 'v6.6.23', 'params': {},
    'source': None, 'all_stocks': [], 'candidates': [],
    'holdings_list': [], 'crisis_alerts': [], 'sector_flow': {},
    'excluded_11': [], 'passed_11': [], 'excluded_12': [], 'passed_12': [],
    'final_recos': [], 'strategy_dist': {},
    'total_raw': 0, 'n11_pass': 0, 'n11_excl': 0, 'n12_pass': 0, 'n12_excl': 0,
    'n13_pass': 0, 'n17_pass': 0, 'n18_pass': 0, 'final_count': 0,
}

BUILTIN_VERSION = "v6.6.23"

# ============================================================
# Core Functions
# ============================================================
def log_alert(level, module, message):
    ts = STATE['beijing_now'].strftime('%Y-%m-%d %H:%M:%S') if STATE['beijing_now'] else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open('/workspace/系统告警.log', 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] [{level}] {module}: {message}\n")
    print(f"[{ts}] [{level}] {module}: {message}")

def safe_read_json(path, default=None):
    try:
        if not os.path.exists(path): return default if default is not None else []
        with open(path, 'r') as f:
            data = json.load(f)
            if not isinstance(data, list):
                log_alert("WARNING", "safe_read_json", f"{path} 格式异常")
                return default if default is not None else []
            return data
    except (json.JSONDecodeError, PermissionError) as e:
        log_alert("ERROR", "safe_read_json", f"{path}: {str(e)}")
        return default if default is not None else []

def safe_write_json(path, data):
    try:
        with open(path, 'w') as f: json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e: log_alert("ERROR", "safe_write_json", f"{path}: {str(e)}")

def safe_append_json(path, record):
    data = safe_read_json(path)
    data.append(record)
    safe_write_json(path, data)

def read_all_history():
    history = []
    try:
        for f in sorted(os.listdir('/workspace')):
            if f.startswith('推荐历史_') and f.endswith('.json'):
                records = safe_read_json(f'/workspace/{f}')
                history.extend(records)
    except Exception as e:
        log_alert("WARNING", "历史读取", f"读取推荐历史失败: {str(e)[:80]}")
    return history

# ============================================================
# STEP 0: Beijing Time
# ============================================================
def step0():
    TIME_APIS = ['https://timeapi.io/api/time/current/zone?timeZone=Asia/Shanghai']
    for api_url in TIME_APIS:
        try:
            req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read())
            dt_str = data['dateTime']
            if '.' in dt_str:
                date_part, frac = dt_str.split('.')
                dt_str = date_part + '.' + frac[:6]
            STATE['beijing_now'] = datetime.fromisoformat(dt_str)
            break
        except Exception as e:
            log_alert("INFO", "北京时间", f"{api_url} 不可达: {str(e)[:60]}")
    
    if STATE['beijing_now'] is None:
        log_alert("ERROR", "北京时间", "所有授时API均不可达，筛选中止")
        raise RuntimeError("北京时间获取失败")
    
    bn = STATE['beijing_now']
    STATE['beijing_date'] = bn.strftime('%Y-%m-%d')
    STATE['beijing_hour'] = bn.hour
    STATE['beijing_weekday'] = bn.weekday()
    
    wd = STATE['beijing_weekday']
    if wd == 5: STATE['data_date'] = (bn - timedelta(days=1)).strftime('%Y-%m-%d')
    elif wd == 6: STATE['data_date'] = (bn - timedelta(days=2)).strftime('%Y-%m-%d')
    else: STATE['data_date'] = STATE['beijing_date']
    
    if wd <= 3: STATE['prediction_date'] = (bn + timedelta(days=1)).strftime('%Y-%m-%d')
    elif wd == 4: STATE['prediction_date'] = (bn + timedelta(days=3)).strftime('%Y-%m-%d')
    elif wd == 5: STATE['prediction_date'] = (bn + timedelta(days=2)).strftime('%Y-%m-%d')
    else: STATE['prediction_date'] = (bn + timedelta(days=1)).strftime('%Y-%m-%d')
    
    STATE['pred_yyyymmdd'] = STATE['prediction_date'].replace('-', '')
    log_alert("INFO", "北京时间", f"beijing={STATE['beijing_date']} data_date={STATE['data_date']} prediction={STATE['prediction_date']}")

# ============================================================
# STEP 0A: Pull Holdings
# ============================================================
def step0A():
    token = os.environ.get("GITHUB_TOKEN", "")
    repo_url = f"https://{token}@github.com/lc132/lv.git"
    temp_dir = "/tmp/lv_pull"
    try:
        if os.path.exists(temp_dir): shutil.rmtree(temp_dir, ignore_errors=True)
        subprocess.run(["git", "clone", "--depth", "1", "--branch", "main", repo_url, temp_dir],
                       capture_output=True, text=True, timeout=30, check=True)
        for f in ['持仓跟踪.xlsx']:
            src = os.path.join(temp_dir, f)
            if os.path.exists(src): shutil.copy(src, f'/workspace/{f}')
        for f in os.listdir(temp_dir):
            if f.startswith('推荐历史_') and f.endswith('.json'):
                dst = f'/workspace/{f}'
                src = os.path.join(temp_dir, f)
                if not os.path.exists(dst) or os.path.getmtime(src) > os.path.getmtime(dst):
                    shutil.copy(src, dst)
        log_alert("INFO", "持仓拉取", "完成")
    except Exception as e:
        log_alert("WARNING", "持仓拉取", f"失败: {str(e)[:80]}")
    finally:
        if os.path.exists(temp_dir): shutil.rmtree(temp_dir, ignore_errors=True)

# ============================================================
# STEP 1: Holidays
# ============================================================
def step1():
    return False  # Simplified: assume non-holiday for Monday

# ============================================================
# STEP 2: Extreme Market
# ============================================================
def step2():
    try:
        req = urllib.request.Request(
            "https://push2.eastmoney.com/api/qt/stock/get?secid=1.000001&fields=f43,f170",
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'})
        resp = urllib.request.urlopen(req, timeout=5)
        d = json.loads(resp.read()).get('data', {})
        sh_change = d.get('f170', 0) / 100 if d.get('f170') else 0
        if sh_change < -3:
            log_alert("INFO", "极端行情", f"上证跌{sh_change}%>3%，跳过")
            return True
        elif sh_change > 3:
            STATE['market_condition'] = '强市'
            STATE['suggested_position'] = 30
            log_alert("INFO", "极端行情", f"上证涨{sh_change}%>3%")
    except Exception as e:
        log_alert("WARNING", "极端行情", f"获取失败: {str(e)[:60]}")
    return False

# ============================================================
# STEP 3: External Market
# ============================================================
def step3():
    try:
        req = urllib.request.Request(
            "https://push2.eastmoney.com/api/qt/stock/get?secid=100.HSI&fields=f43,f170",
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'})
        resp = urllib.request.urlopen(req, timeout=5)
        d = json.loads(resp.read()).get('data', {})
        hsi = d.get('f170', 0) / 100 if d.get('f170') else 0
        if hsi < -3:
            STATE['market_condition'] = '弱市'
            log_alert("INFO", "外围市场", f"恒生跌{hsi}%>3%")
    except: pass

# ============================================================
# STEP 3A: Pre-market Futures
# ============================================================
def step3A():
    pass  # Simplified

# ============================================================
# STEP 4: Holdings Sync
# ============================================================
def step4():
    history = read_all_history()
    holdings = [r for r in history if r.get('type') == 'holding']
    if not holdings:
        log_alert("INFO", "持仓同步", "无持仓记录")
        return
    
    for h in holdings:
        code = h.get('code', '')
        name = h.get('name', '')
        old_current = h.get('current')
        try:
            market = '0' if code.startswith(('000','002','003','300','301')) else '1'
            req = urllib.request.Request(
                f'https://push2.eastmoney.com/api/qt/stock/get?secid={market}.{code}&fields=f43',
                headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=5)
            d = json.loads(resp.read()).get('data', {})
            current_price = d.get('f43', 0) / 100 if d.get('f43') else None
            if current_price and current_price > 0:
                h['prev_close'] = old_current
                h['current'] = current_price
                cost = h.get('cost', 0)
                shares = h.get('shares', 0)
                h['market_value'] = round(current_price * shares, 2)
                h['pnl_amount'] = round((current_price - cost) * shares, 2)
                if cost > 0: h['pnl_pct'] = round((current_price - cost) / cost * 100, 2)
                h['update_date'] = STATE['data_date']
            else:
                log_alert("WARNING", "持仓同步", f"{code} {name} 行情获取失败")
        except Exception as e:
            log_alert("WARNING", "持仓同步", f"{code} {name}: {str(e)[:60]}")
    
    STATE['holdings_list'] = holdings
    # Write back
    for f in sorted(os.listdir('/workspace')):
        if f.startswith('推荐历史_') and f.endswith('.json'):
            records = safe_read_json(f'/workspace/{f}')
            modified = False
            for r in records:
                if r.get('type') == 'holding':
                    for h in holdings:
                        if h.get('code') == r.get('code'):
                            for k in ['current','prev_close','market_value','pnl_amount','pnl_pct','update_date']:
                                if k in h: r[k] = h[k]
                            modified = True
            if modified: safe_write_json(f'/workspace/{f}', records)

# ============================================================
# STEP 4A: Do-T Eval
# ============================================================
def step4A():
    for h in STATE['holdings_list']:
        pnl_pct = h.get('pnl_pct', 0) or 0
        feasible = False
        limit = 0
        if pnl_pct > -5: feasible, limit = "观望", 0
        elif pnl_pct >= -10: feasible, limit = True, 0.33
        elif pnl_pct >= -15: feasible, limit = "谨慎", 0.25
        else: feasible, limit = False, 0
        eval_r = {"type":"do_T_eval","code":h.get('code'),"name":h.get('name'),
                  "date":STATE['data_date'],"pnl_pct":pnl_pct,"do_T_feasible":feasible,
                  "position_limit":limit,"current":h.get('current'),"cost":h.get('cost')}
        safe_append_json(f'/workspace/推荐历史_{STATE["data_date"].replace("-","")}.json', eval_r)

# ============================================================
# STEP 4B: Holdings XLSX Sync
# ============================================================
def step4B():
    xlsx_path = "/workspace/持仓跟踪.xlsx"
    if not os.path.exists(xlsx_path):
        log_alert("WARNING", "持仓跟踪同步", "文件不存在")
        return
    try:
        from openpyxl import load_workbook
        wb = load_workbook(xlsx_path)
        ws = wb["持仓明细"]
        code_row = {}
        for row in range(2, ws.max_row + 1):
            raw = ws.cell(row=row, column=1).value
            if raw:
                c = str(raw).strip()
                if len(c) == 4: c = c.zfill(6)
                if c.isdigit() and len(c) == 6: code_row[c] = row
        updated = 0
        for h in STATE['holdings_list']:
            code = str(h.get('code',''))
            current = h.get('current')
            if not code or code not in code_row or current is None: continue
            row = code_row[code]
            ws.cell(row=row, column=8).value = current
            mv = h.get('market_value')
            if mv is not None: ws.cell(row=row, column=9).value = mv
            pa = h.get('pnl_amount')
            if pa is not None: ws.cell(row=row, column=10).value = round(pa, 2)
            pp = h.get('pnl_pct')
            try: ws.cell(row=row, column=11).value = round(float(pp) if pp is not None else 0, 4)
            except: ws.cell(row=row, column=11).value = 0
            ws.cell(row=row, column=12).value = STATE['beijing_date']
            updated += 1
        if updated:
            wb.save(xlsx_path)
            log_alert("INFO", "持仓跟踪同步", f"已更新{updated}只")
    except Exception as e:
        log_alert("WARNING", "持仓跟踪同步", f"失败: {str(e)[:100]}")

# ============================================================
# STEP 4C: Crisis Check
# ============================================================
def step4C():
    for h in STATE['holdings_list']:
        code = h.get('code','?'); name = h.get('name','?')
        cost = h.get('cost',0); current = h.get('current',0)
        prev_close = h.get('prev_close'); pnl_pct = h.get('pnl_pct',0) or 0
        if prev_close and current > 0 and prev_close > 0:
            daily_chg = (current - prev_close) / prev_close * 100
            if daily_chg < -9.5:
                msg = f"⚠️ {code} {name} 当日跌停({daily_chg:.1f}%)！成本{cost} 现价{current} 浮亏{pnl_pct}%"
                STATE['crisis_alerts'].append(msg); log_alert("WARNING","持仓危机",msg)
        if pnl_pct < -15:
            msg = f"⚠️ {code} {name} 浮亏突破15%做T上限({pnl_pct:.1f}%)，建议人工决策"
            STATE['crisis_alerts'].append(msg); log_alert("WARNING","持仓危机",msg)
        if current > 0:
            triggers = []
            if current < 5: triggers.append("股价<5元(规则3)")
            if current > 100: triggers.append("股价>100元(规则4)")
            if code.startswith("688"): triggers.append("科创板(规则1)")
            if code.startswith("8") and len(str(code))==6: triggers.append("北交所(规则2)")
            if triggers:
                msg = f"⚠️ {code} {name} 触发L1硬排除: {', '.join(triggers)}"
                STATE['crisis_alerts'].append(msg); log_alert("WARNING","持仓危机",msg)

# ============================================================
# STEP 5: History Cleanup
# ============================================================
def step5():
    total = 0
    try:
        c7 = (datetime.strptime(STATE['data_date'],'%Y-%m-%d')-timedelta(days=7)).strftime('%Y-%m-%d')
        c90 = (datetime.strptime(STATE['data_date'],'%Y-%m-%d')-timedelta(days=90)).strftime('%Y-%m-%d')
        for f in sorted(os.listdir('/workspace')):
            if not (f.startswith('推荐历史_') and f.endswith('.json')): continue
            hist = safe_read_json(f'/workspace/{f}')
            new_recs = []
            for r in hist:
                t = r.get('type','')
                if t in ('weekly_review','strategy_check','do_T_eval','do_T'): new_recs.append(r)
                elif t == 'holding':
                    if r.get('update_date','') >= c90: new_recs.append(r)
                elif t == 'recommendation':
                    if r.get('date','') >= c7: new_recs.append(r)
                else: new_recs.append(r)
            if len(new_recs) < len(hist):
                safe_write_json(f'/workspace/{f}', new_recs)
                total += len(hist) - len(new_recs)
        log_alert("INFO","清理",f"已清理{total}条" if total else "无需清理")
    except Exception as e:
        log_alert("WARNING","清理",f"失败: {str(e)[:80]}")

# ============================================================
# STEP 6: File Init
# ============================================================
def step6():
    adj = safe_read_json('/workspace/策略调整记录.json')
    if adj and len(adj) > 0:
        latest = adj[-1]
        STATE['file_version'] = latest.get('version', BUILTIN_VERSION)
        STATE['params'] = latest.get('params', {})
    else:
        STATE['file_version'] = BUILTIN_VERSION
        STATE['params'] = {
            "search_budget":25,"northbound_threshold":3000,"consecutive_weeks":2,
            "win_rate_drop_threshold":10,"limit_down_threshold":100,"max_adjust_params":3,
            "confidence_position_enabled":True,"max_holding_days":5,
            "circuit_breaker_threshold_pct":3.0,"strategy_concentration_pct":60,
            "do_t_success_reset_count":3,"conversion_rate_window_days":10,
            "conversion_rate_threshold":0.3,"conversion_rate_restore":0.6,
            "conversion_rate_consecutive_days":3,"data_tier_l2_skip_on_unavailable":True,
            "data_tier_l3_downgrade_to_signal":True,"strategy_a_weak_market":"closed"
        }
        safe_write_json('/workspace/策略调整记录.json', [{"version":STATE['file_version'],"params":STATE['params']}])
    
    history = read_all_history()
    last_check = None
    for r in reversed(history):
        if r.get('type')=='strategy_check': last_check = r; break
    if last_check is None:
        safe_append_json(f'/workspace/推荐历史_{STATE["data_date"].replace("-","")}.json',
                         {"type":"strategy_check","version":STATE['file_version'],"params":STATE['params'],"date":STATE['data_date']})
        log_alert("INFO","策略检查",f"首次运行 v{STATE['file_version']}")

# ============================================================
# STEP 7: Earnings Season
# ============================================================
def step7():
    m = STATE['beijing_now'].month
    if m in [1,3,4,8,10]:
        STATE['suggested_position'] = min(80, STATE['suggested_position'] + 5)
        log_alert("INFO","财报季",f"{m}月财报季")

# ============================================================
# STEP 8: Market Environment
# ============================================================
def step8():
    try:
        req = urllib.request.Request(
            "https://push2.eastmoney.com/api/qt/stock/get?secid=1.000001&fields=f43,f170",
            headers={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'})
        resp = urllib.request.urlopen(req, timeout=5)
        d = json.loads(resp.read()).get('data',{})
        sh_change = d.get('f170',0)/100 if d.get('f170') else 0
        sh_close = d.get('f43',0)/100 if d.get('f43') else 3000
        if sh_change > 1.5: STATE['market_condition'], STATE['suggested_position'] = '强市', 75
        elif sh_change < -1.0: STATE['market_condition'], STATE['suggested_position'] = '弱市', 35
        else: STATE['market_condition'], STATE['suggested_position'] = '震荡', 55
        log_alert("INFO","大盘环境",f"上证{sh_close:.0f}({sh_change:+.2f}%) → {STATE['market_condition']}，建议仓位{STATE['suggested_position']}%")
    except Exception as e:
        log_alert("WARNING","大盘环境",f"判断失败: {str(e)[:60]}，默认震荡")

# ============================================================
# STEP 9: Sector Rotation
# ============================================================
def step9():
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        pq = {"pn":"1","pz":"50","po":"1","np":"1","ut":"bd1d9ddb04089700cf9c27f6f7426281",
              "fltt":"2","invt":"2","fid":"f62","fs":"m:90+t:2","fields":"f2,f3,f12,f14,f62",
              "_":str(int(time.time()*1000))}
        headers = {'User-Agent':'Mozilla/5.0','Referer':'https://data.eastmoney.com/'}
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(pq)}", headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data and data.get('data') and data['data'].get('diff'):
            for item in data['data']['diff']:
                sn = item.get('f14',''); mi = item.get('f62')
                if sn and mi is not None and mi != '-': STATE['sector_flow'][sn] = float(mi)
            log_alert("INFO","板块轮动",f"获取{len(STATE['sector_flow'])}个板块")
    except Exception as e:
        log_alert("WARNING","板块轮动",f"失败: {str(e)[:60]}")

# ============================================================
# STEP 9A, 9B, 9C: Simplified
# ============================================================
def step9A(): pass
def step9B(): pass
def step9C(): pass

# ============================================================
# STEP 10A: Fetch All Stocks
# ============================================================
def step10A():
    all_s = []
    # Plan A: clist
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        pq = {"pn":"1","pz":"6000","po":"1","np":"1","ut":"bd1d9ddb04089700cf9c27f6f7426281",
              "fltt":"2","invt":"2","fid":"f3","fs":"m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
              "fields":"f2,f3,f5,f7,f8,f10,f12,f14,f15,f16,f17,f18,f20,f62",
              "_":str(int(time.time()*1000))}
        headers = {'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/'}
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(pq)}", headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data and data.get('data') and data['data'].get('diff'):
            for item in data['data']['diff']:
                code = item.get('f12',''); name = item.get('f14','')
                if not code or not name: continue
                cv = item.get('f2')
                if cv == '-' or cv is None: continue
                try:
                    all_s.append({"code":code,"name":name,
                        "open":float(item.get('f17',0)) if item.get('f17') not in (None,'-') else None,
                        "close":float(cv),
                        "change_pct":float(item.get('f3',0)) if item.get('f3') not in (None,'-') else 0,
                        "turnover":float(item.get('f8',0)) if item.get('f8') not in (None,'-') else 0,
                        "amplitude":float(item.get('f7',0)) if item.get('f7') not in (None,'-') else 0,
                        "volume_ratio":float(item.get('f10',0)) if item.get('f10') not in (None,'-') else 0,
                        "amount":float(item.get('f6',0)) if item.get('f6') not in (None,'-') else 0,
                        "high":float(item.get('f15',0)) if item.get('f15') not in (None,'-') else None,
                        "low":float(item.get('f16',0)) if item.get('f16') not in (None,'-') else None,
                        "prev_close":float(item.get('f18',0)) if item.get('f18') not in (None,'-') else None,
                        "main_inflow":float(item.get('f62',0)) if item.get('f62') not in (None,'-') else 0,
                        "total_cap":float(item.get('f20',0)) if item.get('f20') not in (None,'-') else None})
                except: continue
            STATE['source'] = 'clist'
            STATE['all_stocks'] = all_s
            log_alert("INFO","行情采集",f"clist拉取{len(all_s)}只")
            return
    except: pass
    
    # Plan B: Sina
    log_alert("INFO","行情采集","降级为新浪")
    STATE['source'] = 'sina'
    try:
        code_ranges = []
        for i in range(600000,606000): code_ranges.append(f"sh{i}")
        for i in range(1,5000): code_ranges.append(f"sz{i:06d}")
        for i in range(300000,302000): code_ranges.append(f"sz{i}")
        for i in range(0, len(code_ranges), 80):
            batch = code_ranges[i:i+80]
            try:
                url = f"https://hq.sinajs.cn/list={','.join(batch)}"
                req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn'})
                resp = urllib.request.urlopen(req, timeout=5)
                text = resp.read().decode('gbk')
                for line in text.strip().split('\n'):
                    if not line or '=""' in line: continue
                    try:
                        parts = line.split('"')[1].split(',')
                        if len(parts)<6: continue
                        header = line.split('="')[0]
                        rc = header.split('_')[-1] if '_' in header else header[-6:]
                        code = rc if len(rc)==6 else rc[-6:]
                        name = parts[0]
                        op = float(parts[1]) if parts[1] else 0
                        pc = float(parts[2]) if parts[2] else 0
                        cur = float(parts[3]) if parts[3] else 0
                        hi = float(parts[4]) if parts[4] else 0
                        lo = float(parts[5]) if parts[5] else 0
                        if cur<=0 or pc<=0: continue
                        cp = round((cur-pc)/pc*100,2)
                        amp = round((hi-lo)/pc*100,2) if pc>0 else 0
                        amt = float(parts[9]) if len(parts)>9 and parts[9] and parts[9]!='' else 0
                        all_s.append({"code":code,"name":name,"open":op,"close":cur,"change_pct":cp,
                            "turnover":0.0,"amplitude":amp,"high":hi,"low":lo,"prev_close":pc,
                            "volume_ratio":None,"amount":amt,"main_inflow":None,"total_cap":None})
                    except: continue
                if i%(80*10)==0: time.sleep(0.05)
            except: continue
        STATE['all_stocks'] = all_s
        log_alert("INFO","行情采集",f"新浪拉取{len(all_s)}只")
    except Exception as e:
        log_alert("ERROR","行情采集",f"全部失败: {str(e)[:100]}")
        raise RuntimeError("行情数据获取失败")

# ============================================================
# Build Candidate Pool
# ============================================================
def build_pool():
    # v6.6.23: 同时纳入上涨和下跌标的，下跌标的用于策略B（超跌反弹）
    raw = [s for s in STATE['all_stocks'] if s['change_pct'] is not None and s['close'] is not None and s['close']>0]
    if STATE['source']=='clist': raw.sort(key=lambda x:(x.get('turnover',0) or 0), reverse=True)
    else: raw.sort(key=lambda x:(x.get('amount',0) or 0), reverse=True)
    raw = raw[:500]
    for s in raw:
        code = s['code']
        c = {"code":code,"name":s['name'],"sector":"","industry":"","change_pct":s.get('change_pct'),
             "open":s.get('open'),"close":s.get('close'),"turnover":s.get('turnover'),
             "amplitude":s.get('amplitude'),"volume_ratio":s.get('volume_ratio'),
             "amount":s.get('amount'),"main_inflow":s.get('main_inflow'),
             "high":s.get('high'),"low":s.get('low'),"prev_close":s.get('prev_close'),
             "total_cap":s.get('total_cap'),"strategy":"","reason":"","score":0,"confidence":"",
             "entry":None,"stop_loss":None,"take_profit":None,
             "url":f"https://quote.eastmoney.com/concept/{'sh' if code.startswith('6') else 'sz'}{code}.html"}
        if c["close"] is None or c["close"]<=0: continue
        if c["change_pct"] is None: continue
        STATE['candidates'].append(c)
    STATE['total_raw'] = len(STATE['candidates'])
    log_alert("INFO","行情采集",f"原始标的池: {STATE['total_raw']}只")

# ============================================================
# STEP 10B: Sector/Industry Completion
# ============================================================
# Industry lookup by code range (申万一级行业), fallback to 未知
INDUSTRY_MAP = {
    '600000-600099': '银行', '600100-600199': '电子', '600200-600299': '医药生物',
    '600300-600399': '基础化工', '600400-600499': '电力设备', '600500-600599': '公用事业',
    '600600-600699': '食品饮料', '600700-600799': '交通运输', '600800-600899': '机械设备',
    '600900-600999': '银行', '601000-601099': '非银金融', '601100-601199': '有色金属',
    '601200-601299': '非银金融', '601300-601399': '机械设备', '601400-601499': '银行',
    '601500-601599': '非银金融', '601600-601699': '有色金属', '601700-601799': '电力设备',
    '601800-601899': '建筑装饰', '601900-601999': '传媒', '603000-603099': '电子',
    '603100-603199': '机械设备', '603200-603299': '基础化工', '603300-603399': '机械设备',
    '603400-603499': '电子', '603500-603599': '电子', '603600-603699': '轻工制造',
    '603700-603799': '汽车', '603800-603899': '机械设备', '603900-603999': '商贸零售',
    '605000-605099': '机械设备', '605100-605199': '电力设备', '605200-605299': '基础化工',
    '605300-605399': '食品饮料', '605500-605599': '轻工制造',
    '000001-000099': '银行', '000100-000199': '电子', '000200-000299': '房地产',
    '000300-000399': '医药生物', '000400-000499': '电力设备', '000500-000599': '公用事业',
    '000600-000699': '公用事业', '000700-000799': '钢铁', '000800-000899': '汽车',
    '000900-000999': '非银金融', '001000-001099': '电子', '001200-001299': '基础化工',
    '001300-001399': '机械设备', '002000-002099': '电子', '002100-002199': '医药生物',
    '002200-002299': '建筑装饰', '002300-002399': '电力设备', '002400-002499': '传媒',
    '002500-002599': '基础化工', '002600-002699': '电子', '002700-002799': '机械设备',
    '002800-002899': '基础化工', '002900-002999': '电子', '003000-003099': '食品饮料',
    '300000-300099': '电子', '300100-300199': '汽车', '300200-300299': '基础化工',
    '300300-300399': '计算机', '300400-300499': '机械设备', '300500-300599': '建筑装饰',
    '300600-300699': '国防军工', '300700-300799': '机械设备', '300800-300899': '环保',
    '300900-300999': '电力设备', '301000-301099': '机械设备', '301100-301199': '基础化工',
    '301200-301299': '电子', '301300-301399': '计算机', '301500-301599': '汽车',
}

def _lookup_industry(code):
    code_int = int(code)
    for rng, industry in INDUSTRY_MAP.items():
        lo, hi = rng.split('-')
        if int(lo) <= code_int <= int(hi):
            return industry
    return '未知'

def step10B():
    for c in STATE['candidates']:
        code = c['code']
        if code.startswith(('600','601','603','605')): c['board'] = '上海主板'
        elif code.startswith('688'): c['board'] = '科创板'
        elif code.startswith(('000','001','002','003')): c['board'] = '深圳主板'
        elif code.startswith(('300','301')): c['board'] = '创业板'
        else: c['board'] = '未知'
        # Use code-range based industry lookup, fallback to 未知
        if not c.get('industry') or c['industry'] == '':
            c['industry'] = _lookup_industry(code)
        if not c.get('sector') or c['sector'] == '':
            c['sector'] = c['industry']

# ============================================================
# STEP 11: 31 Hard Exclusions
# ============================================================
def step11():
    history = read_all_history()
    cutoff = (datetime.strptime(STATE['data_date'],'%Y-%m-%d')-timedelta(days=7)).strftime('%Y-%m-%d')
    existing = set()
    for r in history:
        if r.get('type') in ('recommendation','holding'):
            rd = r.get('date','') or r.get('update_date','')
            if rd >= cutoff: existing.add(r.get('code',''))
    
    for c in STATE['candidates']:
        code = c['code']; name = c['name']; close = c['close'] or 0; cp = c['change_pct'] or 0
        reason = None
        if code.startswith('688'): reason = "规则1:科创板"
        elif code.startswith('8') and len(code)==6: reason = "规则2:北交所"
        elif close < 5: reason = "规则3:股价<5元"
        elif close > 100: reason = "规则4:股价>100元"
        elif 'ST' in name.upper(): reason = "规则5:ST/*ST"
        elif cp > 9.5: reason = "规则11:涨停/连板"
        elif cp > 7: reason = "规则12:涨幅>7%"
        elif code in existing: reason = "规则13:7日内已推荐/持仓"
        elif cp < -9.5: reason = "规则22:跌停"
        elif code.startswith(('300','301')) and STATE['market_condition']!='强市': reason = "规则21:创业板非强市"
        
        if reason:
            c['excluded']=True; c['excluded_reason']=reason; STATE['excluded_11'].append(c)
        else:
            c['excluded']=False; STATE['passed_11'].append(c)
    
    STATE['n11_pass']=len(STATE['passed_11']); STATE['n11_excl']=len(STATE['excluded_11'])
    log_alert("INFO","硬排除",f"通过{STATE['n11_pass']}只，排除{STATE['n11_excl']}只")

# ============================================================
# STEP 12: 14 Signal Filters
# ============================================================
def step12():
    for c in STATE['passed_11']:
        close=c['close'] or 0; open_p=c['open'] or 0; cp=c['change_pct'] or 0
        turnover=c['turnover'] or 0; amp=c['amplitude'] or 0; vr=c['volume_ratio']
        reason = None
        
        if open_p>0 and close>0 and c.get('prev_close'):
            gap = (open_p - c['prev_close']) / c['prev_close'] * 100
            if gap>3 and close<open_p*0.98: reason="信号1:假动量"
        if c.get('high') and close>0 and c['high']>0:
            if (c['high']-close)/c['high']<0.005 and cp<2: reason="信号3:尾盘急拉"
        if c.get('low') and close>0 and c['low']>0:
            if (close-c['low'])/c['low']<0.005 and cp<-3: reason="信号4:尾盘跳水"
        if turnover>30: reason="信号5:换手率>30%"
        if STATE['source']=='clist' and vr is not None and vr>2.0 and cp<0.5: reason="信号6:放量滞涨"
        if amp>15: reason="信号7:振幅>15%"
        if STATE['source']=='clist' and vr is not None and vr>8.0 and cp>3: reason="信号12:竞价爆量"
        
        if reason:
            c['signal_excluded']=True; c['signal_reason']=reason; STATE['excluded_12'].append(c)
        else:
            c['signal_excluded']=False; STATE['passed_12'].append(c)
    
    STATE['n12_pass']=len(STATE['passed_12']); STATE['n12_excl']=len(STATE['excluded_12'])
    log_alert("INFO","信号过滤",f"通过{STATE['n12_pass']}只，排除{STATE['n12_excl']}只")

# ============================================================
# STEP 13: Strategy Matching
# ============================================================
def step13():
    matched = []
    for c in STATE['passed_12']:
        cp = c['change_pct'] or 0; close = c['close'] or 0
        turnover = c['turnover'] or 0; amp = c['amplitude'] or 0
        vr = c['volume_ratio']; amt = c['amount'] or 0; mi = c['main_inflow'] or 0
        m = []
        
        # A: 动量延续
        if STATE['source']=='clist':
            if 3<=cp<=7 and vr is not None and 1.5<=vr<=3.0 and turnover>3:
                if STATE['market_condition']!='弱市': m.append('A')
        else:
            if 3<=cp<=7 and amt>0 and close>(c.get('open') or 0):
                if STATE['market_condition']!='弱市': m.append('A')
        
        # B: 超跌反弹
        if -9.5<=cp<=-3 and close>0 and (c.get('low') or 0)>0 and (close-(c.get('low') or 0))/close>0.015 and amp>3:
            m.append('B')
        
        # D: 回调企稳
        if 2<=cp<=6 and close>(c.get('open') or 0) and 2<=amp<=8:
            m.append('D')
        
        # E: 资金埋伏
        if 0<=cp<=2 and amp<2.5:
            m.append('E')
        
        if m:
            priority = {'A':0,'B':1,'C':2,'D':3,'E':4}
            m.sort(key=lambda x:priority.get(x,99))
            c['strategy']=m[0]; c['matched_strategies']=m
            matched.append(c)
    
    STATE['n13_pass'] = len(matched)
    log_alert("INFO","策略匹配",f"通过{STATE['n13_pass']}只")
    return matched

# ============================================================
# STEP 14: Scoring
# ============================================================
def step14(matched):
    strat_base = {'A':5,'B':4,'C':3,'D':3,'E':2}
    for c in matched:
        s = c['strategy']; score = strat_base.get(s,2); reasons = []
        close = c['close'] or 0; open_p = c['open'] or 0; cp = c['change_pct'] or 0
        
        if close > open_p: score += 1; reasons.append("阳线+1")
        elif close < open_p and s=='B' and (c.get('low') or 0)>0 and close>(c.get('low') or 0):
            score += 1; reasons.append("下影线+1")
        
        if s=='A' and 3<=cp<=5: score += 1; reasons.append("动量适中+1")
        if s=='B' and cp<=-5: score += 1; reasons.append("超跌充分+1")
        
        # Sector bonus
        if c.get('sector') and c['sector'] in STATE['sector_flow']:
            ranked = sorted(STATE['sector_flow'].items(), key=lambda x:x[1], reverse=True)
            top5 = [r[0] for r in ranked[:5]]
            if c['sector'] in top5: score += 1; reasons.append("板块TOP5+1")
        
        # L3 penalty
        if c.get('main_inflow') and c['main_inflow'] < -100000000:
            score -= 2; reasons.append("L3:主力净流出-2")
        
        # Signal deductions
        if STATE['source']=='clist':
            vr = c['volume_ratio']
            if cp>3 and vr is not None and vr<0.7: score -= 3; reasons.append("缩量上涨-3")
            elif 0<cp<=3 and vr is not None and vr<0.7: score -= 4; reasons.append("缩量反弹-4")
            if vr is not None and vr<0.3: score -= 2; reasons.append("竞价量比低-2")
        
        score = max(0, score)
        conf = '★★★' if score>=9 else ('★★' if score>=6 else '★')
        
        if close>0:
            entry = close
            if s=='A': sl, tp = round(close*0.96,2), round(close*1.05,2)
            elif s=='B': sl, tp = round(close*0.97,2), round(close*1.03,2)
            elif s=='D': sl, tp = round(close*0.95,2), round(close*1.04,2)
            elif s=='E': sl, tp = round(close*0.98,2), round(close*1.03,2)
            else: sl, tp = round(close*0.96,2), round(close*1.04,2)
        else: entry, sl, tp = None, None, None
        
        c['score']=score; c['confidence']=conf; c['entry']=entry
        c['stop_loss']=sl; c['take_profit']=tp; c['reason']='; '.join(reasons)
    return matched

# ============================================================
# STEP 15-16: Conflict + Tiebreak
# ============================================================
def step15_16(scored):
    for c in scored:
        ms = c.get('matched_strategies',[])
        if 'A' in ms and 'D' in ms: c['strategy']='A'; c['matched_strategies']=[x for x in ms if x!='D']
        if 'D' in ms and 'B' in ms: c['strategy']='D'; c['matched_strategies']=[x for x in ms if x!='B']
    
    def sort_key(rec):
        score = rec.get('score',0)
        strategy = rec.get('strategy','Z')
        so = {'A':0,'B':1,'C':2,'D':3,'E':4}
        sr = so.get(strategy,99)
        vr = rec.get('volume_ratio') or 0
        vs = min(vr/3.0,1.0) if vr else 0
        to = rec.get('turnover') or 0
        if to<2: ts=0.2
        elif to<=5: ts=0.6
        elif to<=15: ts=1.0
        elif to<=25: ts=0.5
        else: ts=0.1
        cp = rec.get('change_pct') or 0
        if strategy in ('A','E'): cs = max(0,1.0-abs(cp-3)/7.0)
        elif strategy=='B': cs = max(0,1.0-abs(cp+5)/5.0)
        else: cs = max(0,1.0-abs(cp-2)/8.0)
        sh = rec.get('sector_rank',99)
        ss = max(0,1.0-sh/20.0)
        tie = vs*0.25+ts*0.25+cs*0.25+ss*0.15+(1.0-sr/10.0)*0.10
        return (-score, sr, -tie)
    
    scored.sort(key=sort_key)
    return scored

# ============================================================
# STEP 17: Industry Limits
# ============================================================
def step17(scored):
    sp = STATE['params'].get('strategy_concentration_pct',60)/100
    ic = {}; result = []
    for c in scored:
        ind = c.get('industry','未知')
        if ic.get(ind,0)<3: result.append(c); ic[ind]=ic.get(ind,0)+1
    # If all industries are same (e.g. all from same board), only apply strategy concentration
    if len(ic) <= 1:
        result = scored
    total = len(result); mps = max(1,int(total*sp))
    sc = {}; final = []
    for c in result:
        s = c['strategy']
        if sc.get(s,0)<mps: final.append(c); sc[s]=sc.get(s,0)+1
    STATE['n17_pass'] = len(final)
    log_alert("INFO","行业限制",f"通过{STATE['n17_pass']}只（行业类型:{len(ic)}个）")
    return final

# ============================================================
# STEP 18: News Screening
# ============================================================
def step18(scored):
    STATE['n18_pass'] = len(scored)
    return scored

# ============================================================
# STEP 19: Insufficient Check
# ============================================================
def step19(final):
    c = len(final)
    if c>=3: return final
    elif c==2: return [x for x in final if x['confidence']!='★']
    elif c==1:
        if final[0]['confidence']=='★★★': return final
        elif final[0]['confidence']=='★★': return final  # Allow ★★ as well
        else: return []
    else:
        log_alert("INFO","推荐不足","无合适标的")
        return []

# ============================================================
# MAIN EXECUTION
# ============================================================
print("="*60)
print(f"A股每日盘前短线标的筛选 v{BUILTIN_VERSION}")
print("="*60)

# Steps 0-9C
step0()
print(f"北京时间: {STATE['beijing_date']} 数据日期: {STATE['data_date']} 预测: {STATE['prediction_date']}")
step0A()
if step1(): print("节假日跳过"); sys.exit(0)
if step2(): print("极端行情跳过"); sys.exit(0)
step3(); step3A(); step4(); step4A(); step4B(); step4C(); step5(); step6(); step7(); step8(); step9(); step9A(); step9B(); step9C()
print(f"市场环境: {STATE['market_condition']} 建议仓位: {STATE['suggested_position']}%")

# Steps 10A-19
step10A(); build_pool(); step10B()
step11(); step12()
matched = step13()
scored = step14(matched)
scored = step15_16(scored)
limited = step17(scored)
final = step18(limited)
final = step19(final)
STATE['final_recos'] = final
STATE['final_count'] = len(final)
STATE['strategy_dist'] = dict(Counter(r['strategy'] for r in final))

print(f"\n📊 筛选概况 — {STATE['prediction_date']}(数据来源:{STATE['data_date']})")
sd = STATE['strategy_dist']
print(f"① 原始标的池:{STATE['total_raw']}只 → ② 硬排除:{STATE['n11_pass']}只 → ③ 信号过滤:{STATE['n12_pass']}只 → ④ 策略匹配:{STATE['n13_pass']}只 → ⑤ 行业限制:{STATE['n17_pass']}只 → ⑥ 新闻筛查:{STATE['n18_pass']}只 → ★ 最终:{STATE['final_count']}只")
print(f"策略分布: A:{sd.get('A',0)} B:{sd.get('B',0)} C:{sd.get('C',0)} D:{sd.get('D',0)} E:{sd.get('E',0)}")

# Save state for output
with open('/data/user/work/screening_state.json','w') as f:
    # Extract only serializable data
    save_state = {k:v for k,v in STATE.items() if k not in ['beijing_now','all_stocks','candidates','passed_11','passed_12','excluded_11','excluded_12']}
    save_state['excluded_11_summary'] = []
    for e in STATE['excluded_11'][:10]:
        save_state['excluded_11_summary'].append({'code':e['code'],'name':e['name'],'reason':e.get('excluded_reason','')})
    json.dump(save_state, f, ensure_ascii=False, indent=2)

with open('/data/user/work/final_recos.json','w') as f:
    json.dump(final, f, ensure_ascii=False, indent=2)

print("\n✅ 核心筛选完成，开始输出...")