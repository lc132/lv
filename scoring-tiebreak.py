# 评分相同时的二次排序逻辑
def tie_break_sort(recos):
    """对评分相同的标的按二次评估规则排序"""
    def sort_key(rec):
        score = rec.get('score', 0)
        strategy = rec.get('strategy', 'Z')
        strategy_order = {'A': 0, 'B': 1, 'C': 2, 'D': 3, 'E': 4}
        strat_rank = strategy_order.get(strategy, 99)
        
        vol_ratio = rec.get('volume_ratio') or 0
        # 量比归一化到0~1（>3.0视为1）
        vol_score = min(vol_ratio / 3.0, 1.0) if vol_ratio else 0
        
        turnover = rec.get('turnover') or 0
        # 换手率评分：5-15%最优→1分，2-5%→0.6，<2%→0.2，15-25%→0.5，>25%→0.1
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
        # 涨跌幅评分：按策略偏好调整
        if strategy in ('A', 'E'):
            # 动量/突破类：涨幅越小越好（3%最优，>7%扣分）
            c_score = max(0, 1.0 - abs(change_pct - 3) / 7.0)
        elif strategy == 'B':
            # 超跌类：跌幅越深越好（-5%最优，但不超过-10%）
            c_score = max(0, 1.0 - abs(change_pct + 5) / 5.0)
        else:
            # C/D类：涨幅适中
            c_score = max(0, 1.0 - abs(change_pct - 2) / 8.0)
        
        sector_heat = rec.get('sector_rank', 99)  # 板块热度排名（越小越热，默认99）
        s_score = max(0, 1.0 - sector_heat / 20.0)
        
        # 综合二次评分：量比25% + 换手率25% + 涨跌幅25% + 板块热度15% + 策略10%
        tie_score = (vol_score * 0.25 + t_score * 0.25 + c_score * 0.25 
                     + s_score * 0.15 + (1.0 - strat_rank / 10.0) * 0.10)
        
        # 主排序：score 降序，tie_score 降序，strat_rank 升序
        return (-score, strat_rank, -tie_score)
    
    recos.sort(key=sort_key)
    return recos

# 在最终输出前调用
recos = tie_break_sort(recos)
