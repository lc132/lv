---
name: ashare-screener
description: A股每日盘前短线标的智能筛选(v6.5.0)。基于前一日收盘数据，通过 34步筛选流程（网络授时北京时间→节假日检查→极端行情→外围市场→持仓同步→做T评估→持仓跟踪同步→持仓危机检查→全市场API拉取(东方财富clist)→板块/行业补全→31项硬排除(L1/L2/L3三级可达性)→14项信号过滤→五大策略评分→行业集中度→新闻筛查→GitHub同步→飞书推送→每周复盘），仅输出短线标的_YYYYMMDD.xlsx 预测次日上涨的标的到Excel。推荐历史json和告警日志仅在自动化中写。当用户需要运行盘前筛选、A股短线选股、每日标的预测时使用。
---
# A股盘前短线标的筛选 v6.5.0

基于前一日完整收盘数据筛选当日有望上涨的A股短线标的。**不追高是硬纪律。**

## 步骤零、北京时间获取（最高优先级，必须第一步执行）

**核心原则**：仅通过公共网络授时 API 获取精确北京时间。不依赖本地系统时钟、不降级到 zoneinfo/pytz。

```python
from datetime import datetime, timedelta  # Python ≥ 3.7 才能使用 fromisoformat()
import urllib.request, urllib.error, json

beijing_now = None

# 仅通过网络授时API获取北京时间（多源冗余，任一成功即可）
TIME_APIS = [
    'https://worldtimeapi.org/api/timezone/Asia/Shanghai',
    'https://timeapi.io/api/time/current/zone?timeZone=Asia/Shanghai',
]
for api_url in TIME_APIS:
    try:
        req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        if 'worldtimeapi' in api_url:
            beijing_now = datetime.fromisoformat(data['datetime'])
        else:
            beijing_now = datetime.fromisoformat(data['dateTime'])
        break
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        log_alert("INFO", "北京时间", f"{api_url} 网络不可达: {str(e)[:60]}")
        continue
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log_alert("INFO", "北京时间", f"{api_url} 解析失败: {str(e)[:60]}")
        continue
    except Exception as e:
        log_alert("INFO", "北京时间", f"{api_url} 未知异常: {str(e)[:60]}")
        continue

# 所有API均失败 → 报错中止，不降级到系统时钟
if beijing_now is None:
    log_alert("ERROR", "北京时间", "所有授时API均不可达，本次筛选中止（禁止使用系统时钟）")
    raise RuntimeError("北京时间获取失败：所有授时API均不可达")

beijing_date = beijing_now.strftime('%Y-%m-%d')
beijing_hour = beijing_now.hour
beijing_weekday = beijing_now.weekday()  # 0=周一,6=周日

# 交易日对应：计算 data_date（行情日期）和 prediction_date（预测日期）
if beijing_weekday == 5:  # 周六
    prediction_date = beijing_now + timedelta(days=2)
    data_date = beijing_now - timedelta(days=1)
elif beijing_weekday == 6:  # 周日
    prediction_date = beijing_now + timedelta(days=1)
    data_date = beijing_now - timedelta(days=2)
else:  # 周一至周五
    prediction_date = beijing_now
    data_date = beijing_now
# 周一特殊处理：data_date 应为上周五
if beijing_weekday == 0:
    data_date = beijing_now - timedelta(days=3)

data_date = data_date.strftime('%Y-%m-%d')
prediction_date = prediction_date.strftime('%Y-%m-%d')

# 校验 prediction_date 是否为交易日（防止周六/日推算的周一恰逢节假日）
# 步骤1会检查节假日，若 prediction_date 为节假日则跳过筛选
# 此处仅做日期计算，实际交易日判断由步骤1负责
```

**交易日对应**：周六/日→跳过本次预测 | 周一→`data_date`=上周五,`prediction_date`=周一 | 周二→周一/周二 | 周三→周二/周三 | 周四→周三/周四 | 周五→周四/周五

所有搜索 query 使用 `data_date`，输出文件名 `/workspace/短线标的_YYYYMMDD.xlsx` 使用 `prediction_date`。API 全部不可达→直接中止，不降级。

## 可配置参数

从 `/workspace/策略调整记录.json` 数组末条 `params` 字段读取，共18项参数，默认值：`search_budget=25, northbound_threshold=3000, consecutive_weeks=2, win_rate_drop_threshold=10, limit_down_threshold=100, max_adjust_params=3, confidence_position_enabled=true, max_holding_days=5, circuit_breaker_threshold_pct=3.0, strategy_concentration_pct=60, do_t_success_reset_count=3, conversion_rate_window_days=10, conversion_rate_threshold=0.3, conversion_rate_restore=0.6, conversion_rate_consecutive_days=3, data_tier_l2_skip_on_unavailable=true, data_tier_l3_downgrade_to_signal=true, strategy_a_weak_market="closed"`。

参数用途说明：`search_budget`步骤10搜索次数 | `northbound_threshold`策略D主力资金流入阈值(万元)，默认3000万 | `consecutive_weeks`预留(周线趋势连续周数，当前未启用) | `win_rate_drop_threshold`预留(胜率回撤触发%，当前未启用) | `limit_down_threshold`步骤2跌停阈值 | `max_adjust_params`仅周六Task3回滚参数修改上限，每日筛选不执行回滚 | `confidence_position_enabled`置信度-仓位联动开关(true=启用) | `max_holding_days`九.A持仓超期退出天数 | `circuit_breaker_threshold_pct`九.B熔断阈值(%) | `strategy_concentration_pct`步骤17同策略上限(%) | `do_t_success_reset_count`九 做T成功重置所需次数 | `conversion_rate_*`兑现率闭环参数 | `data_tier_l2_skip_on_unavailable`数据不可达跳过 | `data_tier_l3_downgrade_to_signal`L3降级开关 | `strategy_a_weak_market`预留(弱市策略A开关，当前始终关闭)

## 系统告警

```python
def log_alert(level, module, message, timestamp=None):
    """写入告警日志。timestamp 默认使用系统时钟，若步骤0已获取 beijing_now，调用方可传入 beijing_now 替代。"""
    from datetime import datetime
    if timestamp is None:
        timestamp = datetime.now()
    ts = timestamp.strftime('%Y-%m-%d %H:%M:%S') if hasattr(timestamp, 'strftime') else str(timestamp)
    with open('/workspace/系统告警.log', 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] [{level}] {module}: {message}\n")
```

触发场景：推荐历史读写失败(ERROR)、JSON格式异常(WARNING)、Excel读写失败(WARNING)、持仓行情搜索失败(WARNING)、持仓跟踪同步失败(WARNING)、持仓跟踪同步成功(INFO)、持仓危机(WARNING)、清理成功(INFO)、清理失败(WARNING)、版本一致(INFO)、版本不一致(INFO)、北京时间API不可达(INFO)、北京时间获取失败(ERROR)、筛选概况与Excel行数不一致(ERROR)、GitHub同步成功(INFO)、GitHub同步失败/无令牌(WARNING)、数据不可达跳过(INFO)、飞书推送成功(INFO)、飞书推送失败(WARNING)、行情数据采集失败(WARNING)、行情数据校验异常(WARNING)

## 文件容错

所有文件读写必须使用以下 safe_ 函数：

```python
import json, os
from openpyxl import load_workbook

def safe_read_json(path, default=None):
    try:
        if not os.path.exists(path): return default if default is not None else []
        with open(path, 'r') as f:
            data = json.load(f)
            if not isinstance(data, list):
                log_alert("WARNING", "safe_read_json", f"{path} 格式异常")
                return default if default is not None else []
            return data
    except (json.JSONDecodeError, PermissionError) as e:
        log_alert("ERROR", "safe_read_json", f"{path}: {str(e)}")
        return default if default is not None else []

def safe_write_json(path, data):
    try:
        with open(path, 'w') as f: json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e: log_alert("ERROR", "safe_write_json", f"{path}: {str(e)}")

def safe_append_json(path, record):
    data = safe_read_json(path)
    data.append(record)
    safe_write_json(path, data)

def safe_read_excel(path):
    try:
        if not os.path.exists(path): return None
        return load_workbook(path)
    except Exception as e:
        log_alert("WARNING", "safe_read_excel", f"{path}: {str(e)}")
        return None

def safe_float(value, ndigits=3):
    """安全浮点格式化，供步骤4/4B/步骤10等外部调用"""
    if value is None: return None
    if isinstance(value, (int, float)): return round(float(value), ndigits)
    return value
```

