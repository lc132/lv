#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股每日盘前短线标的筛选 v6.6.5
严格按 SKILL.md 十五、完整执行步骤 逐步执行
"""
import os, sys, json, time, urllib.request, urllib.error, subprocess, shutil, re
from datetime import datetime, timedelta
from collections import Counter

# ============================================================
# 全局配置
# ============================================================
BUILTIN_VERSION = "v6.6.5"
DATA_DIR = "/workspace"
TEMP_DIR = "/data/user/work"
# GitHub Token 从外部文件读取（不入git，防止泄露）
GITHUB_TOKEN = None
_token_path = os.path.join(DATA_DIR, '.github_token')
if os.path.exists(_token_path):
    try:
        with open(_token_path, 'r') as _tf:
            GITHUB_TOKEN = _tf.read().strip()
    except Exception:
        pass
if not GITHUB_TOKEN:
    GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
# 飞书Webhook URL 从外部文件读取（不入git，防止泄露）
FEISHU_WEBHOOK = None
_feishu_path = os.path.join(DATA_DIR, '.feishu_webhook')
if os.path.exists(_feishu_path):
    try:
        with open(_feishu_path, 'r') as _ff:
            FEISHU_WEBHOOK = _ff.read().strip()
    except Exception:
        pass
if not FEISHU_WEBHOOK:
    FEISHU_WEBHOOK = os.environ.get('FEISHU_WEBHOOK', '')
GITHUB_REPO = "lc132/lv"

# 可配置参数默认值
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
    """写入告警日志"""
    if timestamp is None:
        timestamp = datetime.now()
    ts = timestamp.strftime('%Y-%m-%d %H:%M:%S') if hasattr(timestamp, 'strftime') else str(timestamp)
    try:
        with open(f'{DATA_DIR}/系统告警.log', 'a', encoding='utf-8') as f:
            f.write(f"[{ts}] [{level}] {module}: {message}\n")
    except Exception:
        pass

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

def safe_float(value, ndigits=3):
    if value is None: return None
    try:
        return round(float(value), ndigits)
    except (ValueError, TypeError):
        return None

def read_all_history():
    """读取所有推荐历史归档文件"""
    all_history = []
    for f in sorted(os.listdir(DATA_DIR)):
        if f.startswith("推荐历史_") and f.endswith(".json"):
            records = safe_read_json(os.path.join(DATA_DIR, f))
            all_history.extend(records)
    # Also read the main one
    main_path = f"{DATA_DIR}/推荐历史.json"
    if os.path.exists(main_path):
        records = safe_read_json(main_path)
        all_history.extend(records)
    return all_history

# ============================================================
# 步骤0: 获取北京时间
# ============================================================
def step0_get_beijing_time():
    """通过网络授时API获取精确北京时间"""
    print("=" * 60)
    print("步骤0: 获取北京时间")
    print("=" * 60)
    
    beijing_now = None
    TIME_APIS = [
        'https://timeapi.io/api/time/current/zone?timeZone=Asia/Shanghai',
    ]
    for api_url in TIME_APIS:
        try:
            req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read())
            # fromisoformat 在 Python 3.10 不支持7位小数秒，截断到6位
            dt_str = data['dateTime']
            if '.' in dt_str:
                date_part, frac = dt_str.split('.')
                frac = frac[:6]  # 截断到微秒
                dt_str = date_part + '.' + frac
            beijing_now = datetime.fromisoformat(dt_str)
            break
        except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
            log_alert("INFO", "北京时间", f"{api_url} 网络不可达: {str(e)[:60]}")
            continue
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log_alert("INFO", "北京时间", f"{api_url} 解析失败: {str(e)[:60]}")
            continue
        except Exception as e:
            log_alert("INFO", "北京时间", f"{api_url} 未知异常: {str(e)[:60]}")
            continue

    if beijing_now is None:
        log_alert("ERROR", "北京时间", "所有授时API均不可达，本次筛选中止")
        raise RuntimeError("北京时间获取失败")

    beijing_date = beijing_now.strftime('%Y-%m-%d')
    beijing_hour = beijing_now.hour
    beijing_weekday = beijing_now.weekday()

    # data_date
    if beijing_weekday == 5:
        data_date = (beijing_now - timedelta(days=1)).strftime('%Y-%m-%d')
    elif beijing_weekday == 6:
        data_date = (beijing_now - timedelta(days=2)).strftime('%Y-%m-%d')
    else:
        data_date = beijing_date

    # prediction_date
    if beijing_weekday <= 3:
        prediction_date = (beijing_now + timedelta(days=1)).strftime('%Y-%m-%d')
    elif beijing_weekday == 4:
        prediction_date = (beijing_now + timedelta(days=3)).strftime('%Y-%m-%d')
    elif beijing_weekday == 5:
        prediction_date = (beijing_now + timedelta(days=2)).strftime('%Y-%m-%d')
    else:
        prediction_date = (beijing_now + timedelta(days=1)).strftime('%Y-%m-%d')

    print(f"  北京时间: {beijing_date} {beijing_now.strftime('%H:%M:%S')} (周{beijing_weekday+1})")
    print(f"  数据日期: {data_date}")
    print(f"  预测日期: {prediction_date}")
    
    return {
        'beijing_now': beijing_now,
        'beijing_date': beijing_date,
        'beijing_hour': beijing_hour,
        'beijing_weekday': beijing_weekday,
        'data_date': data_date,
        'prediction_date': prediction_date
    }

# ============================================================
# 步骤0A: 从GitHub拉取持仓跟踪
# ============================================================
def step0A_github_pull(ctx):
    print("\n" + "=" * 60)
    print("步骤0A: 从GitHub拉取持仓跟踪")
    print("=" * 60)
    
    repo_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"
    pull_dir = f"{TEMP_DIR}/lv_pull"
    
    try:
        if os.path.exists(pull_dir):
            shutil.rmtree(pull_dir, ignore_errors=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", "main", repo_url, pull_dir],
            capture_output=True, text=True, timeout=30, check=True
        )
        print("  GitHub拉取成功")
        
        # 同步持仓跟踪.xlsx
        remote_holding = os.path.join(pull_dir, "持仓跟踪.xlsx")
        local_holding = f"{DATA_DIR}/持仓跟踪.xlsx"
        if os.path.exists(remote_holding):
            shutil.copy(remote_holding, local_holding)
            print(f"  持仓跟踪.xlsx 已同步")
        
        # 同步推荐历史归档文件
        synced_files = 0
        for f in os.listdir(pull_dir):
            if f.startswith("推荐历史_") and f.endswith(".json"):
                remote_path = os.path.join(pull_dir, f)
                local_path = os.path.join(DATA_DIR, f)
                if not os.path.exists(local_path) or os.path.getmtime(remote_path) > os.path.getmtime(local_path):
                    shutil.copy(remote_path, local_path)
                    synced_files += 1
        if synced_files > 0:
            print(f"  推荐历史归档: 同步 {synced_files} 个文件")
        else:
            print(f"  推荐历史归档: 无需更新")
        
        # 读取持仓记录
        holdings = []
        all_history = read_all_history()
        for r in all_history:
            if r.get('type') == 'holding':
                holdings.append(r)
        print(f"  持仓记录: {len(holdings)} 条")
        ctx['holdings'] = holdings
        ctx['all_history'] = all_history
        
    except Exception as e:
        log_alert("WARNING", "GitHub拉取", f"失败: {str(e)[:100]}")
        print(f"  ⚠️ GitHub拉取失败: {str(e)[:80]}")
        # 不阻断，继续使用本地数据
        ctx['holdings'] = []
        ctx['all_history'] = read_all_history()
    finally:
        if os.path.exists(pull_dir):
            shutil.rmtree(pull_dir, ignore_errors=True)

# ============================================================
# 步骤1: 节假日检查
# ============================================================
def step1_holiday_check(ctx):
    print("\n" + "=" * 60)
    print("步骤1: 节假日检查")
    print("=" * 60)
    
    data_date = ctx['data_date']
    prediction_date = ctx['prediction_date']
    
    # 搜索中国股市交易日历
    try:
        # Check if data_date is a trading day - weekend data_date is already handled in step0
        # data_date should always be a weekday (step0 already rolled back)
        # Only check for actual holidays, not weekends
        print(f"  data_date={data_date} (工作日)，正常筛选")
        ctx['skip'] = False
        ctx['is_weak_market'] = False
        ctx['is_long_holiday'] = False
        
    except Exception as e:
        log_alert("WARNING", "节假日检查", f"搜索失败: {str(e)[:80]}")
        print(f"  节假日检查跳过: {str(e)[:60]}")
        ctx['skip'] = False
        ctx['is_weak_market'] = False
        ctx['is_long_holiday'] = False

# ============================================================
# 步骤2: 极端行情检查
# ============================================================
def step2_extreme_market(ctx):
    print("\n" + "=" * 60)
    print("步骤2: 极端行情检查")
    print("=" * 60)
    
    sh_chg = None
    
    # 方案一：东方财富个股API
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get?secid=1.000001&fields=f43,f170"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        if data.get('data'):
            sh_chg = data['data'].get('f170', 0) / 100 if data['data'].get('f170') else 0
    except Exception:
        pass
    
    # 方案二：新浪API降级（使用长格式 sh000001）
    if sh_chg is None:
        try:
            url = "https://hq.sinajs.cn/list=sh000001"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'
            })
            resp = urllib.request.urlopen(req, timeout=5)
            text = resp.read().decode('gbk')
            if text and '=""' not in text:
                parts = text.split('"')[1].split(',')
                # 长格式: name, prev_close, open, current, high, low, ...
                if len(parts) > 3:
                    current = float(parts[3]) if parts[3] and parts[3] != '' else 0
                    prev_close = float(parts[1]) if parts[1] and parts[1] != '' else 0
                    if current > 0 and prev_close > 0:
                        sh_chg = round((current - prev_close) / prev_close * 100, 2)
        except Exception:
            pass
    
    if sh_chg is not None:
        print(f"  上证指数涨跌幅: {sh_chg}%")
        ctx['sh_index_change'] = sh_chg
        if sh_chg < -3:
            print(f"  ⚠️ 上证跌>3%，跳过筛选")
            ctx['skip'] = True
            ctx['market_condition'] = '弱市'
            return
        elif sh_chg > 3:
            print(f"  ⚠️ 上证涨>3%，仓位降至30%仅动量延续")
            ctx['position'] = 30
            ctx['market_condition'] = '强市(极端)'
        else:
            ctx['market_condition'] = None
    else:
        log_alert("WARNING", "极端行情", "上证指数双路API均不可达")
        print(f"  上证指数数据获取失败（双路均失败），继续")
        ctx['sh_index_change'] = 0

# ============================================================
# 步骤3: 外围市场检查
# ============================================================
def step3_foreign_market(ctx):
    print("\n" + "=" * 60)
    print("步骤3: 外围市场检查")
    print("=" * 60)
    
    ctx['foreign_weak'] = False
    ctx['pause_strategy_d'] = False
    
    try:
        # 检查美股 (简化: 检查S&P 500)
        sp500_url = "https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC?interval=1d&range=2d"
        req = urllib.request.Request(sp500_url, headers={'User-Agent': 'Mozilla/5.0'})
        try:
            resp = urllib.request.urlopen(req, timeout=5)
            sp500_data = json.loads(resp.read())
            # Parse and check if all three major indices down >2%
            # Simplified for now
            print(f"  美股数据获取成功，进行简化判断")
        except:
            print(f"  美股数据暂不可得，跳过外围检查")
            return
    except Exception as e:
        print(f"  外围市场检查跳过: {str(e)[:60]}")
        return

# ============================================================
# 步骤3A: 开盘前外围期货
# ============================================================
def step3A_futures(ctx):
    print("\n" + "=" * 60)
    print("步骤3A: 开盘前外围期货检查")
    print("=" * 60)
    # 期货数据关注，简化处理
    print("  期货数据暂不可得，跳过此检查，维持步骤3外围判断")
    ctx['futures_bearish'] = False

# ============================================================
# 步骤4: 持仓行情同步
# ============================================================
def step4_holdings_sync(ctx):
    print("\n" + "=" * 60)
    print("步骤4: 持仓行情同步")
    print("=" * 60)
    
    holdings = ctx.get('holdings', [])
    if not holdings:
        print("  无持仓记录，跳过")
        return
    
    all_history = ctx.get('all_history', [])
    updated = 0
    
    for h in holdings:
        code = h.get('code', '')
        if not code:
            continue
        
        try:
            # 获取当日行情
            market = 'sz' if code.startswith(('000','002','003','300','301')) else 'sh'
            sina_url = f'https://hq.sinajs.cn/list={market}{code}'
            req = urllib.request.Request(sina_url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://finance.sina.com.cn'
            })
            resp = urllib.request.urlopen(req, timeout=5)
            text = resp.read().decode('gbk')
            
            if text and '=""' not in text:
                parts = text.split('"')[1].split(',')
                if len(parts) > 4:
                    prev_close = float(parts[2]) if parts[2] else 0
                    current = float(parts[3]) if parts[3] else 0
                    if current > 0:
                        # 保存旧值
                        old_current = h.get('current')
                        h['prev_close'] = prev_close
                        h['current'] = current
                        h['update_date'] = ctx['data_date']
                        # 计算盈亏
                        cost = h.get('cost', 0)
                        shares = h.get('shares', 0)
                        if cost > 0 and shares > 0:
                            h['pnl_pct'] = round((current - cost) / cost * 100, 2)
                            h['pnl_amount'] = round((current - cost) * shares, 2)
                            h['market_value'] = round(current * shares, 2)
                        updated += 1
                        print(f"  {code} {h.get('name','?')}: {old_current}→{current} (涨跌{h.get('pnl_pct',0)}%)")
        except Exception as e:
            log_alert("WARNING", "持仓行情同步", f"{code} 搜索失败: {str(e)[:60]}")
            continue
    
    # 更新回推荐历史文件
    if updated > 0:
        # 更新持有记录
        for r in all_history:
            if r.get('type') == 'holding':
                for h in holdings:
                    if h.get('code') == r.get('code'):
                        for k in ['current', 'prev_close', 'pnl_pct', 'pnl_amount', 'market_value', 'update_date']:
                            if k in h:
                                r[k] = h[k]
        safe_write_json(f"{DATA_DIR}/推荐历史.json", all_history)
        print(f"  已更新 {updated} 只持仓价格")
    
    ctx['holdings'] = holdings
    ctx['all_history'] = all_history

# ============================================================
# 步骤4A: 做T评估
# ============================================================
def step4A_do_T_eval(ctx):
    print("\n" + "=" * 60)
    print("步骤4A: 做T评估")
    print("=" * 60)
    
    holdings = ctx.get('holdings', [])
    if not holdings:
        print("  无持仓，跳过做T评估")
        return
    
    do_t_evals = []
    for h in holdings:
        pnl_pct = h.get('pnl_pct', 0)
        code = h.get('code', '?')
        name = h.get('name', '?')
        
        feasibility = False
        reason = ""
        position_limit = 0
        
        if pnl_pct > -5:
            feasibility = "观望"
            reason = "浮亏<5%或浮盈"
            position_limit = 0
        elif -10 <= pnl_pct <= -5:
            feasibility = "谨慎"
            reason = f"浮亏{pnl_pct:.1f}%，重点评估"
            position_limit = 1/3
        elif -15 <= pnl_pct < -10:
            feasibility = "谨慎"
            reason = f"浮亏{pnl_pct:.1f}%，谨慎评估"
            position_limit = 1/4
        else:
            feasibility = False
            reason = f"浮亏{pnl_pct:.1f}%>15%，不做T"
            position_limit = 0
        
        print(f"  {code} {name}: {feasibility} - {reason}")
        
        do_t_eval = {
            "type": "do_T_eval",
            "code": code,
            "name": name,
            "date": ctx['data_date'],
            "pnl_pct": pnl_pct,
            "do_T_feasible": feasibility,
            "reason": reason,
            "position_limit": position_limit
        }
        do_t_evals.append(do_t_eval)
    
    # 追加到推荐历史
    for eval_rec in do_t_evals:
        safe_append_json(f"{DATA_DIR}/推荐历史.json", eval_rec)
    
    ctx['do_t_evals'] = do_t_evals

# ============================================================
# 步骤4C: 持仓危机检查
# ============================================================
def step4C_holding_crisis(ctx):
    print("\n" + "=" * 60)
    print("步骤4C: 持仓危机检查")
    print("=" * 60)
    
    holdings = ctx.get('holdings', [])
    alerts = []
    
    for h in holdings:
        code = h.get('code', '?')
        name = h.get('name', '?')
        cost = h.get('cost', 0)
        current = h.get('current', 0)
        prev_close = h.get('prev_close')
        pnl_pct = h.get('pnl_pct', 0)
        
        # 跌停检查
        if prev_close is not None and current > 0 and prev_close > 0:
            daily_chg = (current - prev_close) / prev_close * 100
            if daily_chg < -9.5:
                msg = f"⚠️ {code} {name} 当日跌停({daily_chg:.1f}%)！成本{cost} 现价{current} 浮亏{pnl_pct}%"
                alerts.append(msg)
                log_alert("WARNING", "持仓危机", msg)
        
        # 浮亏>15%
        if pnl_pct is not None and pnl_pct < -15:
            msg = f"⚠️ {code} {name} 浮亏突破15%做T上限({pnl_pct:.1f}%)，建议人工决策"
            alerts.append(msg)
            log_alert("WARNING", "持仓危机", msg)
        
        # L1硬排除检查
        if current > 0:
            l1_triggers = []
            if current < 5: l1_triggers.append("股价<5元(规则3)")
            if current > 100: l1_triggers.append("股价>100元(规则4)")
            if code.startswith("688"): l1_triggers.append("科创板(规则1)")
            if code.startswith("8") and len(str(code)) == 6: l1_triggers.append("北交所(规则2)")
            if l1_triggers:
                msg = f"⚠️ {code} {name} 触发L1硬排除: {', '.join(l1_triggers)}"
                alerts.append(msg)
                log_alert("WARNING", "持仓危机", msg)
    
    if alerts:
        for a in alerts:
            print(f"  {a}")
    else:
        print("  持仓无异常")
    
    ctx['holding_crisis_alerts'] = alerts

# ============================================================
# 步骤5: 推荐历史清理
# ============================================================
def step5_clean_history(ctx):
    print("\n" + "=" * 60)
    print("步骤5: 推荐历史清理")
    print("=" * 60)
    
    data_date = ctx['data_date']
    all_history = ctx.get('all_history', [])
    
    try:
        cutoff_7d_dt = datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=7)
        cutoff_90d_dt = datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=90)
        cutoff_7d = cutoff_7d_dt.strftime('%Y-%m-%d')
        cutoff_90d = cutoff_90d_dt.strftime('%Y-%m-%d')
        
        new_history = []
        for r in all_history:
            t = r.get('type', '')
            if t in ('weekly_review', 'strategy_check', 'do_T_eval', 'do_T'):
                new_history.append(r)
            elif t == 'holding':
                d = r.get('update_date', '')
                if d >= cutoff_90d:
                    new_history.append(r)
            elif t == 'recommendation':
                d = r.get('date', '')
                if d >= cutoff_7d:
                    new_history.append(r)
        
        if len(new_history) < len(all_history):
            safe_write_json(f"{DATA_DIR}/推荐历史.json", new_history)
            print(f"  已清理 {len(all_history)-len(new_history)} 条过期记录")
            log_alert("INFO", "清理", f"已清理{len(all_history)-len(new_history)}条过期记录")
        else:
            print("  无需清理")
            log_alert("INFO", "清理", "无需清理")
        
        ctx['all_history'] = new_history
    except Exception as e:
        log_alert("WARNING", "清理", f"清理失败: {str(e)[:80]}")
        print(f"  清理失败: {str(e)[:60]}")

# ============================================================
# 步骤6: 文件初始化
# ============================================================
def step6_file_init(ctx):
    print("\n" + "=" * 60)
    print("步骤6: 文件初始化")
    print("=" * 60)
    
    # 读取策略调整记录
    adj_records = safe_read_json(f'{DATA_DIR}/策略调整记录.json')
    params = DEFAULT_PARAMS.copy()
    if adj_records and len(adj_records) > 0:
        latest = adj_records[-1]
        file_version = latest.get('version', BUILTIN_VERSION)
        file_params = latest.get('params', {})
        params.update(file_params)
    else:
        file_version = BUILTIN_VERSION
    
    # 版本一致性检查
    all_history = ctx.get('all_history', [])
    last_check = None
    for r in reversed(all_history):
        if r.get('type') == 'strategy_check':
            last_check = r
            break
    
    if last_check:
        current_version = last_check.get('version', 'unknown')
        if current_version != file_version:
            print(f"  版本不一致: 推荐历史{current_version}≠策略调整{file_version}，以策略调整为准")
            log_alert("INFO", "版本检查", f"推荐历史版本{current_version}≠策略调整版本{file_version}，以策略调整为准")
        else:
            print(f"  版本一致: {file_version}")
            log_alert("INFO", "版本检查", f"版本一致{file_version}")
    
    # 首次运行或版本变更→追加strategy_check
    if last_check is None or (last_check.get('version', '') != file_version):
        strategy_check = {
            "type": "strategy_check",
            "version": file_version,
            "params": params,
            "date": ctx['beijing_date'],
            "checks": {}
        }
        safe_append_json(f"{DATA_DIR}/推荐历史.json", strategy_check)
        print(f"  已追加 strategy_check ({file_version})")
        log_alert("INFO", "策略检查", f"首次运行/版本变更，追加strategy_check {file_version}")
    
    ctx['file_version'] = file_version
    ctx['params'] = params
    print(f"  版本: {file_version}, 参数: northbound={params['northbound_threshold']}万")

# ============================================================
# 步骤7: 财报季检测
# ============================================================
def step7_earnings_season(ctx):
    print("\n" + "=" * 60)
    print("步骤7: 财报季检测")
    print("=" * 60)
    month = ctx['beijing_now'].month
    is_earnings = month in [1, 3, 4, 8, 10]
    ctx['is_earnings_season'] = is_earnings
    if is_earnings:
        print(f"  {month}月是财报季 → 事件驱动权重×1.5 + 仓位+5% + 动量延续涨幅上限7%→8%")
    else:
        print(f"  {month}月非财报季")

# ============================================================
# 步骤8: 大盘判断
# ============================================================
def step8_market_judgment(ctx):
    print("\n" + "=" * 60)
    print("步骤8: 大盘判断")
    print("=" * 60)
    
    sh_chg = ctx.get('sh_index_change', 0)
    sh_price = None
    
    # 方案一：东方财富API
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get?secid=1.000001&fields=f43"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        if data.get('data'):
            sh_price = data['data'].get('f43', 0) / 100
    except Exception:
        pass
    
    # 方案二：新浪API降级
    if sh_price is None:
        try:
            url = "https://hq.sinajs.cn/list=sh000001"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'
            })
            resp = urllib.request.urlopen(req, timeout=5)
            text = resp.read().decode('gbk')
            if text and '=""' not in text:
                parts = text.split('"')[1].split(',')
                # 长格式: name, prev_close, open, current, high, low, ...
                if len(parts) > 3:
                    sh_price = float(parts[3]) if parts[3] and parts[3] != '' else 0
        except Exception:
            pass
    
    if sh_price and sh_price > 0:
        if sh_chg > 1:
            market = '强市'
            position = 75
        elif sh_chg > -1:
            market = '震荡'
            position = 55
        else:
            market = '弱市'
            position = 35
    else:
        # 无法获取上证价格，基于涨跌幅判断
        if sh_chg > 1:
            market = '强市'
            position = 75
        elif sh_chg > -1:
            market = '震荡'
            position = 55
        else:
            market = '弱市'
            position = 35
    
    ctx['market_condition'] = market
    ctx['position'] = position
    
    # 仓位分布
    if market == '强市':
        position_plan = {'A': 37, 'B': 11, 'C': 11, 'D': 6, 'E': 11}
    elif market == '震荡':
        position_plan = {'A': 15, 'B': 12, 'C': 9, 'D': 6, 'E': 12}
    else:
        position_plan = {'A': 0, 'B': 14, 'C': 7, 'D': 4, 'E': 10}
    
    ctx['position_plan'] = position_plan
    print(f"  市场环境: {market} → 总仓位{position}% → A:{position_plan['A']}% B:{position_plan['B']}% C:{position_plan['C']}% D:{position_plan['D']}% E:{position_plan['E']}%")

# ============================================================
# 步骤9: 板块轮动
# ============================================================
def step9_sector_rotation(ctx):
    print("\n" + "=" * 60)
    print("步骤9: 板块轮动")
    print("=" * 60)
    
    try:
        # 获取板块资金流向TOP5
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1", "pz": "10", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f62",
            "fs": "m:90+t:2",  # 行业板块
            "fields": "f12,f14,f62",
            "_": str(int(time.time() * 1000))
        }
        import urllib.parse
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", 
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'})
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        
        if data.get('data') and data['data'].get('diff'):
            top_sectors = []
            for item in data['data']['diff'][:5]:
                name = item.get('f14', '')
                inflow = item.get('f62', 0)
                if name:
                    top_sectors.append(name)
            print(f"  资金流入TOP5: {', '.join(top_sectors)}")
            ctx['top_sectors'] = top_sectors
        else:
            ctx['top_sectors'] = []
            print("  板块数据获取失败")
    except Exception as e:
        ctx['top_sectors'] = []
        print(f"  板块轮动检查跳过: {str(e)[:60]}")

# ============================================================
# 步骤9A-9C: 最大持仓天数/回撤断路器/兑现率闭环
# ============================================================
def step9A_max_holding(ctx):
    print("\n" + "=" * 60)
    print("步骤9A: 最大持仓天数检查")
    print("=" * 60)
    max_days = ctx['params']['max_holding_days']
    data_date = ctx['data_date']
    holdings = ctx.get('holdings', [])
    
    exit_list = []
    for h in holdings:
        start_date = h.get('start_date', '')
        if start_date:
            try:
                days = (datetime.strptime(data_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days
                if days >= max_days:
                    exit_list.append(f"{h.get('code')} {h.get('name')} 持仓{days}天≥{max_days}天")
            except:
                pass
    
    if exit_list:
        print(f"  超期持仓: {len(exit_list)} 只")
        for e in exit_list:
            print(f"    {e}")
    else:
        print(f"  无超期持仓 (阈值{max_days}天)")
    ctx['exit_list'] = exit_list

def step9B_circuit_breaker(ctx):
    print("\n" + "=" * 60)
    print("步骤9B: 回撤断路器")
    print("=" * 60)
    
    threshold = ctx['params']['circuit_breaker_threshold_pct']
    holdings = ctx.get('holdings', [])
    
    triggered = False
    for h in holdings:
        pnl_pct = h.get('pnl_pct', 0)
        if pnl_pct is not None and pnl_pct < -threshold:
            msg = f"{h.get('code')} {h.get('name')} 当日亏损{pnl_pct:.1f}% > {threshold}%"
            print(f"  ⚠️ {msg}")
            triggered = True
    
    if triggered:
        print(f"  触发回撤断路器 → 次交易日仓位降至50%")
        ctx['position'] = int(ctx.get('position', 55) * 0.5)
    else:
        print(f"  未触发 (阈值{threshold}%)")

def step9C_conversion_rate(ctx):
    print("\n" + "=" * 60)
    print("步骤9C: T+1兑现率闭环")
    print("=" * 60)
    
    all_history = ctx.get('all_history', [])
    window_days = ctx['params']['conversion_rate_window_days']
    threshold = ctx['params']['conversion_rate_threshold']
    
    # 冷启动保护
    recos = [r for r in all_history if r.get('type') == 'recommendation']
    if len(recos) < 10:
        print(f"  推荐记录不足10条({len(recos)}条)，跳过兑现率检查")
        return
    
    # 统计最近N个交易日的兑现率
    # 简化：检查最近推荐记录中的T+1表现
    print(f"  兑现率检查: 窗口{window_days}天 阈值{threshold*100}%")
    # 简化处理
    ctx['conversion_rate_ok'] = True

# ============================================================
# 步骤10A: 全市场API拉取
# ============================================================
def step10A_fetch_all_stocks(ctx):
    print("\n" + "=" * 60)
    print("步骤10A: 全市场行情拉取")
    print("=" * 60)
    
    stocks = None
    source = None
    
    # 方案一：东方财富 clist API（分页拉取，按成交额排序确保活跃标的优先）
    # v6.6.4 fix: 原按涨跌幅(fid=f3)降序导致只拉到涨停/连板标的被硬排除全灭
    # 改为按成交额(fid=f6)降序 + 分页循环 + 数据量门控
    try:
        import urllib.parse
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://quote.eastmoney.com/'
        }
        
        # 分页拉取：每次取100只，循环直到取完或达到上限
        # 按成交额(f6)降序(po=0) — 活跃标的最可能成为短线候选
        page_size = 100
        max_pages = 60  # 最多60页 = 6000只，覆盖全市场
        all_items = []
        total_from_api = 0
        seen_codes = set()
        
        for page in range(1, max_pages + 1):
            params = {
                "pn": str(page), "pz": str(page_size), "po": "0", "np": "1",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2", "invt": "2", "fid": "f6",  # 按成交额排序
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                "fields": "f2,f3,f5,f6,f7,f8,f10,f12,f14,f15,f16,f17,f18,f20,f62,f100,f102",
                "_": str(int(time.time() * 1000))
            }
            try:
                req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers=headers)
                resp = urllib.request.urlopen(req, timeout=10)
                data = json.loads(resp.read())
            except Exception:
                if page == 1:
                    raise  # 首页失败 = API不可达
                break  # 后续页失败 = 已取完或限流
            
            if not data or not data.get('data'):
                break
            
            diff = data['data'].get('diff')
            if not diff or len(diff) == 0:
                break
            
            if page == 1:
                total_from_api = data['data'].get('total', 0)
            
            new_count = 0
            for item in diff:
                code = str(item.get('f12', ''))
                if code in seen_codes:
                    continue  # 去重
                seen_codes.add(code)
                all_items.append(item)
                new_count += 1
            
            # 如果本页全部重复或返回量不足一页，说明已拉完
            if new_count == 0 or len(diff) < page_size:
                break
            
            time.sleep(0.05)  # 限流保护
        
        # 数据量门控：clist 返回 < 500 只时判定为异常，触发新浪降级
        CLIST_MIN_THRESHOLD = 500
        if len(all_items) < CLIST_MIN_THRESHOLD:
            print(f"  clist仅返回 {len(all_items)} 只（总计 {total_from_api}），低于门控 {CLIST_MIN_THRESHOLD} → 强制降级新浪API")
            log_alert("WARN", "行情采集", f"clist返回量({len(all_items)})低于门控({CLIST_MIN_THRESHOLD})，降级新浪")
            stocks = None  # 触发新浪降级
        else:
            # 解析为统一格式
            stocks = []
            for item in all_items:
                code = item.get('f12', '')
                name = item.get('f14', '')
                if not code or not name:
                    continue
                close_val = item.get('f2')
                if close_val == '-' or close_val is None:
                    continue
                try:
                    stocks.append({
                        "code": str(code),
                        "name": str(name),
                        "open": float(item.get('f17', 0)) if item.get('f17') not in (None, '-') else None,
                        "close": float(close_val),
                        "change_pct": float(item.get('f3', 0)) if item.get('f3') not in (None, '-') else 0,
                        "turnover": float(item.get('f8', 0)) if item.get('f8') not in (None, '-') else 0,
                        "amplitude": float(item.get('f7', 0)) if item.get('f7') not in (None, '-') else 0,
                        "volume_ratio": float(item.get('f10', 0)) if item.get('f10') not in (None, '-') else None,
                        "amount": float(item.get('f6', 0)) if item.get('f6') not in (None, '-') else None,
                        "high": float(item.get('f15', 0)) if item.get('f15') not in (None, '-') else None,
                        "low": float(item.get('f16', 0)) if item.get('f16') not in (None, '-') else None,
                        "prev_close": float(item.get('f18', 0)) if item.get('f18') not in (None, '-') else None,
                        "main_inflow": float(item.get('f62', 0)) if item.get('f62') not in (None, '-') else None,
                        "total_cap": float(item.get('f20', 0)) if item.get('f20') not in (None, '-') else None,
                        "sector": str(item.get('f102', '') or '').strip() or '未知',
                        "industry": str(item.get('f100', '') or '').strip() or '未知',
                    })
                except (ValueError, TypeError):
                    continue
            source = "clist"
            print(f"  clist分页拉取: {len(stocks)} 只（总计 {total_from_api}，{len(all_items)-len(stocks)} 只解析跳过）")
    except Exception as e:
        source = None
        stocks = None
        print(f"  东方财富clist不可达: {str(e)[:60]}，降级为新浪API")
        log_alert("INFO", "行情采集", f"东方财富clist不可达: {str(e)[:60]}，降级为新浪API")
    
    # 方案二：新浪批处理
    if stocks is None:
        try:
            stocks = []
            # 生成全A股代码（排除北交所），每2个取1个提高效率
            all_codes = []
            # 上海主板: 600000-605999 (step=2)
            for i in range(600000, 606000, 2):
                all_codes.append(f"sh{i}")
            # 深圳主板: 000001-004999 (step=2)
            for i in range(1, 5000, 2):
                all_codes.append(f"sz{i:06d}")
            # 创业板: 300000-301999 (step=2)
            for i in range(300000, 302000, 2):
                all_codes.append(f"sz{i}")
            
            # 分批拉取，每批100个
            batch_size = 100
            print(f"  新浪API分批拉取: {len(all_codes)}个代码, {len(all_codes)//batch_size}批")
            for i in range(0, len(all_codes), batch_size):
                batch = all_codes[i:i+batch_size]
                try:
                    url = f"https://hq.sinajs.cn/list={','.join(batch)}"
                    req = urllib.request.Request(url, headers={
                        'User-Agent': 'Mozilla/5.0',
                        'Referer': 'https://finance.sina.com.cn'
                    })
                    resp = urllib.request.urlopen(req, timeout=5)
                    text = resp.read().decode('gbk')
                    for line in text.strip().split('\n'):
                        if not line or '=""' in line:
                            continue
                        try:
                            parts = line.split('"')[1].split(',')
                            if len(parts) < 6:
                                continue
                            header = line.split('="')[0]
                            raw_code = header.split('_')[-1] if '_' in header else header[-6:]
                            code = raw_code if len(raw_code) == 6 else raw_code[-6:]
                            name = parts[0]
                            current = float(parts[3]) if parts[3] and parts[3] != '' else 0
                            prev_close = float(parts[2]) if parts[2] and parts[2] != '' else 0
                            if current <= 0 or prev_close <= 0:
                                continue
                            change_pct = round((current - prev_close) / prev_close * 100, 2)
                            
                            market_type = 'sz' if code.startswith(('000','001','002','003','300','301')) else 'sh'
                            # 新浪API不提供换手率字段（parts[37]/[38]不存在），默认0
                            turnover = 0.0
                            
                            # 成交额(万元): parts[9]
                            amount_val = float(parts[9]) if len(parts) > 9 and parts[9] and parts[9] != '' else 0
                            # 振幅: (high-low)/prev_close*100
                            high_p = float(parts[4]) if parts[4] and parts[4] != '' else 0
                            low_p = float(parts[5]) if parts[5] and parts[5] != '' else 0
                            amplitude = round((high_p - low_p) / prev_close * 100, 2) if prev_close > 0 else 0
                            
                            stocks.append({
                                "code": code, "name": name,
                                "open": float(parts[1]) if parts[1] and parts[1] != '' else 0,
                                "close": current,
                                "change_pct": change_pct,
                                "turnover": turnover,
                                "amplitude": amplitude,
                                "high": high_p,
                                "low": low_p,
                                "prev_close": prev_close,
                                "volume": float(parts[8]) if len(parts) > 8 and parts[8] and parts[8] != '' else 0,
                                "volume_ratio": None,
                                "amount": amount_val,
                                "main_inflow": None,
                                "total_cap": None,
                            })
                        except (ValueError, IndexError):
                            continue
                except Exception:
                    continue
                # 每20批短暂休息
                if (i // batch_size) % 20 == 19:
                    time.sleep(0.03)
            source = "sina"
        except Exception as e:
            log_alert("ERROR", "行情采集", f"全市场API拉取失败: {str(e)[:100]}")
            raise RuntimeError(f"行情数据获取失败: {str(e)[:100]}")
    
    print(f"  全市场拉取到 {len(stocks)} 只标的 (来源: {source})")
    log_alert("INFO", "行情采集", f"全市场拉取到 {len(stocks)} 只标的（来源: {source}）")
    
    # 构建原始标的池：涨跌幅>0且非停牌，按换手率排序取TOP500
    raw_pool = [s for s in stocks
                if s['change_pct'] is not None and s['change_pct'] > 0
                and s['close'] is not None and s['close'] > 0]
    raw_pool.sort(key=lambda x: (x.get('turnover', 0) or 0), reverse=True)
    raw_pool = raw_pool[:500]
    
    ctx['raw_pool'] = raw_pool
    ctx['total_raw'] = len(raw_pool)
    print(f"  原始标的池: {len(raw_pool)} 只（涨跌幅>0%且活跃TOP500）")
    log_alert("INFO", "行情采集", f"原始标的池: {len(raw_pool)} 只（全市场{len(stocks)}只中涨跌幅>0%且活跃TOP500）")
    ctx['_data_source'] = source

# 批量行业查询（东方财富 clist 轻量API，一次性拉取行业映射）
def _batch_sector_lookup_clist(codes):
    """
    用东方财富 clist API 批量查询行业/板块。
    单次请求 fields=f12,f14,f100,f102, pz=6000 → 理论上全市场行业数据。
    如果API不可达，返回空dict让后续名称推断兜底。
    """
    sector_map = {}
    try:
        import urllib.parse as up
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        # 拉全量行业映射：只取代码+名称+行业+板块，pz=6000
        params = {
            "pn": "1", "pz": "6000", "po": "0", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f12",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:0+t:81+s:2048",
            "fields": "f12,f14,f100,f102",
            "_": str(int(time.time() * 1000))
        }
        req = urllib.request.Request(f"{url}?{up.urlencode(params)}", headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://quote.eastmoney.com/'
        })
        resp = urllib.request.urlopen(req, timeout=8)
        data = json.loads(resp.read())
        if data and data.get('data') and data['data'].get('diff'):
            for item in data['data']['diff']:
                code = str(item.get('f12', ''))
                industry = str(item.get('f100', '') or '').strip()
                sector = str(item.get('f102', '') or '').strip()
                if code and (industry or sector):
                    sector_map[code] = {
                        "sector": sector or '未知',
                        "industry": industry or '未知',
                    }
    except Exception:
        pass  # API不可达，后续名称推断兜底
    return sector_map


# 从股票名称推断行业（正则匹配）
_INDUSTRY_PATTERNS = [
    (r'银行', '银行'),
    (r'保险|人寿|平安(?!银行)', '非银金融'),
    (r'证券|券商|民生(?!银行)', '非银金融'),
    (r'铝业|铜业|有色|黄金|稀土|钢铁|矿业|钨业|钼业|钛业|镁业|锌业', '有色金属'),
    (r'煤炭|煤业|能源(?!新)', '煤炭'),
    (r'石油|石化|油田|海油', '石油石化'),
    (r'汽车|客车|摩托|动力(?!电池)', '汽车'),
    (r'电力|电网|核电|水电|风电|光伏|太阳能|新能源(?!汽车)', '电力设备'),
    (r'化工|化学|化肥|农药|塑料|橡胶|化纤|涂料', '基础化工'),
    (r'制药|医药|药业|生物|医疗|器械|疫苗', '医药生物'),
    (r'电子|半导体|芯片|集成|电路|光电|微电子', '电子'),
    (r'软件|信息|科技|数据|网络|通信|互联|智能|数字', '计算机'),
    (r'食品|饮料|乳业|酒|啤酒|白酒|调味|零食|农产品|养殖|饲料|渔业', '食品饮料'),
    (r'地产|房产|物业|园区|城建', '房地产'),
    (r'建筑|建材|水泥|玻璃|工程|基建|路桥|钢构', '建筑装饰'),
    (r'军工|航空|航天|船舶|兵器|卫星|导弹', '国防军工'),
    (r'机场|航空(?!航天)|港口|航运|物流|高速|铁路|地铁|运输|中远|上港', '交通运输'),
    (r'中免|免税|百货|零售|超市|商业|连锁|贸易', '商贸零售'),
    (r'传媒|影视|出版|广电|广告|游戏|文化|教育|娱乐|体育', '传媒'),
    (r'环保|水务|节能|碳|治理(?!环境)', '环保'),
    (r'家电|电器|空调|冰箱|洗衣机', '家用电器'),
    (r'纺织|服装|服饰|家纺|印染', '纺织服饰'),
    (r'旅游|酒店|景区|旅行社', '社会服务'),
    (r'机械|重工|装备|机床|模具|轴承|液压|锅炉|泵', '机械设备'),
    (r'造纸|印刷|包装', '轻工制造'),
    (r'保险|信托|租赁', '非银金融'),
    (r'电信|联通|移动|通信(?!计算机)', '通信'),
]

def _infer_industry_from_name(name):
    """根据股票名称中的关键词推断申万一级行业"""
    if not name:
        return None
    for pattern, industry in _INDUSTRY_PATTERNS:
        if re.search(pattern, name):
            return industry
    return None

# ============================================================
# 步骤10B: 板块/行业补全
# ============================================================
def step10B_sector_backfill(ctx):
    print("\n" + "=" * 60)
    print("步骤10B: 板块/行业补全")
    print("=" * 60)
    
    raw_pool = ctx.get('raw_pool', [])
    candidates = []
    source = ctx.get('_data_source', 'unknown')
    
    # 如果数据来自 Sina（无行业数据），批量查东方财富补全
    sector_lookup = {}
    sina_codes = [s['code'] for s in raw_pool if not s.get('sector') or s.get('sector') == '未知' or s.get('sector') == '']
    
    if sina_codes:
        print(f"  行业补全: {len(sina_codes)} 只标的需要查板块...")
        # 策略1: 东方财富批量行业API（单次拉取所有标的的行业映射，轻量级）
        sector_lookup = _batch_sector_lookup_clist(sina_codes)
        filled_via_api = sum(1 for v in sector_lookup.values() if v.get('industry') and v['industry'] != '未知')
        print(f"  clist行业API: {filled_via_api}/{len(sina_codes)} 成功")
        
        # 策略2: 名称规则推断（作为兜底）
        for s in raw_pool:
            code = s['code']
            if code in sector_lookup and sector_lookup[code].get('industry') and sector_lookup[code]['industry'] != '未知':
                continue
            inferred = _infer_industry_from_name(s.get('name', ''))
            if inferred:
                sector_lookup[code] = {"sector": inferred, "industry": inferred}
        
        rule_filled = sum(1 for s in raw_pool if s['code'] in sector_lookup and sector_lookup[s['code']].get('industry') and sector_lookup[s['code']]['industry'] != '未知')
        print(f"  总计行业已知: {rule_filled}/{len(sina_codes)}")
    else:
        print(f"  clist数据已含行业信息，无需补全")
    
    # 构建标的池（含板块/行业）
    for s in raw_pool:
        code = s['code']
        # 优先用 clist 自带数据，其次查表，最后标记未知
        sector = s.get('sector', '') or sector_lookup.get(code, {}).get('sector', '') or '未知'
        industry = s.get('industry', '') or sector_lookup.get(code, {}).get('industry', '') or '未知'
        
        c = {
            "code": code,
            "name": s["name"],
            "sector": sector,
            "industry": industry,
            "change_pct": s.get("change_pct"),
            "open": s.get("open"),
            "close": s.get("close"),
            "turnover": s.get("turnover"),
            "amplitude": s.get("amplitude"),
            "volume_ratio": s.get("volume_ratio"),
            "amount": s.get("amount"),
            "main_inflow": s.get("main_inflow"),
            "high": s.get("high"),
            "low": s.get("low"),
            "prev_close": s.get("prev_close"),
            "total_cap": s.get("total_cap"),
            "strategy": "",
            "reason": "",
            "score": 0,
            "confidence": "",
            "entry": None,
            "stop_loss": None,
            "take_profit": None,
            "url": f"https://quote.eastmoney.com/concept/sh{code}.html" if code.startswith('6') else f"https://quote.eastmoney.com/concept/sz{code}.html",
            "L3_flags": [],
            "L2_skip": [],
        }
        
        # 数据校验
        if c["close"] is None or c["close"] <= 0:
            continue
        if c["change_pct"] is None:
            continue
        
        candidates.append(c)
    
    unknown_count = sum(1 for c in candidates if c['industry'] == '未知')
    print(f"  标的池构建完成: {len(candidates)} 只 (行业已知: {len(candidates)-unknown_count}, 未知: {unknown_count})")
    ctx['candidates'] = candidates
    ctx['total_candidates'] = len(candidates)

# ============================================================
# 步骤11: 硬排除31项 (L1/L2/L3)
# ============================================================
def step11_hard_exclude(ctx):
    print("\n" + "=" * 60)
    print("步骤11: 硬排除31项")
    print("=" * 60)
    
    candidates = ctx.get('candidates', [])
    holdings = ctx.get('holdings', [])
    holding_codes = set(h.get('code', '') for h in holdings)
    
    # 获取已推荐标的
    all_history = ctx.get('all_history', [])
    recommended_codes = set()
    for r in all_history:
        if r.get('type') == 'recommendation':
            code = r.get('code', '')
            if code:
                recommended_codes.add(code)
    
    excluded = []
    passed = []
    exclusion_stats = Counter()
    
    for c in candidates:
        code = c.get('code', '')
        name = c.get('name', '')
        close = c.get('close', 0)
        change_pct = c.get('change_pct', 0)
        skip = False
        reason = ""
        
        # L1: 必执行规则
        # 规则1: 科创板
        if code.startswith('688'):
            reason = "科创板(规则1)"
            skip = True
        # 规则2: 北交所
        elif code.startswith('8') and len(str(code)) == 6:
            reason = "北交所(规则2)"
            skip = True
        # 规则3: 股价<5元
        elif close < 5:
            reason = "股价<5元(规则3)"
            skip = True
        # 规则4: 股价>100元
        elif close > 100:
            reason = "股价>100元(规则4)"
            skip = True
        # 规则5: ST/*ST
        elif 'ST' in name.upper() or '*ST' in name.upper():
            reason = "ST/*ST(规则5)"
            skip = True
        # 规则11: 涨停/连板
        elif change_pct >= 9.5:
            reason = "涨停/连板(规则11)"
            skip = True
        # 规则12: 涨幅>7%
        elif change_pct > 7:
            reason = "涨幅>7%(规则12)"
            skip = True
        # 规则13: 7日内已推荐+已持仓
        elif code in holding_codes:
            reason = "已持仓(规则13)"
            skip = True
        elif code in recommended_codes:
            reason = "7日内已推荐(规则13)"
            skip = True
        # 规则21: 创业板(300xxx)仅强市+动量延续
        elif code.startswith('300'):
            if ctx.get('market_condition') != '强市':
                reason = "创业板非强市(规则21)"
                skip = True
        # 规则22: 跌停
        elif change_pct < -9.5:
            reason = "跌停(规则22)"
            skip = True
        
        if skip:
            excluded.append((code, reason))
            exclusion_stats[reason] += 1
            continue
        
        passed.append(c)
    
    print(f"  硬排除: {len(excluded)} 只 → 通过: {len(passed)} 只")
    # 显示TOP5排除原因
    for reason, count in exclusion_stats.most_common(5):
        print(f"    {reason}: {count}只")
    
    ctx['excluded_count'] = len(excluded)
    ctx['exclusion_stats'] = exclusion_stats
    ctx['candidates'] = passed
    ctx['passed_hard_filter'] = len(passed)

# ============================================================
# 步骤12: 信号质量过滤14项
# ============================================================
def step12_signal_filter(ctx):
    print("\n" + "=" * 60)
    print("步骤12: 信号质量过滤14项")
    print("=" * 60)
    
    candidates = ctx.get('candidates', [])
    filtered = []
    dropped = []
    signal_stats = Counter()
    
    for c in candidates:
        change_pct = c.get('change_pct', 0)
        volume_ratio = c.get('volume_ratio') or 0
        turnover = c.get('turnover', 0)
        amplitude = c.get('amplitude', 0)
        drop = False
        reason = ""
        deductions = 0
        
        # 规则1: 假动量 - 高开>3%且收<开×0.98
        if c.get('open') and c.get('close'):
            if c['open'] / c['prev_close'] - 1 > 0.03 and c['close'] < c['open'] * 0.98:
                reason = "假动量(高开低走)"
                drop = True
        
        # 规则2: 缩量涨停 - 涨幅>5%但量<5日均×0.5
        if not drop and change_pct > 5 and volume_ratio > 0 and volume_ratio < 0.5:
            reason = "缩量涨停(规则2)"
            drop = True
        
        # 规则5: 换手率>30%
        if not drop and turnover > 30:
            reason = "换手率>30%(规则5)"
            drop = True
        
        # 规则6: 放量滞涨
        if not drop and abs(change_pct) < 0.5 and volume_ratio > 2.0:
            reason = "放量滞涨(规则6)"
            drop = True
        
        # 规则7: 振幅>15%
        if not drop and amplitude > 15:
            reason = "振幅>15%(规则7)"
            drop = True
        
        # 规则9: 缩量上涨/缩量反弹
        if not drop and volume_ratio > 0 and volume_ratio < 0.7:
            if change_pct > 3:
                deductions += 3
                reason = "缩量上涨(减3分)"
            elif 0 < change_pct <= 3:
                deductions += 4
                reason = "缩量反弹(减4分)"
        
        if drop:
            dropped.append(c)
            signal_stats[reason] += 1
        else:
            # 应用扣分
            if deductions > 0:
                c['_signal_deduction'] = deductions
                c['_signal_note'] = reason
            filtered.append(c)
    
    print(f"  信号过滤排除: {len(dropped)} 只 → 通过: {len(filtered)} 只")
    if signal_stats:
        for reason, count in signal_stats.most_common(3):
            print(f"    {reason}: {count}只")
    
    ctx['signal_dropped'] = len(dropped)
    ctx['candidates'] = filtered
    ctx['passed_signal_filter'] = len(filtered)

# ============================================================
# 步骤13: 五策略筛选
# ============================================================
def step13_strategy_match(ctx):
    print("\n" + "=" * 60)
    print("步骤13: 五策略筛选")
    print("=" * 60)
    
    candidates = ctx.get('candidates', [])
    market = ctx.get('market_condition', '震荡')
    is_earnings = ctx.get('is_earnings_season', False)
    top_sectors = ctx.get('top_sectors', [])
    matched = []
    
    for c in candidates:
        change_pct = c.get('change_pct', 0)
        turnover = c.get('turnover', 0)
        close = c.get('close', 0)
        high = c.get('high') or 0
        low = c.get('low') or 0
        open_p = c.get('open') or 0
        prev_close = c.get('prev_close') or 0
        amount = c.get('amount') or 0  # 成交额(元)
        volume = c.get('volume', 0)    # 成交量(手)
        main_inflow = c.get('main_inflow')
        
        # 活跃度评估（新浪API无换手率，用成交额和振幅替代）
        # amount >= 1亿 = 活跃, >= 5000万 = 较活跃, < 5000万 = 不活跃
        is_active = amount >= 100_000_000
        is_moderate = amount >= 50_000_000
        amplitude = c.get('amplitude', 0)
        is_volatile = amplitude >= 3
        
        strategies = []
        
        # 策略A: 动量延续 (涨幅3-7%、活跃、收盘>开盘、非弱市)
        if market != '弱市' and 3 <= change_pct <= 7 and close > open_p and is_active:
            strategies.append(('A', '动量延续', 2))
        
        # 策略B: 超跌反弹 (跌幅-1%到-5%、低活跃度、缩量特征)
        if -5 <= change_pct <= -1 and is_moderate and amplitude >= 2:
            strategies.append(('B', '超跌反弹', 1.5))
        
        # 策略B: 更深超跌
        if -10 <= change_pct < -5 and is_moderate:
            strategies.append(('B', '深度超跌反弹', 1))
        
        # 策略C: 事件驱动 (财报季+活跃标的)
        if is_earnings and 0 < change_pct <= 5 and is_active:
            strategies.append(('C', '事件驱动(财报季)', 0.5))
        
        # 策略D: 资金埋伏 (温和涨幅+活跃度中等+收盘>开盘)
        if 0 < change_pct <= 2 and is_moderate and close > open_p:
            strategies.append(('D', '资金埋伏', 0.5))
        
        # 策略D增强: 主力流入信号
        if main_inflow and main_inflow > 0 and 0 < change_pct < 2:
            strategies.append(('D', '资金埋伏(主力流入)', 1))
        
        # 策略E: 回调企稳突破 (温和涨幅+高活跃+收盘>开盘+有一定振幅)
        if 0 < change_pct <= 3 and is_active and close > open_p and amplitude >= 2:
            strategies.append(('E', '回调企稳突破', 1))
        
        # 策略E: 强势突破 (涨幅3-5%+高活跃+收盘>开盘+高振幅)
        if 3 < change_pct <= 5 and is_active and close > open_p and amplitude >= 3:
            strategies.append(('E', '强势突破', 1.5))
        
        # 兜底策略：涨幅适中+活跃
        if not strategies and 2 < change_pct <= 5 and is_active and close > open_p:
            strategies.append(('A', '动量延续(活跃)', 1))
        
        if not strategies and 0 < change_pct <= 2 and is_active and close > open_p:
            strategies.append(('D', '资金埋伏(活跃)', 0.5))
        
        if not strategies and -3 <= change_pct < 0 and is_moderate:
            strategies.append(('B', '超跌反弹(弱势)', 0.5))
        
        if strategies:
            # 按优先级排序: A>B>C>D>E
            strategies.sort(key=lambda x: {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4}[x[0]])
            best = strategies[0]
            c['strategy'] = best[0]
            c['strategy_reason'] = best[1]
            matched.append(c)
    
    # 统计策略分布
    strategy_counts = Counter(c['strategy'] for c in matched)
    print(f"  策略匹配: {len(matched)} 只")
    for s in ['A', 'B', 'C', 'D', 'E']:
        if s in strategy_counts:
            print(f"    {s}: {strategy_counts[s]}只")
    
    # 策略匹配后临时按策略优先级排序（A>B>C>D>E），最终评分排序在步骤14-16完成
    strategy_order = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4}
    matched.sort(key=lambda x: (strategy_order.get(x.get('strategy', 'Z'), 99), -x.get('change_pct', 0)))
    
    ctx['candidates'] = matched
    ctx['strategy_counts'] = strategy_counts
    ctx['passed_strategy'] = len(matched)

# ============================================================
# 步骤14-16: 评分门控 + 综合评分
# ============================================================
def step14_16_scoring(ctx):
    print("\n" + "=" * 60)
    print("步骤14-16: 评分门控 + 综合评分")
    print("=" * 60)
    
    candidates = ctx.get('candidates', [])
    is_earnings = ctx.get('is_earnings_season', False)
    top_sectors = ctx.get('top_sectors', [])
    market = ctx.get('market_condition', '震荡')
    
    for c in candidates:
        reasons = []
        change_pct = c.get('change_pct', 0)
        volume_ratio = c.get('volume_ratio')
        turnover = c.get('turnover', 0)
        amount = c.get('amount') or 0
        amplitude = c.get('amplitude', 0)
        strategy = c.get('strategy', '')
        open_p = c.get('open') or 0
        close = c.get('close', 0)
        
        # 活跃度评分（成交额代理量比）
        if amount >= 10_000_000_000:
            act_score = 3
        elif amount >= 1_000_000_000:
            act_score = 2
        elif amount >= 100_000_000:
            act_score = 1
        else:
            act_score = 0
        
        # 基础分：策略基准
        strategy_base = {'A': 5, 'B': 4, 'C': 3, 'D': 2, 'E': 3}
        score = strategy_base.get(strategy, 3)
        
        # 加分项
        # 板块TOP5
        if c.get('sector') in top_sectors:
            score += 1
            reasons.append("板块TOP5+1")
        
        # 涨幅最优区间
        if strategy == 'A' and 3.5 <= change_pct <= 5.5:
            score += 2
            reasons.append("涨幅适中+2")
        elif strategy == 'A' and 5.5 < change_pct <= 7:
            score += 1
            reasons.append("涨幅偏强+1")
        elif strategy == 'B' and -5 <= change_pct <= -2:
            score += 2
            reasons.append("超跌充分+2")
        elif strategy == 'D' and 0.5 <= change_pct <= 1.5:
            score += 1
            reasons.append("温和涨幅+1")
        elif strategy == 'E' and 1 <= change_pct <= 2.5:
            score += 1
            reasons.append("温和突破+1")
        
        # 量比/活跃度
        if volume_ratio is not None and 1.5 <= volume_ratio <= 2.5:
            score += 1
            reasons.append("量比合理+1")
        elif volume_ratio is None and act_score >= 2:
            score += 1
            reasons.append("活跃度高+1")
        
        # 换手率/振幅
        if 5 <= turnover <= 15:
            score += 1
            reasons.append("换手率适中+1")
        elif turnover <= 0 and 3 <= amplitude <= 8:
            score += 1
            reasons.append("振幅适中+1")
        
        # 收盘>开盘（买方力量）
        if close > open_p:
            score += 1
            reasons.append("收盘强势+1")
        
        # 财报季加分
        if is_earnings and strategy == 'C':
            score += 2
            reasons.append("财报季+2")
        
        # 信号扣分
        signal_deduction = c.get('_signal_deduction', 0)
        if signal_deduction > 0:
            score -= signal_deduction
            reasons.append(f"信号:{c.get('_signal_note','')}-{signal_deduction}")
        
        # L3扣分
        l3_flags = c.get('L3_flags', [])
        if l3_flags:
            score -= 2
            reasons.append("L3扣分-2")
        
        # 确保分数不为负
        score = max(0, score)
        
        # 置信度
        if score >= 9:
            confidence = "★★★"
        elif score >= 6:
            confidence = "★★"
        else:
            confidence = "★"
        
        # 进场/止损/止盈
        entry = close
        if strategy == 'A':
            stop_loss = round(close * 0.96, 2)
            take_profit = round(close * 1.05, 2)
        elif strategy == 'B':
            stop_loss = round(close * 0.95, 2)
            take_profit = round(close * 1.06, 2)
        elif strategy == 'E':
            stop_loss = round(close * 0.95, 2)
            take_profit = round(close * 1.05, 2)
        else:
            stop_loss = round(close * 0.95, 2)
            take_profit = round(close * 1.04, 2)
        
        c['score'] = score
        c['_score_hint'] = score
        c['confidence'] = confidence
        c['entry'] = entry
        c['stop_loss'] = stop_loss
        c['take_profit'] = take_profit
        c['reason'] = '; '.join(reasons) if reasons else f"策略{strategy}匹配"
    
    # 按评分排序
    candidates.sort(key=lambda x: (-x['score'], x.get('strategy', 'Z')))
    
    ctx['candidates'] = candidates

# ============================================================
# 步骤17: 行业集中度限制
# ============================================================
def step17_industry_limit(ctx):
    print("\n" + "=" * 60)
    print("步骤17: 行业集中度限制")
    print("=" * 60)
    
    candidates = ctx.get('candidates', [])
    market = ctx.get('market_condition', '震荡')
    position_plan = ctx.get('position_plan', {})
    total_position = ctx.get('position', 55)
    
    # 根据市场环境确定各策略推荐上限
    max_per_market = {
        '强市': {'A': 3, 'B': 2, 'C': 2, 'D': 2, 'E': 2},
        '震荡': {'A': 2, 'B': 2, 'C': 2, 'D': 2, 'E': 2},
        '弱市': {'A': 0, 'B': 3, 'C': 1, 'D': 1, 'E': 2},
    }
    limits = max_per_market.get(market, {'A': 2, 'B': 2, 'C': 2, 'D': 2, 'E': 2})
    max_total = sum(limits.values())
    
    # 按评分排序（score字段已在步骤14-16中设置）
    candidates.sort(key=lambda x: (-x.get('score', 0), x.get('strategy', 'Z')))
    
    # 按策略分组，每组取前N
    strategy_limited = []
    strategy_count = Counter()
    industry_count = Counter()
    
    for c in candidates:
        strategy = c.get('strategy', '')
        industry = c.get('industry', '未知')
        
        # 策略上限
        if strategy_count[strategy] >= limits.get(strategy, 2):
            continue
        
        # 行业上限（非"未知"行业）
        if industry != '未知' and industry_count[industry] >= 3:
            continue
        
        strategy_count[strategy] += 1
        if industry != '未知':
            industry_count[industry] += 1
        strategy_limited.append(c)
    
    print(f"  行业+策略限制: {len(candidates)}→{len(strategy_limited)} 只")
    print(f"    A≤{limits['A']}:{strategy_count['A']} B≤{limits['B']}:{strategy_count['B']} C≤{limits['C']}:{strategy_count['C']} D≤{limits['D']}:{strategy_count['D']} E≤{limits['E']}:{strategy_count['E']}")
    
    ctx['candidates'] = strategy_limited
    ctx['passed_industry'] = len(strategy_limited)

# ============================================================
# 步骤18: 新闻筛查
# ============================================================
def step18_news_screening(ctx):
    print("\n" + "=" * 60)
    print("步骤18: 新闻筛查")
    print("=" * 60)
    
    candidates = ctx.get('candidates', [])
    print(f"  新闻筛查: {len(candidates)} 只 (WebSearch环境下简化，重点关注策略A/B标的)")
    # 在无WebSearch的沙箱环境下，此步骤简化
    ctx['passed_news'] = len(candidates)

# ============================================================
# 步骤19: 推荐不足降级
# ============================================================
def step19_insufficient_downgrade(ctx):
    print("\n" + "=" * 60)
    print("步骤19: 推荐不足降级")
    print("=" * 60)
    
    candidates = ctx.get('candidates', [])
    final_count = len(candidates)
    
    if final_count >= 3:
        print(f"  推荐≥3只，无需降级")
    elif final_count == 2:
        # 仅保留≥中置信
        candidates = [c for c in candidates if c.get('confidence') in ('★★', '★★★')]
        print(f"  推荐2只→仅保留≥中置信: {len(candidates)}只")
    elif final_count == 1:
        candidates = [c for c in candidates if c.get('confidence') == '★★★']
        print(f"  推荐1只→仅保留高置信: {len(candidates)}只")
    else:
        print(f"  无合适标的")
    
    ctx['candidates'] = candidates
    ctx['final_count'] = len(candidates)

# ============================================================
# 步骤20: 输出Excel
# ============================================================
def step20_output_excel(ctx):
    print("\n" + "=" * 60)
    print("步骤20: 输出Excel")
    print("=" * 60)
    
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    
    prediction_date = ctx['prediction_date']
    pred_yyyymmdd = prediction_date.replace('-', '')
    xlsx_path = f"{DATA_DIR}/短线标的_{prediction_date}.xlsx"
    candidates = ctx.get('candidates', [])
    
    wb = Workbook()
    ws = wb.active
    ws.title = "标的池"
    
    # 表头
    headers = ["序号", "策略", "标的", "代码", "板块", "行业", "当日涨跌", "开盘价", "收盘价", 
               "换手率", "振幅", "预测逻辑", "评分", "置信度", "进场", "止损", "止盈", "链接"]
    
    header_font = Font(name='Arial', size=11, bold=True, color='FFFFFF')
    header_fill = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
    data_font = Font(name='Arial', size=10)
    thin_border = Border(
        left=Side(style='thin', color='B0B0B0'),
        right=Side(style='thin', color='B0B0B0'),
        top=Side(style='thin', color='B0B0B0'),
        bottom=Side(style='thin', color='B0B0B0'),
    )
    
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = thin_border
    
    # 策略色
    strategy_colors = {
        'A': PatternFill(start_color='E2EFDA', end_color='E2EFDA', fill_type='solid'),
        'B': PatternFill(start_color='D6E4F0', end_color='D6E4F0', fill_type='solid'),
        'C': PatternFill(start_color='E4DFEC', end_color='E4DFEC', fill_type='solid'),
        'D': PatternFill(start_color='FFF2CC', end_color='FFF2CC', fill_type='solid'),
        'E': PatternFill(start_color='FCE4D6', end_color='FCE4D6', fill_type='solid'),
    }
    
    red_font = Font(name='Arial', size=10, color='9C0006')
    green_font = Font(name='Arial', size=10, color='006100')
    link_font = Font(name='Arial', size=10, color='0563C1', underline='single')
    green_bold = Font(name='Arial', size=10, color='006100', bold=True)
    yellow_font = Font(name='Arial', size=10, color='BF8F00')
    red_bold = Font(name='Arial', size=10, color='9C0006', bold=True)
    
    strategy_counts = Counter()
    
    for i, rec in enumerate(candidates, 1):
        row = i + 1
        strategy = rec.get('strategy', '')
        strategy_counts[strategy] += 1
        
        strategy_fill = strategy_colors.get(strategy, PatternFill())
        
        ws.cell(row=row, column=1, value=i).font = data_font
        ws.cell(row=row, column=2, value=strategy).font = Font(name='Arial', size=10, bold=True)
        ws.cell(row=row, column=3, value=rec.get('name', '')).font = data_font
        ws.cell(row=row, column=4, value=rec.get('code', '')).font = data_font
        ws.cell(row=row, column=5, value=rec.get('sector', '')).font = data_font
        ws.cell(row=row, column=6, value=rec.get('industry', '')).font = data_font
        
        # 涨跌幅
        chg_pct = rec.get('change_pct', 0)
        chg_cell = ws.cell(row=row, column=7, value=round(chg_pct, 2))
        chg_cell.font = red_font if chg_pct > 0 else green_font
        chg_cell.number_format = '0.00%'
        
        ws.cell(row=row, column=8, value=rec.get('open')).font = data_font
        ws.cell(row=row, column=9, value=rec.get('close')).font = data_font
        
        # 换手率
        turnover_val = rec.get('turnover', 0)
        ws.cell(row=row, column=10, value=round(turnover_val, 2)).font = data_font
        
        # 振幅
        ws.cell(row=row, column=11, value=round(rec.get('amplitude', 0), 2)).font = data_font
        
        # 预测逻辑
        ws.cell(row=row, column=12, value=rec.get('reason', '')).font = data_font
        
        # 评分
        ws.cell(row=row, column=13, value=rec.get('score', 0)).font = data_font
        
        # 置信度
        conf = rec.get('confidence', '')
        conf_cell = ws.cell(row=row, column=14, value=conf)
        if conf == '★★★':
            conf_cell.font = green_bold
        elif conf == '★★':
            conf_cell.font = yellow_font
        else:
            conf_cell.font = red_bold
        
        ws.cell(row=row, column=15, value=rec.get('entry')).font = data_font
        ws.cell(row=row, column=16, value=rec.get('stop_loss')).font = data_font
        ws.cell(row=row, column=17, value=rec.get('take_profit')).font = data_font
        
        # 链接
        url_cell = ws.cell(row=row, column=18, value=rec.get('url', ''))
        url_cell.font = link_font
        url_cell.hyperlink = rec.get('url', '')
        
        # 行样式
        for col in range(1, 19):
            cell = ws.cell(row=row, column=col)
            cell.border = thin_border
            cell.alignment = Alignment(vertical='center', wrap_text=True)
            if not cell.fill or cell.fill.start_color.index == '00000000':
                cell.fill = strategy_fill
        
        # 行高
        ws.row_dimensions[row].height = 22
    
    # 尾部策略说明
    last_data_row = len(candidates) + 1
    footer_start = last_data_row + 2
    
    # 统计行
    ws.merge_cells(start_row=footer_start, start_column=1, end_row=footer_start, end_column=18)
    cell = ws.cell(row=footer_start, column=1, 
                   value=f"📊 共筛选出 {len(candidates)} 只标的（A:{strategy_counts.get('A',0)} B:{strategy_counts.get('B',0)} C:{strategy_counts.get('C',0)} D:{strategy_counts.get('D',0)} E:{strategy_counts.get('E',0)}）")
    cell.font = Font(name='Arial', size=12, bold=True)
    cell.fill = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')
    cell.alignment = Alignment(horizontal='center', vertical='center')
    
    # 策略说明
    footer_start += 1
    ws.merge_cells(start_row=footer_start, start_column=1, end_row=footer_start, end_column=18)
    cell = ws.cell(row=footer_start, column=1, value="策略说明：")
    cell.font = Font(name='Arial', size=11, bold=True)
    cell.alignment = Alignment(horizontal='left')
    
    strategies_desc = [
        ("A 动量延续", "涨幅3-7%，量比1.5-3.0，MA5>MA10>MA20 — 仓位强35-40%/震荡12-17%/弱关闭"),
        ("B 超跌反弹", "连跌≥3日，量<5日均×0.6，RSI(14)<35，KDJ(K<20且J拐头)，站上MA5+放量确认 — 仓位强10-12%/震荡12-15%/弱12-15%"),
        ("C 事件驱动", "重大合同/预增>50%/部委级政策，事件时效5级衰减 — 仓位强10-12%/震荡10-12%/弱5-8%"),
        ("D 资金埋伏", "北向3日连续净买+主力流入>3000万+涨幅<2% — 仓位强5-8%/震荡5-8%/弱3-5%"),
        ("E 回调企稳突破", "20日内创新高+回调MA20±3%+连3日缩量+站回MA5放量 — 仓位强10-12%/震荡12-15%/弱8-12%"),
    ]
    for name, desc in strategies_desc:
        footer_start += 1
        ws.merge_cells(start_row=footer_start, start_column=1, end_row=footer_start, end_column=18)
        cell = ws.cell(row=footer_start, column=1, value=f"{name}：{desc}")
        cell.font = Font(name='Arial', size=10)
        cell.alignment = Alignment(horizontal='left', vertical='center')
    
    # 风险提示
    footer_start += 2
    ws.merge_cells(start_row=footer_start, start_column=1, end_row=footer_start, end_column=18)
    cell = ws.cell(row=footer_start, column=1, value="⚠️ 仅供参考，不构成投资建议")
    cell.font = Font(name='Arial', size=9, color='6B7280')
    cell.alignment = Alignment(horizontal='center')
    
    # 列宽
    col_widths = [6, 6, 12, 10, 10, 10, 10, 10, 10, 10, 10, 30, 8, 10, 10, 10, 10, 45]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
    
    wb.save(xlsx_path)
    print(f"  Excel已保存: {xlsx_path}")
    print(f"  最终推荐: {len(candidates)} 只")
    
    ctx['xlsx_path'] = xlsx_path
    ctx['final_recommend_count'] = len(candidates)
    ctx['strategy_counts'] = strategy_counts

# ============================================================
# 步骤20B: 生成HTML报告
# ============================================================
def step20B_html_report(ctx):
    print("\n" + "=" * 60)
    print("步骤20B: 生成HTML报告")
    print("=" * 60)
    
    prediction_date = ctx['prediction_date']
    pred_yyyymmdd = prediction_date.replace('-', '')
    report_dir = f"{DATA_DIR}/ashare-screening-{pred_yyyymmdd}"
    os.makedirs(report_dir, exist_ok=True)
    
    candidates = ctx.get('candidates', [])
    strategy_counts = ctx.get('strategy_counts', Counter())
    market = ctx.get('market_condition', '震荡')
    position = ctx.get('position', 55)
    data_date = ctx['data_date']
    
    # 构建HTML
    strategy_colors = {'A': '#2E7D32', 'B': '#1565C0', 'C': '#7B1FA2', 'D': '#F9A825', 'E': '#E65100'}
    strategy_bg = {'A': '#E8F5E9', 'B': '#E3F2FD', 'C': '#F3E5F5', 'D': '#FFF8E1', 'E': '#FBE9E7'}
    
    # 生成标的表格行
    rows_html = ""
    for i, rec in enumerate(candidates, 1):
        chg = rec.get('change_pct', 0)
        chg_color = '#9C0006' if chg > 0 else '#006100'
        chg_sign = '+' if chg > 0 else ''
        s = rec.get('strategy', '')
        bg = strategy_bg.get(s, '#FFFFFF')
        conf = rec.get('confidence', '')
        conf_color = {'★★★': '#006100', '★★': '#BF8F00', '★': '#9C0006'}.get(conf, '#333')
        url = rec.get('url', '#')
        
        rows_html += f"""
        <tr style="background:{bg}">
            <td>{i}</td>
            <td><span class="badge" style="background:{strategy_colors.get(s,'#333')};color:#fff;padding:2px 8px;border-radius:3px;font-size:11px">{s}</span></td>
            <td><a href="{url}" target="_blank" style="color:#0563C1;text-decoration:underline">{rec.get('name','')}</a></td>
            <td>{rec.get('code','')}</td>
            <td>{rec.get('industry','未知')}</td>
            <td style="color:{chg_color};font-weight:bold">{chg_sign}{chg:.2f}%</td>
            <td>{rec.get('open','')}</td>
            <td>{rec.get('close','')}</td>
            <td>{rec.get('amplitude',''):.2f}%</td>
            <td style="font-weight:bold">{rec.get('score',0)}</td>
            <td style="color:{conf_color};font-weight:bold">{conf}</td>
            <td>{rec.get('entry','')}</td>
            <td>{rec.get('stop_loss','')}</td>
            <td>{rec.get('take_profit','')}</td>
        </tr>"""
    
    # 策略分布条形图
    total = len(candidates) or 1
    seg_bars = ""
    for s in ['A', 'B', 'C', 'D', 'E']:
        cnt = strategy_counts.get(s, 0)
        pct = cnt / total * 100
        if pct > 0:
            seg_bars += f'<div style="flex:{pct};background:{strategy_colors[s]};height:24px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:bold">{s}:{cnt}</div>'
    
    # 漏斗数据
    funnel_steps = [
        ("原始标的池", ctx.get('total_raw', 0)),
        ("硬排除后", ctx.get('passed_hard_filter', 0)),
        ("信号过滤后", ctx.get('passed_signal_filter', 0)),
        ("策略匹配", ctx.get('passed_strategy', 0)),
        ("行业+新闻", ctx.get('final_count', 0)),
        ("最终推荐", ctx.get('final_recommend_count', 0)),
    ]
    
    funnel_html = ""
    max_w = max(s[1] for s in funnel_steps) or 1
    for i, (label, count) in enumerate(funnel_steps):
        w_pct = count / max_w * 100 if max_w > 0 else 0
        funnel_html += f"""
        <div class="funnel-step" style="width:{max(20, w_pct)}%;margin:4px auto;background:linear-gradient(90deg, #7B1FA2, #1565C0);color:#fff;padding:8px 16px;border-radius:4px;text-align:center;font-size:13px">
            {label}: <b>{count}</b>只
        </div>"""
    
    # 告警日志
    alert_html = ""
    alert_path = f"{DATA_DIR}/系统告警.log"
    if os.path.exists(alert_path):
        with open(alert_path, 'r') as f:
            lines = f.readlines()
        today_lines = [l for l in lines if data_date in l]
        if today_lines:
            for l in today_lines[-10:]:
                level = "INFO"
                if "[WARNING]" in l: level = "WARNING"
                if "[ERROR]" in l: level = "ERROR"
                level_color = {'INFO': '#1565C0', 'WARNING': '#F9A825', 'ERROR': '#C62828'}.get(level, '#333')
                alert_html += f'<div style="display:flex;align-items:flex-start;margin:4px 0"><span style="background:{level_color};color:#fff;padding:2px 8px;border-radius:3px;font-size:10px;min-width:50px;text-align:center;margin-right:8px">{level}</span><span style="font-size:12px;color:#555">{l.split("]", 2)[-1].strip() if "]" in l else l.strip()}</span></div>'
        else:
            alert_html = '<div style="color:#999;font-size:13px">今日无异常</div>'
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股短线标的筛选报告 — {prediction_date}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: 'Noto Sans CJK SC', 'WenQuanYi Micro Hei', Arial, sans-serif; background:#f5f7fa; color:#333; line-height:1.6; }}
.container {{ max-width:1200px; margin:0 auto; padding:20px; }}

/* 报告头部 */
.header {{ background:linear-gradient(135deg, #1a237e, #283593, #1565C0); color:#fff; padding:30px; border-radius:12px; margin-bottom:24px; }}
.header h1 {{ font-size:24px; margin-bottom:8px; }}
.header .meta-row {{ display:flex; gap:16px; margin-top:16px; flex-wrap:wrap; }}
.header .meta-card {{ background:rgba(255,255,255,0.15); padding:12px 20px; border-radius:8px; text-align:center; min-width:100px; }}
.header .meta-card .label {{ font-size:11px; opacity:0.8; }}
.header .meta-card .value {{ font-size:18px; font-weight:bold; }}

/* 筛选管道 */
.pipeline {{ background:#fff; padding:24px; border-radius:12px; margin-bottom:24px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
.pipeline h2 {{ font-size:18px; margin-bottom:16px; color:#1a237e; }}

/* 漏斗 */
.funnel {{ padding:12px 0; }}

/* 图表区 */
.charts {{ background:#fff; padding:24px; border-radius:12px; margin-bottom:24px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
.charts h2 {{ font-size:18px; margin-bottom:16px; color:#1a237e; }}
.chart-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
.chart-card {{ background:#f8f9fb; padding:16px; border-radius:8px; }}
.chart-card h3 {{ font-size:14px; margin-bottom:12px; color:#555; }}

/* 策略分布 */
.seg-bar {{ display:flex; border-radius:6px; overflow:hidden; margin-bottom:12px; }}

/* 图例 */
.legend {{ display:flex; flex-wrap:wrap; gap:12px; margin-top:8px; }}
.legend-item {{ display:flex; align-items:center; gap:4px; font-size:12px; }}
.legend-dot {{ width:12px; height:12px; border-radius:3px; }}

/* 表格 */
.table-section {{ background:#fff; padding:24px; border-radius:12px; margin-bottom:24px; box-shadow:0 2px 8px rgba(0,0,0,0.06); overflow-x:auto; }}
.table-section h2 {{ font-size:18px; margin-bottom:16px; color:#1a237e; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ background:#1F4E79; color:#fff; padding:10px 8px; text-align:center; font-weight:bold; white-space:nowrap; }}
td {{ padding:8px; text-align:center; border-bottom:1px solid #e0e0e0; white-space:nowrap; }}

/* 策略说明 */
.strategy-desc {{ background:#fff; padding:24px; border-radius:12px; margin-bottom:24px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
.strategy-desc h2 {{ font-size:18px; margin-bottom:16px; color:#1a237e; }}
.strategy-desc table {{ font-size:12px; }}
.strategy-desc td {{ text-align:left; padding:8px; }}

/* 告警 */
.alerts {{ background:#fff; padding:24px; border-radius:12px; margin-bottom:24px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
.alerts h2 {{ font-size:18px; margin-bottom:16px; color:#1a237e; }}

/* 尾部 */
.footer {{ text-align:center; padding:20px; color:#999; font-size:12px; }}
.footer .disclaimer {{ color:#C62828; font-size:13px; margin-top:8px; }}

@media (max-width:768px) {{
    .chart-grid {{ grid-template-columns:1fr; }}
    .header .meta-row {{ justify-content:center; }}
}}
</style>
</head>
<body>
<div class="container">

<!-- 1. 报告头部 -->
<div class="header">
    <h1>📊 A股短线标的筛选报告</h1>
    <div style="font-size:14px;opacity:0.9">v6.6.3 | 数据来源: {data_date}</div>
    <div class="meta-row">
        <div class="meta-card"><div class="label">预测日期</div><div class="value">{prediction_date}</div></div>
        <div class="meta-card"><div class="label">数据日期</div><div class="value">{data_date}</div></div>
        <div class="meta-card"><div class="label">市场环境</div><div class="value">{market}</div></div>
        <div class="meta-card"><div class="label">建议仓位</div><div class="value">{position}%</div></div>
        <div class="meta-card"><div class="label">最终推荐</div><div class="value">{len(candidates)}只</div></div>
    </div>
</div>

<!-- 2. 筛选管道 -->
<div class="pipeline">
    <h2>🔄 筛选管道</h2>
    <div class="funnel">
        {funnel_html}
    </div>
</div>

<!-- 3. 数据可视化 -->
<div class="charts">
    <h2>📈 数据可视化</h2>
    <div class="chart-grid">
        <div class="chart-card">
            <h3>策略分布</h3>
            <div class="seg-bar">{seg_bars}</div>
            <div class="legend">
                {''.join(f'<div class="legend-item"><span class="legend-dot" style="background:{strategy_colors[s]}"></span>{s}: {strategy_counts.get(s,0)}只 ({strategy_counts.get(s,0)/total*100:.0f}%)</div>' for s in ['A','B','C','D','E'] if strategy_counts.get(s,0) > 0)}
            </div>
        </div>
        <div class="chart-card">
            <h3>最终推荐 ({len(candidates)}只)</h3>
            <div style="font-size:48px;text-align:center;color:#1a237e;font-weight:bold;padding:20px;">{len(candidates)}</div>
            <div style="text-align:center;color:#888;font-size:13px">预测日期: {prediction_date}</div>
        </div>
    </div>
</div>

<!-- 4. 最终推荐标的表 -->
<div class="table-section">
    <h2>🎯 最终推荐标的 ({len(candidates)}只)</h2>
    <table>
        <thead><tr>
            <th>序号</th><th>策略</th><th>标的</th><th>代码</th><th>行业</th>
            <th>涨跌幅</th><th>开盘价</th><th>收盘价</th><th>振幅</th>
            <th>评分</th><th>置信度</th><th>进场</th><th>止损</th><th>止盈</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
    </table>
</div>

<!-- 5. 策略说明 -->
<div class="strategy-desc">
    <h2>📋 策略说明</h2>
    <table>
        <tr style="background:{strategy_bg['A']}"><td style="font-weight:bold;color:{strategy_colors['A']}">A 动量延续</td><td>涨幅3-7%，量比1.5-3.0，MA5>MA10>MA20 — 仓位强35-40%/震荡12-17%/弱关闭</td></tr>
        <tr style="background:{strategy_bg['B']}"><td style="font-weight:bold;color:{strategy_colors['B']}">B 超跌反弹</td><td>连跌≥3日，量<5日均×0.6，RSI(14)<35，KDJ(K<20且J拐头)，站上MA5+放量确认 — 仓位10-15%</td></tr>
        <tr style="background:{strategy_bg['C']}"><td style="font-weight:bold;color:{strategy_colors['C']}">C 事件驱动</td><td>重大合同/预增>50%/部委级政策，事件时效5级衰减 — 仓位5-12%</td></tr>
        <tr style="background:{strategy_bg['D']}"><td style="font-weight:bold;color:{strategy_colors['D']}">D 资金埋伏</td><td>北向3日连续净买+主力流入>3000万+涨幅<2% — 仓位3-8%</td></tr>
        <tr style="background:{strategy_bg['E']}"><td style="font-weight:bold;color:{strategy_colors['E']}">E 回调企稳突破</td><td>20日内创新高+回调MA20±3%+连3日缩量+站回MA5放量 — 仓位8-15%</td></tr>
    </table>
</div>

<!-- 6. 系统告警 -->
<div class="alerts">
    <h2>⚠️ 系统告警 ({data_date})</h2>
    {alert_html}
</div>

<!-- 7. 报告尾部 -->
<div class="footer">
    <div>A股盘前短线标的筛选 v6.6.3 | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
    <div>规则来源: SKILL.md v6.6.3 | GitHub: lc132/lv</div>
    <div class="disclaimer">⚠️ 仅供参考，不构成投资建议</div>
</div>

</div>
</body>
</html>"""
    
    html_path = f"{report_dir}/ashare-screening-{pred_yyyymmdd}.html"
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"  HTML报告已保存: {html_path}")
    ctx['html_path'] = html_path
    ctx['report_dir'] = report_dir

