from datetime import datetime, timedelta  # Python ≥ 3.7 才能使用 fromisoformat()
import urllib.request, urllib.error, json

beijing_now = None

# 仅通过网络授时API获取北京时间（多源冗余，任一成功即可）
TIME_APIS = [
    'https://timeapi.io/api/time/current/zone?timeZone=Asia/Shanghai',
]
for api_url in TIME_APIS:
    try:
        req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req, timeout=5)
        data = json.loads(resp.read())
        # fromisoformat 在 Python 3.10 不支持7位小数秒，截断到6位微秒
        dt_str = data['dateTime']
        if '.' in dt_str:
            date_part, frac = dt_str.split('.')
            frac = frac[:6]
            dt_str = date_part + '.' + frac
        beijing_now = datetime.fromisoformat(dt_str)
        break
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        log_alert("INFO", "北京时间", f"{api_url} 网络不可达: {str(e)[:60]}")
        continue
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        log_alert("INFO", "北京时间", f"{api_url} 解析失败: {str(e)[:60]}")
        continue
    except Exception as e:
        log_alert("INFO", "北京时间", f"{api_url} 未知异常: {str(e)[:60]}")
        continue

# 所有API均失败 → 报错中止，不降级到系统时钟
if beijing_now is None:
    log_alert("ERROR", "北京时间", "所有授时API均不可达，本次筛选中止（禁止使用系统时钟）")
    raise RuntimeError("北京时间获取失败：所有授时API均不可达")

beijing_date = beijing_now.strftime('%Y-%m-%d')
beijing_hour = beijing_now.hour
beijing_weekday = beijing_now.weekday()  # 0=周一,6=周日

# data_date（数据日期）：数据来源日。周末回退到周五
if beijing_weekday == 5:       # 周六 → 数据日期为周五
    data_date = (beijing_now - timedelta(days=1)).strftime('%Y-%m-%d')
elif beijing_weekday == 6:     # 周日 → 数据日期为周五
    data_date = (beijing_now - timedelta(days=2)).strftime('%Y-%m-%d')
else:
    data_date = beijing_date

# prediction_date（预测日期）：下一个交易日
# Mon(0)→Tue(+1), Tue(1)→Wed(+1), Wed(2)→Thu(+1), Thu(3)→Fri(+1)
# Fri(4)→Mon(+3), Sat(5)→Mon(+2), Sun(6)→Mon(+1)
if beijing_weekday <= 3:       # 周一至周四 → 次日
    prediction_date = (beijing_now + timedelta(days=1)).strftime('%Y-%m-%d')
elif beijing_weekday == 4:     # 周五 → 下周一
    prediction_date = (beijing_now + timedelta(days=3)).strftime('%Y-%m-%d')
elif beijing_weekday == 5:     # 周六 → 下周一
    prediction_date = (beijing_now + timedelta(days=2)).strftime('%Y-%m-%d')
else:                           # 周日 → 下周一
    prediction_date = (beijing_now + timedelta(days=1)).strftime('%Y-%m-%d')
