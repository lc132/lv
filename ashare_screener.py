#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股每日盘前短线标的筛选 v6.6.20
严格按 SKILL.md 十五、完整执行步骤 逐步执行
"""
# 从各个lib模块导入所有步骤函数
from lib.core import *
from lib.market_check import *
from lib.holdings import *
from lib.pipeline import *
from lib.fetch import *
from lib.filter import *
from lib.match import *
from lib.score import *
from lib.news import *
from lib.output import *
from lib.sync import *

# ============================================================
# 步骤0: 获取北京时间
# ============================================================
def step0_get_beijing_time():
    """通过网络授时API获取精确北京时间"""
    print("=" * 60)
    print("步骤0: 获取北京时间")
    print("=" * 60)
    
    beijing_now = None
    TIME_APIS = [
        'https://timeapi.io/api/time/current/zone?timeZone=Asia/Shanghai',
    ]
    for api_url in TIME_APIS:
        try:
            req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read())
            # fromisoformat 在 Python 3.10 不支持7位小数秒，截断到6位
            dt_str = data['dateTime']
            if '.' in dt_str:
                date_part, frac = dt_str.split('.')
                frac = frac[:6]  # 截断到微秒
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

    if beijing_now is None:
        log_alert("ERROR", "北京时间", "所有授时API均不可达，本次筛选中止")
        raise RuntimeError("北京时间获取失败")

    beijing_date = beijing_now.strftime('%Y-%m-%d')
    beijing_hour = beijing_now.hour
    beijing_weekday = beijing_now.weekday()

    # data_date
    if beijing_weekday == 5:
        data_date = (beijing_now - timedelta(days=1)).strftime('%Y-%m-%d')
    elif beijing_weekday == 6:
        data_date = (beijing_now - timedelta(days=2)).strftime('%Y-%m-%d')
    else:
        data_date = beijing_date

    # prediction_date
    if beijing_weekday <= 3:
        prediction_date = (beijing_now + timedelta(days=1)).strftime('%Y-%m-%d')
    elif beijing_weekday == 4:
        prediction_date = (beijing_now + timedelta(days=3)).strftime('%Y-%m-%d')
    elif beijing_weekday == 5:
        prediction_date = (beijing_now + timedelta(days=2)).strftime('%Y-%m-%d')
    else:
        prediction_date = (beijing_now + timedelta(days=1)).strftime('%Y-%m-%d')

    print(f"  北京时间: {beijing_date} {beijing_now.strftime('%H:%M:%S')} (周{beijing_weekday+1})")
    print(f"  数据日期: {data_date}")
    print(f"  预测日期: {prediction_date}")
    
    return {
        'beijing_now': beijing_now,
        'beijing_date': beijing_date,
        'beijing_hour': beijing_hour,
        'beijing_weekday': beijing_weekday,
        'data_date': data_date,
        'prediction_date': prediction_date
    }

# ============================================================
# 步骤0A: 从GitHub拉取持仓跟踪
# ============================================================
def step0A_github_pull(ctx):
    print("\n" + "=" * 60)
    print("步骤0A: 从GitHub拉取持仓跟踪")
    print("=" * 60)
    
    repo_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_REPO}.git"
    pull_dir = f"{TEMP_DIR}/lv_pull"
    
    try:
        if os.path.exists(pull_dir):
            shutil.rmtree(pull_dir, ignore_errors=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", "main", repo_url, pull_dir],
            capture_output=True, text=True, timeout=30, check=True
        )
        print("  GitHub拉取成功")
        
        # 同步持仓跟踪.xlsx
        remote_holding = os.path.join(pull_dir, "持仓跟踪.xlsx")
        local_holding = f"{DATA_DIR}/持仓跟踪.xlsx"
        if os.path.exists(remote_holding):
            shutil.copy(remote_holding, local_holding)
            print(f"  持仓跟踪.xlsx 已同步")
        
        # 同步推荐历史归档文件
        synced_files = 0
        for f in os.listdir(pull_dir):
            if f.startswith("推荐历史_") and f.endswith(".json"):
                remote_path = os.path.join(pull_dir, f)
                local_path = os.path.join(DATA_DIR, f)
                if not os.path.exists(local_path) or os.path.getmtime(remote_path) > os.path.getmtime(local_path):
                    shutil.copy(remote_path, local_path)
                    synced_files += 1
        if synced_files > 0:
            print(f"  推荐历史归档: 同步 {synced_files} 个文件")
        else:
            print(f"  推荐历史归档: 无需更新")
        
        # 读取持仓记录
        holdings = []
        all_history = read_all_history()
        for r in all_history:
            if r.get('type') == 'holding':
                holdings.append(r)
        print(f"  持仓记录: {len(holdings)} 条")
        ctx['holdings'] = holdings
        ctx['all_history'] = all_history
        
    except Exception as e:
        log_alert("WARNING", "GitHub拉取", f"失败: {str(e)[:100]}")
        print(f"  ⚠️ GitHub拉取失败: {str(e)[:80]}")
        # 不阻断，继续使用本地数据
        ctx['holdings'] = []
        ctx['all_history'] = read_all_history()
    finally:
        if os.path.exists(pull_dir):
            shutil.rmtree(pull_dir, ignore_errors=True)

# ============================================================
# 主流程
# ============================================================
def main():
    ctx = {}
    
    try:
        # 步骤0: 获取北京时间
        time_info = step0_get_beijing_time()
        ctx.update(time_info)
        
        # 步骤0A: GitHub拉取持仓跟踪
        step0A_github_pull(ctx)
        
        # 步骤1: 节假日检查
        step1_holiday_check(ctx)
        if ctx.get('skip'):
            print("\n今日跳过筛选（节假日/周末）")
            return
        
        # 步骤2: 极端行情
        step2_extreme_market(ctx)
        if ctx.get('skip'):
            print("\n今日跳过筛选（极端行情）")
            return
        
        # 步骤3: 外围市场
        step3_foreign_market(ctx)
        
        # 步骤3A: 期货
        step3A_futures(ctx)
        
        # 步骤4: 持仓行情同步
        step4_holdings_sync(ctx)
        
        # 步骤4A: 做T评估
        step4A_do_T_eval(ctx)
        
        # 步骤4B: 持仓跟踪同步
        step4B_sync_holding_xlsx(ctx)
        
        # 步骤4C: 持仓危机
        step4C_holding_crisis(ctx)
        
        # 步骤5: 推荐历史清理
        step5_clean_history(ctx)
        
        # 步骤6: 文件初始化
        step6_file_init(ctx)
        
        # 步骤7: 财报季
        step7_earnings_season(ctx)
        
        # 步骤8: 大盘判断
        step8_market_judgment(ctx)
        
        # 步骤9: 板块轮动
        step9_sector_rotation(ctx)
        
        # 步骤9A: 最大持仓天数
        step9A_max_holding(ctx)
        
        # 步骤9B: 回撤断路器
        step9B_circuit_breaker(ctx)
        
        # 步骤9C: 兑现率
        step9C_conversion_rate(ctx)
        
        # 步骤9C 暂停检查：连续低兑现率→跳过今日筛选
        if ctx.get('skip'):
            print("\n⛔ 连续低兑现率，今日暂停推荐")
            return
        
        # 步骤10A: 全市场API拉取
        step10A_fetch_all_stocks(ctx)
        
        # 步骤10B: 板块/行业补全
        step10B_sector_backfill(ctx)
        
        # 步骤11: 硬排除
        step11_hard_exclude(ctx)
        
        # 步骤12: 信号过滤
        step12_signal_filter(ctx)
        
        # 步骤13: 策略匹配
        step13_strategy_match(ctx)
        
        # 步骤14-16: 评分
        step14_16_scoring(ctx)
        
        # 步骤17: 行业限制
        step17_industry_limit(ctx)
        
        # 步骤18: 新闻筛查
        step18_news_screening(ctx)
        
        # 步骤19: 推荐不足降级
        step19_insufficient_downgrade(ctx)
        
        # 步骤20: 输出Markdown
        step20_output_markdown(ctx)
        
        # 步骤20B: HTML报告
        step20B_html_report(ctx)
        
        # 步骤21: 最终验证
        step21_final_verify(ctx)
        
        # 步骤22: 写推荐历史
        step22_write_history(ctx)
        
        # 步骤23: 回溯检查昨日做T
        step23_backtrack_do_T(ctx)
        
        # 步骤24: 告警摘要
        step24_alert_summary(ctx)
        
        # 步骤25: 筛选概况
        step25_summary(ctx)
        
        # 步骤26: GitHub同步
        step26_github_sync(ctx)
        
        # 步骤27: 飞书推送
        step27_feishu_push(ctx)
        
        # 步骤28: 每周复盘拉取（仅周六执行）
        step28_weekly_review(ctx)
        
        # 持仓危机告警优先展示
        crisis_alerts = ctx.get('holding_crisis_alerts', [])
        if crisis_alerts:
            print("\n" + "=" * 60)
            print("⚠️ 持仓危机告警")
            print("=" * 60)
            for a in crisis_alerts:
                print(f"  {a}")
        
    except Exception as e:
        print(f"\n❌ 筛选流程异常: {str(e)}")
        log_alert("ERROR", "筛选流程", f"异常: {str(e)[:200]}")
        raise

if __name__ == '__main__':
    main()