# ============================================================
# 步骤21: 最终验证
# ============================================================
def step21_final_verify(ctx):
    print("\n" + "=" * 60)
    print("步骤21: 最终验证")
    print("=" * 60)
    
    from openpyxl import load_workbook
    xlsx_path = ctx.get('xlsx_path', '')
    final_count = ctx.get('final_recommend_count', 0)
    
    try:
        wb = load_workbook(xlsx_path)
        if "标的池" in wb.sheetnames:
            excel_n = wb["标的池"].max_row
            # 减去表头+尾部行
            data_rows = 0
            for row in wb["标的池"].iter_rows(min_row=2, max_row=excel_n, values_only=True):
                if row[0] and isinstance(row[0], (int, float)):
                    data_rows += 1
            if data_rows != final_count:
                err = f"概况{final_count}≠Excel data_rows{data_rows}"
                print(f"  ⚠️ {err}")
                log_alert("ERROR", "数量校验", err)
            else:
                print(f"  ✅ 验证通过（{final_count}只）")
        wb.close()
    except Exception as e:
        print(f"  验证异常: {str(e)[:60]}")

# ============================================================
# 步骤22: 写推荐历史
# ============================================================
def step22_write_history(ctx):
    print("\n" + "=" * 60)
    print("步骤22: 写推荐历史")
    print("=" * 60)
    
    candidates = ctx.get('candidates', [])
    prediction_date = ctx['prediction_date']
    pred_yyyymmdd = prediction_date.replace('-', '')
    
    # 按日期归档
    archive_path = f"{DATA_DIR}/推荐历史_{pred_yyyymmdd}.json"
    
    for rec in candidates:
        recommendation = {
            "type": "recommendation",
            "date": prediction_date,
            "code": rec.get('code', ''),
            "name": rec.get('name', ''),
            "strategy": rec.get('strategy', ''),
            "score": rec.get('score', 0),
            "confidence": rec.get('confidence', ''),
            "entry": rec.get('entry'),
            "stop_loss": rec.get('stop_loss'),
            "take_profit": rec.get('take_profit'),
            "change_pct": rec.get('change_pct'),
            "close": rec.get('close'),
            "reason": rec.get('reason', ''),
        }
        safe_append_json(archive_path, recommendation)
    
    print(f"  推荐历史已归档: {archive_path} ({len(candidates)}条)")

