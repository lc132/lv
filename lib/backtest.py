# ============================================================
# A股短线筛选 — 历史回测模块 v6.13.29
# 读取推荐历史，获取后续K线，模拟止盈止损，计算回测指标
# 新增: HTML报告生成、飞书推送、回测标记查找
# v6.13.29: no_entry改为开盘价追入——限价单未成交时以次日开盘价追入，止损止盈按比例调整，19笔追入中15笔盈利
# v6.13.24: _try_tencent增加Referer头(修复无数据) + 解析过滤非列表元素 + 超时10s + max_drawdown改用复合收益率
# v6.13.23: _fetch_kline_range 增加重试(2次)、三级兜底(宽泛日期)、run_backtest 增加跨日期K线复用
# ============================================================

import urllib.request
import urllib.error
import json
import ssl
import time
import os
from collections import defaultdict, Counter
from datetime import datetime, timedelta

# v6.12.24: 独立SSL上下文，解除对主脚本全局opener的依赖
_BT_SSL_CTX = ssl._create_unverified_context()

# 策略止损/止盈比例（与主脚本 _STRATEGY_STOP_LOSS / _STRATEGY_TAKE_PROFIT 一致）v6.13.10: 同步主脚本
_STRATEGY_STOP_LOSS = {
    'A': 0.95, 'B': 0.93, 'C': 0.95, 'D': 0.95, 'E': 0.965,
    'F': 0.965, 'G': 0.95, 'H': 0.94, 'I': 0.95, 'J': 0.94,
    'K': 0.955, 'L': 0.94, 'M': 0.945, 'N': 0.95, 'O': 0.95,
    'P': 0.945, 'Q': 0.95, 'R': 0.95, 'S': 0.95, 'T': 0.94,
}
_STRATEGY_TAKE_PROFIT = {
    'A': 1.05, 'B': 1.07, 'C': 1.05, 'D': 1.05, 'E': 1.04,
    'F': 1.04, 'G': 1.05, 'H': 1.06, 'I': 1.05, 'J': 1.06,
    'K': 1.05, 'L': 1.06, 'M': 1.05, 'N': 1.05, 'O': 1.05,
    'P': 1.05, 'Q': 1.05, 'R': 1.05, 'S': 1.04, 'T': 1.04,
}
_STRATEGY_NAMES = {
    'A': '动量延续', 'B': '超跌反弹', 'C': '事件驱动', 'D': '回调企稳',
    'E': '资金埋伏', 'F': '北向资金', 'G': '横盘突破', 'H': '地量见底',
    'I': '均线突破', 'J': '龙回头', 'K': '缺口回补', 'L': '黄金坑',
    'M': '涨停回调', 'N': '新高突破', 'O': '回踩均线', 'P': '地量反弹',
    'Q': 'W底突破', 'R': '主力共振(强)', 'S': '主力共振(弱)', 'T': '主力观察',
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
    """v6.13.23: 获取指定日期之后N根日K线（腾讯HTTP重试 → iTick降级 → 宽泛兜底）
    沙箱内东方财富API被阻断，切换为腾讯HTTP作为一级数据源"""

    def _parse_tencent_days(data, mc, code):
        """解析腾讯HTTP返回的日K线数据，过滤非列表元素（如分红信息字典）"""
        days = (data.get('data', {}).get(f'{mc}{code}', {}).get('qfqday', None) or
                data.get('data', {}).get(f'{mc}{code}', {}).get('day', []))
        if not days:
            return None
        result = []
        for d in days:
            if isinstance(d, list) and len(d) >= 6:
                result.append({
                    'date': d[0], 'open': float(d[1]),
                    'close': float(d[2]), 'high': float(d[3]),
                    'low': float(d[4]), 'volume': float(d[5]),
                })
        return result if result else None

    def _try_tencent(req_lmt):
        """尝试腾讯HTTP请求，返回K线列表或None"""
        mc = 'sh' if code.startswith('6') else 'sz'
        url = (f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'
               f'param={mc}{code},day,,,{req_lmt},qfq')
        # v6.13.24: 增加Referer头，修复无数据问题（腾讯API检查Referer）
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://gu.qq.com/'})
        with urllib.request.urlopen(req, timeout=10, context=_BT_SSL_CTX) as resp:
            data = json.loads(resp.read().decode())
        return _parse_tencent_days(data, mc, code)

    # v6.13.23: 一级数据源 — 腾讯HTTP，最多2次重试
    for attempt in range(2):
        try:
            raw = _try_tencent(lmt + 15)
            if raw:
                result = [r for r in raw if r['date'] >= start_date]
                if not result:
                    # 回退：取最新K线作为参考
                    result = raw[-1:]
                if result:
                    return result
            # days为空：等1秒后重试
            if attempt == 0:
                time.sleep(1)
        except Exception as e:
            if os.environ.get('LV_DEBUG'):
                print(f"  [回测K线] 腾讯HTTP失败 {code} (attempt {attempt+1}): {str(e)[:60]}")
            if attempt == 0:
                time.sleep(1)

    # 二级降级: iTick API
    itick_key = os.environ.get("ITICK_API_KEY", "")
    if itick_key:
        try:
            region = 'SH' if code.startswith('6') else 'SZ'
            url = f'https://api.itick.org/stock/kline?region={region}&code={code}&kType=8&limit={lmt + 10}'
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0',
                'accept': 'application/json',
                'token': itick_key})
            with urllib.request.urlopen(req, timeout=10, context=_BT_SSL_CTX) as resp:
                data = json.loads(resp.read().decode())
            bars = data.get('data', [])
            if bars:
                bars_all = sorted([{
                    'date': datetime.fromtimestamp(b['t'] / 1000).strftime('%Y-%m-%d'),
                    'open': b['o'], 'close': b['c'], 'high': b['h'],
                    'low': b['l'], 'volume': b['v'],
                } for b in bars], key=lambda x: x['date'])
                result = [r for r in bars_all if r['date'] >= start_date]
                if result:
                    return result
                # v6.13.23: iTick有数据但全在start_date之前，取最新一根
                if bars_all:
                    return bars_all[-1:]
        except Exception as e:
            if os.environ.get('LV_DEBUG'):
                print(f"  [回测K线] iTick失败 {code}: {str(e)[:60]}")

    # v6.13.23: 三级兜底 — 更宽日期范围请求腾讯HTTP（lmt=30），不限制start_date
    try:
        raw = _try_tencent(30)
        if raw:
            raw.sort(key=lambda x: x['date'])
            return raw[-min(len(raw), lmt):]
    except Exception as e:
        if os.environ.get('LV_DEBUG'):
            print(f"  [回测K线] 腾讯HTTP兜底失败 {code}: {str(e)[:60]}")

    return []


