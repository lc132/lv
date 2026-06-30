(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();
  var surface = style.getPropertyValue('--surface').trim();
  var good = style.getPropertyValue('--good').trim();
  var warn = style.getPropertyValue('--warn').trim();
  var palette = [accent, accent2, good, warn, muted];
  var strategyData = [{"name": "A 动量延续", "count": 2}, {"name": "B 超跌反弹", "count": 5}, {"name": "D 回调企稳", "count": 12}, {"name": "E 资金埋伏", "count": 1}, {"name": "F 北向资金", "count": 2}, {"name": "H 地量见底", "count": 2}, {"name": "I 均线突破", "count": 3}, {"name": "J 龙回头", "count": 5}, {"name": "N 新高突破", "count": 2}];
  var industryData = [{"name": "基础化工", "value": 4}, {"name": "有色金属", "value": 4}, {"name": "电子", "value": 4}, {"name": "机械设备", "value": 3}, {"name": "传媒", "value": 3}, {"name": "建筑装饰", "value": 2}, {"name": "电力设备", "value": 2}, {"name": "公用事业", "value": 2}, {"name": "家用电器", "value": 1}, {"name": "计算机", "value": 1}, {"name": "商贸零售", "value": 1}, {"name": "建筑材料", "value": 1}, {"name": "食品饮料", "value": 1}, {"name": "轻工制造", "value": 1}, {"name": "医药生物", "value": 1}, {"name": "汽车", "value": 1}, {"name": "通信", "value": 1}, {"name": "非银金融", "value": 1}];
  var limitData = [{"sector": "电子", "count": 65, "strength": "🔥🔥🔥 极强"}, {"sector": "机械设备", "count": 38, "strength": "🔥🔥🔥 极强"}, {"sector": "基础化工", "count": 22, "strength": "🔥🔥🔥 极强"}, {"sector": "计算机", "count": 14, "strength": "🔥🔥🔥 极强"}, {"sector": "电力设备", "count": 13, "strength": "🔥🔥🔥 极强"}, {"sector": "汽车", "count": 7, "strength": "🔥🔥🔥 极强"}, {"sector": "轻工制造", "count": 6, "strength": "🔥🔥🔥 极强"}, {"sector": "有色金属", "count": 6, "strength": "🔥🔥🔥 极强"}, {"sector": "社会服务", "count": 5, "strength": "🔥🔥🔥 极强"}, {"sector": "房地产", "count": 3, "strength": "🔥🔥 较强"}];
  var funnelData = [{"name": "①原始标的池", "value": 500}, {"name": "②硬排除", "value": 431}, {"name": "③信号过滤", "value": 185}, {"name": "④策略匹配", "value": 124}, {"name": "⑤微观结构过滤", "value": 109}, {"name": "⑥行业+同策略限制", "value": 34}, {"name": "⑦新闻筛查", "value": 34}, {"name": "★最终推荐", "value": 34}];

  function init(id, option) {
    var el = document.getElementById(id);
    if (!el) return null;
    var chart = echarts.init(el, null, { renderer: 'svg' });
    chart.setOption(option);
    window.addEventListener('resize', function() { chart.resize(); });
    return chart;
  }

  init('chart-strategy', {
    animation: false,
    tooltip: { trigger: 'axis', appendToBody: true },
    grid: { left: 92, right: 38, top: 18, bottom: 24 },
    xAxis: {
      type: 'value',
      axisLabel: { color: muted },
      splitLine: { lineStyle: { color: rule } },
      axisLine: { lineStyle: { color: rule } }
    },
    yAxis: {
      type: 'category',
      inverse: true,
      data: strategyData.map(function(x) { return x.name; }),
      axisLabel: { color: muted, fontSize: 12 },
      axisLine: { lineStyle: { color: rule } }
    },
    series: [{
      type: 'bar',
      data: strategyData.map(function(x, i) { return { value: x.count, itemStyle: { color: palette[i % palette.length] } }; }),
      barWidth: 14,
      label: { show: true, position: 'right', color: ink, formatter: '{c}只' },
      itemStyle: { borderRadius: [0, 8, 8, 0] }
    }]
  });

  init('chart-industry', {
    animation: false,
    tooltip: { trigger: 'axis', appendToBody: true },
    grid: { left: 44, right: 18, top: 18, bottom: 70 },
    xAxis: { type: 'category', data: industryData.map(function(x) { return x.name; }), axisLabel: { color: muted, rotate: 38 }, axisLine: { lineStyle: { color: rule } } },
    yAxis: { type: 'value', axisLabel: { color: muted }, splitLine: { lineStyle: { color: rule } } },
    series: [{ type: 'bar', data: industryData.map(function(x) { return x.value; }), itemStyle: { color: accent2, borderRadius: [5,5,0,0] } }]
  });

  init('chart-limitup', {
    animation: false,
    tooltip: { trigger: 'axis', appendToBody: true },
    grid: { left: 48, right: 18, top: 18, bottom: 80 },
    xAxis: { type: 'category', data: limitData.map(function(x) { return x.sector; }), axisLabel: { color: muted, rotate: 35 }, axisLine: { lineStyle: { color: rule } } },
    yAxis: { type: 'value', axisLabel: { color: muted }, splitLine: { lineStyle: { color: rule } } },
    series: [{ type: 'bar', data: limitData.map(function(x) { return x.count; }), itemStyle: { color: accent2, borderRadius: [6,6,0,0] } }]
  });

  init('chart-funnel', {
    animation: false,
    tooltip: { trigger: 'item', appendToBody: true },
    series: [{
      type: 'funnel',
      left: '8%',
      top: 20,
      width: '84%',
      height: '82%',
      sort: 'descending',
      gap: 4,
      label: { color: ink, formatter: '{b}: {c}只' },
      itemStyle: { borderColor: surface, borderWidth: 2 },
      data: funnelData
    }]
  });
})();
