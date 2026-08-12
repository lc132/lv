---
name: ashare-screener
description: A股每日盘前短线标的智能筛选(v6.22.0)。基于前一日收盘数据，通过37步筛选流程+自动整改，输出短线标的_YYYYMMDD.md和可视化HTML报告。同策略+跨策略冠军PK采用基本面+技术面融合7维度。回测报告新增👑皇冠回测板块+按日期均匀采样交易明细。
---
# A股盘前短线标的筛选 v6.22.0

## 版本历史

- **v6.22.0**: 新闻源全网替换——(1)移除Bing网页搜索(反爬/超时/不稳定), 替换为东方财富个股新闻(AKShare直连, 覆盖全市场财经新闻); (2)新增财联社个股新闻(csw API直连, 电报快讯级实时新闻); (3)正面新闻搜索同步改为东方财富, 过滤负面标题; (4)5源并行: 巨潮资讯网+麦蕊智数(公告+跌停)+东方财富+财联社, 可用源从4→5

- **v6.21.4**: 新增步骤28自动整改——筛选完成后自动分析问题(步骤警告/行业资金排名/策略A胜率/新闻源/回测整体), 发现问题自动生成整改方案、修改代码、更新版本、推送GitHub，每次筛选都是自我优化的闭环。

> **版本号单一真相源(SSOT)**：`VERSION` 文件为唯一来源。`ashare_screener.py` / `pre-check-version.py` 运行时读取 `VERSION`；本文件与 `策略调整记录.json` 由 `scripts/sync_version.py` 在发版时同步，**禁止手工硬编码版本号**。
>
> **版本重编号说明（2026-08-05）**：原 `v6.16.38 / v6.16.39 / v6.16.40` 三个补丁实际发生在基线 `v6.20.2` **之后**，编号低于基线属版本回落，现依时间顺序重编号为 `v6.20.3 / v6.20.4 / v6.20.5`。代码注释与本文件已同步。历史 commit message 中的旧编号不再改写，以此说明为准。

> **版本回落修正说明（sunday_industry_pull.py，2026-08-08）**：commit `f399e69` / `fcec3aa` 在基线 `v6.20.6` 时将 `sunday_industry_pull.py` 的版本标记写为 `v6.13.38`，回落 7 个次版本且长期无留档。依 @since 约定（P2-1）该标记已改为引入版本语义 `@since v6.13.39`（记录该特性实际引入版本，非当前版本）；文件"当前版本"声明点（docstring 首行 L4 与 print 语句 L481）同步至 `v6.20.12`（SSOT 锚点，由 `sync_version.py` 保证与 `VERSION` 一致）。版本回落门禁现覆盖该文件：提交信息级 `commit_gate`（commit-msg 钩子 + CI commit-gate 步骤 + 脚本内自动提交前置校验）对**全部 .py 文件的提交**生效，含 sunday_industry_pull.py 的自动提交（P0 Task 1 已接入）；文件级「当前版本」声明点由 `sync_version.py` 锚点校验保证与 VERSION 一致（8 锚点含 sunday L4/L481）。历史 commit message 中的旧编号不再改写，以此说明为准。

> **代码注释版本号约定（P2-1，@since 语义标记）**：为从根源消除"内联版本注释未随发版同步"的遗漏，**所有代码注释/docstring 中的版本号一律使用 `@since vX.Y.Z` 形式**，表示"引入版本"，**不随发版变动**、不参与同步。
> - 仅 `scripts/sync_version.py` 的 **8 个锚点**（ashare_screener.py 模块 docstring 首行 + 兜底常量；pre-check-version.py 兜底常量；SKILL.md frontmatter + H1；lib/backtest.py 模块头 + 兜底常量；sunday_industry_pull.py docstring 首行）为"**当前版本**"声明点，发版时由 `sync_version.py` 同步。
> - 其余内联版本标记（含本次 v6.20.12 的回测日期口径修复、行业白名单治理等）均为 `@since v6.20.12`，即使发版到 v6.20.13 也**保持不动**——它们记录"该特性引入于何版本"，而非"当前版本"。
> - `策略调整记录.json` 的 `"version"` 字段是数据记录（每条记录描述"该版本引入了哪些变更"），同理不随发版改写，不采用 `@since` 前缀。

