#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤5-9C: 推荐历史清理、文件初始化、财报季检测、大盘判断、板块轮动、最大持仓天数、回撤断路器、兑现率闭环
"""
from lib.core import *

# ============================================================
# 步骤5: 推荐历史清理
# ============================================================
def step5_clean_history(ctx):
    print("\n" + "=" * 60)
    print("步骤5: 推荐历史清理（逐日期文件独立清理）")
    print("=" * 60)
    
    data_date = ctx['data_date']
    
    try:
        cutoff_7d_dt = datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=7)
        cutoff_90d_dt = datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=90)
        cutoff_7d = cutoff_7d_dt.strftime('%Y-%m-%d')
        cutoff_90d = cutoff_90d_dt.strftime('%Y-%m-%d')
        
        total_cleaned = 0
        for f in sorted(os.listdir(DATA_DIR)):
            if not (f.startswith("推荐历史_") and f.endswith(".json")):
                continue
            filepath = os.path.join(DATA_DIR, f)
            records = safe_read_json(filepath)
            if not records:
                continue
            new_records = []
            for r in records:
                t = r.get('type', '')
                if t in ('weekly_review', 'strategy_check', 'do_T_eval', 'do_T'):
                    new_records.append(r)
                elif t == 'holding':
                    d = r.get('update_date', '')
                    if d >= cutoff_90d:
                        new_records.append(r)
                elif t == 'recommendation':
                    d = r.get('date', '')
                    if d >= cutoff_7d:
                        new_records.append(r)
                else:
                    new_records.append(r)
            if len(new_records) < len(records):
                safe_write_json(filepath, new_records)
                total_cleaned += len(records) - len(new_records)
        if total_cleaned > 0:
            print(f"  已清理 {total_cleaned} 条过期记录")
            log_alert("INFO", "清理", f"已清理{total_cleaned}条过期记录")
        else:
            print("  无需清理")
            log_alert("INFO", "清理", "无需清理")
        
        # Re-read all_history after cleanup
        ctx['all_history'] = read_all_history()
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
    
    # 版本一致性检查：比对历史记录版本 vs 当前代码版本
    all_history = ctx.get('all_history', [])
    last_check = None
    for r in reversed(all_history):
        if r.get('type') == 'strategy_check':
            last_check = r
            break
    
    if last_check:
        history_version = last_check.get('version', 'unknown')
        if history_version != BUILTIN_VERSION:
            print(f"  版本变更: 推荐历史{history_version} → 当前代码{BUILTIN_VERSION}，以代码为准")
            log_alert("INFO", "版本检查", f"推荐历史版本{history_version}≠当前代码{BUILTIN_VERSION}，以代码为准")
            ctx['_version_changed'] = True
        else:
            print(f"  版本一致: {BUILTIN_VERSION}")
            log_alert("INFO", "版本检查", f"版本一致{BUILTIN_VERSION}")
            ctx['_version_changed'] = False
    
    # 首次运行或版本变更→追加strategy_check（使用当前代码版本）
    if last_check is None or (last_check.get('version', '') != BUILTIN_VERSION):
        ctx['_version_changed'] = True
        strategy_check = {
            "type": "strategy_check",
            "version": BUILTIN_VERSION,
            "params": params,
            "date": ctx['beijing_date'],
            "checks": {}
        }
        safe_append_json(f"{DATA_DIR}/推荐历史_{ctx['beijing_date'].replace('-', '')}.json", strategy_check)
        print(f"  已追加 strategy_check ({BUILTIN_VERSION})")
        log_alert("INFO", "策略检查", f"首次运行/版本变更，追加strategy_check {BUILTIN_VERSION}")
    
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
    print("步骤8: 大盘判断（MA均线+涨跌比+成交量）")
    print("=" * 60)
    
    sh_chg = ctx.get('sh_index_change', 0)
    market = '震荡'
    position = 55
    
    # 获取上证指数历史K线（用于计算MA5/MA10/MA20）
    ma5 = ma10 = ma20 = None
    avg_volume_20 = None
    today_volume = None
    up_down_ratio = None
    closes = []
    
    try:
        # 使用东方财富K线API获取上证历史数据
        import urllib.parse
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": "1.000001",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
            "klt": "101",  # 日K
            "fqt": "1",    # 前复权
            "end": "20500101",
            "lmt": "30",   # 取30日数据
        }
        req = urllib.request.Request(
            f"{url}?{urllib.parse.urlencode(params)}",
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        klines = data.get('data', {}).get('klines', [])
        
        if klines and len(klines) >= 20:
            volumes = []
            for line in klines:
                parts = line.split(',')
                closes.append(float(parts[2]))  # 收盘价
                volumes.append(float(parts[5]))  # 成交量
            
            if len(closes) >= 20:
                ma5 = round(sum(closes[-5:]) / 5, 2)
                ma10 = round(sum(closes[-10:]) / 10, 2)
                ma20 = round(sum(closes[-20:]) / 20, 2)
                avg_volume_20 = round(sum(volumes[-21:-1]) / 20, 0)  # 前20日均量（不含今天）
                today_volume = volumes[-1]
                
                print(f"  MA5={ma5} MA10={ma10} MA20={ma20}")
                print(f"  今日成交量: {today_volume:.0f}  |  20日均量: {avg_volume_20:.0f}")
                
                # 涨跌比（从涨跌数估算）
                if sh_chg > 0:
                    up_down_ratio = 1.5
                elif sh_chg < 0:
                    up_down_ratio = 0.67
                else:
                    up_down_ratio = 1.0
    except Exception as e:
        print(f"  K线数据获取失败({str(e)[:40]})，使用涨跌幅简化判断")
    
    sh_price = closes[-1] if closes else None
    
    if ma5 and ma10 and ma20 and sh_price:
        # 三级判断：MA均线 + 涨跌比 + 成交量
        ma_bullish = ma5 > ma10 > ma20  # 均线多头排列
        ma_above = sh_price > ma20 * 0.98  # 价格在MA20上方
        ma_neutral = ma20 * 0.98 <= sh_price <= ma20 * 1.02  # 价格在MA20附近
        
        vol_high = today_volume and avg_volume_20 and today_volume > avg_volume_20 * 1.2
        vol_low = today_volume and avg_volume_20 and today_volume < avg_volume_20 * 0.8
        breadth_strong = up_down_ratio and up_down_ratio > 2.0
        breadth_weak = up_down_ratio and up_down_ratio < 0.5
        
        # 强市条件
        if ma_bullish and breadth_strong and vol_high:
            market = '强市'
            position = 75
        # 弱市条件
        elif sh_price < ma20 * 0.98 or breadth_weak or vol_low:
            market = '弱市'
            position = 35
        else:
            market = '震荡'
            position = 55
    else:
        # 降级：基于涨跌幅简判
        if sh_chg > 1:
            market = '强市'
            position = 75
        elif sh_chg > -1:
            market = '震荡'
            position = 55
        else:
            market = '弱市'
            position = 35
    
    # 外围市场压制
    if ctx.get('foreign_weak'):
        if market == '强市':
            market = '震荡'
            position = 55
        elif market == '震荡':
            market = '弱市'
            position = 35
        print(f"  外围偏空 → 降档至 {market}")
    
    ctx['market_condition'] = market
    ctx['position'] = position
    ctx['_ma5'] = ma5
    ctx['_ma10'] = ma10
    ctx['_ma20'] = ma20
    
    # 仓位分布
    if market == '强市':
        position_plan = {'A': 37, 'B': 11, 'C': 11, 'D': 11, 'E': 6}
    elif market == '震荡':
        position_plan = {'A': 15, 'B': 12, 'C': 9, 'D': 12, 'E': 6}
    else:
        position_plan = {'A': 0, 'B': 14, 'C': 7, 'D': 10, 'E': 4}
    
    # 弱市策略A关闭
    if market == '弱市':
        position_plan['A'] = 0
    
    # 策略D被暂停（人民币波动>0.5%）
    if ctx.get('pause_strategy_d'):
        position_plan['D'] = 0
        print(f"  ⚠️ 策略D暂停（人民币波动>0.5%）")
    
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
    restore_threshold = ctx['params']['conversion_rate_restore']
    consecutive_days = ctx['params']['conversion_rate_consecutive_days']
    
    # 冷启动保护
    recos = [r for r in all_history if r.get('type') == 'recommendation']
    if len(recos) < 10:
        print(f"  推荐记录不足10条({len(recos)}条)，跳过兑现率检查")
        ctx['conversion_rate_ok'] = True
        return
    
    # 统计最近window_days个交易日的T+1兑现率
    # 兑现定义: T+1收盘涨幅>2%
    data_dt = datetime.strptime(ctx['data_date'], '%Y-%m-%d')
    window_start = data_dt - timedelta(days=window_days)
    
    # 按日期分组推荐记录
    recos_by_date = {}
    for r in recos:
        r_date_str = r.get('date', '')
        if not r_date_str:
            continue
        try:
            r_date = datetime.strptime(r_date_str, '%Y-%m-%d')
        except ValueError:
            continue
        if r_date < window_start or r_date >= data_dt:
            continue
        if r_date_str not in recos_by_date:
            recos_by_date[r_date_str] = []
        recos_by_date[r_date_str].append(r)
    
    # 检查每笔推荐的 T+1 兑现情况（通过查找次日同一code的收盘价变化）
    total_checked = 0
    total_converted = 0
    daily_rates = {}
    
    for r_date_str, day_recos in recos_by_date.items():
        r_dt = datetime.strptime(r_date_str, '%Y-%m-%d')
        t1_date_str = (r_dt + timedelta(days=1)).strftime('%Y-%m-%d')
        
        day_checked = 0
        day_converted = 0
        
        for rec in day_recos:
            code = rec.get('code', '')
            entry_price = rec.get('close') or rec.get('entry')
            if not entry_price or entry_price <= 0:
                continue
            
            # 搜索T+1日同一code的行情记录
            t1_found = False
            for r2 in all_history:
                if r2.get('type') == 'recommendation' and r2.get('date') == t1_date_str and r2.get('code') == code:
                    t1_close = r2.get('close') or r2.get('entry')
                    if t1_close and t1_close > 0:
                        t1_chg = (t1_close - entry_price) / entry_price * 100
                        day_checked += 1
                        if t1_chg > 2:
                            day_converted += 1
                        t1_found = True
                        break
            # 如果T+1有holding记录也检查
            if not t1_found:
                for r2 in all_history:
                    if r2.get('type') == 'holding' and r2.get('code') == code:
                        # 检查update_date是否在T+1附近
                        upd = r2.get('update_date', '')
                        if upd == t1_date_str:
                            t1_current = r2.get('current')
                            if t1_current and t1_current > 0:
                                t1_chg = (t1_current - entry_price) / entry_price * 100
                                day_checked += 1
                                if t1_chg > 2:
                                    day_converted += 1
                                break
        
        if day_checked > 0:
            daily_rates[r_date_str] = day_converted / day_checked
            total_checked += day_checked
            total_converted += day_converted
    
    if total_checked == 0:
        print(f"  无有效T+1数据，跳过兑现率检查")
        ctx['conversion_rate_ok'] = True
        return
    
    overall_rate = total_converted / total_checked
    print(f"  兑现率: {total_converted}/{total_checked} = {overall_rate*100:.1f}% (阈值{threshold*100}%)")
    ctx['conversion_rate_value'] = round(overall_rate, 3)
    
    # 检查连续低兑现率
    sorted_dates = sorted(daily_rates.keys())
    consecutive_low = 0
    for d in reversed(sorted_dates):
        if daily_rates[d] < threshold:
            consecutive_low += 1
        else:
            break
    
    if overall_rate < threshold:
        print(f"  ⚠️ 兑现率{overall_rate*100:.1f}% < {threshold*100}% → 降一档仓位")
        current_pos = ctx.get('position', 55)
        if current_pos >= 70:
            ctx['position'] = 55
        elif current_pos >= 50:
            ctx['position'] = 35
        else:
            ctx['position'] = 20
        ctx['conversion_rate_ok'] = False
        
        if consecutive_low >= consecutive_days:
            print(f"  ⚠️ 连续{consecutive_low}天兑现率<{threshold*100}% → 暂停推荐1天")
            ctx['skip'] = True
            log_alert("WARNING", "兑现率闭环", f"连续{consecutive_low}天低兑现率，暂停推荐")
    elif overall_rate >= restore_threshold:
        print(f"  兑现率{overall_rate*100:.1f}% ≥ {restore_threshold*100}% → 仓位恢复至正常档位")
        ctx['conversion_rate_ok'] = True
        ctx['position'] = 55  # 恢复默认仓位（步骤8大盘判断会再调整）
    else:
        ctx['conversion_rate_ok'] = True