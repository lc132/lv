#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v6.13.36 市场微观结构过滤模块
在最终候选池输出前，基于流动性与冲击成本、消息敏感度进行严格过滤，
防止买入难以卖出的标的，确保短线获利的可行性。
"""

def compute_amihud_illiquidity(c):
    """
    Amihud非流动性指标
    = |涨跌幅%| / 成交额(万元)
    值越低 → 单位成交额对价格冲击越小 → 流动性越好
    阈值: < 0.5 优秀, 0.5-2.0 正常, > 2.0 危险
    """
    chg = abs(c.get('change_pct', 0) or 0)
    amount = c.get('amount', 0) or 0
    if amount <= 0:
        return 999
    amount_wan = amount / 10000  # 元→万元
    return chg / amount_wan if amount_wan > 0 else 999


def compute_tick_cost_proxy(c):
    """
    Tick价差代理指标
    由于A股最小报价单位统一为0.01元，相对价差 = 0.01/收盘价
    股价越高 → 相对价差越小 → 冲击成本越低
    返回: 0-1 分（越高越好）
    """
    close = c.get('close', 0) or 0
    if close <= 0:
        return 0
    if close >= 50:
        return 1.0
    elif close >= 20:
        return 0.75
    elif close >= 10:
        return 0.5
    elif close >= 5:
        return 0.25
    return 0


def compute_news_sensitivity(kline_data, code):
    """
    消息敏感度评估（0-3分）
    基于历史K线波动特征，评估标的对消息流的反应强度。
    指标1: 20日平均振幅 → 反映日内波动潜力
    指标2: 重大波动频率 → 20日内涨跌幅>5%的天数
    指标3: 跳空频率 → 20日内开-收跳空>2%的天数
    """
    kd = kline_data.get(code, {})
    if not kd:
        return 0
    
    closes = kd.get('closes', [])
    highs = kd.get('highs', [])
    lows = kd.get('lows', [])
    opens = kd.get('opens', closes)  # 若无独立开盘价，用收盘价回退
    
    if len(closes) < 20:
        return 0
    
    score = 0
    n = min(len(closes), 20)
    
    # 指标1: 20日平均振幅
    amps = []
    for i in range(-n + 1, 0):  # v6.12.8: fix n==20 closes[-21] IndexError
        if closes[i - 1] > 0:
            amp = (highs[i] - lows[i]) / closes[i - 1] * 100
            amps.append(amp)
    avg_amp = sum(amps) / len(amps) if amps else 0
    
    if avg_amp >= 5:
        score += 1  # 高波动，消息敏感
    elif avg_amp >= 3:
        score += 0  # 中等波动，不加分
    
    # 指标2: 涨跌幅>5%的频次
    big_move_count = 0
    for i in range(-n, 0):
        if i - 1 < -len(closes):
            continue
        if closes[i - 1] > 0:
            ret = abs(closes[i] - closes[i - 1]) / closes[i - 1]
            if ret > 0.05:
                big_move_count += 1
    if big_move_count >= 3:
        score += 1
    
    # 指标3: 跳空频率
    gap_count = 0
    for i in range(-n, 0):
        if i - 1 < -len(opens):
            continue
        if closes[i - 1] > 0 and opens[i] > 0:
            gap = abs(opens[i] - closes[i - 1]) / closes[i - 1]
            if gap > 0.02:
                gap_count += 1
    if gap_count >= 2:
        score += 1
    
    return score


def compute_liquidity_score(c):
    """
    流动性评分（0-4分）
    综合换手率、Amihud非流动性、Tick价差
    """
    score = 0
    turnover = c.get('turnover', 0) or 0
    amihud = compute_amihud_illiquidity(c)
    tick = compute_tick_cost_proxy(c)
    
    # 换手率评分（0-2分）
    if 5 <= turnover <= 15:
        score += 2  # 理想换手率
    elif 2 <= turnover < 5:
        score += 1  # 可接受
    elif 15 < turnover <= 25:
        score += 1  # 偏高但可接受
    # turnover < 2 → 0分（硬过滤在filter中处理）
    # turnover > 25 → 0分（过度投机）
    
    # Amihud 评分（0-1分）
    if amihud < 0.5:
        score += 1  # 流动性优秀
    elif amihud < 1.0:
        score += 0.5
    
    # Tick价差 评分（0-1分）
    if tick >= 0.5:
        score += 1
    elif tick >= 0.25:
        score += 0.5
    
    return score


def microstructure_filter(candidates, kline_data):
    """
    步骤15: 市场微观结构过滤
    返回: (通过列表, 过滤列表, 统计信息)
    
    硬过滤条件:
    - 换手率 < 2% → 流动性不足，难以卖出
    - Amihud > 2.0 → 冲击成本过高
    - 20日平均振幅 < 2% → 波动不足，短线无获利空间
    
    评分调整:
    - 流动性评分(0-4) → 加入c['_microstructure_score']
    - 消息敏感度(0-3) → 加入c['_news_sensitivity']
    - 两项合计最多+7分，按比例折算到最终score
    """
    passed = []
    filtered = []
    stats = {'total': len(candidates), 'passed': 0, 'filtered': 0,
             'liq_filtered': 0, 'vol_filtered': 0, 'impact_filtered': 0}
    
    for c in candidates:
        code = c.get('code', '')
        turnover = c.get('turnover', 0) or 0
        amihud = compute_amihud_illiquidity(c)
        
        # 硬过滤1: 换手率 < 2%
        if turnover < 2:
            filtered.append({'code': code, 'name': c.get('name', ''),
                           'reason': f'换手率{turnover:.1f}%<2%', 'type': 'liquidity'})
            stats['liq_filtered'] += 1
            continue
        
        # 硬过滤2: Amihud > 2.0（冲击成本过高）
        if amihud > 2.0:
            filtered.append({'code': code, 'name': c.get('name', ''),
                           'reason': f'Amihud{amihud:.2f}>2.0(冲击成本高)', 'type': 'impact'})
            stats['impact_filtered'] += 1
            continue
        
        # 硬过滤3: 20日平均振幅 < 2%（波动不足）
        kd = kline_data.get(code, {})
        if kd:
            closes_k = kd.get('closes', [])
            highs_k = kd.get('highs', [])
            lows_k = kd.get('lows', [])
            if len(closes_k) >= 20 and len(highs_k) >= 20 and len(lows_k) >= 20:
                amps_20 = []
                for i in range(-20, 0):
                    if i - 1 >= -len(closes_k) and closes_k[i - 1] > 0:
                        amps_20.append((highs_k[i] - lows_k[i]) / closes_k[i - 1] * 100)
                if amps_20:
                    avg_amp_20 = sum(amps_20) / len(amps_20)
                    if avg_amp_20 < 2:
                        filtered.append({'code': code, 'name': c.get('name', ''),
                                       'reason': f'20日均振幅{avg_amp_20:.1f}%<2%', 'type': 'volatility'})
                        stats['vol_filtered'] += 1
                        continue
        
        # 通过硬过滤，计算加分
        liq_score = compute_liquidity_score(c)
        news_sens = compute_news_sensitivity(kline_data, code)
        c['_microstructure_score'] = liq_score
        c['_news_sensitivity'] = news_sens
        c['_amihud'] = round(amihud, 3)
        
        # 微观结构加分折算到最终score（最多+7分 → 折算最多+3分）
        micro_bonus = round((liq_score + news_sens) * 3 / 7)
        c['score'] = c.get('score', 0) + micro_bonus
        passed.append(c)
    
    stats['filtered'] = len(filtered)
    stats['passed'] = len(passed)
    return passed, filtered, stats