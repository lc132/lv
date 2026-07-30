#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股新闻筛查模块 v6.17.0
基于推荐方案重构：巨潮资讯网(公告) + 麦蕊智数新API(公告+涨跌停) + Bing网页(利空搜索)
替换旧版：财联社(签名错误)、百度(反爬)、东方财富(502)、麦蕊旧域名(404)
"""
import urllib.request, urllib.parse, urllib.error
import json, re, ssl, time, os
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
CNINFO_BASE = 'http://www.cninfo.com.cn'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'

# 利空关键词
NEGATIVE_KEYWORDS = [
    'ST警示', '风险警示', '退市', '终止上市', '暂停上市',
    '重大亏损', '巨额亏损', '业绩预亏', '净利润为负',
    '立案调查', '证监会调查', '行政处罚', '监管函',
    '重大诉讼', '债务违约', '破产重整', '破产清算',
    '减持', '股份冻结', '司法冻结', '质押爆仓',
    '商誉减值', '资产减值', '计提减值',
    '审计否定', '无法表示意见', '保留意见',
    '停产', '停工', '重大事故',
    '违规担保', '关联交易', '资金占用',
    '重组失败', '终止重组',
]

def _http_get(url, headers=None, timeout=10, encoding='utf-8', binary=False):
    """通用HTTP GET，带重试"""
    ctx = ssl.create_default_context()
    req_headers = {'User-Agent': UA}
    if headers:
        req_headers.update(headers)
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                if binary:
                    return resp.read()
                raw = resp.read()
                try:
                    return raw.decode(encoding, errors='ignore')
                except:
                    return raw.decode('utf-8', errors='ignore')
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
# 1. 巨潮资讯网 — 公告全文搜索
# ============================================================
def check_cninfo(code, stock_name, data_date, lookback_days=30):
    """
    巨潮资讯网公告全文搜索，检测利空内容
    返回: (has_negative, details_list, status)
    """
    try:
        edate = data_date
        sdate = (datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        
        search_key = urllib.parse.quote(code)
        url = '{}/new/fulltextSearch/full?searchkey={}&sdate={}&edate={}&isfulltext=false&sortName=pubdate&sortType=desc&pageNum=1&pageSize=10'.format(
            CNINFO_BASE, search_key, sdate, edate)
        
        headers = {
            'Referer': '{}/new/fulltextSearch'.format(CNINFO_BASE),
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
        }
        data = _http_get(url, headers=headers)
        if not data:
            return False, [], 'cninfo_unavailable'
        
        j = json.loads(data)
        announcements = j.get('announcements', [])
        if not announcements:
            return False, [], 'cninfo_empty'
        
        negatives = []
        for ann in announcements:
            title = ann.get('announcementTitle', '')
            title_clean = re.sub(r'<[^>]+>', '', title)
            for kw in NEGATIVE_KEYWORDS:
                if kw in title_clean:
                    negatives.append({
                        'source': 'cninfo',
                        'title': title_clean[:100],
                        'keyword': kw,
                        'date': ann.get('announcementTime', ''),
                        'url': ann.get('adjunctUrl', ''),
                    })
                    break
        
        return len(negatives) > 0, negatives, 'cninfo_ok'
    except Exception as e:
        return False, [], 'cninfo_error:{}'.format(str(e)[:60])


# ============================================================
# 2. 麦蕊智数新API — 公告
# ============================================================
def check_mairui_ann(code, stock_name, data_date, lookback_days=30):
    """
    麦蕊智数新API公告查询
    返回: (has_negative, details_list, status)
    """
    if not MAIRUI_LICENCE:
        return False, [], 'mairui_no_licence'
    
    try:
        sdate = (datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=lookback_days)).strftime('%Y-%m-%d')
        url = '{}/hsstock/announcement/{}/{}'.format(MAIRUI_BASE, code, MAIRUI_LICENCE)
        data = _http_get(url)
        if not data:
            return False, [], 'mairui_ann_unavailable'
        
        j = json.loads(data)
        if not isinstance(j, list):
            return False, [], 'mairui_ann_format_error'
        
        negatives = []
        for ann in j:
            ann_date = ann.get('t', '')
            if ann_date < sdate:
                continue
            title = ann.get('zt', '')
            for kw in NEGATIVE_KEYWORDS:
                if kw in title:
                    negatives.append({
                        'source': 'mairui',
                        'title': title[:100],
                        'keyword': kw,
                        'date': ann_date,
                    })
                    break
        
        return len(negatives) > 0, negatives, 'mairui_ann_ok'
    except Exception as e:
        return False, [], 'mairui_ann_error:{}'.format(str(e)[:60])


# ============================================================
# 3. 麦蕊智数新API — 涨停/跌停股池
# ============================================================
def get_mairui_limit_pool(data_date, pool_type='ztgc'):
    """
    获取涨停/跌停/强势/炸板股池
    pool_type: ztgc(涨停), dtgc(跌停), qsgc(强势), zbcg(炸板)
    返回: (list of stocks, status)
    """
    if not MAIRUI_LICENCE:
        return [], 'mairui_no_licence'
    
    try:
        url = '{}/hslt/{}/{}/{}'.format(MAIRUI_BASE, pool_type, data_date, MAIRUI_LICENCE)
        data = _http_get(url)
        if not data:
            return [], 'mairui_pool_unavailable'
        
        j = json.loads(data)
        if not isinstance(j, list):
            return [], 'mairui_pool_format_error'
        
        return j, 'mairui_pool_ok'
    except Exception as e:
        return [], 'mairui_pool_error:{}'.format(str(e)[:60])


def check_mairui_dt(code, data_date):
    """检查个股是否在跌停股池中"""
    dt_pool, status = get_mairui_limit_pool(data_date, 'dtgc')
    if status != 'mairui_pool_ok':
        return False, status
    
    for stock in dt_pool:
        if stock.get('dm', '') == code:
            return True, 'mairui_dt_match'
    return False, 'mairui_dt_ok'


# ============================================================
# 4. Bing网页搜索 — 利空检测
# ============================================================
def check_bing_web(code, stock_name, data_date):
    """
    Bing网页搜索利空信息
    返回: (has_negative, details_list, status)
    """
    try:
        query = urllib.parse.quote('{} 利空 公告'.format(stock_name))
        url = 'https://www.bing.com/search?q={}&setlang=zh-cn'.format(query)
        headers = {
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        }
        data = _http_get(url, headers=headers)
        if not data:
            return False, [], 'bing_unavailable'
        
        snippets = re.findall(
            r'<li class="b_algo".*?<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>.*?<p[^>]*>(.*?)</p>',
            data, re.DOTALL
        )
        
        negatives = []
        for href, title, snippet in snippets:
            combined = title + snippet
            for kw in NEGATIVE_KEYWORDS:
                if kw in combined:
                    negatives.append({
                        'source': 'bing',
                        'title': re.sub(r'<[^>]+>', '', title)[:100],
                        'keyword': kw,
                        'url': href,
                    })
                    break
        
        return len(negatives) > 0, negatives, 'bing_ok'
    except Exception as e:
        return False, [], 'bing_error:{}'.format(str(e)[:60])


# ============================================================
# 5. 综合新闻筛查（多源并行）
# ============================================================
def check_stock_news(code, stock_name, data_date, lookback_days=30, timeout=15):
    """
    多源并行新闻筛查，任一源发现利空即排除
    返回: {
        'excluded': bool,
        'sources_checked': int,
        'sources_available': int,
        'negatives': list,
        'details': dict,
    }
    """
    sources_available = 0
    all_negatives = []
    details = {}
    
    # 源1: 巨潮资讯网
    has_neg, negs, status = check_cninfo(code, stock_name, data_date, lookback_days)
    details['cninfo'] = status
    if has_neg:
        all_negatives.extend(negs)
    if 'ok' in status:
        sources_available += 1
    
    # 源2: 麦蕊智数公告
    if MAIRUI_LICENCE:
        has_neg, negs, status = check_mairui_ann(code, stock_name, data_date, lookback_days)
        details['mairui_ann'] = status
        if has_neg:
            all_negatives.extend(negs)
        if 'ok' in status:
            sources_available += 1
    
    # 源3: 麦蕊智数跌停检测
    if MAIRUI_LICENCE:
        is_dt, status = check_mairui_dt(code, data_date)
        details['mairui_dt'] = status
        if is_dt:
            all_negatives.append({
                'source': 'mairui_dt',
                'title': '{}({}) 当日跌停'.format(stock_name, code),
                'keyword': '跌停',
                'date': data_date,
            })
        if 'ok' in status:
            sources_available += 1
    
    # 源4: Bing网页搜索
    has_neg, negs, status = check_bing_web(code, stock_name, data_date)
    details['bing'] = status
    if has_neg:
        all_negatives.extend(negs)
    if 'ok' in status:
        sources_available += 1
    
    return {
        'excluded': len(all_negatives) > 0,
        'sources_checked': 4,
        'sources_available': sources_available,
        'negatives': all_negatives,
        'details': details,
    }


def batch_check_news(candidates, data_date, max_workers=8):
    """
    批量新闻筛查
    candidates: [(code, stock_name), ...]
    返回: {
        'passed': [(code, stock_name), ...],
        'excluded': [(code, stock_name, reasons), ...],
        'summary': dict,
    }
    """
    passed = []
    excluded = []
    sources_stats = {'cninfo': 0, 'mairui_ann': 0, 'mairui_dt': 0, 'bing': 0}
    total_negatives = 0
    
    dt_pool = None
    if MAIRUI_LICENCE:
        dt_pool, _ = get_mairui_limit_pool(data_date, 'dtgc')
        if dt_pool:
            dt_codes = {s.get('dm', '') for s in dt_pool}
        else:
            dt_codes = set()
    else:
        dt_codes = set()
    
    def check_one(item):
        code, stock_name = item
        return check_stock_news(code, stock_name, data_date)
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_one, c): c for c in candidates}
        for future in as_completed(futures):
            code, stock_name = futures[future]
            try:
                result = future.result()
                if result['excluded']:
                    excluded.append((code, stock_name, result['negatives']))
                    total_negatives += len(result['negatives'])
                else:
                    passed.append((code, stock_name))
                
                for k in sources_stats:
                    if 'ok' in result['details'].get(k, ''):
                        sources_stats[k] += 1
            except Exception:
                passed.append((code, stock_name))
    
    return {
        'passed': passed,
        'excluded': excluded,
        'summary': {
            'total': len(candidates),
            'passed': len(passed),
            'excluded': len(excluded),
            'sources_stats': sources_stats,
            'total_negatives': total_negatives,
            'dt_pool_size': len(dt_codes) if dt_pool else 0,
        },
    }


# ============================================================
# 6. 龙虎榜/正面新闻 (TOP10增强)
# ============================================================
def get_top10_longhubang(data_date):
    """获取涨停股池，用于TOP10龙虎榜展示"""
    if not MAIRUI_LICENCE:
        return [], 'no_licence'
    return get_mairui_limit_pool(data_date, 'ztgc')


def get_top10_positive_news(code, stock_name, data_date):
    """
    获取个股正面新闻（Bing搜索）
    返回: list of {title, url, source}
    """
    try:
        query = urllib.parse.quote('{} 利好 业绩'.format(stock_name))
        url = 'https://www.bing.com/search?q={}&setlang=zh-cn'.format(query)
        data = _http_get(url)
        if not data:
            return []
        
        snippets = re.findall(
            r'<li class="b_algo".*?<a[^>]*href="([^"]+)"[^>]*>([^<]+)</a>.*?<p[^>]*>(.*?)</p>',
            data, re.DOTALL
        )
        
        results = []
        for href, title, snippet in snippets[:3]:
            results.append({
                'title': re.sub(r'<[^>]+>', '', title)[:100],
                'url': href,
                'snippet': re.sub(r'<[^>]+>', '', snippet)[:200],
                'source': 'bing',
            })
        return results
    except:
        return []


# ============================================================
# 7. 主脚本兼容接口（step18/step19）
# ============================================================
def step18_news_screening(ctx):
    """
    新闻筛查（兼容主脚本接口）
    使用多源并行检测：巨潮资讯网 + 麦蕊智数(公告+跌停) + Bing搜索
    利空标的直接排除，正面标的加分
    """
    print("\n" + "=" * 60)
    print("步骤18: 新闻筛查（v6.17.0多源并行）")
    print("=" * 60)

    candidates = ctx.get('candidates', [])
    if not candidates:
        ctx['passed_news'] = 0
        return

    data_date = ctx.get('data_date', datetime.now().strftime('%Y-%m-%d'))
    passed = []
    excluded = []
    news_bonus = 0
    
    dt_codes = set()
    if MAIRUI_LICENCE:
        dt_pool, _ = get_mairui_limit_pool(data_date, 'dtgc')
        if dt_pool:
            dt_codes = {str(s.get('dm', '')) for s in dt_pool}

    for c in candidates:
        code = c.get('code', '')
        name = c.get('name', '')
        
        result = check_stock_news(code, name, data_date, lookback_days=30)
        
        if result['excluded']:
            reasons = [n['keyword'] for n in result['negatives']]
            c['_news_risk'] = True
            c['_news_reasons'] = reasons
            excluded.append(c)
            kw_str = ','.join(reasons[:3])
            print("  ❌ {}({}): 利空排除 [{}]".format(name, code, kw_str))
        else:
            pos_news = get_top10_positive_news(code, name, data_date)
            if pos_news:
                c['score'] = c.get('score', 0) + 1
                c['_news_bonus'] = True
                news_bonus += 1
            passed.append(c)
            if result['sources_available'] < 2:
                print("  ⚠️ {}({}): 仅{}/4源可用".format(name, code, result['sources_available']))

    ctx['candidates'] = passed
    ctx['passed_news'] = len(passed)
    print("  新闻筛查: {}只通过 | 排除{}只 | 正面加分{}只".format(len(passed), len(excluded), news_bonus))
    if excluded:
        print("  排除明细: " + '; '.join(
            "{}({})[{}]".format(c['name'], c['code'], ','.join(c.get('_news_reasons', [])))
            for c in excluded
        ))


def step19_insufficient_downgrade(ctx):
    """
    推荐不足降级（兼容主脚本接口）
    """
    print("\n" + "=" * 60)
    print("步骤19: 推荐不足降级")
    print("=" * 60)

    candidates = ctx.get('candidates', [])
    final_count = len(candidates)

    if final_count >= 3:
        print("  推荐≥3只，无需降级")
    elif final_count == 2:
        candidates = [c for c in candidates if c.get('confidence') in ('★★', '★★★')]
        print("  推荐2只→仅保留≥中置信: {}只".format(len(candidates)))
    elif final_count == 1:
        candidates = [c for c in candidates if c.get('confidence') == '★★★']
        print("  推荐1只→仅保留高置信: {}只".format(len(candidates)))
    else:
        print("  无合适标的")

    ctx['candidates'] = candidates
    ctx['final_count'] = len(candidates)


# ============================================================
# 8. 自测入口
# ============================================================
if __name__ == '__main__':
    print('============================================================')
    print('A股新闻筛查模块 v6.17.0 — 自测')
    lic_status = '已配置' if MAIRUI_LICENCE else '未配置'
    print('麦蕊Licence: {}'.format(lic_status))
    now_str = datetime.now().strftime('%Y-%m-%d')
    print('当前日期: {}'.format(now_str))
    print('============================================================')
    
    test_stocks = [
        ('000977', '浪潮信息'),
        ('000001', '平安银行'),
        ('000933', '神火股份'),
        ('002738', '中矿资源'),
        ('600519', '贵州茅台'),
    ]
    
    test_date = '2026-07-30'
    
    print('\n--- 单股测试 ---')
    code, name = test_stocks[0]
    result = check_stock_news(code, name, test_date)
    print('{}({}):'.format(name, code))
    print('  排除: {}'.format(result['excluded']))
    print('  可用源: {}/4'.format(result['sources_available']))
    print('  详情: {}'.format(result['details']))
    if result['negatives']:
        for n in result['negatives']:
            kw = n['keyword']
            src = n['source']
            title = n['title'][:60]
            print('    ⚠️ [{}] {}: {}'.format(src, kw, title))
    
    print('\n--- 批量测试 ---')
    batch_result = batch_check_news(test_stocks, test_date)
    s = batch_result['summary']
    print('总数: {}'.format(s['total']))
    print('通过: {}'.format(s['passed']))
    print('排除: {}'.format(s['excluded']))
    print('源统计: {}'.format(s['sources_stats']))
    for code, name, reasons in batch_result['excluded']:
        kws = [r['keyword'] for r in reasons]
        print('  ❌ {}({}): {}'.format(name, code, kws))
    
    print('\n--- 涨停股池测试 ---')
    zt_pool, status = get_mairui_limit_pool(test_date, 'ztgc')
    print('涨停股池: {} | {}只'.format(status, len(zt_pool)))
    if zt_pool:
        names = [s.get('mc', '') for s in zt_pool[:5]]
        print('前5: {}'.format(names))
    
    print('\n--- 跌停股池测试 ---')
    dt_pool, status = get_mairui_limit_pool(test_date, 'dtgc')
    print('跌停股池: {} | {}只'.format(status, len(dt_pool)))
    if dt_pool:
        names = [s.get('mc', '') for s in dt_pool[:5]]
        print('前5: {}'.format(names))
    
    print('\n--- 正面新闻测试 ---')
    news = get_top10_positive_news('000977', '浪潮信息', test_date)
    print('正面新闻: {}条'.format(len(news)))
    for n in news:
        print('  {}'.format(n['title'][:80]))
