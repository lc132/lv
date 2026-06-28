#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v6.12.3 AI 策略分析师模块
将单纯的数据筛选升级为 AI 智能分析，让大模型充当专属策略分析师。
对最终候选池进行多维度深度分析，输出结构化研判报告。
"""


def generate_market_overview(final_candidates, index_data, market_condition,
                              sector_limit_up, total_raw, ae, asig, astr,
                              amicro, aind, fc):
    """
    市场全景分析
    基于大盘环境、指数走势、资金流向、涨停分布，输出宏观研判。
    """
    lines = []
    lines.append("## 一、市场全景分析")
    lines.append("")

    # 1. 大盘环境
    env_label = {"强市": "强势", "震荡": "震荡", "弱市": "弱势"}
    env = env_label.get(market_condition, "震荡")
    lines.append(f"### 1.1 大盘环境：{env}格局")
    lines.append("")

    # 指数数据
    sse = index_data.get('sh', {})
    szse = index_data.get('sz', {})
    if sse:
        sse_chg = sse.get('change_pct', 0)
        sse_vol = sse.get('volume', 0)
        lines.append(f"- **上证指数**：{sse.get('close', '?')}（{sse_chg:+.2f}%），成交额 {sse_vol/1e8:.0f}亿")
    if szse:
        sz_chg = szse.get('change_pct', 0)
        sz_vol = szse.get('volume', 0)
        lines.append(f"- **深证成指**：{szse.get('close', '?')}（{sz_chg:+.2f}%），成交额 {sz_vol/1e8:.0f}亿")

    # 2. 市场情绪
    lines.append("")
    lines.append("### 1.2 市场情绪")
    lines.append("")

    total_limit_up = sum(sector_limit_up.values()) if sector_limit_up else 0
    if total_limit_up >= 80:
        mood = "极度亢奋"
        mood_desc = "涨停家数>80家，市场情绪极度亢奋，注意追高风险，仓位控制在50%以内"
    elif total_limit_up >= 50:
        mood = "偏暖"
        mood_desc = "涨停家数>50家，赚钱效应较好，可适度参与短线机会"
    elif total_limit_up >= 30:
        mood = "中性"
        mood_desc = "涨停家数30-50家，市场情绪中性，精选标的，控制仓位"
    else:
        mood = "偏冷"
        mood_desc = "涨停家数<30家，市场情绪偏冷，防御为主，降低仓位至30%"

    lines.append(f"- **市场情绪**：{mood}")
    lines.append(f"- **涨停家数**：{total_limit_up}家")
    lines.append(f"- **研判**：{mood_desc}")

    # 3. 筛选漏斗全景
    lines.append("")
    lines.append("### 1.3 筛选漏斗全景")
    lines.append("")
    lines.append(f"| 阶段 | 数量 | 过滤率 | 说明 |")
    lines.append(f"|------|------|--------|------|")
    lines.append(f"| 原始池 | {total_raw} | - | 全市场满足基本条件的标的 |")
    lines.append(f"| 硬排除 | {ae} | {(1-ae/total_raw)*100:.1f}% | 13项硬性排除 |")
    lines.append(f"| 信号过滤 | {asig} | {(1-asig/ae)*100:.1f}% | 27项信号过滤 |")
    lines.append(f"| 策略匹配 | {astr} | {(1-astr/asig)*100:.1f}% | 20策略匹配 |")
    lines.append(f"| 微观结构 | {amicro} | {(1-amicro/astr)*100:.1f}% | 流动性+消息敏感度 |")
    lines.append(f"| 行业限制 | {aind} | {(1-aind/amicro)*100:.1f}% | 行业集中度控制 |")
    lines.append(f"| **最终推荐** | **{fc}** | **{(1-fc/total_raw)*100:.1f}%** | 精选标的 |")

    # 4. 资金流向
    lines.append("")
    lines.append("### 1.4 资金流向")
    lines.append("")
    total_main_in = sum(c.get('main_inflow') or 0 for c in final_candidates)
    avg_main_in = total_main_in / fc if fc > 0 else 0
    lines.append(f"- 推荐标的主力净流入合计：{total_main_in/1e4:.0f}万")
    lines.append(f"- 推荐标的平均主力净流入：{avg_main_in/1e4:.0f}万/只")
    if avg_main_in > 1_000_000:
        lines.append(f"- **研判**：主力资金整体偏多，推荐标的具有资金面支撑")
    elif avg_main_in > 0:
        lines.append(f"- **研判**：主力资金小幅流入，需结合技术面综合判断")
    else:
        lines.append(f"- **研判**：主力资金整体偏空，注意风险控制")

    return "\n".join(lines)


def generate_sector_analysis(final_candidates, sector_limit_up, kline_data):
    """
    板块深度分析
    分析涨停分布、主力资金、龙头识别、板块持续性
    """
    lines = []
    lines.append("## 二、板块深度研判")
    lines.append("")

    if not sector_limit_up:
        lines.append("（无涨停板块数据）")
        return "\n".join(lines)

    # 涨停分布
    sorted_sectors = sorted(sector_limit_up.items(), key=lambda x: -x[1])
    lines.append("### 2.1 涨停分布")
    lines.append("")
    lines.append("| 板块 | 涨停家数 | 强度 |")
    lines.append("|------|---------|------|")
    for sector, count in sorted_sectors[:10]:
        if count >= 5:
            strength = "🔥🔥🔥 极强"
        elif count >= 3:
            strength = "🔥🔥 较强"
        elif count >= 2:
            strength = "🔥 一般"
        else:
            strength = "弱"
        lines.append(f"| {sector} | {count} | {strength} |")

    # 推荐标的板块分布
    lines.append("")
    lines.append("### 2.2 推荐标的板块分布")
    lines.append("")
    sector_rec = {}
    for c in final_candidates:
        ind = c.get('industry', '未知')
        sector_rec[ind] = sector_rec.get(ind, 0) + 1

    sorted_rec = sorted(sector_rec.items(), key=lambda x: -x[1])
    for sector, count in sorted_rec:
        lines.append(f"- **{sector}**：{count}只推荐")

    # 板块持续性研判
    lines.append("")
    lines.append("### 2.3 板块持续性研判")
    lines.append("")
    top_sectors = [s for s, _ in sorted_sectors[:3]]
    if top_sectors:
        lines.append(f"**热点板块**：{'、'.join(top_sectors)}")
        if sorted_sectors[0][1] >= 5:
            lines.append(f"- 龙头板块 **{sorted_sectors[0][0]}** 涨停{ sorted_sectors[0][1]}家，板块效应显著，关注龙头股持续性")
        if sorted_sectors[0][1] >= 3:
            lines.append(f"- 板块轮动健康，热点板块具有持续性，短线可参与")
        else:
            lines.append(f"- 板块效应减弱，热点分散，短线操作难度加大，建议精选个股")

    return "\n".join(lines)


def generate_candidate_analysis(c, kline_data, idx, total):
    """
    个股深度分析
    对单个候选标的进行策略逻辑、技术面、资金面、基本面、风险、操作建议的综合研判。

    返回: dict with keys: strategy_logic, technical, capital, fundamental, risk, suggestion, summary
    """
    code = c.get('code', '')
    name = c.get('name', '')
    strat = c.get('strategy', '?')
    score = c.get('score', 0)
    conf = c.get('confidence', '★')
    change_pct = c.get('change_pct', 0) or 0
    ampl = c.get('amplitude', 0) or 0
    industry = c.get('industry', '未知')
    business = c.get('business', '')
    amount = c.get('amount', 0) or 0
    turnover = c.get('turnover', 0) or 0
    vr = c.get('volume_ratio') or 0
    main_in = c.get('main_inflow') or 0
    close = c.get('close', 0) or 0
    entry = c.get('_entry', 0)
    stop = c.get('_stop', 0)
    target = c.get('_target', 0)
    plr = c.get('_pl_ratio', 0)
    r7d = c.get('_recent_7d', 0)
    r7s = c.get('_recent_7d_strategies', {})
    sigs = c.get('_signal_reasons', [])
    lh = c.get('_longhu', '')
    news = c.get('_news_positive', '')
    ann = c.get('_announcement', '')
    roe = c.get('_fd_roe') or 0
    np_yoy = c.get('_fd_net_profit_yoy') or 0
    pledge = c.get('_fd_pledge_ratio') or 0
    goodwill = c.get('_fd_goodwill_ratio') or 0
    amihud = c.get('_amihud', 0)
    liq_score = c.get('_microstructure_score', 0)
    news_sens = c.get('_news_sensitivity', 0)

    kd = kline_data.get(code, {}) if kline_data else {}
    closes = kd.get('closes', [])
    ma5 = kd.get('ma5', 0)
    ma10 = kd.get('ma10', 0)
    ma20 = kd.get('ma20', 0)
    dif = kd.get('dif', 0)
    dea = kd.get('dea', 0)
    macd_hist = kd.get('macd_hist', 0)
    k_val = kd.get('k', 0)
    d_val = kd.get('d', 0)
    high20 = kd.get('high20', 0)

    strategy_names = {
        'A': '动量延续', 'B': '超跌反弹', 'C': '事件驱动', 'D': '回调企稳',
        'E': '资金埋伏', 'F': '北向资金', 'G': '横盘突破', 'H': '地量见底',
        'I': '均线突破', 'J': '龙回头', 'K': '缺口回补', 'L': '黄金坑',
        'M': '涨停回调', 'N': '新高突破', 'O': '回踩均线', 'P': '地量反弹',
        'Q': 'W底突破', 'R': '主力共振(强)', 'S': '主力共振(弱)', 'T': '主力观察'
    }
    sname = strategy_names.get(strat, '')

    # ========== 策略逻辑 ==========
    strategy_logic = _build_strategy_logic(strat, sname, change_pct, ampl, vr, turnover, industry, r7d, r7s)

    # ========== 技术面分析 ==========
    technical = _build_technical_analysis(code, close, ma5, ma10, ma20, dif, dea, macd_hist,
                                           k_val, d_val, high20, change_pct, ampl, vr, closes)

    # ========== 资金面分析 ==========
    capital = _build_capital_analysis(main_in, amount, turnover, vr, amihud, liq_score)

    # ========== 基本面速览 ==========
    fundamental = _build_fundamental_analysis(roe, np_yoy, pledge, goodwill, industry, business)

    # ========== 风险提示 ==========
    risk = _build_risk_analysis(code, pledge, goodwill, sigs, change_pct, ampl, close, closes)

    # ========== 操作建议 ==========
    suggestion = _build_suggestion(strat, entry, stop, target, plr, score, conf, news_sens)

    # ========== 综合研判 ==========
    summary = _build_summary(strat, sname, score, conf, plr, change_pct, industry, main_in, r7d)

    return {
        'strategy_logic': strategy_logic,
        'technical': technical,
        'capital': capital,
        'fundamental': fundamental,
        'risk': risk,
        'suggestion': suggestion,
        'summary': summary,
    }


def _build_strategy_logic(strat, sname, change_pct, ampl, vr, turnover, industry, r7d, r7s):
    """构建策略逻辑分析"""
    parts = []
    parts.append(f"**{strat} {sname}** 策略匹配逻辑：")

    if strat == 'A':
        parts.append(f"该股涨幅{change_pct:+.2f}%，量比{vr:.1f}，换手率{turnover:.1f}%，呈现典型的动量延续特征。连续强势上涨表明市场共识正在形成，短线惯性上冲概率较大。")
    elif strat == 'B':
        parts.append(f"该股跌幅{change_pct:+.2f}%，振幅{ampl:.1f}%，属于典型的超跌反弹机会。恐慌性抛售后的技术性修复需求强烈，短期反弹概率较大。")
    elif strat == 'D':
        parts.append(f"该股涨幅{change_pct:+.2f}%，振幅{ampl:.1f}%，在回调中企稳。回调幅度适中，量能配合良好，属于低吸策略的理想标的。")
    elif strat == 'E':
        parts.append(f"该股资金连续流入，主力底部建仓痕迹明显。当前涨幅{change_pct:+.2f}%，处于主力成本区附近，安全边际较高。")
    elif strat == 'F':
        parts.append(f"北向资金持续增持，外资看好该股中长期价值。短期涨幅有限，提供了较好的进场窗口。")
    elif strat == 'G':
        parts.append(f"该股突破横盘整理区间，量比{vr:.1f}确认放量，技术形态突破有效。横盘期间筹码充分换手，突破后上行空间打开。")
    elif strat == 'H':
        parts.append(f"地量见底，卖压衰竭。该股经过充分调整后成交量极度萎缩，表明空方力量已耗尽，反弹一触即发。")
    elif strat == 'R':
        parts.append(f"主力底仓+短线起爆双重共振，是系统最强信号。主力资金已完成建仓，短线起爆信号确认，属于高胜率机会。")
    elif strat == 'S':
        parts.append(f"主力底仓+短线起爆弱共振，信号强度仅次于R策略。主力资金已介入，短线起爆条件基本满足，值得关注。")
    elif strat == 'T':
        parts.append(f"主力底仓+短线起爆预共振，信号较弱但具有观察价值。主力资金有建仓迹象，短线起爆条件尚不完全，建议加入观察池。")
    else:
        parts.append(f"该股符合{sname}形态特征，技术形态完整，量价配合良好。")

    if r7d >= 3:
        parts.append(f"该股在7日内已被推荐{r7d}天，系统持续看好，表明趋势延续性强。")
    elif r7d >= 1:
        parts.append(f"该股在7日内已被推荐{r7d}天，属于持续跟踪标的。")

    return " ".join(parts)


def _build_technical_analysis(code, close, ma5, ma10, ma20, dif, dea, macd_hist,
                               k_val, d_val, high20, change_pct, ampl, vr, closes):
    """构建技术面分析"""
    lines = []
    lines.append("**技术面**：")

    # 均线系统
    ma_status = []
    if ma5 > 0 and close > ma5:
        ma_status.append("站上MA5")
    elif ma5 > 0:
        ma_status.append("跌破MA5")
    if ma10 > 0 and close > ma10:
        ma_status.append("站上MA10")
    if ma10 > 0 and ma5 > ma10 > 0:
        ma_status.append("MA5金叉MA10")
    if ma20 > 0 and close > ma20:
        ma_status.append("站上MA20")
    if ma_status:
        lines.append(f"- 均线系统：{'，'.join(ma_status)}")
    else:
        lines.append(f"- 均线系统：数据不足")

    # MACD
    if dif > dea:
        if dif > 0:
            lines.append(f"- MACD：零轴上方多头运行（DIF={dif:.3f}，DEA={dea:.3f}），动能充足")
        else:
            lines.append(f"- MACD：零轴下方金叉（DIF={dif:.3f}，DEA={dea:.3f}），底部反弹信号")
    else:
        lines.append(f"- MACD：空头排列（DIF={dif:.3f}，DEA={dea:.3f}），等待金叉确认")

    # KDJ
    if k_val > 0 and d_val > 0:
        if k_val > d_val:
            lines.append(f"- KDJ：多头排列（K={k_val:.1f}，D={d_val:.1f}），短线偏多")
        else:
            lines.append(f"- KDJ：空头排列（K={k_val:.1f}，D={d_val:.1f}），短线偏空")

    # 关键价位
    if high20 > 0 and close > 0:
        dist_to_high = (high20 - close) / close * 100
        lines.append(f"- 距20日最高价：{dist_to_high:.1f}%")
        if dist_to_high < 3:
            lines.append(f"  ⚠️ 接近前高压力位，突破需放量确认")

    # 量价配合
    if vr > 1.5 and change_pct > 0:
        lines.append(f"- 量价配合：放量上涨（量比{vr:.1f}），量价关系健康")
    elif vr < 0.5 and change_pct > 0:
        lines.append(f"- 量价配合：缩量上涨（量比{vr:.1f}），上涨动力不足")
    elif vr < 0.5 and change_pct < 0:
        lines.append(f"- 量价配合：缩量下跌（量比{vr:.1f}），卖压衰竭")

    return "\n".join(lines)


def _build_capital_analysis(main_in, amount, turnover, vr, amihud, liq_score):
    """构建资金面分析"""
    lines = []
    lines.append("**资金面**：")

    if main_in > 300_000_000:
        lines.append(f"- 主力净流入 {main_in/1e4:.0f}万，大资金积极做多，资金面强势")
    elif main_in > 100_000_000:
        lines.append(f"- 主力净流入 {main_in/1e4:.0f}万，资金温和流入，有一定支撑")
    elif main_in > 0:
        lines.append(f"- 主力净流入 {main_in/1e4:.0f}万，资金小幅流入，关注后续变化")
    elif main_in < 0:
        lines.append(f"- 主力净流出 {abs(main_in)/1e4:.0f}万，资金面偏空，注意风险")
    else:
        lines.append(f"- 主力资金方向不明，需进一步观察")

    lines.append(f"- 成交额 {amount/1e8:.1f}亿，换手率 {turnover:.1f}%，流动性{'良好' if liq_score >= 3 else '一般' if liq_score >= 2 else '偏弱'}")
    if amihud > 0:
        lines.append(f"- Amihud非流动性 {amihud:.3f}（{'冲击成本低' if amihud < 0.5 else '冲击成本适中' if amihud < 1.0 else '冲击成本偏高'}）")

    return "\n".join(lines)


def _build_fundamental_analysis(roe, np_yoy, pledge, goodwill, industry, business):
    """构建基本面速览"""
    lines = []
    lines.append("**基本面速览**：")

    fin_items = []
    try:
        roe_f = float(roe)
        if roe_f > 15:
            fin_items.append(f"ROE {roe_f:.1f}%（优秀）")
        elif roe_f > 5:
            fin_items.append(f"ROE {roe_f:.1f}%（一般）")
        elif roe_f != 0:
            fin_items.append(f"ROE {roe_f:.1f}%（偏低）")
    except (ValueError, TypeError):
        pass

    try:
        np_f = float(np_yoy)
        if np_f > 20:
            fin_items.append(f"净利润同比 +{np_f:.1f}%（高增长）")
        elif np_f > 0:
            fin_items.append(f"净利润同比 +{np_f:.1f}%")
        elif np_f < 0:
            fin_items.append(f"净利润同比 {np_f:.1f}%（下滑）")
    except (ValueError, TypeError):
        pass

    if fin_items:
        lines.append(f"- {'，'.join(fin_items)}")
    else:
        lines.append(f"- 基本面数据暂缺")

    lines.append(f"- 行业：{industry}" + (f" / {business}" if business else ""))

    # 风险指标
    warnings = []
    try:
        if float(pledge) > 30:
            warnings.append(f"质押比例 {float(pledge):.1f}%偏高")
    except (ValueError, TypeError):
        pass
    try:
        if float(goodwill) > 30:
            warnings.append(f"商誉占比 {float(goodwill):.1f}%偏高")
    except (ValueError, TypeError):
        pass

    if warnings:
        lines.append(f"- ⚠️ {'，'.join(warnings)}")

    return "\n".join(lines)


def _build_risk_analysis(code, pledge, goodwill, sigs, change_pct, ampl, close, closes):
    """构建风险提示"""
    lines = []
    lines.append("**风险提示**：")

    risks = []

    # 质押风险
    try:
        if float(pledge) > 30:
            risks.append(f"质押比例 {float(pledge):.1f}%，存在爆仓风险")
    except (ValueError, TypeError):
        pass

    # 商誉风险
    try:
        if float(goodwill) > 30:
            risks.append(f"商誉/净资产 {float(goodwill):.1f}%，存在减值风险")
    except (ValueError, TypeError):
        pass

    # 涨幅过大风险
    if change_pct > 7:
        risks.append(f"当日涨幅 {change_pct:.1f}%，追高风险较大，建议等待回调")

    # 振幅过大风险
    if ampl > 8:
        risks.append(f"振幅 {ampl:.1f}%，波动剧烈，止损需严格执行")

    # 前高压力
    if closes and len(closes) >= 20:
        high20 = max(closes[-20:])
        if close > 0 and (high20 - close) / close < 0.03:
            risks.append("接近前高压力位，突破失败可能导致回调")

    if not risks:
        risks.append("短期风险可控，关注大盘系统性风险")

    for r in risks:
        lines.append(f"- {r}")

    return "\n".join(lines)


def _build_suggestion(strat, entry, stop, target, plr, score, conf, news_sens):
    """构建操作建议"""
    lines = []
    lines.append("**操作建议**：")

    if entry > 0 and stop > 0 and target > 0:
        lines.append(f"- 进场区间：{entry:.2f}元")
        lines.append(f"- 止损位：{stop:.2f}元（{abs(1-stop/entry)*100:.1f}%）")
        lines.append(f"- 止盈位：{target:.2f}元（+{(target/entry-1)*100:.1f}%）")
        lines.append(f"- 盈亏比：{plr:.2f}（{'优秀' if plr >= 2.5 else '良好' if plr >= 1.5 else '一般'}）")

    # 持仓周期建议
    if strat in ('A', 'C', 'N', 'R'):
        lines.append(f"- 持仓周期：1-3天（短线追涨，快进快出）")
    elif strat in ('B', 'H', 'L', 'P'):
        lines.append(f"- 持仓周期：2-5天（超跌反弹，等待修复）")
    elif strat in ('D', 'E', 'F', 'O', 'M'):
        lines.append(f"- 持仓周期：3-5天（低吸埋伏，耐心持有）")
    else:
        lines.append(f"- 持仓周期：2-5天（短线操作）")

    # 消息敏感度
    if news_sens >= 2:
        lines.append(f"- 消息敏感度：高（利好公告可加速上涨，利空需及时止损）")
    elif news_sens >= 1:
        lines.append(f"- 消息敏感度：中（消息面有一定影响，关注公告）")

    return "\n".join(lines)


def _build_summary(strat, sname, score, conf, plr, change_pct, industry, main_in, r7d):
    """构建综合研判——1-2句话总结"""
    summaries = []

    if strat == 'A':
        summaries.append(f"该股是动量延续策略标的，涨幅{change_pct:+.2f}%，短线惯性上冲概率较大")
    elif strat == 'B':
        summaries.append(f"该股是超跌反弹策略标的，短期技术性修复需求强烈")
    elif strat == 'D':
        summaries.append(f"该股是回调企稳策略标的，低吸机会，盈亏比{plr:.2f}")
    elif strat == 'E':
        summaries.append(f"该股是资金埋伏策略标的，主力底部建仓，安全边际较高")
    elif strat == 'R':
        summaries.append(f"该股是主力共振(强)策略标的，底仓+起爆双重确认，胜率较高")
    elif strat == 'S':
        summaries.append(f"该股是主力共振(弱)策略标的，信号较强，值得关注")
    else:
        summaries.append(f"该股是{sname}策略标的，技术形态完整")

    if score >= 18:
        summaries.append(f"评分{score}分，置信度{conf}，综合质量优秀")
    elif score >= 12:
        summaries.append(f"评分{score}分，置信度{conf}，综合质量良好")
    else:
        summaries.append(f"评分{score}分，置信度{conf}，需谨慎参与")

    if r7d >= 3:
        summaries.append(f"7日内持续推荐，趋势延续性强")

    return "，".join(summaries) + "。"


def generate_ai_report(final_candidates, kline_data, index_data, market_condition,
                        sector_limit_up, total_raw, ae, asig, astr, amicro, aind, fc):
    """
    生成完整的 AI 分析报告
    返回: dict with keys: market_overview, sector_analysis, candidate_analyses
    """
    report = {}

    # 市场全景
    report['market_overview'] = generate_market_overview(
        final_candidates, index_data, market_condition,
        sector_limit_up, total_raw, ae, asig, astr, amicro, aind, fc
    )

    # 板块深度
    report['sector_analysis'] = generate_sector_analysis(
        final_candidates, sector_limit_up, kline_data
    )

    # 个股深度分析（TOP10）
    top10 = sorted(final_candidates, key=lambda c: -c.get('_pl_ratio', 0))[:10]
    report['candidate_analyses'] = []
    for i, c in enumerate(top10):
        analysis = generate_candidate_analysis(c, kline_data, i + 1, len(top10))
        analysis['code'] = c.get('code', '')
        analysis['name'] = c.get('name', '')
        analysis['strategy'] = c.get('strategy', '?')
        report['candidate_analyses'].append(analysis)

    return report