- **v6.20.6**: 版本治理 P0 整改——(1)scripts/sync_version.py 改锚定正则多点同步，`--check` 真校验真退出 (2)scripts/pre_push_check.py 增加 VERSION 单调性校验、lib 全量编译、提交信息低版本检测 (3)新增 .github/workflows/quality-gate.yml 接入 CI (4)修复自动提交双 v 前缀 (5)策略调整记录.json 追加方向修正为 insert(0) 与 ashare_screener.py 对齐
- **v6.20.5**: 皇冠冠军锁定prediction_date<=today约束——排除未来买入日冠军被误选为最新一期（原记为 v6.16.40，因低于当时基线 v6.20.2 已重编号）
- **v6.20.4**: step22推荐历史is_champion归一化——(1)写推荐历史后对文件全部记录重算is_champion，清旧标记仅本次champion_code标True (2)修复去重跳过导致冠军标的已存在时无法补标、is_champion停留在最早写入冠军(000603错标、002015漏标)的Bug，使推荐历史冠军与主报告冠军一致（原记为 v6.16.39，因低于当时基线 v6.20.2 已重编号）
- **v6.20.3**: 皇冠回测修复——(1)run_backtest改取完整history中prediction_date最大的is_champion记录作为本期冠军；旧"仅当日"匹配因命中记录随即被"排除当天预测"过滤踢除而取不到标的 (2)对冠军记录豁免"排除当天预测"过滤，使其在买入日收盘后可回测（原记为 v6.16.38，因低于当时基线 v6.20.2 已重编号）
- **v6.20.2**: 仓库治理整改（报告#3-#7）——(1).gitattributes 标记生成物 linguist-vendored/generated 修正仓库被识别为HTML (2).gitignore 忽略 .github_token/日志 防凭证泄露 (3)新增 scripts/pre_push_check.py 发版前质量门禁 (4)新增 scripts/lint_commit_msg.py 规范提交信息(type: desc, 拒绝双v) (5)统一机器人提交身份为 ashare-screener/ashare-bot@github.com 常量, 自动提交前缀 data:
- **v6.20.1**: 版本号统一——全部来源(VERSION/SKILL.md/策略调整记录.json/ashare_screener.py)经 SSOT 机制收敛至 v6.20.1，禁止手工硬编码。
- **v6.16.37**: 行业缓存健壮性——(1)新增步骤0B: 筛选前确保 /workspace 行业缓存文件存在且有效，缺失/损坏自动从 GitHub 克隆同步 (2)缓存加载失败/为空改为硬性飞书告警并中止筛选，杜绝静默降级到 L2 代码段映射导致的行业分类错误 (3)SKILL.md 步骤0B 描述同步更新
- **v6.16.34**: 五项整改——(1)策略A震荡市限制: step13新增震荡市A策略上限检查, 超过strategy_a_shock_market_limit(5)只时按评分降序截断; (2)策略E扩大生效: 主力净流入阈值从硬编码3000万改为可配置参数strategy_e_expand_threshold(默认1500万); (3)行业缓存补全: 42只推荐标的行业数据全部命中,无需拉取; (4)废弃API清理: step10D/step11移除质押/商誉/解禁废弃参数和注释; (5)回测优化: no_entry限价未成交改为次日开盘价追入, 按比例调整止损止盈, 追入交易计入有效样本, 预期胜率更真实反映策略有效性。
- **v6.16.33**: 行业缓存修复——(1)修复10只行业分类错误:神火股份/浪潮信息/兴业银锡/中矿资源/赣锋锂业/精达股份/保变电气/华电能源/博源化工/格林美 (2)二级行业补全:47只标的二级行业列为空需排查缓存加载 (3)策略E扩大生效(3→10只),弱市策略A正确关闭(0只)。
- **v6.16.32**: 日期验证——(1)新增步骤-1 date-validator.py，筛选前多源交叉验证北京时间 (2)验证prediction_date是否为今日+交易日 (3)验证data_date是否为交易日 (4)验证输出文件名YYYYMMDD与北京时间一致 (5)验证失败立即中止筛选并发送飞书告警，杜绝日期错位输出。
- **v6.16.31**: 震荡市策略权重优化——(1)新增strategy_a_shock_market_limit=5，震荡市A策略最多入选5只（回测胜率仅20%） (2)strategy_e_expand_threshold从3000降至2000万，扩大资金埋伏策略候选池（胜率87.5%表现最优）。

