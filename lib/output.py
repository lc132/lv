#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤20-22: 输出Markdown、生成HTML报告、最终验证、写推荐历史
"""
from lib.core import *

# ============================================================
# 步骤20: 输出Markdown
# ============================================================
def step20_output_markdown(ctx):
    print("\n" + "=" * 60)
    print("步骤20: 输出Markdown")
    print("=" * 60)
    
    prediction_date = ctx['prediction_date']
    md_path = f"{DATA_DIR}/短线标的_{prediction_date}.md"
    candidates = ctx.get('candidates', [])
    
    strategy_counts = Counter()
    
    # 构建策略标签映射
    strategy_labels = {
        'A': '动量延续', 'B': '超跌反弹', 'C': '事件驱动',
        'D': '回调企稳', 'E': '资金埋伏'
    }
    confidence_emoji = {'★★★': '🟢', '★★': '🟡', '★': '🔴'}
    
    lines = []
    lines.append(f"# 📊 A股短线标的筛选 — {prediction_date}")
    lines.append("")
    lines.append(f"> **数据日期**: {ctx.get('data_date', prediction_date)} | **市场环境**: {ctx.get('market_condition', '未知')} | **建议仓位**: {ctx.get('position', 0)}%")
    lines.append("")
    
    # 筛选管道
    lines.append("## 筛选管道")
    lines.append("")
    pipeline = (
        f"原始 {ctx.get('total_raw', 0)}只 "
        f"→ 硬排除 {ctx.get('excluded_count', 0)}只 "
        f"→ 信号过滤 {ctx.get('signal_dropped', 0)}只 "
        f"→ 策略匹配 {ctx.get('passed_strategy', 0)}只 "
        f"→ 行业限制 {ctx.get('passed_industry', 0)}只 "
        f"→ 新闻筛查 {ctx.get('passed_news', 0)}只 "
        f"→ ★ **最终 {len(candidates)}只**"
    )
    lines.append(pipeline)
    lines.append("")
    
    # 推荐标的表
    lines.append("## 🎯 推荐标的")
    lines.append("")
    lines.append("| # | 策略 | 标的 | 代码 | 行业 | 涨跌幅 | 开盘 | 收盘 | 振幅 | 评分 | 置信 | 进场 | 止损 | 止盈 |")
    lines.append("|---|------|------|------|------|--------|------|------|------|------|------|------|------|------|")
    
    for i, rec in enumerate(candidates, 1):
        strategy = rec.get('strategy', '')
        strategy_counts[strategy] += 1
        label = strategy_labels.get(strategy, strategy)
        
        chg_pct = rec.get('change_pct', 0)
        chg_str = f"🔴+{chg_pct:.2f}%" if chg_pct > 0 else f"🟢{chg_pct:.2f}%"
        
        conf = rec.get('confidence', '')
        mark = confidence_emoji.get(conf, '')
        conf_str = f"{mark}{conf}"
        
        entry = rec.get('entry', '')
        stop_loss = rec.get('stop_loss', '')
        take_profit = rec.get('take_profit', '')
        amplitude = rec.get('amplitude', 0)
        
        lines.append(
            f"| {i} | **{label}** | **{rec.get('name', '')}** | {rec.get('code', '')} | "
            f"{rec.get('industry', '') or rec.get('sector', '')} | {chg_str} | "
            f"{rec.get('open', '')} | {rec.get('close', '')} | "
            f"{amplitude:.2f}% | {rec.get('score', 0)} | {conf_str} | "
            f"{entry} | {stop_loss} | {take_profit} |"
        )
    
    lines.append("")
    lines.append(f"📊 共筛选出 **{len(candidates)}** 只标的（A动量:{strategy_counts.get('A',0)} B超跌:{strategy_counts.get('B',0)} C事件:{strategy_counts.get('C',0)} D回调:{strategy_counts.get('D',0)} E资金:{strategy_counts.get('E',0)}）")
    lines.append("")
    
    # 策略说明
    lines.append("## 策略说明")
    lines.append("")
    strategies_desc = [
        ("**A 动量延续**", "涨幅3-7%，量比1.5-3.0，量>5日均×1.5且>昨日×1.2，MA5>MA10>MA20 — 仓位强35-40%/震荡12-17%/弱关闭"),
        ("**B 超跌反弹**", "连跌≥3日，量<5日均×0.6，RSI(14)连续≥3日<35或底背离，MA20/MA60支撑，KDJ的K<20且J拐头向上 — 仓位强10-12%/震荡12-15%/弱12-15%"),
        ("**C 事件驱动**", "重大合同/预增>50%或部委级政策，事件时效5级衰减 — 仓位强10-12%/震荡10-12%/弱5-8%"),
        ("**D 回调企稳突破**", "20日内创新高+回调至MA20±3%+连续3日缩量+站回MA5放量 — 仓位强10-12%/震荡12-15%/弱8-12%"),
        ("**E 资金埋伏**", "北向3日连续净买+主力流入>3000万+涨幅<2% — 仓位强5-8%/震荡5-8%/弱3-5%"),
    ]
    for name, desc in strategies_desc:
        lines.append(f"- {name}：{desc}")
    
    lines.append("")
    
    # 硬排除TOP5
    if ctx.get('exclusion_stats'):
        lines.append("## 硬排除 TOP5")
        lines.append("")
        for reason, count in ctx['exclusion_stats'].most_common(5):
            lines.append(f"- {reason}: {count}只")
        lines.append("")
    
    # 风险提示
    lines.append("---")
    lines.append("")
    lines.append("> ⚠️ **免责声明**: 仅供参考，不构成投资建议。投资有风险，入市需谨慎。")
    lines.append(f"> 版本: {BUILTIN_VERSION} | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} CST")
    
    md_content = "\n".join(lines)
    
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md_content)
    
    print(f"  Markdown已保存: {md_path}")
    print(f"  最终推荐: {len(candidates)} 只")
    
    ctx['md_path'] = md_path
    ctx['final_recommend_count'] = len(candidates)
    ctx['strategy_counts'] = strategy_counts

# ============================================================
# 步骤20B: 生成HTML报告
# ============================================================
def step20B_html_report(ctx):
    print("\n" + "=" * 60)
    print("步骤20B: 生成HTML报告")
    print("=" * 60)
    
    prediction_date = ctx['prediction_date']
    pred_yyyymmdd = prediction_date.replace('-', '')
    report_dir = f"{DATA_DIR}/ashare-screening-{pred_yyyymmdd}"
    os.makedirs(report_dir, exist_ok=True)
    
    candidates = ctx.get('candidates', [])
    strategy_counts = ctx.get('strategy_counts', Counter())
    market = ctx.get('market_condition', '震荡')
    position = ctx.get('position', 55)
    data_date = ctx['data_date']
    
    # 构建HTML
    strategy_colors = {'A': '#2E7D32', 'B': '#1565C0', 'C': '#7B1FA2', 'D': '#E65100', 'E': '#F9A825'}
    strategy_bg = {'A': '#E8F5E9', 'B': '#E3F2FD', 'C': '#F3E5F5', 'D': '#FBE9E7', 'E': '#FFF8E1'}
    
    # 生成标的表格行
    rows_html = ""
    for i, rec in enumerate(candidates, 1):
        chg = rec.get('change_pct', 0)
        chg_color = '#9C0006' if chg > 0 else '#006100'
        chg_sign = '+' if chg > 0 else ''
        s = rec.get('strategy', '')
        bg = strategy_bg.get(s, '#FFFFFF')
        conf = rec.get('confidence', '')
        conf_color = {'★★★': '#006100', '★★': '#BF8F00', '★': '#9C0006'}.get(conf, '#333')
        url = rec.get('url', '#')
        
        rows_html += f"""
        <tr style="background:{bg}">
            <td>{i}</td>
            <td><span class="badge" style="background:{strategy_colors.get(s,'#333')};color:#fff;padding:2px 8px;border-radius:3px;font-size:11px">{s}</span></td>
            <td><a href="{url}" target="_blank" style="color:#0563C1;text-decoration:underline">{rec.get('name','')}</a></td>
            <td>{rec.get('code','')}</td>
            <td>{rec.get('industry','未知')}</td>
            <td style="color:{chg_color};font-weight:bold">{chg_sign}{chg:.2f}%</td>
            <td>{rec.get('open','')}</td>
            <td>{rec.get('close','')}</td>
            <td>{rec.get('amplitude',''):.2f}%</td>
            <td style="font-weight:bold">{rec.get('score',0)}</td>
            <td style="color:{conf_color};font-weight:bold">{conf}</td>
            <td>{rec.get('entry','')}</td>
            <td>{rec.get('stop_loss','')}</td>
            <td>{rec.get('take_profit','')}</td>
        </tr>"""
    
    # 策略分布条形图
    total = len(candidates) or 1
    seg_bars = ""
    for s in ['A', 'B', 'C', 'D', 'E']:
        cnt = strategy_counts.get(s, 0)
        pct = cnt / total * 100
        if pct > 0:
            seg_bars += f'<div style="flex:{pct};background:{strategy_colors[s]};height:24px;display:flex;align-items:center;justify-content:center;color:#fff;font-size:11px;font-weight:bold">{s}:{cnt}</div>'
    
    # 漏斗数据
    funnel_steps = [
        ("原始标的池", ctx.get('total_raw', 0)),
        ("硬排除后", ctx.get('passed_hard_filter', 0)),
        ("信号过滤后", ctx.get('passed_signal_filter', 0)),
        ("策略匹配", ctx.get('passed_strategy', 0)),
        ("行业+新闻", ctx.get('final_count', 0)),
        ("最终推荐", ctx.get('final_recommend_count', 0)),
    ]
    
    funnel_html = ""
    max_w = max(s[1] for s in funnel_steps) or 1
    for i, (label, count) in enumerate(funnel_steps):
        w_pct = count / max_w * 100 if max_w > 0 else 0
        funnel_html += f"""
        <div class="funnel-step" style="width:{max(20, w_pct)}%;margin:4px auto;background:linear-gradient(90deg, #7B1FA2, #1565C0);color:#fff;padding:8px 16px;border-radius:4px;text-align:center;font-size:13px">
            {label}: <b>{count}</b>只
        </div>"""
    
    # 告警日志
    alert_html = ""
    alert_path = f"{DATA_DIR}/系统告警.log"
    if os.path.exists(alert_path):
        with open(alert_path, 'r') as f:
            lines = f.readlines()
        today_lines = [l for l in lines if data_date in l]
        if today_lines:
            for l in today_lines[-10:]:
                level = "INFO"
                if "[WARNING]" in l: level = "WARNING"
                if "[ERROR]" in l: level = "ERROR"
                level_color = {'INFO': '#1565C0', 'WARNING': '#F9A825', 'ERROR': '#C62828'}.get(level, '#333')
                alert_html += f'<div style="display:flex;align-items:flex-start;margin:4px 0"><span style="background:{level_color};color:#fff;padding:2px 8px;border-radius:3px;font-size:10px;min-width:50px;text-align:center;margin-right:8px">{level}</span><span style="font-size:12px;color:#555">{l.split("]", 2)[-1].strip() if "]" in l else l.strip()}</span></div>'
        else:
            alert_html = '<div style="color:#999;font-size:13px">今日无异常</div>'
    
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股短线标的筛选报告 — {prediction_date}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: 'Noto Sans CJK SC', 'WenQuanYi Micro Hei', Arial, sans-serif; background:#f5f7fa; color:#333; line-height:1.6; }}
.container {{ max-width:1200px; margin:0 auto; padding:20px; }}

/* 报告头部 */
.header {{ background:linear-gradient(135deg, #1a237e, #283593, #1565C0); color:#fff; padding:30px; border-radius:12px; margin-bottom:24px; }}
.header h1 {{ font-size:24px; margin-bottom:8px; }}
.header .meta-row {{ display:flex; gap:16px; margin-top:16px; flex-wrap:wrap; }}
.header .meta-card {{ background:rgba(255,255,255,0.15); padding:12px 20px; border-radius:8px; text-align:center; min-width:100px; }}
.header .meta-card .label {{ font-size:11px; opacity:0.8; }}
.header .meta-card .value {{ font-size:18px; font-weight:bold; }}

/* 筛选管道 */
.pipeline {{ background:#fff; padding:24px; border-radius:12px; margin-bottom:24px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
.pipeline h2 {{ font-size:18px; margin-bottom:16px; color:#1a237e; }}

/* 漏斗 */
.funnel {{ padding:12px 0; }}

/* 图表区 */
.charts {{ background:#fff; padding:24px; border-radius:12px; margin-bottom:24px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
.charts h2 {{ font-size:18px; margin-bottom:16px; color:#1a237e; }}
.chart-grid {{ display:grid; grid-template-columns:1fr 1fr; gap:20px; }}
.chart-card {{ background:#f8f9fb; padding:16px; border-radius:8px; }}
.chart-card h3 {{ font-size:14px; margin-bottom:12px; color:#555; }}

/* 策略分布 */
.seg-bar {{ display:flex; border-radius:6px; overflow:hidden; margin-bottom:12px; }}

/* 图例 */
.legend {{ display:flex; flex-wrap:wrap; gap:12px; margin-top:8px; }}
.legend-item {{ display:flex; align-items:center; gap:4px; font-size:12px; }}
.legend-dot {{ width:12px; height:12px; border-radius:3px; }}

/* 表格 */
.table-section {{ background:#fff; padding:24px; border-radius:12px; margin-bottom:24px; box-shadow:0 2px 8px rgba(0,0,0,0.06); overflow-x:auto; }}
.table-section h2 {{ font-size:18px; margin-bottom:16px; color:#1a237e; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th {{ background:#1F4E79; color:#fff; padding:10px 8px; text-align:center; font-weight:bold; white-space:nowrap; }}
td {{ padding:8px; text-align:center; border-bottom:1px solid #e0e0e0; white-space:nowrap; }}

/* 策略说明 */
.strategy-desc {{ background:#fff; padding:24px; border-radius:12px; margin-bottom:24px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
.strategy-desc h2 {{ font-size:18px; margin-bottom:16px; color:#1a237e; }}
.strategy-desc table {{ font-size:12px; }}
.strategy-desc td {{ text-align:left; padding:8px; }}

/* 告警 */
.alerts {{ background:#fff; padding:24px; border-radius:12px; margin-bottom:24px; box-shadow:0 2px 8px rgba(0,0,0,0.06); }}
.alerts h2 {{ font-size:18px; margin-bottom:16px; color:#1a237e; }}

/* 尾部 */
.footer {{ text-align:center; padding:20px; color:#999; font-size:12px; }}
.footer .disclaimer {{ color:#C62828; font-size:13px; margin-top:8px; }}

@media (max-width:768px) {{
    .chart-grid {{ grid-template-columns:1fr; }}
    .header .meta-row {{ justify-content:center; }}
}}
</style>
</head>
<body>
<div class="container">

<!-- 1. 报告头部 -->
<div class="header">
    <h1>📊 A股短线标的筛选报告</h1>
    <div style="font-size:14px;opacity:0.9">{BUILTIN_VERSION} | 数据来源: {data_date}</div>
    <div class="meta-row">
        <div class="meta-card"><div class="label">预测日期</div><div class="value">{prediction_date}</div></div>
        <div class="meta-card"><div class="label">数据日期</div><div class="value">{data_date}</div></div>
        <div class="meta-card"><div class="label">市场环境</div><div class="value">{market}</div></div>
        <div class="meta-card"><div class="label">建议仓位</div><div class="value">{position}%</div></div>
        <div class="meta-card"><div class="label">最终推荐</div><div class="value">{len(candidates)}只</div></div>
    </div>
</div>

<!-- 2. 筛选管道 -->
<div class="pipeline">
    <h2>🔄 筛选管道</h2>
    <div class="funnel">
        {funnel_html}
    </div>
</div>

<!-- 3. 数据可视化 -->
<div class="charts">
    <h2>📈 数据可视化</h2>
    <div class="chart-grid">
        <div class="chart-card">
            <h3>策略分布</h3>
            <div class="seg-bar">{seg_bars}</div>
            <div class="legend">
                {''.join(f'<div class="legend-item"><span class="legend-dot" style="background:{strategy_colors[s]}"></span>{s}: {strategy_counts.get(s,0)}只 ({strategy_counts.get(s,0)/total*100:.0f}%)</div>' for s in ['A','B','C','D','E'] if strategy_counts.get(s,0) > 0)}
            </div>
        </div>
        <div class="chart-card">
            <h3>最终推荐 ({len(candidates)}只)</h3>
            <div style="font-size:48px;text-align:center;color:#1a237e;font-weight:bold;padding:20px;">{len(candidates)}</div>
            <div style="text-align:center;color:#888;font-size:13px">预测日期: {prediction_date}</div>
        </div>
    </div>
</div>

<!-- 4. 最终推荐标的表 -->
<div class="table-section">
    <h2>🎯 最终推荐标的 ({len(candidates)}只)</h2>
    <table>
        <thead><tr>
            <th>序号</th><th>策略</th><th>标的</th><th>代码</th><th>行业</th>
            <th>涨跌幅</th><th>开盘价</th><th>收盘价</th><th>振幅</th>
            <th>评分</th><th>置信度</th><th>进场</th><th>止损</th><th>止盈</th>
        </tr></thead>
        <tbody>{rows_html}</tbody>
    </table>
</div>

<!-- 5. 策略说明 -->
<div class="strategy-desc">
    <h2>📋 策略说明</h2>
    <table>
        <tr style="background:{strategy_bg['A']}"><td style="font-weight:bold;color:{strategy_colors['A']}">A 动量延续</td><td>涨幅3-7%，量比1.5-3.0，MA5>MA10>MA20 — 仓位强35-40%/震荡12-17%/弱关闭</td></tr>
        <tr style="background:{strategy_bg['B']}"><td style="font-weight:bold;color:{strategy_colors['B']}">B 超跌反弹</td><td>连跌≥3日，量<5日均×0.6，RSI(14)<35，KDJ(K<20且J拐头)，站上MA5+放量确认 — 仓位10-15%</td></tr>
        <tr style="background:{strategy_bg['C']}"><td style="font-weight:bold;color:{strategy_colors['C']}">C 事件驱动</td><td>重大合同/预增>50%/部委级政策，事件时效5级衰减 — 仓位5-12%</td></tr>
        <tr style="background:{strategy_bg['D']}"><td style="font-weight:bold;color:{strategy_colors['D']}">D 回调企稳突破</td><td>20日内创新高+回调MA20±3%+连3日缩量+站回MA5放量 — 仓位8-15%</td></tr>
        <tr style="background:{strategy_bg['E']}"><td style="font-weight:bold;color:{strategy_colors['E']}">E 资金埋伏</td><td>北向3日连续净买+主力流入>3000万+涨幅<2% — 仓位3-8%</td></tr>
    </table>
</div>

<!-- 6. 系统告警 -->
<div class="alerts">
    <h2>⚠️ 系统告警 ({data_date})</h2>
    {alert_html}
</div>

<!-- 7. 报告尾部 -->
<div class="footer">
    <div>A股盘前短线标的筛选 {BUILTIN_VERSION} | 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
    <div>规则来源: SKILL.md {BUILTIN_VERSION} | GitHub: lc132/lv</div>
    <div class="disclaimer">⚠️ 仅供参考，不构成投资建议</div>
</div>

</div>
</body>
</html>"""
    
    html_path = f"{report_dir}/ashare-screening-{pred_yyyymmdd}.html"
    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)
    
    print(f"  HTML报告已保存: {html_path}")
    ctx['html_path'] = html_path
    ctx['report_dir'] = report_dir

