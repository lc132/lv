#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤1-3A: 节假日检查、极端行情检查、外围市场检查、外围期货检查
v6.6.22: 东方财富API全系不可达→全面迁移至新浪API
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
    beijing_weekday = ctx['beijing_weekday']
    
    # 周末已在 step0 处理（data_date 已回滚到最近工作日），此处检查法定节假日
    ctx['skip'] = False
    ctx['is_weak_market'] = False
    ctx['is_long_holiday'] = False
    
    try:
        # 新浪上证日K线替代东方财富K线（push2不可达）
        url = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
               "CN_MarketData.getKLineData?symbol=sh000001&scale=240&ma=no&datalen=5")
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.sina.com.cn'
        })
        resp = urllib.request.urlopen(req, timeout=5)
        klines = json.loads(resp.read().decode('gbk'))
        latest_date = klines[-1].get('day', '') if klines and len(klines) > 0 else ''
        
        if latest_date != data_date:
            print(f"  ⚠️ {data_date} 最新K线日期={latest_date}，判定为节假日→跳过筛选")
            ctx['skip'] = True
            log_alert("INFO", "节假日检查", f"{data_date}无K线数据(最新={latest_date})，节假日跳过")
            
            # 检查是否为长休（≥3天无交易）
            try:
                dt = datetime.strptime(data_date, '%Y-%m-%d')
                latest_dt = datetime.strptime(latest_date, '%Y-%m-%d')
                gap_days = (dt - latest_dt).days
                if gap_days >= 3:
                    ctx['is_long_holiday'] = True
                    ctx['is_weak_market'] = True
                    params = ctx.get('params', {})
                    params['search_budget'] = params.get('search_budget', 25) + 5
                    ctx['params'] = params
                    print(f"  ⚠️ 长休{gap_days}日→弱市+仓位≤30%+搜索预算+5({params['search_budget']})")
            except:
                pass
        else:
            print(f"  data_date={data_date} K线确认: 交易日，正常筛选")
            
    except Exception as e:
        # API不可达→降级：周末判断已在step0完成，非周末假定为交易日
        log_alert("WARNING", "节假日检查", f"API不可达: {str(e)[:60]}")
        if beijing_weekday >= 5:
            ctx['skip'] = True
            print(f"  周末(周{beijing_weekday+1})，跳过筛选")
        else:
            print(f"  API不可达，非周末假定为交易日，继续筛选")

# ============================================================
# 步骤2: 极端行情检查
# ============================================================
def step2_extreme_market(ctx):
    print("\n" + "=" * 60)
    print("步骤2: 极端行情检查")
    print("=" * 60)
    
    sh_chg = None
    sh_price = None
    
    # 方案一：新浪API（东方财富push2不可达，直接使用新浪）
    try:
        url = "https://hq.sinajs.cn/list=sh000001"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'
        })
        resp = urllib.request.urlopen(req, timeout=5)
        text = resp.read().decode('gbk')
        if text and '=""' not in text:
            parts = text.split('"')[1].split(',')
            if len(parts) > 3:
                current = float(parts[3]) if parts[3] and parts[3] != '' else 0
                prev_close = float(parts[1]) if parts[1] and parts[1] != '' else 0
                if current > 0 and prev_close > 0:
                    sh_chg = round((current - prev_close) / prev_close * 100, 2)
                    sh_price = current
    except Exception:
        pass
    
    # 方案二：腾讯API降级
    if sh_chg is None:
        try:
            url = "https://qt.gtimg.cn/q=sh000001"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=5)
            text = resp.read().decode('gbk')
            parts = text.split('~')
            if len(parts) > 4:
                current = float(parts[3]) if parts[3] and parts[3] != '' else 0
                prev_close = float(parts[4]) if parts[4] and parts[4] != '' else 0
                if current > 0 and prev_close > 0:
                    sh_chg = round((current - prev_close) / prev_close * 100, 2)
                    sh_price = current
        except Exception:
            pass
    
    if sh_chg is not None:
        print(f"  上证指数: {sh_price} | 涨跌幅: {sh_chg:+.2f}%")
        ctx['sh_index_change'] = sh_chg
        ctx['sh_index_price'] = sh_price
        if sh_chg < -3:
            print(f"  ⚠️ 上证跌>3%，跳过筛选")
            ctx['skip'] = True
            ctx['market_condition'] = '弱市'
            return
        elif sh_chg > 3:
            print(f"  ⚠️ 上证涨>3%，仓位降至30%仅动量延续")
            ctx['position'] = 30
            ctx['_extreme_market_position'] = 30
            ctx['_extreme_market'] = '强市(极端)'
            ctx['market_condition'] = '强市(极端)'
            ctx['_extreme_up_a_restore'] = True
        else:
            ctx['market_condition'] = None
        
        # 跌停数阈值检查（东方财富API不可达→降级为跳过）
        log_alert("INFO", "极端行情", "跌停计数API不可达(东方财富push2)，跳过跌停阈值检查")
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

def _fetch_sina_chg(sina_code, label):
    """从新浪API获取单只指数涨跌幅
    外国指数格式: name,price,change,change_pct → parts[3]=涨跌幅(%)"""
    try:
        url = f"https://hq.sinajs.cn/list={sina_code}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'
        })
        resp = urllib.request.urlopen(req, timeout=5)
        text = resp.read().decode('gbk')
        if text and '=""' not in text:
            parts = text.split('"')[1].split(',')
            if len(parts) > 3 and parts[3] and parts[3] != '':
                # 外国指数: parts[3] 直接就是涨跌幅百分比
                return round(float(parts[3]), 2)
    except Exception:
        pass
    return None