## 行业白名单治理（P0-4，v6.20.12）
`sunday_industry_pull.py` 的 `_INDUSTRY_CORRECTION` 白名单**不是根治方案**，受以下治理机制约束，防无限膨胀：
- **条目强制字段**：每条目为 `{primary, secondary, source, effective_date, ttl_days}`，缺 `source`/`effective_date` 视为非法（schema 校验不过）。`source` 写清依据（年报/申万指数/人工核定），`effective_date` 为生效日，`ttl_days` 默认 90。
- **多源交叉校验**：热循环对每只股票做 东方财富 + 申万(push2 f127) + 同花顺(最佳努力) 交叉校验；两源一致才采信，不一致且无白名单 → 告警并回退东方财富；某源连续失败(≥20次)自动禁用该源防拖垮墙钟。
- **月度自动复核（≥30天）**：`_monthly_review` 重新拉取白名单标的多源，若上游已与白名单一致（自修正）→ **自动摘除**（记入 `removed`，运行时抑制该条强制覆盖）；TTL 过期 → 标记需人工复核（保留应用+告警）。复核记录落盘 `行业白名单复核记录.json`（`last_review_date` + `reviews[]` + `removed{}`）。
- **审计抽样**：每月对非白名单标的抽样 60 只做多源校验，发现不一致 → 告警（候选补白名单）。
- **维护**：新增条目在 `_INDUSTRY_CORRECTION` 追加一条含 `source/effective_date/ttl_days` 的记录；命中"upstream_corrected"摘除后，可同步删除该 .py 条目保持单一事实源。`行业白名单复核记录.json` 由脚本自动维护、随缓存一并提交。
- **验收口径**：白名单条目具备 TTL 与复核记录；行业缓存主题（行业缓存.json/二级行业缓存.json）仅在真实变更时提交，缓存预热后月度提交数 < 3。

## 关键字段口径说明

> **前置阻断项（2026-08-06 逐代码校验）**：以下字段口径已核验，任何涉及「收益/标签」的下游用途（回测、建模、打分）须严格遵循，避免时间错位导致结论失真。

### change_pct —— 实际当日涨跌幅（非次日、非预期）
- **口径**：`(最新价 - 昨收) / 昨收 × 100`，单位 %。三处计算完全一致：腾讯 `ashare_screener.py:549`、新浪 `:1296`、pytdx `:1333`。
- **性质**：基于真实行情快照的**已实现（realized）当日涨跌幅**，纯算术、无模型、无预测。
- **盘前语义**：盘前拉取的是「前一日收盘数据」，故 change_pct 反映**已收盘的前一交易日**涨跌幅，绝非「下一个交易日会涨多少」。
- **不是**：既非「次日收益」，也非「预期/预测收益」。
- **用途边界**：
  - ✅ 作筛选/评分**特征**：如 `lib/score.py` 按贴近目标涨跌幅打分、`lib/match.py` 策略匹配区间、`lib/output.py` 展示列。
  - ❌ **不可**当作「次日收益标签」做回测 / 监督学习——时间错位一天。
- **正确的「次日/持仓收益」来源**：`lib/backtest.py` 的 `return_pct`（按 `entry→exit` 真实 K 线模拟）；或基于 `prediction_date` 当日开盘/收盘相对 `entry` 计算。
- **前瞻逻辑所在**：`prediction_date` 框架（`:609`）+ `_calc_entry()`（`:4029` 推算次日合理进场价）。二者**以 change_pct 为输入特征**，产出 `entry` 价，不产出「预测涨跌幅」。

### 回测日期口径 —— 运行日 `date` vs 买入日 `prediction_date`（v6.20.12 修复）
- **两个日期字段**：
  - `date`（=`data_date`）：推荐记录**实际生成日**（盘前/盘后运行的当天），`ashare_screener.py:5525` 赋值。
  - `prediction_date`：该标的**实际买入日**（盘前=当日；盘后=下一交易日）。
- **旧 bug**：`run_backtest` 旧逻辑完全以 `prediction_date` 做包含过滤与报表分组。盘后运行的推荐被打上 `prediction_date=次日`，当回测在「买入日当天」运行时，`prediction_date < today` 不成立 → 整批被排除，表现为「回测报告没有昨日数据」。
- **修复（lib/backtest.py v6.20.12）**：
  - 包含/窗口过滤改用 `date`（运行日）；过去任一运行日产生的推荐都应呈现。
  - K线获取起点仍用 `prediction_date`（买入日），保证从实际买入日开盘回放。
  - 报表分组与「日期」列改用 `date`，使「昨日（运行日）」可定位。
  - 买入日尚未收盘（`prediction_date >= today`）的推荐标记 `holding`（`no_data`），仅展示、**不计入胜率**，待买入日收盘后自动转为有效样本——避免盘中噪声污染统计。
