def log_alert(level, module, message, timestamp=None):
    """写入告警日志。timestamp 默认使用系统时钟，若步骤0已获取 beijing_now，调用方可传入 beijing_now 替代。"""
    from datetime import datetime
    if timestamp is None:
        timestamp = datetime.now()
    ts = timestamp.strftime('%Y-%m-%d %H:%M:%S') if hasattr(timestamp, 'strftime') else str(timestamp)
    with open('/workspace/系统告警.log', 'a', encoding='utf-8') as f:
        f.write(f"[{ts}] [{level}] {module}: {message}\n")
