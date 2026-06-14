#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤13: 五策略筛选
"""
from lib.core import *

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
        volume_ratio = c.get('volume_ratio')
        source = ctx.get('_data_source', 'sina')
        # Sina API无volume_ratio，基于成交额+振幅精确代理量比
        # 多档分级：>=5亿→2.2, >=2亿→1.8, >=1亿→1.5, >=5000万→1.1, >=3000万→0.8, >=1000万→0.5, <1000万→0.3
        # 额外修正：振幅≥5%→+0.3(活跃信号)，振幅<2%→-0.2(滞涨信号)
        if volume_ratio is None:
            if amount >= 500_000_000:
                volume_ratio = 2.2
            elif amount >= 200_000_000:
                volume_ratio = 1.8
            elif amount >= 100_000_000:
                volume_ratio = 1.5
            elif amount >= 50_000_000:
                volume_ratio = 1.1
            elif amount >= 30_000_000:
                volume_ratio = 0.8
            elif amount >= 10_000_000:
                volume_ratio = 0.5
            else:
                volume_ratio = 0.3
            # 振幅修正
            amplitude_val = c.get('amplitude', 0)
            if amplitude_val >= 5:
                volume_ratio = min(3.0, volume_ratio + 0.3)
            elif amplitude_val < 2:
                volume_ratio = max(0.2, volume_ratio - 0.2)
        
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
        
        # 策略B: 超跌反弹 (跌幅-1%到-5%、活跃度中等、缩量特征)
        # Sina降级时用成交额+振幅代理缩量：成交额5千万~3亿+振幅≥2%→缩量特征
        # B需要缩量（低换手），Sina无换手率字段，以「成交额适中(非巨额)」代理
        if source == 'sina':
            b_ok = (-5 <= change_pct <= -1 and is_moderate and amplitude >= 2 
                    and 50_000_000 <= amount <= 300_000_000)
        else:
            b_ok = (-5 <= change_pct <= -1 and is_moderate and amplitude >= 2)
        if b_ok:
            strategies.append(('B', '超跌反弹', 1.5))
        
        # 策略B: 更深超跌
        if -7 <= change_pct < -5 and is_moderate:
            strategies.append(('B', '深度超跌反弹', 1))
        
        # 策略B增强：下跌缩量（成交额1千万~1亿+振幅≥3%→恐慌抛售后缩量企稳）
        if source == 'sina' and -7 <= change_pct <= -2 and 10_000_000 <= amount <= 100_000_000 and amplitude >= 3:
            strategies.append(('B', '超跌缩量企稳', 1.2))
        
        # 策略C: 事件驱动
        # 财报季：活跃标的+温和涨幅
        if is_earnings and 0 < change_pct <= 5 and is_active:
            strategies.append(('C', '事件驱动(财报季)', 0.5))
        # 非财报季：底部放量反弹+振幅>4%（可能是消息驱动，成交额≥2亿）
        if not is_earnings and 1 <= change_pct <= 4 and is_active and amplitude >= 4 and amount >= 200_000_000:
            strategies.append(('C', '事件驱动(放量异动)', 1))
        
        # 策略D: 回调企稳 (极温和涨幅0-1.5%+中等活跃+收盘>开盘+量比>0.8)
        if 0 < change_pct <= 1.5 and is_moderate and close > open_p and volume_ratio >= 0.8:
            strategies.append(('D', '回调企稳', 0.5))
        
        # 策略D增强: 主力流入信号+量比>0.8
        if main_inflow and main_inflow > 0 and 0 < change_pct < 2 and volume_ratio >= 0.8:
            strategies.append(('D', '回调企稳(主力流入)', 1))
        
        # 策略E: 资金埋伏 (温和涨幅1-3%+高活跃+收盘>开盘+有一定振幅)
        if 1 <= change_pct <= 3 and is_active and close > open_p and amplitude >= 2:
            strategies.append(('E', '资金埋伏', 1))
        
        # 策略E: 强势资金 (涨幅3-5%+高活跃+收盘>开盘+高振幅)
        if 3 < change_pct <= 5 and is_active and close > open_p and amplitude >= 3:
            strategies.append(('E', '强势资金', 1.5))
        
        # 兜底策略：涨幅适中+活跃→A
        if not strategies and 2 < change_pct <= 5 and is_active and close > open_p:
            strategies.append(('A', '动量延续(活跃)', 1))
        
        # 兜底：极温和+活跃→D
        if not strategies and 0 < change_pct <= 2 and is_active and close > open_p and volume_ratio >= 0.8:
            strategies.append(('D', '回调企稳(活跃)', 0.5))
        
        if not strategies and -3 <= change_pct < 0 and is_moderate:
            strategies.append(('B', '超跌反弹(弱势)', 0.5))
        # Sina降级兜底：振幅异常+量价背离的下跌股也可能有反弹机会
        if not strategies and source == 'sina' and -3 <= change_pct < 0 and amplitude >= 3 and amount >= 50_000_000:
            strategies.append(('B', '超跌反弹(量价异动)', 0.8))
        
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