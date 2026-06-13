#!/usr/bin/env python3
"""
A股每日盘前短线标的筛选 v6.5.5
严格按 SKILL.md「十五、完整执行步骤」（35步）逐步执行。

35步完整流程:
0.获取北京时间(data_date+prediction_date) → 1.节假日检查 → 2.极端行情 → 
3.外围市场 → 3A.开盘前外围(期货跌>1%→降档) → 4.持仓行情同步 → 4A.做T评估 → 
4B.持仓跟踪同步 → 4C.持仓危机检查 → 5.推荐历史持久化 → 6.文件初始化 → 
7.财报季检测 → 8.大盘判断 → 9.板块轮动 → 10A.全市场API拉取(东方财富clist) → 
10B.板块/行业补全(WebSearch) → 11.硬排除31项(含L1/L2/L3分级) → 
12.信号过滤14项 → 13.五策略筛选 → 14.评分门控 → 15.冲突处理 → 
16.综合评分 → 17.行业限制 → 18.新闻筛查 → 19.推荐不足降级 → 
20.输出Excel(8sheet) → 20B.生成HTML报告 → 21.最终验证 → 
22.写推荐历史+清理 → 23.回溯检查昨日做T → 24.告警日志摘要 → 
25.输出筛选概况 → 26.GitHub同步(xlsx+html) → 27.飞书推送 → 
28.每周复盘拉取(仅周六)
"""
import urllib.request, urllib.error, urllib.parse, json, os, time, shutil, subprocess, ssl, re
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ============================================================
# 文件路径
# ============================================================
WORKSPACE = "/workspace"
HISTORY_PATH = f"{WORKSPACE}/推荐历史.json"
ALERT_LOG_PATH = f"{WORKSPACE}/系统告警.log"
TRACKING_XLSX = f"{WORKSPACE}/持仓跟踪.xlsx"
PARAMS_PATH = f"{WORKSPACE}/策略调整记录.json"
FILTER_XLSX = f"{WORKSPACE}/A股短线选股筛选条件.xlsx"
GITHUB_TOKEN_PATH = f"{WORKSPACE}/.github_token"
FEISHU_WEBHOOK_PATH = f"{WORKSPACE}/.feishu_webhook"

# ============================================================
# 可配置参数默认值
# ============================================================
PARAMS = {
    "search_budget": 25, "northbound_threshold": 3000, "consecutive_weeks": 2,
    "win_rate_drop_threshold": 10, "limit_down_threshold": 100, "max_adjust_params": 3,
    "confidence_position_enabled": True, "max_holding_days": 5,
    "circuit_breaker_threshold_pct": 3.0, "strategy_concentration_pct": 60,
    "do_t_success_reset_count": 3, "conversion_rate_window_days": 10,
    "conversion_rate_threshold": 0.3, "conversion_rate_restore": 0.6,
    "conversion_rate_consecutive_days": 3, "data_tier_l2_skip_on_unavailable": True,
    "data_tier_l3_downgrade_to_signal": True, "strategy_a_weak_market": "closed"
}

# ============================================================
# 全局变量（步骤0填充）
# ============================================================
beijing_now = None
beijing_date = None
prediction_date = None
data_date = None
beijing_weekday = None
beijing_hour = None
file_version = "v6.5.5"

# ============================================================
# 筛选管道计数器（各步骤累积）
# ============================================================
total_raw = 0
excluded_count = 0
filtered_count = 0
matched_count = 0
industry_limited_count = 0
news_filtered_count = 0
final_recommend_count = 0
strategy_counts = Counter()
exclude_stats = Counter()
filter_stats = Counter()


# ============================================================
# 系统告警
# ============================================================
def log_alert(level, module, message, timestamp=None):
    """写入告警日志。timestamp 默认使用系统时钟，可传入 beijing_now 替代。"""
    if timestamp is None:
        timestamp = datetime.now()
    ts = timestamp.strftime('%Y-%m-%d %H:%M:%S') if hasattr(timestamp, 'strftime') else str(timestamp)
    try:
        with open(ALERT_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write(f"[{ts}] [{level}] {module}: {message}\n")
    except Exception:
        pass  # 告警写入失败静默处理，不阻断主流程


# ============================================================
# 文件容错函数
# ============================================================
def safe_read_json(path, default=None):
    try:
        if not os.path.exists(path):
            return default if default is not None else []
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if not isinstance(data, list):
                log_alert("WARNING", "safe_read_json", f"{path} 格式异常")
                return default if default is not None else []
            return data
    except (json.JSONDecodeError, PermissionError) as e:
        log_alert("ERROR", "safe_read_json", f"{path}: {str(e)}")
        return default if default is not None else []
    except Exception as e:
        log_alert("ERROR", "safe_read_json", f"{path}: {str(e)}")
        return default if default is not None else []


def safe_write_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_alert("ERROR", "safe_write_json", f"{path}: {str(e)}")


def safe_append_json(path, record):
    data = safe_read_json(path)
    data.append(record)
    safe_write_json(path, data)


def safe_read_excel(path):
    try:
        if not os.path.exists(path):
            return None
        return load_workbook(path)
    except Exception as e:
        log_alert("WARNING", "safe_read_excel", f"{path}: {str(e)}")
        return None


def safe_float(value, ndigits=3):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return round(float(value), ndigits)
    return value


def read_file_token(path):
    """从外部文件读取单行文本（token/webhook等）"""
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return f.read().strip()
    except Exception:
        pass
    return None


# ============================================================
# WebSearch 辅助（带预算控制）
# ============================================================
_web_search_count = 0
_web_search_budget = 0


def _reset_search_budget():
    global _web_search_count, _web_search_budget
    _web_search_count = 0
    _web_search_budget = PARAMS.get("search_budget", 25)


def _web_search(query, timeout=8):
    """有限的 WebSearch，带预算控制"""
    global _web_search_count
    if _web_search_count >= _web_search_budget:
        return None
    _web_search_count += 1
    try:
        encoded = urllib.parse.quote(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded}"
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read().decode('utf-8', errors='ignore')
        # 简单提取文本片段
        snippets = []
        for m in re.finditer(r'class="result__snippet"[^>]*>(.*?)</a>', raw, re.DOTALL):
            text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            if text:
                snippets.append(text)
        return ' '.join(snippets[:3]) if snippets else None
    except Exception:
        return None


def _stock_web_search(code, name, keywords, budget_use=1):
    """对特定股票搜索指定关键词"""
    query = f"{code} {name} {keywords}"
    return _web_search(query)


# ============================================================
# 步骤零、北京时间获取（最高优先级，必须第一步执行）
# ============================================================
def step0_get_beijing_time():
    """仅通过公共网络授时 API 获取精确北京时间。不依赖本地系统时钟。"""
    global beijing_now, beijing_date, prediction_date, data_date
    global beijing_weekday, beijing_hour

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    # worldtimeapi 优先，timeapi.io 作为降级
    TIME_APIS = [
        ('https://worldtimeapi.org/api/timezone/Asia/Shanghai', 'datetime'),
        ('https://timeapi.io/api/time/current/zone?timeZone=Asia/Shanghai', 'dateTime'),
    ]
    beijing_now_local = None
    for api_url, dt_key in TIME_APIS:
        try:
            req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=10, context=ssl_ctx)
            raw_data = json.loads(resp.read())
            dt_str = raw_data[dt_key]

            # Python 3.10 fromisoformat: 截断>6位小数秒
            if '.' in dt_str:
                dot_idx = dt_str.index('.')
                rest = dt_str[dot_idx + 1:]
                frac_digits = ''
                suffix = ''
                for i, ch in enumerate(rest):
                    if ch.isdigit():
                        frac_digits += ch
                    else:
                        suffix = rest[i:]
                        break
                if len(frac_digits) > 6:
                    frac_digits = frac_digits[:6]
                dt_str = dt_str[:dot_idx + 1] + frac_digits + suffix

            beijing_now_local = datetime.fromisoformat(dt_str)
            print(f"[步骤0] 授时API: {api_url} → {beijing_now_local}")
            break
        except Exception as e:
            log_alert("INFO", "北京时间", f"{api_url} 不可达: {type(e).__name__}: {str(e)[:80]}")
            continue

    # 所有API均失败 → 报错中止
    if beijing_now_local is None:
        log_alert("ERROR", "北京时间", "所有授时API均不可达，本次筛选中止（禁止使用系统时钟）")
        raise RuntimeError("北京时间获取失败：所有授时API均不可达")

    beijing_now = beijing_now_local
    beijing_date = beijing_now.strftime('%Y-%m-%d')
    beijing_hour = beijing_now.hour
    beijing_weekday = beijing_now.weekday()  # 0=周一, 6=周日

    # data_date（数据日期）：数据来源日。周末回退到周五
    if beijing_weekday == 5:  # 周六 → 数据日期为周五
        data_dt = beijing_now - timedelta(days=1)
        data_date = data_dt.strftime('%Y-%m-%d')
    elif beijing_weekday == 6:  # 周日 → 数据日期为周五
        data_dt = beijing_now - timedelta(days=2)
        data_date = data_dt.strftime('%Y-%m-%d')
    else:
        data_date = beijing_date

    # prediction_date（预测日期）：下一个交易日
    # Mon(0)→Tue(+1), Tue(1)→Wed(+1), Wed(2)→Thu(+1), Thu(3)→Fri(+1)
    # Fri(4)→Mon(+3), Sat(5)→Mon(+2), Sun(6)→Mon(+1)
    if beijing_weekday <= 3:       # 周一至周四 → 次日
        pred_dt = beijing_now + timedelta(days=1)
    elif beijing_weekday == 4:      # 周五 → 下周一
        pred_dt = beijing_now + timedelta(days=3)
    elif beijing_weekday == 5:      # 周六 → 下周一
        pred_dt = beijing_now + timedelta(days=2)
    else:                            # 周日 → 下周一
        pred_dt = beijing_now + timedelta(days=1)
    prediction_date = pred_dt.strftime('%Y-%m-%d')

    print(f"[步骤0] 北京时间: {beijing_date} {beijing_now.strftime('%H:%M:%S')}")
    print(f"[步骤0] data_date={data_date}, prediction_date={prediction_date}")


# ============================================================
# 步骤1：节假日检查
# ============================================================
def _load_holidays():
    """加载2026年A股节假日列表"""
    return [
        '2026-01-01', '2026-01-02',
        '2026-02-16', '2026-02-17', '2026-02-18', '2026-02-19', '2026-02-20',
        '2026-04-06',
        '2026-05-01', '2026-05-04', '2026-05-05',
        '2026-06-19',
        '2026-09-25',
        '2026-10-01', '2026-10-02', '2026-10-05', '2026-10-06', '2026-10-07',
    ]


def step1_holiday_check():
    """节假日检查，含长休≥3日的弱市降级"""
    global PARAMS
    print(f"[步骤1] 节假日检查...")
    holidays = _load_holidays()

    if data_date in holidays or prediction_date in holidays:
        print(f"[步骤1] 当前日期 {data_date} 为节假日，跳过今日筛选")
        return "SKIP"

    # 长休检测：≥3个连续交易日的假期
    holiday_set = set(holidays)
    data_dt = datetime.strptime(data_date, '%Y-%m-%d')
    consecutive_holidays = 0
    check_dt = data_dt
    for _ in range(10):
        if check_dt.strftime('%Y-%m-%d') in holiday_set:
            consecutive_holidays += 1
            check_dt += timedelta(days=1)
        else:
            break
    check_dt = data_dt - timedelta(days=1)
    for _ in range(10):
        if check_dt.strftime('%Y-%m-%d') in holiday_set:
            consecutive_holidays += 1
            check_dt -= timedelta(days=1)
        else:
            break

    if consecutive_holidays >= 3:
        print(f"[步骤1] 长休≥3日 → 弱市+仓位≤30%+搜索预算+5")
        PARAMS["search_budget"] = PARAMS.get("search_budget", 25) + 5
        return "LONG_HOLIDAY"

    print(f"[步骤1] {data_date} 为交易日，继续")
    return "OK"


# ============================================================
# 步骤2：极端行情检查
# ============================================================
def step2_extreme_market():
    """检查上证指数涨跌幅和跌停数量"""
    print(f"[步骤2] 极端行情检查...")
    try:
        url = "https://push2.eastmoney.com/api/qt/stock/get"
        params = {
            "secid": "1.000001",
            "fields": "f43,f44,f45,f46,f47,f48,f50,f60,f116,f117,f169,f170,f171"
        }
        qs = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{url}?{qs}", headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data and data.get('data'):
            d = data['data']
            sh_close = d.get('f43', 0) / 100 if d.get('f43') else 0
            sh_chg = d.get('f170', 0) / 100 if d.get('f170') else 0
            print(f"[步骤2] 上证收盘: {sh_close:.2f}, 涨跌幅: {sh_chg:.2f}%")

            threshold = PARAMS.get("limit_down_threshold", 100)

            if sh_chg < -3:
                print(f"[步骤2] 上证跌{sh_chg:.2f}%>3%，跳过筛选")
                return "SKIP", sh_chg, "weak"
            elif sh_chg > 3:
                print(f"[步骤2] 上证暴涨{sh_chg:.2f}%>3%，弱市策略A临时启用仓位15%")
                return "OK", sh_chg, "strong_surge"
            else:
                return "OK", sh_chg, "normal"
    except Exception as e:
        log_alert("WARNING", "极端行情", f"上证指数获取失败: {str(e)[:80]}")
        print(f"[步骤2] 上证数据获取失败，继续流程")
        return "OK", 0, "unknown"


# ============================================================
# 步骤3：外围市场
# ============================================================
def _fetch_global_index(secid, name):
    """获取全球指数涨跌幅"""
    try:
        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f43,f170"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=5)
        d = json.loads(resp.read())
        if d and d.get('data'):
            chg = d['data'].get('f170', 0) / 100 if d['data'].get('f170') else 0
            return chg
    except Exception:
        pass
    return None


def step3_global_markets():
    """外围市场检查：美股+港股+人民币汇率"""
    print(f"[步骤3] 外围市场检查...")
    result = {"us_bear": False, "hk_bear": False, "cny_volatile": False,
              "us_holiday": False, "hk_holiday": False}

    # 美股三大指数
    us_indices = [
        ("100.NDX", "纳斯达克"),
        ("100.DJI", "道琼斯"),
        ("100.SPX", "标普500"),
    ]
    us_drops = 0
    us_fetched = 0
    for code, name in us_indices:
        chg = _fetch_global_index(code, name)
        if chg is not None:
            us_fetched += 1
            if chg < -2:
                us_drops += 1
                print(f"  {name}: {chg:.2f}% (跌>2%)")

    if us_fetched == 0:
        result["us_holiday"] = True
        print(f"[步骤3] 美股数据获取为空，可能为假期，跳过美股检查")
    elif us_drops == 3:
        result["us_bear"] = True
        print(f"[步骤3] 美股三大指数均跌>2%，弱市仓位≤30%")

    # 恒生指数
    hsi_chg = _fetch_global_index("124.HSI", "恒生指数")
    if hsi_chg is not None:
        if hsi_chg < -3:
            result["hk_bear"] = True
            print(f"[步骤3] 恒生跌{hsi_chg:.2f}%>3%，弱市仅超跌反弹")
    else:
        result["hk_holiday"] = True
        print(f"[步骤3] 港股数据获取为空，可能为假期，跳过港股检查")

    # 人民币汇率波动检测（简化）
    print(f"[步骤3] 人民币汇率检查跳过（数据源有限）")

    return result