⚠️ JSON追加必须用 `safe_append_json`，禁止直接 `safe_write_json` 追加。

## 前置检查（步骤1-8）

**1.节假日**：搜索中国股市交易日历→data_date 或 prediction_date 为节假日→跳过；长休≥3日→弱市+仓位≤30%+搜索预算+5

**2.极端行情**：上证跌>3%→跳过；涨>3%→仓位30%仅动量延续（若弱市策略A已关闭，则临时启用A仓位15%）；跌停>threshold→跳过

**3.外围市场**：美股三大指数均跌>2%→弱市仓位≤30%；恒生跌>3%→弱市仅超跌反弹；人民币波动>0.5%→暂停策略D。美股/港股假期→跳过此检查

**3A.开盘前外围**：盘前搜索美股期货（标普/纳指/道指期货）实时行情。任一期货较前日收盘跌>1%→外围偏空，仓位降一档（强→震荡→弱）。若期货数据不可得→跳过此检查，维持步骤3外围判断。

**4.持仓行情同步**：遍历推荐历史中 `type="holding"` 记录，**先将旧 current 保存为 prev_close**，再搜索当日收盘价→更新current/pnl_pct/update_date，同时计算 market_value=round(current×shares,2) 和 pnl_amount=round((current-cost)×shares,2)。搜不到→log_alert WARNING保留旧数据。`safe_write_json` 写回推荐历史。prev_close 用于步骤4C跌停检测。

**4A.做T评估**：对持仓进行做T可行性评估，详见「九、做T评估」。输出 `type="do_T_eval"` 追加到推荐历史，回溯检查昨日 do_T_eval。

**4B.持仓跟踪.xlsx同步**：步骤4完成后，将更新后的 holding 收盘价同步写入 `/workspace/持仓跟踪.xlsx` 的「持仓明细」sheet。
- 仅更新「当前价」列（列7）和「市值」列（列8）、「盈亏额」列（列9）、「盈亏率」列（列10）
- 按 code 匹配行，不新增/删除行，不修改成本/持仓量等字段
- 若 xlsx 不存在或结构异常→log_alert WARNING，跳过；若某 code 在 xlsx 中找不到→log_alert WARNING
- 同步后 save

```python
from openpyxl import load_workbook
def sync_holding_prices_to_xlsx(holdings, path="/workspace/持仓跟踪.xlsx"):
    """将步骤4更新后的持仓价格写入持仓跟踪.xlsx"""
    try:
        wb = load_workbook(path)
        ws = wb["持仓明细"]
        # code → row mapping (skip header row)
        code_row = {}
        for row in range(2, ws.max_row + 1):
            code = ws.cell(row=row, column=1).value
            if code and isinstance(code, str) and len(code) == 6:
                code_row[str(code)] = row
        
        updated = 0
        for h in holdings:
            current = None
            try:
                raw_code = h.get("code")
                code = str(raw_code) if raw_code is not None else ""
                current = h.get("current")  # 防御性读取，缺失返回None
                if not code or code not in code_row:
                    if code:
                        log_alert("WARNING", "持仓跟踪同步", f"{code} 在xlsx中找不到")
                    continue
                if current is None:
                    log_alert("WARNING", "持仓跟踪同步", f"{code} 缺少current字段，跳过")
                    continue

                row = code_row[code]
                # 市值和盈亏额：优先取holding中的值，缺失则从xlsx读取成本/持仓量计算
                mv = h.get("market_value")
                pnl_amt = h.get("pnl_amount")
                if mv is None or pnl_amt is None:
                    cost = ws.cell(row=row, column=3).value   # 成本
                    shares = ws.cell(row=row, column=4).value  # 持仓量
                    if cost and shares and current:
                        mv = round(current * shares, 2)
                        pnl_amt = round((current - cost) * shares, 2)
                ws.cell(row=row, column=7).value = current              # 当前价
                if mv is not None:
                    ws.cell(row=row, column=8).value = mv               # 市值
                if pnl_amt is not None:
                    ws.cell(row=row, column=9).value = round(pnl_amt, 2)  # 盈亏额
                pnl_pct_val = h.get("pnl_pct")
                # 安全类型转换：若为字符串（如 "3.5%"），先尝试转为 float
                try:
                    pnl_pct_float = float(pnl_pct_val) if pnl_pct_val is not None else 0.0
                except (ValueError, TypeError):
                    pnl_pct_float = 0.0
                ws.cell(row=row, column=10).value = round(pnl_pct_float, 4)  # 盈亏率
                updated += 1
            except Exception as e:
                log_alert("WARNING", "持仓跟踪同步", f"单条记录异常(code={h.get('code', 'unknown')}): {str(e)[:80]}")
                continue
        if updated > 0:
            wb.save(path)
            log_alert("INFO", "持仓跟踪同步", f"已更新{updated}只持仓价格")
    except Exception as e:
        log_alert("WARNING", "持仓跟踪同步", f"失败: {str(e)[:100]}")
```

**4C.持仓危机检查**：步骤4持仓行情更新后，遍历 holding 记录检查危机信号。不阻断筛选流程，但在对话输出中置于筛选概况上方优先展示。

检查规则：
- 当日收盘价较前日跌停(< -9.5%) → log_alert WARNING + 对话置顶告警「⚠️ {code} {name} 当日跌停！成本{cost} 现价{current} 浮亏{pnl_pct}%」
- 浮亏>15% → log_alert WARNING + 对话置顶告警「⚠️ {code} {name} 浮亏突破15%做T上限({pnl_pct}%)，建议人工决策」
- 触发硬排除规则(L1级) → 对话告警「⚠️ {code} {name} 触发硬排除规则{rules}（例：股价跌破5元、ST等）」

```python
def check_holding_crisis(holdings):
    alerts = []
    for h in holdings:
        code = h.get("code", "?")
        name = h.get("name", "?")
        cost = h.get("cost", 0)
        current = h.get("current", 0)
        prev_close = h.get("prev_close")  # 步骤4在更新前保存的昨日收盘价
        pnl_pct = h.get("pnl_pct", 0)
        
        # 跌停检查：用prev_close（昨日收盘） vs current（今日收盘）
        if prev_close is not None and current > 0 and prev_close > 0:
            daily_chg = (current - prev_close) / prev_close * 100
            if daily_chg < -9.5:
                msg = f"⚠️ {code} {name} 当日跌停({daily_chg:.1f}%)！成本{cost} 现价{current} 浮亏{pnl_pct}%"
                alerts.append(msg)
                log_alert("WARNING", "持仓危机", msg)
        
        # 浮亏>15%
        if pnl_pct is not None and pnl_pct < -15:
            msg = f"⚠️ {code} {name} 浮亏突破15%做T上限({pnl_pct:.1f}%)，建议人工决策"
            alerts.append(msg)
            log_alert("WARNING", "持仓危机", msg)
        
        # L1级硬排除规则触发检查
        if current > 0:
            l1_triggers = []
            if current < 5: l1_triggers.append("股价<5元(规则3)")
            if current > 100: l1_triggers.append("股价>100元(规则4)")
            if code.startswith("688"): l1_triggers.append("科创板(规则1)")
            if code.startswith("8") and len(str(code)) == 6: l1_triggers.append("北交所(规则2)")
            if l1_triggers:
                msg = f"⚠️ {code} {name} 触发L1硬排除: {', '.join(l1_triggers)}"
                alerts.append(msg)
                log_alert("WARNING", "持仓危机", msg)
    return alerts
```

**5.推荐历史持久化**：`safe_read_json` 读取，提取 recommendation(7日内排除)+holding(已持仓排除)。生成后用 `safe_append_json` 追加。