def _simulate_trade(entry, stop_loss, take_profit, klines, hold_days=10):
    """模拟单笔交易：盘中触及止损/止盈则出场，否则持有到期
    v6.13.14: 新增移动止损(盈利达TP50%时保本) + 时间止损(持仓3天仍亏损则离场)
    v6.13.29: no_entry时以次日开盘价追入（模拟挂单未成交→市价追入），止损止盈按比例调整"""
    if not klines:
        return {'result': 'no_data', 'exit_price': entry, 'exit_date': '',
                'exit_reason': 'no_data', 'return_pct': 0, 'hold_days': 0,
                'max_drawdown_pct': 0, 'max_profit_pct': 0}

    max_drawdown = 0.0
    max_profit = 0.0
    kl = klines[:hold_days]

    # v6.13.29: 限价单未成交 → 以次日开盘价追入，止损止盈按比例调整
    no_entry_filled = False
    if kl[0]['low'] > entry:
        no_entry_filled = True
        orig_entry = entry
        orig_sl = stop_loss
        orig_tp = take_profit
        entry = kl[0]['open']
        sl_ratio = orig_sl / orig_entry
        tp_ratio = orig_tp / orig_entry
        stop_loss = round(entry * sl_ratio, 2)
        take_profit = round(entry * tp_ratio, 2)

    # v6.13.14: 移动止损 — 盈利达止盈目标50%时，将止损上移至保本价
    trailing_active = False
    trailing_stop = entry  # 保本价

    for i, k in enumerate(kl):
        high_pct = (k['high'] - entry) / entry * 100
        low_pct = (k['low'] - entry) / entry * 100
        max_profit = max(max_profit, high_pct)
        max_drawdown = min(max_drawdown, low_pct)

        # v6.12.12: A股T+1规则 — 当日买入不可卖出，i=0跳过止盈止损检查
        if i == 0:
            continue

        # v6.13.14: 移动止损激活 — 当日最高价达到止盈目标的50%
        if not trailing_active:
            tp_mid = (entry + take_profit) / 2
            if k['high'] >= tp_mid:
                trailing_active = True

        # v6.13.14: 时间止损 — 持仓第3天收盘仍亏损则离场
        if i >= 3:
            if k['close'] < entry:
                return {
                    'result': 'loss', 'exit_price': round(k['close'], 2),
                    'exit_date': k['date'], 'exit_reason': 'time_stop',
                    'return_pct': round((k['close'] - entry) / entry * 100, 2),
                    'hold_days': i + 1,
                    'max_drawdown_pct': round(max_drawdown, 2),
                    'max_profit_pct': round(max_profit, 2),
                    'no_entry_filled': no_entry_filled,
                }

        # 出场优先级: 止盈 > 移动止损 > 固定止损
        if k['high'] >= take_profit:
            return {
                'result': 'win', 'exit_price': take_profit,
                'exit_date': k['date'], 'exit_reason': 'take_profit',
                'return_pct': round((take_profit - entry) / entry * 100, 2),
                'hold_days': i + 1,
                'max_drawdown_pct': round(max_drawdown, 2),
                'max_profit_pct': round(max_profit, 2),
                'no_entry_filled': no_entry_filled,
            }

        # 移动止损: 激活后若跌破保本价则离场
        if trailing_active and k['low'] <= trailing_stop:
            return {
                'result': 'win', 'exit_price': trailing_stop,
                'exit_date': k['date'], 'exit_reason': 'trailing_stop',
                'return_pct': 0.0, 'hold_days': i + 1,
                'max_drawdown_pct': round(max_drawdown, 2),
                'max_profit_pct': round(max_profit, 2),
                'no_entry_filled': no_entry_filled,
            }

        if k['low'] <= stop_loss:
            return {
                'result': 'loss', 'exit_price': stop_loss,
                'exit_date': k['date'], 'exit_reason': 'stop_loss',
                'return_pct': round((stop_loss - entry) / entry * 100, 2),
                'hold_days': i + 1,
                'max_drawdown_pct': round(max_drawdown, 2),
                'max_profit_pct': round(max_profit, 2),
                'no_entry_filled': no_entry_filled,
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
        'no_entry_filled': no_entry_filled,
    }


