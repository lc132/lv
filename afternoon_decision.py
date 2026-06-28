#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v6.12.0 尾盘决策模型 v1.0
运行时机: 14:30-15:00
功能: 筛选隔夜持有标的（封板质量+空间潜力+板块强度+资金强度）
"""
import os, sys, json, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.afternoon import (
    fetch_intraday_minute, detect_lock_time,
    compute_overnight_score, hard_filter_afternoon
)


def main():
    print("=" * 60)
    print("尾盘决策模型 v1.0")
    print("=" * 60)
    
    # 1. 获取全市场实时行情（涨停股筛选）
    limit_up_stocks = _fetch_limit_up_stocks()
    if not limit_up_stocks:
        print("[INFO] 无涨停股，跳过尾盘筛选")
        return
    
    print(f"[INFO] 涨停股: {len(limit_up_stocks)} 只")
    
    # 2. 加载K线数据
    kline_data = _load_kline_snapshot()
    
    # 3. 检测封板时间
    for c in limit_up_stocks:
        intraday = fetch_intraday_minute(c.get('code', ''))
        if intraday:
            lock_minute = detect_lock_time(intraday, c.get('prev_close', 0))
            c['_lock_minute'] = lock_minute
    
    # 4. 统计板块涨停分布
    sector_limit_up = {}
    for c in limit_up_stocks:
        industry = c.get('industry', '未知')
        sector_limit_up[industry] = sector_limit_up.get(industry, 0) + 1
    for c in limit_up_stocks:
        c['_sector_limit_up_count'] = sector_limit_up.get(c.get('industry', '未知'), 0)
    
    # 5. 硬过滤
    passed = []
    filtered = []
    for c in limit_up_stocks:
        ok, reason = hard_filter_afternoon(c, kline_data)
        if ok:
            passed.append(c)
        else:
            filtered.append({'code': c.get('code', ''), 'name': c.get('name', ''), 'reason': reason})
    
    # 6. 评分排序
    for c in passed:
        score = compute_overnight_score(c, kline_data, c.get('_sector_limit_up_count', 0))
        c['overnight_score'] = score
    
    passed.sort(key=lambda x: x.get('overnight_score', 0), reverse=True)
    
    # 7. 输出
    top = passed[:10]
    print(f"\n[结果] 通过{len(passed)}只, 过滤{len(filtered)}只")
    print(f"\n隔夜标的TOP10:")
    for i, c in enumerate(top):
        print(f"  {i+1}. {c.get('code','')} {c.get('name','')} "
              f"封板{c.get('_lock_minute','?')}min "
              f"板块{c.get('_sector_limit_up_count',0)}只 "
              f"主力{(c.get('main_inflow') or 0)/10000:.0f}万 "
              f"评分{c.get('overnight_score',0)}")
    
    if filtered:
        print(f"\n过滤清单:")
        for f in filtered[:5]:
            print(f"  {f['code']} {f.get('name','')} - {f['reason']}")
    
    # 保存结果
    result = {
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_limit_up': len(limit_up_stocks),
        'passed': len(passed),
        'filtered': len(filtered),
        'top10': top[:10],
        'filtered_list': filtered[:20],
    }
    with open('/workspace/overnight_result.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n[保存] 结果已写入 /workspace/overnight_result.json")


def _fetch_limit_up_stocks():
    """获取全市场涨停股（东方财富实时行情）v6.12.0: 分页拉取超过100只"""
    all_stocks = []
    for pn in range(1, 6):  # 最多5页=500只
        url = ("https://push2.eastmoney.com/api/qt/clist/get?"
               f"pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23"
               "&fields=f2,f3,f12,f14,f15,f16,f17,f18,f62,f100,f102,f104")
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=15)
            data = json.loads(resp.read().decode('utf-8'))
            page_stocks = []
            if data.get('data') and data['data'].get('diff'):
                for d in data['data']['diff']:
                    chg = d.get('f3', 0)
                    if chg is not None and chg >= 9.8:
                        page_stocks.append({
                            'code': d.get('f12', ''),
                            'name': d.get('f14', ''),
                            'change_pct': chg,
                            'close': d.get('f2', 0),
                            'prev_close': d.get('f18', 0),
                            'main_inflow': d.get('f62', 0),
                            'industry': d.get('f100', ''),
                            '_sector_limit_up_count': 0,
                        })
            all_stocks.extend(page_stocks)
            if not page_stocks:
                break  # 该页无涨停股，停止分页
        except Exception as e:
            print(f"[WARN] 获取涨停股第{pn}页失败: {e}")
            break
    return all_stocks


def _load_kline_snapshot():
    return {}


if __name__ == '__main__':
    main()