清理逻辑：
```python
# 清理7天前recommendation + 90天前holding/do_T
# 保留类型：weekly_review、strategy_check、do_T_eval、do_T（不受清理影响）
# data_date 由步骤0定义（beijing_date的值），如 "2026-06-12"
try:
    from datetime import datetime, timedelta
    history = safe_read_json('/workspace/推荐历史.json')
    cutoff_7d_dt = datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=7)
    cutoff_90d_dt = datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=90)
    cutoff_7d = cutoff_7d_dt.strftime('%Y-%m-%d')
    cutoff_90d = cutoff_90d_dt.strftime('%Y-%m-%d')
    new_history = []
    for r in history:
        t = r.get('type', '')
        if t in ('weekly_review', 'strategy_check', 'do_T_eval', 'do_T'):
            new_history.append(r)  # 永久保留（do_T 由主对话管理，不在此处清理）
        elif t in ('holding',):
            d = r.get('update_date', '')
            if d >= cutoff_90d:
                new_history.append(r)  # 90天内保留
        elif t == 'recommendation':
            d = r.get('date', '')
            if d >= cutoff_7d:
                new_history.append(r)  # 7天内保留
    if len(new_history) < len(history):
        safe_write_json('/workspace/推荐历史.json', new_history)
        log_alert("INFO", "清理", f"已清理{len(history)-len(new_history)}条过期记录")
    else:
        log_alert("INFO", "清理", "无需清理")
except Exception as e:
    log_alert("WARNING", "清理", f"清理失败: {str(e)[:80]}")
```

**注意**：weekly_review/strategy_check 类型保留不清理。

**6.文件初始化**：策略调整记录.json取末条version+params，损坏→默认v6.4.20。交叉验证推荐历史中strategy_check版本，不一致以策略调整记录为准→log_alert INFO。**首次运行或版本变更→safe_append_json追加type="strategy_check"记录**（含version/params/checks），验证各项条件计数与预期一致

版本一致性检查代码：
```python
# 读取策略调整记录（获取 file_version 和 params）
adj_records = safe_read_json('/workspace/策略调整记录.json')
if adj_records and len(adj_records) > 0:
    latest = adj_records[-1]
    file_version = latest.get('version', 'v6.4.20')
    params = latest.get('params', {})
else:
    file_version = 'v6.4.20'
    params = {}

# 读取推荐历史找最后一个strategy_check
history = safe_read_json('/workspace/推荐历史.json')
last_check = None
current_version = None  # 显式初始化，防止首次运行 NameError
for r in reversed(history):
    if r.get('type') == 'strategy_check':
        last_check = r
        break
if last_check:
    current_version = last_check.get('version', 'unknown')
    if current_version != file_version:
        log_alert("INFO", "版本检查", f"推荐历史版本{current_version}≠策略调整版本{file_version}，以策略调整为准")
    else:
        log_alert("INFO", "版本检查", f"版本一致{file_version}")

# 自动更新筛选条件表格
if last_check is None or current_version != file_version:
    try:
        from openpyxl import load_workbook
        from openpyxl.styles import Font, Alignment, Border, Side

        xlsx_path = "/workspace/A股短线选股筛选条件.xlsx"
        if os.path.exists(xlsx_path):
            wb = load_workbook(xlsx_path)
            _cell_font = Font(name='Arial', size=10)
            _bold_font = Font(name='Arial', size=10, bold=True)
            _thin_border = Border(
                left=Side(style='thin', color='B0B0B0'),
                right=Side(style='thin', color='B0B0B0'),
                top=Side(style='thin', color='B0B0B0'),
                bottom=Side(style='thin', color='B0B0B0'),
            )

            def _wc(ws, r, c, v, font=_cell_font):
                # 注意：此函数与步骤26(十三.A)中的 _wc 定义一致，修改时需同步更新两处
                for mr in list(ws.merged_cells.ranges):
                    if mr.min_row <= r <= mr.max_row and mr.min_col <= c <= mr.max_col:
                        if not (r == mr.min_row and c == mr.min_col):
                            return
                        ws.unmerge_cells(str(mr))
                cell = ws.cell(row=r, column=c, value=v)
                cell.font = font
                cell.border = _thin_border
                cell.alignment = Alignment(vertical='center', wrap_text=True)

            # 更新筛选条件概述
            ws1 = wb['筛选条件概述']
            _wc(ws1, 1, 1, f'A股短线选股筛选条件 — {file_version}', _bold_font)
            _wc(ws1, 2, 2, file_version)
            _wc(ws1, 2, 3, f'{beijing_date}更新')
            # 追加版本记录
            vr = ws1.max_row + 1
            _wc(ws1, vr, 1, file_version)
            _wc(ws1, vr, 2, beijing_date)
            _wc(ws1, vr, 3, '版本同步')

            # 更新关键纪律版本号
            if '关键纪律' in wb.sheetnames:
                ws11 = wb['关键纪律']
                _wc(ws11, 1, 1, f'关键纪律 — {file_version}', _bold_font)

            wb.save(xlsx_path)
            log_alert("INFO", "筛选条件", f"筛选条件.xlsx 已同步至 {file_version}")
        else:
            log_alert("WARNING", "筛选条件", "筛选条件.xlsx 不存在，跳过自动更新")
    except Exception as e:
        log_alert("WARNING", "筛选条件", f"筛选条件.xlsx 自动更新失败: {str(e)[:80]}")
```

**7.财报季**：1/3/4/8/10月→事件驱动权重×1.5+仓位+5%，动量延续涨幅上限7%→8%

**8.大盘环境**：

| 环境 | 条件 | 总仓位 | 动量(A) | 超跌(B) | 事件(C) | 资金(D) | 回调(E) |
|------|------|--------|---------|---------|---------|---------|---------|
| 强市 | 上证>MA20且MA5>MA10>MA20且涨跌比>2:1且成交>20日均×1.2 | 70-80% | 35-40% | 10-12% | 10-12% | 5-8% | 10-12% |
| 震荡 | 上证在MA20±2%或涨跌比≈1:1 | 50-60% | 12-17% | 10-13% | 8-10% | 5-8% | 10-13% |
| 弱市 | 上证<MA20×0.98或涨跌比<1:2或成交<20日均×0.8 | 30-40% | **0%** | 12-15% | 5-8% | 3-5% | 8-12% |

边界：上证在MA20×0.98~MA20但不满足震荡→弱市。**三项指标矛盾时（均线/涨跌比/成交量跨不同档级）→保守取弱一档（安全优先）**

## 一、硬性排除（31项）