def _compute_metrics(trades):
    """计算回测指标
    v6.13.29: no_entry已改为开盘价追入，不再有no_entry结果；仅no_data不计入有效样本"""
    if not trades:
        return {'total': 0, 'win_rate': 0, 'avg_return': 0,
                'max_drawdown': 0, 'profit_factor': 0, 'sharpe': 0}

    total = len(trades)
    wins = [t for t in trades if t['result'] == 'win']
    losses = [t for t in trades if t['result'] == 'loss']
    # v6.13.29: no_entry已改为开盘价追入，仅no_data不计入有效样本
    no_data = [t for t in trades if t['result'] == 'no_data']
    # v6.13.29: 统计开盘价追入的交易
    no_entry_filled = [t for t in trades if t.get('no_entry_filled')]
    valid_count = total - len(no_data)

    win_rate = len(wins) / max(valid_count, 1) * 100 if valid_count > 0 else 0
    # v6.13.29: 仅排除no_data，no_entry已改为开盘价追入计入有效样本
    avg_return = sum(t['return_pct'] for t in trades if t['result'] != 'no_data') / max(valid_count, 1) if valid_count > 0 else 0
    avg_win = sum(t['return_pct'] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['return_pct'] for t in losses) / len(losses) if losses else 0
    avg_hold = sum(t['hold_days'] for t in trades if t['hold_days'] > 0) / max(valid_count, 1)

    # v6.13.20: 盈亏比改为总额比（总盈利/总亏损绝对值），排除no_data
    total_win_amt = sum(t['return_pct'] for t in wins) if wins else 0
    total_loss_amt = abs(sum(t['return_pct'] for t in losses)) if losses else 0
    profit_factor = round(total_win_amt / total_loss_amt, 2) if total_loss_amt > 0 else 0

    # v6.13.24: 最大回撤改用复合收益率计算（而非线性求和），更准确反映风险
    max_dd = 0.0; cum_val = 1.0; peak_val = 1.0
    for t in trades:
        if t['result'] == 'no_data':
            continue
        cum_val *= (1 + t['return_pct'] / 100.0)
        peak_val = max(peak_val, cum_val)
        dd = (peak_val - cum_val) / peak_val * 100.0
        max_dd = max(max_dd, dd)

    # v6.13.29: 夏普计算仅排除no_data
    returns = [t['return_pct'] for t in trades if t['result'] != 'no_data']
    if len(returns) > 1:
        avg_r = sum(returns) / len(returns)
        variance = sum((r - avg_r) ** 2 for r in returns) / (len(returns) - 1)  # v6.13.10: 样本方差N-1
        std = variance ** 0.5
        sharpe = avg_r / std if std > 0 else 0
    else:
        sharpe = 0

    return {
        'total': total, 'wins': len(wins), 'losses': len(losses),
        'no_data': len(no_data), 'no_entry_filled': len(no_entry_filled),
        'win_rate': round(win_rate, 1),
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

    today = datetime.now() + timedelta(hours=8)  # v6.13.10: 北京时间（与主脚本一致）
    cutoff = today - timedelta(days=max_days_lookback)
    # v6.13.28: 预测日=买入日(盘前预测当日买入)，排除当天预测(尚无收盘K线，显示无意义)
    history = [h for h in history
               if h.get('prediction_date') and h['prediction_date'] >= cutoff.strftime('%Y-%m-%d')
               and h['prediction_date'] < today.strftime('%Y-%m-%d')]

    # v6.13.10: 去重key改为(code,date,strategy,entry)，保留同股票不同策略的推荐
    seen = set()
    unique_history = []
    for h in history:
        key = (h.get('code'), h.get('prediction_date'), h.get('strategy'), round(h.get('entry', 0), 2))
        if key not in seen:
            seen.add(key)
            unique_history.append(h)
    history = unique_history

    print(f"  推荐历史: {len(history)} 条")

    code_kline_cache = {}
    # v6.13.23: 按code聚合所有pred_date，先尝试精确获取，失败后启用跨日期复用
    codes_to_fetch = set((h.get('code', ''), h.get('prediction_date', '')) for h in history)
    print(f"  获取后续K线: {len(codes_to_fetch)} 个(代码,日期)组合...")

    # v6.13.23: 同code的K线缓存（按日期），用于跨日期复用兜底
    code_all_klines = {}

    for code, pred_date in codes_to_fetch:
        if not code or not pred_date:
            continue
        cache_key = (code, pred_date)
        if cache_key in code_kline_cache:
            continue
        klines = _fetch_kline_range(code, pred_date, lmt=hold_days + 5)
        if klines:
            code_kline_cache[cache_key] = {k['date']: k for k in klines}
            # v6.13.23: 聚合到code_all_klines用于跨日期复用
            if code not in code_all_klines:
                code_all_klines[code] = {}
            code_all_klines[code].update({k['date']: k for k in klines})
        time.sleep(0.02)

    # v6.13.23: 对于获取失败的(code, pred_date)，尝试从同code其他日期缓存中复用
    missing_count = 0
    reused_count = 0
    for code, pred_date in codes_to_fetch:
        cache_key = (code, pred_date)
        if cache_key in code_kline_cache:
            continue
        if code in code_all_klines and code_all_klines[code]:
            # 找到pred_date之后最近的K线
            all_dates = sorted(code_all_klines[code].keys())
            post_dates = [d for d in all_dates if d >= pred_date]
            if post_dates:
                code_kline_cache[cache_key] = {d: code_all_klines[code][d] for d in post_dates[:hold_days + 5]}
                reused_count += 1
            else:
                missing_count += 1
        else:
            missing_count += 1

    if reused_count > 0:
        print(f"  K线获取: {len(code_kline_cache)} 只有效 (含{reused_count}只跨日期复用)")

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

        klines = code_kline_cache.get((code, pred_date), {})
        post_klines = [k for d, k in sorted(klines.items()) if d >= pred_date]
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
          f"均收{metrics['avg_return']}% | 盈亏比{metrics['profit_factor']} | 夏普{metrics['sharpe']}"
          f" | 追入{metrics.get('no_entry_filled', 0)}笔")

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
            f.write('# 历史回测报告\n\n暂无回测数据。\n\n## 回测说明\n\n- 回测使用最近90天推荐历史。\n- 单笔最大持仓10个交易日。\n- 按推荐时的进场、止损、止盈价格进行模拟。\n- 遵循A股T+1规则，买入当日不检查止盈止损出场。\n- 出场优先级：止盈 > 移动止损(保本) > 固定止损。移动止损盈利达TP50%激活。\n- 持仓3天收盘仍亏损按时间止损离场。\n- 回测未计入滑点、手续费、涨跌停无法成交、真实排队成交等因素，仅供参考。\n')
        return output_path

    today_str = (datetime.now() + timedelta(hours=8)).strftime('%Y-%m-%d')  # v6.13.10: 北京时间
    lines = [
        f"# A股短线筛选 — 历史回测报告",
        f"",
        f"- **生成日期**: {today_str}",
        f"- **回测周期**: 最近90天",
        f"- **最大持仓**: 10个交易日",
        f"",
        "## 回测说明",
        f"",
        "- **样本来源**：最近90天推荐历史，按当时推荐标的、策略、进场价、止损价、止盈价回放后续K线。",
        "- **出场规则**：单笔最大持仓10个交易日；出场优先级为 止盈 > 移动止损(保本) > 固定止损。移动止损在盈利达止盈目标50%时激活，将止损上移至保本价；持仓第3天收盘仍亏损则按时间止损离场。",
        "- **T+1处理**：遵循A股T+1规则，买入当日不检查止盈止损出场，从下一交易日起判断。",
        "- **结果含义**：`win`为盈利样本，`loss`为亏损样本，`no_data`为后续K线不足或无法形成有效模拟。",
        "- **指标说明**：胜率为盈利样本占有效样本比例；盈亏比为总盈利绝对值/总亏损绝对值；夏普为单笔收益均值相对波动的简化指标。",
        "- **局限性**：未计入滑点、手续费、涨跌停无法成交、真实排队成交、资金容量和盘中流动性冲击，回测结果不代表未来表现。",
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
        res_emoji = '\U0001f7e2' if t['result'] == 'win' else ('\U0001f534' if t['result'] == 'loss' else '\u26aa')
        lines.append(
            f"| {t['prediction_date']} | {t['name']} | {t['code']} | {t['strategy']} | "
            f"{t['industry']} | {t['entry']:.2f} | {res_emoji}{t['result']} | "
            f"{t['exit_price']:.2f} | {t['return_pct']:+.2f}% | {t['hold_days']}天 |"
        )

    lines.extend([
        "",
        f"> \u26a0\ufe0f 免责声明：回测结果不代表未来表现，仅供参考。",
        f"> 版本: v6.13.24 | 生成: {today_str}",
    ])

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"  回测报告: {output_path}")
    return output_path


