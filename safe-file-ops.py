import json, os, sys
from openpyxl import load_workbook

def _log_alert(level, module, message):
    """内置日志（独立脚本无外部依赖时的兜底）"""
    print(f"[{level}] {module}: {message}", file=sys.stderr)

def safe_read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if not isinstance(data, list):
                _log_alert("WARNING", "safe_read_json", f"{path} 格式异常")
                return default if default is not None else []
            return data
    except FileNotFoundError:
        return default if default is not None else []
    except (json.JSONDecodeError, PermissionError) as e:
        _log_alert("ERROR", "safe_read_json", f"{path}: {str(e)}")
        return default if default is not None else []

def safe_write_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f: json.dump(data, f, ensure_ascii=False, indent=2)
    except (PermissionError, OSError) as e:
        _log_alert("ERROR", "safe_write_json", f"{path}: {str(e)}")

def safe_append_json(path, record):
    data = safe_read_json(path)
    data.append(record)
    safe_write_json(path, data)

def safe_read_excel(path):
    try:
        return load_workbook(path)
    except FileNotFoundError:
        return None
    except Exception as e:
        _log_alert("WARNING", "safe_read_excel", f"{path}: {str(e)}")
        return None

def safe_float(value, ndigits=3):
    """安全浮点格式化，供步骤4/4B/步骤10等外部调用"""
    if value is None: return None
    try:
        return round(float(value), ndigits)
    except (ValueError, TypeError):
        return None