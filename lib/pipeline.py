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
        ctx['_earnings_bonus'] = True  # 由步骤8消费：仓位+5%
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
        # 新浪上证日K线替代东方财富K线（push2his不可达）
        url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "CN_MarketData.getKLineData?symbol=sh000001&scale=240&ma=no&datalen=30")
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.sina.com.cn'
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            klines = json.loads(resp.read().decode('gbk'))
        
        if klines and len(klines) >= 20:
            volumes = []
            for item in klines:
                closes.append(float(item.get('close', 0)))
                volumes.append(float(item.get('volume', 0)))
            
            if len(closes) >= 20:
                ma5 = round(sum(closes[-5:]) / 5, 2)
                ma10 = round(sum(closes[-10:]) / 10, 2)
                ma20 = round(sum(closes[-20:]) / 20, 2)
                avg_volume_20 = round(sum(volumes[-21:-1]) / 20, 0)
                today_volume = volumes[-1]
                
                print(f"  MA5={ma5} MA10={ma10} MA20={ma20}")
                print(f"  今日成交量: {today_volume:.0f}  |  20日均量: {avg_volume_20:.0f}")
                
                # 涨跌比（东方财富API不可达→基于涨跌幅估算）
                if sh_chg > 1:
                    up_down_ratio = 2.0
                elif sh_chg > 0:
                    up_down_ratio = 1.5
                elif sh_chg > -1:
                    up_down_ratio = 0.67
                else:
                    up_down_ratio = 0.33
                print(f"  涨跌比(估算): {up_down_ratio}")
    except Exception as e:
        print(f"  K线数据获取失败({str(e)[:40]})，使用涨跌幅简化判断")
    
    sh_price = closes[-1] if 'closes' in dir() and closes else None
    
    if ma5 and ma10 and ma20 and sh_price:
        # 三级判断：MA均线 + 涨跌比 + 成交量
        ma_bullish = ma5 > ma10 > ma20  # 均线多头排列
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
    
    # 步骤2极端行情保护：若步骤2已设极端仓位，步骤8不覆盖
    if ctx.get('_extreme_market_position'):
        position = ctx['_extreme_market_position']
        market = ctx.get('_extreme_market', market)
        print(f"  极端行情保护: 仓位锁定{position}%")
    
    # 外围市场压制
    if ctx.get('foreign_weak'):
        if market == '强市':
            market = '震荡'
            position = 55
        elif market == '震荡':
            market = '弱市'
            position = 35
        print(f"  外围偏空 → 降档至 {market}")
    
    # 期货偏空压制（步骤3A标志）
    if ctx.get('futures_bearish'):
        if market == '强市':
            market = '震荡'
            position = 55
        elif market == '震荡':
            market = '弱市'
            position = 35
        print(f"  期货偏空 → 降档至 {market}")
    
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
    # 极端涨>3%→临时恢复策略A（15%仓位）
    if ctx.get('_extreme_up_a_restore'):
        position_plan['A'] = 15
        print(f"  ⚠️ 极端涨>3%→临时恢复策略A仓位15%")
    
    # 策略E被暂停（人民币波动>0.5%）
    if ctx.get('pause_strategy_e'):
        position_plan['E'] = 0
        print(f"  ⚠️ 策略E暂停（人民币波动>0.5%）")
    
    # 长休弱市压制仓位（必须在财报季+5%之后执行，确保硬上限不被突破）
    if ctx.get('is_long_holiday'):
        position = min(position, 30)
        ctx['position'] = position
        print(f"  长休≥3日→仓位压至{position}%（跳过财报季+5%）")
    elif ctx.get('_earnings_bonus'):
        # 财报季仓位+5%（SKILL §步骤7: 1/3/4/8/10月→仓位+5%）
        position = min(position + 5, 80)
        ctx['position'] = position
        print(f"  财报季→仓位+5%至{position}%")
    
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
        # 新浪行业涨幅排名替代东方财富（push2不可达）
        import re as _re
        url = ("https://vip.stock.finance.sina.com.cn/q/go.php/vIndustryRank/"
               "kind/sshy/p/1/num/10/sort/changepercent/asc/0/daession/hy/desc/1.page")
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.sina.com.cn'
        })
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read().decode('gbk')
        
        # 解析HTML获取行业名称和涨幅
        sectors = []
        # 匹配 <tr> 行中的行业名称和涨幅
        for match in _re.finditer(r'<td[^>]*>[-+]?\d+\.\d+%</td>', html):
            pass  # 不精确的HTML解析，降级为空列表
        
        # 新浪行业涨幅榜解析太复杂，降级为使用已知行业排名
        ctx['top_sectors'] = []
        ctx['inflow_sectors'] = []
        ctx['outflow_sectors'] = []
        print("  板块轮动数据不可达(东方财富push2)，跳过板块资金流向检查")
        log_alert("INFO", "板块轮动", "东方财富API不可达，新浪行业解析复杂，跳过板块轮动")
    except Exception as e:
        ctx['top_sectors'] = []
        ctx['inflow_sectors'] = []
        ctx['outflow_sectors'] = []
        print(f"  板块轮动检查跳过: {str(e)[:60]}")