def step3_foreign_market(ctx):
    print("\n" + "=" * 60)
    print("步骤3: 外围市场检查")
    print("=" * 60)
    
    ctx['foreign_weak'] = False
    ctx['pause_strategy_e'] = False
    ctx['foreign_detail'] = {}
    
    # 1. 美股三大指数检查（新浪API → Yahoo降级）
    us_indices = {
        'int_dji': ('道琼斯', '^DJI'),
        'int_nasdaq': ('纳斯达克', '^IXIC'),
        'int_sp500': ('标普500', '^GSPC'),
    }
    us_chgs = {}
    for sina_code, (label, yahoo_sym) in us_indices.items():
        chg = _fetch_sina_chg(sina_code, label)
        if chg is None:
            chg = _fetch_yahoo_chg(yahoo_sym, label)
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
        print(f"  美股数据均不可得，可能美股休市，跳过美股检查")
        log_alert("INFO", "外围市场", "美股数据不可得，可能美股休市，跳过美股检查")
    
    # 2. 恒生指数检查（新浪API → Yahoo降级）
    hsi_chg = _fetch_sina_chg('int_hangseng', '恒生指数')
    if hsi_chg is None:
        hsi_chg = _fetch_yahoo_chg('^HSI', '恒生指数')
    ctx['foreign_detail']['hsi'] = hsi_chg
    if hsi_chg is not None:
        print(f"  恒生指数: {hsi_chg:+.2f}%")
        if hsi_chg < -3:
            ctx['foreign_weak'] = True
            print(f"  ⚠️ 恒生跌>3% → 弱市，仅超跌反弹")
    else:
        print(f"  恒生数据不可得，跳过")
    
    # 3. 人民币汇率波动检查（新浪API → Yahoo降级）
    cny_chg = None
    try:
        url = "https://hq.sinajs.cn/list=fx_susdcny"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'
        })
        resp = urllib.request.urlopen(req, timeout=5)
        text = resp.read().decode('gbk')
        if text and '=""' not in text:
            parts = text.split('"')[1].split(',')
            # 格式: time,bid,ask,last,?,high,low,open,prev_close,...
            if len(parts) > 8 and parts[3] and parts[3] != '':
                last = float(parts[3])
                prev_close = float(parts[8]) if parts[8] and parts[8] != '' else 0
                if prev_close > 0 and last > 0:
                    cny_chg = abs((last - prev_close) / prev_close * 100)
    except Exception:
        pass
    
    if cny_chg is None:
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
        except Exception:
            pass
    
    if cny_chg is not None:
        ctx['foreign_detail']['cny_vol'] = round(cny_chg, 2)
        print(f"  人民币波动: {cny_chg:.2f}%")
        if cny_chg > 0.5:
            ctx['pause_strategy_e'] = True
            print(f"  ⚠️ 人民币波动>{0.5}% → 暂停策略E")
    else:
        print(f"  人民币汇率数据不可得，跳过")
    
    if not ctx['foreign_weak'] and not ctx['pause_strategy_e']:
        print(f"  外围市场正常")

# ============================================================
# 步骤3A: 开盘前外围期货
# ============================================================
def step3A_futures(ctx):
    print("\n" + "=" * 60)
    print("步骤3A: 开盘前外围期货检查")
    print("=" * 60)
    
    # 新浪期货代码（Yahoo Finance不可达→新浪替代）
    futures = {
        'hf_ES': '标普500期货',
        'hf_NQ': '纳斯达克期货',
        'hf_YM': '道琼斯期货',
        'hf_XIN': 'A50期货',
    }
    bearish_count = 0
    results = {}
    
    for sina_code, label in futures.items():
        try:
            url = f"https://hq.sinajs.cn/list={sina_code}"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'
            })
            resp = urllib.request.urlopen(req, timeout=5)
            text = resp.read().decode('gbk')
            if text and '=""' not in text:
                parts = text.split('"')[1].split(',')
                # 期货格式: current,?,?,?,high,low,time,prev_settle,...
                if len(parts) > 7 and parts[0] and parts[0] != '':
                    current = float(parts[0])
                    prev_settle = float(parts[7]) if parts[7] and parts[7] != '' else 0
                    if prev_settle > 0 and current > 0:
                        chg = round((current - prev_settle) / prev_settle * 100, 2)
                        results[label] = chg
                        print(f"  {label}: {chg:+.2f}%")
                        if chg < -1:
                            bearish_count += 1
                    else:
                        results[label] = None
                else:
                    results[label] = None
            else:
                results[label] = None
        except Exception:
            results[label] = None
    
    ctx['futures_detail'] = results
    
    if bearish_count > 0:
        ctx['futures_bearish'] = True
        print(f"  ⚠️ {bearish_count}只期货跌>1% → 外围偏空，步骤8将降一档仓位")
    else:
        ctx['futures_bearish'] = False
        if all(v is None for v in results.values()):
            print(f"  期货数据均不可得，跳过此检查")
        else:
            print(f"  期货市场正常")