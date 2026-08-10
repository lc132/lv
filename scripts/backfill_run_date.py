#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_run_date.py — 回填推荐历史的 run_date 字段
@since v6.20.16

背景
----
v6.20.16 的回测修复引入 run_date(= 实际运行日历日), 让回测报告按"运行日"而非
"data_date(数据基准日)"分组。周一盘前 data_date 会回退到上周五, 导致 8/10 那批
盘前选股被戳成 date=2026-08-07, 回测报告按 date 分组时全进了 2026-08-07 分组,
于是在报告里看不到 2026-08-10 自己的分组。

本脚本对已生成的 推荐历史_*.json(缺少 run_date 的旧记录)做一次性回填:
  - 盘前记录 (prediction_date 存在且与 date 不同): run_date = prediction_date
  - 盘后记录 (无 prediction_date):               run_date = date + 1 个日历日
  - 已有 run_date 的记录: 默认跳过 (--force 可覆盖)
  - 非 recommendation 类型: 不处理

注意: 本脚本只"补全"分组用的 run_date, 不改变任何选股/评分逻辑。
      建议先 --dry-run 预览, 再正式回填; 正式写入前会为原文件生成 .bak 备份。

用法
----
  python3 backfill_run_date.py [--data-dir DIR] [--dry-run] [--force] [--no-backup]

  --data-dir DIR   推荐历史所在目录 (默认 $LV_DATA_DIR 或 /workspace)
  --dry-run        只打印将要做的改动, 不写文件 (强烈建议先跑一次)
  --force          覆盖已有的 run_date
  --no-backup      不生成 .bak 备份
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta

HIST_PREFIX = "推荐历史_"
HIST_SUFFIX = ".json"


def _parse(s):
    return datetime.strptime(s, "%Y-%m-%d")


def _fmt(d):
    return d.strftime("%Y-%m-%d")


def infer_run_date(rec):
    """返回应填的 run_date 字符串; 无法推断返回 None."""
    date = rec.get("date")
    if not date:
        return None
    try:
        d = _parse(date)
    except ValueError:
        return None
    pred = rec.get("prediction_date")
    # 盘前运行: prediction_date 始终等于实际运行日, 且与 data_date 不同
    if pred and pred != date:
        return pred
    # 盘后运行: data_date = 运行日 - 1 个日历日  =>  run_date = date + 1
    return _fmt(d + timedelta(days=1))


def safe_read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def main():
    ap = argparse.ArgumentParser(description="回填推荐历史的 run_date 字段")
    ap.add_argument("--data-dir", default=os.environ.get("LV_DATA_DIR", "/workspace"),
                    help="推荐历史目录 (默认 $LV_DATA_DIR 或 /workspace)")
    ap.add_argument("--dry-run", action="store_true", help="只打印改动, 不写文件")
    ap.add_argument("--force", action="store_true", help="覆盖已有的 run_date")
    ap.add_argument("--no-backup", action="store_true", help="不生成 .bak 备份")
    args = ap.parse_args()

    d = args.data_dir
    if not os.path.isdir(d):
        print(f"[backfill] 目录不存在: {d}")
        return 1
    files = sorted(f for f in os.listdir(d)
                   if f.startswith(HIST_PREFIX) and f.endswith(HIST_SUFFIX))
    if not files:
        print(f"[backfill] 在 {d} 未找到 {HIST_PREFIX}*{HIST_SUFFIX}")
        return 0

    print(f"[backfill] data-dir={d}  dry-run={args.dry_run}  force={args.force}")
    total = 0
    for fn in files:
        path = os.path.join(d, fn)
        records = safe_read_json(path)
        if not isinstance(records, list):
            print(f"[backfill] 跳过 {fn}: 顶层非 list")
            continue
        updated = 0
        for r in records:
            if not isinstance(r, dict) or r.get("type") != "recommendation":
                continue
            if r.get("run_date") and not args.force:
                continue
            rd = infer_run_date(r)
            if not rd:
                continue
            old = r.get("run_date")
            if old == rd and not args.force:
                continue
            if args.dry_run:
                print(f"  [dry-run] {fn} code={r.get('code')} date={r.get('date')} "
                      f"pred={r.get('prediction_date')} -> run_date: {old} -> {rd}")
            else:
                r["run_date"] = rd
            updated += 1
        if updated:
            total += updated
            if args.dry_run:
                print(f"[backfill] {fn}: 将更新 {updated} 条 (dry-run, 未写盘)")
            else:
                if not args.no_backup and not os.path.exists(path + ".bak"):
                    os.replace(path, path + ".bak")
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(records, f, ensure_ascii=False, indent=2)
                print(f"[backfill] {fn}: 已更新 {updated} 条 -> {path}")

    print(f"[backfill] 完成, 共更新 {total} 条 recommendation 记录"
          + (" (dry-run, 未写盘)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
