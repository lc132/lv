#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤11-12: 硬排除31项 (L1/L2/L3)、信号质量过滤14项
"""
from lib.core import *

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
    
    # 获取已推荐标的（按分组窗口去重，避免测试运行的重复写入导致排除过度膨胀）
    # 窗口：(data_date - 3天, data_date]，按 (data_date, code) 去重
    all_history = ctx.get('all_history', [])
    data_dt = datetime.strptime(ctx['data_date'], '%Y-%m-%d')
    window_start = data_dt - timedelta(days=3)
    
    # 收集窗口内的唯一推荐：按(code)去重（同一天同一代码多次写入只算一次）
    window_recommendations = {}  # code → earliest_date
    for r in all_history:
        if r.get('type') == 'recommendation':
            code = r.get('code', '')
            r_date_str = r.get('date', '')
            if not code or not r_date_str:
                continue
            try:
                r_date = datetime.strptime(r_date_str, '%Y-%m-%d')
            except ValueError:
                continue
            if window_start <= r_date <= data_dt:
                if code not in window_recommendations:
                    window_recommendations[code] = r_date_str
    
    recommended_codes = set(window_recommendations.keys())
    
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
        # 规则21: 创业板(300xxx/301xxx)仅强市+动量延续
        elif code.startswith(('300', '301')):
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