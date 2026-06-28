#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v6.10.2 尾盘决策数据获取与筛选模块
数据源: 东方财富分时API + clist主力资金
"""
import urllib.request, json


def fetch_intraday_minute(code):
    """
    拉取当日分时数据（分钟K线）
    东方财富趋势接口
    """
    if code.startswith('6'):
        secid = f"1.{code}"
    elif code.startswith(('0', '3')):
        secid = f"0.{code}"
    else:
        return None
    
    url = f"https://push2.eastmoney.com/api/qt/stock/trends2/get?secid={secid}&fields1=f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13&fields2=f51,f52,f53,f54,f55,f56,f57,f58&ndays=1"
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://quote.eastmoney.com/'
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode('utf-8'))
        if data.get('data') and data['data'].get('trends'):
            trends = data['data']['trends']
            minutes = []
            for t in trends:
                parts = t.split(',')
                if len(parts) >= 8:
                    minutes.append({
                        'time': parts[0],
                        'open': float(parts[1]),
                        'close': float(parts[2]),
                        'high': float(parts[3]),
                        'low': float(parts[4]),
                        'volume': float(parts[5]),
                        'amount': float(parts[6]),
                    })
            return minutes
    except Exception:
        return None


def detect_lock_time(intraday_minutes, prev_close):
    """
    检测封板时间
    返回: 封板分钟数(距开盘)，未封板返回None
    v6.10.2: 修复尾盘封板被误判为未封板的bug（continuous_lock未延续到收盘）
    """
    if not intraday_minutes or not prev_close:
        return None
    
    limit_price = prev_close * 1.098  # 10%涨停价
    lock_time = None
    continuous_lock = 0
    
    for m in intraday_minutes:
        if m['high'] >= limit_price * 0.995:
            if lock_time is None:
                lock_time = m['time']
            continuous_lock += 1
        else:
            if continuous_lock >= 3:
                # 已确认封板，后续开板不重置
                pass
            else:
                lock_time = None
            continuous_lock = 0
    
    if lock_time is None:
        return None
    
    # v6.10.2: 至少连续3分钟在涨停价附近才算封板
    if continuous_lock < 3 and len(intraday_minutes) > 0:
        last_m = intraday_minutes[-1]
        if last_m['high'] < limit_price * 0.995:
            return None
    
    # 解析时间 HH:MM 转为分钟数
    try:
        h, mi = lock_time.split(':')
        return int(h) * 60 + int(mi) - 570  # 570 = 9*60+30 (开盘分钟)
    except:
        return None


def compute_distance_to_high(kline_data, code, close):
    """
    计算距60日最高价的距离
    返回: 距离百分比
    """
    kd = kline_data.get(code, {})
    highs = kd.get('highs', [])
    if len(highs) < 60:
        high20 = kd.get('high20', 0)
        return (high20 - close) / close if high20 > 0 and close > 0 else 0
    high60 = max(highs[-60:])
    return (high60 - close) / close if high60 > 0 and close > 0 else 0


def compute_overnight_score(c, kline_data, sector_limit_up_count):
    """
    尾盘隔夜标的评分（0-12分）
    v6.10.2: 移除未使用的 limit_up_map 参数
    """
    score = 0
    code = c.get('code', '')
    close = c.get('close', 0)
    main_inflow = c.get('main_inflow', 0) or 0
    
    # 封板质量 (0-3)
    lock_minute = c.get('_lock_minute')
    if lock_minute is not None:
        if lock_minute <= 30: score += 3
        elif lock_minute <= 60: score += 2
        elif lock_minute <= 90: score += 1
    
    # 空间潜力 (0-3)
    dist_to_high = compute_distance_to_high(kline_data, code, close)
    if dist_to_high > 0.15: score += 3
    elif dist_to_high > 0.10: score += 2
    elif dist_to_high > 0.05: score += 1
    
    # 板块强度 (0-3)
    if sector_limit_up_count >= 3: score += 3
    elif sector_limit_up_count >= 2: score += 2
    elif sector_limit_up_count >= 1: score += 1
    
    # 资金强度 (0-3)
    if main_inflow > 300_000_000: score += 3
    elif main_inflow > 100_000_000: score += 2
    elif main_inflow > 50_000_000: score += 1
    
    return score


def hard_filter_afternoon(c, kline_data):
    """
    尾盘硬过滤
    v6.10.2: 移除未使用的 intraday_data 参数
    - 封板时间 < 10:30
    - 距前高 > 10%
    - 板块内 ≥ 2只涨停
    - 主力净流入 > 1亿
    返回: (通过, 过滤原因)
    """
    code = c.get('code', '')
    close = c.get('close', 0)
    main_inflow = c.get('main_inflow', 0) or 0
    
    lock_minute = c.get('_lock_minute')
    if lock_minute is None or lock_minute > 60:
        return False, f"封板时间不满足({lock_minute}min>60min)"
    
    dist = compute_distance_to_high(kline_data, code, close)
    if dist < 0.10:
        return False, f"距前高不足({dist:.1%}<10%)"
    
    sector_count = c.get('_sector_limit_up_count', 0)
    if sector_count < 2:
        return False, f"板块跟风不足({sector_count}只<2只)"
    
    if main_inflow < 100_000_000:
        return False, f"主力净流入不足({main_inflow/10000:.0f}万<1亿)"
    
    return True, None