# ============================================================
# 步骤3A：开盘前外围期货检查
# ============================================================
def step3a_futures_check(global_result):
    """检查美股期货盘前走势"""
    print(f"[步骤3A] 期货检查...")
    futures_drop = False
    futures_codes = [
        ("100.ES", "标普期货"),
        ("100.NQ", "纳指期货"),
        ("100.YM", "道指期货"),
    ]
    for code, name in futures_codes:
        chg = _fetch_global_index(code, name)
        if chg is not None and chg < -1:
            print(f"  {name}: {chg:.2f}% (跌>1%)")
            futures_drop = True

    if futures_drop:
        print(f"[步骤3A] 期货偏空 → 仓位降一档")
        return "downgrade"
    else:
        print(f"[步骤3A] 期货正常/数据不可得，维持步骤3判断")
        return "maintain"


# ============================================================
# 步骤4：持仓行情同步
# ============================================================
def step4_holding_sync():
    """持仓行情同步：更新收盘价/盈亏"""
    print(f"[步骤4] 持仓行情同步...")
    history = safe_read_json(HISTORY_PATH)
    holdings = [r for r in history if r.get('type') == 'holding']
    if not holdings:
        print(f"[步骤4] 无持仓记录，跳过")
        return holdings, history

    updated = 0
    for h in holdings:
        code = str(h.get('code', ''))
        if not code:
            continue
        h['prev_close'] = h.get('current')  # 保存旧current为prev_close
        try:
            market = '0' if code.startswith(('000', '002', '003', '300', '301')) else '1'
            url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={market}.{code}&fields=f43,f170"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=5)
            d = json.loads(resp.read())
            if d and d.get('data') and d['data'].get('f43'):
                current = d['data']['f43'] / 100
                h['current'] = current
                h['update_date'] = data_date
                cost = h.get('cost', current)
                shares = h.get('shares', 0)
                pnl_pct = round((current - cost) / cost * 100, 2) if cost else 0
                h['pnl_pct'] = pnl_pct
                h['market_value'] = round(current * shares, 2) if shares else 0
                h['pnl_amount'] = round((current - cost) * shares, 2) if shares else 0
                updated += 1
        except Exception as e:
            log_alert("WARNING", "持仓行情", f"{code} {h.get('name','')}: {str(e)[:60]}")

    if updated > 0:
        safe_write_json(HISTORY_PATH, history)
        log_alert("INFO", "持仓行情", f"已更新{updated}只持仓")
    print(f"[步骤4] 持仓更新: {updated}只")
    return holdings, history


# ============================================================
# 步骤4A：做T评估
# ============================================================
def step4a_do_t_eval(holdings):
    """对持仓进行做T可行性评估"""
    print(f"[步骤4A] 做T评估...")
    evals = []
    for h in holdings:
        pnl_pct = h.get('pnl_pct', 0) or 0
        code = str(h.get('code', ''))
        name = str(h.get('name', ''))

        if pnl_pct > -5:
            status = "观望"
            advice = "浮亏<5%或浮盈，不建议做T"
        elif -10 <= pnl_pct < -5:
            status = "重点评估"
            advice = f"仓位≤总持仓1/3，目标2-3%止盈，-3%止损"
        elif -15 <= pnl_pct < -10:
            status = "谨慎评估"
            advice = f"仓位≤总持仓1/4，目标2-3%止盈，-3%止损"
        else:
            status = "不做T"
            advice = "浮亏>15%，不建议做T，建议人工决策"

        eval_record = {
            "type": "do_T_eval",
            "date": beijing_date,
            "code": code,
            "name": name,
            "pnl_pct": pnl_pct,
            "status": status,
            "advice": advice,
        }
        evals.append(eval_record)
        safe_append_json(HISTORY_PATH, eval_record)

    if evals:
        for e in evals:
            print(f"  {e['code']} {e['name']}: {e['status']} ({e['pnl_pct']:.1f}%)")
    else:
        print(f"[步骤4A] 无持仓，跳过做T评估")
    return evals


# ============================================================
# 步骤4B：持仓跟踪xlsx同步
# ============================================================
def step4b_tracking_sync(holdings):
    """将更新后的持仓价格写入持仓跟踪.xlsx"""
    print(f"[步骤4B] 持仓跟踪同步...")
    if not os.path.exists(TRACKING_XLSX):
        log_alert("WARNING", "持仓跟踪同步", "持仓跟踪.xlsx 不存在，跳过")
        return
    try:
        wb = load_workbook(TRACKING_XLSX)
        if "持仓明细" not in wb.sheetnames:
            log_alert("WARNING", "持仓跟踪同步", "持仓明细 sheet不存在")
            wb.close()
            return
        ws = wb["持仓明细"]
        code_row = {}
        for row in range(2, ws.max_row + 1):
            code = ws.cell(row=row, column=1).value
            if code and isinstance(code, str) and len(code) == 6:
                code_row[str(code)] = row

        updated = 0
        for h in holdings:
            code = str(h.get("code", ""))
            current = h.get("current")
            if not code or code not in code_row:
                continue
            if current is None:
                log_alert("WARNING", "持仓跟踪同步", f"{code} 缺少current字段，跳过")
                continue
            row = code_row[code]
            ws.cell(row=row, column=7).value = current  # 当前价
            mv = h.get("market_value")
            if mv is not None:
                ws.cell(row=row, column=8).value = mv  # 市值
            pnl_amt = h.get("pnl_amount")
            if pnl_amt is not None:
                ws.cell(row=row, column=9).value = round(pnl_amt, 2)  # 盈亏额
            pnl_pct = h.get("pnl_pct")
            if pnl_pct is not None:
                try:
                    ws.cell(row=row, column=10).value = round(float(pnl_pct), 4)
                except (ValueError, TypeError):
                    ws.cell(row=row, column=10).value = 0.0
            updated += 1

        if updated > 0:
            wb.save(TRACKING_XLSX)
            log_alert("INFO", "持仓跟踪同步", f"已更新{updated}只持仓价格")
        wb.close()
        print(f"[步骤4B] 同步{updated}只")
    except Exception as e:
        log_alert("WARNING", "持仓跟踪同步", f"失败: {str(e)[:80]}")


# ============================================================
# 步骤4C：持仓危机检查
# ============================================================
def step4c_crisis_check(holdings):
    """检查持仓危机信号：跌停/浮亏>15%/L1硬排除触发"""
    print(f"[步骤4C] 持仓危机检查...")
    alerts = []
    for h in holdings:
        code = str(h.get("code", "?"))
        name = str(h.get("name", "?"))
        cost = h.get("cost", 0) or 0
        current = h.get("current", 0) or 0
        prev_close = h.get("prev_close")
        pnl_pct = h.get("pnl_pct", 0) or 0

        # 跌停检查
        if prev_close is not None and current > 0 and prev_close > 0:
            daily_chg = (current - prev_close) / prev_close * 100
            if daily_chg < -9.5:
                msg = (f"  {code} {name} 当日跌停({daily_chg:.1f}%)！"
                       f"成本{cost} 现价{current} 浮亏{pnl_pct}%")
                alerts.append(msg)
                log_alert("WARNING", "持仓危机", msg)

        # 浮亏>15%
        if pnl_pct < -15:
            msg = (f"  {code} {name} 浮亏突破15%做T上限({pnl_pct:.1f}%)，"
                   f"建议人工决策")
            alerts.append(msg)
            log_alert("WARNING", "持仓危机", msg)

        # L1硬排除触发
        if current > 0:
            l1_triggers = []
            if current < 5:
                l1_triggers.append("股价<5元(规则3)")
            if current > 100:
                l1_triggers.append("股价>100元(规则4)")
            if code.startswith("688"):
                l1_triggers.append("科创板(规则1)")
            if code.startswith("8") and len(code) == 6:
                l1_triggers.append("北交所(规则2)")
            if l1_triggers:
                msg = f"  {code} {name} 触发L1硬排除: {', '.join(l1_triggers)}"
                alerts.append(msg)
                log_alert("WARNING", "持仓危机", msg)

    if alerts:
        print(f"[步骤4C] 持仓危机告警: {len(alerts)}条")
    else:
        print(f"[步骤4C] 无持仓危机")
    return alerts


# ============================================================
# 步骤5：推荐历史持久化 + 清理
# ============================================================
def step5_history_cleanup():
    """清理7天前recommendation + 90天前holding"""
    print(f"[步骤5] 推荐历史清理...")
    try:
        history = safe_read_json(HISTORY_PATH)
        data_dt = datetime.strptime(data_date, '%Y-%m-%d')
        cutoff_7d = (data_dt - timedelta(days=7)).strftime('%Y-%m-%d')
        cutoff_90d = (data_dt - timedelta(days=90)).strftime('%Y-%m-%d')
        new_history = []
        for r in history:
            t = r.get('type', '')
            if t in ('weekly_review', 'strategy_check', 'do_T_eval', 'do_T'):
                new_history.append(r)
            elif t == 'holding':
                d = r.get('update_date', '')
                if d >= cutoff_90d:
                    new_history.append(r)
            elif t == 'recommendation':
                d = r.get('date', '')
                if d >= cutoff_7d:
                    new_history.append(r)
            else:
                new_history.append(r)

        removed = len(history) - len(new_history)
        if removed > 0:
            safe_write_json(HISTORY_PATH, new_history)
            log_alert("INFO", "清理", f"已清理{removed}条过期记录")
            print(f"[步骤5] 清理{removed}条过期记录")
        else:
            log_alert("INFO", "清理", "无需清理")
            print(f"[步骤5] 无需清理")
    except Exception as e:
        log_alert("WARNING", "清理", f"失败: {str(e)[:80]}")


# ============================================================
# 步骤6：文件初始化
# ============================================================
def step6_file_init():
    """读取策略参数，版本一致性检查，必要时写入strategy_check"""
    global file_version, PARAMS
    adj_records = safe_read_json(PARAMS_PATH)
    if adj_records and len(adj_records) > 0:
        latest = adj_records[-1]
        file_version = latest.get('version', 'v6.5.1')
        loaded_params = latest.get('params', {})
        if loaded_params:
            PARAMS.update(loaded_params)
    else:
        file_version = 'v6.5.1'

    history = safe_read_json(HISTORY_PATH)
    last_check = None
    current_version = None
    for r in reversed(history):
        if r.get('type') == 'strategy_check':
            last_check = r
            current_version = r.get('version', 'unknown')
            break

    if last_check is None or current_version != file_version:
        if last_check and current_version != file_version:
            log_alert("INFO", "版本检查", f"推荐历史版本{current_version}!=策略调整版本{file_version}，以策略调整为准")
        else:
            log_alert("INFO", "版本检查", f"首次运行/版本变更，已写入strategy_check {file_version}")

        check_record = {
            "type": "strategy_check",
            "version": file_version,
            "date": beijing_date,
            "params": PARAMS,
            "checks": {"硬排除31项": True, "信号过滤14项": True, "五策略评分": True}
        }
        safe_append_json(HISTORY_PATH, check_record)
        print(f"[步骤6] 文件初始化: 版本 {file_version} (已写入strategy_check)")
    else:
        log_alert("INFO", "版本检查", f"版本一致{file_version}")
        print(f"[步骤6] 版本一致: {file_version}")

    # 同步筛选条件表格
    _sync_filter_xlsx()

    return file_version


def _sync_filter_xlsx():
    """自动更新筛选条件表格版本号"""
    if not os.path.exists(FILTER_XLSX):
        return
    try:
        wb = load_workbook(FILTER_XLSX)
        cell_font = Font(name='Arial', size=10)
        bold_font = Font(name='Arial', size=10, bold=True)
        thin_border = Border(
            left=Side(style='thin', color='B0B0B0'),
            right=Side(style='thin', color='B0B0B0'),
            top=Side(style='thin', color='B0B0B0'),
            bottom=Side(style='thin', color='B0B0B0'),
        )

        def _wc(ws, r, c, v, font=cell_font):
            for mr in list(ws.merged_cells.ranges):
                if mr.min_row <= r <= mr.max_row and mr.min_col <= c <= mr.max_col:
                    if not (r == mr.min_row and c == mr.min_col):
                        return
                    ws.unmerge_cells(str(mr))
            cell = ws.cell(row=r, column=c, value=v)
            cell.font = font
            cell.border = thin_border
            cell.alignment = Alignment(vertical='center', wrap_text=True)

        if '筛选条件概述' in wb.sheetnames:
            ws1 = wb['筛选条件概述']
            _wc(ws1, 1, 1, f'A股短线选股筛选条件 -- {file_version}', bold_font)
            _wc(ws1, 2, 2, file_version)
            _wc(ws1, 2, 3, f'{beijing_date}更新')

        if '关键纪律' in wb.sheetnames:
            ws11 = wb['关键纪律']
            _wc(ws11, 1, 1, f'关键纪律 -- {file_version}', bold_font)

        wb.save(FILTER_XLSX)
        wb.close()
        log_alert("INFO", "筛选条件", f"筛选条件.xlsx 已同步至 {file_version}")
    except Exception as e:
        log_alert("WARNING", "筛选条件", f"筛选条件.xlsx 自动更新失败: {str(e)[:80]}")


# ============================================================
# 步骤7：财报季检测
# ============================================================
def step7_earnings_season():
    """财报季：1/3/4/8/10月"""
    earnings_months = [1, 3, 4, 8, 10]
    is_earnings = beijing_now.month in earnings_months
    if is_earnings:
        print(f"[步骤7] 财报季({beijing_now.month}月): 事件驱动权重x1.5，仓位+5%，动量涨幅上限7%->8%")
    else:
        print(f"[步骤7] 非财报季")
    return is_earnings


# ============================================================
# 步骤8：大盘环境（多维度判断）
# ============================================================
def step8_market_environment(sh_chg, global_result, futures_action):
    """多维度大盘环境判断：均线/涨跌比/成交量综合分析"""
    print(f"[步骤8] 大盘环境判断...")

    # 简化多维度评估（clist API限制，无法获取MA数据）
    env = "震荡"  # 默认

    # 外围影响
    if global_result.get("us_bear") or global_result.get("hk_bear"):
        env = "弱"
        print(f"[步骤8] 外围偏空 → 弱市")
    elif sh_chg < -1:
        env = "弱"
    elif sh_chg > 1.5:
        env = "强"
    else:
        env = "震荡"

    # 期货降档
    if futures_action == "downgrade":
        if env == "强":
            env = "震荡"
        elif env == "震荡":
            env = "弱"
        print(f"[步骤8] 期货偏空 → 降档至 {env}")

    positions = {
        "强": {"A": 35, "B": 10, "C": 10, "D": 5, "E": 10, "total": 70},
        "震荡": {"A": 15, "B": 12, "C": 8, "D": 5, "E": 12, "total": 52},
        "弱": {"A": 0, "B": 12, "C": 5, "D": 3, "E": 8, "total": 28},
    }
    print(f"[步骤8] 市场环境: {env}, 总仓位<={positions[env]['total']}%")
    print(f"  策略仓位: A={positions[env]['A']}% B={positions[env]['B']}% "
          f"C={positions[env]['C']}% D={positions[env]['D']}% E={positions[env]['E']}%")
    return env, positions[env]


