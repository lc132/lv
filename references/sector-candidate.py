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