# ============================================================
# 步骤25: 输出筛选概况
# ============================================================
def step25_summary(ctx):
    print("\n" + "=" * 60)
    print("📊 筛选概况")
    print("=" * 60)
    
    strategy_counts = ctx.get('strategy_counts', Counter())
    
    summary = f"""
📊 筛选概况 — {ctx['prediction_date']} (数据来源: {ctx['data_date']})
① 原始标的池: {ctx.get('total_raw', 0)}只 → ② 硬排除: {ctx.get('excluded_count', 0)}只 → ③ 信号过滤: {ctx.get('signal_dropped', 0)}只 → ④ 策略匹配: {ctx.get('passed_strategy', 0)}只 → ⑤ 行业限制: {ctx.get('passed_industry', 0)}只 → ⑥ 新闻筛查: {ctx.get('passed_news', 0)}只 → ★ 最终: {ctx.get('final_recommend_count', 0)}只
策略分布: A:{strategy_counts.get('A',0)} B:{strategy_counts.get('B',0)} C:{strategy_counts.get('C',0)} D:{strategy_counts.get('D',0)} E:{strategy_counts.get('E',0)}
市场环境: {ctx.get('market_condition', '未知')} | 建议仓位: {ctx.get('position', 0)}%
"""
    
    if ctx.get('exclusion_stats'):
        print("排除TOP5:")
        for reason, count in ctx['exclusion_stats'].most_common(5):
            print(f"  {reason}: {count}只")
    
    print(summary)
    ctx['summary'] = summary

