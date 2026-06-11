---
name: ashare-screener
description: A股每日盘前短线标的智能筛选(v6.4.4)。基于前一日收盘数据，通过 30步筛选流程（北京时间获取→节假日检查→极端行情→外围市场→持仓同步→做T评估→持仓跟踪同步→31项硬排除→14项信号过滤→五大策略评分→行业集中度→新闻筛查→GitHub同步），仅输出短线标的_YYYYMMDD.xlsx 预测次日上涨的标的到Excel。推荐历史json和告警日志仅在自动化中写。当用户需要运行盘前筛选、A股短线选股、每日标的预测时使用。
---

# A股盘前短线标的筛选 v6.4.4

基于前一日完整收盘数据筛选当日有望上涨的A股短线标的。**不追高是硬纪律。**

## 步骤零、北京时间获取（最高优先级，必须第一步执行）

```python
from datetime import datetime
import pytz
beijing_tz = pytz.timezone('Asia/Shanghai')
beijing_now = datetime.now(beijing_tz)
beijing_date = beijing_now.strftime('%Y-%m-%d')
beijing_hour = beijing_now.hour
beijing_weekday = beijing_now.weekday()  # 0=周一,6=周日
```

**交易日对应**：周六/日→跳过本次预测 | 周一→`data_date`=上周五,`prediction_date`=周一 | 周二→周一/周二 | 周三→周二/周三 | 周四→周三/周四 | 周五→周四/周五

所有搜索 query 使用 `data_date`，输出文件名 `/workspace/短线标的_YYYYMMDD.xlsx` 使用 `prediction_date`。pytz不可用→log_alert ERROR，fallback系统时间。

## 可配置参数

从 `/workspace/策略调整记录.json` 数组末条 `params` 字段读取，默认：`search_budget=25, northbound_threshold=100, consecutive_weeks=2, win_rate_drop_threshold=10, limit_down_threshold=100, max_adjust_params=3`

## 系统告警

```python
def log_alert(level, module, message):
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open('/workspace/系统告警.log', 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] [{level}] {module}: {message}\n")
```

触发场景：推荐历史读写失败(ERROR)、持仓行情搜索失败(WARNING)、清理失败(WARNING)、Excel创建失败(ERROR)、版本不一致(INFO)、北京时间获取失败(ERROR)、筛选概况与Excel行数不一致(ERROR)

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
    except (json.JSONDecodeError, FileNotFoundError, PermissionError) as e:
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
    if value is None: return None
    if isinstance(value, (int, float)): return round(float(value), ndigits)
    return value
```

⚠️ JSON追加必须用 `safe_append_json`，禁止直接 `safe_write_json` 追加。

## 前置检查（步骤1-8）

**1.节假日**：搜索中国股市交易日历→节假日跳过；长休≥3日→弱市+仓位≤30%+搜索预算+5

**2.极端行情**：上证跌>3%→跳过；涨>3%→仓位30%仅动量延续；跌停>threshold→跳过

**3.外围市场**：美股三大指数均跌>2%→弱市仓位≤30%；恒生跌>3%→弱市仅超跌反弹；人民币波动>0.5%→暂停策略D。美股/港股假期→跳过此检查

**4.持仓行情同步**：遍历推荐历史中 `type="holding"` 记录，搜索当日收盘价→更新current/pnl_pct/update_date。搜不到→log_alert WARNING保留旧数据。`safe_write_json` 写回推荐历史。

**4A.持仓跟踪.xlsx同步**：步骤4完成后，将更新后的 holding 收盘价同步写入 `/workspace/持仓跟踪.xlsx` 的「持仓明细」sheet。
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
            code = str(h["code"])
            if code in code_row:
                row = code_row[code]
                ws.cell(row=row, column=7).value = h["current"]          # 当前价
                ws.cell(row=row, column=8).value = h["market_value"]     # 市值
                ws.cell(row=row, column=9).value = round(h["pnl_amount"], 2)  # 盈亏额
                ws.cell(row=row, column=10).value = round(h["pnl_pct"], 4)     # 盈亏率
                updated += 1
            else:
                log_alert("WARNING", "持仓跟踪同步", f"{code} 在xlsx中找不到")
        if updated > 0:
            wb.save(path)
            log_alert("INFO", "持仓跟踪同步", f"已更新{updated}只持仓价格")
    except Exception as e:
        log_alert("WARNING", "持仓跟踪同步", f"失败: {str(e)[:100]}")
```

