#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
沙箱数据适配层 (v6.21.0 新增)
================================
问题背景:
  沙箱出口防火墙按「主机 + 路径」做 L7 拦截, 东财行情接口
  push2.eastmoney.com / push2his.eastmoney.com 的 /api/qt/* 路径被 reset
  (curl 返回 "Empty reply"), 且沙箱内无任何通用出口代理, 无法绕墙直连东财.
  实测可达的替代源:
    - 腾讯行情  qt.gtimg.cn/q=               (实时价/指数, 沙箱 200)
    - 腾讯历史  proxy.finance.qq.com/ifzqgtimg/.../fqkline (日K线, 沙箱 200, 含真实数据)
    - 新浪行情  hq.sinajs.cn                  (实时价备选, 沙箱 200)
    - 东财报表  datacenter-web.eastmoney.com  (商誉/质押等 RPT, 沙箱 200, 保持不变)
  腾讯「板块排行 / 竞价控制器」专用接口在沙箱返回 "No dispatch", 不可用,
  相关功能(资金去向/主力资金/竞价)维持脚本既有的降级行为.

本模块把所有行情/历史/K线/竞价/指数数据源统一改走可达源, 返回结构尽量与
调用方既有期望一致, 保证打分/排序/报告逻辑零改动.
"""
import urllib.request
import json
import re

_TENCENT_QUOTE = "https://qt.gtimg.cn/q="
_TENCENT_KLINE = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/fqkline/get"
_UA = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://gu.qq.com/',
}


def _normalize_tcode(code):
    """6位代码 -> 腾讯前缀代码; 已带 sh/sz/us/r_hk 等前缀的保持原样."""
    if re.match(r'^(sh|sz|us|r_hk|hk|us_)', code, re.I):
        return code
    if code.startswith('6'):
        return 'sh' + code
    if code and code[0] in '03':
        return 'sz' + code
    return 'sh' + code  # 兜底


def tencent_kline(code, limit=60, qfq=True, ktype='day'):
    """腾讯日K线(前复权). 返回 [[date, open, close, high, low, volume], ...] 或 [].

    与东财 push2his kline 格式对齐(分钟级不需要), 供 K线兜底/回测复用.
    """
    prefix = _normalize_tcode(code)
    try:
        fq = 'qfq' if qfq else 'hfq'
        url = f"{_TENCENT_KLINE}?param={prefix},{ktype},,,{limit},{fq}"
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        node = data.get('data', {}).get(f"{prefix}", {})
        klines = node.get(f"{fq}day") or node.get("day") or []
        out = []
        for k in klines[-limit:]:
            if len(k) >= 6:
                try:
                    out.append([k[0], float(k[1]), float(k[2]),
                                float(k[3]), float(k[4]), float(k[5])])
                except (ValueError, TypeError):
                    continue
        return out
    except Exception:
        return []


def tencent_realtime(code):
    """腾讯实时行情(个股/指数通用). 返回归一化 dict 或 None.

    gtimg 字段(0-based):
      [1]名称 [2]代码 [3]现价 [4]昨收 [5]今开 [30]时间 [31]涨跌
      [32]涨跌幅% [33]最高 [34]最低 [36]成交量(手) [37]成交额(万元)
      [38]换手% [45]流通市值(亿) [46]总市值(亿)
    amount 已换算为「元」以对齐东财 f6 语义.
    """
    prefix = _normalize_tcode(code)
    try:
        url = f"{_TENCENT_QUOTE}{prefix}"
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=8) as resp:
            text = resp.read().decode('gbk', 'ignore')
        for line in text.strip().split('\n'):
            if not line.startswith(f"v_{prefix}="):
                continue
            parts = line.split('~')
            if len(parts) < 40:
                continue

            def f(i):
                v = parts[i] if i < len(parts) else ''
                if v in ('', '-'):
                    return 0.0
                try:
                    return float(v)
                except ValueError:
                    return 0.0

            return {
                'name': parts[1],
                'code': parts[2],
                'price': f(3),
                'prev_close': f(4),
                'open': f(5),
                'change_pct': f(32),
                'high': f(33),
                'low': f(34),
                'volume': f(36),            # 手
                'amount': f(37) * 10000.0,   # 万元 -> 元
                'turnover': f(38),           # %
            }
    except Exception:
        return None
    return None


def tencent_auction(code):
    """竞价数据(沙箱最佳努力).

    东财竞价控制器在沙箱被 L7 拦截, 腾讯竞价控制器也返回 "No dispatch",
    因此用腾讯实时行情在集合竞价时段(09:15-09:25)的现价近似竞价价.
    非竞价时段返回 None, 避免用昨收误报竞价信号.
    返回 {code,price,open,high,low,volume,amount,change_pct,prev_close,gap_pct} 或 None.
    """
    prefix = _normalize_tcode(code)
    try:
        url = f"{_TENCENT_QUOTE}{prefix}"
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=8) as resp:
            text = resp.read().decode('gbk', 'ignore')
        for line in text.strip().split('\n'):
            if not line.startswith(f"v_{prefix}="):
                continue
            parts = line.split('~')
            if len(parts) < 40:
                continue
            tstr = parts[30] if len(parts) > 30 else ''
            # 仅集合竞价时段(09:15~09:25)的报价视为有效竞价价
            if not (len(tstr) >= 12 and tstr[8:12] in (
                    '0915', '0916', '0917', '0918', '0919',
                    '0920', '0921', '0922', '0923', '0924', '0925')):
                return None

            def f(i):
                v = parts[i] if i < len(parts) else ''
                if v in ('', '-'):
                    return 0.0
                try:
                    return float(v)
                except ValueError:
                    return 0.0

            price = f(3)
            prev = f(4)
            return {
                'code': code,
                'price': price,
                'open': f(5),
                'high': f(33),
                'low': f(34),
                'volume': f(36),
                'amount': f(37) * 10000.0,
                'change_pct': f(32),
                'prev_close': prev,
                'gap_pct': (price - prev) / prev if prev > 0 else 0.0,
            }
    except Exception:
        return None
    return None


def tencent_board_flow():
    """板块级行业主力净流入排名.

    腾讯板块排行接口(proxy.finance.qq.com / ifzqgtimg)在沙箱均返回
    "No dispatch", 不可用. 返回 [] 让调用方降级到「个股 main_inflow 汇总估算」
    (脚本 v6.20.16 既有的沙箱行为, 不破坏).
    """
    return []


def tencent_minute(code):
    """当日分时(分钟)数据. 返回 [{time,open,close,high,low,volume,amount}, ...] 或 None.

    腾讯 minute/query 每行: 'HHMM price avg_price cumulative_volume cumulative_amount'.
    与东财 trends2 的 minutes 结构对齐(open/high/low 用当前价近似, 满足封板检测需求).
    """
    prefix = _normalize_tcode(code)
    try:
        url = f"https://proxy.finance.qq.com/ifzqgtimg/appstock/app/minute/query?code={prefix}"
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        rows = (data.get('data', {}).get(f"{prefix}", {})
                .get('data', {}).get('data', []))
        if not rows:
            return None
        minutes = []
        for r in rows:
            parts = r.split()
            if len(parts) < 4:
                continue
            price = float(parts[1])
            minutes.append({
                'time': parts[0],
                'open': price, 'close': price, 'high': price, 'low': price,
                'volume': float(parts[2]),
                'amount': float(parts[3]),
            })
        return minutes if minutes else None
    except Exception:
        return None
