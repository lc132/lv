#!/usr/bin/env python3
# 最终验证：校验 Markdown 表格行数与 final_recommend_count 一致
# 依赖外部变量：ctx, os, log_alert, final_recommend_count
import os

md_path = ctx.get('md_path', '')
if os.path.exists(md_path):
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    # 统计 Markdown 表格中的数据行（以 | 数字 | 开头的行）
    table_rows = 0
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped.startswith('| '):
            parts = stripped.split('|')
            if len(parts) > 1 and parts[1].strip().isdigit():
                table_rows += 1
    if table_rows != final_recommend_count:
        log_alert("ERROR", "数量校验", f"概况{final_recommend_count}≠MD表格{table_rows}")
    else:
        print(f"✅ 验证通过（{final_recommend_count}只）")
else:
    log_alert("ERROR", "数量校验", f"md文件不存在: {md_path}")