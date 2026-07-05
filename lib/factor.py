#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v6.13.4 多因子共振模块
主力底仓埋伏 + 短线放量起爆 双重共振检测
"""

def compute_main_force_position(kline_data, c):
    """
    主力底仓检测（0-3分）
    分析近20日K线数据，检测主力资金建仓痕迹。
    指标1: 连续缩量小阳线（5日内）
    指标2: 底部放量（60日跌幅>15% + 近5日放量）
    指标3: 主力资金连续流入（5日内≥3日净流入）
    """
    code = c.get('code', '')
    kd = kline_data.get(code, {})
    if not kd:
        return 0
    
    closes = kd.get('closes', [])
    volumes = kd.get('volumes', [])
    score = 0
    
    if len(closes) < 20 or len(volumes) < 20:
        return 0
    
    # 指标1: 连续缩量小阳线（5日内至少3日满足）
    # v6.12.0: 修复负数索引越界，改用 len 检查
    small_yang_count = 0
    n = len(closes)
    for i in range(-5, 0):
        if n + i - 1 < 0:
            continue
        idx = n + i  # 转为正索引
        if idx - 1 < 0:
            continue
        chg_day = (closes[idx] - closes[idx - 1]) / closes[idx - 1] if closes[idx - 1] > 0 else 0
        if 0 < chg_day <= 0.02:
            avg_vol_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 and sum(volumes[-20:]) > 0 else 0
            if avg_vol_20 > 0 and volumes[idx] < avg_vol_20 * 0.7:
                small_yang_count += 1
    if small_yang_count >= 3:
        score += 1
    
    # 指标2: 底部放量（60日跌幅>15% + 近5日放量）
    # v6.12.0: 修复 60日跌幅需要至少21根K线，改用60日最高价计算
    if len(closes) >= 21 and closes[-21] > 0:
        drop_60d = (closes[-1] - closes[-21]) / closes[-21]
        if drop_60d <= -0.15:
            avg_vol_20 = sum(volumes[-20:]) / 20
            recent_avg_vol = sum(volumes[-5:]) / 5
            if avg_vol_20 > 0 and recent_avg_vol > avg_vol_20 * 1.5:
                ma10 = kd.get('ma10', 0)
                if ma10 > 0 and closes[-1] > ma10:
                    score += 1
    
    # 指标3: 主力资金连续流入（主力净流入>5000万）
    # v6.12.0: 修复阈值 5000→5000万(50000000)，原为5000元误写
    main_inflow = c.get('main_inflow', 0)
    if main_inflow is not None and main_inflow > 50_000_000:
        score += 1
    
    return score


def compute_short_term_breakout(kline_data, c):
    """
    短线放量起爆检测（0-5分）
    指标1: 放量突破（量比>2, 涨幅3-7%, 突破20日最高）
    指标2: 均线金叉（MA5上穿MA10或MA20）
    指标3: MACD金叉+零轴附近
    指标4: 成交量突破（当日量>20日均量×2, 收阳线）
    """
    code = c.get('code', '')
    kd = kline_data.get(code, {})
    if not kd:
        return 0
    
    closes = kd.get('closes', [])
    volumes = kd.get('volumes', [])
    ma5 = kd.get('ma5', 0)
    ma10 = kd.get('ma10', 0)
    ma20 = kd.get('ma20', 0)
    high20 = kd.get('high20', 0)
    dif = kd.get('dif', 0)
    dea = kd.get('dea', 0)
    chg = c.get('change_pct', 0)
    vr = c.get('volume_ratio')
    close_p = kd.get('closes', [0])
    close = close_p[-1] if close_p and close_p[-1] else 0
    op = c.get('open', 0)
    score = 0
    
    if len(closes) < 20:
        return 0
    
    # 指标1: 放量突破（量比>2, 涨幅3-7%, 收盘价突破20日最高价×0.98）
    if vr is not None and vr > 2.0 and 3 <= chg <= 7:
        if high20 > 0 and close >= high20 * 0.98:
            score += 2
    
    # 指标2: 均线金叉
    if len(closes) >= 2 and closes[-2] > 0:
        prev_ma5 = sum(closes[-6:-1]) / 5 if len(closes) >= 6 else 0
        prev_ma10 = sum(closes[-11:-1]) / 10 if len(closes) >= 11 else 0
        if prev_ma5 > 0 and prev_ma10 > 0:
            if prev_ma5 <= prev_ma10 and ma5 > ma10:
                score += 1  # MA5上穿MA10
            elif ma5 > ma20 > 0 and prev_ma5 <= ma20:
                score += 1  # MA5上穿MA20
    
    # 指标3: MACD金叉+零轴附近
    if len(closes) >= 3:
        ema12_prev = closes[-3]
        ema26_prev = closes[-3]
        for pr in closes[-2:]:
            ema12_prev = ema12_prev * 11/13 + pr * 2/13
            ema26_prev = ema26_prev * 25/27 + pr * 2/27
        dif_prev = ema12_prev - ema26_prev
        if dif_prev <= 0 and dif > 0:
            if -0.5 < dif < 0.5:
                score += 1  # MACD金叉+零轴附近
    
    # 指标4: 成交量突破
    avg_vol_20 = sum(volumes[-20:]) / 20
    if len(volumes) >= 1 and avg_vol_20 > 0:
        if volumes[-1] > avg_vol_20 * 2 and close > op:
            score += 1
    
    return score


def resonance_check(position_score, breakout_score):
    """
    双重共振判断
    返回: (strategy, level) 或 (None, None)
    - R: 强共振 (底仓≥3, 起爆≥4)
    - S: 弱共振 (底仓≥2, 起爆≥3)
    - T: 预共振 (底仓≥2, 起爆≥2)
    """
    if position_score >= 3 and breakout_score >= 4:
        return 'R', '强共振'
    elif position_score >= 2 and breakout_score >= 3:
        return 'S', '弱共振'
    elif position_score >= 2 and breakout_score >= 2:
        return 'T', '预共振'
    return None, None