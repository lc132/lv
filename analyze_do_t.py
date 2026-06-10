#!/usr/bin/env python3
"""
做T可行性分析脚本 v1.0
================================
功能：对持仓标的进行T+0操作可行性评估
输入：持仓数据(dict) + 行情数据(dict)
输出：做T分析报告(可嵌入持仓分析报告)

评估维度（5维100分制）：
  1. 日内振幅 (25分) — T+0利润空间
  2. 流动性 (20分)  — 进出便利性
  3. 趋势适配 (20分) — 方向选择
  4. 盈亏状态 (15分) — 做T动力
  5. 消息风险 (20分) — 突发事件风险

触发：用户上传持仓截图后自动调用
"""

import json
import math
from datetime import datetime

# ============================================================
# 做T分析模型
# ============================================================

def score_amplitude(amp_pct):
    """日内振幅评分 (0-25)"""
    if amp_pct is None or amp_pct <= 0:
        return 0, "数据缺失"
    if amp_pct >= 8:
        return 25, f"极高振幅({amp_pct:.1f}%)，利润空间充裕 ⭐"
    elif amp_pct >= 5:
        return 22, f"高振幅({amp_pct:.1f}%)，做T利润空间好"
    elif amp_pct >= 3:
        return 16, f"中等振幅({amp_pct:.1f}%)，可操作"
    elif amp_pct >= 2:
        return 10, f"偏低振幅({amp_pct:.1f}%)，利润空间有限"
    elif amp_pct >= 1:
        return 5, f"低振幅({amp_pct:.1f}%)，不建议做T"
    else:
        return 2, f"极低振幅({amp_pct:.1f}%)，无操作价值"


def score_liquidity(turnover, volume_yi):
    """流动性评分 (0-20)"""
    score = 0
    reasons = []
    if turnover is not None:
        if turnover >= 8:
            score += 10
            reasons.append(f"高换手率({turnover:.1f}%)")
        elif turnover >= 4:
            score += 7
            reasons.append(f"中等换手率({turnover:.1f}%)")
        elif turnover >= 1.5:
            score += 4
            reasons.append(f"换手率偏低({turnover:.1f}%)")
        else:
            reasons.append(f"换手率低({turnover:.1f}%)，流动性不足")
    if volume_yi is not None:
        if volume_yi >= 5:
            score += 10
            reasons.append(f"成交活跃({volume_yi:.1f}亿)")
        elif volume_yi >= 2:
            score += 6
            reasons.append(f"成交适中({volume_yi:.1f}亿)")
        elif volume_yi >= 0.5:
            score += 3
            reasons.append(f"成交偏低({volume_yi:.1f}亿)")
        else:
            reasons.append(f"成交清淡({volume_yi:.1f}亿)")
    if not reasons:
        reasons.append("流动性数据缺失")
    return min(score, 20), " | ".join(reasons)


def score_trend(trend_desc, consecutive_direction):
    """趋势适配评分 (0-20)"""
    # 最适合做T的是震荡行情
    mapping = {
        "宽幅震荡": (20, "宽幅震荡 → 最适合做T，双向可操作"),
        "震荡偏多": (18, "震荡偏多 → 优先正T，高位可倒T"),
        "震荡偏空": (16, "震荡偏空 → 优先倒T，低位可正T"),
        "横盘整理": (17, "横盘整理 → 震荡区间清晰，适合做T"),
        "缓慢上升": (13, "缓慢上升 → 正T为主，注意追高风险"),
        "缓慢下降": (11, "缓慢下降 → 倒T为主，注意踏空风险"),
        "强势上攻": (8, "强势上攻 → 不建议做T，持筹更优"),
        "弱势下跌": (6, "弱势下跌 → 做T风险大，建议观望"),
        "主升浪": (6, "主升浪中 → 做T容易卖飞，不建议"),
        "急跌": (5, "急跌中 → 做T风险极高，等待企稳"),
        "未知": (12, "趋势不明 → 中性评估"),
    }
    score, desc = mapping.get(trend_desc, (12, f"{trend_desc} → 中性评估"))
    if consecutive_direction and consecutive_direction >= 3:
        score = max(score - 3, 3)
        desc += f"，连续{consecutive_direction}日同向→降分"
    return score, desc


