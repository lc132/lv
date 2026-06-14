#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤14-17: 评分相同时的二次评估、评分门控+综合评分、行业集中度限制
"""
from lib.core import *

# ============================================================
# 评分相同时的二次评估（打破平局）
# ============================================================
def tie_break_sort(candidates):
    """评分相同时按优势大小排序：量比→换手率→涨跌幅→板块热度→策略优先级"""
    strategy_order = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4}
    
    def sort_key(rec):
        score = rec.get('score', 0)
        strategy = rec.get('strategy', 'Z')
        strat_rank = strategy_order.get(strategy, 99)
        
        vol_ratio = rec.get('volume_ratio') or 0
        vol_score = min(vol_ratio / 3.0, 1.0) if vol_ratio else 0
        
        turnover = rec.get('turnover') or 0
        if turnover < 2:
            t_score = 0.2
        elif turnover <= 5:
            t_score = 0.6
        elif turnover <= 15:
            t_score = 1.0
        elif turnover <= 25:
            t_score = 0.5
        else:
            t_score = 0.1
        
        change_pct = rec.get('change_pct') or 0
        if strategy in ('A', 'D'):
            c_score = max(0, 1.0 - abs(change_pct - 3) / 7.0)
        elif strategy == 'B':
            c_score = max(0, 1.0 - abs(change_pct + 5) / 5.0)
        else:
            c_score = max(0, 1.0 - abs(change_pct - 2) / 8.0)
        
        sector_rank = rec.get('sector_rank', 99)
        s_score = max(0, 1.0 - sector_rank / 20.0)
        
        tie_score = (vol_score * 0.25 + t_score * 0.25 + c_score * 0.25
                     + s_score * 0.15 + (1.0 - strat_rank / 10.0) * 0.10)
        
        return (-score, strat_rank, -tie_score)
    
    candidates.sort(key=sort_key)
    return candidates


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
            reasons.append("温和企稳+1")
        elif strategy == 'E' and 1 <= change_pct <= 2.5:
            score += 1
            reasons.append("温和推升+1")
        
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
        
        # ROE评分（基本面维度，数据可用时启用）
        roe = c.get('roe', None)
        if roe is not None:
            if roe > 15:
                score += 2
                reasons.append(f"ROE高+2({roe:.1f}%)")
            elif roe >= 5:
                score += 1
                reasons.append(f"ROE中+1({roe:.1f}%)")
            elif roe < 0:
                score -= 1
                reasons.append(f"ROE负-1({roe:.1f}%)")
        
        # 经营现金流（数据可用时启用）
        cash_flow = c.get('cash_flow_positive', None)
        if cash_flow is True:
            score += 1
            reasons.append("现金流正+1")
        
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
    
    # 按评分排序（同分二次评估：量比→换手率→涨跌幅→板块热度→策略优先级）
    candidates = tie_break_sort(candidates)
    
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
    # Sina降级时数据质量差，各策略上限+1以补偿信号失真
    is_sina_fallback = ctx.get('_data_source', '') == 'sina'
    max_per_market = {
        '强市': {'A': 3, 'B': 2, 'C': 2, 'D': 2, 'E': 2},
        '震荡': {'A': 3, 'B': 2, 'C': 2, 'D': 3, 'E': 3},
        '弱市': {'A': 0, 'B': 3, 'C': 1, 'D': 2, 'E': 1},
    }
    if is_sina_fallback:
        max_per_market = {
            '强市': {'A': 4, 'B': 4, 'C': 3, 'D': 4, 'E': 3},
            '震荡': {'A': 5, 'B': 4, 'C': 3, 'D': 5, 'E': 5},
            '弱市': {'A': 0, 'B': 5, 'C': 2, 'D': 3, 'E': 3},
        }
    limits = max_per_market.get(market, {'A': 2, 'B': 2, 'C': 2, 'D': 2, 'E': 2})
    max_total = sum(limits.values())
    
    # 按评分排序（同分二次评估：量比→换手率→涨跌幅→板块热度→策略优先级）
    candidates = tie_break_sort(candidates)
    
    # 评分门槛兜底：Sina降级时评分≥3才保留，避免低分占位挤掉高分
    SCORE_FLOOR = 3 if is_sina_fallback else 0
    
    # 低波动行业额外限制（银行/非银金融/公用事业振幅小，不符合短线策略预期）
    LOW_VOL_INDUSTRIES = {'银行', '非银金融', '公用事业'}
    
    # 按策略分组，每组取前N
    strategy_limited = []
    strategy_count = Counter()
    industry_count = Counter()
    low_vol_count = Counter()
    
    for c in candidates:
        strategy = c.get('strategy', '')
        industry = c.get('industry', '未知')
        score = c.get('score', 0)
        amplitude = c.get('amplitude', 0)
        
        # 评分门槛
        if score < SCORE_FLOOR:
            continue
        
        # 策略上限
        if strategy_count[strategy] >= limits.get(strategy, 2):
            continue
        
        # 行业上限（非"未知"行业）
        if industry != '未知' and industry_count[industry] >= 3:
            continue
        
        # 低波动行业：同行业≤2只 + 利润空间检查（振幅≥2.5%）
        if industry in LOW_VOL_INDUSTRIES:
            if low_vol_count[industry] >= 2:
                continue
            if amplitude < 2.5:
                continue
            low_vol_count[industry] += 1
        
        strategy_count[strategy] += 1
        if industry != '未知':
            industry_count[industry] += 1
        strategy_limited.append(c)
    
    droplo = ' +低波动过滤' if any(low_vol_count.values()) else ''
    print(f"  行业+策略限制: {len(candidates)}→{len(strategy_limited)} 只{'（Sina降级放宽）' if is_sina_fallback else ''}{droplo}")
    print(f"    A≤{limits['A']}:{strategy_count['A']} B≤{limits['B']}:{strategy_count['B']} C≤{limits['C']}:{strategy_count['C']} D≤{limits['D']}:{strategy_count['D']} E≤{limits['E']}:{strategy_count['E']}")
    
    ctx['candidates'] = strategy_limited
    ctx['passed_industry'] = len(strategy_limited)