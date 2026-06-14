# 清理7天前recommendation + 90天前holding/do_T
# 保留类型：weekly_review、strategy_check、do_T_eval、do_T（不受清理影响）
# data_date 由步骤0定义（beijing_date的值），如 "2026-06-12"
try:
    from datetime import datetime, timedelta
    # 逐日期文件独立清理
    total_cleaned = 0
    for f in sorted(os.listdir('/workspace')):
        if not (f.startswith('推荐历史_') and f.endswith('.json')):
            continue
        history = safe_read_json(f'/workspace/{{f}}')
    cutoff_7d_dt = datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=7)
    cutoff_90d_dt = datetime.strptime(data_date, '%Y-%m-%d') - timedelta(days=90)
    cutoff_7d = cutoff_7d_dt.strftime('%Y-%m-%d')
    cutoff_90d = cutoff_90d_dt.strftime('%Y-%m-%d')
        new_records = []
        for r in history:
        t = r.get('type', '')
        if t in ('weekly_review', 'strategy_check', 'do_T_eval', 'do_T'):
                new_records.append(r)
        elif t in ('holding',):
            d = r.get('update_date', '')
            if d >= cutoff_90d:
                    new_records.append(r)
        elif t == 'recommendation':
            d = r.get('date', '')
            if d >= cutoff_7d:
                    new_records.append(r)
    if len(new_history) < len(history):
        if len(new_records) < len(history):
            safe_write_json(f'/workspace/{{f}}', new_records)
            total_cleaned += len(history) - len(new_records)
    if total_cleaned > 0:
        log_alert("INFO", "清理", f"已清理{total_cleaned}条过期记录")
    else:
        log_alert("INFO", "清理", "无需清理")
except Exception as e:
    log_alert("WARNING", "清理", f"清理失败: {str(e)[:80]}")
