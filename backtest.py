#!/usr/bin/env python3
"""
A股短线标的回测机制 v6.13.10
基于推荐历史中的进场价/止损/止盈，拉取T+1实际收盘价，计算各策略胜率与收益
"""
import urllib.request, json, os, time
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict

WORKSPACE = "/workspace"

# ============================================================
# 1. 读取所有推荐历史
# ============================================================
def read_all_recommendations():
    all_recs = []
    seen = set()
    for fname in sorted(os.listdir(WORKSPACE)):
        if fname.startswith("推荐历史_") and fname.endswith(".json"):
            path = os.path.join(WORKSPACE, fname)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for r in data:
                    if r.get("type") == "recommendation":
                        r["source_file"] = fname
                        # Deduplicate: code + rec_date as unique key
                        key = f"{r.get('code', '')}-{r.get('date', '')}"
                        if key not in seen:
                            seen.add(key)
                            all_recs.append(r)
            except Exception:pass
    return all_recs

# ============================================================
# 2. 获取股票历史K线数据（用于T+1回测）— 腾讯优先(沙箱可用), 东方财富备用
# ============================================================
def fetch_t1_close(code, target_date):
    """
    获取指定股票在 target_date 的收盘价
    优先使用腾讯日K接口(沙箱可用), 东方财富备用
    """
    # 主方案: 腾讯日K (沙箱可用)
    prefix = "sh" if code.startswith("6") else "sz"
    try:
        url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,{target_date.replace('-','')},,1,qfq"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read())
        klines = data.get("data", {}).get(f"{prefix}{code}", {}).get("qfqday", [])
        if klines:
            k = klines[-1]
            return {"close": float(k[2]), "open": float(k[1]), "high": float(k[3]), "low": float(k[4])}
    except Exception as e:
        pass
    
    # Fallback: 东方财富日K (非沙箱环境)
    try:
        if code.startswith("6"):
            secid = f"1.{code}"
        else:
            secid = f"0.{code}"
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101", "fqt": "1",
            "beg": target_date,  # 腾讯K线需要带连字符格式 YYYY-MM-DD
            "end": target_date,  # 腾讯K线需要带连字符格式 YYYY-MM-DD
            "_": str(int(time.time() * 1000))
        }
        query = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{url}?{query}", headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://quote.eastmoney.com/'
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())
        if data and data.get("data") and data["data"].get("klines"):
            kline = data["data"]["klines"][-1]
            parts = kline.split(",")
            return {"close": float(parts[2]), "open": float(parts[1]), "high": float(parts[3]), "low": float(parts[4])}
    except Exception:pass
    
    return None