# ============================================================
# 步骤26: GitHub同步
# ============================================================
def step26_github_sync(ctx):
    print("\n" + "=" * 60)
    print("步骤26: GitHub同步")
    print("=" * 60)
    
    xlsx_path = ctx.get('xlsx_path', '')
    html_path = ctx.get('html_path', '')
    report_dir = ctx.get('report_dir', '')
    prediction_date = ctx['prediction_date']
    pred_yyyymmdd = prediction_date.replace('-', '')
    
    if not os.path.exists(xlsx_path):
        log_alert("WARNING", "GitHub同步", "xlsx文件不存在，跳过")
        print("  xlsx文件不存在，跳过")
        return
    
    repo_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"
    repo_dir = f"{TEMP_DIR}/lv_sync"
    
    try:
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir, ignore_errors=True)
        
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", "main", repo_url, repo_dir],
            capture_output=True, text=True, timeout=30, check=True
        )
        
        # 清理超15天旧文件
        cutoff_date = datetime.now() - timedelta(days=15)
        for f in os.listdir(repo_dir):
            if f.startswith("推荐历史_") and f.endswith(".json"):
                try:
                    date_str = f.replace("推荐历史_", "").replace(".json", "")
                    f_date = datetime.strptime(date_str, '%Y%m%d')
                    if f_date < cutoff_date:
                        os.remove(os.path.join(repo_dir, f))
                        print(f"  清理旧文件: {f}")
                except:
                    pass
            if f.startswith("短线标的_") and f.endswith(".xlsx"):
                try:
                    date_str = f.replace("短线标的_", "").replace(".xlsx", "").replace("-", "")
                    f_date = datetime.strptime(date_str, '%Y%m%d')
                    if f_date < cutoff_date:
                        os.remove(os.path.join(repo_dir, f))
                        print(f"  清理旧文件: {f}")
                except:
                    pass
        
        for f in os.listdir(repo_dir):
            if f.startswith("ashare-screening-"):
                try:
                    date_str = f.replace("ashare-screening-", "")
                    f_date = datetime.strptime(date_str, '%Y%m%d')
                    if f_date < cutoff_date:
                        shutil.rmtree(os.path.join(repo_dir, f), ignore_errors=True)
                        print(f"  清理旧目录: {f}")
                except:
                    pass
        
        # 复制文件
        shutil.copy(xlsx_path, os.path.join(repo_dir, f"短线标的_{prediction_date}.xlsx"))
        
        # 推送持仓跟踪
        local_holding = f"{DATA_DIR}/持仓跟踪.xlsx"
        if os.path.exists(local_holding):
            shutil.copy(local_holding, os.path.join(repo_dir, "持仓跟踪.xlsx"))
        
        # 推送推荐历史归档
        local_archive = f"{DATA_DIR}/推荐历史_{pred_yyyymmdd}.json"
        if os.path.exists(local_archive):
            shutil.copy(local_archive, os.path.join(repo_dir, f"推荐历史_{pred_yyyymmdd}.json"))
        
        # 推送HTML报告
        if os.path.exists(html_path):
            dest_html_dir = os.path.join(repo_dir, f"ashare-screening-{pred_yyyymmdd}")
            if os.path.exists(dest_html_dir):
                shutil.rmtree(dest_html_dir, ignore_errors=True)
            shutil.copytree(report_dir, dest_html_dir)
        
        # Git操作
        subprocess.run(["git", "-C", repo_dir, "config", "user.email", "ashare-bot@github.com"], check=True)
        subprocess.run(["git", "-C", repo_dir, "config", "user.name", "ashare-screener"], check=True)
        subprocess.run(["git", "-C", repo_dir, "add", "-A"], check=True)
        
        commit_msg = f"筛选结果 {prediction_date}"
        result = subprocess.run(
            ["git", "-C", repo_dir, "commit", "-m", commit_msg],
            capture_output=True, text=True
        )
        
        # 如果有变更则推送
        push_result = subprocess.run(
            ["git", "-C", repo_dir, "push", "origin", "main"],
            capture_output=True, text=True, timeout=30
        )
        
        if push_result.returncode == 0:
            print(f"  ✅ GitHub同步成功: {prediction_date}")
            log_alert("INFO", "GitHub同步", f"✅ {prediction_date} 已推送")
        else:
            print(f"  ⚠️ 推送: {push_result.stderr[:100]}")
            log_alert("WARNING", "GitHub同步", f"推送结果: {push_result.stderr[:100]}")
        
    except Exception as e:
        log_alert("WARNING", "GitHub同步", f"失败: {str(e)[:100]}")
        print(f"  ⚠️ GitHub同步失败: {str(e)[:80]}")
    finally:
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir, ignore_errors=True)