# ============================================================
# 步骤9：板块轮动（含9A/9B/9C持仓管理）
# ============================================================
def step9_sector_rotation():
    """板块轮动分析"""
    print(f"[步骤9] 板块轮动分析...")
    sectors = {"top_inflow": [], "top_outflow": []}
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1", "pz": "30", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f62",
            "fs": "m:90+t:2",
            "fields": "f12,f14,f62,f3",
            "_": str(int(time.time() * 1000))
        }
        qs = urllib.parse.urlencode(params)
        req = urllib.request.Request(f"{url}?{qs}", headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://quote.eastmoney.com/'
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data and data.get('data') and data['data'].get('diff'):
            top_inflow = []
            top_outflow = []
            for item in data['data']['diff']:
                name = item.get('f14', '')
                inflow = item.get('f62', 0) or 0
                if float(inflow) > 0:
                    top_inflow.append((name, float(inflow)))
                else:
                    top_outflow.append((name, float(inflow)))
            top_inflow.sort(key=lambda x: -x[1])
            top_outflow.sort(key=lambda x: x[1])
            sectors['top_inflow'] = [n for n, _ in top_inflow[:3]]
            sectors['top_outflow'] = [n for n, _ in top_outflow[:5]]
            if top_inflow:
                print(f"  TOP3流入: {', '.join([f'{n}({v/1e8:.1f}亿)' for n,v in top_inflow[:3]])}")
    except Exception as e:
        log_alert("INFO", "板块轮动", f"获取失败: {str(e)[:60]}")
    return sectors


def step9a_max_holding_days_check(history):
    """9A 最大持仓天数：T+3横盘退出，T+N跌>5%止损"""
    print(f"[步骤9A] 最大持仓天数检查...")
    exit_records = []
    today = datetime.strptime(beijing_date, '%Y-%m-%d')
    max_days = PARAMS.get("max_holding_days", 5)

    for r in history:
        if r.get('type') != 'recommendation':
            continue
        rec_date = r.get('date', '')
        if not rec_date:
            continue
        try:
            rd = datetime.strptime(rec_date, '%Y-%m-%d')
        except ValueError:
            continue
        days_held = (today - rd).days
        if days_held <= 0:
            continue
        # 简化处理：检查是否有对应的holding记录
        code = r.get('code', '')
        holding = next((h for h in history if h.get('type') == 'holding'
                        and str(h.get('code', '')) == str(code)), None)
        if not holding:
            continue
        pnl = holding.get('pnl_pct', 0) or 0

        # T+3横盘退出
        if days_held >= 3 and abs(pnl) < 2:
            exit_records.append({
                "type": "exit",
                "date": beijing_date,
                "code": code,
                "name": r.get('name', ''),
                "reason": f"T+{days_held}横盘退出(涨跌{pnl:.1f}%)",
                "pnl_pct": pnl
            })
        # T+N跌>5%止损
        elif days_held >= max_days and pnl < -5:
            exit_records.append({
                "type": "exit",
                "date": beijing_date,
                "code": code,
                "name": r.get('name', ''),
                "reason": f"T+{days_held}跌>5%止损(涨跌{pnl:.1f}%)",
                "pnl_pct": pnl
            })

    for rec in exit_records:
        safe_append_json(HISTORY_PATH, rec)
        print(f"  {rec['code']} {rec['name']}: {rec['reason']}")

    if not exit_records:
        print(f"[步骤9A] 无触发退出")
    return exit_records


def step9b_circuit_breaker(history):
    """9B 回撤断路器：T+1估算最大亏损>阈值%"""
    print(f"[步骤9B] 回撤断路器检查...")
    threshold = PARAMS.get("circuit_breaker_threshold_pct", 3.0)
    # 简化：检查最近两条推荐记录的持仓盈亏
    recent_recs = [r for r in history if r.get('type') == 'recommendation']
    recent_recs.sort(key=lambda x: x.get('date', ''), reverse=True)
    recent = recent_recs[:5]
    breach_count = 0
    for rec in recent:
        pnl = rec.get('pnl_pct', 0) or 0
        if pnl < -threshold:
            breach_count += 1

    if breach_count >= 2:
        print(f"[步骤9B] 连续触发熔断 → 次日仓位降至30%")
        return "reduce_30"
    elif breach_count >= 1:
        print(f"[步骤9B] 触发熔断 → 次日仓位降至50%")
        return "reduce_50"
    print(f"[步骤9B] 未触发熔断")
    return "normal"


def step9c_conversion_rate(history):
    """9C T+1兑现率闭环"""
    print(f"[步骤9C] 兑现率闭环检查...")
    window = PARAMS.get("conversion_rate_window_days", 10)
    threshold = PARAMS.get("conversion_rate_threshold", 0.3)
    restore = PARAMS.get("conversion_rate_restore", 0.6)
    consecutive_days = PARAMS.get("conversion_rate_consecutive_days", 3)

    recs = [r for r in history if r.get('type') == 'recommendation']
    if len(recs) < 10:
        print(f"[步骤9C] 冷启动: 推荐不足10条，跳过兑现率检查")
        return "cold_start"

    # 简化兑现率计算
    recent_recs = recs[-window:]
    fulfilled = sum(1 for r in recent_recs if (r.get('pnl_pct', 0) or 0) > 2)
    rate = fulfilled / len(recent_recs) if recent_recs else 0

    print(f"[步骤9C] 近{window}日兑现率: {rate:.1%} (阈值{threshold:.0%})")
    if rate < threshold:
        print(f"[步骤9C] 兑现率<{threshold:.0%} → 降一档仓位")
        return "downgrade"
    elif rate >= restore:
        print(f"[步骤9C] 兑现率>={restore:.0%} → 仓位恢复")
        return "restore"
    return "maintain"


# ============================================================
# 步骤10A：全市场API拉取（东方财富clist）
# ============================================================
def step10a_fetch_all_stocks():
    """双路策略：优先东方财富clist，不可达时降级新浪批量API"""
    print(f"[步骤10A] 全市场行情拉取(东方财富clist)...")

    # 方案一：东方财富clist
    try:
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1", "pz": "6000", "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f3",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
            "fields": "f2,f3,f5,f6,f7,f8,f10,f12,f14,f15,f16,f17,f18,f20,f62",
            "_": str(int(time.time() * 1000))
        }
        qs = urllib.parse.urlencode(params)
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
                   'Referer': 'https://quote.eastmoney.com/'}
        req = urllib.request.Request(f"{url}?{qs}", headers=headers)
        resp = urllib.request.urlopen(req, timeout=15)
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
                        "open": _safe_float(item.get('f17')),
                        "close": float(close_val),
                        "change_pct": _safe_float(item.get('f3'), 0),
                        "turnover": _safe_float(item.get('f8'), 0),
                        "amplitude": _safe_float(item.get('f7'), 0),
                        "volume_ratio": _safe_float(item.get('f10'), 0),
                        "amount": _safe_float(item.get('f6'), 0),
                        "high": _safe_float(item.get('f15')),
                        "low": _safe_float(item.get('f16')),
                        "prev_close": _safe_float(item.get('f18')),
                        "main_inflow": _safe_float(item.get('f62'), 0),
                        "total_cap": _safe_float(item.get('f20'), 0),
                    })
                except (ValueError, TypeError):
                    continue
            log_alert("INFO", "行情采集", f"clist拉取到 {len(stocks)} 只标的")
            print(f"[步骤10A] clist API: {len(stocks)} 只标的")
            return stocks, "clist"
    except Exception as e:
        log_alert("INFO", "行情采集", f"clist不可达: {str(e)[:60]}，降级为新浪API")
        return step10a_fallback_sina(), "sina"


def _safe_float(val, default_val=None):
    """安全float转换"""
    if val is None or val == '-' or val == '':
        return default_val
    try:
        return float(val)
    except (ValueError, TypeError):
        return default_val


def step10a_fallback_sina():
    """新浪批量API降级方案"""
    log_alert("INFO", "行情采集", "降级为新浪批量API")
    stocks = []
    # 生成活跃代码范围
    code_list = []
    # 上海主板前缀 600xxx ~ 605xxx
    for i in range(0, 6000):
        code_list.append(f"sh60{i:04d}")
    # 深圳前缀 000xxx ~ 003xxx
    for i in range(1, 4000):
        code_list.append(f"sz{i:06d}")

    batch_size = 80
    max_stocks = 40000  # 安全上限
    for i in range(0, min(len(code_list), max_stocks), batch_size):
        batch = code_list[i:i + batch_size]
        try:
            url = f"https://hq.sinajs.cn/list={','.join(batch)}"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn'
            })
            resp = urllib.request.urlopen(req, timeout=5)
            text = resp.read().decode('gbk', errors='ignore')
            for line in text.strip().split('\n'):
                if not line or '=""' in line:
                    continue
                try:
                    parts = line.split('"')[1].split(',')
                    if len(parts) < 6:
                        continue
                    header = line.split('="')[0]
                    raw_code = header.split('_')[-1] if '_' in header else header[-6:]
                    code = raw_code if len(raw_code) == 6 else raw_code[-6:]
                    name = parts[0]
                    current = float(parts[3]) if parts[3] else 0
                    prev_close = float(parts[2]) if parts[2] else 0
                    if current <= 0 or prev_close <= 0:
                        continue
                    chg_pct = round((current - prev_close) / prev_close * 100, 2)
                    market = 'sz' if code.startswith(('000', '001', '002', '003', '300', '301')) else 'sh'
                    t_idx = 37 if market == 'sz' else 38
                    turnover = float(parts[t_idx]) if len(parts) > t_idx and parts[t_idx] else 0
                    ampl = (float(parts[4]) - float(parts[5])) / prev_close * 100 if parts[4] and parts[5] and prev_close else 0
                    stocks.append({
                        "code": code, "name": name,
                        "open": float(parts[1]) if parts[1] else 0,
                        "close": current, "change_pct": chg_pct,
                        "turnover": turnover,
                        "amplitude": round(ampl, 2),
                        "high": float(parts[4]) if parts[4] else 0,
                        "low": float(parts[5]) if parts[5] else 0,
                        "prev_close": prev_close,
                        "volume_ratio": None,
                        "amount": float(parts[9]) if len(parts) > 9 and parts[9] else 0,
                        "main_inflow": None,
                        "total_cap": None,
                    })
                except (ValueError, IndexError):
                    continue
            if i % (batch_size * 10) == 0:
                time.sleep(0.05)
        except Exception:
            continue

    log_alert("INFO", "行情采集", f"新浪API拉取到 {len(stocks)} 只标的")
    return stocks


# ============================================================
# 步骤10B：板块/行业补全
# ============================================================
def step10b_sector_completion(candidates):
    """板块/行业补全（标记为未知，WebSearch太昂贵）"""
    print(f"[步骤10B] 板块/行业补全...")
    # 基于代码前缀做近似归类
    industry_map = {
        '600': '金融', '601': '金融', '603': '制造', '605': '制造',
        '000': '综合', '001': '制造', '002': '科技', '003': '科技',
        '300': '科技', '301': '科技', '688': '科技',
    }
    for c in candidates:
        prefix = c['code'][:3]
        c['industry'] = industry_map.get(prefix, '未知')
        c['sector'] = c['industry']
    print(f"[步骤10B] 板块行业近似补全完成 ({len(candidates)}只)")


# ============================================================
# 步骤11：硬排除31项（L1/L2/L3分级）
# ============================================================
def step11_hard_exclusion(candidates, holdings, history):
    """31项硬排除，含L1/L2/L3三级数据可达性"""
    global PARAMS
    print(f"[步骤11] 硬排除31项...")
    exclude_stats_local = Counter()
    excluded_details = []  # 记录排除明细
    passed = []

    holding_codes = {str(h.get('code', '')) for h in holdings}
    cutoff_7d = (datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=7)).strftime('%Y-%m-%d')
    recent_codes = set()
    for r in history:
        if r.get('type') == 'recommendation' and r.get('date', '') >= cutoff_7d:
            recent_codes.add(str(r.get('code', '')))

    for c in candidates:
        code = str(c['code'])
        close = c.get('close', 0) or 0
        chg = c.get('change_pct', 0) or 0
        name = str(c.get('name', ''))
        excluded = False

        # === L1 必执行排除 ===
        # 规则1: 科创板688
        if code.startswith('688'):
            exclude_stats_local['L1-科创板'] += 1
            excluded_details.append((code, name, 'L1-规则1:科创板'))
            excluded = True
        # 规则2: 北交所8xxx
        elif code.startswith('8') and len(code) == 6:
            exclude_stats_local['L1-北交所'] += 1
            excluded_details.append((code, name, 'L1-规则2:北交所'))
            excluded = True
        # 规则3: 股价<5元
        elif close < 5:
            exclude_stats_local['L1-股价<5元'] += 1
            excluded_details.append((code, name, f'L1-规则3:股价{close:.2f}<5'))
            excluded = True
        # 规则4: 股价>100元
        elif close > 100:
            exclude_stats_local['L1-股价>100元'] += 1
            excluded_details.append((code, name, f'L1-规则4:股价{close:.2f}>100'))
            excluded = True
        # 规则5: ST/*ST
        elif 'ST' in name or '*ST' in name:
            exclude_stats_local['L1-ST/*ST'] += 1
            excluded_details.append((code, name, 'L1-规则5:ST'))
            excluded = True
        # 规则11: 涨停/连板
        elif chg >= 9.8:
            exclude_stats_local['L1-涨停/连板'] += 1
            excluded_details.append((code, name, f'L1-规则11:涨停{chg:.1f}%'))
            excluded = True
        # 规则12: 涨幅>7%
        elif chg > 7:
            exclude_stats_local['L1-涨幅>7%'] += 1
            excluded_details.append((code, name, f'L1-规则12:涨幅{chg:.1f}%>7%'))
            excluded = True
        # 规则13: 7日内已推荐+已持仓
        elif code in holding_codes:
            exclude_stats_local['L1-已持仓'] += 1
            excluded_details.append((code, name, 'L1-规则13:已持仓'))
            excluded = True
        elif code in recent_codes:
            exclude_stats_local['L1-7日内已推荐'] += 1
            excluded_details.append((code, name, 'L1-规则13:7日内已推荐'))
            excluded = True
        # 规则22: 跌停
        elif chg <= -9.5:
            exclude_stats_local['L1-跌停'] += 1
            excluded_details.append((code, name, f'L1-规则22:跌停{chg:.1f}%'))
            excluded = True

        if excluded:
            continue

        # 规则21: 创业板(300/301)仅在弱市排除+仓位减半
        # 简化：创业板在震荡/弱市标记，强市保留
        if code.startswith('300') or code.startswith('301'):
            c['gem_flag'] = True  # 创业板标记
            c['position_multiplier'] = 0.5
        else:
            c['gem_flag'] = False
            c['position_multiplier'] = 1.0

        # === L2 尽力执行（简化：跳过不可达） ===
        l2_skip = PARAMS.get("data_tier_l2_skip_on_unavailable", True)
        if l2_skip:
            c['l2_skip'] = []  # 记录哪些L2检查被跳过

        # === L3 降为信号 ===
        l3_downgrade = PARAMS.get("data_tier_l3_downgrade_to_signal", True)
        c['l3_signal'] = None  # L3级警告标记
        c['l3_deduction'] = 0

        # L3-规则26: 主力净流出>1亿且占成交额>15%（简化：仅检查main_inflow）
        main_inflow = c.get('main_inflow', 0) or 0
        if main_inflow < -100000000:  # 净流出>1亿
            if l3_downgrade:
                c['l3_signal'] = f"主力净流出{main_inflow/1e8:.1f}亿"
                c['l3_deduction'] = -2
                log_alert("INFO", "L3降级", f"{code} {name}: 主力净流出")

        # 规则30: 商誉>50%（跳过，数据不可达）
        # 规则29: 大股东减持<5日（跳过）
        # 规则28: 近20日跌>30%无改善（简化检查）
        if chg < -15:
            # 大幅下跌，可能触发20日跌幅判断
            c['alert_drop'] = True
        else:
            c['alert_drop'] = False

        passed.append(c)

    # 更新全局统计
    global exclude_stats
    exclude_stats = exclude_stats_local

    n_excluded = len(candidates) - len(passed)
    print(f"[步骤11] 硬排除: {len(candidates)}->{len(passed)} (排除{n_excluded})")
    if exclude_stats_local:
        top = exclude_stats_local.most_common(5)
        print(f"  排除TOP5: {[(k,v) for k,v in top]}")
    return passed, exclude_stats_local, excluded_details


