#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股板块热度统计模块 v6.18.0
基于麦蕊智数涨停股池 + 行业缓存 + 资金流向聚合
实现：涨停板块热度、行业分布、板块轮动分析、资金流向排名
"""
import urllib.request, urllib.error
import json, ssl, time, os
from datetime import datetime, timedelta
from collections import Counter, defaultdict

# ============================================================
# 全局配置
# ============================================================
MAIRUI_LICENCE = None
_licence_paths = [
    '/workspace/.mairui_licence',
    '/data/user/work/.mairui_licence',
]
for p in _licence_paths:
    try:
        with open(p, 'r') as f:
            MAIRUI_LICENCE = f.read().strip()
        if MAIRUI_LICENCE:
            break
    except:
        pass
if not MAIRUI_LICENCE:
    MAIRUI_LICENCE = os.environ.get('MAIRUI_LICENCE', '')

MAIRUI_BASE = 'https://api.mairuiapi.com'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

# 行业缓存路径
INDUSTRY_CACHE_PATH = '/workspace/行业缓存.json'
_industry_cache = None

# SW1行业名称映射（涨停股池hy字段 -> SW1标准名称）
HY_TO_SW1 = {
    '酒店餐饮': '社会服务',
    '化学制品': '基础化工',
    '教育': '社会服务',
    '汽车整车': '汽车',
    '汽车零部件': '汽车',
    '半导体': '电子',
    '元件': '电子',
    '光学光电子': '电子',
    '消费电子': '电子',
    '电子化学品': '电子',
    '电网设备': '电力设备',
    '电池': '电力设备',
    '光伏设备': '电力设备',
    '风电设备': '电力设备',
    '电机': '电力设备',
    '自动化设备': '机械设备',
    '通用设备': '机械设备',
    '专用设备': '机械设备',
    '轨交设备': '机械设备',
    '工程机械': '机械设备',
    '中药': '医药生物',
    '化学制药': '医药生物',
    '生物制品': '医药生物',
    '医疗器械': '医药生物',
    '医药商业': '医药生物',
    '医疗服务': '医药生物',
    '白酒': '食品饮料',
    '食品加工': '食品饮料',
    '饮料乳品': '食品饮料',
    '调味发酵品': '食品饮料',
    '休闲食品': '食品饮料',
    '房地产开发': '房地产',
    '房地产服务': '房地产',
    '煤炭开采': '煤炭',
    '焦炭加工': '煤炭',
    '工业金属': '有色金属',
    '贵金属': '有色金属',
    '小金属': '有色金属',
    '能源金属': '有色金属',
    '金属新材料': '有色金属',
    '建筑装饰': '建筑装饰',
    '建筑材料': '建筑材料',
    '水泥': '建筑材料',
    '玻璃玻纤': '建筑材料',
    '装修建材': '建筑材料',
    '航空机场': '交通运输',
    '铁路公路': '交通运输',
    '航运港口': '交通运输',
    '物流': '交通运输',
    '银行': '银行',
    '证券': '非银金融',
    '保险': '非银金融',
    '多元金融': '非银金融',
    '计算机设备': '计算机',
    '软件开发': '计算机',
    'IT服务': '计算机',
    '通信设备': '通信',
    '通信服务': '通信',
    '电力': '公用事业',
    '燃气': '公用事业',
    '环保设备': '环保',
    '环境治理': '环保',
    '军工电子': '国防军工',
    '航空装备': '国防军工',
    '航天装备': '国防军工',
    '地面兵装': '国防军工',
    '航海装备': '国防军工',
    '化学纤维': '基础化工',
    '化学原料': '基础化工',
    '农化制品': '基础化工',
    '非金属材料': '基础化工',
    '塑料': '基础化工',
    '橡胶': '基础化工',
    '纺织制造': '纺织服饰',
    '服装家纺': '纺织服饰',
    '饰品': '纺织服饰',
    '造纸': '轻工制造',
    '包装印刷': '轻工制造',
    '家用轻工': '轻工制造',
    '文娱用品': '轻工制造',
    '种植业': '农林牧渔',
    '渔业': '农林牧渔',
    '饲料': '农林牧渔',
    '农产品加工': '农林牧渔',
    '动物保健': '农林牧渔',
    '养殖业': '农林牧渔',
    '钢铁': '钢铁',
    '冶钢原料': '钢铁',
    '普钢': '钢铁',
    '特钢': '钢铁',
    '油服工程': '石油石化',
    '炼化及贸易': '石油石化',
    '油气开采': '石油石化',
    '出版': '传媒',
    '电视广播': '传媒',
    '影视院线': '传媒',
    '数字媒体': '传媒',
    '广告营销': '传媒',
    '游戏': '传媒',
    '旅游及景区': '社会服务',
    '体育': '社会服务',
    '专业服务': '社会服务',
    '检测服务': '社会服务',
    '个护用品': '美容护理',
    '化妆品': '美容护理',
    '医美': '美容护理',
    '互联网电商': '商贸零售',
    '贸易': '商贸零售',
    '一般零售': '商贸零售',
    '专业连锁': '商贸零售',
    '商业物业经营': '商贸零售',
    '综合': '综合',
    '家用电器': '家用电器',
    '厨卫电器': '家用电器',
    '小家电': '家用电器',
    '照明设备': '家用电器',
    '家电零部件': '家用电器',
    '黑色家电': '家用电器',
}


def _load_industry_cache():
    """加载行业缓存"""
    global _industry_cache
    if _industry_cache is not None:
        return _industry_cache
    try:
        with open(INDUSTRY_CACHE_PATH, 'r') as f:
            _industry_cache = json.load(f)
    except:
        _industry_cache = {}
    return _industry_cache


def _http_get(url, timeout=10):
    """通用HTTP GET，带重试"""
    ctx = ssl.create_default_context()
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                return resp.read().decode('utf-8', errors='ignore')
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
        except Exception:
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    return None


def _map_hy_to_sw1(hy_name):
    """将涨停股池的hy字段映射到SW1行业名称"""
    if not hy_name:
        return '其他'
    if hy_name in HY_TO_SW1:
        return HY_TO_SW1[hy_name]
    # 尝试模糊匹配
    for hy_key, sw1_name in HY_TO_SW1.items():
        if hy_key in hy_name or hy_name in hy_key:
            return sw1_name
    return hy_name  # 返回原名


# ============================================================
# 1. 涨停股池获取
# ============================================================
def get_limit_up_pool(data_date):
    """
    获取涨停股池
    返回: (list of stocks, status)
    每个stock: {dm, mc, zf, p, cje, hs, hy, zj, lbc, tj, ...}
    """
    if not MAIRUI_LICENCE:
        return [], 'no_licence'

    try:
        url = f'{MAIRUI_BASE}/hslt/ztgc/{data_date}/{MAIRUI_LICENCE}'
        data = _http_get(url)
        if not data:
            return [], 'unavailable'

        j = json.loads(data)
        if not isinstance(j, list):
            return [], 'format_error'

        return j, 'ok'
    except Exception as e:
        return [], f'error:{str(e)[:60]}'


def get_limit_down_pool(data_date):
    """获取跌停股池"""
    if not MAIRUI_LICENCE:
        return [], 'no_licence'

    try:
        url = f'{MAIRUI_BASE}/hslt/dtgc/{data_date}/{MAIRUI_LICENCE}'
        data = _http_get(url)
        if not data:
            return [], 'unavailable'

        j = json.loads(data)
        return j if isinstance(j, list) else [], 'ok'
    except:
        return [], 'error'


def get_strong_pool(data_date):
    """获取强势股池"""
    if not MAIRUI_LICENCE:
        return [], 'no_licence'

    try:
        url = f'{MAIRUI_BASE}/hslt/qsgc/{data_date}/{MAIRUI_LICENCE}'
        data = _http_get(url)
        if not data:
            return [], 'unavailable'

        j = json.loads(data)
        return j if isinstance(j, list) else [], 'ok'
    except:
        return [], 'error'


# ============================================================
# 2. 涨停板块热度统计
# ============================================================
def get_sector_heat(data_date):
    """
    涨停板块热度统计
    基于涨停股池的hy字段，映射到SW1行业后聚合
    返回: {
        'sectors': [{
            'industry': str,       # SW1行业名称
            'limit_up_count': int, # 涨停数
            'heat': str,           # 热度等级: 🔥火/⚡电/💡星
            'stocks': [{dm, mc, zf, hy_raw}, ...],
            'avg_zf': float,       # 平均涨幅
            'market_cap_ratio': float, # 涨停市值占比
        }, ...],
        'summary': {
            'total_limit_up': int,
            'total_sectors': int,
            'top_sector': str,
            'market_sentiment': str,  # 市场情绪: 强势/正常/偏弱
        },
        'status': str,
    }
    """
    pool, status = get_limit_up_pool(data_date)
    if status != 'ok':
        return {'sectors': [], 'summary': {}, 'status': status}

    # 按SW1行业聚合
    sector_map = defaultdict(lambda: {
        'stocks': [],
        'limit_up_count': 0,
        'total_zf': 0.0,
        'total_zsz': 0.0,
    })

    for stock in pool:
        hy_raw = stock.get('hy', '其他')
        sw1 = _map_hy_to_sw1(hy_raw)

        # 过滤掉非SW1行业的杂项
        if sw1 in ('其他',) and hy_raw not in HY_TO_SW1:
            # 可能是概念板块名称，归类到对应行业
            pass

        sector_map[sw1]['stocks'].append({
            'dm': stock.get('dm', ''),
            'mc': stock.get('mc', ''),
            'zf': round(stock.get('zf', 0), 2),
            'hy_raw': hy_raw,
            'cje': stock.get('cje', 0),
            'zsz': stock.get('zsz', 0),
            'lbc': stock.get('lbc', 0),
        })
        sector_map[sw1]['limit_up_count'] += 1
        sector_map[sw1]['total_zf'] += stock.get('zf', 0)
        sector_map[sw1]['total_zsz'] += stock.get('zsz', 0) or 0

    # 生成排序结果
    sectors = []
    for industry, data in sector_map.items():
        n = data['limit_up_count']
        avg_zf = data['total_zf'] / n if n > 0 else 0

        # 热度等级
        if n >= 8:
            heat = '🔥🔥🔥'
        elif n >= 5:
            heat = '🔥🔥'
        elif n >= 3:
            heat = '🔥'
        elif n >= 2:
            heat = '⚡'
        else:
            heat = '💡'

        sectors.append({
            'industry': industry,
            'limit_up_count': n,
            'heat': heat,
            'stocks': data['stocks'],
            'avg_zf': round(avg_zf, 2),
            'total_zsz': round(data['total_zsz'] / 1e8, 2),  # 亿元
        })

    # 按涨停数排序
    sectors.sort(key=lambda x: x['limit_up_count'], reverse=True)

    total_limit_up = len(pool)
    total_sectors = len(sectors)

    # 市场情绪判断
    if total_limit_up >= 100:
        sentiment = '强势'
    elif total_limit_up >= 50:
        sentiment = '正常'
    else:
        sentiment = '偏弱'

    return {
        'sectors': sectors,
        'summary': {
            'total_limit_up': total_limit_up,
            'total_sectors': total_sectors,
            'top_sector': sectors[0]['industry'] if sectors else '',
            'top_count': sectors[0]['limit_up_count'] if sectors else 0,
            'market_sentiment': sentiment,
            'limit_down_count': 0,  # 需要额外获取
        },
        'status': 'ok',
    }


# ============================================================
# 3. 推荐标的行业分布
# ============================================================
def get_candidate_industry_distribution(candidates):
    """
    推荐标的行业分布统计
    返回: [{
        'industry': str,
        'count': int,
        'pct': float,
        'stocks': [{code, name, score}, ...],
        'focus_level': str,  # 重点关注/一般关注/分散
    }, ...]
    """
    if not candidates:
        return []

    cache = _load_industry_cache()
    total = len(candidates)

    sector_map = defaultdict(lambda: {'stocks': [], 'count': 0})

    for c in candidates:
        code = c.get('code', '')
        name = c.get('name', '')
        industry = cache.get(code, '其他')

        sector_map[industry]['stocks'].append({
            'code': code,
            'name': name,
            'score': c.get('score', 0),
        })
        sector_map[industry]['count'] += 1

    sectors = []
    for industry, data in sector_map.items():
        pct = data['count'] / total * 100 if total > 0 else 0
        if pct >= 15:
            focus = '重点关注'
        elif pct >= 8:
            focus = '一般关注'
        else:
            focus = '分散'

        sectors.append({
            'industry': industry,
            'count': data['count'],
            'pct': round(pct, 1),
            'stocks': sorted(data['stocks'], key=lambda x: x['score'], reverse=True),
            'focus_level': focus,
        })

    sectors.sort(key=lambda x: x['count'], reverse=True)
    return sectors


# ============================================================
# 4. 板块轮动分析
# ============================================================
def get_sector_rotation_analysis(candidates, data_date):
    """
    板块轮动分析
    对比涨停板块与推荐板块的重合度
    返回: {
        'overlap_sectors': [str],       # 涨停与推荐重合板块
        'overlap_count': int,           # 重合板块数
        'unique_limit_up': [str],       # 仅涨停的板块
        'unique_candidate': [str],      # 仅推荐的板块
        'concentration': {
            'top3_pct': float,          # TOP3涨停占比
            'conclusion': str,          # 集中度结论
        },
        'rotation_signal': str,         # 轮动信号
    }
    """
    # 获取涨停板块
    heat_result = get_sector_heat(data_date)
    limit_up_sectors = {s['industry'] for s in heat_result.get('sectors', [])}

    # 获取推荐板块
    distribution = get_candidate_industry_distribution(candidates)
    candidate_sectors = {d['industry'] for d in distribution}

    # 重合分析
    overlap = limit_up_sectors & candidate_sectors
    unique_limit = limit_up_sectors - candidate_sectors
    unique_candidate = candidate_sectors - limit_up_sectors

    # TOP3涨停占比
    top3_count = sum(s['limit_up_count'] for s in heat_result.get('sectors', [])[:3])
    total_limit = heat_result.get('summary', {}).get('total_limit_up', 1)
    top3_pct = top3_count / total_limit * 100 if total_limit > 0 else 0

    if top3_pct >= 50:
        conc = '热点集中，主线明确'
    elif top3_pct >= 30:
        conc = '热点分散但主线清晰'
    else:
        conc = '热点分散，无明显主线'

    # 轮动信号
    if len(overlap) >= 5:
        rotation = '涨停与推荐高度重合，板块联动性强'
    elif len(overlap) >= 3:
        rotation = '部分重合，关注轮动节奏'
    else:
        rotation = '重合度低，市场风格切换中'

    return {
        'overlap_sectors': sorted(overlap),
        'overlap_count': len(overlap),
        'unique_limit_up': sorted(unique_limit),
        'unique_candidate': sorted(unique_candidate),
        'concentration': {
            'top3_pct': round(top3_pct, 1),
            'conclusion': conc,
        },
        'rotation_signal': rotation,
    }


# ============================================================
# 5. 综合板块热度报告
# ============================================================
def get_sector_heat_report(candidates, data_date):
    """
    综合板块热度报告（整合所有维度）
    返回: {
        'limit_up_heat': {...},        # 涨停板块热度
        'candidate_distribution': [...], # 推荐标的行业分布
        'rotation_analysis': {...},    # 板块轮动分析
        'market_sentiment': str,       # 综合市场情绪
        'top_recommendation': str,     # 重点推荐板块
    }
    """
    # 涨停板块热度
    heat = get_sector_heat(data_date)

    # 推荐标的行业分布
    distribution = get_candidate_industry_distribution(candidates)

    # 板块轮动分析
    rotation = get_sector_rotation_analysis(candidates, data_date)

    # 综合市场情绪
    sentiment = heat.get('summary', {}).get('market_sentiment', '未知')
    overlap_count = rotation.get('overlap_count', 0)

    if sentiment == '强势' and overlap_count >= 5:
        overall_sentiment = '强势共振'
    elif sentiment == '强势' or overlap_count >= 5:
        overall_sentiment = '偏强'
    elif sentiment == '偏弱' and overlap_count < 3:
        overall_sentiment = '弱势'
    else:
        overall_sentiment = '中性'

    # 重点推荐：涨停数最多且与推荐重合的板块
    top_rec = ''
    if heat.get('sectors'):
        for s in heat['sectors']:
            if s['industry'] in rotation.get('overlap_sectors', []):
                top_rec = s['industry']
                break
        if not top_rec:
            top_rec = heat['sectors'][0]['industry']

    return {
        'limit_up_heat': heat,
        'candidate_distribution': distribution,
        'rotation_analysis': rotation,
        'market_sentiment': overall_sentiment,
        'top_recommendation': top_rec,
    }


# ============================================================
# 6. 主脚本兼容接口
# ============================================================
def step17_sector_heat(ctx):
    """
    板块热度统计（兼容主脚本接口）
    替换旧版：东方财富板块接口+硬编码
    """
    print("\n" + "=" * 60)
    print("步骤17: 板块热度统计（v6.18.0 麦蕊涨停股池）")
    print("=" * 60)

    candidates = ctx.get('candidates', [])
    data_date = ctx.get('data_date', datetime.now().strftime('%Y-%m-%d'))

    # 获取综合板块热度
    report = get_sector_heat_report(candidates, data_date)

    ctx['sector_heat'] = report

    # 打印涨停板块热度
    heat = report['limit_up_heat']
    s = heat.get('summary', {})
    print(f"  涨停板块热度: {s.get('total_limit_up', 0)}只涨停 | "
          f"{s.get('total_sectors', 0)}个板块 | "
          f"市场情绪: {s.get('market_sentiment', '未知')} | "
          f"TOP板块: {s.get('top_sector', '')}({s.get('top_count', 0)}只)")

    # 打印TOP10板块
    for sec in heat.get('sectors', [])[:10]:
        stocks_str = ','.join(
            s['mc'] for s in sec['stocks'][:3]
        )
        print(f"  {sec['heat']} {sec['industry']}: "
              f"{sec['limit_up_count']}只涨停 | "
              f"代表: {stocks_str}")

    # 打印行业分布
    dist = report['candidate_distribution']
    print(f"\n  推荐标的行业分布: {len(dist)}个行业")
    for d in dist[:5]:
        print(f"  {d['industry']}: {d['count']}只({d['pct']}%) | {d['focus_level']}")

    # 打印轮动分析
    rot = report['rotation_analysis']
    print(f"\n  板块轮动: {rot['rotation_signal']}")
    print(f"  重合板块({rot['overlap_count']}个): {', '.join(rot['overlap_sectors'][:5])}")
    print(f"  集中度: TOP3占比{rot['concentration']['top3_pct']}% | {rot['concentration']['conclusion']}")


# ============================================================
# 自测入口
# ============================================================
if __name__ == '__main__':
    print("=" * 60)
    print("板块热度统计模块 v6.18.0 自测")
    print("=" * 60)

    test_date = '2026-07-29'

    # 测试1: 涨停板块热度
    print(f"\n[1] 涨停板块热度测试（{test_date}）")
    heat = get_sector_heat(test_date)
    s = heat.get('summary', {})
    print(f"  状态: {heat['status']}")
    print(f"  涨停总数: {s.get('total_limit_up', 0)} | "
          f"板块数: {s.get('total_sectors', 0)} | "
          f"情绪: {s.get('market_sentiment', '')}")

    for sec in heat.get('sectors', [])[:10]:
        print(f"  {sec['heat']} {sec['industry']}: "
              f"{sec['limit_up_count']}只涨停 | "
              f"代表: {', '.join(s['mc'] for s in sec['stocks'][:3])}")

    # 测试2: 推荐标的行业分布
    print("\n[2] 推荐标的行业分布测试")
    test_candidates = [
        {'code': '000001', 'name': '平安银行', 'score': 85},
        {'code': '600519', 'name': '贵州茅台', 'score': 90},
        {'code': '300750', 'name': '宁德时代', 'score': 88},
        {'code': '002594', 'name': '比亚迪', 'score': 82},
        {'code': '000858', 'name': '五粮液', 'score': 80},
        {'code': '601398', 'name': '工商银行', 'score': 75},
        {'code': '600036', 'name': '招商银行', 'score': 78},
        {'code': '601012', 'name': '隆基绿能', 'score': 76},
    ]
    dist = get_candidate_industry_distribution(test_candidates)
    for d in dist:
        print(f"  {d['industry']}: {d['count']}只({d['pct']}%) | {d['focus_level']}")

    # 测试3: 板块轮动分析
    print("\n[3] 板块轮动分析测试")
    rotation = get_sector_rotation_analysis(test_candidates, test_date)
    print(f"  轮动信号: {rotation['rotation_signal']}")
    print(f"  重合板块: {rotation['overlap_sectors']}")
    print(f"  仅涨停板块: {rotation['unique_limit_up'][:5]}")
    print(f"  仅推荐板块: {rotation['unique_candidate']}")
    print(f"  集中度: TOP3={rotation['concentration']['top3_pct']}% | "
          f"{rotation['concentration']['conclusion']}")

    # 测试4: 综合报告
    print("\n[4] 综合板块热度报告测试")
    report = get_sector_heat_report(test_candidates, test_date)
    print(f"  市场情绪: {report['market_sentiment']}")
    print(f"  重点推荐板块: {report['top_recommendation']}")

    print("\n✅ 板块热度统计模块自测完成")