def score_position(pnl_pct, position_pct):
    """盈亏状态评分 (0-15)"""
    score = 0
    details = []
    if pnl_pct is not None:
        if pnl_pct <= -15:
            score += 7
            details.append(f"深套({pnl_pct:+.2f}%)，做T降成本动力极强")
        elif pnl_pct <= -5:
            score += 9
            details.append(f"被套({pnl_pct:+.2f}%)，做T降成本意愿强 ⭐")
        elif pnl_pct < 0:
            score += 7
            details.append(f"浅套({pnl_pct:+.2f}%)，有做T降成本需求")
        elif pnl_pct <= 3:
            score += 5
            details.append(f"微盈({pnl_pct:+.2f}%)，可考虑做T扩大利润")
        else:
            score += 3
            details.append(f"盈利({pnl_pct:+.2f}%)，做T需求不强")
    if position_pct is not None:
        if position_pct >= 40:
            score += 6
            details.append(f"重仓({position_pct:.0f}%)→有分散需求")
        elif position_pct >= 20:
            score += 4
            details.append(f"中等仓位({position_pct:.0f}%)")
        else:
            score += 2
            details.append(f"轻仓({position_pct:.0f}%)")
    return min(score, 15), " | ".join(details)


def score_news(risk_flags):
    """消息风险评分 (0-20) — 扣分制"""
    score = 20
    deductions = []
    if not risk_flags:
        return 20, "无明显风险事件 ✅"
    for flag in risk_flags:
        if "ST" in flag or "退市" in flag:
            score -= 15
            deductions.append(f"⚠️ {flag}(-15)")
        elif "异常波动" in flag or "连续涨停" in flag or "连续跌停" in flag:
            score -= 8
            deductions.append(f"⚡ {flag}(-8)")
        elif "冻结" in flag or "减持" in flag or "亏损" in flag:
            score -= 5
            deductions.append(f"📉 {flag}(-5)")
        elif "重大公告" in flag or "解禁" in flag:
            score -= 3
            deductions.append(f"📋 {flag}(-3)")
    return max(score, 0), " | ".join(deductions)


def determine_direction(trend_desc, pnl_pct, amp_pct):
    """判断做T方向"""
    # 震荡行情 → 双向
    if "震荡" in trend_desc:
        if amp_pct and amp_pct >= 5:
            return "双向", "宽幅震荡，高抛低吸双向操作"
        return "双向", "震荡行情，灵活操作"

    # 深套 + 下降趋势 → 优先倒T
    if pnl_pct is not None and pnl_pct <= -5:
        if "下降" in trend_desc or "弱势" in trend_desc or "急跌" in trend_desc:
            return "倒T(先卖后买)", "被套+弱势→优先减仓降成本"
        if "上升" in trend_desc or "强势" in trend_desc:
            return "正T(先买后卖)", "被套+反弹→低位补仓高位卖出"
        return "倒T为主", "被套状态，优先倒T降成本"

    # 盈亏平衡 → 看趋势
    if "上升" in trend_desc or "强势" in trend_desc or "主升" in trend_desc:
        return "正T(先买后卖)", "上升趋势优先做正T"
    if "下降" in trend_desc or "弱势" in trend_desc:
        return "倒T(先卖后买)", "下降趋势优先做倒T"

    return "双向偏正T", "趋势中性，灵活偏多"


