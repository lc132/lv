#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v6.13.4 早盘竞价数据获取与验证模块
数据源: 东方财富竞价接口 (免费)
"""
import urllib.request, json, time

# 东方财富竞价接口
_AUCTION_URL = "https://push2.eastmoney.com/api/qt/stock/auction/get"
_AUCTION_FIELDS = "f42,f43,f44,f45,f46,f47,f48,f60,f170,f171"


def fetch_auction_single(code):
    """
    拉取单只股票竞价数据
    code: 6位股票代码
    返回: dict 或 None
    """
    if code.startswith('6'):
        secid = f"1.{code}"
    elif code.startswith(('0', '3')):
        secid = f"0.{code}"
    else:
        return None
    
    url = f"{_AUCTION_URL}?secid={secid}&fields={_AUCTION_FIELDS}"
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://quote.eastmoney.com/'
        })
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read().decode('utf-8'))
        if data.get('data'):
            d = data['data']
            return {
                'code': code,
                'price': d.get('f43', 0) / 100 if d.get('f43') else 0,
                'open': d.get('f44', 0) / 100 if d.get('f44') else 0,
                'high': d.get('f45', 0) / 100 if d.get('f45') else 0,
                'low': d.get('f46', 0) / 100 if d.get('f46') else 0,
                'volume': d.get('f47', 0),
                'amount': d.get('f48', 0),
                'change_pct': d.get('f170', 0) / 100 if d.get('f170') else 0,
            }
    except Exception as e:
        return None


def fetch_auction_batch(codes, prev_close_map=None):
    """
    批量拉取竞价数据
    codes: 股票代码列表
    prev_close_map: {code: prev_close} 昨收价映射
    返回: 竞价数据列表
    """
    results = []
    for code in codes:
        data = fetch_auction_single(code)
        if data:
            if prev_close_map and code in prev_close_map:
                pc = prev_close_map[code]
                data['prev_close'] = pc
                data['gap_pct'] = (data['price'] - pc) / pc if pc > 0 else 0
            results.append(data)
        time.sleep(0.05)  # 限流
    return results


def compute_auction_score(auction_data, kline_data, prev_close):
    """
    竞价评分（0-10分）
    量价得分(0-4) + 异动得分(0-3) + 形态得分(0-3)
    """
    score = 0
    code = auction_data.get('code', '')
    price = auction_data.get('price', 0)
    volume = auction_data.get('volume', 0)
    amount = auction_data.get('amount', 0)
    gap_pct = auction_data.get('gap_pct', 0)
    
    kd = kline_data.get(code, {})
    vols = kd.get('volumes', [])
    closes = kd.get('closes', [])
    
    # 量价得分 (0-4)
    if len(vols) >= 5 and prev_close > 0:
        avg_vol_5 = sum(vols[-5:]) / 5
        vol_ratio = volume / avg_vol_5 if avg_vol_5 > 0 else 0
        if 1.5 <= vol_ratio < 3.0:
            score += 2
        elif vol_ratio >= 3.0:
            score += 3
        elif 0.5 <= vol_ratio < 1.5:
            score += 1
        
        if 0.01 <= gap_pct <= 0.03:
            score += 1
        elif 0.03 < gap_pct <= 0.05:
            score += 0  # 高开合理但不加分
        elif -0.02 <= gap_pct < 0:
            score += 0  # 低开不加分
        elif gap_pct < -0.02:
            score -= 1  # 大幅低开扣分
    
    # 异动得分 (0-3)
    if amount >= 10_000_000:
        score += 1
    if gap_pct > 0.03 and volume > 0:
        score += 1
    if amount >= 50_000_000:
        score += 1
    
    # 形态得分 (0-3)
    if len(closes) >= 5:
        if closes[-1] > closes[-2] > closes[-3]:
            score += 1
        ma5 = kd.get('ma5', 0)
        if ma5 > 0 and price > ma5:
            score += 1
        if len(closes) >= 10:
            ma10 = kd.get('ma10', 0)
            if ma10 > 0 and price > ma10:
                score += 1
    
    return max(0, min(10, score))


def hard_filter_auction(auction_data):
    """
    竞价硬过滤
    v6.12.0: 移除未使用的 kline_data 参数
    返回: (通过, 过滤原因)
    """
    code = auction_data.get('code', '')
    gap_pct = auction_data.get('gap_pct', 0)
    amount = auction_data.get('amount', 0)
    price = auction_data.get('price', 0)
    volume = auction_data.get('volume', 0)
    
    # 高开>8% → 过滤
    if gap_pct > 0.08:
        return False, f"高开{gap_pct:.1%}>8%"
    
    # 一字板（竞价涨停+无成交）→ 过滤
    if gap_pct > 0.095 and volume < 100:
        return False, "一字板涨停"
    
    # 竞价跌停 → 过滤
    if gap_pct < -0.095:
        return False, "竞价跌停"
    
    # 竞价成交额<100万 → 过滤
    if amount < 1_000_000:
        return False, f"竞价成交额{amount/10000:.0f}万<100万"
    
    # ST/科创/北交/创业板 → 过滤
    if code.startswith('688') or code.startswith('8') or code.startswith('300') or code.startswith('301'):
        return False, "科创板/北交所/创业板"
    
    return True, None