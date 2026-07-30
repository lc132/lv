#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤 -1: 日期验证 (v6.16.32 新增)
在所有筛选开始前，从权威时间源获取北京时间，交叉验证：
1. 系统宣称的日期是否与实际北京时间一致
2. data_date / prediction_date 逻辑是否正确
3. 输出文件名 YYYYMMDD 是否与 prediction_date 一致
4. prediction_date 是否为交易日（非周末/节假日）

验证失败 -> 立即中止筛选，发送飞书告警，防止错误日期输出到报告。
"""

import urllib.request, urllib.error, json, ssl, os, sys, re
from datetime import datetime, timedelta

_CN_HOLIDAYS_2026 = {
    "2026-01-01","2026-01-02","2026-02-16","2026-02-17","2026-02-18",
    "2026-02-19","2026-02-20","2026-04-06","2026-05-01","2026-06-19",
    "2026-06-20","2026-06-21","2026-09-25","2026-10-01","2026-10-02",
    "2026-10-05","2026-10-06","2026-10-07"
}
_SSL_CTX = ssl._create_unverified_context()

def _log_alert(level, module, message):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"  [{ts}] [{level}] {module}: {message}")

def _is_trading_day(date_str):
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    if dt.weekday() >= 5:
        return False
    if date_str in _CN_HOLIDAYS_2026:
        return False
    return True

def _fetch_beijing_time():
    TIME_APIS = [
        ('https://timeapi.io/api/time/current/zone?timeZone=Asia/Shanghai', 'dateTime'),
        ('https://worldtimeapi.org/api/timezone/Asia/Shanghai', 'datetime'),
    ]
    for url, key in TIME_APIS:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8, context=_SSL_CTX) as resp:
                data = json.loads(resp.read())
            ts = data[key]
            if '.' in ts:
                parts = ts.split('.')
                frac = parts[1].split('+')[0].split('-')[0].split('Z')[0][:6]
                ts = parts[0] + '.' + frac
            return datetime.fromisoformat(ts)
        except Exception as e:
            _log_alert("INFO", "日期验证", f"时间源 {url} 失败: {str(e)[:60]}")
            continue
    return None

def validate_dates(expected_data_date=None, expected_prediction_date=None,
                   expected_yyyymmdd=None, output_md_path=None):
    errors = []
    warnings = []
    details = {}

    _log_alert("INFO", "日期验证", "===== 步骤-1: 日期验证开始 =====")
    beijing_now = _fetch_beijing_time()
    if beijing_now is None:
        _log_alert("ERROR", "日期验证", "所有时间源不可达，无法验证日期")
        return False, {"error": "时间源不可达", "beijing_now": None}

    beijing_date = beijing_now.strftime('%Y-%m-%d')
    beijing_weekday = beijing_now.weekday()
    beijing_hour = beijing_now.hour
    details['beijing_now'] = beijing_now.isoformat()
    details['beijing_date'] = beijing_date
    details['beijing_weekday'] = beijing_weekday
    details['beijing_hour'] = beijing_hour

    _log_alert("INFO", "日期验证", f"北京时间: {beijing_now.strftime('%Y-%m-%d %H:%M:%S')} (周{beijing_weekday+1})")

    if expected_prediction_date:
        if expected_prediction_date != beijing_date:
            err = f"prediction_date({expected_prediction_date}) != 北京时间今日({beijing_date})"
            errors.append(err)
            _log_alert("ERROR", "日期验证", err)
        else:
            _log_alert("INFO", "日期验证", f"OK prediction_date = 北京时间 {beijing_date}")

    if expected_prediction_date:
        if not _is_trading_day(expected_prediction_date):
            err = f"prediction_date({expected_prediction_date}) 非交易日(周末/节假日)"
            errors.append(err)
            _log_alert("ERROR", "日期验证", err)
        else:
            _log_alert("INFO", "日期验证", f"OK prediction_date 为交易日")

    if expected_data_date:
        if not _is_trading_day(expected_data_date):
            warn = f"data_date({expected_data_date}) 非交易日，数据可能无效"
            warnings.append(warn)
            _log_alert("WARNING", "日期验证", warn)
        else:
            _log_alert("INFO", "日期验证", f"OK data_date 为交易日")

    if expected_yyyymmdd:
        beijing_yyyymmdd = beijing_now.strftime('%Y%m%d')
        if expected_yyyymmdd != beijing_yyyymmdd:
            err = f"文件名日期({expected_yyyymmdd}) != 北京时间({beijing_yyyymmdd})"
            errors.append(err)
            _log_alert("ERROR", "日期验证", err)
        else:
            _log_alert("INFO", "日期验证", f"OK 文件名日期 = {beijing_yyyymmdd}")

    if output_md_path:
        match = re.search(r'(\d{8})', os.path.basename(output_md_path))
        if match:
            file_date = match.group(1)
            beijing_yyyymmdd = beijing_now.strftime('%Y%m%d')
            if file_date != beijing_yyyymmdd:
                err = f"输出文件日期({file_date}) != 北京时间({beijing_yyyymmdd})"
                errors.append(err)
                _log_alert("ERROR", "日期验证", err)
            else:
                _log_alert("INFO", "日期验证", f"OK 输出文件路径日期正确")

    details['errors'] = errors
    details['warnings'] = warnings
    details['passed'] = len(errors) == 0

    if errors:
        _log_alert("ERROR", "日期验证", f"===== 验证失败({len(errors)}项错误) =====")
        for e in errors:
            _log_alert("ERROR", "日期验证", f"  X {e}")
        return False, details
    else:
        if warnings:
            for w in warnings:
                _log_alert("WARNING", "日期验证", f"  ! {w}")
        _log_alert("INFO", "日期验证", "===== 日期验证通过 =====")
        return True, details

if __name__ == '__main__':
    passed, info = validate_dates(
        expected_data_date=sys.argv[1] if len(sys.argv) > 1 else None,
        expected_prediction_date=sys.argv[2] if len(sys.argv) > 2 else None,
        expected_yyyymmdd=sys.argv[3] if len(sys.argv) > 3 else None,
        output_md_path=sys.argv[4] if len(sys.argv) > 4 else None
    )
    sys.exit(0 if passed else 1)