# ============================================================
# v6.12.13: 回测标记查找、HTML报告、飞书推送
# ============================================================

def _build_backtest_lookup(bt_result):
    """构建 代码→历史回测汇总 的查找字典，供筛选结果表格标记回测结果
    v6.13.29: no_entry_filled交易计入有效样本"""
    trades = bt_result.get('all_trades', [])
    if not trades:
        return {}
    code_trades = defaultdict(list)
    for t in trades:
        code_trades[t['code']].append(t)
    lookup = {}
    for code, ts in code_trades.items():
        total = len(ts)
        wins = sum(1 for t in ts if t['result'] == 'win')
        losses = sum(1 for t in ts if t['result'] == 'loss')
        no_data = sum(1 for t in ts if t['result'] == 'no_data')
        avg_ret = sum(t['return_pct'] for t in ts) / total if total > 0 else 0
        # v6.13.29: no_entry_filled也是有效交易
        valid = [t for t in ts if t['result'] != 'no_data']
        last = valid[-1] if valid else ts[-1]
        no_entry_count = sum(1 for t in ts if t.get('no_entry_filled'))
        lookup[code] = {
            'total': total, 'wins': wins, 'losses': losses, 'no_data': no_data,
            'no_entry': no_entry_count,
            'avg_return': round(avg_ret, 2),
            'last_result': last['result'], 'last_return': last['return_pct'],
            'last_date': last.get('prediction_date', ''),
        }
    return lookup


