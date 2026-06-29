# ============================================================
# A股短线筛选 — 历史回测模块 v6.12.11
# 读取推荐历史，获取后续K线，模拟止盈止损，计算回测指标
# ============================================================

import urllib.request
import json
import time
import os
from collections import defaultdict, Counter
from datetime import datetime, timedelta

# 策略止损/止盈比例（与主脚本 _STRATEGY_STOP_LOSS / _STRATEGY_TAKE_PROFIT 一致）
_STRATEGY_STOP_LOSS = {
    'A': 0.97, 'B': 0.93, 'C': 0.96, 'D': 0.95, 'E': 0.965,
    'F': 0.96, 'G': 0.95, 'H': 0.97, 'I': 0.96, 'J': 0.95,
    'K': 0.96, 'L': 0.95, 'M': 0.96, 'N': 0.96, 'O': 0.95,
    'P': 0.96, 'Q': 0.96, 'R': 0.95, 'S': 0.96, 'T': 0.95,
}
_STRATEGY_TAKE_PROFIT = {
    'A': 1.06, 'B': 1.07, 'C': 1.05, 'D': 1.06, 'E': 1.04,
    'F': 1.04, 'G': 1.05, 'H': 1.05, 'I': 1.05, 'J': 1.06,
    'K': 1.05, 'L': 1.05, 'M': 1.05, 'N': 1.05, 'O': 1.05,
    'P': 1.05, 'Q': 1.05, 'R': 1.05, 'S': 1.05, 'T': 1.05,
}
_STRATEGY_NAMES = {
    'A': '动量延续', 'B': '超跌反弹', 'C': '财报驱动', 'D': '回调企稳',
    'E': '资金埋伏', 'F': '北向资金', 'G': '横盘突破', 'H': '地量见底',
    'I': '事件驱动', 'J': '箱体突破', 'K': '均线金叉', 'L': '龙虎榜跟随',
    'M': '板块联动', 'N': '新高突破', 'O': '估值修复', 'P': '政策催化',
    'Q': '机构调研', 'R': '次新博弈', 'S': '高股息防守', 'T': '底部放量',
}

DATA_DIR = os.environ.get('LV_DATA_DIR', '/workspace')


def _safe_read_json(path, default=None):
    try:
        if not os.path.exists(path):
            return default if default is not None else []
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError, OSError):
        return default if default is not None else []


def _fetch_kline_range(code, start_date, lmt=15):
    """获取指定日期之后N根日K线（东方财富HTTP API）"""
    try:
        mc = '1' if code.startswith('6') else '0'
        secid = f'{mc}.{code}'
        end_dt = datetime.strptime(start_date, '%Y-%m-%d') + timedelta(days=30)
        end_date = end_dt.strftime('%Y%m%d')
        url = (f'https://push2his.eastmoney.com/api/qt/stock/kline/get?'
               f'secid={secid}&fields1=f1,f2,f3,f4,f5,f6&'
               f'fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&'
               f'end={end_date}&lmt={lmt}')
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://quote.eastmoney.com/'})
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
        klines = data.get('data', {}).get('klines', [])
        if not klines:
            return []
        result = []
        for k in klines:
            parts = k.split(',')
            result.append({
                'date': parts[0], 'open': float(parts[1]),
                'close': float(parts[2]), 'high': float(parts[3]),
                'low': float(parts[4]), 'volume': float(parts[5]),
            })
        result = [r for r in result if r['date'] > start_date]
        return result
    except Exception:
        return []


def _simulate_trade(entry, stop_loss, take_profit, klines, hold_days=10):
    """模拟单笔交易：盘中触及止损/止盈则出场，否则持有到期"""
    if not klines:
        return {'result': 'no_data', 'exit_price': entry, 'exit_date': '',
                'exit_reason': 'no_data', 'return_pct': 0, 'hold_days': 0,
                'max_drawdown_pct': 0, 'max_profit_pct': 0}

    max_drawdown = 0.0
    max_profit = 0.0
    kl = klines[:hold_days]

    for i, k in enumerate(kl):
        high_pct = (k['high'] - entry) / entry * 100
        low_pct = (k['low'] - entry) / entry * 100
        max_profit = max(max_profit, high_pct)
        max_drawdown = min(max_drawdown, low_pct)

        if k['low'] <= stop_loss:
            return {
                'result': 'loss', 'exit_price': stop_loss,
                'exit_date': k['date'], 'exit_reason': 'stop_loss',
                'return_pct': round((stop_loss - entry) / entry * 100, 2),
                'hold_days': i + 1,
                'max_drawdown_pct': round(max_drawdown, 2),
                'max_profit_pct': round(max_profit, 2),
            }
        if k['high'] >= take_profit:
            return {
                'result': 'win', 'exit_price': take_profit,
                'exit_date': k['date'], 'exit_reason': 'take_profit',
                'return_pct': round((take_profit - entry) / entry * 100, 2),
                'hold_days': i + 1,
                'max_drawdown_pct': round(max_drawdown, 2),
                'max_profit_pct': round(max_profit, 2),
            }

    last_k = kl[-1]
    ret_pct = (last_k['close'] - entry) / entry * 100
    return {
        'result': 'win' if ret_pct > 0 else 'loss',
        'exit_price': round(last_k['close'], 2),
        'exit_date': last_k['date'],
        'exit_reason': 'hold_expire',
        'return_pct': round(ret_pct, 2),
        'hold_days': len(kl),
        'max_drawdown_pct': round(max_drawdown, 2),
        'max_profit_pct': round(max_profit, 2),
    }