# ============================================================
# 步骤21: 最终验证
# ============================================================
def step21_final_verify(ctx):
    print("\n" + "=" * 60)
    print("步骤21: 最终验证")
    print("=" * 60)
    
    md_path = ctx.get('md_path', '')
    final_count = ctx.get('final_recommend_count', 0)
    
    try:
        if not os.path.exists(md_path):
            print(f"  ⚠️ MD文件不存在: {md_path}")
            log_alert("WARNING", "数量校验", f"MD文件不存在: {md_path}")
            return
        
        with open(md_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 统计 Markdown 表格中的数据行（以 | 数字 | 开头的行）
        table_rows = 0
        for line in content.split('\n'):
            line = line.strip()
            if line and line.startswith('| ') and line.split('|')[1].strip().isdigit():
                table_rows += 1
        
        if table_rows != final_count:
            err = f"概况{final_count}≠MD表格行数{table_rows}"
            print(f"  ⚠️ {err}")
            log_alert("ERROR", "数量校验", err)
        else:
            print(f"  ✅ 验证通过（{final_count}只）")
    except Exception as e:
        print(f"  验证异常: {str(e)[:60]}")

# ============================================================
# 步骤22: 写推荐历史
# ============================================================
def step22_write_history(ctx):
    print("\n" + "=" * 60)
    print("步骤22: 写推荐历史")
    print("=" * 60)
    
    candidates = ctx.get('candidates', [])
    prediction_date = ctx['prediction_date']
    pred_yyyymmdd = prediction_date.replace('-', '')
    
    # 按日期归档
    archive_path = f"{DATA_DIR}/推荐历史_{pred_yyyymmdd}.json"
    
    for rec in candidates:
        recommendation = {
            "type": "recommendation",
            "date": prediction_date,
            "code": rec.get('code', ''),
            "name": rec.get('name', ''),
            "strategy": rec.get('strategy', ''),
            "score": rec.get('score', 0),
            "confidence": rec.get('confidence', ''),
            "entry": rec.get('entry'),
            "stop_loss": rec.get('stop_loss'),
            "take_profit": rec.get('take_profit'),
            "change_pct": rec.get('change_pct'),
            "close": rec.get('close'),
            "reason": rec.get('reason', ''),
        }
        safe_append_json(archive_path, recommendation)
    
    print(f"  推荐历史已归档: {archive_path} ({len(candidates)}条)")