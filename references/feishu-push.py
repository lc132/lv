import urllib.request, json, os
from collections import Counter

FEISHU_WEBHOOK = None
with open("/workspace/.feishu_webhook") as f:
    FEISHU_WEBHOOK = f.read().strip()
if not FEISHU_WEBHOOK:
    log_alert("WARNING", "飞书推送", "未配置Webhook URL，跳过")
    return

pages_base = "https://lc132.github.io/lv"
pages_report = f"{pages_base}/ashare-screening-{pred_yyyymmdd}/ashare-screening-{pred_yyyymmdd}.html"

card = {
    "msg_type": "interactive",
    "card": {
        "header": {
            "title": {"tag": "plain_text", "content": f"📊 每日短线标的筛选 — {prediction_date}"},
            "template": "blue"
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**数据来源**: {data_date}  |  **市场环境**: {market_condition}  |  **建议仓位**: {position}%"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"原始标的池: **{total_raw}**只 → ... → ★ 最终: **{final_count}**只"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**策略分布**: {strategy_summary}"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"📈 [**查看完整可视化报告（GitHub Pages）**]({pages_report})\n📁 [**报告列表首页**]({pages_base})"}},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "⚠️ 仅供参考，不构成投资建议"}]}
        ]
    }
}

req = urllib.request.Request(FEISHU_WEBHOOK,
    data=json.dumps(card, ensure_ascii=False).encode('utf-8'),
    headers={'Content-Type': 'application/json'}, method='POST')
resp = urllib.request.urlopen(req, timeout=10)
result = json.loads(resp.read())
if result.get('code') == 0:
    log_alert("INFO", "飞书推送", f"✅ {prediction_date} 已推送（Pages: {pages_report}）")
else:
    log_alert("WARNING", "飞书推送", f"推送失败: {result.get('msg','')}")