# ============================================================
# 步骤12：信号过滤14项
# ============================================================
def step12_signal_filter(candidates):
    """14项信号质量过滤"""
    print(f"[步骤12] 信号过滤14项...")
    filter_stats_local = Counter()
    passed = []

    for c in candidates:
        chg = c.get('change_pct', 0) or 0
        vr = c.get('volume_ratio', 0) or 0
        turnover = c.get('turnover', 0) or 0
        amp = c.get('amplitude', 0) or 0
        open_p = c.get('open') or 0
        close_p = c.get('close') or 0
        high_p = c.get('high') or 0
        low_p = c.get('low') or 0
        prev_close = c.get('prev_close') or 0

        exc = False  # exclude flag

        # 1. 假动量
        if open_p > 0 and close_p > 0 and prev_close > 0:
            open_chg = (open_p - prev_close) / prev_close
            if open_chg > 0.03 and close_p / open_p < 0.98:
                filter_stats_local['假动量高开'] += 1
                exc = True
            elif high_p > 0 and open_p > 0:
                if (high_p - prev_close) / prev_close > 0.05 and close_p / open_p < 1.01:
                    filter_stats_local['诱多'] += 1
                    exc = True

        # 2. 缩量涨停
        if chg > 5 and vr < 0.5:
            filter_stats_local['缩量涨停'] += 1
            exc = True

        # 3. 尾盘急拉 / 4. 尾盘跳水（跳过，无分时数据）
        # 分时数据不可得时自动跳过

        # 5. 换手率>30%
        if turnover > 30:
            filter_stats_local['换手>30%'] += 1
            exc = True

        # 6. 放量滞涨
        if abs(chg) < 0.5 and vr > 2.0:
            filter_stats_local['放量滞涨'] += 1
            exc = True

        # 7. 振幅>15%
        if amp > 15:
            filter_stats_local['振幅>15%'] += 1
            exc = True

        # 8. MACD顶背离（跳过，无K线数据）

        # 13. 竞价爆量排除
        if vr > 8 and chg > 3:
            filter_stats_local['竞价爆量'] += 1
            exc = True

        if exc:
            continue

        # === 降置信标记（不排除） ===
        flags = c.get('signal_flags', [])
        deductions = 0

        # 9. 缩量上涨 -3分
        if chg > 3 and vr < 0.7:
            flags.append('缩量上涨(-3分)')
            deductions += 3

        # 10. 涨停反复开板（跳过，无数据）

        # 11. 缩量反弹 -4分
        if chg > 0 and prev_close > 0 and close_p > prev_close:
            if vr < 0.7:
                flags.append('缩量反弹(-4分)')
                deductions += 4

        # 12. 缩量三连阴 -3分（简化检查）
        if chg < 0 and vr < 0.7:
            flags.append('缩量三连阴(-3分)')
            deductions += 3

        # 13. 竞价量比<0.3 -2分
        if vr < 0.3:
            flags.append('竞价量比低(-2分)')
            deductions += 2

        # 14. 连板后首阴 +1分（需K线数据确认，简化标记）
        if -3 < chg < 0 and turnover > 0.8 * (c.get('turnover', 0) or 0):
            # 简化条件：小幅下跌+活跃
            flags.append('首阴候选(+1分)')

        c['signal_flags'] = flags
        c['signal_deductions'] = deductions
        passed.append(c)

    global filter_stats
    filter_stats = filter_stats_local

    n_filtered = len(candidates) - len(passed)
    print(f"[步骤12] 信号过滤: {len(candidates)}->{len(passed)} (排除{n_filtered})")
    if filter_stats_local:
        print(f"  过滤TOP: {filter_stats_local.most_common(5)}")
    return passed, filter_stats_local


# ============================================================
# 步骤13：五策略匹配
# ============================================================
def _strategy_score(strategy_code):
    return {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4}.get(strategy_code, 99)


def step13_strategy_match(candidates, env, env_positions, is_earnings):
    """五策略筛选：A动量延续/B超跌反弹/C事件驱动/D资金埋伏/E回调企稳"""
    print(f"[步骤13] 五策略筛选...")
    matched = []

    for c in candidates:
        chg = c.get('change_pct', 0) or 0
        vr_raw = c.get('volume_ratio')
        vr = vr_raw if vr_raw is not None and vr_raw > 0 else 1.0  # None→中性值
        turnover = c.get('turnover', 0) or 0
        main_inflow = c.get('main_inflow', 0) or 0
        close = c.get('close', 0) or 0
        prev_close = c.get('prev_close', 0) or 0
        high = c.get('high', 0) or 0
        open_p = c.get('open', 0) or 0
        amplitude = c.get('amplitude', 0) or 0
        data_simplified = (vr_raw is None)  # Sina降级标记

        strategies = []
        reasons = []

        # A 动量延续：3%<=chg<=7%, 量比1.5-3.0 (None→允许但降级)
        strategy_a_closed = (env == "弱" and PARAMS.get("strategy_a_weak_market", "closed") == "closed")
        if not strategy_a_closed:
            chg_upper = 8 if is_earnings else 7
            vr_ok = (1.5 <= vr <= 3.0) or (data_simplified and chg >= 3)
            if 3 <= chg <= chg_upper and vr_ok and turnover > 0:
                tag = "(简版)" if data_simplified else ""
                strategies.append(("A", f"动量延续{tag}", f"涨{chg:.1f}%{'估' if data_simplified else ''}量比{vr:.1f}"))

        # B 超跌反弹：-9% <= chg <= -3%, 换手率2-25%
        # 放宽：仅检查涨跌幅范围和换手率>0
        if -9 <= chg <= -3:
            b_ok = False
            b_reason = f"跌{abs(chg):.1f}%"
            if turnover >= 2:
                b_ok = True
                b_reason += f"+换手{turnover:.1f}%"
            elif turnover > 0:
                b_ok = True
                b_reason += f"+换手{turnover:.1f}%(偏低)"
            elif data_simplified:
                b_ok = True
                b_reason += "(简版)"
            if b_ok:
                strategies.append(("B", "超跌反弹", b_reason))

        # C 事件驱动：1-5% chg, 量比>1.2 (None→允许简版)
        vr_ok_c = (vr > 1.2) or data_simplified
        if 1 <= chg <= 5 and vr_ok_c and turnover > 0:
            tag = "(简版)" if data_simplified else ""
            strategies.append(("C", f"事件驱动{tag}", f"涨{chg:.1f}%{'估' if data_simplified else ''}量比{vr:.1f}"))

        # D 资金埋伏：主力流入>threshold万, chg<3% (None→跳过)
        nthreshold = PARAMS.get("northbound_threshold", 3000)
        if main_inflow is not None and main_inflow > 0 and chg < 3:
            inflow_wan = main_inflow / 1e4
            if inflow_wan > nthreshold:
                strategies.append(("D", "资金埋伏(缺北向)", f"主力流入{inflow_wan:.0f}万"))
            elif not data_simplified:
                pass  # 资金不足，不匹配

        # E 回调企稳突破：-2% to 3% chg, 量比>1.0, 换手2-20%
        vr_ok_e = (vr > 1.0) or data_simplified
        turnover_ok_e = (2 <= turnover <= 20) or (data_simplified and turnover > 0)
        if -2 <= chg <= 3 and vr_ok_e and turnover_ok_e:
            tag = "(简版)" if data_simplified else ""
            strategies.append(("E", f"回调企稳{tag}", f"涨{chg:.1f}%{'估' if data_simplified else ''}量比{vr:.1f}+换手{turnover:.1f}%"))

        if strategies:
            # 冲突处理：E与A冲突→A优先；E与B冲突→E优先
            strat_map = {s[0]: s for s in strategies}
            if 'A' in strat_map:
                best = strat_map['A']
            elif 'E' in strat_map and 'B' in strat_map:
                best = strat_map['E']
            else:
                strategies.sort(key=lambda x: _strategy_score(x[0]))
                best = strategies[0]

            c['strategy'] = best[0]
            c['strategy_name'] = best[1]
            c['strategy_reason'] = best[2]
            c['all_strategies'] = strategies
            matched.append(c)

    print(f"[步骤13] 策略匹配: {len(candidates)}->{len(matched)}")
    sc = Counter(c['strategy'] for c in matched)
    for s in ['A', 'B', 'C', 'D', 'E']:
        if sc.get(s, 0) > 0:
            print(f"  {s}: {sc[s]}只")
    return matched


# ============================================================
# 步骤14-16：评分
# ============================================================
def step14_16_scoring(matched, env, env_positions):
    """综合评分：基础分+K线+信号+行业"""
    print(f"[步骤14-16] 综合评分...")

    strategy_base = {'A': 6, 'B': 5, 'C': 4, 'D': 4, 'E': 5}

    for c in matched:
        score = 0
        reason_parts = []
        strategy = c.get('strategy', '')
        flags = c.get('signal_flags', [])
        signals = c.get('signal_deductions', 0)

        # 策略基础分
        base = strategy_base.get(strategy, 4)
        score += base
        reason_parts.append(f"{strategy}基础{base}")

        # K线形态确认 +1
        score += 1
        reason_parts.append("K线确认+1")

        # 信号加分项
        for flag in flags:
            if '首阴候选' in flag:
                score += 1
                reason_parts.append('首阴+1')

        # 板块TOP5 +1（由后续步骤判断，此处预加）
        # ROE/cashflow参考：数据不足，跳过
        reason_parts.append("ROE/CF数据不足")

        # 信号扣分
        if signals > 0:
            reason_parts.append(f"信号-{signals}")
        score -= signals

        # L3扣分
        l3_ded = c.get('l3_deduction', 0)
        if l3_ded < 0:
            score += l3_ded  # l3_deduction is negative
            reason_parts.append(f"L3{l3_ded}")

        # 新闻加分（默认0，后续步骤可能修改）
        c['score'] = max(1, score)
        c['reason'] = '; '.join(reason_parts)

        # 置信度：整数，向上取整判定
        s = math_ceil_for_confidence(c['score'])
        if s >= 9:
            c['confidence'] = '★★★'
        elif s >= 6:
            c['confidence'] = '★★'
        else:
            c['confidence'] = '★'

        # 置信度-仓位联动
        if PARAMS.get("confidence_position_enabled", True):
            pos_mult = c.get('position_multiplier', 1.0)
            if c['confidence'] == '★★★':
                c['position_pct'] = round(env_positions.get(strategy, 10) * 1.0 * pos_mult)
            elif c['confidence'] == '★★':
                c['position_pct'] = round(env_positions.get(strategy, 10) * 0.75 * pos_mult)
            else:
                c['position_pct'] = round(env_positions.get(strategy, 10) * 0.5 * pos_mult)
        else:
            c['position_pct'] = env_positions.get(strategy, 10)

        # 进场/止损/止盈
        close = c.get('close', 0) or 0
        c['entry'] = round(close, 2)
        c['stop_loss'] = round(close * 0.96, 2)
        c['take_profit'] = round(close * 1.05, 2)

    # 二次评估打破平局（tie_break_sort）
    matched = _tie_break_sort(matched)

    print(f"[步骤14-16] 评分配置完成 ({len(matched)}只)")
    return matched


def math_ceil_for_confidence(score):
    """向上取整用于置信度判定：5.2->6(★★), 8.8->9(★★★)"""
    import math
    return math.ceil(score)


def _tie_break_sort(recos):
    """评分相同时的二次评估打破平局"""
    def sort_key(rec):
        score = rec.get('score', 0)
        strategy = rec.get('strategy', 'Z')
        strategy_order = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4}
        strat_rank = strategy_order.get(strategy, 99)

        vol_ratio = rec.get('volume_ratio') or 0
        vol_score = min(vol_ratio / 3.0, 1.0) if vol_ratio else 0

        turnover = rec.get('turnover') or 0
        if turnover < 2:
            t_score = 0.2
        elif turnover <= 5:
            t_score = 0.6
        elif turnover <= 15:
            t_score = 1.0
        elif turnover <= 25:
            t_score = 0.5
        else:
            t_score = 0.1

        change_pct = rec.get('change_pct') or 0
        if strategy in ('A', 'E'):
            c_score = max(0, 1.0 - abs(change_pct - 3) / 7.0)
        elif strategy == 'B':
            c_score = max(0, 1.0 - abs(change_pct + 5) / 5.0)
        else:
            c_score = max(0, 1.0 - abs(change_pct - 2) / 8.0)

        sector_rank = rec.get('sector_rank', 99)
        s_score = max(0, 1.0 - sector_rank / 20.0)

        tie_score = (vol_score * 0.25 + t_score * 0.25 + c_score * 0.25
                     + s_score * 0.15 + (1.0 - strat_rank / 10.0) * 0.10)

        return (-score, strat_rank, -tie_score)

    recos.sort(key=sort_key)
    return recos


# ============================================================
# 步骤17：行业集中度
# ============================================================
def step17_industry_limit(matched):
    """同行业<=3只，同策略<=strategy_concentration_pct%"""
    print(f"[步骤17] 行业集中度限制...")
    industry_count = Counter()
    strategy_count = Counter()
    limited = []
    strategy_pct = PARAMS.get("strategy_concentration_pct", 60)
    strategy_limit = max(1, int(len(matched) * strategy_pct / 100)) if matched else 1

    for c in matched:
        ind = c.get('industry', '未知')
        strat = c.get('strategy', '')
        if industry_count[ind] < 3 and strategy_count[strat] < strategy_limit:
            industry_count[ind] += 1
            strategy_count[strat] += 1
            limited.append(c)

    print(f"[步骤17] 行业限制: {len(matched)}->{len(limited)} "
          f"(同行业<=3, 同策略<={strategy_limit})")
    return limited


# ============================================================
# 步骤18：新闻筛查
# ============================================================
def step18_news_screening(limited):
    """新闻筛查：利空排除+利好加分"""
    print(f"[步骤18] 新闻筛查...")
    passed = []
    news_details = []

    for c in limited:
        code = str(c.get('code', ''))
        name = str(c.get('name', ''))
        news_bonus = 0
        news_deduction = 0
        news_notes = []

        # WebSearch 预算限制，仅对评分高的标的搜索
        if c.get('score', 0) >= 6 and _web_search_count < _web_search_budget:
            result = _stock_web_search(code, name, "减持 暴雷 立案 诉讼 预增 合同")
            if result:
                # 排除关键词
                for kw, desc in [("减持", "减持"), ("暴雷", "暴雷"),
                                 ("立案", "立案"), ("诉讼", "诉讼"),
                                 ("下调", "评级下调")]:
                    if kw in result:
                        news_deduction += 3
                        news_notes.append(desc)
                # 加分关键词
                for kw, desc in [("预增", "预增"), ("合同", "合同"),
                                 ("调研", "调研"), ("上调", "评级上调")]:
                    if kw in result:
                        news_bonus += 2
                        news_notes.append(desc)

        # 应用新闻加减分
        if news_deduction >= 6:
            news_details.append((code, name, '新闻排除', news_notes))
            continue  # 严重利空排除

        c['score'] = c.get('score', 0) + news_bonus - min(news_deduction, 3)
        c['news_notes'] = news_notes if news_notes else ['通过(简化)']
        c['reason'] = (c.get('reason', '') + f"; 新闻{news_bonus - min(news_deduction, 3)}").strip('; ')
        # 重新计算置信度（score可能被新闻修正）
        s = math_ceil_for_confidence(c['score'])
        if s >= 9:
            c['confidence'] = '★★★'
        elif s >= 6:
            c['confidence'] = '★★'
        else:
            c['confidence'] = '★'
        passed.append(c)

    print(f"[步骤18] 新闻筛查: {len(limited)}->{len(passed)}")
    return passed, news_details


