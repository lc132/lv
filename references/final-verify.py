md_path = ctx.get('md_path', '')
if os.path.exists(md_path):
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    # 统计 Markdown 表格中的数据行（以 | 数字 | 开头的行）
    table_rows = sum(1 for line in content.split('
')
                     if line.strip().startswith('| ') and line.split('|')[1].strip().isdigit())
    if table_rows != final_recommend_count:
        log_alert("ERROR", "数量校验", f"概况{final_recommend_count}≠MD表格{table_rows}")
    else:
        print(f"✅ 验证通过（{final_recommend_count}只）")
