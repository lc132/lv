# A股盘前短线标的筛选 (ashare-screener)

> **当前版本：v6.24.1**

基于**前一日收盘数据**，通过 37 步筛选流程 + 步骤28 自动整改 + 策略级胜率监控熔断，自动输出每日盘前短线标的清单（`短线标的_YYYY-MM-DD.md`）与可视化 HTML 报告，并通过 GitHub 与飞书同步推送。

## 运行流程

- **步骤 -1** 多源交叉验证北京时间（prediction_date / data_date / 输出文件名）
- **步骤 0** 北京时间设定、持仓拉取、行业缓存同步、推荐历史与次日收益采集
- **步骤 1-17** 节假日 / 极端行情 / 外围与大环境判断、全市场与历史K线拉取、信号识别、**21 策略**×29 信号、13 项硬排除、微观结构过滤、评分与行业限制
- **步骤 18-27** AI 策略分析、资金去向、龙头/落地组合、卖出清单、历史回测、报告生成（MD + HTML）、GitHub 与飞书推送
- **步骤 28** 自动整改——检测步骤警告 / 行业资金排名 / 策略胜率 / 新闻源 / 回测整体，命中即自动发版（同步 VERSION、SKILL.md、策略调整记录.json）

## 核心机制

| 机制 | 说明 |
|---|---|
| 策略级胜率 | 回测胜率低的策略**正常参与筛选与推荐**，仅在**皇冠评选环节**被 `crown_min_strategy_winrate`(30%) 门槛拦截，不进入跨策略冠军 PK |
| 皇冠评选 | 同策略 + 跨策略冠军 PK，12 维度（基本面+技术面）融合评分，含历史盈利加成衰减、重复夺冠冷却 |
| 版本治理(SSOT) | `VERSION` 文件为唯一版本真相源，`scripts/sync_version.py` 发版时强制同步 8 处锚点 + SKILL.md + 策略调整记录.json，`pre_push_check.py` 提供质量门禁（version 单调性 / 编译 / 静态检查 / 身份白名单 / 参数审计） |
| 数据源 | 腾讯一级行情、腾讯 HTTP 一级 K 线、iTick 二级 K 线、东方财富/申万/同花顺行业多源交叉校验、巨潮+麦蕊+东方财富+财联社 5 源新闻并联 |

## 目录结构

- `ashare_screener.py` 主筛选脚本（37 步流程）
- `scripts/sync_version.py` SSOT 版本同步器
- `scripts/pre_push_check.py` 发版前质量门禁
- `references/` 辅助脚本（date-validator 等）
- `hooks/` git 钩子（commit-msg / pre-commit 质量门禁）
- `.github/workflows/` CI（quality-gate / cleanup-stale-issues）

## 文档

- 详细流程与版本历史见 [SKILL.md](./SKILL.md)
- 变更记录见 [CHANGELOG.md](./CHANGELOG.md)

---

**免责声明**：本项目仅供研究参考，不构成个人投资建议，市场有风险，决策需谨慎。