**5.推荐历史持久化**：`safe_read_json` 读取，提取 recommendation(7日内排除)+holding(已持仓排除)。生成后用 `safe_append_json` 追加。清理7天前recommendation+90天前holding/do_T（weekly_review/strategy_check保留）

**6.文件初始化**：策略调整记录.json取末条version+params，损坏→默认v6.4.4。交叉验证推荐历史中strategy_check版本，不一致以策略调整记录为准→log_alert INFO。**首次运行或版本变更→safe_append_json追加type="strategy_check"记录**（含version/params/checks），验证各项条件计数与预期一致

**7.财报季**：1/3/4/8/10月→事件驱动权重×1.5+仓位+5%，动量延续涨幅上限7%→8%

**8.大盘环境**：

| 环境 | 条件 | 总仓位 | 动量(A) | 超跌+事件+资金+回调(B/C/D/E) |
|------|------|--------|------|---------------|
| 强市 | 上证>MA20且MA5>MA10>MA20且涨跌比>2:1且成交>20日均×1.2 | 70-80% | 40% | 30-40% |
| 震荡 | 上证在MA20±2%或涨跌比≈1:1 | 50-60% | 30% | 20-30% |
| 弱市 | 上证<MA20×0.98或涨跌比<1:2或成交<20日均×0.8 | 30-40% | 0-10% | 25-30% |

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
10.涨停反复开板≥3次→排除/降置信（封板意愿弱）
11.缩量反弹：前日跌幅>5%+当日反弹但量<前日量×0.7→降置信(减4分)
12.缩量三连阴：连续3日收阴+量逐日递减(每日量<前日×0.95)→降置信(减3分)
13.竞价爆量：竞价量比>8.0且开盘涨幅>3%→排除（过度炒作）；竞价量比<0.3→降置信(减2分，开盘无人关注)
14.连板后首阴：前3日有连板(≥2板)+当日收阴(跌幅<3%)+成交量>5日均×0.8→标注"首阴候选"+加分(不排除)

## 三、行业集中度

同申万一级行业≤3只，评分前先行业预分配确保≥2个行业。**同策略集中度**：总推荐中同策略标的≤60%（如5只中同策略≤3只），超出则降分排序取前。

## 四、推荐不足降级

3只→全部+放宽至中 | 2只→全部+仅≥中置信 | 1只→仅高置信 | 0只→"无合适标的"+追加空标记

## 五、五大策略

**A动量延续(优1)**：涨幅3-7%、量比1.5-3.0、量>5日均×1.5且>昨日×1.2、MA5>MA10>MA20；加分：板块TOP5；仓位：强30-40%/震荡20-30%/弱0-10%
**B超跌反弹(优2)**：连跌≥3日、量<5日均×0.6、RSI(14)连续≥3日<35或底背离、MA20/MA60支撑、KDJ的K<20且J拐头向上（增强B最低置信★★）；**反弹确认：收盘站上MA5+成交量>昨日×1.2**；仓位：20-25%
**C事件驱动(优3)**：重大合同/预增>50%或部委级政策；仓位：10-15%(财报+5%)；事件时效5级衰减：当日100%→次日80%→第3日50%→4-7日30%→>7日10%；高开>5%→不追
**D资金埋伏(优4)**：北向3日连续净买+主力流入>3000万+涨幅<2%；仓位0-5%；汇率>0.5%暂停。退出：买入后3日涨幅<2%→退出；加仓：北向连续5日净买+仍满足涨幅<2%→仓位上限翻倍至10%
**E回调企稳突破(优5)**：20日内创新高+回调至MA20±3%+连续3日缩量(量<5日均×0.6)+站回MA5放量(量>昨日×1.3)；仓位：强10-15%/震荡10%/弱5%；注意：E与A不能同时匹配，以A优先。假突破过滤：当日上下影线比>2:1(上影>下影×2)→降置信减3分

## 六、板块轮动

资金流入TOP3→动量优先 | 连续3日流入→资金+事件 | 板块龙头涨停→找MA20支撑标的 | 流出TOP5→回避

## 七、评分公式

