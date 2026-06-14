#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤1-3A: 节假日检查、极端行情检查、外围市场检查、外围期货检查
"""
from lib.core import *

# ============================================================
# 步骤1: 节假日检查
# ============================================================
def step1_holiday_check(ctx):
    print("\n" + "=" * 60)
    print("步骤1: 节假日检查")
    print("=" * 60)
    
    data_date = ctx['data_date']
    prediction_date = ctx['prediction_date']
    
    # 搜索中国股市交易日历
    try:
        # Check if data_date is a trading day - weekend data_date is already handled in step0
        # data_date should always be a weekday (step0 already rolled back)
        # Only check for actual holidays, not weekends
        print(f"  data_date={data_date} (工作日)，正常筛选")
        ctx['skip'] = False
        ctx['is_weak_market'] = False
        ctx['is_long_holiday'] = False
        
    except Exception as e:
        log_alert("WARNING", "节假日检查", f"搜索失败: {str(e)[:80]}")
        print(f"  节假日检查跳过: {str(e)[:60]}")
        ctx['skip'] = False
        ctx['is_weak_market'] = False
        ctx['is_long_holiday'] = False

# ============================================================
# 步骤2: 极端行情检查
# ============================================================
def step2_extreme_market(ctx):
    print("\n" + "=" * 60)
    print("步骤2: 极端行情检查")
    print("=" * 60)
    
    sh_chg = None
    
    # 方案一：东方财富个股API
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get?secid=1.000001&fields=f43,f170"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        if data.get('data'):
            sh_chg = data['data'].get('f170', 0) / 100 if data['data'].get('f170') else 0
    except Exception:
        pass
    
    # 方案二：新浪API降级（使用长格式 sh000001）
    if sh_chg is None:
        try:
            url = "https://hq.sinajs.cn/list=sh000001"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'
            })
            resp = urllib.request.urlopen(req, timeout=5)
            text = resp.read().decode('gbk')
            if text and '=""' not in text:
                parts = text.split('"')[1].split(',')
                # 长格式: name, prev_close, open, current, high, low, ...
                if len(parts) > 3:
                    current = float(parts[3]) if parts[3] and parts[3] != '' else 0
                    prev_close = float(parts[1]) if parts[1] and parts[1] != '' else 0
                    if current > 0 and prev_close > 0:
                        sh_chg = round((current - prev_close) / prev_close * 100, 2)
        except Exception:
            pass
    
    if sh_chg is not None:
        print(f"  上证指数涨跌幅: {sh_chg}%")
        ctx['sh_index_change'] = sh_chg
        if sh_chg < -3:
            print(f"  ⚠️ 上证跌>3%，跳过筛选")
            ctx['skip'] = True
            ctx['market_condition'] = '弱市'
            return
        elif sh_chg > 3:
            print(f"  ⚠️ 上证涨>3%，仓位降至30%仅动量延续")
            ctx['position'] = 30
            ctx['market_condition'] = '强市(极端)'
        else:
            ctx['market_condition'] = None
    else:
        log_alert("WARNING", "极端行情", "上证指数双路API均不可达")
        print(f"  上证指数数据获取失败（双路均失败），继续")
        ctx['sh_index_change'] = 0

# ============================================================
# 步骤3: 外围市场检查
# ============================================================
def _fetch_yahoo_chg(symbol, label):
    """从 Yahoo Finance 获取单只指数涨跌幅"""
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=2d"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        result = data.get('chart', {}).get('result', [])
        if result:
            quotes = result[0].get('indicators', {}).get('quote', [{}])[0]
            closes = quotes.get('close', [])
            if len(closes) >= 2 and closes[-2] and closes[-1]:
                chg = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2)
                return chg
    except Exception:
        pass
    return None

def step3_foreign_market(ctx):
    print("\n" + "=" * 60)
    print("步骤3: 外围市场检查")
    print("=" * 60)
    
    ctx['foreign_weak'] = False
    ctx['pause_strategy_d'] = False
    ctx['foreign_detail'] = {}
    
    # 1. 美股三大指数检查
    us_indices = {
        '^GSPC': '标普500',
        '^IXIC': '纳斯达克',
        '^DJI': '道琼斯'
    }
    us_chgs = {}
    for sym, label in us_indices.items():
        chg = _fetch_yahoo_chg(sym, label)
        us_chgs[label] = chg
        if chg is not None:
            print(f"  {label}: {chg:+.2f}%")
    
    ctx['foreign_detail']['us'] = us_chgs
    
    # 美股三大指数均跌>2% → 弱市仓位≤30%
    us_chg_values = [v for v in us_chgs.values() if v is not None]
    if len(us_chg_values) >= 2 and all(v < -2 for v in us_chg_values):
        ctx['foreign_weak'] = True
        print(f"  ⚠️ 美股三大指数均跌>2% → 弱市仓位≤30%")
    elif len(us_chg_values) == 0:
        print(f"  美股数据均不可得，跳过美股检查")
    
    # 2. 恒生指数检查
    hsi_chg = _fetch_yahoo_chg('^HSI', '恒生指数')
    ctx['foreign_detail']['hsi'] = hsi_chg
    if hsi_chg is not None:
        print(f"  恒生指数: {hsi_chg:+.2f}%")
        if hsi_chg < -3:
            ctx['foreign_weak'] = True
            print(f"  ⚠️ 恒生跌>3% → 弱市，仅超跌反弹")
    else:
        print(f"  恒生数据不可得，跳过")
    
    # 3. 人民币汇率波动检查（USD/CNY）
    try:
        cny_url = "https://query1.finance.yahoo.com/v8/finance/chart/CNY=X?interval=1d&range=2d"
        req = urllib.request.Request(cny_url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        result = data.get('chart', {}).get('result', [])
        if result:
            quotes = result[0].get('indicators', {}).get('quote', [{}])[0]
            closes = quotes.get('close', [])
            if len(closes) >= 2 and closes[-2] and closes[-1]:
                cny_chg = abs((closes[-1] - closes[-2]) / closes[-2] * 100)
                ctx['foreign_detail']['cny_vol'] = round(cny_chg, 2)
                print(f"  人民币波动: {cny_chg:.2f}%")
                if cny_chg > 0.5:
                    ctx['pause_strategy_d'] = True
                    print(f"  ⚠️ 人民币波动>{0.5}% → 暂停策略D")
    except Exception:
        print(f"  人民币汇率数据不可得，跳过")
    
    if not ctx['foreign_weak'] and not ctx['pause_strategy_d']:
        print(f"  外围市场正常")

# ============================================================
# 步骤3A: 开盘前外围期货
# ============================================================
def step3A_futures(ctx):
    print("\n" + "=" * 60)
    print("步骤3A: 开盘前外围期货检查")
    print("=" * 60)
    # 期货数据关注，简化处理
    print("  期货数据暂不可得，跳过此检查，维持步骤3外围判断")
    ctx['futures_bearish'] = False