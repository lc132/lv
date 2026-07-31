#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股资金流向模块 v6.18.0
基于麦蕊智数API：个股资金流向历史 + 主力资金走势 + 行业聚合
替换旧版：东方财富(502/403)、腾讯ff_(失效)、代理估算
"""
import urllib.request, urllib.error
import json, ssl, time, os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

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


# ============================================================
# 1. 个股资金流向（日级别）
# ============================================================
def get_stock_capital_flow(code, date=None, days=1):
    """
    获取个股资金流向数据
    参数:
        code: 股票代码 (如 '000001')
        date: 目标日期 (YYYY-MM-DD)，None则取最新
        days: 获取天数
    返回: {
        'code': str,
        'date': str,
        'main_buy': float,    # 主力买入(亿元)
        'main_sell': float,   # 主力卖出(亿元)
        'main_net': float,    # 主力净流入(亿元)
        'main_net_rate': float, # 主力净流入率(%)
        'super_large_buy': float, # 超大单买入
        'super_large_sell': float, # 超大单卖出
        'large_buy': float,   # 大单买入
        'large_sell': float,  # 大单卖出
        'north_buy': float,   # 北向买入
        'north_sell': float,  # 北向卖出
        'status': str,        # 状态
        'raw': dict,          # 原始数据
    }
    """
    if not MAIRUI_LICENCE:
        return {'code': code, 'status': 'no_licence', 'main_net': 0}

    try:
        url = f'{MAIRUI_BASE}/hsstock/history/transaction/{code}/{MAIRUI_LICENCE}'
        if days:
            url += f'?lt={days}'
        data = _http_get(url)
        if not data:
            return {'code': code, 'status': 'unavailable', 'main_net': 0}

        j = json.loads(data)
        if not isinstance(j, list) or len(j) == 0:
            return {'code': code, 'status': 'empty', 'main_net': 0}

        # 选择目标日期
        target = None
        if date:
            for d in j:
                if d.get('t', '') == date:
                    target = d
                    break
        if not target:
            target = j[0]  # 最新一条

        # 计算主力资金（四档合计）
        main_buy = (
            (target.get('zmbtdcje', 0) or 0) +
            (target.get('zmbddcje', 0) or 0) +
            (target.get('zmbzdcje', 0) or 0) +
            (target.get('zmbxdcje', 0) or 0)
        )
        main_sell = (
            (target.get('zmstdcje', 0) or 0) +
            (target.get('zmsddcje', 0) or 0) +
            (target.get('zmszdcje', 0) or 0) +
            (target.get('zmsxdcje', 0) or 0)
        )
        main_net = main_buy - main_sell

        # 超大单
        super_large_buy = (target.get('zmbtdcje', 0) or 0)
        super_large_sell = (target.get('zmstdcje', 0) or 0)

        # 大单
        large_buy = (target.get('zmbddcje', 0) or 0)
        large_sell = (target.get('zmsddcje', 0) or 0)

        # 北向资金
        north_buy = (target.get('bdmbtdcje', 0) or 0) + (target.get('bdmbddcje', 0) or 0)
        north_sell = (target.get('bdmstdcje', 0) or 0) + (target.get('bdmsddcje', 0) or 0)

        # 主力净流入率（基于总成交额估算）
        total_volume = main_buy + main_sell
        main_net_rate = (main_net / total_volume * 100) if total_volume > 0 else 0

        return {
            'code': code,
            'date': target.get('t', ''),
            'main_buy': round(main_buy / 1e8, 2),
            'main_sell': round(main_sell / 1e8, 2),
            'main_net': round(main_net / 1e8, 2),
            'main_net_rate': round(main_net_rate, 2),
            'super_large_buy': round(super_large_buy / 1e8, 2),
            'super_large_sell': round(super_large_sell / 1e8, 2),
            'large_buy': round(large_buy / 1e8, 2),
            'large_sell': round(large_sell / 1e8, 2),
            'north_buy': round(north_buy / 1e8, 2),
            'north_sell': round(north_sell / 1e8, 2),
            'north_net': round((north_buy - north_sell) / 1e8, 2),
            'status': 'ok',
            'raw': target,
        }
    except Exception as e:
        return {'code': code, 'status': f'error:{str(e)[:60]}', 'main_net': 0}


# ============================================================
# 2. 主力资金走势（分钟级）
# ============================================================
def get_stock_realtime_flow(code):
    """
    获取个股主力资金走势（分钟级数据）
    返回: {
        'code': str,
        'data': [{t, zdf, lrzj, lczj, jlr, jlrl, lrl, shlrl}, ...],
        'latest': {zdf, jlr, jlrl, lrl, shlrl},
        'status': str,
    }
    """
    if not MAIRUI_LICENCE:
        return {'code': code, 'status': 'no_licence', 'data': [], 'latest': {}}

    try:
        url = f'{MAIRUI_BASE}/hsmy/zlzj/{code}/{MAIRUI_LICENCE}'
        data = _http_get(url, timeout=15)
        if not data:
            return {'code': code, 'status': 'unavailable', 'data': [], 'latest': {}}

        j = json.loads(data)
        if not isinstance(j, list) or len(j) == 0:
            return {'code': code, 'status': 'empty', 'data': [], 'latest': {}}

        # 解析数据
        records = []
        for d in j:
            records.append({
                't': d.get('t', ''),
                'zdf': d.get('zdf', 0),
                'lrzj': d.get('lrzj', 0),      # 流入资金
                'lczj': d.get('lczj', 0),      # 流出资金
                'jlr': d.get('jlr', 0),         # 净流入
                'jlrl': d.get('jlrl', 0),       # 净流入率
                'lrl': d.get('lrl', 0),         # 主力流入率
                'shlrl': d.get('shlrl', 0),     # 散户流入率
            })

        latest = records[-1] if records else {}

        return {
            'code': code,
            'data': records,
            'latest': latest,
            'status': 'ok',
        }
    except Exception as e:
        return {'code': code, 'status': f'error:{str(e)[:60]}', 'data': [], 'latest': {}}


# ============================================================
# 3. 行业资金流向聚合
# ============================================================
def get_sector_capital_flow(candidates, data_date=None):
    """
    批量获取个股资金流向，按行业聚合
    参数:
        candidates: [{code, name, ...}, ...]
        data_date: 目标日期
    返回: {
        'sectors': [{
            'industry': str,
            'count': int,
            'total_main_net': float,   # 行业主力净流入合计(亿)
            'avg_main_net': float,      # 平均主力净流入(亿)
            'stocks': [{code, name, main_net, main_net_rate}, ...],
        }, ...],
        'summary': {
            'total_stocks': int,
            'total_main_net': float,
            'avg_main_net': float,
            'top_inflow_sector': str,
            'top_outflow_sector': str,
        },
        'status': str,
    }
    """
    if not candidates:
        return {'sectors': [], 'summary': {}, 'status': 'no_candidates'}

    cache = _load_industry_cache()

    # 批量获取资金流向
    flows = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        for c in candidates:
            code = c.get('code', '')
            if code:
                futures[executor.submit(get_stock_capital_flow, code, data_date)] = code

        for future in as_completed(futures):
            try:
                result = future.result()
                if result['status'] == 'ok':
                    flows[result['code']] = result
            except:
                pass

    # 按行业聚合
    sector_map = {}  # industry -> {stocks, total_net}
    for c in candidates:
        code = c.get('code', '')
        name = c.get('name', '')
        industry = cache.get(code, '其他')

        if code not in flows:
            continue

        flow = flows[code]
        if industry not in sector_map:
            sector_map[industry] = {'stocks': [], 'total_main_net': 0.0}

        sector_map[industry]['stocks'].append({
            'code': code,
            'name': name,
            'main_net': flow['main_net'],
            'main_net_rate': flow['main_net_rate'],
        })
        sector_map[industry]['total_main_net'] += flow['main_net']

    # 排序并生成结果
    sectors = []
    for industry, data in sector_map.items():
        avg_net = data['total_main_net'] / len(data['stocks']) if data['stocks'] else 0
        sectors.append({
            'industry': industry,
            'count': len(data['stocks']),
            'total_main_net': round(data['total_main_net'], 2),
            'avg_main_net': round(avg_net, 2),
            'stocks': sorted(data['stocks'], key=lambda x: x['main_net'], reverse=True),
        })

    # 按总净流入排序
    sectors.sort(key=lambda x: x['total_main_net'], reverse=True)

    total_stocks = len(flows)
    total_net = sum(s['total_main_net'] for s in sectors)
    avg_net = total_net / total_stocks if total_stocks > 0 else 0

    return {
        'sectors': sectors,
        'summary': {
            'total_stocks': total_stocks,
            'fetched': len(flows),
            'total_main_net': round(total_net, 2),
            'avg_main_net': round(avg_net, 2),
            'top_inflow_sector': sectors[0]['industry'] if sectors and sectors[0]['total_main_net'] > 0 else '',
            'top_outflow_sector': sectors[-1]['industry'] if sectors and sectors[-1]['total_main_net'] < 0 else '',
        },
        'status': 'ok',
    }


# ============================================================
# 4. 主力资金走势聚合（行业级别）
# ============================================================
def get_sector_realtime_flow(candidates):
    """
    获取候选标的的主力资金走势，按行业聚合
    返回: {
        'sectors': [{
            'industry': str,
            'avg_jlr': float,      # 平均净流入
            'avg_jlrl': float,     # 平均净流入率
            'avg_lrl': float,      # 平均主力流入率
            'stocks': [{code, name, jlr, jlrl, lrl}, ...],
        }, ...],
        'status': str,
    }
    """
    if not candidates:
        return {'sectors': [], 'status': 'no_candidates'}

    cache = _load_industry_cache()

    # 批量获取实时走势
    flows = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        for c in candidates:
            code = c.get('code', '')
            if code:
                futures[executor.submit(get_stock_realtime_flow, code)] = code

        for future in as_completed(futures):
            try:
                result = future.result()
                if result['status'] == 'ok' and result['latest']:
                    flows[result['code']] = result
            except:
                pass

    # 按行业聚合
    sector_map = {}
    for c in candidates:
        code = c.get('code', '')
        name = c.get('name', '')
        industry = cache.get(code, '其他')

        if code not in flows:
            continue

        latest = flows[code]['latest']
        if industry not in sector_map:
            sector_map[industry] = {'stocks': [], 'total_jlr': 0.0, 'total_jlrl': 0.0, 'total_lrl': 0.0}

        sector_map[industry]['stocks'].append({
            'code': code,
            'name': name,
            'jlr': latest.get('jlr', 0),
            'jlrl': latest.get('jlrl', 0),
            'lrl': latest.get('lrl', 0),
            'zdf': latest.get('zdf', 0),
        })
        sector_map[industry]['total_jlr'] += latest.get('jlr', 0)
        sector_map[industry]['total_jlrl'] += latest.get('jlrl', 0)
        sector_map[industry]['total_lrl'] += latest.get('lrl', 0)

    sectors = []
    for industry, data in sector_map.items():
        n = len(data['stocks'])
        sectors.append({
            'industry': industry,
            'count': n,
            'avg_jlr': round(data['total_jlr'] / n, 2) if n else 0,
            'avg_jlrl': round(data['total_jlrl'] / n, 2) if n else 0,
            'avg_lrl': round(data['total_lrl'] / n, 2) if n else 0,
            'stocks': sorted(data['stocks'], key=lambda x: x['jlr'] or 0, reverse=True),
        })

    sectors.sort(key=lambda x: x['avg_jlr'], reverse=True)

    return {
        'sectors': sectors,
        'status': 'ok',
    }


# ============================================================
# 5. 主脚本兼容接口
# ============================================================
def step16_capital_flow(ctx):
    """
    资金流向分析（兼容主脚本接口）
    替换旧版：东方财富API + 代理估算
    """
    print("\n" + "=" * 60)
    print("步骤16: 资金流向分析（v6.18.0 麦蕊API）")
    print("=" * 60)

    candidates = ctx.get('candidates', [])
    if not candidates:
        print("  无候选标的，跳过资金流向分析")
        ctx['capital_flow'] = {'sectors': [], 'summary': {}}
        return

    data_date = ctx.get('data_date', datetime.now().strftime('%Y-%m-%d'))

    # 获取行业资金流向
    result = get_sector_capital_flow(candidates, data_date)

    ctx['capital_flow'] = result

    # 打印汇总
    s = result['summary']
    print(f"  资金流向: {s['fetched']}/{s['total_stocks']}只 | "
          f"合计净流入: {s['total_main_net']:+.2f}亿 | "
          f"平均: {s['avg_main_net']:+.2f}亿/只")

    # 打印TOP5行业
    for sec in result['sectors'][:5]:
        sign = '+' if sec['total_main_net'] > 0 else ''
        print(f"  {sec['industry']}: {sec['count']}只 | "
              f"净流入 {sign}{sec['total_main_net']:.2f}亿 | "
              f"平均 {sign}{sec['avg_main_net']:.2f}亿")

    # 北向资金汇总
    north_total = sum(
        c.get('_capital_flow', {}).get('north_net', 0)
        for c in candidates
        if '_capital_flow' in c
    )
    if north_total:
        print(f"  北向资金: 净流入 {north_total:+.2f}亿（{len(candidates)}只候选）")


def step16_enrich_candidates(ctx):
    """
    为候选标的附加资金流向数据（用于后续策略判断）
    """
    candidates = ctx.get('candidates', [])
    if not candidates:
        return

    data_date = ctx.get('data_date', datetime.now().strftime('%Y-%m-%d'))

    # 批量获取资金流向
    flows = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        for c in candidates:
            code = c.get('code', '')
            if code:
                futures[executor.submit(get_stock_capital_flow, code, data_date)] = code

        for future in as_completed(futures):
            try:
                result = future.result()
                if result['status'] == 'ok':
                    flows[result['code']] = result
            except:
                pass

    # 附到候选标的上
    for c in candidates:
        code = c.get('code', '')
        if code in flows:
            c['_capital_flow'] = flows[code]
            c['main_net'] = flows[code]['main_net']
            c['main_net_rate'] = flows[code]['main_net_rate']
            c['north_net'] = flows[code].get('north_net', 0)
        else:
            c['_capital_flow'] = None
            c['main_net'] = 0
            c['main_net_rate'] = 0
            c['north_net'] = 0

    print(f"  资金流向附加: {len(flows)}/{len(candidates)}只")


# ============================================================
# 自测入口
# ============================================================
if __name__ == '__main__':
    print("=" * 60)
    print("资金流向模块 v6.18.0 自测")
    print("=" * 60)

    # 测试1: 个股资金流向
    print("\n[1] 个股资金流向测试")
    for code in ['000001', '600519', '300750']:
        result = get_stock_capital_flow(code)
        if result['status'] == 'ok':
            sign = '+' if result['main_net'] > 0 else ''
            print(f"  {code}: 日期={result['date']} | "
                  f"主力买入={result['main_buy']}亿 | "
                  f"卖出={result['main_sell']}亿 | "
                  f"净流入={sign}{result['main_net']}亿 | "
                  f"北向净={result['north_net']:+.2f}亿")
        else:
            print(f"  {code}: {result['status']}")

    # 测试2: 主力资金走势
    print("\n[2] 主力资金走势测试")
    for code in ['000001', '600519']:
        result = get_stock_realtime_flow(code)
        if result['status'] == 'ok':
            l = result['latest']
            print(f"  {code}: 数据点={len(result['data'])} | "
                  f"最新净流入率={l.get('jlrl', 0):.2f}% | "
                  f"主力流入率={l.get('lrl', 0):.2f}%")
        else:
            print(f"  {code}: {result['status']}")

    # 测试3: 行业资金流向聚合
    print("\n[3] 行业资金流向聚合测试")
    test_candidates = [
        {'code': '000001', 'name': '平安银行'},
        {'code': '600519', 'name': '贵州茅台'},
        {'code': '300750', 'name': '宁德时代'},
        {'code': '002594', 'name': '比亚迪'},
        {'code': '000858', 'name': '五粮液'},
    ]
    result = get_sector_capital_flow(test_candidates)
    print(f"  状态: {result['status']}")
    s = result['summary']
    print(f"  汇总: {s['fetched']}只 | 合计净流入={s['total_main_net']:+.2f}亿")
    for sec in result['sectors']:
        sign = '+' if sec['total_main_net'] > 0 else ''
        print(f"  {sec['industry']}: {sec['count']}只 | "
              f"净流入 {sign}{sec['total_main_net']:.2f}亿")

    print("\n✅ 资金流向模块自测完成")