总分=必选×3+加分×2+参考×1+新闻加分-新闻扣分。**参考项加分：ROE>15%(+2分) + ROE 5-15%(+1分) + ROE<0%(-1分) + 经营现金流为正(+1分)**。置信：≥15★★★ | 10-14★★ | <10★。策略冲突按优先级归类，动量+超跌同时→以动量为准。

**置信度-仓位联动**：同一策略内 ★★★→取仓位上限 | ★★→取仓位中值 | ★→取仓位下限。仓位上限/下限按策略定义取整。

## 八、新闻筛查

排除：减持/暴雷/立案/诉讼/下调评级 | 观察：异常波动/解禁/高管减持 | 加分：预增/合同/调研/上调评级

**时效性衰减**：当日 → 100%权重；2-3日 → 70%权重；4-7日 → 30%权重；>7日 → 0%权重

## 九、做T评估（仅对持仓）

浮亏5-10%→重点评估，仓位≤总持仓1/3；浮亏10-15%→谨慎评估，仓位≤总持仓1/4；>15%→不做T；<5%或浮盈→观望。评估：止跌信号(下影/十字星/站MA5)+波动≥3%+非放量跌+板块预期+无利空。仓位阶梯：<5%观望 | 5-10%≤1/3 | 10-15%≤1/4 | >15%不做T。目标2-3%止盈，-3%止损。累计成功≥3次→重置失败计数器；连续2次失败→放弃。输出 `type="do_T_eval"` 追加推荐历史，回溯检查昨日do_T_eval→do_T缺失则提醒。

## 九.A、最大持仓天数（推荐后管理）

- T+3日收盘较推荐日涨跌幅<2%→主动退出（横盘不作为）
- T+5日收盘较推荐日跌幅>5%→无条件止损（趋势判断失误）
- T+1日盘中跌幅>7%→日内止损（极端行情保护）
- 退出时追加 `type="exit"` 到推荐历史，记录退出日期/价格/盈亏/原因

## 九.B、组合回撤断路器

当日推荐组合的T+1日盘中估算最大亏损>3%→次交易日总仓位降至50%，连续2日触发→降至30%。仅影响下一交易日，不改变策略参数。

## 九.C、T+1兑现率闭环

每次运行前读取推荐历史，统计最近10个交易日的T+1兑现率（收盘涨幅>2%视为兑现）。兑现率<30%→自动降一档仓位（强→震荡→弱→跳过），连续3个交易日兑现率均<30%→暂停推荐1天。兑现率≥60%→仓位恢复至正常档位。

**冷启动保护**：推荐历史中 type="recommendation" 不足 10 条时→跳过兑现率检查，直接使用步骤8的大盘仓位。首次达到 10 条后正常启动闭环。

## 十、回滚

本任务不执行回滚，由周六Task3负责。本任务只读取当前最新参数。

## 十一、输出（含筛选概况）

**Excel**：`/workspace/短线标的_YYYYMMDD.xlsx`（8工作表），prediction_date命名。14列：序号|策略|标的|代码|板块|行业|当日涨跌|预测逻辑|评分|置信度|进场|止损|止盈|链接

**筛选概况（对话中必须输出）**：

```
📊 筛选概况 — prediction_date(数据来源:data_date)
① 原始标的池:N只 → ② 硬排除:N只 → ③ 信号过滤:N只 → ④ 策略匹配:N只 → ⑤ 行业限制:N只 → ⑥ 新闻筛查:N只 → ★ 最终:N只
策略分布: A:N B:N C:N D:N E:N
排除TOP5: 股价<5:X只 ST:X只 ...
```

阶段=0则后续全0。最终N必须=Excel标的池行数，不一致→log_alert ERROR。

## 十二、Excel格式化

表头：Arial 11pt Bold白底蓝(1F4E79)，数据行：Arial 10pt灰边框(B0B0B0)行高22。涨跌红(9C0006)涨绿(006100)跌。策略色：A绿(E2EFDA) B蓝(D6E4F0) C紫(E4DFEC) D黄(FFF2CC)。置信★★★绿加粗/★★黄/★红。链接：蓝下划线(0563C1)，60→sh,00/30→sz,8→bj。创业板标的+⚠️。