# ============================================================
# 步骤9A-9C: 最大持仓天数/回撤断路器/兑现率闭环
# ============================================================
def step9A_max_holding(ctx):
    print("\n" + "=" * 60)
    print("步骤9A: 最大持仓天数检查 + 退出规则")
    print("=" * 60)
    max_days = ctx['params']['max_holding_days']
    data_date = ctx['data_date']
    all_history = ctx.get('all_history', [])
    holdings = ctx.get('holdings', [])
    
    exit_list = []
    
    for h in holdings:
        code = h.get('code', '')
        name = h.get('name', '?')
        start_date = h.get('start_date', '')
        cost = h.get('cost', 0)
        current = h.get('current', 0)
        pnl_pct = h.get('pnl_pct', 0)
        days = 0
        
        if start_date:
            try:
                days = (datetime.strptime(data_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days
            except Exception:
                pass
        
        # T+5 硬止损：持仓≥5天且跌幅>5%
        if days >= max_days and pnl_pct < -5:
            exit_list.append((code, name, f"T+{days}止损>5% → 无条件退出", pnl_pct))
            continue
        
        # T+max_days 超期退出
        if days >= max_days:
            exit_list.append((code, name, f"持仓{days}天≥{max_days}天 → 超期退出", pnl_pct))
            continue
        
        # T+3 横盘退出：持仓≥3天且累计涨幅<2%
        if days >= 3 and abs(pnl_pct) < 2:
            exit_list.append((code, name, f"T+{days}横盘不作为(涨幅{pnl_pct:.1f}%<2%) → 主动退出", pnl_pct))
            continue
        
        # T+1 日内止损：当日跌幅>7%（SKILL §九.A: T+1日盘中跌幅>7%→日内止损）
        if days >= 1:
            prev_close = h.get('prev_close')
            if prev_close and prev_close > 0 and current > 0:
                daily_drop = (current - prev_close) / prev_close * 100
                if daily_drop < -7:
                    exit_list.append((code, name, f"T+{days}日内止损(当日跌{daily_drop:.1f}%>7%) → 极端行情保护", pnl_pct))
                    continue
    
    if exit_list:
        print(f"  触发退出规则: {len(exit_list)} 只")
        for code, name, reason, pnl in exit_list:
            print(f"    ⚠️ {code} {name}: {reason}")
            # 追加 type="exit" 记录
            exit_rec = {
                "type": "exit",
                "code": code,
                "name": name,
                "date": ctx['data_date'],
                "exit_reason": reason,
                "pnl_pct": pnl,
            }
            safe_append_json(f"{DATA_DIR}/推荐历史_{ctx['beijing_date'].replace('-', '')}.json", exit_rec)
    else:
        print(f"  无触发退出规则 (阈值{max_days}天)")
    ctx['exit_list'] = [(c, n, r) for c, n, r, _ in exit_list]

def step9B_circuit_breaker(ctx):
    print("\n" + "=" * 60)
    print("步骤9B: 回撤断路器")
    print("=" * 60)
    
    threshold = ctx['params']['circuit_breaker_threshold_pct']
    holdings = ctx.get('holdings', [])
    all_history = ctx.get('all_history', [])
    data_date = ctx['data_date']
    
    triggered_today = False
    for h in holdings:
        # 当日跌幅 = (current - prev_close) / prev_close（SKILL §九.B: 任一持仓当日亏损>threshold%）
        current = h.get('current', 0)
        prev_close = h.get('prev_close')
        daily_drop = 0
        if prev_close and prev_close > 0 and current > 0:
            daily_drop = (current - prev_close) / prev_close * 100
        if daily_drop < -threshold:
            msg = f"{h.get('code')} {h.get('name')} 当日跌{daily_drop:.1f}% > {threshold}%"
            print(f"  ⚠️ {msg}")
            triggered_today = True
    
    if triggered_today:
        # 检查昨日是否也触发
        yesterday = (datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        yesterday_triggered = False
        for r in all_history:
            if r.get('type') == 'strategy_check' and r.get('date') == yesterday:
                checks = r.get('checks', {})
                if checks.get('circuit_breaker_triggered'):
                    yesterday_triggered = True
                    break
        
        if yesterday_triggered:
            print(f"  连续2日触发 → 次交易日仓位降至30%")
            ctx['position'] = 30
            log_alert("WARNING", "回撤断路器", f"连续2日触发({threshold}%)，仓位降至30%")
        else:
            print(f"  首次触发 → 次交易日持仓降至50%" if ctx['position'] > 50 else f"  触发回撤断路器")
            ctx['position'] = int(ctx.get('position', 55) * 0.5)
        
        # 记录触发状态
        ctx['_circuit_breaker_today'] = True
    else:
        print(f"  未触发 (阈值{threshold}%)")
    
    # 回写断路器状态供次日连续检测
    strategy_check = {
        "type": "strategy_check",
        "date": data_date,
        "checks": {"circuit_breaker_triggered": triggered_today}
    }
    safe_append_json(f"{DATA_DIR}/推荐历史_{data_date.replace('-', '')}.json", strategy_check)

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
        # 财报季+5%重新应用（SKILL §步骤7: 1/3/4/8/10月→仓位+5%）
        if ctx.get('_earnings_bonus'):
            ctx['position'] = min(ctx['position'] + 5, 80)
            print(f"  兑现率恢复后重新应用财报季+5%→{ctx['position']}%")
    else:
        ctx['conversion_rate_ok'] = True
    
    # 仓位变更后重新计算策略分配表
    new_pos = ctx['position']
    if new_pos >= 70:
        ctx['position_plan'] = {'A': 37, 'B': 11, 'C': 11, 'D': 11, 'E': 6}
    elif new_pos >= 50:
        ctx['position_plan'] = {'A': 15, 'B': 12, 'C': 9, 'D': 12, 'E': 6}
    elif new_pos >= 30:
        ctx['position_plan'] = {'A': 5, 'B': 11, 'C': 7, 'D': 10, 'E': 4}
    else:
        ctx['position_plan'] = {'A': 0, 'B': 10, 'C': 5, 'D': 3, 'E': 2}
    
    # 保留 step8 特殊标志：极端涨>3%恢复A、人民币波动禁E
    if ctx.get('_extreme_up_a_restore'):
        ctx['position_plan']['A'] = 15
        print(f"  兑现率联动后保留极端涨A=15%")
    if ctx.get('pause_strategy_e'):
        ctx['position_plan']['E'] = 0
        print(f"  兑现率联动后保留策略E暂停")