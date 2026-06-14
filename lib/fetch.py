#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
步骤10A-10B: 全市场API拉取、板块/行业补全
"""
from lib.core import *

# 从股票名称推断行业（正则匹配）
_INDUSTRY_PATTERNS = [
    (r'银行', '银行'),
    (r'保险|人寿|平安(?!银行)', '非银金融'),
    (r'证券|券商|民生(?!银行)', '非银金融'),
    (r'铝业|铜业|有色|黄金|稀土|钢铁|杭钢|宝钢|中钢|矿业|钨业|钼业|钛业|镁业|锌业|锆业|硅业|资源|金银', '有色金属'),
    (r'煤炭|煤业|煤电|煤化工|华阳|山煤|平煤|盘江|兖矿|神华|中煤|陕西煤业|淮北|安源|昊华', '煤炭'),
    (r'石油|石化|油田|海油', '石油石化'),
    (r'汽车|客车|摩托|动力(?!电池)|电驱|轮胎|隆鑫|宇通|上汽|中通|金龙|福田|江淮|中鼎|均胜', '汽车'),
    (r'电力|电网|核电|水电|风电|光伏|太阳能|电气|电机|新能源(?!汽车)|林洋|新能', '电力设备'),
    (r'化工|化学|化肥|农药|塑料|橡胶|化纤|氟|涂料|索普|华鲁|鲁西|华峰', '基础化工'),
    (r'制药|医药|药业|生物|医疗|器械|疫苗|敖东|海思|人福|华海|普利', '医药生物'),
    (r'电子|半导体|芯片|集成|电路|光电|微电子|京东方|宏昌|景旺|深南电路|沪电', '电子'),
    (r'软件|信息|科技|数据|网络|通信|互联|智能|数字|视觉|恒通|东华|用友', '计算机'),
    (r'食品|饮料|乳业|酒|啤酒|白酒|调味|零食|农产品|养殖|饲料|渔业|海天|牧原|温氏', '食品饮料'),
    (r'地产|房产|物业|园区|城建|保利|万科|招商蛇口|金地|金科|龙湖', '房地产'),
    (r'建筑|建材|水泥|玻璃|工程|基建|路桥|钢构|防水|铁建|中铁|电建|交建', '建筑装饰'),
    (r'军工|航空|航天|船舶|兵器|卫星|导弹|导航|北斗|防务|红箭|兵装|中兵|光启|彩虹|雷科', '国防军工'),
    (r'机场|航空(?!航天)|港口|航运|物流|高速|高铁|铁路|地铁|运输|中远|上港|东航|大秦|京沪', '交通运输'),
    (r'中免|免税|百货|零售|超市|商业|连锁|贸易|五矿|合百|大商|王府井|银座', '商贸零售'),
    (r'传媒|影视|电影|出版|广电|广告|游戏|文化|教育|娱乐|体育|人民|三七|儒意', '传媒'),
    (r'环保|水务|节能|碳中和|碳排放|碳交易|低碳|治理(?!环境)', '环保'),
    (r'家电|电器|空调|冰箱|洗衣机|美的|格力|海尔|海信|TCL', '家用电器'),
    (r'纺织|服装|服饰|家纺|印染', '纺织服饰'),
    (r'旅游|酒店|景区|旅行社|首旅', '社会服务'),
    (r'机械|重工|装备|机床|模具|轴承|液压|锅炉|泵|应流|冰轮|陕鼓|大连重工', '机械设备'),
    (r'造纸|印刷|包装', '轻工制造'),
    (r'保险|信托|租赁', '非银金融'),
    (r'电信|联通|移动|通信|通讯(?!计算机)|共进|一二|烽火|中兴', '通信'),
    (r'电缆|线缆|亨通|精达|汉缆', '电力设备'),
    (r'裕能|电池|锂|储能|正极|负极|隔膜|电解|胜华|能科|协鑫|申能|新能', '电力设备'),
    (r'材料|龙盛|天赐|宏大|神马|新材|楚江|雅化|西部', '基础化工'),
    (r'中车|东睦|重工|装备|机床|模具|轴承|液压|锅炉|泵|应流|冰轮|陕鼓', '机械设备'),
    (r'煤|焦炭|焦化', '煤炭'),
    (r'大陆|新大|恒生|顶点|金证|用友|东软|浪潮|三六|云赛|智联|网宿|光环|中科曙光|宝信|深信服', '计算机'),
    (r'黄金|铜|铝|锌|铅|锡|镍|钴|钛|稀土|有色|矿业|钨|钼|镁|锆', '有色金属'),
    (r'神火|明泰', '有色金属'),
]

# 硬编码行业修正（覆盖名称推断错误或API返回错误的情况）
_INDUSTRY_CORRECTIONS = {
    '600258': '社会服务',   # 首旅酒店
    '600026': '交通运输',   # 中远海能
    '600150': '国防军工',   # 中国船舶
    '601600': '有色金属',   # 中国铝业
    '600392': '有色金属',   # 盛和资源
    '601168': '有色金属',   # 西部矿业
    '600338': '有色金属',   # 西藏珠峰
    '600256': '石油石化',   # 广汇能源
    '600884': '电力设备',   # 杉杉股份
    '601212': '有色金属',   # 白银有色
    '600298': '食品饮料',   # 安琪酵母
    '600216': '医药生物',   # 浙江医药
    '600550': '电力设备',   # 保变电气
    '600746': '基础化工',   # 江苏索普
    '603178': '汽车',        # 圣龙股份
    '601156': '交通运输',   # 东航物流
    '601066': '非银金融',   # 中信建投
    '601688': '非银金融',   # 华泰证券
    '600352': '基础化工',   # 浙江龙盛
    '600114': '机械设备',   # 东睦股份
    '000933': '有色金属',   # 神火股份
    '601766': '机械设备',   # 中国中车
    '000997': '计算机',     # 新大陆
    '603212': '电力设备',   # 赛伍技术（光伏背板）
    '600330': '基础化工',   # 天通股份（电子材料）
    '600348': '煤炭',       # 华阳股份
    '000417': '商贸零售',   # 合百集团
    '002297': '国防军工',   # 博云新材（航空材料）
    '603260': '有色金属',   # 合盛硅业
    '600096': '基础化工',   # 云天化
    '600188': '煤炭',       # 兖矿能源（已在正则，此处保底）
    '000725': '电子',       # 京东方A
    '601058': '汽车',       # 赛轮轮胎
    '002057': '有色金属',   # 中钢天源
    '603766': '汽车',       # 隆鑫通用
    '601222': '电力设备',   # 林洋能源（非煤炭）
    '000617': '非银金融',   # 中油资本
    '000603': '有色金属',   # 盛达资源
    '601216': '基础化工',   # 君正集团
    '000333': '家用电器',   # 美的集团
    '002413': '国防军工',   # 雷科防务
    '605090': '公用事业',   # 九丰能源（燃气）
    '600602': '计算机',     # 云赛智联
    '601360': '计算机',     # 三六零
    '601166': '银行',       # 兴业银行（XD前缀已清理）
    '600406': '电力设备',   # 国电南瑞
    '600126': '有色金属',   # 杭钢股份
    '002939': '非银金融',   # 长城证券
    '601088': '煤炭',       # 中国神华（已在正则，保底）
    '600938': '石油石化',   # 中国海油
    '000801': '国防军工',   # 四川九洲（电子对抗/军用设备）
    '002023': '国防军工',   # 海特高新（航空维修）
    '002283': '汽车',       # 天润工业（曲轴/汽车零部件）
    '600312': '电力设备',   # 平高电气
    '603000': '传媒',       # 人民网
    '603026': '电力设备',   # 石大胜华（锂电池电解液溶剂）
    '000975': '有色金属',   # 山金国际（黄金矿业）
}

def _clean_name(name):
    """清理除权除息标记，避免名称匹配失败"""
    if not name:
        return name
    # XD=除息, XR=除权, DR=除权除息, N=新股, C=次新股, L=（无含义标记）
    for prefix in ['XD', 'XR', 'DR', 'N', 'C', 'L']:
        if name.startswith(prefix):
            return name[len(prefix):]
    return name

def _infer_industry_from_name(name):
    """根据股票名称中的关键词推断申万一级行业"""
    name = _clean_name(name)
    if not name:
        return None
    for pattern, industry in _INDUSTRY_PATTERNS:
        if re.search(pattern, name):
            return industry
    return None

# 批量行业查询（东方财富 clist 轻量API，一次性拉取行业映射）
def _batch_sector_lookup_clist(codes):
    """
    用东方财富 clist API 批量查询行业/板块。
    单次请求 fields=f12,f14,f100,f102, pz=6000 → 理论上全市场行业数据。
    如果API不可达，返回空dict让后续名称推断兜底。
    """
    sector_map = {}
    try:
        import urllib.parse as up
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        # 拉全量行业映射：只取代码+名称+行业+板块，pz=6000
        params = {
            "pn": "1", "pz": "6000", "po": "0", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2", "fid": "f12",
            "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:0+t:81+s:2048",
            "fields": "f12,f14,f100,f102",
            "_": str(int(time.time() * 1000))
        }
        req = urllib.request.Request(f"{url}?{up.urlencode(params)}", headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://quote.eastmoney.com/'
        })
        resp = urllib.request.urlopen(req, timeout=8)
        data = json.loads(resp.read())
        if data and data.get('data') and data['data'].get('diff'):
            for item in data['data']['diff']:
                code = str(item.get('f12', ''))
                industry = str(item.get('f100', '') or '').strip()
                sector = str(item.get('f102', '') or '').strip()
                if code and (industry or sector):
                    sector_map[code] = {
                        "sector": sector or '未知',
                        "industry": industry or '未知',
                    }
    except Exception:
        pass  # API不可达，后续名称推断兜底
    return sector_map

# ============================================================
# 步骤10A: 全市场API拉取
# ============================================================
def step10A_fetch_all_stocks(ctx):
    print("\n" + "=" * 60)
    print("步骤10A: 全市场行情拉取")
    print("=" * 60)
    
    stocks = None
    source = None
    
    # 方案一：东方财富 clist API（分页拉取，按成交额排序确保活跃标的优先）
    # v6.6.4 fix: 原按涨跌幅(fid=f3)降序导致只拉到涨停/连板标的被硬排除全灭
    # 改为按成交额(fid=f6)降序 + 分页循环 + 数据量门控
    # v6.6.17: 增加重试机制（3次，间隔2s）+ User-Agent轮换
    CLIST_RETRY = 3
    clist_success = False
    ua_list = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    ]
    
    for retry_num in range(CLIST_RETRY):
        try:
            import urllib.parse
            url = "https://push2.eastmoney.com/api/qt/clist/get"
            headers = {
                'User-Agent': ua_list[retry_num % len(ua_list)],
                'Referer': 'https://quote.eastmoney.com/'
            }
            
            page_size = 100
            max_pages = 60
            all_items = []
            total_from_api = 0
            seen_codes = set()
            
            for page in range(1, max_pages + 1):
                params = {
                    "pn": str(page), "pz": str(page_size), "po": "0", "np": "1",
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                    "fltt": "2", "invt": "2", "fid": "f6",
                    "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
                    "fields": "f2,f3,f5,f6,f7,f8,f10,f12,f14,f15,f16,f17,f18,f20,f62,f100,f102",
                    "_": str(int(time.time() * 1000))
                }
                try:
                    req = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers=headers)
                    resp = urllib.request.urlopen(req, timeout=15)
                    data = json.loads(resp.read())
                except Exception:
                    if page == 1:
                        raise
                    break
                
                if not data or not data.get('data'):
                    break
                
                diff = data['data'].get('diff')
                if not diff or len(diff) == 0:
                    break
                
                if page == 1:
                    total_from_api = data['data'].get('total', 0)
                
                new_count = 0
                for item in diff:
                    code = str(item.get('f12', ''))
                    if code in seen_codes:
                        continue
                    seen_codes.add(code)
                    all_items.append(item)
                    new_count += 1
                
                if new_count == 0 or len(diff) < page_size:
                    break
                
                time.sleep(0.05)
            
            CLIST_MIN_THRESHOLD = 500
            if len(all_items) < CLIST_MIN_THRESHOLD:
                print(f"  clist重试{retry_num+1}: 仅返回{len(all_items)}只(总计{total_from_api})，低于门控{CLIST_MIN_THRESHOLD}")
                if retry_num < CLIST_RETRY - 1:
                    time.sleep(2)
                    continue
                log_alert("WARN", "行情采集", f"clist返回量({len(all_items)})低于门控，降级新浪")
                stocks = None
            else:
                # 解析
                stocks = []
                for item in all_items:
                    code = item.get('f12', '')
                    name = item.get('f14', '')
                    if not code or not name:
                        continue
                    close_val = item.get('f2')
                    if close_val == '-' or close_val is None:
                        continue
                    try:
                        stocks.append({
                            "code": str(code), "name": str(name),
                            "open": float(item.get('f17', 0)) if item.get('f17') not in (None, '-') else None,
                            "close": float(close_val),
                            "change_pct": float(item.get('f3', 0)) if item.get('f3') not in (None, '-') else 0,
                            "turnover": float(item.get('f8', 0)) if item.get('f8') not in (None, '-') else 0,
                            "amplitude": float(item.get('f7', 0)) if item.get('f7') not in (None, '-') else 0,
                            "volume_ratio": float(item.get('f10', 0)) if item.get('f10') not in (None, '-') else None,
                            "amount": float(item.get('f6', 0)) if item.get('f6') not in (None, '-') else None,
                            "high": float(item.get('f15', 0)) if item.get('f15') not in (None, '-') else None,
                            "low": float(item.get('f16', 0)) if item.get('f16') not in (None, '-') else None,
                            "prev_close": float(item.get('f18', 0)) if item.get('f18') not in (None, '-') else None,
                            "main_inflow": float(item.get('f62', 0)) if item.get('f62') not in (None, '-') else None,
                            "total_cap": float(item.get('f20', 0)) if item.get('f20') not in (None, '-') else None,
                            "sector": str(item.get('f102', '') or '').strip() or '未知',
                            "industry": str(item.get('f100', '') or '').strip() or '未知',
                        })
                    except (ValueError, TypeError):
                        continue
                source = "clist"
                clist_success = True
                print(f"  clist重试{retry_num+1}成功: {len(stocks)}只(总计{total_from_api})")
                break
                
        except Exception as e:
            print(f"  clist重试{retry_num+1}失败: {str(e)[:60]}")
            if retry_num < CLIST_RETRY - 1:
                time.sleep(2)
                continue
            log_alert("INFO", "行情采集", f"clist不可达(重试{CLIST_RETRY}次): {str(e)[:60]}，降级新浪")
    
    if not clist_success:
        stocks = None
    
    # 方案二：新浪批处理
    if stocks is None:
        try:
            stocks = []
            # 生成全A股代码（排除北交所），每2个取1个提高效率
            all_codes = []
            # 上海主板: 600000-605999 (step=2)
            for i in range(600000, 606000, 2):
                all_codes.append(f"sh{i}")
            # 深圳主板: 000001-004999 (step=2)
            for i in range(1, 5000, 2):
                all_codes.append(f"sz{i:06d}")
            # 创业板: 300000-301999 (step=2)
            for i in range(300000, 302000, 2):
                all_codes.append(f"sz{i}")
            
            # 分批拉取，每批100个
            batch_size = 100
            print(f"  新浪API分批拉取: {len(all_codes)}个代码, {len(all_codes)//batch_size}批")
            for i in range(0, len(all_codes), batch_size):
                batch = all_codes[i:i+batch_size]
                try:
                    url = f"https://hq.sinajs.cn/list={','.join(batch)}"
                    req = urllib.request.Request(url, headers={
                        'User-Agent': 'Mozilla/5.0',
                        'Referer': 'https://finance.sina.com.cn'
                    })
                    resp = urllib.request.urlopen(req, timeout=5)
                    text = resp.read().decode('gbk')
                    for line in text.strip().split('\n'):
                        if not line or '=""' in line:
                            continue
                        try:
                            parts = line.split('"')[1].split(',')
                            if len(parts) < 6:
                                continue
                            header = line.split('="')[0]
                            raw_code = header.split('_')[-1] if '_' in header else header[-6:]
                            code = raw_code if len(raw_code) == 6 else raw_code[-6:]
                            name = parts[0]
                            current = float(parts[3]) if parts[3] and parts[3] != '' else 0
                            prev_close = float(parts[2]) if parts[2] and parts[2] != '' else 0
                            if current <= 0 or prev_close <= 0:
                                continue
                            change_pct = round((current - prev_close) / prev_close * 100, 2)
                            
                            market_type = 'sz' if code.startswith(('000','001','002','003','300','301')) else 'sh'
                            # 新浪API不提供换手率字段（parts[37]/[38]不存在），默认0
                            turnover = 0.0
                            
                            # 成交额(万元): parts[9]
                            amount_val = float(parts[9]) if len(parts) > 9 and parts[9] and parts[9] != '' else 0
                            # 振幅: (high-low)/prev_close*100
                            high_p = float(parts[4]) if parts[4] and parts[4] != '' else 0
                            low_p = float(parts[5]) if parts[5] and parts[5] != '' else 0
                            amplitude = round((high_p - low_p) / prev_close * 100, 2) if prev_close > 0 else 0
                            
                            stocks.append({
                                "code": code, "name": name,
                                "open": float(parts[1]) if parts[1] and parts[1] != '' else 0,
                                "close": current,
                                "change_pct": change_pct,
                                "turnover": turnover,
                                "amplitude": amplitude,
                                "high": high_p,
                                "low": low_p,
                                "prev_close": prev_close,
                                "volume": float(parts[8]) if len(parts) > 8 and parts[8] and parts[8] != '' else 0,
                                "volume_ratio": None,
                                "amount": amount_val,
                                "main_inflow": None,
                                "total_cap": None,
                            })
                        except (ValueError, IndexError):
                            continue
                except Exception:
                    continue
                # 每20批短暂休息
                if (i // batch_size) % 20 == 19:
                    time.sleep(0.03)
            source = "sina"
        except Exception as e:
            log_alert("ERROR", "行情采集", f"全市场API拉取失败: {str(e)[:100]}")
            raise RuntimeError(f"行情数据获取失败: {str(e)[:100]}")
    
    print(f"  全市场拉取到 {len(stocks)} 只标的 (来源: {source})")
    log_alert("INFO", "行情采集", f"全市场拉取到 {len(stocks)} 只标的（来源: {source}）")
    
    # 构建原始标的池：分两路
    # 上涨池：涨跌幅0%~7%（涨停/涨幅>7%会被硬排除，不浪费名额），按成交额取TOP400
    gainers = [s for s in stocks
               if s['change_pct'] is not None and 0 < s['change_pct'] <= 7
               and s['close'] is not None and s['close'] > 0
               and s.get('amount', 0) >= 10_000_000]  # 至少1000万成交额
    if source == 'clist':
        gainers.sort(key=lambda x: (x.get('turnover', 0) or 0), reverse=True)
    else:
        gainers.sort(key=lambda x: (x.get('amount', 0) or 0), reverse=True)
    gainers = gainers[:400]
    
    # 下跌池：涨跌幅-7%~0%（跌停会被硬排除），按成交额取TOP100
    losers = [s for s in stocks
              if s['change_pct'] is not None and -7 <= s['change_pct'] < 0
              and s['close'] is not None and s['close'] > 0
              and s.get('amount', 0) >= 10_000_000]
    if source == 'clist':
        losers.sort(key=lambda x: (x.get('turnover', 0) or 0), reverse=True)
    else:
        losers.sort(key=lambda x: (x.get('amount', 0) or 0), reverse=True)
    losers = losers[:100]
    
    raw_pool = gainers + losers
    
    ctx['raw_pool'] = raw_pool
    ctx['total_raw'] = len(raw_pool)
    gainer_cnt = len(gainers)
    loser_cnt = len(losers)
    print(f"  原始标的池: {len(raw_pool)} 只（上涨{gainer_cnt}+下跌{loser_cnt}，成交额≥1000万）")
    log_alert("INFO", "行情采集", f"原始标的池: {len(raw_pool)} 只（上涨{gainer_cnt}+下跌{loser_cnt}）")
    ctx['_data_source'] = source

# ============================================================
# 步骤10B: 板块/行业补全
# ============================================================
def step10B_sector_backfill(ctx):
    print("\n" + "=" * 60)
    print("步骤10B: 板块/行业补全")
    print("=" * 60)
    
    raw_pool = ctx.get('raw_pool', [])
    candidates = []
    source = ctx.get('_data_source', 'unknown')
    
    # 如果数据来自 Sina（无行业数据），批量查东方财富补全
    sector_lookup = {}
    sina_codes = [s['code'] for s in raw_pool if not s.get('sector') or s.get('sector') == '未知' or s.get('sector') == '']
    
    if sina_codes:
        print(f"  行业补全: {len(sina_codes)} 只标的需要查板块...")
        # 策略1: 东方财富批量行业API（单次拉取所有标的的行业映射，轻量级）
        sector_lookup = _batch_sector_lookup_clist(sina_codes)
        filled_via_api = sum(1 for v in sector_lookup.values() if v.get('industry') and v['industry'] != '未知')
        print(f"  clist行业API: {filled_via_api}/{len(sina_codes)} 成功")
        
        # 策略2: 名称规则推断（作为兜底）
        for s in raw_pool:
            code = s['code']
            if code in sector_lookup and sector_lookup[code].get('industry') and sector_lookup[code]['industry'] != '未知':
                continue
            inferred = _infer_industry_from_name(s.get('name', ''))
            if inferred:
                sector_lookup[code] = {"sector": inferred, "industry": inferred}
        
        rule_filled = sum(1 for s in raw_pool if s['code'] in sector_lookup and sector_lookup[s['code']].get('industry') and sector_lookup[s['code']]['industry'] != '未知')
        print(f"  总计行业已知: {rule_filled}/{len(sina_codes)}")
        
        # 策略3: 硬编码行业修正（覆盖已知错误）
        corrected = 0
        for code, correct_industry in _INDUSTRY_CORRECTIONS.items():
            if code in sina_codes:
                if code not in sector_lookup or sector_lookup[code].get('industry') != correct_industry:
                    sector_lookup[code] = {"sector": correct_industry, "industry": correct_industry}
                    corrected += 1
        if corrected > 0:
            print(f"  硬编码行业修正: {corrected} 只")
    else:
        print(f"  clist数据已含行业信息，无需补全")
    
    # 构建标的池（含板块/行业）
    for s in raw_pool:
        code = s['code']
        # 优先用 clist 自带数据，其次查表，最后标记未知
        sector = s.get('sector', '') or sector_lookup.get(code, {}).get('sector', '') or '未知'
        industry = s.get('industry', '') or sector_lookup.get(code, {}).get('industry', '') or '未知'
        
        c = {
            "code": code,
            "name": s["name"],
            "sector": sector,
            "industry": industry,
            "change_pct": s.get("change_pct"),
            "open": s.get("open"),
            "close": s.get("close"),
            "turnover": s.get("turnover"),
            "amplitude": s.get("amplitude"),
            "volume_ratio": s.get("volume_ratio"),
            "amount": s.get("amount"),
            "main_inflow": s.get("main_inflow"),
            "high": s.get("high"),
            "low": s.get("low"),
            "prev_close": s.get("prev_close"),
            "total_cap": s.get("total_cap"),
            "strategy": "",
            "reason": "",
            "score": 0,
            "confidence": "",
            "entry": None,
            "stop_loss": None,
            "take_profit": None,
            "url": f"https://quote.eastmoney.com/concept/sh{code}.html" if code.startswith('6') else f"https://quote.eastmoney.com/concept/sz{code}.html",
            "L3_flags": [],
            "L2_skip": [],
        }
        
        # 数据校验
        if c["close"] is None or c["close"] <= 0:
            continue
        if c["change_pct"] is None:
            continue
        
        candidates.append(c)
    
    unknown_count = sum(1 for c in candidates if c['industry'] == '未知')
    print(f"  标的池构建完成: {len(candidates)} 只 (行业已知: {len(candidates)-unknown_count}, 未知: {unknown_count})")
    ctx['candidates'] = candidates
    ctx['total_candidates'] = len(candidates)