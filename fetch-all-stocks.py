# 双路策略：优先东方财富clist API，不可达时降级为新浪批量API
import urllib.request, urllib.parse, json, time

def fetch_all_a_stocks():
    """
    拉取全A股行情数据。优先东方财富clist（一次性返回），
    不可达时自动降级为新浪批量API（分批拉取）。
    返回 (stocks_list, error_msg)
    """
    # === 方案一：东方财富 clist API（一次性全量，效率最高） ===
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1", "pz": "6000", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f2,f3,f5,f7,f8,f10,f12,f14,f15,f16,f17,f18,f20,f62",
            "_": str(int(time.time() * 1000))
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://quote.eastmoney.com/'
        }
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        if data and data.get('data') and data['data'].get('diff'):
            stocks = []
            for item in data['data']['diff']:
                code = item.get('f12', '')
                name = item.get('f14', '')
                if not code or not name:
                    continue
                close_val = item.get('f2')
                if close_val == '-' or close_val is None:
                    continue
                try:
                    stocks.append({
                        "code": code, "name": name,
                        "open": float(item.get('f17', 0)) if item.get('f17') not in (None, '-') else None,
                        "close": float(close_val),
                        "change_pct": float(item.get('f3', 0)) if item.get('f3') not in (None, '-') else 0,
                        "turnover": float(item.get('f8', 0)) if item.get('f8') not in (None, '-') else 0,
                        "amplitude": float(item.get('f7', 0)) if item.get('f7') not in (None, '-') else 0,
                        "volume_ratio": float(item.get('f10', 0)) if item.get('f10') not in (None, '-') else 0,
                        "amount": float(item.get('f6', 0)) if item.get('f6') not in (None, '-') else 0,
                        "high": float(item.get('f15', 0)) if item.get('f15') not in (None, '-') else None,
                        "low": float(item.get('f16', 0)) if item.get('f16') not in (None, '-') else None,
                        "prev_close": float(item.get('f18', 0)) if item.get('f18') not in (None, '-') else None,
                        "main_inflow": float(item.get('f62', 0)) if item.get('f62') not in (None, '-') else None,
                        "total_cap": float(item.get('f20', 0)) if item.get('f20') not in (None, '-') else None,
                    })
                except (ValueError, TypeError):
                    continue
            return stocks, None
    except Exception:
        pass  # 降级到方案二

    # === 方案二：新浪财经批量API（沙箱环境适用，分批拉取全A股） ===
    log_alert("INFO", "行情采集", "东方财富clist不可达，降级为新浪批量API")
    try:
        # 生成全A股代码范围（排除北交所8xxx，规则2直接排除无需拉取）
        code_ranges = []
        # 上海主板: 600000-605999（约2000只实际标的）
        for i in range(600000, 606000):
            code_ranges.append(f"sh{i}")
        # 上海科创板: 688000-689999（约600只实际标的）
        for i in range(688000, 690000):
            code_ranges.append(f"sh{i}")
        # 深圳主板: 000001-004999（约2000只实际标的）
        for i in range(1, 5000):
            code_ranges.append(f"sz{i:06d}")
        # 深圳创业板: 300000-301999（约1400只实际标的）
        for i in range(300000, 302000):
            code_ranges.append(f"sz{i}")

        stocks = []
        batch_size = 80  # 新浪API每批建议≤100
        for i in range(0, len(code_ranges), batch_size):
            batch = code_ranges[i:i+batch_size]
            try:
                url = f"https://hq.sinajs.cn/list={','.join(batch)}"
                req = urllib.request.Request(url, headers={
                    'User-Agent': 'Mozilla/5.0',
                    'Referer': 'https://finance.sina.com.cn'
                })
                with urllib.request.urlopen(req, timeout=5) as resp:
                    text = resp.read().decode('gbk')
                for line in text.strip().split('\n'):
                    if not line or '=""' in line:
                        continue
                    try:
                        parts = line.split('"')[1].split(',')
                        if len(parts) < 6:
                            continue
                        # 解析代码和名称
                        header = line.split('="')[0]
                        raw_code = header.split('_')[-1] if '_' in header else header[-6:]
                        code = raw_code if len(raw_code) == 6 else raw_code[-6:]
                        name = parts[0]
                        open_val = float(parts[1]) if parts[1] else 0
                        prev_close = float(parts[2]) if parts[2] else 0
                        current = float(parts[3]) if parts[3] else 0
                        high = float(parts[4]) if parts[4] else 0
                        low = float(parts[5]) if parts[5] else 0
                        if current <= 0 or prev_close <= 0:
                            continue
                        # 涨跌幅
                        change_pct = round((current - prev_close) / prev_close * 100, 2)
                        # 振幅
                        amplitude = round((high - low) / prev_close * 100, 2) if prev_close > 0 else 0
                        # 换手率：新浪API不提供此字段（parts[37]/[38]在大多数响应中不存在），设为0
                        turnover = 0.0
                        # 成交量(手): parts[8]
                        volume = float(parts[8]) if len(parts) > 8 and parts[8] else 0
                        # 成交额(元): parts[9] — 新浪API提供的实际成交额
                        amount_val = float(parts[9]) if len(parts) > 9 and parts[9] and parts[9] != '' else 0
                        stocks.append({
                            "code": code, "name": name,
                            "open": open_val, "close": current,
                            "change_pct": change_pct,
                            "turnover": turnover,
                            "amplitude": amplitude,
                            "high": high, "low": low,
                            "prev_close": prev_close,
                            "volume": volume,
                            "volume_ratio": None,   # 新浪无此字段，策略匹配时用成交额+振幅代理
                            "amount": amount_val,    # 成交额(元)，策略匹配中作为活跃度代理
                            "main_inflow": None,     # 新浪无此字段
                            "total_cap": None,
                        })
                    except (ValueError, IndexError):
                        continue
                if i % (batch_size * 10) == 0:
                    time.sleep(0.05)  # 每10批短暂休息，避免被限流
            except Exception:
                continue
        return stocks, None
    except Exception as e:
        return None, f"新浪批量也失败: {str(e)[:100]}"

# 调用
all_stocks, err = fetch_all_a_stocks()
if all_stocks is None:
    log_alert("ERROR", "行情采集", f"全市场API拉取失败: {err}")
    raise RuntimeError(f"行情数据获取失败: {err}")
# 判断数据来源（clist有volume_ratio字段，sina无）
source = 'clist' if any(s.get('volume_ratio') is not None for s in all_stocks[:10]) else 'sina'
log_alert("INFO", "行情采集", f"全市场拉取到 {len(all_stocks)} 只标的（来源: {source}）")

# 从全市场数据构建原始标的池
# 保留涨跌幅>0%且非停牌且有成交量的标的，按活跃度排序取TOP500
raw_pool = [s for s in all_stocks
            if s['change_pct'] is not None and s['change_pct'] > 0
            and s['close'] is not None and s['close'] > 0
            and s.get('volume', 1) > 0]  # 成交量>0排除停牌
# 排序：clist用换手率，sina用成交额代理
if source == 'clist':
    raw_pool.sort(key=lambda x: (x.get('turnover', 0) or 0), reverse=True)
else:
    raw_pool.sort(key=lambda x: (x.get('amount', 0) or 0), reverse=True)
raw_pool = raw_pool[:500]  # 取活跃度前500只进入后续筛选
total_raw = len(raw_pool)
log_alert("INFO", "行情采集", f"原始标的池: {total_raw} 只（全市场{len(all_stocks)}只中涨跌幅>0%且活跃TOP500）")