**标的池工作表尾部**（数据行下方空一行后追加）：
1. 一行合并单元格居中：`📊 共筛选出 N 只标的`（灰色底 F1F5F9，Arial 12pt Bold）
2. 一行合并单元格居中：`策略说明：`（同上格式，左对齐）
3. 五行分别列出策略说明，每行格式如下（Arial 10pt，左对齐）：
   - `A 动量延续：涨幅3-7%，量比1.5-3.0，MA5>MA10>MA20，量>5日均×1.5且>昨日×1.2 — 仓位强30-40%/震荡20-30%/弱0-10%`
   - `B 超跌反弹：连跌≥3日，量<5日均×0.6，RSI(14)<35，KDJ(K<20且J拐头)，站上MA5+放量确认 — 仓位20-25%`
   - `C 事件驱动：重大合同/预增>50%/部委级政策，事件时效5级衰减 — 仓位10-15%(财报+5%)`
   - `D 资金埋伏：北向3日连续净买+主力流入>3000万+涨幅<2% — 仓位0-5%`
   - `E 回调企稳突破：20日内创新高+回调MA20±3%+连3日缩量+站回MA5放量 — 仓位强10-15%/震荡10%/弱5%`
5. 一行合并单元格居中：`⚠️ 仅供参考，不构成投资建议`（灰色字 6B7280，Arial 9pt）

实现代码示例：
```python
from openpyxl.styles import Alignment
ws = wb["标的池"]
last_data_row = ws.max_row  # 最后一行数据
footer_start = last_data_row + 2  # 空一行

# 统计各策略数量
from collections import Counter
strategy_counts = Counter()  # 从标的池列2(策略)统计

ws.merge_cells(start_row=footer_start, start_column=1, end_row=footer_start, end_column=14)
cell = ws.cell(row=footer_start, column=1, value=f"📊 共筛选出 {final_recommend_count} 只标的（A:{strategy_counts.get('A',0)} B:{strategy_counts.get('B',0)} C:{strategy_counts.get('C',0)} D:{strategy_counts.get('D',0)} E:{strategy_counts.get('E',0)}）")
cell.font = Font(name='Arial', size=12, bold=True)
cell.fill = PatternFill(start_color='F1F5F9', end_color='F1F5F9', fill_type='solid')
cell.alignment = Alignment(horizontal='center', vertical='center')

# 策略说明标题
footer_start += 1
ws.merge_cells(start_row=footer_start, start_column=1, end_row=footer_start, end_column=14)
cell = ws.cell(row=footer_start, column=1, value="策略说明：")
cell.font = Font(name='Arial', size=11, bold=True)
cell.alignment = Alignment(horizontal='left')

# 五行策略说明
strategies = [
    ("A 动量延续", "涨幅3-7%，量比1.5-3.0，MA5>MA10>MA20，量>5日均×1.5且>昨日×1.2 — 仓位强30-40%/震荡20-30%/弱0-10%"),
    ("B 超跌反弹", "连跌≥3日，量<5日均×0.6，RSI(14)<35，KDJ(K<20且J拐头)，站上MA5+放量确认 — 仓位20-25%"),
    ("C 事件驱动", "重大合同/预增>50%/部委级政策，事件时效5级衰减 — 仓位10-15%(财报+5%)"),
    ("D 资金埋伏", "北向3日连续净买+主力流入>3000万+涨幅<2% — 仓位0-5%"),
    ("E 回调企稳突破", "20日内创新高+回调MA20±3%+连3日缩量+站回MA5放量 — 仓位强10-15%/震荡10%/弱5%"),
]
for i, (name, desc) in enumerate(strategies):
    footer_start += 1
    ws.merge_cells(start_row=footer_start, start_column=1, end_row=footer_start, end_column=14)
    cell = ws.cell(row=footer_start, column=1, value=f"{name}：{desc}")
    cell.font = Font(name='Arial', size=10)
    cell.alignment = Alignment(horizontal='left', vertical='center')

# 风险提示
footer_start += 2
ws.merge_cells(start_row=footer_start, start_column=1, end_row=footer_start, end_column=14)
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
            log_alert("ERROR", "数量校验", f"概况{final_recommend_count}≠Excel{excel_n}")
    for sn in wb.sheetnames:
        for row in wb[sn].iter_rows():
            for c in row:
                if isinstance(c.value, float) and '.' in str(c.value) and len(str(c.value).split('.')[-1])>3:
                    c.value = round(c.value, 3)
                if c.value and c.font.name != 'Arial':
                    c.font = Font(name='Arial', size=c.font.size or 10, bold=c.font.bold)
    if errors: wb.save(path)
    else: print(f"✅ 验证通过（{final_recommend_count}只）")
    wb.close()
```

