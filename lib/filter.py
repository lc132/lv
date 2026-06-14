#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤11-12: 硬排除31项 (L1/L2/L3分级)、信号质量过滤14项
"""
from lib.core import *

# ============================================================
# 日志记录 L2/L3 跳过
# ============================================================
def _log_skip(rule_num, rule_name, tier, msg=""):
    """统一记录L2/L3规则跳过"""
    prefix = f"[L{tier}跳过] 规则{rule_num}({rule_name})"
    if msg:
        prefix += f": {msg}"
    log_alert("INFO", "排除分级", prefix)

def _log_l3_flag(code, name, rule_num, rule_name):
    """记录L3降为信号"""
    log_alert("INFO", "排除分级", f"[L3信号] {code} {name}: 规则{rule_num}({rule_name})")

# ============================================================
# 步骤11: 硬排除31项 (L1/L2/L3)
# ============================================================
def step11_hard_exclude(ctx):
    print("\n" + "=" * 60)
    print("步骤11: 硬排除31项 (L1/L2/L3分级)")
    print("=" * 60)
    
    candidates = ctx.get('candidates', [])
    holdings = ctx.get('holdings', [])
    holding_codes = set(h.get('code', '') for h in holdings)
    
    all_history = ctx.get('all_history', [])
    data_dt = datetime.strptime(ctx['data_date'], '%Y-%m-%d')
    window_start = data_dt - timedelta(days=3)
    
    window_recommendations = {}
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
    l2_skipped = 0
    l3_flagged = 0
    l2_skip_log = set()
    l3_skip_log = set()
    
    for c in candidates:
        code = c.get('code', '')
        name = c.get('name', '')
        close = c.get('close', 0)
        change_pct = c.get('change_pct', 0)
        open_p = c.get('open') or 0
        prev_close = c.get('prev_close') or 0
        amount = c.get('amount') or 0
        main_inflow = c.get('main_inflow')
        total_cap = c.get('total_cap') or 0
        skip = False
        reason = ""
        l3_flags = []
        
        # ====================
        # L1: 必执行规则
        # ====================
        
        # 规则1: 科创板(688xxx)
        if code.startswith('688'):
            reason = "科创板(规则1)"
            skip = True
        # 规则2: 北交所(8开头)
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
        # 规则10: 前日涨停但当日开板（前日涨幅>9.5%且当日开盘<前日收盘*0.98）
        elif not skip and open_p > 0 and prev_close > 0:
            # 通过clist无法获取前日数据，使用prev_close近似
            if close / prev_close - 1 > 0.095 and open_p < prev_close * 0.98:
                reason = "前日涨停开板(规则10)"
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
        # 规则20: PE(TTM)>500且非困境反转（total_cap<20亿→可能困境反转）
        elif not skip:
            pe_ttm = c.get('pe_ttm')
            if pe_ttm is not None and pe_ttm > 500 and total_cap >= 20_000_000_000:
                reason = "PE>500非困境反转(规则20)"
                skip = True
        # 规则21: 创业板(300xxx/301xxx)仅强市+动量延续
        elif code.startswith(('300', '301')):
            if ctx.get('market_condition') != '强市':
                reason = "创业板非强市(规则21)"
                skip = True
            # 通过：强市中的创业板，标记仓位减半（SKILL §一规则21: 仓位减半）
            # 标记由步骤17行业限制消费
        # 规则22: 跌停
        elif change_pct < -9.5:
            reason = "跌停(规则22)"
            skip = True
        # 规则28: 近20日跌幅>30%且无基本面改善
        # 近似：当日跌幅>5%且成交额<1亿（暴跌+无资金关注→续跌概率高）
        elif change_pct < -5 and amount < 100_000_000:
            reason = "近20日跌幅>30%(规则28-近似)"
            skip = True
        
        if skip:
            excluded.append((code, reason))
            exclusion_stats[reason] += 1
            continue
        
        # ====================
        # L2: 尽力执行规则（数据不可达→跳过，不排除）
        # ====================
        # 规则6: 退市整理期 → L2跳过
        l2_skip_log.add("规则6: 退市整理期(L2跳过-数据不可达)")
        l2_skipped += 1
        # 规则7: 连续亏损2年+最新季度营收同比降>10% → L2跳过
        l2_skip_log.add("规则7: 连亏2年+营收降(L2跳过-数据不可达)")
        l2_skipped += 1
        # 规则8: 上市<60日 → L2跳过（可通过clist上市日期实现，暂无）
        l2_skip_log.add("规则8: 上市<60日(L2跳过-数据不可达)")
        l2_skipped += 1
        # 规则9: 停牌→复牌<3日 → L2跳过
        l2_skip_log.add("规则9: 停牌复牌<3日(L2跳过-数据不可达)")
        l2_skipped += 1
        # 规则14: 7日内解禁>流通5% → L2跳过
        l2_skip_log.add("规则14: 7日内解禁(L2跳过-数据不可达)")
        l2_skipped += 1
        # 规则15: 3日内分红除权 → L2跳过
        l2_skip_log.add("规则15: 3日内分红除权(L2跳过-数据不可达)")
        l2_skipped += 1
        # 规则16: 可转债强赎/转股>10% → L2跳过
        l2_skip_log.add("规则16: 可转债强赎(L2跳过-数据不可达)")
        l2_skipped += 1
        # 规则17: 30日内研报下调≥2级 → L2跳过
        l2_skip_log.add("规则17: 研报下调(L2跳过-数据不可达)")
        l2_skipped += 1
        # 规则18: 5日内大宗折价>5%且>5000万 → L2跳过
        l2_skip_log.add("规则18: 大宗折价(L2跳过-数据不可达)")
        l2_skipped += 1
        # 规则23: 质押>70%且距平仓线<20% → L2跳过
        l2_skip_log.add("规则23: 高质押平仓风险(L2跳过-数据不可达)")
        l2_skipped += 1
        # 规则24: 30日内业绩修正(预增→预亏) → L2跳过
        l2_skip_log.add("规则24: 业绩修正(L2跳过-数据不可达)")
        l2_skipped += 1
        # 规则25: 30日内立案调查/行政处罚 → L2跳过
        l2_skip_log.add("规则25: 立案调查(L2跳过-数据不可达)")
        l2_skipped += 1
        # 规则27: 龙虎榜机构席位净卖出>3000万 → L2跳过
        l2_skip_log.add("规则27: 龙虎榜机构卖出(L2跳过-数据不可达)")
        l2_skipped += 1
        # 规则29: 大股东减持计划公告<5日 → L2跳过
        l2_skip_log.add("规则29: 大股东减持(L2跳过-数据不可达)")
        l2_skipped += 1
        # 规则30: 商誉占净资产>50%且业绩承诺到期<6个月 → L2跳过
        l2_skip_log.add("规则30: 商誉风险(L2跳过-数据不可达)")
        l2_skipped += 1
        
        # ====================
        # L3: 降为信号规则（满足条件→标注⚠️不排除）
        # ====================
        # 规则19: 融券连续3日增>50% → L3跳过
        l3_skip_log.add("规则19: 融券连增(L3跳过-数据不可达)")
        # 规则26: 当日主力净流出>1亿且占成交额>15%（clist有f62字段）
        if main_inflow is not None and amount > 0:
            try:
                inflow_ratio = abs(main_inflow) / amount * 100
                if main_inflow < 0 and abs(main_inflow) > 100_000_000 and inflow_ratio > 15:
                    l3_flags.append("规则26: 主力净流出>1亿且占>15%")
                    _log_l3_flag(code, name, 26, "主力净流出")
                    l3_flagged += 1
            except (ZeroDivisionError, TypeError):
                pass
        else:
            l3_skip_log.add("规则26: 主力净流出(L3跳过-数据不可达)")
        # 规则31: 行业级政策利空公告<5日 → L3跳过
        l3_skip_log.add("规则31: 行业政策利空(L3跳过-数据不可达)")
        
        if l3_flags:
            c['L3_flags'] = l3_flags
        # 创业板强市通过：标记仓位减半
        if code.startswith(('300', '301')):
            c['_gem_half_position'] = True
        passed.append(c)
    
    for msg in sorted(l2_skip_log):
        log_alert("INFO", "排除分级", f"[L2跳过] {msg}")
    for msg in sorted(l3_skip_log):
        log_alert("INFO", "排除分级", f"[L3跳过] {msg}")
    
    print(f"  硬排除: {len(excluded)} 只 → 通过: {len(passed)} 只")
    print(f"  L2跳过: {l2_skipped}规则次 | L3信号: {l3_flagged}只")
    for reason, count in exclusion_stats.most_common(5):
        print(f"    {reason}: {count}只")
    
    ctx['excluded_count'] = len(excluded)
    ctx['exclusion_stats'] = exclusion_stats
    ctx['candidates'] = passed
    ctx['passed_hard_filter'] = len(passed)
    ctx['_l2_skipped'] = l2_skipped
    ctx['_l3_flagged'] = l3_flagged

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
        open_p = c.get('open') or 0
        close = c.get('close', 0)
        prev_close = c.get('prev_close') or 0
        drop = False
        reason = ""
        deductions = 0
        
        # 规则1: 假动量 - 高开>3%且收<开×0.98
        if open_p > 0 and close > 0 and prev_close > 0:
            gap = open_p / prev_close - 1
            body_ratio = close / open_p if open_p > 0 else 1
            if gap > 0.03 and body_ratio < 0.98:
                reason = "假动量(高开低走)"
                drop = True
        
        # 规则2: 缩量涨停 - 涨幅>5%但量<5日均×0.5（volume_ratio<0.5代理）
        if not drop and change_pct > 5 and volume_ratio > 0 and volume_ratio < 0.5:
            reason = "缩量涨停(规则2)"
            drop = True
        
        # 规则3: 尾盘急拉（通过回撤率近似：收盘>开盘+价格在日内高位）
        if not drop and close > open_p > 0:
            if (close - open_p) / open_p > 0.03 and amplitude > 5:
                # 振幅>5%+涨幅>3%但收盘接近高点→可能的尾盘拉
                if close > 0 and c.get('high', 0) > 0:
                    if (c.get('high', 0) - close) / c.get('high', 0) < 0.005:
                        reason = "尾盘急拉(规则3-近似)"
                        drop = True
        
        # 规则4: 尾盘跳水（通过振幅+收盘接近低点近似）
        if not drop and close > 0 and c.get('low', 0) > 0 and amplitude > 4:
            if (close - c.get('low', 0)) / c.get('low', 0) < 0.005:
                reason = "尾盘跳水(规则4-近似)"
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
        
        # 规则11: 缩量三连阴(减3分)
        if not drop and change_pct < 0:
            if volume_ratio > 0 and volume_ratio < 0.95:
                deductions += 3
                reason = "缩量三连阴(减3分)-近似"
        
        # 规则12: 竞价爆量 → clist无竞价数据，跳过
        
        # 规则13: 连板后首阴(加分)
        if not drop and -3 < change_pct < 0 and turnover > 3:
            # 无法确认前日是否连板，标记为潜在首阴候选
            deductions -= 1  # 这是加分（减负）
            reason = "潜在首阴候选+1"
        
        # 规则8: MACD顶背离 → 需要K线历史，跳过
        # 规则10: 涨停反复开板 → 需要盘中数据，跳过
        
        if drop:
            dropped.append(c)
            signal_stats[reason] += 1
        else:
            if deductions > 0:
                c['_signal_deduction'] = deductions
                c['_signal_note'] = reason
            elif deductions < 0:
                c['_signal_bonus'] = abs(deductions)
                c['_signal_note'] = reason
            filtered.append(c)
    
    print(f"  信号过滤排除: {len(dropped)} 只 → 通过: {len(filtered)} 只")
    if signal_stats:
        for reason, count in signal_stats.most_common(3):
            print(f"    {reason}: {count}只")
    
    ctx['signal_dropped'] = len(dropped)
    ctx['candidates'] = filtered
    ctx['passed_signal_filter'] = len(filtered)