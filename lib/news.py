#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤18-19: 新闻筛查、推荐不足降级
"""
from lib.core import *
from lib.score import tie_break_sort

# ============================================================
# 步骤18: 新闻筛查
# ============================================================
def step18_news_screening(ctx):
    print("\n" + "=" * 60)
    print("步骤18: 新闻筛查")
    print("=" * 60)
    
    candidates = ctx.get('candidates', [])
    if not candidates:
        ctx['passed_news'] = 0
        return
    
    # 风险关键词批量检查（通过搜索API）
    risk_keywords = ['减持', '暴雷', '立案', '诉讼', '下调评级', '预亏', '披露违规']
    warn_keywords = ['异常波动', '解禁', '高管减持', '问询函', '监管函']
    bonus_keywords = ['预增', '重大合同', '调研', '上调评级', '中标']
    
    # 对每个候选标的做简化的名称+代码搜索
    # 在沙箱环境限制下，通过东方财富新闻搜索接口快速筛查
    eliminated = 0
    news_bonus = 0
    
    for c in candidates:
        code = c.get('code', '')
        name = c.get('name', '')
        has_risk = False
        has_bonus = False
        
        try:
            # 东方财富个股新闻搜索 (快速接口)
            import urllib.parse
            url = f"https://search-api.eastmoney.com/search?pageIndex=1&pageSize=5&keyword={urllib.parse.quote(code + ' ' + name)}&type=8193"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': 'https://so.eastmoney.com/'
            })
            resp = urllib.request.urlopen(req, timeout=3)
            news_data = json.loads(resp.read())
            articles = news_data.get('Data', [])
            
            for art in articles:
                title = art.get('Title', '') or ''
                content = art.get('Content', '') or ''
                text = title + content
                
                # 时间衰减权重：当日100%→2-3日70%→4-7日30%→>7日0%
                pub_date = art.get('PubDate', '') or art.get('Date', '')
                decay_weight = 1.0  # 默认当日
                try:
                    if pub_date:
                        pub_dt = datetime.strptime(pub_date[:10], '%Y-%m-%d')
                        data_dt = datetime.strptime(ctx['data_date'], '%Y-%m-%d')
                        age = (data_dt - pub_dt).days
                        if age <= 1:
                            decay_weight = 1.0
                        elif age <= 3:
                            decay_weight = 0.7
                        elif age <= 7:
                            decay_weight = 0.3
                        else:
                            decay_weight = 0.0
                except:
                    pass
                
                if decay_weight == 0.0:
                    continue
                
                # 检查风险关键词
                for kw in risk_keywords:
                    if kw in text:
                        has_risk = True
                        break
                
                # 检查加分关键词
                for kw in bonus_keywords:
                    if kw in text:
                        has_bonus = True
                        break
        except Exception:
            pass
        
        # 风险标的：扣分但不排除（除非触发排除级关键词）
        if has_risk:
            c['score'] = max(0, c.get('score', 0) - 1)
            c['_news_risk'] = True
            eliminated += 1
        elif has_bonus:
            c['score'] = c.get('score', 0) + 1
            c['_news_bonus'] = True
            news_bonus += 1
    
    # 重新按评分排序
    if eliminated > 0 or news_bonus > 0:
        candidates = tie_break_sort(candidates)
        # 重新计算置信度（SKILL §六: 评分变更后置信度需同步刷新）
        for c in candidates:
            s = c.get('score', 0)
            if s >= 9:
                c['confidence'] = "★★★"
            elif s >= 6:
                c['confidence'] = "★★"
            else:
                c['confidence'] = "★"
    
    ctx['candidates'] = candidates
    ctx['passed_news'] = len(candidates)
    print(f"  新闻筛查: {len(candidates)} 只 → 风险标记{eliminated}只 + 正面加分{news_bonus}只")

# ============================================================
# 步骤19: 推荐不足降级
# ============================================================
def step19_insufficient_downgrade(ctx):
    print("\n" + "=" * 60)
    print("步骤19: 推荐不足降级")
    print("=" * 60)
    
    candidates = ctx.get('candidates', [])
    final_count = len(candidates)
    
    if final_count >= 3:
        print(f"  推荐≥3只，无需降级")
    elif final_count == 2:
        # 仅保留≥中置信
        candidates = [c for c in candidates if c.get('confidence') in ('★★', '★★★')]
        print(f"  推荐2只→仅保留≥中置信: {len(candidates)}只")
    elif final_count == 1:
        candidates = [c for c in candidates if c.get('confidence') == '★★★']
        print(f"  推荐1只→仅保留高置信: {len(candidates)}只")
    else:
        print(f"  无合适标的")
    
    ctx['candidates'] = candidates
    ctx['final_count'] = len(candidates)