def calc_entry_exit(current_price, cost_price, amp_pct, direction):
    """计算做T参考价位"""
    half_range = (amp_pct or 3) / 2 / 100 * current_price

    if "正T" in direction:
        buy_zone = (current_price * (1 - amp_pct/300), current_price * (1 - amp_pct/500))
        sell_target = current_price * (1 + amp_pct/200)
        stop_loss = current_price * (1 - amp_pct/80)
    elif "倒T" in direction:
        buy_zone = None
        sell_target = current_price * (1 + amp_pct/300)
        buy_back = current_price * (1 - amp_pct/250)
        stop_loss = current_price * (1 + amp_pct/80)
    else:
        buy_zone = (current_price * 0.98, current_price * 0.99)
        sell_target = current_price * 1.02
        stop_loss = current_price * 0.97

    return {
        "buy_zone": buy_zone,
        "sell_target": sell_target,
        "stop_loss": stop_loss,
        "half_range": half_range
    }


def position_sizing(position_pct, pnl_pct):
    """做T仓位建议"""
    if position_pct is None:
        return "不超过总仓位的15%"
    if pnl_pct is not None and pnl_pct <= -10:
        return f"不超过持仓的30%（约{position_pct*0.3:.0f}%总仓位），以降低成本为主"
    if position_pct >= 40:
        return "不超过持仓的20%-25%，分批操作"
    if position_pct >= 20:
        return "不超过持仓的30%-40%"
    return "不超过持仓的50%"


def analyze_do_t(holdings, market_data):
    """
    主分析函数

    holdings: [{"code": "601908", "name": "京能电力", "shares": 1400, "cost": 9.095,
                 "current": 9.13, "pnl_pct": 0.39, "position_pct": 18.1}, ...]
    market_data: {"601908": {"amp_pct": 8.1, "turnover": 5.1, "volume_yi": 29.8,
                               "trend": "震荡偏多", "risk_flags": [], ...}, ...}

    Returns: analysis_results dict
    """
    results = []
    total_score = 0
    count = 0

    for h in holdings:
        code = h["code"]
        md = market_data.get(code, {})

        # 5维评分
        s_amp, r_amp = score_amplitude(md.get("amp_pct"))
        s_liq, r_liq = score_liquidity(md.get("turnover"), md.get("volume_yi"))
        s_trd, r_trd = score_trend(md.get("trend", "未知"), md.get("consecutive_days"))
        s_pos, r_pos = score_position(h.get("pnl_pct"), h.get("position_pct"))
        s_new, r_new = score_news(md.get("risk_flags", []))

        total = s_amp + s_liq + s_trd + s_pos + s_new

        # 方向
        direction, dir_reason = determine_direction(
            md.get("trend", "未知"), h.get("pnl_pct"), md.get("amp_pct"))

        # 价位
        prices = calc_entry_exit(
            h.get("current", h.get("cost")), h.get("cost"),
            md.get("amp_pct", 3), direction)

        # 仓位
        sizing = position_sizing(h.get("position_pct"), h.get("pnl_pct"))

        # 综合评级
        if total >= 75:
            grade = "✅ 强烈推荐"
            grade_emoji = "🟢"
        elif total >= 60:
            grade = "👍 可以操作"
            grade_emoji = "🟡"
        elif total >= 45:
            grade = "⚠️ 谨慎操作"
            grade_emoji = "🟠"
        else:
            grade = "❌ 不建议"
            grade_emoji = "🔴"

        # 操作摘要
        summary_lines = []
        if "正T" in direction:
            summary_lines.append(f"📈 方向：{direction} — {dir_reason}")
            if prices["buy_zone"]:
                summary_lines.append(f"📥 买入区间：¥{prices['buy_zone'][0]:.2f} ~ ¥{prices['buy_zone'][1]:.2f}")
            summary_lines.append(f"📤 卖出目标：¥{prices['sell_target']:.2f}")
        elif "倒T" in direction:
            summary_lines.append(f"📉 方向：{direction} — {dir_reason}")
            summary_lines.append(f"📤 卖出区间：¥{h.get('current', 0)*0.99:.2f} ~ ¥{prices['sell_target']:.2f}")
            if prices.get("buy_back"):
                summary_lines.append(f"📥 买回目标：¥{prices['buy_back']:.2f}")
        else:
            summary_lines.append(f"🔄 方向：{direction}")
            summary_lines.append(f"📥 低吸位：¥{prices['buy_zone'][0]:.2f}")
            summary_lines.append(f"📤 高抛位：¥{prices['sell_target']:.2f}")
        summary_lines.append(f"🛑 止损位：¥{prices['stop_loss']:.2f}")
        summary_lines.append(f"📦 {sizing}")

        result = {
            "code": code,
            "name": h["name"],
            "grade": grade,
            "grade_emoji": grade_emoji,
            "total_score": total,
            "scores": {
                "振幅": (s_amp, r_amp),
                "流动性": (s_liq, r_liq),
                "趋势适配": (s_trd, r_trd),
                "盈亏状态": (s_pos, r_pos),
                "消息风险": (s_new, r_new),
            },
            "direction": direction,
            "dir_reason": dir_reason,
            "prices": prices,
            "sizing": sizing,
            "summary_lines": summary_lines,
            "risk_flags": md.get("risk_flags", []),
        }
        results.append(result)
        total_score += total
        count += 1

    return {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "avg_score": round(total_score / count, 1) if count else 0,
        "count": count,
        "results": results,
    }


