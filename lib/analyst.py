#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v6.13.6 AI 策略分析师模块
将单纯的数据筛选升级为 AI 智能分析，让大模型充当专属策略分析师。
对最终候选池进行多维度深度分析，输出结构化研判报告。

v6.13.0 优化:
- 市场全景: 新增外围市场联动/市场宽度/成交量分析
- 板块深度: 新增板块轮动/龙头识别/板块评分
- 个股深度: 新增60日区间/BOLL带/KDJ超买超卖/量价背离/板块关联
"""


def generate_market_overview(final_candidates, index_data, market_condition,
                              sector_limit_up, total_raw, ae, asig, astr,
                              amicro, aind, fc):
    """
    市场全景分析
    基于大盘环境、指数走势、市场宽度、成交量、涨停分布，输出宏观研判。
    v6.13.0: 新增外围市场联动/市场宽度/成交量分析
    """
    lines = []
    lines.append("## 一、市场全景分析")
    lines.append("")

    # 1. 大盘环境
    env_label = {"强市": "强势", "震荡": "震荡", "弱市": "弱势"}
    env = env_label.get(market_condition, "震荡")
    lines.append(f"### 1.1 大盘环境：{env}格局")
    lines.append("")

    sse = index_data.get('sh', {})
    szse = index_data.get('sz', {})
    if sse:
        sse_chg = sse.get('change_pct', 0)
        sse_amt = sse.get('amount', 0) or 0
        lines.append(f"- **上证指数**：{sse.get('price', '?')}（{sse_chg:+.2f}%），涨跌 {sse.get('change_amount', 0):+.2f}点")
        if sse_amt > 0: lines.append(f"  成交额：{sse_amt/1e8:.0f}亿")
    if szse:
        sz_chg = szse.get('change_pct', 0)
        sz_amt = szse.get('amount', 0) or 0
        lines.append(f"- **深证成指**：{szse.get('price', '?')}（{sz_chg:+.2f}%），涨跌 {szse.get('change_amount', 0):+.2f}点")
        if sz_amt > 0: lines.append(f"  成交额：{sz_amt/1e8:.0f}亿")

    # v6.13.0: 成交量分析
    total_amt = (sse.get('amount', 0) or 0) + (szse.get('amount', 0) or 0)
    if total_amt > 0:
        lines.append("")
        lines.append(f"**两市合计成交**：{total_amt/1e8:.0f}亿")
        if total_amt > 1.5e12:
            lines.append(f"- 成交活跃，放量{env}，资金参与度高，短线机会丰富")
        elif total_amt > 1e12:
            lines.append(f"- 成交量正常，市场交投活跃度适中")
        elif total_amt > 6e11:
            lines.append(f"- 成交量偏低，市场观望情绪浓厚，需精选标的")
        else:
            lines.append(f"- 成交量萎缩，市场交投清淡，控制仓位")

    # 2. 市场情绪
    lines.append("")
    lines.append("### 1.2 市场情绪与宽度")
    lines.append("")

    total_limit_up = sum(sector_limit_up.values()) if sector_limit_up else 0
    if total_limit_up >= 80:
        mood = "极度亢奋"; mood_icon = "🔥🔥🔥"
        mood_desc = "涨停家数>80家，市场情绪极度亢奋，注意追高风险"
    elif total_limit_up >= 50:
        mood = "偏暖"; mood_icon = "🔥🔥"
        mood_desc = "涨停家数>50家，赚钱效应较好，可适度参与短线机会"
    elif total_limit_up >= 30:
        mood = "中性"; mood_icon = "🔥"
        mood_desc = "涨停家数30-50家，市场情绪中性，精选标的"
    else:
        mood = "偏冷"; mood_icon = "❄️"
        mood_desc = "涨停家数<30家，市场情绪偏冷，防御为主"

    lines.append(f"- **市场情绪**：{mood_icon} {mood}（涨停{total_limit_up}家）")
    lines.append(f"- **研判**：{mood_desc}")

    # v6.13.0: 市场宽度（策略分布反映市场风格）
    strat_count = {}
    for c in final_candidates:
        s = c.get('strategy', '?')
        strat_count[s] = strat_count.get(s, 0) + 1
    trend_strats = sum(strat_count.get(k, 0) for k in ['A', 'C', 'G', 'I', 'N', 'R'])
    rebound_strats = sum(strat_count.get(k, 0) for k in ['B', 'D', 'H', 'L', 'P'])
    ambush_strats = sum(strat_count.get(k, 0) for k in ['E', 'F', 'J', 'K', 'M', 'O', 'Q', 'S', 'T'])
    total_strats = trend_strats + rebound_strats + ambush_strats
    if total_strats > 0:
        trend_pct = trend_strats / total_strats * 100
        if trend_pct >= 50:
            style = "追涨型（动量/突破策略为主），市场风格偏向强者恒强"
        elif rebound_strats / total_strats * 100 >= 40:
            style = "低吸型（超跌/回调策略为主），市场风格偏向价值修复"
        else:
            style = "均衡型，追涨与低吸机会并存"
        lines.append(f"- **市场风格**：{style}")

    # 3. 筛选漏斗全景
    lines.append("")
    lines.append("### 1.3 筛选漏斗全景")
    lines.append("")
    lines.append(f"| 阶段 | 数量 | 过滤率 | 说明 |")
    lines.append(f"|------|------|--------|------|")
    lines.append(f"| 原始池 | {total_raw} | - | 全市场满足基本条件的标的 |")
    lines.append(f"| 硬排除 | {ae} | {(1-ae/max(total_raw,1))*100:.1f}% | 13项硬性排除 |")
    lines.append(f"| 信号过滤 | {asig} | {(1-asig/max(ae,1))*100:.1f}% | 27项信号过滤 |")
    lines.append(f"| 策略匹配 | {astr} | {(1-astr/max(asig,1))*100:.1f}% | 20策略匹配 |")
    lines.append(f"| 微观结构 | {amicro} | {(1-amicro/max(astr,1))*100:.1f}% | 流动性+消息敏感度 |")
    lines.append(f"| 行业限制 | {aind} | {(1-aind/max(amicro,1))*100:.1f}% | 行业集中度控制 |")
    lines.append(f"| **最终推荐** | **{fc}** | **{(1-fc/max(total_raw,1))*100:.1f}%** | 精选标的 |")

    # 4. 资金流向
    lines.append("")
    lines.append("### 1.4 资金流向")
    lines.append("")
    total_main_in = sum(c.get('main_inflow') or 0 for c in final_candidates)
    avg_main_in = total_main_in / fc if fc > 0 else 0
    lines.append(f"- 推荐标的主力净流入合计：{total_main_in/1e4:.0f}万")
    if avg_main_in > 1_000_000:
        lines.append(f"- 平均主力净流入：{avg_main_in/1e4:.0f}万/只 → **主力资金积极做多，资金面有支撑**")
    elif avg_main_in > 0:
        lines.append(f"- 平均主力净流入：{avg_main_in/1e4:.0f}万/只 → 主力资金小幅流入，需结合技术面判断")
    elif total_main_in == 0:
        lines.append(f"- 主力资金数据不可得（API通道受限），建议结合盘口观察")
    else:
        lines.append(f"- 平均主力净流出：{abs(avg_main_in)/1e4:.0f}万/只 → **主力资金偏空，注意风险控制**")

    # v6.13.0: 策略分布概览
    lines.append("")
    lines.append("### 1.5 策略分布概览")
    lines.append("")
    sn = {'A': '动量延续', 'B': '超跌反弹', 'C': '事件驱动', 'D': '回调企稳', 'E': '资金埋伏',
          'F': '北向资金', 'G': '横盘突破', 'H': '地量见底', 'I': '均线突破', 'J': '龙回头',
          'K': '缺口回补', 'L': '黄金坑', 'M': '涨停回调', 'N': '新高突破', 'O': '回踩均线',
          'P': '地量反弹', 'Q': 'W底突破', 'R': '主力共振(强)', 'S': '主力共振(弱)', 'T': '主力观察'}
    sorted_s = sorted(strat_count.items(), key=lambda x: -x[1])
    parts = []
    for s, cnt in sorted_s:
        sname = sn.get(s, s)
        parts.append(f"{s} {sname}({cnt}只)")
    lines.append(f"策略分布：{' | '.join(parts)}")
    lines.append(f"趋势追涨型：{trend_strats}只 | 超跌低吸型：{rebound_strats}只 | 埋伏观察型：{ambush_strats}只")

    return "\n".join(lines)


def generate_sector_analysis(final_candidates, sector_limit_up, fc):
    """
    板块深度研判
    基于行业涨停分布、推荐标的行业分布、板块轮动/龙头/评分，输出板块研判。
    v6.13.0: 新增板块轮动分析/龙头识别/板块持续性评分
    """
    lines = []
    lines.append("## 二、板块深度研判")
    lines.append("")

    # 1. 涨停板块分布
    lines.append("### 2.1 涨停板块热度")
    lines.append("")
    if sector_limit_up:
        sorted_sectors = sorted(sector_limit_up.items(), key=lambda x: -x[1])
        lines.append("| 排名 | 板块 | 涨停数 | 热度 | 研判 |")
        lines.append("|------|------|--------|------|------|")
        for i, (sec, cnt) in enumerate(sorted_sectors[:10], 1):
            if cnt >= 15:
                hot = "🔥🔥🔥"; jp = "主线板块，资金高度聚焦"
            elif cnt >= 8:
                hot = "🔥🔥"; jp = "热点板块，短线机会丰富"
            elif cnt >= 4:
                hot = "🔥"; jp = "活跃板块，可择优参与"
            else:
                hot = "⚡"; jp = "零星活跃，个股行情为主"
            lines.append(f"| {i} | {sec} | {cnt} | {hot} | {jp} |")
    else:
        lines.append("（涨停数据不可用）")

    # 2. 推荐标的行业分布
    lines.append("")
    lines.append("### 2.2 推荐标的行业分布")
    lines.append("")
    sector_cnt = {}
    for c in final_candidates:
        ind = c.get('industry', '未知')
        sector_cnt[ind] = sector_cnt.get(ind, 0) + 1
    sorted_rc = sorted(sector_cnt.items(), key=lambda x: -x[1])
    if sorted_rc:
        lines.append("| 行业 | 推荐数 | 占比 | 判断 |")
        lines.append("|------|--------|------|------|")
        for ind, cnt in sorted_rc[:10]:
            pct = cnt / fc * 100
            if pct >= 15: jd = "⚠️ 集中度过高"
            elif pct >= 8: jd = "重点关注"
            else: jd = "分散配置"
            lines.append(f"| {ind} | {cnt} | {pct:.1f}% | {jd} |")

    # v6.13.0: 板块轮动分析
    lines.append("")
    lines.append("### 2.3 板块轮动分析")
    lines.append("")
    hot_sectors = {sec for sec, cnt in sorted_sectors[:5]} if sector_limit_up else set()
    rec_sectors = {ind for ind, _ in sorted_rc}
    overlap = hot_sectors & rec_sectors
    if overlap:
        lines.append(f"- **涨停与推荐重合板块**：{'、'.join(sorted(overlap))}（{len(overlap)}个）")
        lines.append(f"- 推荐标的与涨停热点高度重合，板块联动性强，短线操作参考板块龙头走势")
    else:
        lines.append(f"- 推荐标的与涨停热点无明显重合，可能偏好冷门板块或独立行情个股")
    # 板块持续性评分
    if sector_limit_up:
        total_up = sum(sector_limit_up.values())
        top3 = sum(c for _, c in sorted_sectors[:3])
        top3_ratio = top3 / max(total_up, 1)
        if top3_ratio > 0.5:
            lines.append(f"- **板块集中度**：TOP3板块涨停占比{top3_ratio*100:.0f}%，资金高度集中，龙头效应强")
        elif top3_ratio > 0.3:
            lines.append(f"- **板块集中度**：TOP3板块涨停占比{top3_ratio*100:.0f}%，热点分散但主线清晰")
        else:
            lines.append(f"- **板块集中度**：TOP3板块涨停占比{top3_ratio*100:.0f}%，热点分散，轮动快")

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
    # v6.12.5: 检测kline数据是否有效
    has_kline = bool(kd and kd.get('closes') and len(kd.get('closes', [])) >= 20)
    closes = kd.get('closes', []) if has_kline else []
    highs = kd.get('highs', []) if has_kline else []
    lows = kd.get('lows', []) if has_kline else []
    volumes = kd.get('volumes', []) if has_kline else []
    ma5 = kd.get('ma5', 0) if has_kline else 0
    ma10 = kd.get('ma10', 0) if has_kline else 0
    ma20 = kd.get('ma20', 0) if has_kline else 0
    dif = kd.get('dif', 0) if has_kline else 0
    dea = kd.get('dea', 0) if has_kline else 0
    macd_hist = kd.get('macd_hist', 0) if has_kline else 0
    k_val = kd.get('k', 0) if has_kline else 0
    d_val = kd.get('d', 0) if has_kline else 0
    j_val = kd.get('j', 0) if has_kline else 0
    high20 = kd.get('high20', 0) if has_kline else 0
    high60 = kd.get('high60', 0) if has_kline else 0
    low60 = kd.get('low60', 0) if has_kline else 0
    boll_upper = kd.get('boll_upper', 0) if has_kline else 0
    boll_mid = kd.get('boll_mid', 0) if has_kline else 0
    boll_lower = kd.get('boll_lower', 0) if has_kline else 0
    boll_width = kd.get('boll_width', 0) if has_kline else 0
    rsi14 = kd.get('rsi14', 0) if has_kline else 0

    strategy_names = {
        'A': '动量延续', 'B': '超跌反弹', 'C': '事件驱动', 'D': '回调企稳',
        'E': '资金埋伏', 'F': '北向资金', 'G': '横盘突破', 'H': '地量见底',
        'I': '均线突破', 'J': '龙回头', 'K': '缺口回补', 'L': '黄金坑',
        'M': '涨停回调', 'N': '新高突破', 'O': '回踩均线', 'P': '地量反弹',
        'Q': 'W底突破', 'R': '主力共振(强)', 'S': '主力共振(弱)', 'T': '主力观察'
    }
    sname = strategy_names.get(strat, '')

    # ========== 策略逻辑 ==========
    strategy_logic = _build_strategy_logic(strat, sname, change_pct, ampl, vr, turnover, industry, r7d, r7s, close, high60, low60, has_kline)

    # ========== 技术面分析 ==========
    technical = _build_technical_analysis(code, close, ma5, ma10, ma20, dif, dea, macd_hist,
                                           k_val, d_val, j_val, high20, high60, low60, change_pct, ampl, vr,
                                           closes, highs, lows, volumes, boll_upper, boll_mid, boll_lower,
                                           boll_width, rsi14, has_kline)

    # ========== 资金面分析 ==========
    capital = _build_capital_analysis(main_in, amount, turnover, vr, amihud, liq_score)

    # ========== 基本面速览 ==========
    fundamental = _build_fundamental_analysis(roe, np_yoy, pledge, goodwill, industry, business)

    # ========== 风险提示 ==========
    risk = _build_risk_analysis(code, pledge, goodwill, sigs, change_pct, ampl, close, closes)

    # ========== 操作建议 ==========
    suggestion = _build_suggestion(strat, entry, stop, target, plr, score, conf, news_sens)

    # ========== 综合研判 ==========
    summary = _build_summary(strat, sname, score, conf, plr, change_pct, industry, main_in, r7d, close, high60, low60, has_kline)

    return {
        'strategy_logic': strategy_logic,
        'technical': technical,
        'capital': capital,
        'fundamental': fundamental,
        'risk': risk,
        'suggestion': suggestion,
        'summary': summary,
    }


def _build_strategy_logic(strat, sname, change_pct, ampl, vr, turnover, industry, r7d, r7s, close, high60, low60, has_kline):
    """构建策略逻辑分析 — v6.13.0: 新增60日区间位置分析"""
    parts = []
    parts.append(f"**{strat} {sname}** 策略逻辑：")

    # v6.13.0: 60日区间位置
    if has_kline and high60 > 0 and low60 > 0 and close > 0:
        pos_60 = (close - low60) / (high60 - low60) * 100 if high60 > low60 else 50
        h60_pct = (high60 - close) / close * 100
        l60_pct = (close - low60) / low60 * 100
        if pos_60 >= 80:
            pos_label = f"60日高位区({pos_60:.0f}%)，距高{h60_pct:.1f}%"
        elif pos_60 >= 50:
            pos_label = f"60日中位区({pos_60:.0f}%)，距高{h60_pct:.1f}%距低{l60_pct:.1f}%"
        elif pos_60 >= 20:
            pos_label = f"60日低位区({pos_60:.0f}%)，距低{l60_pct:.1f}%"
        else:
            pos_label = f"60日底部区({pos_60:.0f}%)，距低{l60_pct:.1f}%"
        parts.append(pos_label)

    if strat == 'A':
        parts.append(f"动量延续策略：涨幅{change_pct:+.2f}%，量比{vr:.1f}，换手{turnover:.1f}%，连续强势上涨，市场共识正在形成，短线惯性上冲概率大。")
        if vr > 1.5: parts.append(f"量比{vr:.1f}>1.5，放量上攻确认方向，关注开盘竞价强度。")
    elif strat == 'B':
        parts.append(f"超跌反弹策略：跌幅{change_pct:+.2f}%，振幅{ampl:.1f}%，恐慌性抛售后的技术性修复需求强烈。")
        if has_kline and low60 > 0 and close > 0:
            parts.append(f"低位企稳后反弹概率较高，目标看至前密集成交区。")
    elif strat == 'D':
        parts.append(f"回调企稳策略：涨幅{change_pct:+.2f}%，振幅{ampl:.1f}%，回调幅度适中，量能配合良好，低吸买点。")
    elif strat == 'E':
        parts.append(f"资金埋伏策略：主力底部建仓痕迹明显，当前处于主力成本区附近，安全边际较高。")
    elif strat == 'F':
        parts.append(f"北向资金策略：外资持续增持，看好中长期价值，短期涨幅有限提供较好进场窗口。")
    elif strat == 'G':
        parts.append(f"横盘突破策略：突破横盘整理区间，量比{vr:.1f}确认放量，横盘筹码充分换手后上行空间打开。")
    elif strat == 'H':
        parts.append(f"地量见底策略：经过充分调整后成交量极度萎缩，空方力量已耗尽，反弹一触即发。")
    elif strat == 'I':
        parts.append(f"均线突破策略：均线系统多头排列，突破关键均线压制，趋势确认。")
    elif strat == 'J':
        parts.append(f"龙回头策略：前期强势股回调到位，龙头股性活跃，二次启动概率高。")
    elif strat == 'L':
        parts.append(f"黄金坑策略：短期急跌后企稳，形成黄金坑形态，反弹空间可观。")
    elif strat == 'R':
        parts.append(f"主力共振(强)：主力底仓+短线起爆双重共振，系统最强信号，属于高胜率机会。")
    elif strat == 'S':
        parts.append(f"主力共振(弱)：主力底仓+短线起爆弱共振，信号强度仅次于R，值得关注。")
    elif strat == 'T':
        parts.append(f"主力观察：主力底仓+短线起爆预共振，信号较弱，建议加入观察池。")
    else:
        parts.append(f"符合{sname}形态特征，技术形态完整，量价配合良好。")

    # 7日持续推荐
    if r7d >= 3:
        parts.append(f"该股7日内已被推荐{r7d}天，系统持续看好，趋势延续性强。")
    elif r7d >= 1:
        parts.append(f"该股7日内已被推荐{r7d}天，属于持续跟踪标的。")

    return " ".join(parts)


def _build_technical_analysis(code, close, ma5, ma10, ma20, dif, dea, macd_hist,
                               k_val, d_val, j_val, high20, high60, low60, change_pct, ampl, vr,
                               closes, highs, lows, volumes, boll_upper, boll_mid, boll_lower,
                               boll_width, rsi14, has_kline=True):
    """构建技术面分析 — v6.13.0: 新增60日区间/BOLL带/KDJ超买超卖/量价背离"""
    lines = []
    lines.append("**技术面**：")

    if not has_kline:
        lines.append(f"- 技术指标数据不可得，建议参考基本面筛选结果")
        if close > 0: lines.append(f"- 收盘价：{close:.2f}，涨跌幅：{change_pct:+.2f}%")
        if vr > 0 and change_pct > 0: lines.append(f"- 量价配合：放量上涨（量比{vr:.1f}），量价关系健康")
        return "\n".join(lines)

    # v6.13.0: 60日区间位置
    if high60 > 0 and low60 > 0 and close > 0:
        pos_60 = (close - low60) / (high60 - low60) * 100 if high60 > low60 else 50
        h60_pct = (high60 - close) / close * 100
        l60_pct = (close - low60) / low60 * 100
        if pos_60 >= 80:
            lines.append(f"- 60日区间：高位区({pos_60:.0f}%)，距高{h60_pct:.1f}%，⚠️ 接近前高注意压力")
        elif pos_60 >= 50:
            lines.append(f"- 60日区间：中位区({pos_60:.0f}%)，距高{h60_pct:.1f}%距低{l60_pct:.1f}%")
        elif pos_60 >= 20:
            lines.append(f"- 60日区间：低位区({pos_60:.0f}%)，距低{l60_pct:.1f}%，支撑较强")
        else:
            lines.append(f"- 60日区间：底部区({pos_60:.0f}%)，距低{l60_pct:.1f}%，安全边际高")

    # 均线系统
    ma_status = []
    if ma5 > 0 and close > ma5: ma_status.append("站上MA5")
    elif ma5 > 0: ma_status.append("跌破MA5")
    if ma10 > 0 and close > ma10: ma_status.append("站上MA10")
    if ma10 > 0 and ma5 > ma10 > 0: ma_status.append("MA5↑MA10")
    if ma20 > 0 and close > ma20: ma_status.append("站上MA20")
    if ma_status:
        lines.append(f"- 均线：{'，'.join(ma_status)}")
    else:
        lines.append(f"- 均线：数据不足")

    # MACD
    if dif > dea:
        if dif > 0:
            lines.append(f"- MACD：零轴上多头（DIF={dif:.3f}）→ 动能充足")
        else:
            lines.append(f"- MACD：零轴下金叉（DIF={dif:.3f}）→ 底部反弹信号")
    else:
        lines.append(f"- MACD：空头排列（DIF={dif:.3f}），等待金叉确认")

    # v6.13.0: KDJ + 超买超卖
    if k_val > 0 and d_val > 0:
        if k_val > 80:
            kdj_label = "超买区"
        elif k_val < 20:
            kdj_label = "超卖区"
        elif k_val > d_val:
            kdj_label = "多头发散"
        else:
            kdj_label = "空头收敛"
        lines.append(f"- KDJ：K={k_val:.1f} D={d_val:.1f} J={j_val:.1f} → {kdj_label}")

    # v6.13.0: BOLL带位置
    if boll_upper > 0 and boll_lower > 0 and close > 0:
        if close > boll_upper:
            boll_label = "突破上轨，超强走势"
        elif close > boll_mid:
            boll_pos = (close - boll_mid) / (boll_upper - boll_mid) * 100 if boll_upper > boll_mid else 0
            boll_label = f"上轨区间({boll_pos:.0f}%)，偏强"
        elif close > boll_lower:
            boll_pos = (close - boll_lower) / (boll_mid - boll_lower) * 100 if boll_mid > boll_lower else 0
            boll_label = f"下轨区间({boll_pos:.0f}%)，偏弱"
        else:
            boll_label = "跌破下轨，超卖"
        lines.append(f"- BOLL：上{boll_upper:.2f} 中{boll_mid:.2f} 下{boll_lower:.2f} → {boll_label}")

    # v6.13.0: 量价背离检测
    if closes and volumes and len(closes) >= 5 and len(volumes) >= 5:
        price_change = closes[-1] - closes[-5] if len(closes) >= 5 else 0
        vol_change = sum(volumes[-1:]) - sum(volumes[-5:-1]) if len(volumes) >= 5 else 0
        if price_change > 0 and vol_change < 0 and change_pct > 0:
            lines.append(f"- ⚠️ 量价背离：近5日价涨量缩，上涨动能减弱，注意回调风险")
        elif price_change < 0 and vol_change > 0 and change_pct < 0:
            lines.append(f"- 量价背离：近5日价跌量增，可能是底部放量吸筹信号")

    # 关键价位
    if high20 > 0 and close > 0:
        dist_to_high = (high20 - close) / close * 100
        if dist_to_high < 3:
            lines.append(f"- ⚠️ 距20日高仅{dist_to_high:.1f}%，突破需放量确认")

    # 量价配合
    if vr > 1.5 and change_pct > 0:
        lines.append(f"- 量价：放量上涨（量比{vr:.1f}），量价关系健康")
    elif vr < 0.5 and change_pct > 0:
        lines.append(f"- 量价：缩量上涨（量比{vr:.1f}），上涨动力不足")
    elif vr < 0.5 and change_pct < 0:
        lines.append(f"- 量价：缩量下跌（量比{vr:.1f}），卖压衰竭")

    return "\n".join(lines)


def _build_capital_analysis(main_in, amount, turnover, vr, amihud, liq_score):
    """构建资金面分析"""
    lines = []
    lines.append("**资金面**：")

    if main_in is not None and main_in > 300_000_000:
        lines.append(f"- 主力净流入 {main_in/1e4:.0f}万，大资金积极做多，资金面强势")
    elif main_in is not None and main_in > 100_000_000:
        lines.append(f"- 主力净流入 {main_in/1e4:.0f}万，资金温和流入，有一定支撑")
    elif main_in is not None and main_in > 0:
        lines.append(f"- 主力净流入 {main_in/1e4:.0f}万，资金小幅流入，关注后续变化")
    elif main_in is not None and main_in < 0:
        lines.append(f"- 主力净流出 {abs(main_in)/1e4:.0f}万，资金面偏空，注意风险")
    else:
        lines.append(f"- 主力资金数据不可得，需结合盘口观察")

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


def _build_summary(strat, sname, score, conf, plr, change_pct, industry, main_in, r7d, close, high60, low60, has_kline):
    """构建综合研判 — v6.13.0: 新增60日位置+板块关联"""
    summaries = []

    # 策略摘要
    if strat == 'A':
        summaries.append(f"动量延续策略，涨幅{change_pct:+.2f}%，短线惯性上冲概率大")
    elif strat == 'B':
        summaries.append(f"超跌反弹策略，短期技术性修复需求强烈")
    elif strat == 'D':
        summaries.append(f"回调企稳策略，低吸机会，盈亏比{plr:.2f}")
    elif strat == 'E':
        summaries.append(f"资金埋伏策略，主力底部建仓，安全边际较高")
    elif strat == 'G':
        summaries.append(f"横盘突破策略，放量突破整理区间，上行空间打开")
    elif strat == 'I':
        summaries.append(f"均线突破策略，趋势确认，顺势而为")
    elif strat == 'J':
        summaries.append(f"龙回头策略，前期强势股回调到位，二次启动概率高")
    elif strat == 'L':
        summaries.append(f"黄金坑策略，急跌企稳后反弹空间可观")
    elif strat == 'R':
        summaries.append(f"主力共振(强)，底仓+起爆双重确认，高胜率")
    elif strat == 'S':
        summaries.append(f"主力共振(弱)，信号较强，值得关注")
    elif strat == 'T':
        summaries.append(f"主力观察，加入观察池跟踪")
    else:
        summaries.append(f"{sname}策略，技术形态完整")

    # 评分
    if score >= 18:
        summaries.append(f"评分{score}分{conf}，综合质量优秀")
    elif score >= 12:
        summaries.append(f"评分{score}分{conf}，综合质量良好")
    else:
        summaries.append(f"评分{score}分{conf}，需谨慎参与")

    # v6.13.0: 60日位置
    if has_kline and high60 > 0 and low60 > 0 and close > 0:
        pos_60 = (close - low60) / (high60 - low60) * 100 if high60 > low60 else 50
        if pos_60 >= 70:
            summaries.append(f"60日高位区({pos_60:.0f}%)，注意高位追涨风险")
        elif pos_60 >= 30:
            summaries.append(f"60日中位区({pos_60:.0f}%)，位置适中")
        else:
            summaries.append(f"60日低位区({pos_60:.0f}%)，安全边际较好")

    # 7日持续
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
        final_candidates, sector_limit_up, fc
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