#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股短线筛选 — 会话记忆模块 v6.13.38
每个筛选会话独立记忆，新会话自动清除旧会话数据。
用途：断点续跑、步骤追踪、调试回溯。
"""
import json, os, time, uuid
from datetime import datetime

SESSION_FILE = "/workspace/.session_state.json"


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _stamp():
    return int(time.time())


def init_session(version, date_str):
    """初始化新会话。如果旧会话存在且日期不同则清除。
    v6.13.47: 若旧会话已完成(completed=true)，允许同天重新初始化（新会话ID）"""
    old = _read()
    if old:
        if old.get("date") != date_str:
            # 新一天的筛选，清除旧会话
            _write(None)
            old = None
        elif old.get("completed"):
            # 同天已完成会话，允许重新运行（新会话ID，旧steps保留在日志中）
            old = None  # 不继承旧会话，全新开始

    session_id = str(uuid.uuid4())[:8]
    state = {
        "session_id": session_id,
        "date": date_str,
        "version": version,
        "started_at": _now(),
        "start_stamp": _stamp(),
        "completed": False,
        "steps": [],          # [{step, status, ts, note}]
        "warnings": [],       # [{module, message, ts}]
        "errors": [],         # [{module, message, ts}]
        "current_step": "",
        "last_updated": _now(),
    }
    _write(state)
    return state


def save_step(step_name, status="OK", note=""):
    """
    记录步骤完成。status: OK|SKIP|WARN|ERROR
    返回当前步骤序号。
    """
    state = _read()
    if not state:
        return 0
    entry = {"step": step_name, "status": status, "ts": _now(), "note": note}
    # 同步骤去重（幂等）
    existing = [s for s in state["steps"] if s["step"] == step_name]
    if existing:
        existing[0].update(entry)
    else:
        state["steps"].append(entry)
    state["current_step"] = step_name
    state["last_updated"] = _now()
    _write(state)
    return len(state["steps"])


def save_warning(module, message):
    """记录警告"""
    state = _read()
    if state:
        state["warnings"].append({"module": module, "message": message, "ts": _now()})
        state["last_updated"] = _now()
        _write(state)


def save_error(module, message):
    """记录错误"""
    state = _read()
    if state:
        state["errors"].append({"module": module, "message": message, "ts": _now()})
        state["last_updated"] = _now()
        _write(state)


def finish_session():
    """标记会话完成"""
    state = _read()
    if state:
        state["completed"] = True
        state["finished_at"] = _now()
        state["last_updated"] = _now()
        _write(state)
    return _summary(state)


def get_progress():
    """获取当前进度摘要"""
    state = _read()
    if not state:
        return None
    total = len(state["steps"])
    ok = sum(1 for s in state["steps"] if s["status"] == "OK")
    skip = sum(1 for s in state["steps"] if s["status"] == "SKIP")
    warn = sum(1 for s in state["steps"] if s["status"] == "WARN")
    err = sum(1 for s in state["steps"] if s["status"] == "ERROR")
    return {
        "session_id": state["session_id"],
        "date": state["date"],
        "version": state["version"],
        "completed": state["completed"],
        "total_steps": total,
        "ok": ok, "skip": skip, "warn": warn, "error": err,
        "current_step": state["current_step"],
        "elapsed": _stamp() - state["start_stamp"],
    }


def _summary(state):
    if not state:
        return ""
    steps = state["steps"]
    total = len(steps)
    ok = sum(1 for s in steps if s["status"] == "OK")
    skip = sum(1 for s in steps if s["status"] == "SKIP")
    warn = sum(1 for s in steps if s["status"] == "WARN")
    n_err = sum(1 for s in steps if s["status"] == "ERROR")
    elapsed = _stamp() - state["start_stamp"]
    return (f"会话 {state['session_id']} | {state['date']} {state['version']} | "
            f"步骤: 通过{ok} 跳过{skip} 警告{warn} 失败{n_err} | "
            f"耗时{elapsed}s")


def _read():
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write(state):
    if state is None:
        try:
            os.remove(SESSION_FILE)
        except FileNotFoundError:
            pass
        return
    tmp = SESSION_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SESSION_FILE)