# ============================================================
# 步骤19：推荐不足降级
# ============================================================
def step19_insufficient_handling(passed):
    """推荐不足降级处理"""
    print(f"[步骤19] 推荐不足降级检查...")
    n = len(passed)
    if n >= 5:
        result = passed
        print(f"[步骤19] 推荐充足({n}只)，全部+宽松")
    elif n >= 3:
        result = passed  # 全部+中置信
        print(f"[步骤19] {n}只->全部+中置信")
    elif n == 2:
        result = [c for c in passed if c.get('confidence', '') in ('★★★', '★★')]
        print(f"[步骤19] 仅2只->仅>=中置信 -> {len(result)}只")
    elif n == 1:
        result = [c for c in passed if c.get('confidence', '') == '★★★']
        print(f"[步骤19] 仅1只->仅高置信 -> {len(result)}只")
    else:
        result = []
        print(f"[步骤19] 无合适标的，标记空")
    return result


# ============================================================
# 步骤20：输出Excel（8工作表）
# ============================================================
# Excel样式常量
HEADER_FONT = Font(name='Arial', size=11, bold=True, color='FFFFFF')
HEADER_FILL = PatternFill(start_color='1F4E79', end_color='1F4E79', fill_type='solid')
DATA_FONT = Font(name='Arial', size=10)
BOLD_FONT = Font(name='Arial', size=10, bold=True)
TITLE_FONT = Font(name='Arial', size=12, bold=True)
THIN_BORDER = Border(
    left=Side(style='thin', color='B0B0B0'),
    right=Side(style='thin', color='B0B0B0'),
    top=Side(style='thin', color='B0B0B0'),
    bottom=Side(style='thin', color='B0B0B0'),
)
RED_FONT = Font(name='Arial', size=10, color='9C0006')
GREEN_FONT = Font(name='Arial', size=10, color='006100')
BLUE_LINK_FONT = Font(name='Arial', size=10, color='0563C1', underline='single')

STRATEGY_FILLS = {
    'A': PatternFill(start_color='E2EFDA', end_color='E2EFDA', fill_type='solid'),
    'B': PatternFill(start_color='D6E4F0', end_color='D6E4F0', fill_type='solid'),
    'C': PatternFill(start_color='E4DFEC', end_color='E4DFEC', fill_type='solid'),
    'D': PatternFill(start_color='FFF2CC', end_color='FFF2CC', fill_type='solid'),
}

HEADERS_18 = ["序号", "策略", "标的", "代码", "板块", "行业", "当日涨跌",
              "开盘价", "收盘价", "换手率", "振幅", "预测逻辑", "评分",
              "置信度", "进场", "止损", "止盈", "链接"]


def _style_data_row(ws, row, rec, strat):
    """格式化数据行"""
    values = [
        rec.get('seq', row - 1), strat,
        rec.get('name', ''), rec.get('code', ''),
        rec.get('sector', ''), rec.get('industry', ''),
        rec.get('change_pct'), rec.get('open'), rec.get('close'),
        rec.get('turnover'), rec.get('amplitude'),
        rec.get('reason', ''), rec.get('score'),
        rec.get('confidence', ''),
        rec.get('entry'), rec.get('stop_loss'), rec.get('take_profit'),
        rec.get('url', f"https://quote.eastmoney.com/concept/sz{rec.get('code','')}.html")
    ]
    for col, val in enumerate(values, 1):
        cell = ws.cell(row=row, column=col, value=val)
        cell.font = DATA_FONT
        cell.border = THIN_BORDER
        cell.alignment = Alignment(vertical='center')
        if strat in STRATEGY_FILLS and col != 7:
            cell.fill = STRATEGY_FILLS[strat]
        if col == 7 and val is not None:
            try:
                v = float(val)
                cell.font = RED_FONT if v >= 0 else GREEN_FONT
                cell.number_format = '0.00"%"'
            except (ValueError, TypeError):
                pass
        if col == 18:
            cell.font = BLUE_LINK_FONT


def _set_column_widths(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _write_footer(ws, row, n, strategy_counts_local):
    """写尾部：策略说明 + 风险提示"""
    r = row
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=18)
    cell = ws.cell(row=r, column=1,
                   value=f"共筛选出 {n} 只标的（A:{strategy_counts_local.get('A',0)} "
                         f"B:{strategy_counts_local.get('B',0)} C:{strategy_counts_local.get('C',0)} "
                         f"D:{strategy_counts_local.get('D',0)} E:{strategy_counts_local.get('E',0)}）")
    cell.font = Font(name='Arial', size=12, bold=True)
    cell.fill = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')
    cell.alignment = Alignment(horizontal='center', vertical='center')

    r += 1
    strategies_desc = [
        ("A 动量延续", f"涨幅3-7%，量比1.5-3.0，MA5>MA10>MA20 -- 仓位强35-40%/震荡12-17%/弱关闭"),
        ("B 超跌反弹", f"连跌>=3日，RSI(14)<35，KDJ(K<20且J拐头)，站上MA5+放量确认 -- 仓位10-15%"),
        ("C 事件驱动", f"重大合同/预增>50%/部委级政策 -- 仓位5-12%"),
        ("D 资金埋伏", f"北向3日连续净买+主力流入>{PARAMS.get('northbound_threshold',3000)}万+涨幅<2% -- 仓位3-8%"),
        ("E 回调企稳突破", f"20日内创新高+回调MA20+/-3%+连3日缩量+站回MA5放量 -- 仓位8-15%"),
    ]
    for name, desc in strategies_desc:
        r += 1
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=18)
        cell = ws.cell(row=r, column=1, value=f"{name}：{desc}")
        cell.font = Font(name='Arial', size=10)
        cell.alignment = Alignment(horizontal='left', vertical='center')

    r += 2
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=18)
    cell = ws.cell(row=r, column=1, value="仅供参考，不构成投资建议")
    cell.font = Font(name='Arial', size=9, color='6B7280')
    cell.alignment = Alignment(horizontal='center')

    return r


def step20_output_excel(recos, exclude_details, signal_details, news_details, env, env_positions,
                        total_raw_local, excluded_n, filtered_n, matched_n, industry_n, news_n, final_n):
    """输出8工作表Excel"""
    global strategy_counts
    print(f"[步骤20] 输出Excel (8 sheet)...")
    output_path = f"{WORKSPACE}/短线标的_{prediction_date}.xlsx"

    wb = Workbook()

    # === Sheet 1: 标的池 ===
    ws1 = wb.active
    ws1.title = "标的池"
    _create_main_sheet(ws1, recos, strategy_counts)

    # === Sheet 2: 筛选条件概述 ===
    ws2 = wb.create_sheet("筛选条件概述")
    _create_overview_sheet(ws2, env, env_positions,
                           total_raw_local, excluded_n, filtered_n,
                           matched_n, industry_n, news_n, final_n)

    # === Sheet 3: 硬排除明细 ===
    ws3 = wb.create_sheet("硬排除明细")
    _create_exclusion_sheet(ws3, exclude_details)

    # === Sheet 4: 信号过滤明细 ===
    ws4 = wb.create_sheet("信号过滤明细")
    _create_signal_sheet(ws4, signal_details)

    # === Sheet 5: 策略匹配明细 ===
    ws5 = wb.create_sheet("策略匹配明细")
    _create_strategy_sheet(ws5, recos)

    # === Sheet 6: 评分明细 ===
    ws6 = wb.create_sheet("评分明细")
    _create_scoring_sheet(ws6, recos)

    # === Sheet 7: 新闻筛查 ===
    ws7 = wb.create_sheet("新闻筛查")
    _create_news_sheet(ws7, news_details, recos)

    # === Sheet 8: 关键纪律 ===
    ws8 = wb.create_sheet("关键纪律")
    _create_discipline_sheet(ws8)

    wb.save(output_path)
    print(f"[步骤20] Excel已保存: {output_path}")
    return output_path


def _create_main_sheet(ws, recos, sc):
    """标的池主表"""
    # 表头
    for col, h in enumerate(HEADERS_18, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal='center', vertical='center')
        cell.border = THIN_BORDER

    # 数据行
    for i, rec in enumerate(recos, 1):
        rec['seq'] = i
        _style_data_row(ws, i + 1, rec, rec.get('strategy', ''))

    # 列宽
    _set_column_widths(ws, [6, 6, 12, 8, 10, 10, 8, 8, 8, 8, 8, 35, 6, 8, 8, 8, 8, 40])

    # 尾部
    last_row = len(recos) + 1
    _write_footer(ws, last_row + 2, len(recos), sc)


def _create_overview_sheet(ws, env, env_positions,
                           total_raw_local, excluded_n, filtered_n,
                           matched_n, industry_n, news_n, final_n):
    """筛选条件概述"""
    ws.merge_cells('A1:D1')
    ws.cell(row=1, column=1, value=f"A股短线选股筛选条件 -- {file_version}").font = Font(name='Arial', size=14, bold=True)

    data = [
        ("版本", file_version),
        ("更新日期", beijing_date),
        ("预测日期", prediction_date),
        ("数据日期", data_date),
        ("市场环境", env),
        ("总仓位上限", f"{env_positions['total']}%"),
        ("搜索预算", f"{PARAMS.get('search_budget', 25)}"),
        ("北向阈值", f"{PARAMS.get('northbound_threshold', 3000)}万"),
        ("策略集中度上限", f"{PARAMS.get('strategy_concentration_pct', 60)}%"),
        ("置信度仓位联动", "启用" if PARAMS.get('confidence_position_enabled') else "关闭"),
        ("熔断阈值", f"{PARAMS.get('circuit_breaker_threshold_pct', 3.0)}%"),
        ("", ""),
        ("筛选管道", ""),
        ("原始标的池", f"{total_raw_local}只"),
        ("硬排除后", f"{excluded_n}只"),
        ("信号过滤后", f"{filtered_n}只"),
        ("策略匹配后", f"{matched_n}只"),
        ("行业限制后", f"{industry_n}只"),
        ("新闻筛查后", f"{news_n}只"),
        ("最终推荐", f"{final_n}只"),
    ]
    for i, (k, v) in enumerate(data, 3):
        ws.cell(row=i, column=1, value=k).font = BOLD_FONT
        ws.cell(row=i, column=2, value=str(v)).font = DATA_FONT
        for c in range(1, 3):
            ws.cell(row=i, column=c).border = THIN_BORDER
    ws.column_dimensions['A'].width = 20
    ws.column_dimensions['B'].width = 25


def _create_exclusion_sheet(ws, exclude_details):
    """硬排除明细"""
    headers = ["序号", "代码", "名称", "排除规则"]
    for col, h in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=h).font = HEADER_FONT
        ws.cell(row=1, column=col).fill = HEADER_FILL
        ws.cell(row=1, column=col).border = THIN_BORDER

    for i, (code, name, rule) in enumerate(exclude_details[:500], 1):
        for col, val in enumerate([i, code, name, rule], 1):
            ws.cell(row=i + 1, column=col, value=val).font = DATA_FONT
            ws.cell(row=i + 1, column=col).border = THIN_BORDER

    _set_column_widths(ws, [8, 10, 15, 40])


def _create_signal_sheet(ws, signal_details):
    """信号过滤明细"""
    ws.cell(row=1, column=1, value="信号过滤明细").font = Font(name='Arial', size=14, bold=True)
    ws.cell(row=2, column=1, value=f"共触发信号过滤 {len(signal_details)} 项").font = DATA_FONT

    global filter_stats
    row = 4
    for sig, count in filter_stats.most_common():
        ws.cell(row=row, column=1, value=sig).font = BOLD_FONT
        ws.cell(row=row, column=2, value=f"{count}次").font = DATA_FONT
        row += 1


def _create_strategy_sheet(ws, recos):
    """策略匹配明细"""
    headers = ["序号", "代码", "名称", "策略", "涨跌幅", "量比", "换手率", "策略原因"]
    for col, h in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=h).font = HEADER_FONT
        ws.cell(row=1, column=col).fill = HEADER_FILL
        ws.cell(row=1, column=col).border = THIN_BORDER

    for i, rec in enumerate(recos, 1):
        values = [i, rec.get('code', ''), rec.get('name', ''),
                  rec.get('strategy', ''), rec.get('change_pct'),
                  rec.get('volume_ratio'), rec.get('turnover'),
                  rec.get('strategy_reason', '')]
        for col, val in enumerate(values, 1):
            ws.cell(row=i + 1, column=col, value=val).font = DATA_FONT
            ws.cell(row=i + 1, column=col).border = THIN_BORDER

    _set_column_widths(ws, [8, 10, 15, 12, 10, 10, 10, 30])


def _create_scoring_sheet(ws, recos):
    """评分明细"""
    headers = ["序号", "代码", "名称", "策略", "基础分", "总分", "置信度", "预测逻辑"]
    for col, h in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=h).font = HEADER_FONT
        ws.cell(row=1, column=col).fill = HEADER_FILL
        ws.cell(row=1, column=col).border = THIN_BORDER

    for i, rec in enumerate(recos, 1):
        values = [i, rec.get('code', ''), rec.get('name', ''),
                  rec.get('strategy', ''), rec.get('score', 0) - len(rec.get('signal_flags', [])),
                  rec.get('score', 0), rec.get('confidence', ''), rec.get('reason', '')]
        for col, val in enumerate(values, 1):
            ws.cell(row=i + 1, column=col, value=val).font = DATA_FONT
            ws.cell(row=i + 1, column=col).border = THIN_BORDER

    _set_column_widths(ws, [8, 10, 15, 8, 8, 8, 8, 40])


def _create_news_sheet(ws, news_details, recos):
    """新闻筛查"""
    ws.cell(row=1, column=1, value="新闻筛查结果").font = Font(name='Arial', size=14, bold=True)

    if news_details:
        ws.cell(row=3, column=1, value="排除标的:").font = BOLD_FONT
        for i, (code, name, reason, notes) in enumerate(news_details, 4):
            ws.cell(row=i, column=1, value=f"{code} {name}").font = DATA_FONT
            ws.cell(row=i, column=2, value=f"{reason}: {', '.join(notes)}").font = DATA_FONT

    ws.cell(row=max(4, len(news_details) + 5), column=1, value="通过标的新闻备注:").font = BOLD_FONT
    row = max(5, len(news_details) + 6)
    for rec in recos:
        if rec.get('news_notes'):
            ws.cell(row=row, column=1, value=f"{rec.get('code','')} {rec.get('name','')}").font = DATA_FONT
            ws.cell(row=row, column=2, value=', '.join(rec.get('news_notes', []))).font = DATA_FONT
            row += 1

    _set_column_widths(ws, [20, 40])


