#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2 新闻源替换实现: 麦蕊智数 + 财联社CLS电报
集成到 ashare_screener.py v6.16.0
"""
import hashlib, time, urllib.request, urllib.parse, json, ssl

# ============================================================
# Part 1: 麦蕊智数 API 封装
# ============================================================
def _load_mairui_licence():
    import os
    if 'MAIRUI_LICENCE' in os.environ:
        return os.environ['MAIRUI_LICENCE']
    try:
        with open('/workspace/.mairui_licence', 'r') as f:
            return f.read().strip()
    except (FileNotFoundError, PermissionError):
        pass
    return ''

MAIRUI_LICENCE = _load_mairui_licence()
MAIRUI_BASE = 'http://api.mairui.club'
MAIRUI_BASE_V2 = 'https://a.mairuiapi.com'

def _mairui_longhubang_daily(licence=None):
    """获取当日龙虎榜全量数据"""
    if licence is None: licence = MAIRUI_LICENCE
    if not licence: return None
    try:
        url = f'{MAIRUI_BASE}/hilh/mrxq/{licence}'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.mairui.club/'})
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if isinstance(data, list): return data
            elif isinstance(data, dict): return data.get('data', data.get('result', []))
    except Exception: pass
    return None

def _mairui_announcements(code, licence=None):
    """获取个股最新公告"""
    if licence is None: licence = MAIRUI_LICENCE
    if not licence: return None
    try:
        url = f'{MAIRUI_BASE_V2}/hsstock/announcement/{code}/{licence}'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.mairui.club/'})
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if isinstance(data, list): return data
            elif isinstance(data, dict): return data.get('data', data.get('result', []))
    except Exception: pass
    return None

def _mairui_news(code, licence=None):
    """获取个股新闻/资讯"""
    if licence is None: licence = MAIRUI_LICENCE
    if not licence: return None
    try:
        url = f'{MAIRUI_BASE_V2}/hsstock/news/{code}/{licence}'
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.mairui.club/'})
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if isinstance(data, list): return data
            elif isinstance(data, dict): return data.get('data', data.get('result', []))
    except Exception: pass
    return None

# ============================================================
# Part 2: CLS 财联社电报 API 封装
# ============================================================
def _cls_sign(params_dict):
    sorted_keys = sorted(params_dict.keys())
    raw = '&'.join(f'{k}={params_dict[k]}' for k in sorted_keys if params_dict[k] is not None)
    sha1_hash = hashlib.sha1(raw.encode()).hexdigest()
    md5_hash = hashlib.md5(sha1_hash.encode()).hexdigest()
    return md5_hash

_cls_telegraph_cache = None

def _fetch_cls_telegraphs(pages=3):
    global _cls_telegraph_cache
    if _cls_telegraph_cache is not None: return _cls_telegraph_cache
    all_items = []
    ctx = ssl._create_unverified_context()
    for page in range(1, pages + 1):
        try:
            ts = int(time.time())
            params = {'app': 'CailianpressWeb', 'os': 'web', 'sv': '8.4.6'}
            if page > 1: params['page'] = str(page)
            sign = _cls_sign(params)
            params['sign'] = sign
            url = f'https://www.cls.cn/v3/depth/list/1003?{urllib.parse.urlencode(params)}'
            req = urllib.request.Request(url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Referer': 'https://www.cls.cn/'})
            with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                items = data.get('data', [])
                if isinstance(items, list): all_items.extend(items)
        except Exception: pass
    _cls_telegraph_cache = all_items
    return all_items

# ============================================================
# Part 3: 新闻筛查检查器
# ============================================================
NEGATIVE_KW = [
    '立案调查', '行政处罚', '监管函', '问询函', '业绩修正', '预亏', '预减',
    '大股东减持', '控股股东减持', '质押平仓', '商誉减值', '退市风险',
    '重大诉讼', '债务违约', '暂停上市', '终止上市', '限售股解禁',
    '业绩变脸', '财务造假', '信披违规', '内幕交易', '操纵市场',
    '强制退市', '破产重整', '资不抵债', '审计非标',
    '违规担保', '资金占用', '重组失败', '定增终止',
    '净利润下滑', '营收下滑', '毛利率下滑', '评级下调',
    '应收账款', '坏账计提', '存货跌价', '资产减值', '内控缺陷', '证监会立案', '通报批评'
]

FALSE_POSITIVE_NEGATORS = [
    '终止减持', '不减持', '解除质押', '整改完成', '撤销',
    '大幅增长', '扭亏', '摘帽', '恢复正常', '已消除',
    '不立案', '不处罚', '不予', '驳回', '和解', '撤回',
    '增持', '回购', '承诺不', '减持完毕', '解除异常', '无违规'
]

def _check_cls_v2(code, name):
    """CLS电报v2: 批量拉取电报后在本地搜索匹配"""
    try:
        telegraphs = _fetch_cls_telegraphs(pages=3)
        if not telegraphs: return None
        for item in telegraphs:
            title = (item.get('title', '') or '') + ' ' + (item.get('brief', '') or '')
            if name not in title and code not in title: continue
            for kw in NEGATIVE_KW:
                if kw in title:
                    if not any(neg in title for neg in FALSE_POSITIVE_NEGATORS):
                        return ('cls_v2', kw)
        return None
    except Exception: return None

def _check_mairui_lhb(code, name):
    """麦蕊龙虎榜利空检测"""
    if not MAIRUI_LICENCE: return None
    try:
        lhb_data = _mairui_longhubang_daily()
        if not lhb_data: return None
        for item in lhb_data:
            item_code = str(item.get('code', '') or item.get('dm', '') or '')
            item_name = str(item.get('name', '') or item.get('mc', '') or '')
            if code not in item_code and name not in item_name: continue
            net_amt = item.get('net', item.get('jme', 0)) or 0
            if isinstance(net_amt, str):
                try: net_amt = float(net_amt)
                except: net_amt = 0
            if net_amt < 0 and abs(net_amt) > 10000000:
                return ('mairui_lhb', f'龙虎榜净卖出{abs(net_amt)/1e8:.1f}亿')
            explanation = str(item.get('explanation', '') or item.get('sm', '') or '')
            if '机构卖出' in explanation or '游资卖出' in explanation:
                return ('mairui_lhb', '龙虎榜机构/游资卖出')
            return None
        return None
    except Exception: return None

def _check_mairui_ann(code, name):
    """麦蕊公告利空检测"""
    if not MAIRUI_LICENCE: return None
    try:
        anns = _mairui_announcements(code)
        if not anns: return None
        for ann in anns:
            title = str(ann.get('title', '') or ann.get('bt', '') or '')
            for kw in NEGATIVE_KW:
                if kw in title and not any(neg in title for neg in FALSE_POSITIVE_NEGATORS):
                    return ('mairui_ann', kw)
        return None
    except Exception: return None

def _check_mairui_news(code, name):
    """麦蕊新闻利空检测"""
    if not MAIRUI_LICENCE: return None
    try:
        news_items = _mairui_news(code)
        if not news_items: return None
        for item in news_items:
            title = str(item.get('title', '') or item.get('bt', '') or '')
            for kw in NEGATIVE_KW:
                if kw in title and not any(neg in title for neg in FALSE_POSITIVE_NEGATORS):
                    return ('mairui_news', kw)
        return None
    except Exception: return None

# ============================================================
# Part 4: TOP10龙虎榜替换
# ============================================================
def _mairui_longhubang_for_top10(code, name):
    """麦蕊龙虎榜数据(TOP10增强用)"""
    if not MAIRUI_LICENCE: return ''
    try:
        lhb_data = _mairui_longhubang_daily()
        if not lhb_data: return ''
        for item in lhb_data:
            item_code = str(item.get('code', '') or item.get('dm', '') or '')
            if code not in item_code: continue
            lh_date = str(item.get('date', '') or item.get('tdate', '') or '')[:10]
            net_amt = item.get('net', item.get('jme', 0)) or 0
            if isinstance(net_amt, str):
                try: net_amt = float(net_amt)
                except: net_amt = 0
            lh_dir = '净买入' if net_amt > 0 else '净卖出'
            lh_abs = abs(net_amt)
            if lh_abs >= 100000000:
                lh_amt_str = f'{lh_abs/1e8:.1f}亿'
            else:
                lh_amt_str = f'{lh_abs/1e4:.0f}万'
            return f'{lh_date} {lh_dir} {lh_amt_str}'
        return ''
    except Exception: return ''

# ============================================================
# 测试
# ============================================================
if __name__ == '__main__':
    print("=" * 60)
    print("P2 新闻源实现测试")
    print("=" * 60)
    print("\n[1] CLS电报拉取测试...")
    telegraphs = _fetch_cls_telegraphs(pages=2)
    if telegraphs:
        print(f"  ✅ 拉取成功: {len(telegraphs)}条电报")
        for t in telegraphs[:3]:
            print(f"  - {t.get('title','')[:60]}")
    else:
        print("  ❌ 拉取失败")
    
    print("\n[2] CLS搜索测试 (贵州茅台 600519)...")
    result = _check_cls_v2('600519', '贵州茅台')
    print(f"  结果: {result}" if result else "  ✅ 未发现利空")
    
    print("\n[3] 麦蕊智数状态...")
    if MAIRUI_LICENCE:
        print(f"  ✅ Licence: {MAIRUI_LICENCE[:8]}...")
    else:
        print("  ⚠️ Licence未配置")
        print("  注册: https://www.mairui.club/gratis (需手机号+短信)")
    print("=" * 60)