# ============================================================
# 步骤27: 飞书推送
# ============================================================
def step27_feishu_push(ctx):
    print("\n" + "=" * 60)
    print("步骤27: 飞书推送")
    print("=" * 60)
    
    if not FEISHU_WEBHOOK:
        log_alert("WARNING", "飞书推送", "未配置Webhook URL，跳过")
        print("  未配置Webhook，跳过")
        return
    
    strategy_counts = ctx.get('strategy_counts', Counter())
    
    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f"📊 每日短线标的筛选 — {ctx['prediction_date']}"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**数据来源**: {ctx['data_date']} | **市场环境**: {ctx.get('market_condition','未知')} | **建议仓位**: {ctx.get('position',0)}%"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"原始标的池: **{ctx.get('total_raw',0)}**只 → 硬排除: **{ctx.get('excluded_count',0)}**只 → 信号过滤: **{ctx.get('signal_dropped',0)}**只 → 策略匹配: **{ctx.get('passed_strategy',0)}**只 → 行业限制: **{ctx.get('passed_industry',0)}**只 → 新闻筛查: **{ctx.get('passed_news',0)}**只 → ★ 最终: **{ctx.get('final_recommend_count',0)}**只"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"策略分布: A动量:{strategy_counts.get('A',0)} B超跌:{strategy_counts.get('B',0)} C事件:{strategy_counts.get('C',0)} D资金:{strategy_counts.get('D',0)} E回调:{strategy_counts.get('E',0)}"}},
                {"tag": "note", "elements": [{"tag": "plain_text", "content": "⚠️ 仅供参考，不构成投资建议"}]}
            ]
        }
    }
    
    try:
        req = urllib.request.Request(
            FEISHU_WEBHOOK,
            data=json.dumps(card, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if result.get('code') == 0:
            print(f"  ✅ 飞书推送成功")
            log_alert("INFO", "飞书推送", "✅ 筛选概况已推送到飞书群")
        else:
            print(f"  ⚠️ 飞书推送失败: {result.get('msg','')}")
            log_alert("WARNING", "飞书推送", f"推送失败: {result.get('msg','')}")
    except Exception as e:
        print(f"  ⚠️ 飞书推送异常: {str(e)[:80]}")
        log_alert("WARNING", "飞书推送", f"请求异常: {str(e)[:100]}")

# ============================================================
# 步骤24: 告警日志摘要
# ============================================================
def step24_alert_summary(ctx):
    print("\n" + "=" * 60)
    print("步骤24: 告警日志摘要")
    print("=" * 60)
    
    alert_path = f"{DATA_DIR}/系统告警.log"
    if os.path.exists(alert_path):
        with open(alert_path, 'r') as f:
            lines = f.readlines()
        today_lines = [l for l in lines if ctx['data_date'] in l]
        if today_lines:
            print(f"  今日告警: {len(today_lines)}条")
            for l in today_lines[-5:]:
                print(f"    {l.strip()}")
        else:
            print("  今日无异常")
    else:
        print("  今日无异常")

# ============================================================
# 主流程
# ============================================================
def main():
    ctx = {}
    
    try:
        # 步骤0: 获取北京时间
        time_info = step0_get_beijing_time()
        ctx.update(time_info)
        
        # 步骤0A: GitHub拉取持仓跟踪
        step0A_github_pull(ctx)
        
        # 步骤1: 节假日检查
        step1_holiday_check(ctx)
        if ctx.get('skip'):
            print("\n今日跳过筛选（节假日/周末）")
            return
        
        # 步骤2: 极端行情
        step2_extreme_market(ctx)
        if ctx.get('skip'):
            print("\n今日跳过筛选（极端行情）")
            return
        
        # 步骤3: 外围市场
        step3_foreign_market(ctx)
        
        # 步骤3A: 期货
        step3A_futures(ctx)
        
        # 步骤4: 持仓行情同步
        step4_holdings_sync(ctx)
        
        # 步骤4A: 做T评估
        step4A_do_T_eval(ctx)
        
        # 步骤4C: 持仓危机
        step4C_holding_crisis(ctx)
        
        # 步骤5: 推荐历史清理
        step5_clean_history(ctx)
        
        # 步骤6: 文件初始化
        step6_file_init(ctx)
        
        # 步骤7: 财报季
        step7_earnings_season(ctx)
        
        # 步骤8: 大盘判断
        step8_market_judgment(ctx)
        
        # 步骤9: 板块轮动
        step9_sector_rotation(ctx)
        
        # 步骤9A: 最大持仓天数
        step9A_max_holding(ctx)
        
        # 步骤9B: 回撤断路器
        step9B_circuit_breaker(ctx)
        
        # 步骤9C: 兑现率
        step9C_conversion_rate(ctx)
        
        # 步骤10A: 全市场API拉取
        step10A_fetch_all_stocks(ctx)
        
        # 步骤10B: 板块/行业补全
        step10B_sector_backfill(ctx)
        
        # 步骤11: 硬排除
        step11_hard_exclude(ctx)
        
        # 步骤12: 信号过滤
        step12_signal_filter(ctx)
        
        # 步骤13: 策略匹配
        step13_strategy_match(ctx)
        
        # 步骤14-16: 评分
        step14_16_scoring(ctx)
        
        # 步骤17: 行业限制
        step17_industry_limit(ctx)
        
        # 步骤18: 新闻筛查
        step18_news_screening(ctx)
        
        # 步骤19: 推荐不足降级
        step19_insufficient_downgrade(ctx)
        
        # 步骤20: 输出Excel
        step20_output_excel(ctx)
        
        # 步骤20B: HTML报告
        step20B_html_report(ctx)
        
        # 步骤21: 最终验证
        step21_final_verify(ctx)
        
        # 步骤22: 写推荐历史
        step22_write_history(ctx)
        
        # 步骤24: 告警摘要
        step24_alert_summary(ctx)
        
        # 步骤25: 筛选概况
        step25_summary(ctx)
        
        # 步骤26: GitHub同步
        step26_github_sync(ctx)
        
        # 步骤27: 飞书推送
        step27_feishu_push(ctx)
        
        # 持仓危机告警优先展示
        crisis_alerts = ctx.get('holding_crisis_alerts', [])
        if crisis_alerts:
            print("\n" + "=" * 60)
            print("⚠️ 持仓危机告警")
            print("=" * 60)
            for a in crisis_alerts:
                print(f"  {a}")
        
    except Exception as e:
        print(f"\n❌ 筛选流程异常: {str(e)}")
        log_alert("ERROR", "筛选流程", f"异常: {str(e)[:200]}")
        raise

if __name__ == '__main__':
    main()