def _compute_metrics(trades):
    """计算回测指标"""
    if not trades:
        return {'total': 0, 'win_rate': 0, 'avg_return': 0,
                'max_drawdown': 0, 'profit_factor': 0, 'sharpe': 0}

    total = len(trades)
    wins = [t for t in trades if t['result'] == 'win']
    losses = [t for t in trades if t['result'] == 'loss']
    no_data = [t for t in trades if t['result'] == 'no_data']

    win_rate = len(wins) / max(total - len(no_data), 1) * 100 if total > len(no_data) else 0
    avg_return = sum(t['return_pct'] for t in trades) / total if total > 0 else 0
    avg_win = sum(t['return_pct'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['return_pct'] for t in losses) / len(losses) if losses else 0
    avg_hold = sum(t['hold_days'] for t in trades if t['hold_days'] > 0) / max(total - len(no_data), 1)

    profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 and wins and losses else 0

    max_dd = 0.0; cum = 0.0
    for t in trades:
        cum += t['return_pct']
        max_dd = min(max_dd, cum)
        cum = max(cum, 0)

    returns = [t['return_pct'] for t in trades]
    if len(returns) > 1:
        avg_r = sum(returns) / len(returns)
        variance = sum((r - avg_r) ** 2 for r in returns) / len(returns)
        std = variance ** 0.5
        sharpe = avg_r / std if std > 0 else 0
    else:
        sharpe = 0

    return {
        'total': total, 'wins': len(wins), 'losses': len(losses),
        'no_data': len(no_data), 'win_rate': round(win_rate, 1),
        'avg_return': round(avg_return, 2), 'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2), 'avg_hold_days': round(avg_hold, 1),
        'profit_factor': round(profit_factor, 2),
        'max_drawdown': round(max_dd, 2), 'sharpe': round(sharpe, 2),
    }


def run_backtest(hold_days=10, max_days_lookback=90):
    """运行历史回测"""
    print("\n[步骤25] 历史回测...")

    history = []
    for f in sorted(os.listdir(DATA_DIR)):
        if f.startswith("推荐历史_") and f.endswith(".json"):
            records = _safe_read_json(os.path.join(DATA_DIR, f))
            for r in records:
                if r.get('type') == 'recommendation':
                    history.append(r)

    if not history:
        print("  无推荐历史记录，跳过回测")
        return {'all_trades': [], 'metrics': {}, 'strategy_metrics': {}, 'industry_metrics': {}}

    today = datetime.now()
    cutoff = today - timedelta(days=max_days_lookback)
    history = [h for h in history
               if h.get('prediction_date') and h['prediction_date'] >= cutoff.strftime('%Y-%m-%d')
               and h['prediction_date'] < today.strftime('%Y-%m-%d')]

    seen = set()
    unique_history = []
    for h in history:
        key = (h.get('code'), h.get('prediction_date'))
        if key not in seen:
            seen.add(key)
            unique_history.append(h)
    history = unique_history

    print(f"  推荐历史: {len(history)} 条")

    code_kline_cache = {}
    codes_to_fetch = set(h.get('code', '') for h in history)
    print(f"  获取后续K线: {len(codes_to_fetch)} 只股票...")

    for code in codes_to_fetch:
        if not code:
            continue
        related = [h for h in history if h.get('code') == code]
        latest_pred = max(h.get('prediction_date', '') for h in related)
        if not latest_pred:
            continue
        klines = _fetch_kline_range(code, latest_pred, lmt=hold_days + 5)
        if klines:
            code_kline_cache[code] = {k['date']: k for k in klines}
        time.sleep(0.02)

    print(f"  K线获取: {len(code_kline_cache)} 只有效")

    trades = []
    for h in history:
        code = h.get('code', '')
        strategy = h.get('strategy', '?')
        entry = h.get('entry', 0)
        pred_date = h.get('prediction_date', '')
        if not code or not entry or not pred_date:
            continue

        sl = round(entry * _STRATEGY_STOP_LOSS.get(strategy, 0.96), 2)
        tp = round(entry * _STRATEGY_TAKE_PROFIT.get(strategy, 1.05), 2)

        klines = code_kline_cache.get(code, {})
        post_klines = [k for d, k in sorted(klines.items()) if d > pred_date]
        trade = _simulate_trade(entry, sl, tp, post_klines, hold_days)
        trade['code'] = code
        trade['name'] = h.get('name', '')
        trade['strategy'] = strategy
        trade['industry'] = h.get('industry', '')
        trade['entry'] = entry
        trade['stop_loss'] = sl
        trade['take_profit'] = tp
        trade['prediction_date'] = pred_date
        trade['score'] = h.get('score', 0)
        trades.append(trade)

    metrics = _compute_metrics(trades)

    strategy_trades = defaultdict(list)
    for t in trades:
        strategy_trades[t['strategy']].append(t)
    strategy_metrics = {s: _compute_metrics(ts) for s, ts in strategy_trades.items()}

    industry_trades = defaultdict(list)
    for t in trades:
        industry_trades[t['industry']].append(t)
    industry_metrics = {i: _compute_metrics(ts) for i, ts in industry_trades.items()}

    print(f"  回测结果: {metrics['total']}笔 | 胜率{metrics['win_rate']}% | "
          f"均收{metrics['avg_return']}% | 盈亏比{metrics['profit_factor']} | 夏普{metrics['sharpe']}")

    return {
        'all_trades': trades, 'metrics': metrics,
        'strategy_metrics': strategy_metrics, 'industry_metrics': industry_metrics,
    }


def generate_backtest_report(bt_result, output_path=None):
    """生成回测报告（Markdown格式）"""
    if output_path is None:
        output_path = os.path.join(DATA_DIR, '回测报告.md')

    metrics = bt_result.get('metrics', {})
    strategy_metrics = bt_result.get('strategy_metrics', {})
    industry_metrics = bt_result.get('industry_metrics', {})
    trades = bt_result.get('all_trades', [])

    if not trades:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write('# 历史回测报告\n\n暂无回测数据。\n')
        return output_path

    today_str = datetime.now().strftime('%Y-%m-%d')
    lines = [
        f"# A股短线筛选 — 历史回测报告",
        f"",
        f"- **生成日期**: {today_str}",
        f"- **回测周期**: 最近90天",
        f"- **最大持仓**: 10个交易日",
        f"",
        "## 一、综合指标",
        f"",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 总交易笔数 | {metrics['total']} |",
        f"| 胜率 | {metrics['win_rate']}% |",
        f"| 平均收益率 | {metrics['avg_return']}% |",
        f"| 平均盈利 | {metrics['avg_win']}% |",
        f"| 平均亏损 | {metrics['avg_loss']}% |",
        f"| 盈亏比 | {metrics['profit_factor']} |",
        f"| 最大回撤 | {metrics['max_drawdown']}% |",
        f"| 夏普比率 | {metrics['sharpe']} |",
        f"| 平均持仓天数 | {metrics['avg_hold_days']}天 |",
        f"| 无数据笔数 | {metrics['no_data']} |",
        f"",
        "## 二、策略维度",
        f"",
        "| 策略 | 笔数 | 胜率 | 均收 | 盈亏比 | 夏普 |",
        "|------|------|------|------|--------|------|",
    ]
    for s in sorted(strategy_metrics.keys()):
        sm = strategy_metrics[s]
        sname = _STRATEGY_NAMES.get(s, s)
        lines.append(f"| {s} {sname} | {sm['total']} | {sm['win_rate']}% | {sm['avg_return']}% | {sm['profit_factor']} | {sm['sharpe']} |")

    lines.extend([
        "", "## 三、行业维度", "",
        "| 行业 | 笔数 | 胜率 | 均收 | 盈亏比 |",
        "|------|------|------|------|--------|",
    ])
    for ind in sorted(industry_metrics.keys(), key=lambda x: -industry_metrics[x]['total']):
        im = industry_metrics[ind]
        lines.append(f"| {ind} | {im['total']} | {im['win_rate']}% | {im['avg_return']}% | {im['profit_factor']} |")

    lines.extend([
        "", "## 四、最近交易明细", "",
        "| 日期 | 标的 | 代码 | 策略 | 行业 | 进场 | 结果 | 出场 | 收益 | 持仓 |",
        "|------|------|------|------|------|------|------|------|------|------|",
    ])
    recent = sorted(trades, key=lambda x: x.get('prediction_date', ''), reverse=True)[:20]
    for t in recent:
        res_emoji = '🟢' if t['result'] == 'win' else ('🔴' if t['result'] == 'loss' else '⚪')
        lines.append(
            f"| {t['prediction_date']} | {t['name']} | {t['code']} | {t['strategy']} | "
            f"{t['industry']} | {t['entry']:.2f} | {res_emoji}{t['result']} | "
            f"{t['exit_price']:.2f} | {t['return_pct']:+.2f}% | {t['hold_days']}天 |"
        )

    lines.extend([
        "",
        f"> ⚠️ 免责声明：回测结果不代表未来表现，仅供参考。",
        f"> 版本: v6.12.11 | 生成: {today_str}",
    ])

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"  回测报告: {output_path}")
    return output_path