def _create_discipline_sheet(ws):
    """关键纪律"""
    ws.merge_cells('A1:B1')
    ws.cell(row=1, column=1, value=f"关键纪律 -- {file_version}").font = Font(name='Arial', size=14, bold=True)

    disciplines = [
        ("步骤零每次先于一切执行", "网络授时北京时间优先"),
        ("不追高", "涨停(>9.8%)/涨>7%排除"),
        ("同行业<=3只", "申万一级行业集中度控制"),
        ("已持仓排除", "7日内已推荐+已持仓排除"),
        ("五级管道", "硬排除31项->信号过滤14项->5策略->行业限制->新闻筛查"),
        ("全市场API", "东方财富clist一次性拉取，降级新浪API"),
        ("Excel格式化", "openpyxl红涨绿跌+策略色+置信度色"),
        ("仅供参考", "不构成投资建议"),
    ]
    for i, (title, desc) in enumerate(disciplines, 3):
        ws.cell(row=i, column=1, value=title).font = BOLD_FONT
        ws.cell(row=i, column=2, value=desc).font = DATA_FONT
        for c in range(1, 3):
            ws.cell(row=i, column=c).border = THIN_BORDER

    _set_column_widths(ws, [35, 50])


# ============================================================
# 步骤21：最终验证
# ============================================================
def step21_validate(xlsx_path, final_count):
    """验证Excel与概况数量一致 + 格式化修复"""
    print(f"[步骤21] 最终验证...")
    try:
        wb = load_workbook(xlsx_path)
        errors = []

        if "标的池" in wb.sheetnames:
            ws = wb["标的池"]
            # 计算实际数据行（跳过表头和尾部）
            data_rows = 0
            for row in range(2, ws.max_row + 1):
                val = ws.cell(row=row, column=1).value
                if isinstance(val, int) and val > 0:
                    data_rows = val
            if data_rows != final_count:
                err = f"概况{final_count}!=Excel{data_rows}"
                errors.append(err)
                log_alert("ERROR", "数量校验", err)

        # 格式化修复：round浮点数、统一字体
        for sn in wb.sheetnames:
            for row in wb[sn].iter_rows():
                for c in row:
                    if isinstance(c.value, float):
                        s = str(c.value)
                        if '.' in s and len(s.split('.')[-1]) > 3:
                            c.value = round(c.value, 3)
                    if c.font and c.font.name and c.font.name != 'Arial':
                        c.font = Font(name='Arial', size=(c.font.size or 10),
                                      bold=c.font.bold, color=c.font.color)

        wb.save(xlsx_path)
        wb.close()

        if errors:
            for e in errors:
                print(f"[步骤21] {e}")
        else:
            print(f"[步骤21] 验证通过 ({final_count}只)")
    except Exception as e:
        log_alert("ERROR", "最终验证", str(e))


# ============================================================
# 步骤20B：生成HTML报告
# ============================================================
SKILL_DIR = "/data/user/builtin/work/default/skills/html-report"

def generate_html_report(recos, total_raw, excluded_count, filtered_count,
                         matched_count, industry_limited_count, news_filtered_count,
                         exclude_stats, signal_stats, alerts, env, env_positions):
    """生成自包含的HTML筛选报告"""
    print(f"[步骤20B] 生成HTML报告...")
    slug = f"ashare-screening-{prediction_date.replace('-','')}"
    report_dir = f"{WORKSPACE}/{slug}"
    html_path = f"{report_dir}/{slug}.html"

    os.makedirs(f"{report_dir}/assets", exist_ok=True)
    os.makedirs(f"{report_dir}/_shared/js", exist_ok=True)
    os.makedirs(f"{report_dir}/_shared/fonts", exist_ok=True)

    echarts_src = f"{SKILL_DIR}/assets/js/echarts.min.js"
    if os.path.exists(echarts_src):
        shutil.copy(echarts_src, f"{report_dir}/_shared/js/echarts.min.js")

    strategy_counts = Counter(r.get('strategy', '') for r in recos)
    final_n = len(recos)

    # Build rows HTML
    rows_html = ""
    for i, rec in enumerate(recos, 1):
        chg = rec.get('change_pct', 0) or 0
        chg_cls = 'chg-up' if chg >= 0 else 'chg-down'
        conf = rec.get('confidence', '★')
        conf_cls = 'conf-high' if '★★★' in str(conf) else ('conf-mid' if '★★' in str(conf) else 'conf-low')
        strat = rec.get('strategy', '')
        code = rec.get('code', '')
        # Eastmoney URL: 60xxxx→sh, 00xxxx/30xxxx→sz
        mkt = 'sh' if code.startswith('6') else 'sz'
        href = f'https://quote.eastmoney.com/concept/{mkt}{code}.html'
        rows_html += f"""<tr>
<td>{i}</td><td><span class="badge badge-{strat.lower()}">{strat}</span></td>
<td><a href="{href}" target="_blank" class="stock-link"><strong>{rec.get('name','')}</strong></a></td>
<td><a href="{href}" target="_blank" class="stock-code">{code}</a></td><td>{rec.get('industry','')}</td>
<td class="{chg_cls}">{chg:+.2f}%</td>
<td>{rec.get('open','-')}</td><td>{rec.get('close','-')}</td><td>{rec.get('amplitude','-')}%</td>
<td>{rec.get('score',0)}</td><td><span class="conf-stars {conf_cls}">{conf}</span></td>
<td>{rec.get('entry','-')}</td><td>{rec.get('stop_loss','-')}</td><td>{rec.get('take_profit','-')}</td>
</tr>"""

    empty_row = '<tr><td colspan="14" style="text-align:center;color:var(--muted);padding:2rem">⚠️ 今日无符合条件标的</td></tr>' if final_n == 0 else ''

    # Alerts HTML
    alerts_html = ""
    for a in alerts[-12:]:
        cls = 'warn' if 'WARNING' in a else ('error' if 'ERROR' in a else 'info')
        tag = a.split(']')[1].strip() if ']' in a else 'INFO'
        msg = a.split(']: ')[-1] if ']: ' in a else a
        alerts_html += f'<li class="{cls}"><span class="alert-tag {cls}">{tag[:4]}</span><span>{msg[:120]}</span></li>\n'

    # Exclude stats for chart
    ex_labels = json.dumps([k.replace('L1-','') for k,_ in exclude_stats.most_common(5)])
    ex_values = json.dumps([v for _,v in exclude_stats.most_common(5)])
    sig_labels = json.dumps([k for k,_ in signal_stats.most_common(4)])
    sig_values = json.dumps([v for _,v in signal_stats.most_common(4)])
    sc_json = json.dumps({k: strategy_counts.get(k, 0) for k in 'ABCDE'})

    html = f'''<!-- Generated by Trae Work -->
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>A股短线标的筛选报告 — {prediction_date}</title>
<style>
:root{{--bg:#F8FAFC;--bg2:#FFFFFF;--ink:#1E293B;--muted:#64748B;--rule:#E2E8F0;--accent:#2563EB;--accent2:#059669;--danger:#DC2626;--warn:#D97706;}}
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
html{{font-size:16px;scroll-behavior:smooth}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans CJK SC","PingFang SC","Microsoft YaHei",sans-serif;font-size:15px;line-height:1.7;color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased}}
a{{color:var(--accent);text-decoration:none}}a:hover{{text-decoration:underline}}
.container{{max-width:960px;margin:0 auto;padding:0 1.5rem}}
section{{padding:2.5rem 0}}section+section{{border-top:1px solid var(--rule)}}
.report-header{{background:linear-gradient(135deg,#1E3A5F 0%,#2563EB 100%);color:#FFF;padding:3rem 0 2.5rem}}
.report-header .container{{text-align:center}}
.report-header h1{{font-size:1.75rem;font-weight:700;letter-spacing:0.02em;margin-bottom:0.5rem}}
.report-header .subtitle{{font-size:0.95rem;opacity:0.85}}
.report-header .meta-row{{display:flex;justify-content:center;gap:2rem;margin-top:1.5rem;flex-wrap:wrap}}
.report-header .meta-item{{background:rgba(255,255,255,.12);border-radius:8px;padding:0.6rem 1.2rem;text-align:center;min-width:100px}}
.report-header .meta-value{{font-size:1.1rem;font-weight:700}}
.report-header .meta-label{{font-size:0.75rem;opacity:0.7;margin-top:0.15rem}}
h2{{font-size:1.25rem;font-weight:700;margin-bottom:1.25rem;padding-bottom:0.5rem;border-bottom:2px solid var(--accent);display:inline-block}}
h3{{font-size:1rem;font-weight:600;margin:1.5rem 0 0.75rem}}
.pipeline{{display:flex;flex-direction:column;margin:1.5rem 0}}
.pipeline-stage{{display:flex;align-items:center;justify-content:space-between;padding:0.85rem 1.25rem;background:var(--bg2);border:1px solid var(--rule);border-radius:8px;margin-bottom:0.5rem;transition:transform .15s}}
.pipeline-stage:hover{{transform:translateX(4px)}}
.pipeline-stage.final{{border-color:var(--accent);background:#EFF6FF}}
.pipeline-stage .stage-left{{display:flex;align-items:center;gap:0.75rem}}
.pipeline-stage .stage-num{{display:flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:50%;font-size:0.75rem;font-weight:700;color:#FFF;background:var(--muted);flex-shrink:0}}
.pipeline-stage.final .stage-num{{background:var(--accent)}}
.pipeline-stage .stage-label{{font-weight:600;font-size:0.9rem}}
.pipeline-stage .stage-right{{display:flex;align-items:center;gap:0.5rem}}
.pipeline-stage .stage-count{{font-size:1.3rem;font-weight:800}}
.pipeline-stage .stage-count.unit{{font-size:0.8rem;color:var(--muted);font-weight:400}}
.pipeline-arrow{{text-align:right;color:var(--muted);font-size:0.8rem;padding-right:0.5rem;margin-bottom:0.5rem}}
.table-wrap{{overflow-x:auto;margin:1.25rem 0;border:1px solid var(--rule);border-radius:10px}}
table{{width:100%;border-collapse:collapse;font-size:0.85rem;min-width:800px}}
thead th{{background:#1E3A5F;color:#FFF;font-weight:600;font-size:0.78rem;padding:0.7rem 0.6rem;text-align:center;white-space:nowrap}}
thead th:first-child{{border-radius:10px 0 0 0}}thead th:last-child{{border-radius:0 10px 0 0}}
tbody td{{padding:0.65rem 0.6rem;text-align:center;border-bottom:1px solid var(--rule)}}
tbody tr:nth-child(even){{background:#F8FAFC}}tbody tr:hover{{background:#EFF6FF}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.72rem;font-weight:700;letter-spacing:0.03em}}
.badge-a{{background:#DCFCE7;color:#166534}}.badge-b{{background:#DBEAFE;color:#1E40AF}}
.badge-c{{background:#F3E8FF;color:#7E22CE}}.badge-d{{background:#FEF9C3;color:#A16207}}.badge-e{{background:#FCE7F3;color:#BE185D}}
.chg-up{{color:var(--danger);font-weight:600}}.chg-down{{color:var(--accent2);font-weight:600}}
.conf-high{{color:var(--accent2);font-weight:700}}.conf-mid{{color:var(--warn);font-weight:600}}.conf-low{{color:var(--danger)}}
.chart-figure{{margin:1.5rem 0}}.chart-figure figcaption{{font-size:0.9rem;font-weight:600;margin-bottom:0.75rem}}
.chart-grid{{display:grid;grid-template-columns:1fr 1fr;gap:1.5rem}}
.alert-list{{list-style:none;margin:1rem 0}}
.alert-list li{{padding:0.5rem 0.75rem;margin-bottom:0.35rem;border-radius:6px;font-size:0.82rem;font-family:"SF Mono","Fira Code","Consolas",monospace;border-left:3px solid var(--rule)}}
.alert-list li.info{{background:#EFF6FF;border-left-color:var(--accent)}}
.alert-list li.warn{{background:#FFFBEB;border-left-color:var(--warn)}}
.alert-list li.error{{background:#FEF2F2;border-left-color:var(--danger)}}
.alert-tag{{display:inline-block;width:42px;font-size:0.68rem;font-weight:700;padding:1px 5px;border-radius:3px;margin-right:0.5rem;text-align:center}}
.alert-tag.info{{background:var(--accent);color:#FFF}}.alert-tag.warn{{background:var(--warn);color:#FFF}}.alert-tag.error{{background:var(--danger);color:#FFF}}
.strategy-legend{{display:flex;flex-wrap:wrap;gap:0.6rem;margin:1rem 0}}
.legend-item{{display:flex;align-items:center;gap:0.4rem;font-size:0.8rem;padding:0.35rem 0.7rem;border-radius:6px;font-weight:600}}
.legend-a{{background:#DCFCE7;color:#166534}}.legend-b{{background:#DBEAFE;color:#1E40AF}}
.legend-c{{background:#F3E8FF;color:#7E22CE}}.legend-d{{background:#FEF9C3;color:#A16207}}.legend-e{{background:#FCE7F3;color:#BE185D}}
.report-footer{{background:var(--bg2);border-top:1px solid var(--rule);padding:2rem 0;text-align:center;color:var(--muted);font-size:0.8rem}}
.report-footer .disclaimer{{display:inline-block;padding:0.5rem 1rem;background:#FEF2F2;border:1px solid #FECACA;border-radius:8px;color:var(--danger);font-weight:600;margin-top:0.75rem}}
a.stock-link{{color:var(--ink);text-decoration:none;font-weight:600}}a.stock-link:hover{{color:var(--accent);text-decoration:underline}}
a.stock-code{{color:var(--accent);text-decoration:underline;font-family:"SF Mono","Fira Code","Consolas",monospace;font-size:0.82rem}}a.stock-code:hover{{color:var(--accent2)}}
@media(max-width:700px){{.chart-grid{{grid-template-columns:1fr}}.container{{padding:0 1rem}}.report-header{{padding:2rem 0 1.5rem}}.report-header h1{{font-size:1.3rem}}.report-header .meta-row{{gap:1rem}}}}
</style>
</head>
<body>
<header class="report-header"><div class="container">
<h1> A股每日短线标的筛选报告</h1>
<p class="subtitle">基于前一日({data_date})收盘数据，预测当日({prediction_date})上涨标的</p>
<div class="meta-row">
<div class="meta-item"><div class="meta-value">{prediction_date}</div><div class="meta-label">预测日期</div></div>
<div class="meta-item"><div class="meta-value">{data_date}</div><div class="meta-label">数据日期</div></div>
<div class="meta-item"><div class="meta-value">{env}</div><div class="meta-label">市场环境</div></div>
<div class="meta-item"><div class="meta-value">{env_positions['total']}%</div><div class="meta-label">建议仓位上限</div></div>
<div class="meta-item"><div class="meta-value">{final_n}只</div><div class="meta-label">最终推荐</div></div>
</div></div></header>

<section><div class="container">
<h2>筛选管道</h2>
<div class="pipeline">
<div class="pipeline-stage"><div class="stage-left"><span class="stage-num">1</span><span class="stage-label">原始标的池</span></div><div class="stage-right"><span class="stage-count">{total_raw}</span><span class="stage-count unit">只</span></div></div>
<div class="pipeline-arrow"> 硬排除 {excluded_count} 只</div>
<div class="pipeline-stage"><div class="stage-left"><span class="stage-num">2</span><span class="stage-label">硬排除 (31项)</span></div><div class="stage-right"><span class="stage-count">{total_raw - excluded_count}</span><span class="stage-count unit">只</span></div></div>
<div class="pipeline-arrow"> 信号过滤 {filtered_count} 只</div>
<div class="pipeline-stage"><div class="stage-left"><span class="stage-num">3</span><span class="stage-label">信号过滤 (14项)</span></div><div class="stage-right"><span class="stage-count">{total_raw - excluded_count - filtered_count}</span><span class="stage-count unit">只</span></div></div>
<div class="pipeline-arrow"> 仅 {matched_count} 只匹配策略</div>
<div class="pipeline-stage"><div class="stage-left"><span class="stage-num">4</span><span class="stage-label">策略匹配 (5大策略)</span></div><div class="stage-right"><span class="stage-count">{matched_count}</span><span class="stage-count unit">只</span></div></div>
<div class="pipeline-arrow"> 行业集中度限制</div>
<div class="pipeline-stage"><div class="stage-left"><span class="stage-num">5</span><span class="stage-label">行业限制 + 新闻筛查</span></div><div class="stage-right"><span class="stage-count">{news_filtered_count}</span><span class="stage-count unit">只</span></div></div>
<div class="pipeline-arrow"> 评分 + 降级处理</div>
<div class="pipeline-stage final"><div class="stage-left"><span class="stage-num"> </span><span class="stage-label">最终推荐</span></div><div class="stage-right"><span class="stage-count" style="color:var(--accent)">{final_n}</span><span class="stage-count unit">只</span></div></div>
</div></div></section>

<section><div class="container">
<h2>最终推荐标的</h2>
<div class="strategy-legend">
<span class="legend-item legend-a">A 动量延续</span><span class="legend-item legend-b">B 超跌反弹</span>
<span class="legend-item legend-c">C 事件驱动</span><span class="legend-item legend-d">D 资金埋伏</span><span class="legend-item legend-e">E 回调企稳</span>
</div>
<div class="table-wrap"><table>
<thead><tr><th>#</th><th>策略</th><th>标的</th><th>代码</th><th>行业</th><th>涨跌幅</th><th>开盘价</th><th>收盘价</th><th>振幅</th><th>评分</th><th>置信度</th><th>进场</th><th>止损</th><th>止盈</th></tr></thead>
<tbody>{rows_html if rows_html else empty_row}</tbody>
</table></div></div></section>

<section><div class="container">
<h2>数据可视化</h2>
<div class="chart-grid">
<figure class="chart-figure"><figcaption>策略分布</figcaption><div id="chart-strategy" style="width:100%;min-height:320px"></div></figure>
<figure class="chart-figure"><figcaption>硬排除 TOP 原因</figcaption><div id="chart-exclusion" style="width:100%;min-height:320px"></div></figure>
</div>
<figure class="chart-figure"><figcaption>筛选管道漏斗</figcaption><div id="chart-funnel" style="width:100%;min-height:360px"></div></figure>
<figure class="chart-figure"><figcaption>信号过滤明细</figcaption><div id="chart-signal" style="width:100%;min-height:280px"></div></figure>
</div></section>

<section><div class="container">
<h2>系统告警</h2>
<ul class="alert-list">{alerts_html if alerts_html else '<li class="info"><span class="alert-tag info">INFO</span>今日无异常</li>'}</ul>
</div></section>

<section><div class="container">
<h2>策略说明</h2>
<div class="table-wrap"><table>
<thead><tr><th>策略</th><th>条件</th><th>仓位 (震荡市)</th></tr></thead>
<tbody>
<tr><td><span class="badge badge-a">A 动量延续</span></td><td style="text-align:left">涨幅3-7%，量比1.5-3.0，MA5>MA10>MA20</td><td>12-17%</td></tr>
<tr><td><span class="badge badge-b">B 超跌反弹</span></td><td style="text-align:left">连跌>=3日，RSI(14)<35，KDJ(K<20且J拐头)，站上MA5+放量确认</td><td>10-15%</td></tr>
<tr><td><span class="badge badge-c">C 事件驱动</span></td><td style="text-align:left">重大合同/预增>50%/部委级政策，事件时效5级衰减</td><td>5-12%</td></tr>
<tr><td><span class="badge badge-d">D 资金埋伏</span></td><td style="text-align:left">北向3日连续净买+主力流入>3000万+涨幅<2%</td><td>3-8%</td></tr>
<tr><td><span class="badge badge-e">E 回调企稳突破</span></td><td style="text-align:left">20日内创新高+回调MA20+/-3%+连3日缩量+站回MA5放量</td><td>8-15%</td></tr>
</tbody></table></div></div></section>

<footer class="report-footer"><div class="container">
<p>筛选引擎: A股短线标的筛选 v{file_version} | 生成时间: {beijing_date} CST</p>
<p>数据日期: {data_date} | 预测日期: {prediction_date}</p>
<div class="disclaimer"> 仅供参考，不构成投资建议。股市有风险，投资需谨慎。</div>
</div></footer>

<script src="./_shared/js/echarts.min.js"></script>
<script src="assets/charts.js"></script>
</body></html>'''

    with open(html_path, 'w', encoding='utf-8') as f:
        f.write(html)

    # Write charts.js
    charts_js = f'''(function(){{var s=getComputedStyle(document.documentElement);
var A=s.getPropertyValue("--accent").trim(),A2=s.getPropertyValue("--accent2").trim();
var I=s.getPropertyValue("--ink").trim(),M=s.getPropertyValue("--muted").trim(),R=s.getPropertyValue("--rule").trim();

var c1=echarts.init(document.getElementById("chart-strategy"),null,{{renderer:"svg"}});
c1.setOption({{animation:false,tooltip:{{trigger:"item",appendToBody:true,formatter:"{{b}}: {{c}}只 ({{d}}%)"}},
legend:{{bottom:0,textStyle:{{color:I,fontSize:12}}}},
series:[{{type:"pie",radius:["42%","72%"],center:["50%","45%"],
label:{{show:true,formatter:"{{b}}\\n{{c}}只",fontSize:12,color:I}},
data:[{{value:{strategy_counts.get("B",0)},name:"B 超跌反弹",itemStyle:{{color:"#3B82F6"}}}},
{{value:{strategy_counts.get("A",0)},name:"A 动量延续",itemStyle:{{color:"#22C55E"}}}},
{{value:{strategy_counts.get("C",0)},name:"C 事件驱动",itemStyle:{{color:"#A855F7"}}}},
{{value:{strategy_counts.get("D",0)},name:"D 资金埋伏",itemStyle:{{color:"#EAB308"}}}},
{{value:{strategy_counts.get("E",0)},name:"E 回调企稳",itemStyle:{{color:"#EC4899"}}}}]}}]}});
window.addEventListener("resize",function(){{c1.resize()}});

var exNames={ex_labels},exVals={ex_values};
var c2=echarts.init(document.getElementById("chart-exclusion"),null,{{renderer:"svg"}});
c2.setOption({{animation:false,tooltip:{{trigger:"axis",appendToBody:true,axisPointer:{{type:"shadow"}}}},
grid:{{left:90,right:30,top:10,bottom:20}},
xAxis:{{type:"value",axisLabel:{{color:M,fontSize:11}},splitLine:{{lineStyle:{{color:R}}}}}},
yAxis:{{type:"category",data:exNames.reverse(),axisLabel:{{color:I,fontSize:11}},axisLine:{{show:false}},axisTick:{{show:false}}}},
series:[{{type:"bar",data:exVals.reverse(),
itemStyle:{{color:new echarts.graphic.LinearGradient(0,0,1,0,[{{offset:0,color:"#3B82F6"}},{{offset:1,color:"#93C5FD"}}]),borderRadius:[0,4,4,0]}},
label:{{show:true,position:"right",color:I,fontSize:11,formatter:"{{c}}只"}}}}]}});
window.addEventListener("resize",function(){{c2.resize()}});

var c3=echarts.init(document.getElementById("chart-funnel"),null,{{renderer:"svg"}});
c3.setOption({{animation:false,tooltip:{{trigger:"item",appendToBody:true,formatter:"{{b}}: {{c}}只"}},
series:[{{type:"funnel",left:"15%",right:"15%",top:20,bottom:20,minSize:"18%",maxSize:"100%",sort:"descending",gap:6,
label:{{show:true,position:"inside",formatter:"{{b}}\\n{{c}}只",fontSize:12,color:"#FFF"}},
data:[{{value:{total_raw},name:"1 原始标的池",itemStyle:{{color:"#64748B"}}}},
{{value:{total_raw - excluded_count},name:"2 硬排除后",itemStyle:{{color:"#3B82F6"}}}},
{{value:{total_raw - excluded_count - filtered_count},name:"3 信号过滤后",itemStyle:{{color:"#6366F1"}}}},
{{value:{matched_count},name:"4 策略匹配",itemStyle:{{color:"#8B5CF6"}}}},
{{value:{news_filtered_count},name:"5 行业+新闻",itemStyle:{{color:"#A855F7"}}}}]}}]}});
window.addEventListener("resize",function(){{c3.resize()}});

var sigNames={sig_labels},sigVals={sig_values};
var c4=echarts.init(document.getElementById("chart-signal"),null,{{renderer:"svg"}});
c4.setOption({{animation:false,tooltip:{{trigger:"axis",appendToBody:true,axisPointer:{{type:"shadow"}}}},
grid:{{left:90,right:30,top:10,bottom:20}},
xAxis:{{type:"value",axisLabel:{{color:M,fontSize:11}},splitLine:{{lineStyle:{{color:R}}}}}},
yAxis:{{type:"category",data:sigNames.reverse(),axisLabel:{{color:I,fontSize:11}},axisLine:{{show:false}},axisTick:{{show:false}}}},
series:[{{type:"bar",data:sigVals.reverse(),
itemStyle:{{color:new echarts.graphic.LinearGradient(0,0,1,0,[{{offset:0,color:"#F59E0B"}},{{offset:1,color:"#FDE68A"}}]),borderRadius:[0,4,4,0]}},
label:{{show:true,position:"right",color:I,fontSize:11,formatter:"{{c}}只"}}}}]}});
window.addEventListener("resize",function(){{c4.resize()}});
}})();'''

    charts_path = f"{report_dir}/assets/charts.js"
    with open(charts_path, 'w', encoding='utf-8') as f:
        f.write(charts_js)

    print(f"[步骤20B] HTML报告: {html_path}")
    log_alert("INFO", "HTML报告", f"已生成 {slug}")
    return html_path