- **核对要点**：回测明细「日期」列 = 推荐运行日；要看实际买入日查 `prediction_date`。盘后推荐在「买入日当天」回测会显示「持有中」，属正常，次日自动转为有效样本。

### 步骤 -1: 日期验证（v6.16.32 新增）
**在所有筛选开始前强制执行**。调用 `references/date-validator.py` 从权威授时源（timeapi.io → worldtimeapi.org）获取实时北京时间，交叉验证：
- **prediction_date** 必须等于北京时间今日，且为交易日（非周末/节假日）
- **data_date** 必须为交易日
- **输出文件名 YYYYMMDD** 必须与北京时间一致
- 验证失败 → 立即中止筛选，`log_alert ERROR`，发送飞书红色告警卡片
- 验证通过 → 打印确认日志，继续步骤0。

### 步骤0: 北京时间
使用内置 `step0_get_beijing_time()` 获取北京时间，计算 `data_date`（数据来源日期）和 `prediction_date`（预测日期）。

### 步骤0A: 拉取持仓
从 GitHub 同步 持仓跟踪.xlsx 和推荐历史JSON文件。

### 步骤0B: 行业缓存同步（v6.16.37 新增）
筛选前校验 /workspace 下 `行业缓存.json` 与 `二级行业缓存.json` 是否存在且有效（非空 dict）。缺失/损坏时从数据仓（lc132/lv-data）自动克隆同步；同步失败则发送飞书红色告警并**中止筛选**，不再静默降级到 L2 代码段映射，防止行业分类错误污染结果。

### 步骤1-9C: 市场环境检查
- 步骤1: 节假日检查
- 步骤2: 极端行情
- 步骤2A: 极端行情修复监测（v6.15.0新增）——检测前一日极端行情后的修复力度，4维度评分(上证/深证/创业板/均涨幅，满分10分)，动态调整仓位(40%/25%/20%)
- 步骤3: 外围市场
- 步骤3A: 大盘代理
- 步骤4-4C: 持仓同步/做T/持仓跟踪/持仓危机
- 步骤5: 推荐历史清理
- 步骤6: 文件初始化（版本检查）
- 步骤7: 财报季
- 步骤8: 大盘环境判断（强市/震荡/弱市，决定仓位比例）
- 步骤9-9C: 板块轮动/最大持仓天数/回撤断路器/兑现率闭环

### 步骤10A: 全市场拉取
腾讯一级 > 新浪二级 > pytdx三级降级策略。

### 步骤10B: 行业补全
所有日期统一仅读取磁盘缓存，不执行HTTP拉取。行业缓存由 `sunday_industry_pull.py` 单独维护。

### 步骤10C: 历史K线
三级降级策略：pytdx → 东方财富HTTP → iTick API。计算MA/MACD/KDJ/BOLL等指标。

### 步骤10D-10H: 数据采集
- 10D: 财务数据（质押/商誉/解禁API已废弃降级）
- 10E: F10基本面
- 10F: 风险事件（解禁API降级使用内置数据缓冲）
- 10G: 拥挤度（机构持仓+融资过热代理）
- 10H: 二级行业赋值

### 步骤11-19: 筛选流程
- 步骤11: 13项硬排除（ST/科创/北交/创业板/7日涨幅>7%/涨停/跌停/次新/30日涨幅>30%/庄股/7日新高/7日跌幅>12%/财务异常）
- 步骤12: 27项信号过滤
- 步骤13: 20策略匹配（ABCDEFGHIJKLMNOPQ + RST主力共振）
  - **R 主力共振(强)**: 底仓≥3分 + 起爆≥4分，双重确认
  - **S 主力共振(弱)**: 底仓≥2分 + 起爆≥3分
  - **T 主力观察**: 底仓≥2分 + 起爆≥2分，仅观察不推荐
- 步骤14: 评分（含MACD+K线技术指标加分，最多+8分）
- 步骤15: 微观结构过滤（流动性+消息敏感度）
- 步骤15A: 主力资金流向（东方财富push2主力净流入API）+ 龙虎榜机构席位（东财RPT_DAILYBILLBOARD_DETAILSNEW，单次覆盖全市场）+ 融资融券日变动（同花顺rzrqgg个股页HTML解析，限定final短名单）；接口不可达/限流时优雅降级，绝不阻塞主流程
- 步骤15B: AI策略分析（市场全景+板块研判+个股深度研判，基于最终精选TOP10）
- 步骤16: 综合评分+平局打破
- 步骤17: 行业限制（行业集中度控制，弹性+5）
- 步骤18: 新闻筛查
- 步骤18B: TOP10龙虎榜采集+正面新闻筛查+公司公告
- 步骤19: 降级

