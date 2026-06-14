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
    beijing_weekday = ctx['beijing_weekday']
    
    # 周末已在 step0 处理（data_date 已回滚到最近工作日），此处检查法定节假日
    ctx['skip'] = False
    ctx['is_weak_market'] = False
    ctx['is_long_holiday'] = False
    
    try:
        # 搜索中国股市交易日历
        url = "https://push2.eastmoney.com/api/qt/kline/get"
        params = {
            "secid": "1.000001",
            "fields1": "f1",
            "fields2": "f51",
            "klt": "101",
            "fqt": "1",
            "beg": data_date.replace('-', ''),
            "end": data_date.replace('-', ''),
            "lmt": "1",
        }
        import urllib.parse
        req = urllib.request.Request(
            f"{url}?{urllib.parse.urlencode(params)}",
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        klines = data.get('data', {}).get('klines', [])
        
        if not klines or len(klines) == 0:
            # data_date 无K线数据 → 节假日/停市
            print(f"  ⚠️ {data_date} 无K线数据，判定为节假日→跳过筛选")
            ctx['skip'] = True
            log_alert("INFO", "节假日检查", f"{data_date}无K线数据，节假日跳过")
            
            # 检查是否为长休（≥3天无交易）
            try:
                dt = datetime.strptime(data_date, '%Y-%m-%d')
                for check_days in [1, 2]:
                    prev_dt = dt - timedelta(days=check_days)
                    prev_str = prev_dt.strftime('%Y-%m-%d').replace('-', '')
                    p = dict(params)
                    p["beg"] = prev_str
                    p["end"] = prev_str
                    req2 = urllib.request.Request(
                        f"{url}?{urllib.parse.urlencode(p)}",
                        headers={'User-Agent': 'Mozilla/5.0'}
                    )
                    try:
                        resp2 = urllib.request.urlopen(req2, timeout=3)
                        data2 = json.loads(resp2.read())
                        k2 = data2.get('data', {}).get('klines', [])
                        if not k2:
                            ctx['is_long_holiday'] = True
                            ctx['is_weak_market'] = True
                            # 搜索预算+5（SKILL §步骤1: 长休≥3日→搜索预算+5）
                            params = ctx.get('params', {})
                            params['search_budget'] = params.get('search_budget', 25) + 5
                            ctx['params'] = params
                            print(f"  ⚠️ 长休≥3日→弱市+仓位≤30%+搜索预算+5({params['search_budget']})")
                            break
                    except:
                        break
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
            ctx['_extreme_market_position'] = 30
            ctx['_extreme_market'] = '强市(极端)'
            ctx['market_condition'] = '强市(极端)'
            # 若弱市策略A已关闭，临时启用A仓位15%
            ctx['_extreme_up_a_restore'] = True
        else:
            ctx['market_condition'] = None
        
        # 跌停数阈值检查（SKILL §步骤2: 跌停>threshold→跳过）
        limit_down_threshold = ctx.get('params', {}).get('limit_down_threshold', 100)
        try:
            import urllib.parse
            ld_url = "https://push2.eastmoney.com/api/qt/clist/get"
            ld_params = {
                "pn": "1", "pz": "1", "po": "0", "np": "1",
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": "2", "invt": "2", "fid": "f3",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                "fields": "f12",
                "_": str(int(time.time() * 1000))
            }
            ld_req = urllib.request.Request(
                f"{ld_url}?{urllib.parse.urlencode(ld_params)}",
                headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'}
            )
            ld_resp = urllib.request.urlopen(ld_req, timeout=5)
            ld_data = json.loads(ld_resp.read())
            limit_down_count = ld_data.get('data', {}).get('total', 0) if ld_data.get('data') else 0
            print(f"  跌停家数: {limit_down_count} (阈值{limit_down_threshold})")
            if limit_down_count > limit_down_threshold:
                print(f"  ⚠️ 跌停{limit_down_count}>{limit_down_threshold}，跳过筛选")
                ctx['skip'] = True
                log_alert("WARNING", "极端行情", f"跌停{limit_down_count}只>{limit_down_threshold}，跳过筛选")
                return
        except Exception:
            log_alert("INFO", "极端行情", "跌停计数API不可达，跳过跌停阈值检查")
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
    ctx['pause_strategy_e'] = False
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
        # 美股假期检测：所有数据均为None→可能美股休市，跳过美股检查
        print(f"  美股数据均不可得，可能美股休市，跳过美股检查")
        log_alert("INFO", "外围市场", "美股数据不可得，可能美股休市，跳过美股检查")
    
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
                    ctx['pause_strategy_e'] = True
                    print(f"  ⚠️ 人民币波动>{0.5}% → 暂停策略E")
    except Exception:
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
    
    # 检查美股期货（标普/纳指/道指期货）实时行情
    # Yahoo Finance 期货代码: ES=F(标普), NQ=F(纳指), YM=F(道指)
    futures = {
        'ES=F': '标普500期货',
        'NQ=F': '纳斯达克期货',
        'YM=F': '道琼斯期货'
    }
    bearish_count = 0
    results = {}
    
    for sym, label in futures.items():
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=2d"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read())
            result = data.get('chart', {}).get('result', [])
            if result:
                quotes = result[0].get('indicators', {}).get('quote', [{}])[0]
                closes = quotes.get('close', [])
                if len(closes) >= 2 and closes[-2] and closes[-1]:
                    chg = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2)
                    results[label] = chg
                    print(f"  {label}: {chg:+.2f}%")
                    if chg < -1:
                        bearish_count += 1
        except Exception:
            results[label] = None
    
    ctx['futures_detail'] = results
    
    # 任一期货跌>1% → 外围偏空，设置标志由步骤8大盘判断处理降档
    if bearish_count > 0:
        ctx['futures_bearish'] = True
        print(f"  ⚠️ {bearish_count}只期货跌>1% → 外围偏空，步骤8将降一档仓位")
    else:
        ctx['futures_bearish'] = False
        if all(v is None for v in results.values()):
            print(f"  期货数据均不可得，跳过此检查")
        else:
            print(f"  期货市场正常")