def render_report(analysis):
    """渲染为Markdown格式的做T分析报告"""
    lines = []
    lines.append("## 🔄 做T(T+0)可行性分析")
    lines.append("")
    lines.append(f"> 分析时间：{analysis['date']} | 整体做T可行性均分：**{analysis['avg_score']}/100**")
    lines.append("")
    lines.append("---")
    lines.append("")

    for i, r in enumerate(analysis["results"]):
        lines.append(f"### {r['grade_emoji']} {r['name']} ({r['code']}) — {r['grade']}")
        lines.append("")
        lines.append(f"**综合评分：{r['total_score']}/100**")

        # 评分表
        lines.append("")
        lines.append("| 维度 | 得分 | 评价 |")
        lines.append("|------|------|------|")
        for dim, (s, desc) in r["scores"].items():
            bar = "█" * (s // 5) + "░" * (5 - s // 5)
            if dim == "振幅":
                lines.append(f"| {dim} | {s}/25 {bar} | {desc} |")
            elif dim == "盈亏状态":
                lines.append(f"| {dim} | {s}/15 {bar[:3]} | {desc} |")
            else:
                lines.append(f"| {dim} | {s}/20 {bar} | {desc} |")

        lines.append("")

        # 操作建议
        lines.append("**📋 操作建议：**")
        for sl in r["summary_lines"]:
            lines.append(f"- {sl}")

        # 风险提示
        if r["risk_flags"]:
            lines.append("")
            lines.append("**⚠️ 风险提醒：**")
            for rf in r["risk_flags"]:
                lines.append(f"- {rf}")

        # 注意事项
        lines.append("")
        lines.append("**💡 T+0操作原则：**")
        lines.append("- ⏰ 当日买卖必须完成，不隔夜留仓")
        lines.append("- 🎯 目标不宜过大，1-3%即可满意")
        lines.append("- 🛑 到止损位无条件执行，不扛单")
        lines.append("- 📊 下午14:30后不建议开新仓")
        lines.append("- 🚫 急涨急跌时不追，等回踩确认")

        if i < len(analysis["results"]) - 1:
            lines.append("")
            lines.append("---")
            lines.append("")

    # 总结
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("### 📊 做T优先级排序")
    lines.append("")
    sorted_results = sorted(analysis["results"], key=lambda x: x["total_score"], reverse=True)
    lines.append("| 优先级 | 标的 | 评分 | 方向 | 核心优势 |")
    lines.append("|--------|------|------|------|----------|")
    for idx, r in enumerate(sorted_results):
        top_reason = r["scores"]["振幅"][1] if r["scores"]["振幅"][0] >= 15 else \
                     r["scores"]["盈亏状态"][1]
        lines.append(f"| {idx+1} | {r['name']} | {r['total_score']}分 | {r['direction'].split('(')[0]} | {top_reason.split('—')[0].split('|')[0]} |")

    return "\n".join(lines)
