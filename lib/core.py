#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股每日盘前短线标的筛选 v6.12.8 — 核心工具模块
全局配置、工具函数
"""
import os, json
from datetime import datetime

# ============================================================
# 全局配置
# ============================================================
BUILTIN_VERSION = "v6.12.15"
DATA_DIR = "/workspace"
TEMP_DIR = "/data/user/work"
# GitHub Token 从外部文件读取（不入git，防止泄露）
GITHUB_TOKEN = None
_token_path = os.path.join(DATA_DIR, '.github_token')
try:
    with open(_token_path, 'r', encoding='utf-8') as _tf:
        GITHUB_TOKEN = _tf.read().strip()
except (FileNotFoundError, PermissionError):
    pass
if not GITHUB_TOKEN:
    GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN', '')
# 飞书Webhook URL 从外部文件读取（不入git，防止泄露）
FEISHU_WEBHOOK = None
_feishu_path = os.path.join(DATA_DIR, '.feishu_webhook')
try:
    with open(_feishu_path, 'r', encoding='utf-8') as _ff:
        FEISHU_WEBHOOK = _ff.read().strip()
except (FileNotFoundError, PermissionError):
    pass
if not FEISHU_WEBHOOK:
    FEISHU_WEBHOOK = os.environ.get('FEISHU_WEBHOOK', '')
GITHUB_REPO = "lc132/lv"

# 可配置参数默认值
DEFAULT_PARAMS = {
    "search_budget": 25, "northbound_threshold": 3000, "consecutive_weeks": 2,
    "win_rate_drop_threshold": 10, "limit_down_threshold": 100,
    "max_adjust_params": 3, "confidence_position_enabled": True,
    "max_holding_days": 5, "circuit_breaker_threshold_pct": 3.0,
    "strategy_concentration_pct": 30, "do_t_success_reset_count": 3,
    "conversion_rate_window_days": 10, "conversion_rate_threshold": 0.3,
    "conversion_rate_restore": 0.6, "conversion_rate_consecutive_days": 3,
    "data_tier_l2_skip_on_unavailable": True,
    "data_tier_l3_downgrade_to_signal": True,
    "strategy_a_weak_market": "closed"
}

# ============================================================
# 工具函数
# ============================================================
def log_alert(level, module, message, timestamp=None):
    """写入告警日志"""
    if timestamp is None:
        timestamp = datetime.now()
    ts = timestamp.strftime('%Y-%m-%d %H:%M:%S') if hasattr(timestamp, 'strftime') else str(timestamp)
    try:
        with open(f'{DATA_DIR}/系统告警.log', 'a', encoding='utf-8') as f:
            f.write(f"[{ts}] [{level}] {module}: {message}\n")
    except (PermissionError, OSError):
        pass

def safe_read_json(path, default=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if not isinstance(data, list):
                log_alert("WARNING", "safe_read_json", f"{path} 格式异常")
                return default if default is not None else []
            return data
    except (json.JSONDecodeError, PermissionError) as e:
        log_alert("ERROR", "safe_read_json", f"{path}: {str(e)}")
        return default if default is not None else []
    except FileNotFoundError:
        return default if default is not None else []

def safe_write_json(path, data):
    """原子写入：先写临时文件再重命名，防止写入中断导致数据损坏"""
    try:
        tmp_path = path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)  # 原子操作(POSIX)
    except (PermissionError, OSError) as e: log_alert("ERROR", "safe_write_json", f"{path}: {str(e)}")

def safe_append_json(path, record):
    data = safe_read_json(path)
    data.append(record)
    safe_write_json(path, data)

def safe_float(value, ndigits=3):
    if value is None: return None
    try:
        return round(float(value), ndigits)
    except (ValueError, TypeError):
        return None

def read_all_history():
    """读取所有推荐历史归档文件"""
    all_history = []
    for f in sorted(os.listdir(DATA_DIR)):
        if f.startswith("推荐历史_") and f.endswith(".json"):
            records = safe_read_json(os.path.join(DATA_DIR, f))
            all_history.extend(records)
    return all_history

def write_history_to_date_files(all_history, default_date):
    """将 all_history 按记录的 date/update_date 分组写入对应日期的归档文件"""
    from collections import defaultdict
    groups = defaultdict(list)
    for r in all_history:
        t = r.get('type', '')
        if t == 'holding':
            d = r.get('update_date', default_date)
        elif t in ('recommendation', 'strategy_check', 'weekly_review', 'do_T', 'do_T_eval'):
            d = r.get('date', default_date)
        else:
            d = default_date
        if d:
            d_compact = d.replace('-', '')
        else:
            d_compact = default_date.replace('-', '')
        groups[d_compact].append(r)
    for d_compact, records in groups.items():
        safe_write_json(f"{DATA_DIR}/推荐历史_{d_compact}.json", records)