def generate_backtest_html(bt_result, output_path=None):
    """生成自包含HTML回测报告（含图表可视化）"""
    if output_path is None:
        output_path = os.path.join(DATA_DIR, '回测报告.html')

    metrics = bt_result.get('metrics', {})
    strategy_metrics = bt_result.get('strategy_metrics', {})
    industry_metrics = bt_result.get('industry_metrics', {})
    trades = bt_result.get('all_trades', [])
    today_str = (datetime.now() + timedelta(hours=8)).strftime('%Y-%m-%d')  # v6.13.10: 北京时间

    if not trades:
        html = f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>历史回测报告</title>
<style>body{{font-family:"Noto Sans CJK SC","WenQuanYi Micro Hei",sans-serif;max-width:900px;margin:40px auto;padding:20px;background:#f8fafc;color:#1e293b}}h1{{color:#2563eb}}</style></head>
<body><h1>历史回测报告</h1><p>暂无回测数据。</p><h2>回测说明</h2><ul><li>回测使用最近90天推荐历史。</li><li>单笔最大持仓10个交易日。</li><li>按推荐时的进场、止损、止盈价格进行模拟。</li><li>遵循A股T+1规则，买入当日不检查止盈止损出场。</li><li>回测未计入滑点、手续费、涨跌停无法成交、真实排队成交等因素，仅供参考。</li><li>v6.13.14新增：移动止损(盈利达TP50%保本)、时间止损(持仓3天仍亏损离场)。</li></ul><p style="color:#94a3b8">版本: v6.13.24 | 生成: {today_str}</p></body></html>'''
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        return output_path

    # 综合指标卡片
    card_items = [
        ('总交易', f"{metrics['total']}笔"),
        ('胜率', f"{metrics['win_rate']}%"),
        ('平均收益', f"{metrics['avg_return']}%"),
        ('盈亏比', f"{metrics['profit_factor']}"),
        ('夏普', f"{metrics['sharpe']}"),
        ('最大回撤', f"{metrics['max_drawdown']}%"),
        ('平均盈利', f"{metrics['avg_win']}%"),
        ('平均亏损', f"{metrics['avg_loss']}%"),
        ('平均持仓', f"{metrics['avg_hold_days']}天"),
    ]
    cards_html = ''
    for label, value in card_items:
        cards_html += f'<div class="metric-card"><div class="metric-label">{label}</div><div class="metric-value">{value}</div></div>'

    # 策略维度表
    strategy_rows = ''
    for s in sorted(strategy_metrics.keys()):
        sm = strategy_metrics[s]
        sname = _STRATEGY_NAMES.get(s, s)
        wr = sm['win_rate']
        wr_cls = 'win' if wr >= 50 else 'loss'
        strategy_rows += f'''<tr><td><span class="badge">{s}</span> {sname}</td>
        <td>{sm['total']}</td><td class="{wr_cls}">{wr}%</td>
        <td>{sm['avg_return']}%</td><td>{sm['profit_factor']}</td><td>{sm['sharpe']}</td></tr>'''

    # 行业维度表
    industry_rows = ''
    for ind in sorted(industry_metrics.keys(), key=lambda x: -industry_metrics[x]['total']):
        im = industry_metrics[ind]
        wr = im['win_rate']
        wr_cls = 'win' if wr >= 50 else 'loss'
        industry_rows += f'''<tr><td>{ind}</td><td>{im['total']}</td>
        <td class="{wr_cls}">{wr}%</td><td>{im['avg_return']}%</td><td>{im['profit_factor']}</td></tr>'''

    # 交易明细表
    trade_rows = ''
    recent = sorted(trades, key=lambda x: x.get('prediction_date', ''), reverse=True)[:30]
    for t in recent:
        res_cls = 'win' if t['result'] == 'win' else ('loss' if t['result'] == 'loss' else 'nodata')
        res_label = '\u76c8\u5229' if t['result'] == 'win' else ('\u4e8f\u635f' if t['result'] == 'loss' else '\u65e0\u6570\u636e')
        ret_sign = '+' if t['return_pct'] >= 0 else ''
        trade_rows += f'''<tr><td>{t['prediction_date']}</td><td>{t['name']}</td><td>{t['code']}</td>
        <td>{t['strategy']}</td><td>{t['industry']}</td><td>{t['entry']:.2f}</td>
        <td class="{res_cls}">{res_label}</td><td>{t['exit_price']:.2f}</td>
        <td class="{res_cls}">{ret_sign}{t['return_pct']:.2f}%</td><td>{t['hold_days']}\u5929</td></tr>'''

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>A\u80a1\u77ed\u7ebf\u7b5b\u9009 \u2014 \u5386\u53f2\u56de\u6d4b\u62a5\u544a</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:"Noto Sans CJK SC","WenQuanYi Micro Hei",sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}}
.container{{max-width:1100px;margin:0 auto;padding:30px 20px}}
.header{{text-align:center;padding:40px 0 30px}}
.header h1{{font-size:28px;color:#38bdf8;margin-bottom:8px}}
.header .meta{{color:#94a3b8;font-size:14px}}
.metrics-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:30px}}
.metric-card{{background:#1e293b;border-radius:10px;padding:16px;text-align:center;border:1px solid #334155}}
.metric-label{{color:#94a3b8;font-size:12px;margin-bottom:6px}}
.metric-value{{color:#e2e8f0;font-size:22px;font-weight:700}}
.section{{background:#1e293b;border-radius:12px;padding:24px;margin-bottom:20px;border:1px solid #334155}}
.section h2{{color:#38bdf8;font-size:18px;margin-bottom:16px;padding-bottom:8px;border-bottom:1px solid #334155}}
.note-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}}
.note-card{{background:#0f172a;border:1px solid #334155;border-radius:10px;padding:14px;color:#cbd5e1;font-size:13px;line-height:1.65}}
.note-card b{{color:#38bdf8}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{background:#0f172a;color:#94a3b8;padding:10px 8px;text-align:left;font-weight:600;white-space:nowrap}}
td{{padding:8px;border-bottom:1px solid #1e293b}}
tr:hover td{{background:rgba(56,189,248,0.05)}}
.badge{{display:inline-block;background:#334155;color:#38bdf8;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600}}
.win{{color:#22c55e}}
.loss{{color:#ef4444}}
.nodata{{color:#94a3b8}}
.footer{{text-align:center;color:#64748b;font-size:12px;padding:20px;margin-top:20px}}
@media(max-width:600px){{.metrics-grid{{grid-template-columns:repeat(3,1fr)}}table{{font-size:11px}}}}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>\U0001f4ca A\u80a1\u77ed\u7ebf\u7b5b\u9009 \u2014 \u5386\u53f2\u56de\u6d4b\u62a5\u544a</h1>
<p class="meta">\u751f\u6210\u65e5\u671f: {today_str} | \u56de\u6d4b\u5468\u671f: \u6700\u8fd190\u5929 | \u6700\u5927\u6301\u4ed3: 10\u4e2a\u4ea4\u6613\u65e5</p>
</div>

<div class="metrics-grid">{cards_html}</div>

<div class="section">
<h2>回测说明</h2>
<div class="note-grid">
<div class="note-card"><b>样本来源</b><br>最近90天推荐历史，按当时推荐标的、策略、进场价、止损价、止盈价回放后续K线。</div>
<div class="note-card"><b>出场规则</b><br>单笔最大持仓10个交易日；若盘中触及止损或止盈，按对应价格出场；若到期未触发，按持仓期末收盘价计算。</div>
<div class="note-card"><b>T+1处理</b><br>遵循A股T+1规则，买入当日不检查止盈止损出场，从下一交易日起判断。</div>
<div class="note-card"><b>结果含义</b><br>win为盈利样本，loss为亏损样本，no_data为后续K线不足或无法形成有效模拟。</div>
<div class="note-card"><b>指标说明</b><br>胜率为盈利样本占有效样本比例；盈亏比为总盈利绝对值/总亏损绝对值；夏普为单笔收益均值相对波动的简化指标。</div>
<div class="note-card"><b>局限性</b><br>未计入滑点、手续费、涨跌停无法成交、真实排队成交、资金容量和盘中流动性冲击。</div>
</div>
</div>

<div class="section">
<h2>\u7b56\u7565\u7ef4\u5ea6</h2>
<table><thead><tr><th>\u7b56\u7565</th><th>\u7b14\u6570</th><th>\u80dc\u7387</th><th>\u5747\u6536</th><th>\u76c8\u4e8f\u6bd4</th><th>\u590f\u666e</th></tr></thead>
<tbody>{strategy_rows}</tbody></table>
</div>

<div class="section">
<h2>\u884c\u4e1a\u7ef4\u5ea6</h2>
<table><thead><tr><th>\u884c\u4e1a</th><th>\u7b14\u6570</th><th>\u80dc\u7387</th><th>\u5747\u6536</th><th>\u76c8\u4e8f\u6bd4</th></tr></thead>
<tbody>{industry_rows}</tbody></table>
</div>

<div class="section">
<h2>\u6700\u8fd1\u4ea4\u6613\u660e\u7ec6</h2>
<table><thead><tr><th>\u65e5\u671f</th><th>\u6807\u7684</th><th>\u4ee3\u7801</th><th>\u7b56\u7565</th><th>\u884c\u4e1a</th><th>\u8fdb\u573a</th><th>\u7ed3\u679c</th><th>\u51fa\u573a</th><th>\u6536\u76ca</th><th>\u6301\u4ed3</th></tr></thead>
<tbody>{trade_rows}</tbody></table>
</div>

<div class="footer">
<p>\u26a0\ufe0f \u514d\u8d23\u58f0\u660e\uff1a\u56de\u6d4b\u7ed3\u679c\u4e0d\u4ee3\u8868\u672a\u6765\u8868\u73b0\uff0c\u4ec5\u4f9b\u53c2\u8003\u3002</p>
<p>\u7248\u672c: v6.13.24 | \u751f\u6210: {today_str}</p>
</div>
</div>
</body>
</html>'''

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"  回测HTML: {output_path}")
    return output_path


def push_backtest_to_feishu(bt_result):
    """推送回测核心指标到飞书卡片消息"""
    webhook = os.environ.get('FEISHU_WEBHOOK', '')
    if not webhook:
        webhook_path = os.path.join(DATA_DIR, '.feishu_webhook')
        try:
            with open(webhook_path, 'r', encoding='utf-8') as f:
                webhook = f.read().strip()
        except (FileNotFoundError, PermissionError):
            pass
    if not webhook:
        print("  飞书Webhook未配置，跳过回测推送")
        return False

    try:
        metrics = bt_result.get('metrics', {})
        if not metrics or metrics.get('total', 0) == 0:
            print("  无回测数据，跳过飞书推送")
            return False
        # v6.13.13: 全部no_data时也推送概要（修复回测0笔时飞书无推送问题）

        today_str = (datetime.now() + timedelta(hours=8)).strftime('%Y-%m-%d')  # v6.13.10: 北京时间
        pb = "https://lc132.github.io/lv"
        bt_url = f"{pb}/backtest/"  # v6.13.18: GitHub Pages不支持中文文件名，统一使用backtest/目录

        # 策略TOP3（按胜率）
        strategy_metrics = bt_result.get('strategy_metrics', {})
        top_strats = sorted(strategy_metrics.items(), key=lambda x: -x[1].get('win_rate', 0))[:3]
        top_strat_lines = []
        for s, sm in top_strats:
            sname = _STRATEGY_NAMES.get(s, s)
            top_strat_lines.append(f"**{s} {sname}**: 胜率{sm['win_rate']}% | {sm['total']}笔 | 均收{sm['avg_return']}%")
        top_strat_text = '\n'.join(top_strat_lines) if top_strat_lines else '无数据'

        card = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": f"\U0001f4c8 历史回测报告 — {today_str}"},
                    "template": "blue"
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md",
                        "content": f"**回测周期**: 最近90天 | **最大持仓**: 10个交易日 | **总交易**: {metrics['total']}笔"}},
                    {"tag": "hr"},
                    {"tag": "div", "text": {"tag": "lark_md",
                        "content": f"胜率: **{metrics['win_rate']}%** | 均收: **{metrics['avg_return']}%** | 盈亏比: **{metrics['profit_factor']}** | 夏普: **{metrics['sharpe']}**"}},
                    {"tag": "hr"},
                    {"tag": "div", "text": {"tag": "lark_md",
                        "content": f"平均盈利: {metrics['avg_win']}% | 平均亏损: {metrics['avg_loss']}% | 最大回撤: {metrics['max_drawdown']}% | 平均持仓: {metrics['avg_hold_days']}天"}},
                    {"tag": "hr"},
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"**策略TOP3**:\n{top_strat_text}"}},
                    {"tag": "hr"},
                    {"tag": "div", "text": {"tag": "lark_md", "content": f"\U0001f4ca [**查看完整回测报告（HTML）**]({bt_url})"}},
                    {"tag": "note", "elements": [{"tag": "plain_text", "content": "\u26a0\ufe0f 回测结果不代表未来表现，仅供参考"}]}
                ]
            }
        }
        req = urllib.request.Request(webhook, data=json.dumps(card, ensure_ascii=False).encode('utf-8'),
                                     headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=10, context=_BT_SSL_CTX) as resp:
            result = json.loads(resp.read())
        if result.get('code') == 0:
            print(f"  回测飞书推送: \u2705")
            return True
        else:
            print(f"  回测飞书推送失败: {result.get('msg', '')}")
            return False
    except Exception as e:
        print(f"  回测飞书推送异常: {str(e)[:80]}")
        return False