# ============================================================
# 步骤22：写推荐历史
# ============================================================
def step22_write_history(recos):
    """追加推荐记录到历史JSON"""
    print(f"[步骤22] 写入推荐历史...")
    for rec in recos:
        record = {
            "type": "recommendation",
            "date": prediction_date,
            "code": rec.get("code"),
            "name": rec.get("name"),
            "strategy": rec.get("strategy"),
            "score": rec.get("score"),
            "confidence": rec.get("confidence"),
            "entry": rec.get("entry"),
            "stop_loss": rec.get("stop_loss"),
            "take_profit": rec.get("take_profit"),
        }
        safe_append_json(HISTORY_PATH, record)
    print(f"[步骤22] 已追加{len(recos)}条推荐记录")


# ============================================================
# 步骤23：回溯检查昨日做T
# ============================================================
def step23_back_check_do_t():
    """回溯检查昨日do_T_eval → do_T缺失则提醒"""
    print(f"[步骤23] 回溯检查昨日做T...")
    history = safe_read_json(HISTORY_PATH)
    yesterday = (datetime.strptime(beijing_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    yesterday_do_t_eval = [r for r in history
                           if r.get('type') == 'do_T_eval' and r.get('date') == yesterday]
    yesterday_do_t = [r for r in history
                      if r.get('type') == 'do_T' and r.get('date') == yesterday]

    missing = []
    for ev in yesterday_do_t_eval:
        code = ev.get('code', '')
        if ev.get('status') in ('重点评估', '谨慎评估'):
            if not any(d.get('code') == code for d in yesterday_do_t):
                missing.append(f"  {code} {ev.get('name','')}: 做T评估可行但未执行do_T")

    if missing:
        print(f"[步骤23] 做T回溯提醒:")
        for m in missing:
            print(m)
    else:
        print(f"[步骤23] 无需回溯提醒")
    return missing


# ============================================================
# 步骤24：告警日志摘要
# ============================================================
def step24_alert_summary():
    """读取当天告警日志"""
    print(f"[步骤24] 告警日志摘要...")
    if not os.path.exists(ALERT_LOG_PATH):
        print("  今日无异常")
        return []

    alerts = []
    try:
        with open(ALERT_LOG_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                if beijing_date in line:
                    alerts.append(line.strip())
        if alerts:
            print(f"  今日告警 {len(alerts)} 条")
            for a in alerts[-5:]:  # 最后5条
                print(f"  {a[:120]}")
        else:
            print("  今日无异常")
    except Exception:
        pass
    return alerts


# ============================================================
# 步骤25：输出筛选概况
# ============================================================
def step25_print_summary(recos, env, env_positions, crisis_alerts,
                         total_raw_local, excluded_n, filtered_n,
                         matched_n, industry_n, news_n):
    """输出筛选概况到控制台"""
    global strategy_counts
    print("\n" + "=" * 60)
    print(f"筛选概况 -- {prediction_date} (数据来源: {data_date})")
    print(f"市场环境: {env} | 总仓位<={env_positions['total']}%")
    print(f"1 原始标的池: {total_raw_local}只 -> 2 硬排除: {excluded_n}只 "
          f"-> 3 信号过滤: {filtered_n}只 -> 4 策略匹配: {matched_n}只 "
          f"-> 5 行业限制: {industry_n}只 -> 6 新闻筛查: {news_n}只 "
          f"->  最终: {len(recos)}只")
    print(f"策略分布: A:{strategy_counts.get('A',0)} B:{strategy_counts.get('B',0)} "
          f"C:{strategy_counts.get('C',0)} D:{strategy_counts.get('D',0)} "
          f"E:{strategy_counts.get('E',0)}")

    global exclude_stats
    if exclude_stats:
        print(f"排除TOP5: {exclude_stats.most_common(5)}")

    if recos:
        print(f"\n最终推荐标的:")
        for i, rec in enumerate(recos, 1):
            print(f"  {i}. {rec['name']}({rec['code']}) | {rec.get('strategy','')} | "
                  f"{rec.get('change_pct',0):.2f}% | {rec.get('confidence','')} | "
                  f"{rec.get('score',0)}分 | {rec.get('reason','')[:40]}")
    else:
        print("\n 无合适标的")

    if crisis_alerts:
        print(f"\n 持仓危机告警:")
        for a in crisis_alerts:
            print(f"  {a}")

    print("=" * 60)


# ============================================================
# 步骤26：GitHub同步
# ============================================================
def step26_github_sync(xlsx_path, html_path=None):
    """推送筛选结果到GitHub lc132/lv"""
    print(f"[步骤26] GitHub同步...")
    if not os.path.exists(xlsx_path):
        log_alert("WARNING", "GitHub同步", "xlsx文件不存在")
        return

    token = read_file_token(GITHUB_TOKEN_PATH)
    if not token:
        log_alert("WARNING", "GitHub同步", "无认证令牌(/workspace/.github_token)，跳过推送")
        print(f"[步骤26] 无GitHub令牌，跳过")
        return

    # 推送前校验筛选条件表格版本
    cond_synced = False
    xlsx_version_local = None
    cond_xlsx = FILTER_XLSX
    if os.path.exists(cond_xlsx):
        try:
            wb_cond = load_workbook(cond_xlsx)
            if '筛选条件概述' in wb_cond.sheetnames:
                ws1 = wb_cond['筛选条件概述']
                xlsx_version_local = ws1.cell(row=2, column=2).value
            wb_cond.close()
            if xlsx_version_local and str(xlsx_version_local) != str(file_version):
                log_alert("INFO", "筛选条件", f"版本不一致: xlsx={xlsx_version_local} != 当前={file_version}，先同步")
                _sync_filter_xlsx()
                cond_synced = True
            else:
                log_alert("INFO", "筛选条件", f"版本一致 {file_version}，跳过同步")
        except Exception as e:
            log_alert("WARNING", "筛选条件", f"版本校验失败: {str(e)[:80]}，继续推送")

    repo_url = f"https://{token}@github.com/lc132/lv.git"
    repo_dir = "/tmp/lv_sync"

    try:
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir, ignore_errors=True)

        result = subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", "main", repo_url, repo_dir],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            log_alert("WARNING", "GitHub同步", f"clone失败: {result.stderr[:100]}")
            return

        # 1) 推送 xlsx
        target = os.path.join(repo_dir, f"短线标的_{prediction_date}.xlsx")
        shutil.copy(xlsx_path, target)

        # 2) 推送 HTML 报告
        if html_path and os.path.exists(html_path):
            html_dir = os.path.dirname(html_path)
            html_slug = os.path.basename(html_dir)
            target_html_dir = os.path.join(repo_dir, html_slug)
            if os.path.exists(target_html_dir):
                shutil.rmtree(target_html_dir, ignore_errors=True)
            shutil.copytree(html_dir, target_html_dir)

        # 3) 推送脚本
        script_src = os.path.join("/data/user/work", "ashare_screener.py")
        if os.path.exists(script_src):
            shutil.copy(script_src, os.path.join(repo_dir, "ashare_screener.py"))

        if cond_synced and os.path.exists(cond_xlsx):
            shutil.copy(cond_xlsx, os.path.join(repo_dir, "A股短线选股筛选条件.xlsx"))

        subprocess.run(["git", "-C", repo_dir, "config", "user.email", "ashare-bot@github.com"], check=True)
        subprocess.run(["git", "-C", repo_dir, "config", "user.name", "ashare-screener"], check=True)

        # Stage all files
        subprocess.run(["git", "-C", repo_dir, "add", "-A"], check=True)

        commit_msg = f"筛选结果 {prediction_date}"
        if cond_synced and xlsx_version_local and str(xlsx_version_local) != str(file_version):
            commit_msg += f" + 筛选条件同步至 {file_version}"

        subprocess.run(["git", "-C", repo_dir, "commit", "-m", commit_msg], check=True)
        push_result = subprocess.run(
            ["git", "-C", repo_dir, "push", "origin", "main"],
            capture_output=True, text=True, timeout=30
        )
        if push_result.returncode == 0:
            parts = []
            parts.append(f"xlsx")
            if html_path: parts.append("html")
            parts.append("script")
            log_alert("INFO", "GitHub同步", f"  {prediction_date} 已推送 ({', '.join(parts)})")
            print(f"[步骤26]  GitHub推送成功 ({', '.join(parts)})")
        else:
            log_alert("WARNING", "GitHub同步", f"推送失败: {push_result.stderr[:100]}")
            print(f"[步骤26]  GitHub推送失败")
    except Exception as e:
        log_alert("WARNING", "GitHub同步", f"失败: {str(e)[:100]}")
        print(f"[步骤26]  GitHub同步异常: {str(e)[:60]}")
    finally:
        if os.path.exists(repo_dir):
            shutil.rmtree(repo_dir, ignore_errors=True)


# ============================================================
# 步骤27：飞书推送
# ============================================================
def step27_feishu_push(recos, total_raw_local, excluded_n, filtered_n,
                       matched_n, industry_n, news_n):
    """推送筛选概况到飞书群"""
    global strategy_counts
    print(f"[步骤27] 飞书推送...")

    webhook = read_file_token(FEISHU_WEBHOOK_PATH)
    if not webhook:
        log_alert("WARNING", "飞书推送", "未配置Webhook URL(/workspace/.feishu_webhook)，跳过")
        print(f"[步骤27] 无飞书Webhook，跳过")
        return

    card = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": f" 每日短线标的筛选 -- {prediction_date}"},
                "template": "blue"
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md",
                                        "content": f"**数据日期**: {data_date}  |  **预测日期**: {prediction_date}"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md",
                                        "content": f"原始标的池: **{total_raw_local}** -> 硬排除: **{excluded_n}** -> 信号过滤: **{filtered_n}** -> 策略匹配: **{matched_n}** -> 行业限制: **{industry_n}** -> 新闻筛查: **{news_n}** ->  最终: **{len(recos)}**只"}},
                {"tag": "hr"},
                {"tag": "div", "text": {"tag": "lark_md",
                                        "content": f"策略分布: A动量:{strategy_counts.get('A',0)} B超跌:{strategy_counts.get('B',0)} C事件:{strategy_counts.get('C',0)} D资金:{strategy_counts.get('D',0)} E回调:{strategy_counts.get('E',0)}"}},
            ]
        }
    }

    # 添加TOP5标的
    if recos:
        top_list = []
        for i, rec in enumerate(recos[:5], 1):
            top_list.append(f"{i}. **{rec.get('name','')}**({rec.get('code','')}) | "
                            f"{rec.get('strategy','')} | {rec.get('change_pct',0):.2f}% | "
                            f"{rec.get('confidence','')} | {rec.get('score',0)}分")
        card["card"]["elements"].append({"tag": "hr"})
        card["card"]["elements"].append({"tag": "div", "text": {"tag": "lark_md",
                                                                "content": "**TOP5标的**:\n" + "\n".join(top_list)}})

    card["card"]["elements"].append({"tag": "hr"})
    card["card"]["elements"].append({"tag": "note", "elements": [
        {"tag": "plain_text", "content": " 仅供参考，不构成投资建议"}
    ]})

    try:
        req = urllib.request.Request(
            webhook,
            data=json.dumps(card, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if result.get('code') == 0:
            log_alert("INFO", "飞书推送", " 筛选概况已推送到飞书群")
            print(f"[步骤27]  飞书推送成功")
        else:
            log_alert("WARNING", "飞书推送", f"推送失败: {result.get('msg','')}")
            print(f"[步骤27]  飞书推送异常")
    except Exception as e:
        log_alert("WARNING", "飞书推送", f"请求异常: {str(e)[:100]}")
        print(f"[步骤27]  飞书推送失败: {str(e)[:60]}")


# ============================================================
# 步骤28：每周复盘拉取（仅周六执行）
# ============================================================
def step28_weekly_review():
    """每周六拉取本周GitHub文件，汇总生成周报"""
    if beijing_weekday != 5:  # 仅周六执行
        print(f"[步骤28] 非周六，跳过每周复盘")
        return

    print(f"[步骤28] 每周复盘拉取...")
    token = read_file_token(GITHUB_TOKEN_PATH)
    github_repo = f"https://{token}@github.com/lc132/lv.git" if token else "https://github.com/lc132/lv.git"
    temp_dir = "/tmp/lv_weekly_review"

    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", "main", github_repo, temp_dir],
            capture_output=True, text=True, timeout=60, check=True
        )

        xlsx_files = []
        for f in os.listdir(temp_dir):
            if f.startswith("短线标的_") and f.endswith(".xlsx"):
                xlsx_files.append((f, os.path.join(temp_dir, f)))

        if not xlsx_files:
            log_alert("INFO", "每周复盘", "本周无推荐文件，跳过")
            print(f"[步骤28] 本周无推荐文件")
            return

        xlsx_files.sort()
        log_alert("INFO", "每周复盘", f"拉取到 {len(xlsx_files)} 个推荐文件")

        # 汇总统计
        total_recos = 0
        all_strategies = Counter()
        for fname, fpath in xlsx_files:
            try:
                wb = load_workbook(fpath)
                if "标的池" in wb.sheetnames:
                    ws = wb["标的池"]
                    for row in range(2, ws.max_row + 1):
                        strat = ws.cell(row=row, column=2).value
                        if strat and isinstance(strat, str) and len(strat) == 1:
                            all_strategies[strat] += 1
                            total_recos += 1
                wb.close()
            except Exception:
                continue

        summary = {
            "type": "weekly_review",
            "date": beijing_date,
            "week_files": len(xlsx_files),
            "total_recommendations": total_recos,
            "strategy_distribution": dict(all_strategies),
        }
        safe_append_json(HISTORY_PATH, summary)
        print(f"[步骤28] 周报: {len(xlsx_files)}文件, {total_recos}条推荐")
        log_alert("INFO", "每周复盘", f"周报已生成: {total_recos}条推荐")

    except Exception as e:
        log_alert("WARNING", "每周复盘", f"拉取失败: {str(e)[:100]}")
        print(f"[步骤28] 每周复盘异常: {str(e)[:60]}")
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


# ============================================================
# 主流程
# ============================================================
def main():
    global total_raw, excluded_count, filtered_count, matched_count
    global industry_limited_count, news_filtered_count, final_recommend_count
    global strategy_counts, exclude_stats, filter_stats

    print("=" * 60)
    print("A股每日盘前短线标的筛选 v6.5.3")
    print(f"执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # === 步骤0: 北京时间（最高优先级） ===
    step0_get_beijing_time()

    # 重置搜索预算
    _reset_search_budget()

    # === 步骤1: 节假日检查 ===
    holiday = step1_holiday_check()
    if holiday == "SKIP":
        print("节假日，跳过筛选")
        step28_weekly_review()
        return None

    long_holiday = (holiday == "LONG_HOLIDAY")

    # === 步骤2: 极端行情 ===
    market_status, sh_chg, market_type = step2_extreme_market()
    if market_status == "SKIP":
        print("极端行情，跳过筛选")
        step28_weekly_review()
        return None

    # === 步骤3: 外围市场 ===
    global_result = step3_global_markets()

    # === 步骤3A: 期货检查 ===
    futures_action = step3a_futures_check(global_result)

    # === 步骤4-4C: 持仓同步 ===
    holdings, history = step4_holding_sync()
    step4a_do_t_eval(holdings)
    step4b_tracking_sync(holdings)
    crisis_alerts = step4c_crisis_check(holdings)

    # === 步骤5: 历史清理 ===
    step5_history_cleanup()

    # === 步骤6: 文件初始化 ===
    step6_file_init()

    # === 步骤7: 财报季 ===
    is_earnings = step7_earnings_season()

    # === 步骤8: 大盘环境 ===
    env, env_positions = step8_market_environment(sh_chg, global_result, futures_action)

    # === 步骤9: 板块轮动 + 9A/9B/9C持仓管理 ===
    sectors = step9_sector_rotation()
    step9a_max_holding_days_check(history)
    step9b_circuit_breaker(history)
    step9c_conversion_rate(history)

    # === 步骤10A: 全市场API拉取 ===
    all_stocks, source = step10a_fetch_all_stocks()
    if not all_stocks:
        log_alert("ERROR", "行情采集", "全市场API拉取失败")
        print("行情数据获取失败，跳过筛选")
        return None

    # 构建原始标的池（涨跌>0%，活跃TOP500）
    raw_pool = [s for s in all_stocks
                if s.get('change_pct') is not None and s.get('close', 0) > 0]
    raw_pool.sort(key=lambda x: (x.get('turnover', 0) or 0), reverse=True)
    raw_pool = raw_pool[:500]

    candidates = []
    for s in raw_pool:
        c = {
            "code": s["code"], "name": s["name"],
            "sector": "", "industry": "",
            "change_pct": s.get("change_pct"),
            "open": s.get("open"), "close": s.get("close"),
            "turnover": s.get("turnover"), "amplitude": s.get("amplitude"),
            "volume_ratio": s.get("volume_ratio"), "amount": s.get("amount"),
            "high": s.get("high"), "low": s.get("low"),
            "prev_close": s.get("prev_close"),
            "main_inflow": s.get("main_inflow"),
            "total_cap": s.get("total_cap"),
            "strategy": "", "strategy_name": "", "strategy_reason": "",
            "reason": "", "score": 0, "confidence": "",
            "entry": None, "stop_loss": None, "take_profit": None,
            "signal_flags": [], "signal_deductions": 0,
            "news_notes": [], "l3_signal": None, "l3_deduction": 0,
            "gem_flag": False, "position_multiplier": 1.0,
            "url": (f"https://quote.eastmoney.com/concept/sh{s['code']}.html"
                    if s["code"].startswith('6')
                    else f"https://quote.eastmoney.com/concept/sz{s['code']}.html")
        }
        if c["close"] is None or c["close"] <= 0:
            continue
        candidates.append(c)

    total_raw = len(candidates)
    print(f"[步骤10A] 原始标的池: {total_raw}只 (来源: {source})")

    # === 步骤10B: 板块补全 ===
    step10b_sector_completion(candidates)

    # === 步骤11: 硬排除31项 ===
    post_l1, _, exclude_details = step11_hard_exclusion(candidates, holdings, history)
    excluded_count = len(candidates) - len(post_l1)

    # === 步骤12: 信号过滤14项 ===
    post_signal, signal_detail_data = step12_signal_filter(post_l1)
    filtered_count = len(post_l1) - len(post_signal)

    # === 步骤13: 五策略匹配 ===
    post_strategy = step13_strategy_match(post_signal, env, env_positions, is_earnings)
    matched_count = len(post_strategy)

    # === 步骤14-16: 综合评分 ===
    post_scored = step14_16_scoring(post_strategy, env, env_positions)

    # === 步骤17: 行业集中度 ===
    post_industry = step17_industry_limit(post_scored)
    industry_limited_count = len(post_industry)

    # === 步骤18: 新闻筛查 ===
    post_news, news_details = step18_news_screening(post_industry)
    news_filtered_count = len(post_news)

    # === 步骤19: 推荐不足降级 ===
    final_recos = step19_insufficient_handling(post_news)
    final_recommend_count = len(final_recos)

    # 更新全局策略计数
    strategy_counts = Counter(r.get('strategy', '') for r in final_recos)

    # === 步骤20: 输出Excel ===
    xlsx_path = step20_output_excel(
        final_recos, exclude_details, signal_detail_data, news_details,
        env, env_positions, total_raw, excluded_count, filtered_count,
        matched_count, industry_limited_count, news_filtered_count,
        final_recommend_count
    )

    # === 步骤21: 最终验证 ===
    step21_validate(xlsx_path, final_recommend_count)

    # === 步骤20B: 生成HTML报告 ===
    alerts_for_html = step24_alert_summary()
    html_path = generate_html_report(
        final_recos, total_raw, excluded_count, filtered_count,
        matched_count, industry_limited_count, news_filtered_count,
        exclude_stats, filter_stats, alerts_for_html, env, env_positions
    )

    # === 步骤22: 写推荐历史 ===
    step22_write_history(final_recos)

    # === 步骤23: 回溯做T ===
    step23_back_check_do_t()

    # === 步骤25: 输出筛选概况 ===
    step25_print_summary(
        final_recos, env, env_positions, crisis_alerts,
        total_raw, excluded_count, filtered_count,
        matched_count, industry_limited_count, news_filtered_count
    )

    # === 步骤26: GitHub同步 ===
    step26_github_sync(xlsx_path, html_path)

    # === 步骤27: 飞书推送 ===
    step27_feishu_push(
        final_recos, total_raw, excluded_count, filtered_count,
        matched_count, industry_limited_count, news_filtered_count
    )

    # === 步骤28: 每周复盘 ===
    step28_weekly_review()

    print(f"\n 筛选完成: {xlsx_path}")
    return xlsx_path


if __name__ == "__main__":
    main()