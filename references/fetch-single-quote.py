# 采集单个标的的行情数据（开盘价/收盘价/换手率/振幅/涨跌幅）
import urllib.request, json

def fetch_stock_quote(code, data_date):
    """通过定向URL获取精确行情，返回 dict 或 None。data_date 用于校验行情日期（YYYY-MM-DD）"""
    market = 'sz' if code.startswith(('000','002','003','300','301')) else 'sh'
    # 东方财富secid格式：深圳0，上海1（数字代码，非sz/sh字符串）
    secid_market = '0' if market == 'sz' else '1'
    # 新浪API不提供换手率字段（parts[37]/[38]在大多数响应中不存在），不尝试读取

    # 方案一：新浪财经实时行情API
    try:
        sina_url = f'https://hq.sinajs.cn/list={market}{code}'
        req = urllib.request.Request(sina_url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.sina.com.cn'
        })
        resp = urllib.request.urlopen(req, timeout=5)
        text = resp.read().decode('gbk')
        if text and '=""' not in text:
            parts = text.split('"')[1].split(',')
            if len(parts) > 5:
                open_price = float(parts[1]) if parts[1] else None
                prev_close = float(parts[2]) if parts[2] else None  # 昨收
                current = float(parts[3]) if parts[3] else None
                high = float(parts[4]) if parts[4] else None
                low = float(parts[5]) if parts[5] else None
                if open_price is None or open_price <= 0:
                    raise ValueError("价格数据无效")
                # 收盘价：取现价（盘中）或昨收（盘后），由调用方 date 校验判断是否目标日
                close = current if current and current > 0 else prev_close
                amplitude = round((high - low) / prev_close * 100, 2) if prev_close else None
                change_pct = round((current - prev_close) / prev_close * 100, 2) if current and prev_close else None
                turnover = None  # 新浪API不提供换手率
                # Sina API 日期在 parts[30]（格式 YYYY-MM-DD），用于校验
                quote_date = parts[30] if len(parts) > 30 and parts[30] else None
                return {
                    "open": open_price, "close": close,
                    "turnover": turnover, "amplitude": amplitude, "change_pct": change_pct,
                    "quote_date": quote_date, "source": "sina"
                }
    except Exception:
        pass

    # 方案二：东方财富行情API（secid用数字市场代码：0=深圳, 1=上海）
    try:
        em_url = f'https://push2.eastmoney.com/api/qt/stock/get?secid={secid_market}.{code}&fields=f43,f44,f45,f46,f50,f168,f170'
        req = urllib.request.Request(em_url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        if data.get('data'):
            d = data['data']
            open_price = d.get('f46', 0) / 100 if d.get('f46') else None
            close_price = d.get('f43', 0) / 100 if d.get('f43') else None
            turnover = d.get('f168', 0) / 100 if d.get('f168') else None
            amplitude = d.get('f50', 0) / 100 if d.get('f50') else None
            change_pct = d.get('f170', 0) / 100 if d.get('f170') else None
            if open_price and open_price > 0:
                return {
                    "open": open_price, "close": close_price, "turnover": turnover,
                    "amplitude": amplitude, "change_pct": change_pct,
                    "quote_date": None, "source": "eastmoney"
                }
    except Exception:
        pass

    return None

# === 数据校验（关键新增） ===
def validate_quote(quote, code, name, data_date):
    """校验行情数据合理性+日期匹配，不通过则标记为不可用"""
    if quote is None:
        return None
    # 0. 日期校验（关键）：Sina API 返回 quote_date（如"2026-06-11"），必须匹配 data_date
    qd = quote.get('quote_date')
    if qd and data_date:
        if qd != data_date:
            log_alert("WARNING", "数据校验", f"{code} {name} 行情日期={qd}≠目标日期={data_date}，数据不可用")
            return None
    # 1. 价格区间检查：A股正常范围 0.01 ~ 9999
    for k in ['open', 'close']:
        if quote.get(k) is not None and (quote[k] <= 0 or quote[k] > 9999):
            log_alert("WARNING", "数据校验", f"{code} {name} {k}={quote[k]} 超出合理范围")
            return None
    # 2. 涨跌幅合理性：单日涨跌幅应在 -20% ~ +20%（A股涨跌停±10%，科创板±20%）
    if quote.get('change_pct') is not None:
        if abs(quote['change_pct']) > 20:
            log_alert("WARNING", "数据校验", f"{code} {name} 涨跌幅={quote['change_pct']}% 超出±20%")
            return None
    # 3. 振幅合理性
    if quote.get('amplitude') is not None and quote['amplitude'] > 30:
        log_alert("WARNING", "数据校验", f"{code} {name} 振幅={quote['amplitude']}% 异常")
    return quote