1.科创板(688xxx) 2.北交所(8开头) 3.股价<5元 4.股价>100元 5.ST/*ST 6.退市整理期 7.连续亏损2年+最新季度营收同比降>10% 8.上市<60日 9.停牌→复牌<3日 10.前日涨停但当日开板 11.涨停/连板 12.涨幅>7% 13.7日内已推荐+已持仓 14.7日内解禁>流通5% 15.3日内分红除权 16.可转债强赎/转股>10% 17.30日内研报从买入/增持下调≥2级 18.5日内大宗折价>5%且>5000万 19.融券连续3日增>50% 20.PE(TTM)>500且非困境反转 21.创业板(300xxx)仅强市+动量延续+仓位减半 22.跌停(<-9.5%) 23.质押>70%且距平仓线<20% 24.30日内业绩修正(预增→预亏) 25.30日内立案调查/行政处罚；监管函/问询函→进入30日观察期，未出新利空则观察期满自动降级，仍有公告→排除 26.当日主力净流出>1亿且占成交额>15% 27.龙虎榜机构席位净卖出>3000万 28.近20日跌幅>30%且无基本面改善 29.大股东减持计划公告<5日 30.商誉占净资产>50%且业绩承诺到期<6个月 31.行业级政策利空公告<5日

### 数据源声明

| 规则 | 数据字段 | 数据源 |
|------|---------|--------|
| 26 | 主力资金流向 | 东方财富/同花顺Level-2 |
| 27 | 龙虎榜席位 | 沪深交易所官网 |
| 28 | 基本面改善 | 公司公告/季报/年报 |
| 29 | 大股东减持计划 | 巨潮资讯网/公司公告 |
| 30 | 商誉占净资产+业绩承诺 | 公司财报(资产负债表+附注) |
| 31 | 行业级政策利空 | 部委官网/新华社/国务院公告 |

### 数据可达性分级（v6.4.5 新增）

部分规则依赖专业金融终端数据，WebSearch 无法可靠获取。按数据源分为三级，不可达时自动降级：

| 分级 | 规则编号 | 数据源 | 不可达时处理 |
|------|----------|--------|-------------|
| **L1 必执行** | 1-16, 20-22, 28 | 公开行情/公告 | 正常排除（WebSearch可获取） |
| **L2 尽力执行** | 17, 18, 23, 24, 25, 27, 29, 30 | 专业终端（研报/大宗/龙虎榜/质押/减持/商誉） | 标注「数据不可达→跳过」，**不排除**，记录到告警日志 |
| **L3 降为信号** | 19, 26, 31 | 可部分获取（融券/主力资金/行业政策） | 降为信号级：满足条件→标注⚠️不排除，在Excel「预测逻辑」列注明风险 |

L2 规则执行逻辑：搜索数据→可获取且满足条件→排除；搜索失败/数据不可得→log_alert INFO标注跳过，继续流程。L3 规则执行逻辑：搜索数据→可获取且满足条件→标注⚠️但保留标的，评分时可酌情扣分；搜索失败→跳过。

## 二、信号质量过滤（14项）

1.假动量：高开>3%且收<开×0.98→排除；盘中最高涨>5%且收<开×1.01→诱多排除
2.缩量涨停：涨幅>5%但量<5日均×0.5→排除
3.尾盘急拉：最后30分涨>3%→排除
4.尾盘跳水：最后30分跌>3%→排除
5.换手率>30%(非次新/非公告日)→排除/标注异常
6.放量滞涨：涨幅<0.5%但量比>2.0→排除
7.振幅>15%→排除（有明确利好公告可豁免）
8.MACD顶背离：价格新高但DIF未新高→降置信/排除
9.缩量上涨：涨幅>3%但量<5日均×0.7→降置信(减3分)
10.涨停反复开板≥3次→降置信（涨停留言板≥3次+量超昨×2→加回）（封板意愿弱）
11.缩量反弹：前日跌幅>5%+当日反弹但量<前日量×0.7→降置信(减4分)
12.缩量三连阴：连续3日收阴+量逐日递减(每日量<前日×0.95)→降置信(减3分)
13.竞价爆量：竞价量比>8.0且开盘涨幅>3%→排除（过度炒作）；竞价量比<0.3→降置信(减2分，开盘无人关注)
14.连板后首阴：前3日有连板(≥2板)+当日收阴(跌幅<3%)+成交量>5日均×0.8→标注"首阴候选"+加分(不排除)

## 三、行业集中度

同申万一级行业≤3只，评分前先行业预分配确保≥2个行业。**同策略集中度**：总推荐中同策略标的≤{strategy_concentration_pct}%（如5只中同策略≤{strategy_concentration_pct*5//100}只，由代码预计算），超出则降分排序取前。

## 四、推荐不足降级

3只→全部+放宽至中 | 2只→全部+仅≥中置信 | 1只→仅高置信 | 0只→"无合适标的"+追加空标记

## 五、五大策略

**A动量延续(优1)**：涨幅3-7%、量比1.5-3.0、量>5日均×1.5且>昨日×1.2、MA5>MA10>MA20；加分：板块TOP5；仓位：强35-40%/震荡12-17%/弱**关闭**。弱市动量大概率诱多，直接关闭策略A。
**B超跌反弹(优2)**：连跌≥3日、量<5日均×0.6、RSI(14)连续≥3日<35或底背离、MA20/MA60支撑、KDJ的K<20且J拐头向上（增强B最低置信★★）；**反弹确认：收盘站上MA5+成交量>昨日×1.2**；**趋势底线：股价<MA60→跳过策略B**（周线趋势向下，超跌易变接飞刀）；仓位：强10-12%/震荡12-15%/弱12-15%
**C事件驱动(优3)**：重大合同/预增>50%或部委级政策；仓位：强10-12%/震荡10-12%/弱5-8%(财报+5%)；事件时效5级衰减：当日100%→次日80%→第3日50%→4-7日30%→>7日10%；高开>5%→不追
**D资金埋伏(优4)**：北向3日连续净买+主力流入>{northbound_threshold}万+涨幅<2%；仓位：强5-8%/震荡5-8%/弱3-5%；汇率>0.5%暂停。退出：买入后3日累计涨幅<2%→退出（横盘不作为）；加仓：北向连续5日净买+仍满足涨幅<2%→仓位上限翻倍至16%
**E回调企稳突破(优5)**：20日内创新高+回调至MA20±3%+连续3日缩量(量<5日均×0.6)+站回MA5放量(量>昨日×1.3)；仓位：强10-12%/震荡12-15%/弱8-12%；注意：E与A不能同时匹配，以A优先。**E与B同时匹配→E优先**（企稳突破比超跌反弹可靠性更高）。假突破过滤：当日上下影线比>2:1(上影>下影×2)→降置信减3分

## 六、板块轮动

资金流入TOP3→动量优先 | 连续3日流入→资金+事件 | 板块龙头涨停→找MA20支撑标的 | 流出TOP5→回避

**搜集行情（步骤10）**：通过东方财富全A股行情API（`push2.eastmoney.com/api/qt/clist/get`）一次性拉取全市场约5000+只标的的实时行情数据（含最新价、涨跌幅、换手率、振幅、量比、成交额、主力净流入等），替代 WebSearch 搜索引擎发现。API 返回数据自动包含 open/close/turnover/amplitude/change_pct 等字段，无需逐只 fetch_stock_quote。API 不可达→降级为各板块涨幅TOP20分页拉取，2次均失败→log_alert ERROR 中止。

**标的池数据结构**（每只标的一律包含以下字段，筛选过程中逐阶段填充）：

```python
candidate = {
    "code": "000001",          # 股票代码
    "name": "平安银行",         # 名称
    "sector": "银行",           # 板块
    "industry": "银行",         # 申万一级行业
    "change_pct": 2.35,        # 当日涨跌幅(%)
    "open": 12.50,             # 开盘价 ← 必须采集
    "close": 12.80,            # 收盘价 ← 必须采集
    "turnover": 5.62,          # 换手率(%) ← 必须采集
    "amplitude": 4.50,         # 振幅(%) ← 必须采集
    "strategy": "A",           # 匹配策略（筛选后填充）
    "reason": "涨幅3-7%...",   # 预测逻辑
    "score": 12,               # 综合评分
    "confidence": "★★★",       # 置信度
    "entry": 12.80,            # 建议进场价
    "stop_loss": 12.29,        # 止损价
    "take_profit": 13.44,      # 止盈价
    "url": "https://..."       # 行情链接
}
```

**行情数据采集**（v6.5.0重写：全市场API拉取替代逐只搜索）：

**步骤10A — 全市场行情拉取**（替代原 WebSearch 发现）：

```python
# 通过东方财富 clist API 一次性拉取全A股行情数据
import urllib.request, json, time

def fetch_all_a_stocks():
    """拉取全A股实时行情，返回 [{code, name, open, close, change_pct, turnover, amplitude, volume_ratio, amount, high, low, prev_close, main_inflow, pe_ttm, total_cap}, ...]"""
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    # fs 覆盖全部A股：沪主板+科创板+深主板+创业板+北交所
    params = {
        "pn": "1",
        "pz": "6000",         # 一次性拉取全部（A股约5500只）
        "po": "1",            # 按涨跌幅降序
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",          # 按涨跌幅排序
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "fields": "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f23,f62,f115",
        "_": str(int(time.time() * 1000))
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://quote.eastmoney.com/'
    }
    try:
        req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers=headers)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if not data or not data.get('data') or not data['data'].get('diff'):
            return None, "API返回空数据"
        
        stocks = []
        for item in data['data']['diff']:
            # 字段映射：f2=最新价, f3=涨跌幅, f4=涨跌额, f5=成交量(手), f6=成交额,
            # f7=振幅, f8=换手率, f9=市盈率(动态), f10=量比, f12=代码, f14=名称,
            # f15=最高, f16=最低, f17=今开, f18=昨收, f20=总市值, f21=流通市值,
            # f23=市净率, f62=主力净流入(元), f115=市盈率(TTM)
            code = item.get('f12', '')
            name = item.get('f14', '')
            if not code or not name:
                continue
            # 基础过滤：排除停牌/无价格标的
            close = item.get('f2')
            if close == '-' or close is None:
                continue
            try:
                close_val = float(close) if close != '-' else None
                open_val = float(item.get('f17', 0)) if item.get('f17') not in (None, '-') else None
                high_val = float(item.get('f15', 0)) if item.get('f15') not in (None, '-') else None
                low_val = float(item.get('f16', 0)) if item.get('f16') not in (None, '-') else None
                prev_close_val = float(item.get('f18', 0)) if item.get('f18') not in (None, '-') else None
                change_pct = float(item.get('f3', 0)) if item.get('f3') not in (None, '-') else 0.0
                turnover = float(item.get('f8', 0)) if item.get('f8') not in (None, '-') else 0.0
                amplitude = float(item.get('f7', 0)) if item.get('f7') not in (None, '-') else 0.0
                volume_ratio = float(item.get('f10', 0)) if item.get('f10') not in (None, '-') else 0.0
                amount = float(item.get('f6', 0)) if item.get('f6') not in (None, '-') else 0.0
                total_cap = float(item.get('f20', 0)) if item.get('f20') not in (None, '-') else 0.0
                main_inflow = float(item.get('f62', 0)) if item.get('f62') not in (None, '-') else 0.0
                pe_ttm = float(item.get('f115', 0)) if item.get('f115') not in (None, '-') else 0.0
            except (ValueError, TypeError):
                continue
            
            stocks.append({
                "code": code,
                "name": name,
                "open": open_val,
                "close": close_val,
                "change_pct": change_pct,
                "turnover": turnover,
                "amplitude": amplitude,
                "volume_ratio": volume_ratio,
                "amount": amount,
                "high": high_val,
                "low": low_val,
                "prev_close": prev_close_val,
                "main_inflow": main_inflow,       # 主力净流入(元)
                "pe_ttm": pe_ttm,
                "total_cap": total_cap,
            })
        return stocks, None
    except Exception as e:
        return None, str(e)[:100]

# 调用
all_stocks, err = fetch_all_a_stocks()
if all_stocks is None:
    log_alert("ERROR", "行情采集", f"全市场API拉取失败: {err}")
    raise RuntimeError(f"行情数据获取失败: {err}")
log_alert("INFO", "行情采集", f"全市场拉取到 {len(all_stocks)} 只标的")

# 从全市场数据构建原始标的池（按涨跌幅筛选活跃标的）
# 保留涨跌幅>0%且非停牌标的，按换手率/量比/成交额排序取前500只
raw_pool = [s for s in all_stocks if s['change_pct'] is not None and s['change_pct'] > 0]
raw_pool.sort(key=lambda x: (x.get('turnover', 0) or 0), reverse=True)
raw_pool = raw_pool[:500]  # 取活跃度前500只进入后续筛选
total_raw = len(raw_pool)
log_alert("INFO", "行情采集", f"原始标的池: {total_raw} 只（全市场{len(all_stocks)}只中涨跌幅>0%且活跃TOP500）")
```

**步骤10B — 逐只行情补全**（仅对 clist API 缺少的字段，如板块/行业/K线形态）：

```python
# clist API 已提供 open/close/turnover/amplitude/change_pct/volume_ratio/main_inflow
# 以下字段需通过 WebSearch 补全：板块(sector)、申万行业(industry)、K线形态(MA/RSI等)
# 搜不到的标的保留在池中，sector/industry 标记为 "未知"，后续硬排除中对应规则跳过
```

**保留 fetch_stock_quote 作为降级和补充**（当 clist API 不可达时，逐只拉取）：

```python
# 采集单个标的的行情数据（开盘价/收盘价/换手率/振幅/涨跌幅）
import urllib.request, json

def fetch_stock_quote(code, data_date):
    """通过定向URL获取精确行情，返回 dict 或 None。data_date 用于校验行情日期（YYYY-MM-DD）"""
    market = 'sz' if code.startswith(('000','002','003','300','301')) else 'sh'
    # 东方财富secid格式：深圳0，上海1（数字代码，非sz/sh字符串）
    secid_market = '0' if market == 'sz' else '1'
    # 新浪换手率索引：深圳 parts[37]，上海 parts[38]
    turnover_idx = 37 if market == 'sz' else 38

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
            if len(parts) > max(4, turnover_idx):
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
                turnover = float(parts[turnover_idx]) if len(parts) > turnover_idx and parts[turnover_idx] else None
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
```

# clist API 已提供核心行情数据，直接填充到 candidate
# 仅需补全板块/行业信息（WebSearch）
for s in raw_pool:
    candidate = {
        "code": s["code"],
        "name": s["name"],
        "sector": "",         # 待步骤10B补全
        "industry": "",       # 待步骤10B补全
        "change_pct": s.get("change_pct"),
        "open": s.get("open"),
        "close": s.get("close"),
        "turnover": s.get("turnover"),
        "amplitude": s.get("amplitude"),
        "volume_ratio": s.get("volume_ratio"),
        "amount": s.get("amount"),
        "main_inflow": s.get("main_inflow"),
        "pe_ttm": s.get("pe_ttm"),
        "total_cap": s.get("total_cap"),
        "strategy": "",
        "reason": "",
        "score": 0,
        "confidence": "",
        "entry": None,
        "stop_loss": None,
        "take_profit": None,
        "url": f"https://quote.eastmoney.com/concept/sh{s['code']}.html" if s["code"].startswith('6') else f"https://quote.eastmoney.com/concept/sz{s['code']}.html"
    }
    # 数据校验：核心字段为空则排除
    if candidate["close"] is None or candidate["close"] <= 0:
        continue
    if candidate["change_pct"] is None:
        continue
    candidates.append(candidate)

## 七、评分公式

**计分规则**：先通过三项必选门控条件（不通过→直接排除），再计算数值分：总分=加分×2+参考×1+新闻加分-新闻扣分-L3扣分。

**必选（三项门控条件，任一项不满足→直接排除，不进入评分）**：
1. 策略条件全部满足（A/B/C/D/E 至少一个策略的全部条件通过）
2. 信号质量无排除项（14项信号过滤中无一触发排除，降置信除外）
3. 行业集中度通过（同行业≤3只 + 同策略≤{strategy_concentration_pct}%）

**加分项**：板块TOP5+1、信号加分项(首阴候选+1)、K线形态确认+1。
**参考项加分**：ROE>15%(+2分) + ROE 5-15%(+1分) + ROE<0%(-1分) + 经营现金流为正(+1分)。
**L3扣分**：L3级信号触发→减2分（主力净流出/融券增/行业利空）。
置信：≥9★★★ | 6-8★★ | <6★。分值为整数，如遇小数→向上取整判定置信度（如5.2→★★，8.8→★★★）。策略冲突按优先级归类，动量+超跌同时→以动量为准。

**置信度-仓位联动**（受 `confidence_position_enabled` 开关控制）：`confidence_position_enabled=true` 时→同一策略内 ★★★→取仓位上限 | ★★→取仓位中值 | ★→取仓位下限。`confidence_position_enabled=false` 时→统一取仓位中值。仓位上限/下限按策略定义取整。

## 八、新闻筛查

排除：减持/暴雷/立案/诉讼/下调评级 | 观察：异常波动/解禁/高管减持 | 加分：预增/合同/调研/上调评级

**时效性衰减**：当日 → 100%权重；2-3日 → 70%权重；4-7日 → 30%权重；>7日 → 0%权重

## 九、做T评估（仅对持仓）

浮亏<5%或浮盈→观望；浮亏5%-10%（含10%）→重点评估，仓位≤总持仓1/3；浮亏10%-15%（含15%）→谨慎评估，仓位≤总持仓1/4；>15%→不做T。评估：止跌信号(下影/十字星/站MA5)+波动≥3%+非放量跌+板块预期+无利空。仓位阶梯：<5%观望 | 5-10%(含10%)≤1/3 | 10-15%(含15%)≤1/4 | >15%不做T。目标2-3%止盈，-3%止损。累计成功≥{do_t_success_reset_count}次→重置失败计数器；连续2次失败→放弃。输出 `type="do_T_eval"` 追加推荐历史，回溯检查昨日do_T_eval→do_T缺失则提醒。

## 九.A、最大持仓天数（推荐后管理）

- T+3日收盘较推荐日涨跌幅<2%→主动退出（横盘不作为）
- T+{max_holding_days}日收盘较推荐日跌幅>5%→无条件止损（趋势判断失误）
- T+1日盘中跌幅>7%→日内止损（极端行情保护）
- 退出时追加 `type="exit"` 到推荐历史，记录退出日期/价格/盈亏/原因

## 九.B、组合回撤断路器

当日推荐组合的T+1日盘中估算最大亏损>{circuit_breaker_threshold_pct}%→次交易日总仓位降至50%，连续2日触发→降至30%。仅影响下一交易日，不改变策略参数。

## 九.C、T+1兑现率闭环

每次运行前读取推荐历史，统计最近{conversion_rate_window_days}个交易日的T+1兑现率（收盘涨幅>2%视为兑现）。兑现率<{conversion_rate_threshold*100}%→自动降一档仓位（强→震荡→弱→跳过），连续{conversion_rate_consecutive_days}个交易日兑现率均<{conversion_rate_threshold*100}%→暂停推荐1天。兑现率≥{conversion_rate_restore*100}%→仓位恢复至正常档位。

**冷启动保护**：推荐历史中 type="recommendation" 不足 10 条时→跳过兑现率检查，直接使用步骤8的大盘仓位。首次达到 10 条后正常启动闭环。

## 十、回滚

本任务不执行回滚，由周六Task3负责。本任务只读取当前最新参数。

## 十一、输出（含筛选概况）

**Excel**：`/workspace/短线标的_YYYYMMDD.xlsx`（8工作表），prediction_date命名。18列：序号|策略|标的|代码|板块|行业|当日涨跌|开盘价|收盘价|换手率|振幅|预测逻辑|评分|置信度|进场|止损|止盈|链接

**Excel 写入逻辑**（数据来自标的池 candidate 列表，按评分降序排列）：

```python
# 写入表头
headers = ["序号","策略","标的","代码","板块","行业","当日涨跌","开盘价","收盘价","换手率","振幅","预测逻辑","评分","置信度","进场","止损","止盈","链接"]
for col_idx, h in enumerate(headers, 1):
    ws.cell(row=1, column=col_idx, value=h)

# 写入数据行（recos 为最终推荐列表，已按评分降序）
for i, rec in enumerate(recos, 1):
    ws.cell(row=i+1, column=1, value=i)                          # 序号
    ws.cell(row=i+1, column=2, value=rec.get("strategy",""))     # 策略
    ws.cell(row=i+1, column=3, value=rec.get("name",""))         # 标的
    ws.cell(row=i+1, column=4, value=rec.get("code",""))         # 代码
    ws.cell(row=i+1, column=5, value=rec.get("sector",""))       # 板块
    ws.cell(row=i+1, column=6, value=rec.get("industry",""))     # 行业
    ws.cell(row=i+1, column=7, value=rec.get("change_pct"))      # 当日涨跌
    ws.cell(row=i+1, column=8, value=rec.get("open"))            # 开盘价 ← 新增
    ws.cell(row=i+1, column=9, value=rec.get("close"))           # 收盘价 ← 新增
    ws.cell(row=i+1, column=10, value=rec.get("turnover"))       # 换手率 ← 新增
    ws.cell(row=i+1, column=11, value=rec.get("amplitude"))      # 振幅 ← 新增
    ws.cell(row=i+1, column=12, value=rec.get("reason",""))      # 预测逻辑
    ws.cell(row=i+1, column=13, value=rec.get("score"))          # 评分
    ws.cell(row=i+1, column=14, value=rec.get("confidence",""))  # 置信度
    ws.cell(row=i+1, column=15, value=rec.get("entry"))          # 进场
    ws.cell(row=i+1, column=16, value=rec.get("stop_loss"))      # 止损
    ws.cell(row=i+1, column=17, value=rec.get("take_profit"))    # 止盈
    ws.cell(row=i+1, column=18, value=rec.get("url",""))         # 链接
```

**筛选概况（对话中必须输出）**：

```
📊 筛选概况 — prediction_date(数据来源:data_date)
① 原始标的池:N只 → ② 硬排除:N只 → ③ 信号过滤:N只 → ④ 策略匹配:N只 → ⑤ 行业限制:N只 → ⑥ 新闻筛查:N只 → ★ 最终:N只
策略分布: A:N B:N C:N D:N E:N
排除TOP5: 股价<5:X只 ST:X只 ...
```

阶段通过数N=按顺序检查：①原始池→②硬排除通过→③信号过滤通过→④策略匹配→⑤行业限制→⑥新闻筛查。若某阶段通过数=0，则其后的阶段通过数也必为0（上游空了，下游无输入）。最终N=⑥新闻筛查通过数，必须等于Excel标的池行数（即最终推荐数）。

## 十二、Excel格式化

表头：Arial 11pt Bold白底蓝(1F4E79)，数据行：Arial 10pt灰边框(B0B0B0)行高22。涨跌红(9C0006)涨绿(006100)跌。策略色：A绿(E2EFDA) B蓝(D6E4F0) C紫(E4DFEC) D黄(FFF2CC)。置信★★★绿加粗/★★黄/★红。链接：蓝下划线(0563C1)，60→sh,00/30→sz,8→bj。创业板标的+⚠️。

**标的池工作表尾部**（数据行下方空一行后追加）：
1. 一行合并单元格居中：`📊 共筛选出 N 只标的`（灰色底 F1F5F9，Arial 12pt Bold）
2. 一行合并单元格居中：`策略说明：`（同上格式，左对齐）
3. 五行分别列出策略说明，每行格式如下（Arial 10pt，左对齐）：
   - `A 动量延续：涨幅3-7%，量比1.5-3.0，MA5>MA10>MA20 — 仓位强35-40%/震荡12-17%/弱关闭`
   - `B 超跌反弹：连跌≥3日，量<5日均×0.6，RSI(14)<35，KDJ(K<20且J拐头)，站上MA5+放量确认，股价≥MA60 — 仓位12-15%`
   - `C 事件驱动：重大合同/预增>50%/部委级政策，事件时效5级衰减 — 仓位5-12%`
   - `D 资金埋伏：北向3日连续净买+主力流入>{northbound_threshold}万+涨幅<2% — 仓位3-8%`
   - `E 回调企稳突破：20日内创新高+回调MA20±3%+连3日缩量+站回MA5放量 — 仓位8-15%`
5. 一行合并单元格居中：`⚠️ 仅供参考，不构成投资建议`（灰色字 6B7280，Arial 9pt）

实现代码示例：
```python
from openpyxl.styles import Font, Alignment, PatternFill
ws = wb["标的池"]
last_data_row = ws.max_row  # 最后一行数据
footer_start = last_data_row + 2  # 空一行

# 统计各策略数量
from collections import Counter
strategy_counts = Counter()
for row in ws.iter_rows(min_row=2, max_row=last_data_row, values_only=True):
    if len(row) > 1 and row[1]:
        strategy_counts[row[1]] += 1

ws.merge_cells(start_row=footer_start, start_column=1, end_row=footer_start, end_column=18)
cell = ws.cell(row=footer_start, column=1, value=f"📊 共筛选出 {final_recommend_count} 只标的（A:{strategy_counts.get('A',0)} B:{strategy_counts.get('B',0)} C:{strategy_counts.get('C',0)} D:{strategy_counts.get('D',0)} E:{strategy_counts.get('E',0)}）")
cell.font = Font(name='Arial', size=12, bold=True)
cell.fill = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')
cell.alignment = Alignment(horizontal='center', vertical='center')

# 策略说明标题
footer_start += 1
ws.merge_cells(start_row=footer_start, start_column=1, end_row=footer_start, end_column=18)
cell = ws.cell(row=footer_start, column=1, value="策略说明：")
cell.font = Font(name='Arial', size=11, bold=True)
cell.alignment = Alignment(horizontal='left')

# 五行策略说明
strategies = [
    ("A 动量延续", "涨幅3-7%，量比1.5-3.0，量>5日均×1.5且>昨日×1.2，MA5>MA10>MA20 — 仓位强35-40%/震荡12-17%/弱关闭"),
    ("B 超跌反弹", "连跌≥3日，量<5日均×0.6，RSI(14)<35，KDJ(K<20且J拐头)，站上MA5+放量确认，股价≥MA60 — 仓位强10-12%/震荡12-15%/弱12-15%"),
    ("C 事件驱动", "重大合同/预增>50%/部委级政策，事件时效5级衰减 — 仓位强10-12%/震荡10-12%/弱5-8%"),
    ("D 资金埋伏", "北向3日连续净买+主力流入>{northbound_threshold}万+涨幅<2% — 仓位强5-8%/震荡5-8%/弱3-5%（连续5日→上限翻倍至16%）"),
    ("E 回调企稳突破", "20日内创新高+回调MA20±3%+连3日缩量+站回MA5放量 — 仓位强10-12%/震荡12-15%/弱8-12%"),
]
for i, (name, desc) in enumerate(strategies):
    footer_start += 1
    ws.merge_cells(start_row=footer_start, start_column=1, end_row=footer_start, end_column=18)
    cell = ws.cell(row=footer_start, column=1, value=f"{name}：{desc}")
    cell.font = Font(name='Arial', size=10)
    cell.alignment = Alignment(horizontal='left', vertical='center')

# 风险提示
footer_start += 2
ws.merge_cells(start_row=footer_start, start_column=1, end_row=footer_start, end_column=18)
cell = ws.cell(row=footer_start, column=1, value="⚠️ 仅供参考，不构成投资建议")
cell.font = Font(name='Arial', size=9, color='6B7280')
cell.alignment = Alignment(horizontal='center')
```

## 十三、最终验证

```python
from openpyxl.styles import Font
wb = safe_read_excel(path)
if wb:
    errors = []
    if "标的池" in wb.sheetnames:
        excel_n = wb["标的池"].max_row - 1
        if excel_n != final_recommend_count:
            errors.append(f"概况{final_recommend_count}≠Excel{excel_n}")
            log_alert("ERROR", "数量校验", f"概况{final_recommend_count}≠Excel{excel_n}")
    for sn in wb.sheetnames:
        for row in wb[sn].iter_rows():
            for c in row:
                if isinstance(c.value, float) and '.' in str(c.value) and len(str(c.value).split('.')[-1])>3:
                    c.value = round(c.value, 3)
                if c.value and c.font and c.font.name and c.font.name != 'Arial':
                    c.font = Font(name='Arial', size=(c.font.size or 10), bold=c.font.bold)
    # 格式化修复无条件保存，错误仅记录日志
    wb.save(path)
    if errors:
        for e in errors:
            log_alert("ERROR", "最终验证", e)
    else:
        print(f"✅ 验证通过（{final_recommend_count}只）")
    wb.close()
```

## 十三.A、GitHub同步 — 推送筛选结果前先校验并同步筛选条件表格

筛选完成后将 `短线标的_YYYYMMDD.xlsx` 同步到 GitHub 仓库 `lc132/lv`。

推送前先检查 `/workspace/A股短线选股筛选条件.xlsx` 版本是否与当前 `file_version` 一致，不一致则先更新后再推送。

⚠️ **不上传推荐历史.json**（含持仓隐私）。仅上传筛选结果 Excel 和筛选条件表格。

**执行逻辑**（失败仅 log_alert WARNING，不影响主流程）：
```python
import subprocess, os, shutil

xlsx_path = f"/workspace/短线标的_{prediction_date}.xlsx"
if not os.path.exists(xlsx_path):
    log_alert("WARNING", "GitHub同步", "xlsx文件不存在，跳过")
    return

# 读取认证令牌
token = None
token_path = "/workspace/.github_token"
if os.path.exists(token_path):
    try:
        with open(token_path, 'r') as f:
            token = f.read().strip()
    except Exception:
        pass
if not token:
    log_alert("WARNING", "GitHub同步", "无认证令牌，跳过推送")
    return

# === 推送前校验并同步筛选条件表格 ===
cond_xlsx = "/workspace/A股短线选股筛选条件.xlsx"
cond_synced = False  # 仅在版本不一致且成功同步后置为 True
xlsx_version = None  # 显式初始化，防止 NameError
if os.path.exists(cond_xlsx):
    try:
        from openpyxl import load_workbook
        from openpyxl.styles import Font, Alignment, Border, Side

        wb_cond = load_workbook(cond_xlsx)
        ws1 = wb_cond['筛选条件概述']
        # 读取xlsx中已记录的版本号（第2行第2列）
        xlsx_version = ws1.cell(row=2, column=2).value
        if xlsx_version and str(xlsx_version) != str(file_version):
            log_alert("INFO", "筛选条件", f"版本不一致: xlsx={xlsx_version} ≠ 当前={file_version}，先同步")

            _cell_font = Font(name='Arial', size=10)
            _bold_font = Font(name='Arial', size=10, bold=True)
            _thin_border = Border(
                left=Side(style='thin', color='B0B0B0'),
                right=Side(style='thin', color='B0B0B0'),
                top=Side(style='thin', color='B0B0B0'),
                bottom=Side(style='thin', color='B0B0B0'),
            )

            # _wc 函数与步骤6中定义一致，提取为独立函数避免重复维护
            def _wc(ws, r, c, v, font=_cell_font):
                for mr in list(ws.merged_cells.ranges):
                    if mr.min_row <= r <= mr.max_row and mr.min_col <= c <= mr.max_col:
                        if not (r == mr.min_row and c == mr.min_col):
                            return
                        ws.unmerge_cells(str(mr))
                cell = ws.cell(row=r, column=c, value=v)
                cell.font = font
                cell.border = _thin_border
                cell.alignment = Alignment(vertical='center', wrap_text=True)

            _wc(ws1, 1, 1, f'A股短线选股筛选条件 — {file_version}', _bold_font)
            _wc(ws1, 2, 2, file_version)
            _wc(ws1, 2, 3, f'{beijing_date}更新')
            vr = ws1.max_row + 1
            _wc(ws1, vr, 1, file_version)
            _wc(ws1, vr, 2, beijing_date)
            _wc(ws1, vr, 3, 'GitHub推送前自动同步')
            if '关键纪律' in wb_cond.sheetnames:
                ws11 = wb_cond['关键纪律']
                _wc(ws11, 1, 1, f'关键纪律 — {file_version}', _bold_font)

            wb_cond.save(cond_xlsx)
            cond_synced = True  # 标记已同步，推送时一并上传
            log_alert("INFO", "筛选条件", f"筛选条件.xlsx 已同步至 {file_version}")
        else:
            log_alert("INFO", "筛选条件", f"版本一致 {file_version}，跳过同步")
    except Exception as e:
        log_alert("WARNING", "筛选条件", f"版本校验/同步失败: {str(e)[:80]}，继续推送")
else:
    log_alert("WARNING", "筛选条件", "筛选条件.xlsx 不存在，跳过校验")
# === 校验结束，开始推送 ===

repo_url = f"https://{token}@github.com/lc132/lv.git"
repo_dir = "/tmp/lv_sync"
try:
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", "main", repo_url, repo_dir],
        capture_output=True, text=True, timeout=30, check=True
    )
    # 推送筛选结果
    shutil.copy(xlsx_path, os.path.join(repo_dir, f"短线标的_{prediction_date}.xlsx"))
    # 若筛选条件表格已同步，一并推送
    if cond_synced and os.path.exists(cond_xlsx):
        shutil.copy(cond_xlsx, os.path.join(repo_dir, "A股短线选股筛选条件.xlsx"))
    subprocess.run(["git", "-C", repo_dir, "config", "user.email", "ashare-bot@github.com"], check=True)
    subprocess.run(["git", "-C", repo_dir, "config", "user.name", "ashare-screener"], check=True)
    subprocess.run(["git", "-C", repo_dir, "add", f"短线标的_{prediction_date}.xlsx"], check=True)
    if cond_synced and os.path.exists(cond_xlsx):
        subprocess.run(["git", "-C", repo_dir, "add", "A股短线选股筛选条件.xlsx"], check=True)
    commit_msg = f"筛选结果 {prediction_date}"
    if cond_synced and xlsx_version and str(xlsx_version) != str(file_version):
        commit_msg += f" + 筛选条件同步至 {file_version}"
    subprocess.run(["git", "-C", repo_dir, "commit", "-m", commit_msg], check=True)
    result = subprocess.run(
        ["git", "-C", repo_dir, "push", "origin", "main"],
        capture_output=True, text=True, timeout=30
    )
    if result.returncode == 0:
        log_alert("INFO", "GitHub同步", f"✅ {prediction_date} 已推送")
    else:
        log_alert("WARNING", "GitHub同步", f"推送失败: {result.stderr[:100]}")
except Exception as e:
    log_alert("WARNING", "GitHub同步", f"失败: {str(e)[:100]}")
finally:
    if os.path.exists(repo_dir):
        shutil.rmtree(repo_dir, ignore_errors=True)
```

## 十三.B、飞书推送 — 筛选结果通过群机器人 Webhook 推送

筛选完成后，通过飞书群机器人 Webhook 发送筛选概况卡片到指定群聊。

**前置条件**：飞书群已添加自定义机器人，获得 Webhook URL（`https://open.feishu.cn/open-apis/bot/v2/hook/xxx`）。

**执行逻辑**（失败仅 log_alert WARNING，不影响主流程）：

```python
import urllib.request, json, os
from collections import Counter

# 从外部文件读取飞书 Webhook URL（不入git，防止泄露）
FEISHU_WEBHOOK = None
webhook_path = "/workspace/.feishu_webhook"
if os.path.exists(webhook_path):
    try:
        with open(webhook_path, 'r') as f:
            FEISHU_WEBHOOK = f.read().strip()
    except Exception:
        pass
if not FEISHU_WEBHOOK:
    log_alert("WARNING", "飞书推送", "未配置Webhook URL，跳过")
    return

# 构建筛选概况卡片
# 以下变量在筛选管道各阶段累积：total_raw/excluded/filtered/matched/industry_limited/news_filtered/final_recommend_count
# 各变量由对应步骤计算。strategy_counts 由步骤13 Counter统计，此处显式初始化防止 NameError
strategy_counts = strategy_counts if 'strategy_counts' in dir() else Counter()
card = {
    "msg_type": "interactive",
    "card": {
        "header": {
            "title": {"tag": "plain_text", "content": f"📊 每日短线标的筛选 — {prediction_date}"},
            "template": "blue"
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**数据来源**: {data_date}"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"原始标的池: **{total_raw}**只 → 硬排除: **{excluded}**只 → 信号过滤: **{filtered}**只 → 策略匹配: **{matched}**只 → 行业限制: **{industry_limited}**只 → 新闻筛查: **{news_filtered}**只 → ★ 最终: **{final_recommend_count}**只"}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"策略分布: A动量:{strategy_counts.get('A',0)} B超跌:{strategy_counts.get('B',0)} C事件:{strategy_counts.get('C',0)} D资金:{strategy_counts.get('D',0)} E回调:{strategy_counts.get('E',0)}"}},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "⚠️ 仅供参考，不构成投资建议"}]}
        ]
    }
}

try:
    req = urllib.request.Request(
        FEISHU_WEBHOOK,
        data=json.dumps(card, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    resp = urllib.request.urlopen(req, timeout=10)
    result = json.loads(resp.read())
    if result.get('code') == 0:
        log_alert("INFO", "飞书推送", f"✅ 筛选概况已推送到飞书群")
    else:
        log_alert("WARNING", "飞书推送", f"推送失败: {result.get('msg','')}")
except Exception as e:
    log_alert("WARNING", "飞书推送", f"请求异常: {str(e)[:100]}")
```

**Excel 文件获取**：筛选结果 xlsx 已同步到 GitHub `lc132/lv`，群成员可从 GitHub 下载。若需直接发送文件，需在飞书开发者后台开通 `im:message`/`im:resource` scope 后走 lark-cli API。

## 十四、每周复盘数据拉取（仅周六执行）

每周六，将 GitHub 上本周所有 `短线标的_YYYYMMDD.xlsx` 文件拉取到本地，汇总生成周度复盘报表，计算本周推荐胜率、平均涨跌、策略分布，推送到飞书群。

```python
import subprocess, os, json, shutil
from datetime import datetime, timedelta

# 从 GitHub 拉取本周所有短线标的文件
# 读取认证令牌（若仓库改为私有，缺少令牌则回退到公开URL）
token = None
token_path = "/workspace/.github_token"
if os.path.exists(token_path):
    try:
        with open(token_path, 'r') as f:
            token = f.read().strip()
    except Exception:
        pass
github_repo = f"https://{token}@github.com/lc132/lv.git" if token else "https://github.com/lc132/lv.git"
temp_dir = "/tmp/lv_weekly_review"
try:
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", "main", github_repo, temp_dir],
        check=True, timeout=60
    )
    # 列出所有短线标的文件
    xlsx_files = []
    for f in os.listdir(temp_dir):
        if f.startswith("短线标的_") and f.endswith(".xlsx"):
            xlsx_files.append((f, os.path.join(temp_dir, f)))
    if not xlsx_files:
        log_alert("INFO", "每周复盘", "本周无推荐文件，跳过")
        return
    # 排序按日期
    xlsx_files.sort()
    log_alert("INFO", "每周复盘", f"拉取到 {len(xlsx_files)} 个推荐文件")
    # 汇总统计...
    # ...（完整统计逻辑在复盘任务中执行）
except Exception as e:
    log_alert("WARNING", "每周复盘", f"拉取失败: {str(e)[:100]}")
finally:
    if os.path.exists(temp_dir):
        shutil.rmtree(temp_dir, ignore_errors=True)
```

**流程**：每日筛选后自动上传到 GitHub `lc132/lv` → 周六自动拉取汇总 → 生成复盘报表推送飞书。

## 十五、完整执行步骤（34步，含3A/4A/4B/4C/10A/10B子步骤）

0.获取北京时间(data_date+prediction_date) → 1.节假日检查 → 2.极端行情 → 3.外围市场 → 3A.开盘前外围(期货跌>1%→降档) → 4.持仓行情同步 → 4A.做T评估 → 4B.持仓跟踪同步 → 4C.持仓危机检查 → 5.推荐历史持久化 → 6.文件初始化 → 7.财报季检测 → 8.大盘判断 → 9.板块轮动 → 10A.全市场API拉取(东方财富clist) → 10B.板块/行业补全(WebSearch) → 11.硬排除31项(含L1/L2/L3分级) → 12.信号过滤14项(记录数量) → 13.五策略筛选 → 14.评分门控(含L3扣分) → 15.冲突处理 → 16.综合评分 → 17.行业限制(记录数量) → 18.新闻筛查(记录数量) → 19.推荐不足降级 → 20.输出Excel(含筛选概况) → 21.最终验证(含数量校验) → 22.写推荐历史+清理90天前 → 23.回溯检查昨日做T → 24.告警日志摘要 → 25.输出📊筛选概况到对话 → 26.GitHub同步(xlsx) → 27.飞书推送(概况+文件) → 28.每周复盘拉取（仅每周六执行）

步骤说明：
- **步骤10A 全市场API拉取**：通过东方财富clist API一次性拉取全A股约5500只标的行情（详见六末尾）。
- **步骤10B 板块/行业补全**：对clist未覆盖的板块/行业/MA/RSI等字段，通过WebSearch逐只补全。
- **步骤24 告警日志摘要**：读取 `/workspace/系统告警.log` 当天记录，在对话中输出告警汇总（若当天无告警则输出「今日无异常」）。
- 其余步骤的详细执行逻辑见正文各对应章节。

## 十六、持久化文件说明（除短线标的文件外，本技能可读写推荐历史.json/持仓跟踪.xlsx/系统告警.log/筛选条件.xlsx；策略调整记录.json只读；绩效统计/周度复盘等由主对话管理）

| 文件 | 操作 |
|------|------|
| **短线标的_YYYYMMDD.xlsx** | 输出预测结果到该文件（唯一输出文件） |
| 推荐历史.json | safe_append_json追加推荐记录 + 清理7天推荐 + 清理90天holding+do_T；步骤4更新holding收盘价 |
| 持仓跟踪.xlsx | 步骤4B同步持仓收盘价（仅更新当前价/市值/盈亏，不修改成本/持仓量） |
| 策略调整记录.json | 只读version+params，不写入 |
| 系统告警.log | 所有异常写入告警日志 |
| **A股短线选股筛选条件.xlsx** | 筛选条件变化时手动更新 `/workspace/A股短线选股筛选条件.xlsx`（11 Sheet），不上传GitHub |

> ⚠️ 绩效统计.xlsx / 周度复盘*.xlsx 均由主对话管理，本技能不操作这些文件。

## ⚠️ 关键纪律

- 步骤零每次先于一切执行
- 每次对话必须展示📊筛选概况全链路数量
- 所有文件读写 safe_ 系列，追加用 safe_append_json
- 不追高(涨停/涨>7%)，同行业≤3只，已持仓排除
- 硬排除31项→信号过滤14项→5大策略匹配→行业限制→新闻筛查的5级管道
- 原始标的池通过东方财富clist API一次性拉取全市场，不再依赖搜索引擎
- Excel必须openpyxl实现红涨绿跌+策略色+置信度色+蓝色链接
- 所有异常写告警日志
- 仅供参考，不构成投资建议