### 步骤20-25: 输出
- 步骤20: Markdown输出（行业列+二级行业列+盈亏比列+TOP10⭐标注）
- 步骤20B: HTML报告（深色主题+TOP10板块热度精选+AI分析+可视化图表）
- 步骤21: 最终验证（仅统计推荐表，排除TOP10精选表干扰）
- 步骤22: 推荐历史（周六/周日跳过）
- 步骤25: 历史回测（胜率/均收/盈亏比/夏普比率，输出回测报告+飞书推送）

### 步骤26-27: 同步推送
- 步骤26: GitHub同步
- 步骤27: 飞书推送

## 板块热度精选排序（TOP10）

`_compute_pl_ratios()` 函数对TOP10精选标的进行两级排序：

| 优先级 | 排序键 | 方向 | 说明 |
|--------|--------|------|------|
| 第一优先级 | 板块涨停家数 | 降序 | 所属行业当日涨停家数越多，排名越靠前 |
| 第二优先级 | 盈亏比 | 降序 | 同板块热度下，盈亏比越高越靠前 |

```python
# 先按板块热度降序，再按盈亏比降序
_pl_sorted = sorted(_pl_data, key=lambda x: (-x[2], -x[1]))
```

盈亏比 = (止盈价 - 进场价) / (进场价 - 止损价)，由 `_STRATEGY_STOP_LOSS` 和 `_STRATEGY_TAKE_PROFIT` 全局映射表定义。

## 多因子共振模型

### 主力底仓检测（lib/factor.py）
- 指标1: 连续缩量小阳线（5日内≥3日满足涨0-2%且量<20日均量×0.7）
- 指标2: 底部放量（60日跌幅>15% + 近5日量>20日均量×1.5 + 站上MA10）
- 指标3: 主力资金连续流入（主力净流入>5000万）

### 短线放量起爆检测（lib/factor.py）
- 指标1: 放量突破（量比>2 + 涨幅3-7% + 突破20日最高）
- 指标2: 均线金叉（MA5上穿MA10或MA20）
- 指标3: MACD金叉+零轴附近（DIF上穿DEA，DIF在±0.5范围内）
- 指标4: 成交量突破（当日量>20日均量×2 + 收阳线）

### 共振判定
| 策略 | 底仓分 | 起爆分 | 含义 | 优先级 |
|------|--------|--------|------|--------|
| R | ≥3 | ≥4 | 主力共振(强) | 17 |
| S | ≥2 | ≥3 | 主力共振(弱) | 18 |
| T | ≥2 | ≥2 | 主力观察 | 19 |

## 微观结构过滤

在最终候选池输出前，基于市场微观结构数据进行严格过滤。

### 流动性与冲击成本过滤（lib/microstructure.py）
- **换手率硬过滤**: 换手率 < 2% → 排除
- **Amihud非流动性指标**: |涨跌幅%| / 成交额(万元)，值>2.0 → 排除
- **Tick价差代理**: 收盘价越高 → 相对价差越小（≥50元+1分，≥20元+0.75分）
- **流动性评分**: 换手率(0-2分) + Amihud(0-1分) + Tick价差(0-1分)，满分4分

### 消息敏感度测试（lib/microstructure.py）
- **20日平均振幅**: ≥5% → +1分
- **重大波动频率**: 20日内涨跌幅>5%≥3次 → +1分
- **跳空频率**: 20日内开-收跳空>2%≥2次 → +1分
- **硬过滤**: 20日均振幅 < 2% → 排除

### 评分折算
流动性评分(0-4) + 消息敏感度(0-3) = 最多7分，按比例折算到最终score（最多+3分）。

## AI 策略分析

将单纯的数据筛选升级为AI智能分析，在微观结构过滤后自动生成三类深度分析报告。

### 市场全景分析
- 大盘环境研判（强市/震荡/弱市）
- 三大指数走势与成交额
- 市场情绪（涨停家数、赚钱效应）
- 筛选漏斗全景（各阶段过滤率）
- 资金流向（主力净流入合计与均值）

### 板块深度研判
- 涨停分布（板块→涨停家数→强度评级）
- 推荐标的板块分布
- 板块持续性研判（龙头板块、轮动健康度）