## 十三.A、GitHub同步 — 仅上传筛选结果Excel

筛选完成后将 `短线标的_YYYYMMDD.xlsx` 同步到 GitHub 仓库 `lc132/lv`。

⚠️ **不上传推荐历史.json**（含持仓隐私）。仅上传筛选结果 Excel。

**执行逻辑**（失败仅 log_alert WARNING，不影响主流程）：
```python
import subprocess, os, shutil

xlsx_path = f"/workspace/短线标的_{prediction_date}.xlsx"
if not os.path.exists(xlsx_path):
    log_alert("WARNING", "GitHub同步", "xlsx文件不存在，跳过")
    return

repo_dir = "/tmp/lv_sync"
try:
    subprocess.run(
        ["git", "clone", "--depth", "1", "https://github.com/lc132/lv.git", repo_dir],
        capture_output=True, text=True, timeout=30, check=True
    )
    shutil.copy(xlsx_path, os.path.join(repo_dir, f"短线标的_{prediction_date}.xlsx"))
    subprocess.run(["git", "-C", repo_dir, "config", "user.email", "ashare-bot@github.com"], check=True)
    subprocess.run(["git", "-C", repo_dir, "config", "user.name", "ashare-screener"], check=True)
    subprocess.run(["git", "-C", repo_dir, "add", "."], check=True)
    subprocess.run(["git", "-C", repo_dir, "commit", "-m", f"筛选结果 {prediction_date}"], check=True)
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
        subprocess.run(["rm", "-rf", repo_dir])
```

## 十四、完整执行步骤（30步，含3A/4A/4B子步骤）

0.获取北京时间(data_date+prediction_date) → 1.节假日检查 → 2.极端行情 → 3.外围市场 → 3A.开盘前外围(期货跌>1%→降档) → 4.持仓行情同步 → 4A.做T评估 → 4B.持仓跟踪同步 → 5.推荐历史持久化 → 6.文件初始化 → 7.财报季检测 → 8.大盘判断 → 9.板块轮动 → 10.搜集行情 → 11.硬排除31项(记录原始+排除后数量) → 12.信号过滤14项(记录数量) → 13.五策略筛选 → 14.评分门控 → 15.冲突处理 → 16.综合评分 → 17.行业限制(记录数量) → 18.新闻筛查(记录数量) → 19.推荐不足降级 → 20.输出Excel(含筛选概况) → 21.最终验证(含数量校验) → 22.写推荐历史+清理90天前 → 23.回溯检查昨日做T → 24.告警日志摘要 → 25.输出📊筛选概况到对话 → 26.GitHub同步(xlsx)

## 十五、持久化文件说明（规则：除短线标的文件外，仅读写推荐历史.json和系统告警.log，其他都由主对话管理）

| 文件 | 操作 |
|------|------|
| **短线标的_YYYYMMDD.xlsx** | 输出预测结果到该文件（唯一输出文件） |
| 推荐历史.json | safe_append_json追加推荐记录 + 清理7天推荐 + 清理90天holding+do_T；步骤4更新holding收盘价 |
| 持仓跟踪.xlsx | 步骤4B同步持仓收盘价（仅更新当前价/市值/盈亏，不修改成本/持仓量） |
| 策略调整记录.json | 只读version+params，不写入 |
| 系统告警.log | 所有异常写入告警日志 |

> ⚠️ 绩效统计.xlsx / 周度复盘*.xlsx / 筛选条件*.xlsx 均由主对话管理，本技能不操作这些文件。

## ⚠️ 关键纪律

- 步骤零每次先于一切执行
- 每次对话必须展示📊筛选概况全链路数量
- 所有文件读写 safe_ 系列，追加用 safe_append_json
- 不追高(涨停/涨>7%)，同行业≤3只，已持仓排除
- 硬排除31项→信号过滤14项→5大策略匹配→行业限制→新闻筛查的5级管道
- 搜索预算默认25次，长假+5，不足标注跳过项
- Excel必须openpyxl实现红涨绿跌+策略色+置信度色+蓝色链接
- 所有异常写告警日志
- 仅供参考，不构成投资建议