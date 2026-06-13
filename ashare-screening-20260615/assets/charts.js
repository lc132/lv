(function(){var s=getComputedStyle(document.documentElement);
var A=s.getPropertyValue("--accent").trim(),A2=s.getPropertyValue("--accent2").trim();
var I=s.getPropertyValue("--ink").trim(),M=s.getPropertyValue("--muted").trim(),R=s.getPropertyValue("--rule").trim();

var c1=echarts.init(document.getElementById("chart-strategy"),null,{renderer:"svg"});
c1.setOption({animation:false,tooltip:{trigger:"item",appendToBody:true,formatter:"{b}: {c}只 ({d}%)"},
legend:{bottom:0,textStyle:{color:I,fontSize:12}},
series:[{type:"pie",radius:["42%","72%"],center:["50%","45%"],
label:{show:true,formatter:"{b}\n{c}只",fontSize:12,color:I},
data:[{value:3,name:"B 超跌反弹",itemStyle:{color:"#3B82F6"}},
{value:0,name:"A 动量延续",itemStyle:{color:"#22C55E"}},
{value:0,name:"C 事件驱动",itemStyle:{color:"#A855F7"}},
{value:0,name:"D 资金埋伏",itemStyle:{color:"#EAB308"}},
{value:0,name:"E 回调企稳",itemStyle:{color:"#EC4899"}}]}]});
window.addEventListener("resize",function(){c1.resize()});

var exNames=["\u80a1\u4ef7<5\u5143", "7\u65e5\u5185\u5df2\u63a8\u8350", "ST/*ST", "\u6da8\u505c/\u8fde\u677f", "\u6da8\u5e45>7%"],exVals=[171, 10, 9, 9, 4];
var c2=echarts.init(document.getElementById("chart-exclusion"),null,{renderer:"svg"});
c2.setOption({animation:false,tooltip:{trigger:"axis",appendToBody:true,axisPointer:{type:"shadow"}},
grid:{left:90,right:30,top:10,bottom:20},
xAxis:{type:"value",axisLabel:{color:M,fontSize:11},splitLine:{lineStyle:{color:R}}},
yAxis:{type:"category",data:exNames.reverse(),axisLabel:{color:I,fontSize:11},axisLine:{show:false},axisTick:{show:false}},
series:[{type:"bar",data:exVals.reverse(),
itemStyle:{color:new echarts.graphic.LinearGradient(0,0,1,0,[{offset:0,color:"#3B82F6"},{offset:1,color:"#93C5FD"}]),borderRadius:[0,4,4,0]},
label:{show:true,position:"right",color:I,fontSize:11,formatter:"{c}只"}}]});
window.addEventListener("resize",function(){c2.resize()});

var c3=echarts.init(document.getElementById("chart-funnel"),null,{renderer:"svg"});
c3.setOption({animation:false,tooltip:{trigger:"item",appendToBody:true,formatter:"{b}: {c}只"},
series:[{type:"funnel",left:"15%",right:"15%",top:20,bottom:20,minSize:"18%",maxSize:"100%",sort:"descending",gap:6,
label:{show:true,position:"inside",formatter:"{b}\n{c}只",fontSize:12,color:"#FFF"},
data:[{value:500,name:"1 原始标的池",itemStyle:{color:"#64748B"}},
{value:290,name:"2 硬排除后",itemStyle:{color:"#3B82F6"}},
{value:255,name:"3 信号过滤后",itemStyle:{color:"#6366F1"}},
{value:5,name:"4 策略匹配",itemStyle:{color:"#8B5CF6"}},
{value:3,name:"5 行业+新闻",itemStyle:{color:"#A855F7"}}]}]});
window.addEventListener("resize",function(){c3.resize()});

var sigNames=["\u5047\u52a8\u91cf\u9ad8\u5f00", "\u7f29\u91cf\u6da8\u505c", "\u8bf1\u591a", "\u632f\u5e45>15%"],sigVals=[13, 12, 10, 1];
var c4=echarts.init(document.getElementById("chart-signal"),null,{renderer:"svg"});
c4.setOption({animation:false,tooltip:{trigger:"axis",appendToBody:true,axisPointer:{type:"shadow"}},
grid:{left:90,right:30,top:10,bottom:20},
xAxis:{type:"value",axisLabel:{color:M,fontSize:11},splitLine:{lineStyle:{color:R}}},
yAxis:{type:"category",data:sigNames.reverse(),axisLabel:{color:I,fontSize:11},axisLine:{show:false},axisTick:{show:false}},
series:[{type:"bar",data:sigVals.reverse(),
itemStyle:{color:new echarts.graphic.LinearGradient(0,0,1,0,[{offset:0,color:"#F59E0B"},{offset:1,color:"#FDE68A"}]),borderRadius:[0,4,4,0]},
label:{show:true,position:"right",color:I,fontSize:11,formatter:"{c}只"}}]});
window.addEventListener("resize",function(){c4.resize()});
})();