### 个股深度研判（最终精选TOP10每只7维分析）
| 维度 | 内容 |
|------|------|
| 策略逻辑 | 为什么匹配该策略，市场背景支撑 |
| 技术面 | 均线系统、MACD、KDJ、关键价位、量价配合 |
| 资金面 | 主力净流入、成交额、换手率、Amihud流动性、龙虎榜机构席位净买、融资融券余额日变动 |
| 基本面速览 | ROE、净利润同比、质押比例、商誉风险 |
| 风险提示 | 质押/商誉/涨幅过大/前高压力/振幅风险 |
| 操作建议 | 进场区间、止损止盈、盈亏比、持仓周期 |
| 综合研判 | 1-2句话总结，含评分和置信度 |

### 输出集成
- HTML 报告：三个AI分析section（市场全景/板块研判/个股深度研判），每只TOP10标的生成AI分析卡片
- Markdown 报告：市场全景、板块深度研判、个股深度分析三个章节

## HTML 报告

自包含HTML文件，零外部依赖，直接浏览器打开。v6.12.18版本采用深色专业主题：
- **Hero头部**: 渐变背景+微光动画，版本号Badge
- **指数卡片**: 彩色渐变条（涨红跌绿），hover上浮动效
- **筛选漏斗**: 最终结果脉冲发光动画
- **策略分布**: 分段彩色条+图例
- **推荐标的表**: 策略Badge彩色边框，行hover高亮，⭐TOP10标注
- **TOP10卡片**: 指标面板（盈亏比/进场/止损/止盈/评分/成交额/换手率），左侧渐变竖线hover指示器
- **AI分析**: 市场全景+板块研判+个股深度研判，分层排版
- **响应式**: 移动端自适应布局

## 早盘竞价模型

### morning_auction.py（9:26-9:30执行）
- 数据源: 东方财富竞价接口
- 量价关系验证: 竞价量比 + 竞价涨幅 + 竞价匹配率
- 竞价异动检测: 尾盘竞价突变 + 大单托盘/压盘
- 硬过滤: 高开>8%过滤、一字板过滤、竞价跌停过滤、竞价成交额<100万过滤
- 输出: /workspace/auction_result.json

## 尾盘决策模型

### afternoon_decision.py（14:30-15:00执行）
- 数据源: 东方财富分时API + clist主力资金
- 封板时间筛选: 封板时间<10:30
- 距前高筛选: 距60日最高价>10%
- 板块跟风: 同板块≥2只涨停
- 主力净流入: >1亿
- 评分维度: 封板质量(0-3) + 空间潜力(0-3) + 板块强度(0-3) + 资金强度(0-3)
- 输出: /workspace/overnight_result.json

## 仓库结构治理 (P1-1, v6.20.12)

生成物与源码物理分离（main 体积下降约 55%，code diff 信噪比显著提升）：
- **main**：仅源码 + 配置（83 文件 / ~2.4MB），禁止承载制品。
- **gh-pages**：承载全部制品——`ashare-screening-*/`、`*.html`、`短线标的*.md`、`回测报告*`、`推荐历史_*.json`、`持仓跟踪.xlsx`，由 GitHub Pages 托管，报告对外 URL 不变。
- step26_github_sync 已改为推送 `gh-pages`（克隆 `gh-pages` 分支、`git push origin gh-pages`）；`.gitignore` 已忽略上述制品，杜绝 main 再次膨胀。
- lib/sync.py 的 `git add -A` 收紧为仅暂存实际写入的 `SKILL.md` / `策略调整记录.json`。
- 运维：每日运行产出的新报告写入 gh-pages；GitHub Pages 发布源须在仓库 Settings → Pages 设为 **gh-pages** 分支（根目录）。

## 提交信息门禁 (P1-3, v6.20.12)

提交信息须过 `scripts/commit_gate.py` 门禁（SSOT），规则：`^(type)(\(.+\))?:\s.+`，type 含标准 Conventional Commits（fix/feat/data/docs/chore/refactor/test/build）与治理/周末/中文批次（P0整改/P1-1~P1-4/PO-1~PO-3/周日清理/修复/清理/筛选…）；并拦截双 v(vv) 与版本回退。

