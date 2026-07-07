#!/usr/bin/env python3
"""雪球新闻缓存助手 — 由Agent调用WebFetch填充缓存
用法: echo '{"SZ002056": [{"title":"...","text":"..."}]}' | python3 xueqiu_cache_helper.py merge
      或: python3 xueqiu_cache_helper.py clear
      或: python3 xueqiu_cache_helper.py show
"""

import json, sys, os

CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'xueqiu_news_cache.json')


def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, 'r') as f:
            return json.loads(f.read())
    return {}


def save_cache(cache):
    with open(CACHE_PATH, 'w') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f"缓存已保存: {len(cache)} 只标的")


def merge(new_data):
    """合并新数据到缓存"""
    cache = load_cache()
    cache.update(new_data)
    save_cache(cache)


def show():
    cache = load_cache()
    if not cache:
        print("缓存为空")
        return
    for code, posts in cache.items():
        print(f"{code}: {len(posts)}条讨论")
        for p in posts[:2]:
            t = (p.get('title', '') or p.get('text', ''))[:80]
            print(f"  [{p.get('date', '?')}] {t}")


def clear():
    save_cache({})


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'show'
    if cmd == 'merge':
        data = json.loads(sys.stdin.read())
        merge(data)
    elif cmd == 'clear':
        clear()
    else:
        show()