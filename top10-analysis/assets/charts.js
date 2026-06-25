// assets/charts.js — TOP10 Analysis Charts
(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();
  var success = style.getPropertyValue('--success').trim();

  var names = ['中油资本', '中国中车', '小商品城', '银轮股份', '伊利股份', '上海电力', '杭氧股份', '紫光国微', '浪潮信息', '雄韬股份'];
  var ratios = [1.17, 1.17, 1.14, 1.14, 1.14, 1.13, 1.01, 1.00, 1.00, 1.00];
  var scores = [16, 15, 17, 16, 16, 16, 23, 27, 27, 27];
  var strategies = ['E', 'E', 'E', 'E', 'E', 'E', 'D', 'A', 'A', 'A'];
  var entryPrices = [6.95, 5.27, 10.04, 48.61, 24.47, 15.86, 28.10, 89.63, 70.85, 35.62];

  var stratColor = {
    'A': accent,
    'D': accent2,
    'E': success
  };

  // --- Chart: 盈亏比排名 ---
  var chartRatio = echarts.init(document.getElementById('chart-ratio'), null, { renderer: 'svg' });
  chartRatio.setOption({
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      appendToBody: true,
      formatter: function(p) { return p[0].name + '<br/>盈亏比: <b>' + p[0].value.toFixed(2) + '</b>'; }
    },
    grid: { left: '3%', right: '8%', top: '3%', bottom: '3%', containLabel: true },
    xAxis: {
      type: 'value', min: 0.9, max: 1.22,
      axisLine: { lineStyle: { color: rule } },
      axisLabel: { color: muted, fontSize: 11 },
      splitLine: { lineStyle: { color: rule } }
    },
    yAxis: {
      type: 'category',
      data: names.slice().reverse(),
      axisLine: { lineStyle: { color: rule } },
      axisLabel: { color: ink, fontSize: 11 },
      axisTick: { show: false }
    },
    series: [{
      type: 'bar',
      data: ratios.map(function(v, i) {
        return {
          value: v,
          itemStyle: {
            color: strategies[ratios.length - 1 - i] === 'E' ? success
                 : strategies[ratios.length - 1 - i] === 'D' ? accent2
                 : strategies[ratios.length - 1 - i] === 'A' ? accent
                 : muted,
            borderRadius: [0, 4, 4, 0]
          }
        };
      }).reverse(),
      barWidth: 20,
      label: {
        show: true,
        position: 'right',
        color: ink,
        fontSize: 11,
        fontWeight: 700,
        formatter: '{c}'
      }
    }]
  });
  window.addEventListener('resize', function() { chartRatio.resize(); });

  // --- Chart: 评分分布 ---
  var chartScore = echarts.init(document.getElementById('chart-score'), null, { renderer: 'svg' });
  chartScore.setOption({
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      appendToBody: true,
      formatter: function(p) { return p[0].name + '<br/>评分: <b>' + p[0].value + '</b>'; }
    },
    grid: { left: '3%', right: '8%', top: '3%', bottom: '3%', containLabel: true },
    xAxis: {
      type: 'value', min: 10, max: 30,
      axisLine: { lineStyle: { color: rule } },
      axisLabel: { color: muted, fontSize: 11 },
      splitLine: { lineStyle: { color: rule } }
    },
    yAxis: {
      type: 'category',
      data: names.slice().reverse(),
      axisLine: { lineStyle: { color: rule } },
      axisLabel: { color: ink, fontSize: 11 },
      axisTick: { show: false }
    },
    series: [{
      type: 'bar',
      data: scores.map(function(v, i) {
        return {
          value: v,
          itemStyle: {
            color: strategies[scores.length - 1 - i] === 'E' ? success
                 : strategies[scores.length - 1 - i] === 'D' ? accent2
                 : strategies[scores.length - 1 - i] === 'A' ? accent
                 : muted,
            borderRadius: [0, 4, 4, 0]
          }
        };
      }).reverse(),
      barWidth: 20,
      label: {
        show: true,
        position: 'right',
        color: ink,
        fontSize: 11,
        fontWeight: 700,
        formatter: '{c}'
      }
    }]
  });
  window.addEventListener('resize', function() { chartScore.resize(); });

  // --- Chart: 策略分布饼图 ---
  var chartStrat = echarts.init(document.getElementById('chart-strategy'), null, { renderer: 'svg' });
  chartStrat.setOption({
    animation: false,
    tooltip: {
      trigger: 'item',
      appendToBody: true,
      formatter: '{b}: {c}只 ({d}%)'
    },
    series: [{
      type: 'pie',
      radius: ['45%', '75%'],
      center: ['50%', '50%'],
      avoidLabelOverlap: false,
      itemStyle: { borderRadius: 6, borderColor: bg2, borderWidth: 3 },
      label: { show: true, color: ink, fontSize: 12, fontWeight: 600 },
      labelLine: { lineStyle: { color: rule } },
      emphasis: { disabled: true },
      data: [
        { value: 6, name: 'E 资金埋伏', itemStyle: { color: success } },
        { value: 3, name: 'A 动量延续', itemStyle: { color: accent } },
        { value: 1, name: 'D 回调企稳', itemStyle: { color: accent2 } }
      ]
    }]
  });
  window.addEventListener('resize', function() { chartStrat.resize(); });

  // --- Chart: 盈亏比 vs 评分 散点图 ---
  var chartScatter = echarts.init(document.getElementById('chart-scatter'), null, { renderer: 'svg' });
  var scatterData = [];
  for (var i = 0; i < names.length; i++) {
    scatterData.push({
      name: names[i],
      value: [ratios[i], scores[i]],
      itemStyle: {
        color: strategies[i] === 'E' ? success
             : strategies[i] === 'D' ? accent2
             : strategies[i] === 'A' ? accent
             : muted
      }
    });
  }
  chartScatter.setOption({
    animation: false,
    tooltip: {
      trigger: 'item',
      appendToBody: true,
      formatter: function(p) { return p.name + '<br/>盈亏比: ' + p.value[0].toFixed(2) + ' ｜ 评分: ' + p.value[1]; }
    },
    grid: { left: '8%', right: '8%', top: '8%', bottom: '8%' },
    xAxis: {
      name: '盈亏比',
      nameLocation: 'center',
      nameGap: 30,
      nameTextStyle: { color: muted, fontSize: 12 },
      min: 0.95, max: 1.22,
      axisLine: { lineStyle: { color: rule } },
      axisLabel: { color: muted, fontSize: 11 },
      splitLine: { lineStyle: { color: rule } }
    },
    yAxis: {
      name: '评分',
      nameLocation: 'center',
      nameGap: 35,
      nameTextStyle: { color: muted, fontSize: 12 },
      min: 10, max: 30,
      axisLine: { lineStyle: { color: rule } },
      axisLabel: { color: muted, fontSize: 11 },
      splitLine: { lineStyle: { color: rule } }
    },
    series: [{
      type: 'scatter',
      data: scatterData,
      symbolSize: function(val) { return val[1] * 0.8 + 6; },
      label: {
        show: true,
        formatter: function(p) { return p.name; },
        position: 'top',
        color: ink,
        fontSize: 10,
        fontWeight: 600
      },
      emphasis: { disabled: true }
    }]
  });
  window.addEventListener('resize', function() { chartScatter.resize(); });

})();