- **本地钩子**：`hooks/commit-msg` 在 `git commit` 阶段即时拦截，委托 `commit_gate.py` 校验；合并/变基提交自动跳过。
- **启用（克隆仓库执行一次）**：`git config core.hooksPath hooks`。紧急跳过用 `git commit --no-verify`（仍会被 CI `quality-gate` 拦截）。
- **CI 硬门禁（⚠️ 当前未启用分支保护，仅运行级拦截）**：`.github/workflows/quality-gate.yml` 的 `quality-gate` 任务在 push/PR 到 main 时运行，对每条提交经 `commit_gate` 校验，不合规即标红。但**截至 v6.20.12 仍未开启 main 分支保护**，故本门禁可被一行 `git push origin main` 直接绕过（PR #1「代码变更走 PR」未合并即关闭，是 v6.20.2 以来全部门禁的系统性漏洞）。**待办（P0-2）：为 main 开启分支保护，将「质量门禁 (Quality Gate)」设为必需状态检查，对 `*.py`/`SKILL.md`/`VERSION` 强制走 PR**。
- 历史登记在 `data:` 等的自定义 type 已正式写入白名单，合规率由 17.2% 提升至 90%+。

## CI 诊断纪律 (P0-1, v6.20.12)

> **背景**：截至 2026-08-09，quality-gate 共 83 次运行、41 次失败（失败率 ~49%），其中 38 次为直推 `main` 触发、另有 `probe-1785948422` / `tmp-push-probe-60251` 两条临时分支的失败记录——均属**用生产分支/临时分支做诊断探针**导致的虚假红灯。仓库长期处红灯态，门禁结论不可信。**禁止把生产分支当调试场**。

