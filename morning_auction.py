#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v6.11.0 早盘竞价模型 v1.0
运行时机: 9:26-9:30（竞价结束后、开盘前）
功能: 通过集合竞价数据验证，筛选强势潜力股
"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib.auction import fetch_auction_batch, compute_auction_score, hard_filter_auction


def main():
    print("=" * 60)
    print("早盘竞价模型 v1.0")
    print("=" * 60)
    
    # 1. 加载昨日筛选结果作为关注列表
    watchlist = _load_watchlist()
    if not watchlist:
        print("[INFO] 无关注列表，跳过竞价筛选")
        return
    
    print(f"[INFO] 关注列表: {len(watchlist)} 只标的")
    
    # 2. 拉取竞价数据
    codes = [w['code'] for w in watchlist]
    prev_close_map = {w['code']: w.get('prev_close', 0) for w in watchlist}
    auction_data = fetch_auction_batch(codes, prev_close_map)
    print(f"[INFO] 竞价数据: {len(auction_data)} 只")
    
    # 3. 加载K线数据（用于评分）
    kline_data = _load_kline_snapshot()
    
    # 4. 硬过滤
    passed = []
    filtered = []
    for a in auction_data:
        ok, reason = hard_filter_auction(a)
        if ok:
            passed.append(a)
        else:
            filtered.append({'code': a.get('code', ''), 'reason': reason})
    
    # 5. 竞价评分
    for a in passed:
        prev_close = a.get('prev_close', 0)
        score = compute_auction_score(a, kline_data, prev_close)
        a['auction_score'] = score
    
    passed.sort(key=lambda x: x.get('auction_score', 0), reverse=True)
    
    # 6. 输出
    top = passed[:10]
    print(f"\n[结果] 通过{len(passed)}只, 过滤{len(filtered)}只")
    print(f"\n竞价TOP10:")
    for i, a in enumerate(top):
        print(f"  {i+1}. {a.get('code','')} 竞价{a.get('price',0):.2f} 高开{a.get('gap_pct',0):.2%} 评分{a.get('auction_score',0)}")
    
    if filtered:
        print(f"\n过滤清单:")
        for f in filtered[:5]:
            print(f"  {f['code']} - {f['reason']}")
    
    # 保存结果
    result = {
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_watchlist': len(watchlist),
        'auction_data_count': len(auction_data),
        'passed': len(passed),
        'filtered': len(filtered),
        'top10': top[:10],
        'filtered_list': filtered[:20],
    }
    with open('/workspace/auction_result.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n[保存] 结果已写入 /workspace/auction_result.json")


def _load_watchlist():
    """从昨日筛选结果加载关注列表"""
    watchlist = []
    workspace = '/workspace'
    if not os.path.isdir(workspace):
        return watchlist
    
    for fname in sorted(os.listdir(workspace)):
        if fname.startswith('推荐历史_') and fname.endswith('.json'):
            try:
                with open(os.path.join(workspace, fname), 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for r in data:
                        if r.get('type') == 'recommendation':
                            watchlist.append({
                                'code': r.get('code', ''),
                                'name': r.get('name', ''),
                                'prev_close': r.get('close', 0),
                            })
            except:
                pass
    return watchlist


def _load_kline_snapshot():
    """加载K线快照（简化版，实际使用pytdx数据）"""
    return {}


if __name__ == '__main__':
    main()