# ============================================================
# 3. 计算下一个交易日
# ============================================================
def next_trading_day(date_str):
    """简单推算：跳过周末，不考虑节假日"""
    d = datetime.strptime(date_str, "%Y-%m-%d")
    wd = d.weekday()
    if wd == 4:  # 周五 → 下周一
        return (d + timedelta(days=3)).strftime("%Y-%m-%d")
    elif wd == 5:  # 周六 → 下周一
        return (d + timedelta(days=2)).strftime("%Y-%m-%d")
    elif wd == 6:  # 周日 → 下周一
        return (d + timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        return (d + timedelta(days=1)).strftime("%Y-%m-%d")

# ============================================================
# 4. 回测核心逻辑
# ============================================================
def backtest(recommendations):
    results = []
    strategy_stats = defaultdict(lambda: {
        "total": 0, "win": 0, "hit_tp": 0, "hit_sl": 0,
        "returns": [], "max_return": 0, "min_return": 0,
        "hit_entry": 0  # 盘中触及进场价
    })
    
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")
    
    for rec in recommendations:
        code = rec.get("code", "")
        name = rec.get("name", "")
        strategy = rec.get("strategy", "")
        entry = rec.get("entry", 0)  # entry在score.py中已下修0.97倍(v6.6.41)，回测无需重复下修
        stop_loss = rec.get("stop_loss", 0)
        take_profit = rec.get("take_profit", 0)
        rec_date = rec.get("date", "")
        
        if not code or not entry: continue
        
        # T+1日期
        t1_date = next_trading_day(rec_date)
        # 如果T+1还未到（即今天），跳过
        if t1_date > today:
            continue
        
        # 获取T+1实际数据
        t1_data = fetch_t1_close(code, t1_date)
        if not t1_data:
            continue
        
        t1_close = t1_data["close"]
        t1_high = t1_data["high"]
        t1_low = t1_data["low"]
        t1_open = t1_data["open"]
        
        # 计算各项指标
        return_pct = round((t1_close - entry) / entry * 100, 2)
        is_win = return_pct > 0
        
        # 检查是否触及止盈/止损
        hit_tp = t1_high >= take_profit if take_profit else False
        hit_sl = t1_low <= stop_loss if stop_loss else False
        
        # 检查是否触及进场价（盘中低点 <= 进场价 <= 盘中高点）
        hit_entry = t1_low <= entry <= t1_high
        
        result = {
            "code": code, "name": name, "strategy": strategy,
            "rec_date": rec_date, "t1_date": t1_date,
            "entry": entry, "stop_loss": stop_loss, "take_profit": take_profit,
            "t1_open": t1_open, "t1_high": t1_high, "t1_low": t1_low, "t1_close": t1_close,
            "return_pct": return_pct, "is_win": is_win,
            "hit_tp": hit_tp, "hit_sl": hit_sl, "hit_entry": hit_entry
        }
        results.append(result)
        
        # 统计
        st = strategy_stats[strategy]
        st["total"] += 1
        if is_win: st["win"] += 1
        if hit_tp: st["hit_tp"] += 1
        if hit_sl: st["hit_sl"] += 1
        if hit_entry: st["hit_entry"] += 1
        st["returns"].append(return_pct)
        st["max_return"] = max(st["max_return"], return_pct)
        st["min_return"] = min(st["min_return"], return_pct)
    
    return results, strategy_stats

# ============================================================
# 5. 生成回测报告
# ============================================================
def generate_report(results, stats):
    beijing_now = datetime.now(timezone.utc) + timedelta(hours=8)
    strategy_names = {"A": "动量延续", "B": "超跌反弹", "C": "事件驱动", "D": "回调企稳", "E": "资金埋伏", "F": "北向资金", "G": "横盘突破", "H": "地量见底", "I": "均线突破", "J": "龙回头", "K": "缺口回补", "L": "黄金坑", "M": "涨停回调", "N": "新高突破", "O": "回踩均线", "P": "地量反弹", "Q": "W底突破", "R": "主力共振(强)", "S": "主力共振(弱)", "T": "主力观察"}
    
    lines = []
    lines.append("# A股短线标的回测报告")
    lines.append("")
    lines.append(f"**生成时间**: {beijing_now.strftime('%Y-%m-%d %H:%M')} (北京时间)")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 回测方法论")
    lines.append("")
    lines.append("| 维度 | 说明 |")
    lines.append("|------|------|")
    lines.append("| 数据范围 | 推荐历史中所有已完成T+1交易的标的 |")
    lines.append("| 进场价 | 使用推荐时的 `entry` 字段（基于ATR/振幅推算） |")
    lines.append("| 胜负判定 | T+1收盘价 > 进场价 → 胜 | 否则 → 负 |")
    lines.append("| 止盈判定 | T+1盘中最高价 ≥ 止盈价 → 触发 |")
    lines.append("| 止损判定 | T+1盘中最低价 ≤ 止损价 → 触发 |")
    lines.append("| 进场可行 | T+1盘中最低价 ≤ 进场价 ≤ 盘中最高价 → 可成交 |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 各策略回测汇总")
    lines.append("")
    lines.append("| 策略 | 样本数 | 胜率 | 平均收益 | 最大收益 | 最大亏损 | 止盈触发率 | 止损触发率 | 进场可行率 |")
    lines.append("|------|--------|------|----------|----------|----------|------------|------------|------------|")
    
    total_all = 0
    total_win = 0
    total_returns = []
    
    for s in ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T"]:
        st = stats.get(s)
        if not st or st["total"] == 0:
            continue
        total_all += st["total"]
        total_win += st["win"]
        total_returns.extend(st["returns"])
        
        win_rate = round(st["win"] / st["total"] * 100, 1)
        avg_ret = round(sum(st["returns"]) / len(st["returns"]), 2)
        tp_rate = round(st["hit_tp"] / st["total"] * 100, 1)
        sl_rate = round(st["hit_sl"] / st["total"] * 100, 1)
        entry_rate = round(st["hit_entry"] / st["total"] * 100, 1)
        sname = strategy_names.get(s, s)
        
        lines.append(f"| {s} {sname} | {st['total']} | {win_rate}% | {avg_ret:+.2f}% | {st['max_return']:+.2f}% | {st['min_return']:+.2f}% | {tp_rate}% | {sl_rate}% | {entry_rate}% |")
    
    if total_all > 0:
        total_win_rate = round(total_win / total_all * 100, 1)
        total_avg = round(sum(total_returns) / len(total_returns), 2)
        lines.append(f"| **合计** | **{total_all}** | **{total_win_rate}%** | **{total_avg:+.2f}%** | | | | | |")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 最近回测明细")
    lines.append("")
    lines.append("| 日期 | 代码 | 名称 | 策略 | 进场 | T+1收盘 | 收益 | 结果 | 止盈 | 止损 |")
    lines.append("|------|------|------|------|------|---------|------|------|------|------|")
    
    for r in sorted(results, key=lambda x: (x["rec_date"], x["strategy"]), reverse=True)[:50]:
        emoji = "✅" if r["is_win"] else "❌"
        tp_emoji = "🎯" if r["hit_tp"] else "-"
        sl_emoji = "🛑" if r["hit_sl"] else "-"
        lines.append(f"| {r['rec_date']} | {r['code']} | {r['name']} | {r['strategy']} | {r['entry']} | {r['t1_close']} | {r['return_pct']:+.2f}% | {emoji} | {tp_emoji} | {sl_emoji} |")
    
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 策略诊断")
    lines.append("")
    
    for s in ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T"]:
        st = stats.get(s)
        if not st or st["total"] < 3:
            continue
        sname = strategy_names.get(s, s)
        win_rate = st["win"] / st["total"] * 100
        tp_rate = st["hit_tp"] / st["total"] * 100
        sl_rate = st["hit_sl"] / st["total"] * 100
        
        diag = []
        if win_rate >= 60:
            diag.append(f"✅ 胜率{win_rate:.0f}%表现优秀，策略有效")
        elif win_rate >= 40:
            diag.append(f"⚠️ 胜率{win_rate:.0f}%表现一般，建议优化进场价")
        else:
            diag.append(f"❌ 胜率{win_rate:.0f}%偏低，策略需重新评估")
        
        if tp_rate > sl_rate:
            diag.append(f"止盈触发({tp_rate:.0f}%) > 止损触发({sl_rate:.0f}%)，盈亏比健康")
        elif sl_rate > 0:
            diag.append(f"⚠️ 止损触发({sl_rate:.0f}%)偏高，建议收紧进场价或放宽止损")
        
        if st["hit_entry"] / st["total"] * 100 < 50:
            diag.append(f"⚠️ 进场价命中率偏低({st['hit_entry']/st['total']*100:.0f}%)，建议调整进场价推算公式")
        
        lines.append(f"**{s} {sname}** (n={st['total']}): {' | '.join(diag)}")
        lines.append("")
    
    lines.append("---")
    lines.append("*⚠️ 回测仅基于历史数据，不预测未来表现。仅供参考。*")
    
    return "\n".join(lines)

# ============================================================
# 6. 主流程
# ============================================================
def main():
    print("=" * 60)
    print("A股短线标的回测机制")
    print("=" * 60)
    
    recs = read_all_recommendations()
    print(f"读取推荐记录: {len(recs)} 条")
    
    # 按日期统计
    dates = Counter(r.get("date") for r in recs)
    print(f"日期分布: {dict(dates)}")
    
    results, stats = backtest(recs)
    print(f"可回测(T+1已完成): {len(results)} 条")
    
    report = generate_report(results, stats)
    
    out_path = f"{WORKSPACE}/回测报告_{datetime.now(timezone.utc).strftime('%Y%m%d')}.md"
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"\n回测报告已生成: {out_path}")
    
    # Print summary
    print("\n📊 回测摘要:")
    for s in ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T"]:
        st = stats.get(s)
        if st and st["total"] > 0:
            win_rate = round(st["win"] / st["total"] * 100, 1)
            avg_ret = round(sum(st["returns"]) / len(st["returns"]), 2)
            print(f"  {s}: {st['total']}笔 | 胜率{win_rate}% | 均收益{avg_ret:+.2f}% | 止盈{st['hit_tp']} | 止损{st['hit_sl']}")

if __name__ == "__main__":
    main()