- **禁止用生产分支做诊断探针**：不得向 `main` 推送任何 `_qg_diag*.txt` / 探针脚本 / 临时测试提交来排查 CI；此类操作污染失败率统计并制造假红灯。
- **诊断的正确姿势**：
  1. **本地**：用 [`act`](https://github.com/nektos/act) 在本地跑 workflow（`act -W .github/workflows/quality-gate.yml`）；
  2. **远端**：开**临时分支**（如 `diag/xxx`）推送触发，验证完即删，绝不进 `main`。
- **清理钩子**：`_qg_diag_*.txt` 已纳入 `.gitignore`；若不慎推上 `main`，须立即从 main 删除并 `git fetch -p` 清远端引用。

## 提交者身份治理 (P0-1, v6.20.12)

提交者身份严格收敛为**两个**、且均关联 GitHub 账户（杜绝 `author=null`）：

| 身份 | name | email（GitHub noreply，关联 lc132 账户） |
|------|------|------------------------------------------|
| 人类维护者 | `lc132` | `72593777+lc132@users.noreply.github.com` |
| 机器人 | `ashare-screener` | `72593777+ashare-screener@users.noreply.github.com` |

- **机器人邮箱统一为 GitHub noreply 格式**：原 `ashare-bot@github.com` 无法关联账户（API 返回 `author=null`），v6.20.2 身份统一后仍产生 2 次 IDE（Trae Bot `<bot@trae.ai>`）泄漏提交、累计 28 条 `author=null`（21.5%），均已废弃。代码常量 `BOT_AUTHOR_EMAIL` / `user.email`（`ashare_screener.py` / `sunday_industry_pull.py` / `lib/sync.py`）统一改为 noreply 格式。
- **本地钩子 `hooks/pre-commit`**：硬校验 `git config user.name/user.email` 必须属于上表白名单，不匹配（含 `Trae Bot <bot@trae.ai>`、IDE 默认身份、旧 `ashare-bot@github.com`）**直接拒绝提交**。合并/变基提交（`MERGE_HEAD`/`REBASE_HEAD`）自动跳过。
- **CI 兜底**：`scripts/pre_push_check.py::check_author_email_whitelist` 扫描 `baseline..HEAD` 作者邮箱，仅放行两个 noreply 邮箱 + 历史遗留 `ashare-bot@github.com`（兼容 v6.20.x tag 之前的 legacy 提交，不重写历史）。
- **验收口径**：新提交 `author=null = 0`；提交者身份种类 ≤ 2（历史遗留 null 提交不重写，避免破坏 v6.20.x tag）。

## 策略自动检查机制 (strategy_check, v6.20.12)

`strategy_check` 是一套**以仓位（`position_pct`）为执行杠杆**的自动风控闭环：触发信号写入「推荐历史」（`推荐历史_YYYYMMDD.json`）的 `type=strategy_check` 记录，供次日 / T+1 判定。

### 触发条件与判定周期

| 机制 | 函数 | 触发条件 | 判定周期 | 调整动作 |
|------|------|----------|----------|----------|
| 回撤断路器 | `step9B_circuit_breaker` | 任一持仓当日亏损 > `circuit_breaker_threshold_pct`（默认 3.0%） | 当日触发 + 昨日（`data_date-1`）是否已触发 → **连续 2 日** | 连续 2 日 → `position_pct = 30`；首日 → `max(20, position_pct * 0.5)` |
| T+1 兑现率闭环 | `step9C_conversion_rate` | 近 `conversion_rate_window_days`（默认 10）天兑现率 < `conversion_rate_threshold`（默认 0.3） | T+1（当日推荐 vs 次日收盘），窗口内样本 ≥ 5 才评估 | 低于阈值 → `position_pct = max(20, position_pct - 10)`；≥ `conversion_rate_restore`（默认 0.6）→ `position_pct = min(75, position_pct + 5)` |
| 版本/参数快照 | `step6_file_init` | 每次运行首发版检查 | 运行日 | 向推荐历史追加 `strategy_check{version, params, date}`（当版本首次出现） |

### 数据落点

- 触发记录：`/workspace/推荐历史_*.json` 中 `{"type":"strategy_check","date":...,"checks":{"circuit_breaker_triggered":bool}}`（step9B）与 `{"type":"strategy_check","version":...,"params":...,"date":...}`（step6）。
- step9B 读昨日 `strategy_check` 记录的 `checks.circuit_breaker_triggered` 判断是否「连续 2 日」。

### ✅ 失效 / 死参数（已清理 v6.20.13 与 v6.20.14，仅余 1 项遗留）

以下参数历史上为死参数（定义即死，零活动引用）。经 P0-3(v6.20.13) 与 v6.20.14 治理，绝大多数已物理删除：

| 参数 | 默认值 | 现状 |
|------|--------|------|
| `win_rate_drop_threshold` | 10 | ✅ 已删除（v6.20.13，全仓库 0 引用） |
| `consecutive_weeks` | 2 | ✅ 已删除（v6.20.13，全仓库 0 引用） |
| `max_adjust_params` | 3 | ✅ 已删除（v6.20.13，全仓库 0 引用） |
| `northbound_threshold` | 3000 | ✅ 已删除（v6.20.14，全仓库 0 引用；原「北向资金」策略名实不符，策略F已正名为「主力资金」） |
| `conversion_rate_consecutive_days` | 3 | ⚠️ **唯一遗留**：`ashare_screener.py:1122` 读取但 `step9C` 未使用（读而未用）；`lib/pipeline.py` 有引用但该模块未被 import。待接线或删除（P2 遗留） |

> 注：`search_budget`（默认 25）是**活参数**（step2 区域动态调整 `+5`），**不属于**死参数。
> 其余 P0-3 删除项（`data_tier_*` / `recovery_monitor_enabled` / `mairui_licence_configured` / `date_validation_enabled` / `news_sources` / `capital_flow_source` / `sector_heat_source` / `limit_down_threshold`）同理已从 `DEFAULT_PARAMS` 与 `策略调整记录.json` 清除。

### 验收口径

- 连续 2 日回撤触发 → 次日 `position_pct = 30`（可在推荐历史 `strategy_check` 记录追溯）。
- T+1 兑现率闭环按窗口动态调整仓位，日志含「兑现率 X/Y = Z%」与仓位变动。

## 行业缓存迁出主仓 (P2-2, v6.20.12)

行业缓存（一级 / 二级）+ 白名单复核记录从主仓 `lc132/lv` 迁至独立数据仓 `lc132/lv-data`（LFS 不支持，故独立仓），主仓体积与周提交噪声显著下降（此前每周全量重写产生 ~9,845 行变更）。

- **数据仓 `lc132/lv-data`**：私有，承载 `行业缓存.json` / `二级行业缓存.json` / `行业白名单复核记录.json`，由 `sunday_industry_pull.py` 每周日全量重建并直推（不经 main 分支保护）。
- **主仓清理**：`.gitignore` 已忽略上述三文件，避免重新进入主仓；`step0B` 恢复源改为 `lv-data`（`INDUSTRY_CACHE_REPO`）。
- **读取路径不变**：筛选运行时仍读 `/workspace/行业缓存.json`（磁盘），仅来源仓变更。
- **验收口径**：主仓 `git ls-files` 不再含两个行业缓存；`sunday_industry_pull.py` 周提交落到 `lv-data`，主仓周变更 ≈ 0。