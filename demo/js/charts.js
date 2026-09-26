/* ============================================================================
   charts.js —— ECharts 封装（本地 vendor/echarts.min.js，零 CDN）
   惰性初始化：首次进入该屏才初始化，避免隐藏容器 0 尺寸导致空白；
   每次切屏统一 resize()。
   ========================================================================== */
(function () {
  'use strict';
  var D = window.DEMO_DATA;
  var C = { ours: '#c0392b', base: '#78909c', ns3: '#2c6e9b', py: '#7d5ba6', ok: '#1e8449',
            warn: '#b7791f', ink: '#152232', muted: '#6b7c91', line: '#dbe3ec' };
  var reg = [], made = {};
  var mode = 'ours';
  /** 对比开关：被强调的一侧用红色，另一侧用灰蓝（只改呈现侧重，不改数值） */
  function em(side) { return (side === mode) ? C.ours : C.base; }
  function bd(side) { return (side === mode) ? { borderColor: '#152232', borderWidth: 2 } : {}; }

  function register(screen, dom, builder) { reg.push({ s: screen, d: dom, b: builder }); }

  function onShow(id) {
    var todo = reg.filter(function (r) { return r.s === id; });
    if (!todo.length) return;
    if (!window.echarts) { console.error('[demo] echarts 未加载'); return; }
    todo.forEach(function (r) {
      var el = document.getElementById(r.d);
      if (!el) return;
      if (!made[r.d]) {
        var inst = echarts.init(el, null, { renderer: 'canvas' });
        made[r.d] = inst;
      }
      try {
        made[r.d].setOption(r.b(), true);
        made[r.d].resize();
      } catch (e) { console.error('[demo] chart ' + r.d + ': ' + e.message); }
    });
  }
  function resizeAll() {
    Object.keys(made).forEach(function (k) { try { made[k].resize(); } catch (e) {} });
  }
  function base(extra) {
    var o = {
      textStyle: { fontFamily: '"Microsoft YaHei","Segoe UI",sans-serif', color: C.ink, fontSize: 12 },
      grid: { left: 58, right: 22, top: 34, bottom: 38 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross', label: { fontSize: 10 } },
                 backgroundColor: '#fff', borderColor: C.line, textStyle: { color: C.ink, fontSize: 12 } },
      animationDuration: 420
    };
    if (extra) for (var k in extra) o[k] = extra[k];
    return o;
  }

  /* ---------- S6：拦截率随泄露占比（实测 vs 理论线 1−share） ---------- */
  function cS6() {
    var g = D.sensitivity.groups.compromised_share.points;
    var xs = g.map(function (p) { return +p.x; });
    var ys = g.map(function (p) { return +p.block_rate; });
    var th = xs.map(function (x) { return +(1 - x).toFixed(4); });
    return base({
      xAxis: { type: 'category', data: xs.map(function (x) { return '泄露 ' + x.toFixed(2); }),
               axisLine: { lineStyle: { color: C.line } }, axisLabel: { color: C.muted } },
      yAxis: { type: 'value', min: 0.4, max: 1.05, name: '拦截率', nameTextStyle: { color: C.muted },
               splitLine: { lineStyle: { color: '#eef2f7' } }, axisLabel: { color: C.muted, formatter: function (v) { return v.toFixed(2); } } },
      legend: { data: ['实测拦截率', '理论线 1 − 泄露占比'], top: 0, textStyle: { fontSize: 11.5 } },
      series: [
        { name: '实测拦截率', type: 'line', data: ys, symbolSize: 9, lineStyle: { width: 3, color: em('ours') },
          itemStyle: { color: em('ours') }, label: { show: true, fontSize: 11, color: em('ours'), formatter: function (p) { return p.value.toFixed(3); } } },
        { name: '理论线 1 − 泄露占比', type: 'line', data: th, symbol: 'none', lineStyle: { width: 2, type: 'dashed', color: em('base') },
          itemStyle: { color: em('base') } }
      ]
    });
  }

  /* ---------- S11：T8 业务连续性（开/关 × 双轨 分组柱） ---------- */
  function cS11() {
    var rows = D.t8.rows;
    return base({
      xAxis: { type: 'category', data: rows.map(function (r) { return r.svc; }),
               axisLine: { lineStyle: { color: C.line } }, axisLabel: { color: C.muted, fontSize: 12 } },
      yAxis: { type: 'value', min: 0.75, max: 1.02, name: '连续性满足率', nameTextStyle: { color: C.muted },
               splitLine: { lineStyle: { color: '#eef2f7' } }, axisLabel: { color: C.muted, formatter: function (v) { return v.toFixed(2); } } },
      legend: { top: 0, textStyle: { fontSize: 11.5 }, data: ['本方案·T8 开（Python）', '本方案·T8 开（ns-3）', 'T8 关（Python）', 'T8 关（ns-3）'] },
      grid: { left: 62, right: 22, top: 44, bottom: 34 },
      series: [
        { name: '本方案·T8 开（Python）', type: 'bar', data: rows.map(function (r) { return r.py_on; }),
          itemStyle: { color: em('ours') }, barGap: '12%', label: { show: true, position: 'top', fontSize: 10.5, color: em('ours') } },
        { name: '本方案·T8 开（ns-3）', type: 'bar', data: rows.map(function (r) { return r.ns3_on; }),
          itemStyle: { color: C.ns3 }, label: { show: true, position: 'top', fontSize: 10.5, color: C.ns3 } },
        { name: 'T8 关（Python）', type: 'bar', data: rows.map(function (r) { return r.py_off; }),
          itemStyle: { color: em('base') }, label: { show: true, position: 'top', fontSize: 10, color: em('base') } },
        { name: 'T8 关（ns-3）', type: 'bar', data: rows.map(function (r) { return r.ns3_off; }),
          itemStyle: { color: '#b9cbd8' }, label: { show: true, position: 'top', fontSize: 10, color: '#6f8496' } }
      ]
    });
  }

  /* ---------- S15：敏感性三组曲线 ---------- */
  function sensChart(key) {
    return function () {
      var blk = D.sensitivity.groups[key], pts = blk.points;
      var isInt = blk.meta.metric === '切换中断均值_ms';
      var data = pts.map(function (p) { return isInt ? +p.interrupt_ms : +p.block_rate; });
      return base({
        xAxis: { type: 'category', data: pts.map(function (p) { return p.x + (blk.meta.unit ? ' ' + blk.meta.unit : ''); }),
                 axisLine: { lineStyle: { color: C.line } }, axisLabel: { color: C.muted, fontSize: 11 } },
        yAxis: { type: isInt ? 'log' : 'value', name: isInt ? '切换中断均值 (ms，对数轴)' : '拦截率',
                 nameTextStyle: { color: C.muted, fontSize: 11 },
                 splitLine: { lineStyle: { color: '#eef2f7' } }, axisLabel: { color: C.muted },
                 logBase: 10, min: isInt ? 0.005 : 0.4, max: isInt ? 20000 : 1.05 },
        tooltip: { trigger: 'axis', backgroundColor: '#fff', borderColor: C.line,
                   textStyle: { color: C.ink, fontSize: 12 },
                   formatter: function (ps) { return ps[0].axisValue + '<br/>' + blk.meta.metric + ' = <b>' + ps[0].value + '</b>'; } },
        series: [{
          type: 'line', data: data, symbolSize: 8, lineStyle: { width: 3, color: C.ours }, itemStyle: { color: C.ours },
          label: { show: true, fontSize: 10.5, color: C.ours, formatter: function (p) { return isInt ? p.value : (+p.value).toFixed(3); } },
          areaStyle: { color: 'rgba(192,57,43,.07)' }
        }]
      });
    };
  }

  /* ---------- S16：10 种子 95% CI ---------- */
  function cS16() {
    var cfgs = ['Rel17四步基线', '核心方案_无优先', '全方案_含生存优先'];
    var vals = cfgs.map(function (k) { return D.multiseed.configs[k].values['切换总时延均值_ms']; });
    var sds = cfgs.map(function (k) { return D.multiseed.configs[k].values['切换总时延均值_ms_stdev']; });
    var lab = cfgs.map(function (k) { return D.multiseed.configs[k].label; });
    return base({
      xAxis: { type: 'category', data: lab, axisLine: { lineStyle: { color: C.line } },
               axisLabel: { color: C.muted, fontSize: 11, interval: 0 } },
      yAxis: { type: 'value', name: '切换总时延均值 (ms)', nameTextStyle: { color: C.muted, fontSize: 11 },
               splitLine: { lineStyle: { color: '#eef2f7' } }, axisLabel: { color: C.muted } },
      tooltip: { trigger: 'axis', backgroundColor: '#fff', borderColor: C.line,
                 textStyle: { color: C.ink, fontSize: 12 },
                 formatter: function (ps) {
                   var i = ps[0].dataIndex;
                   return lab[i] + '<br/>切换总时延均值 = <b>' + vals[i].toFixed(3) + ' ms</b>' +
                          '<br/>种子间标准差 σ = ' + sds[i] + ' ms（相对 ' +
                          (sds[i] / vals[i] * 100).toFixed(3) + '%）';
                 } },
      series: [
        { name: '切换总时延均值', type: 'bar', barWidth: 54,
          data: vals.map(function (v, i) { return { value: +v.toFixed(3), itemStyle: { color: em(i === 0 ? 'base' : 'ours') } }; }),
          label: { show: true, position: 'top', fontSize: 11.5, fontWeight: 'bold',
                   formatter: function (p) { return p.value.toFixed(2) + ' ms\n±' + sds[p.dataIndex] + ' σ'; } } }
      ]
    });
  }

  /* ---------- S4：两步 vs 四步 定量对比 ---------- */
  function cS4() {
    var m = D.two_vs_four.rows;
    var lat = m.filter(function (r) { return r.key === 'latency'; })[0];
    var su = m.filter(function (r) { return r.key === 'success'; })[0];
    var thr = m.filter(function (r) { return r.key === 'thr'; })[0];
    return base({
      legend: { top: 0, textStyle: { fontSize: 11.5 } },
      grid: { left: 66, right: 26, top: 40, bottom: 40 },
      xAxis: { type: 'category', data: ['两步 RACH（本方案）', 'Rel-17 四步 RACH'],
               axisLine: { lineStyle: { color: C.line } }, axisLabel: { color: C.muted } },
      yAxis: [
        { type: 'value', name: '接入时延均值 (ms)', nameTextStyle: { color: C.muted, fontSize: 11 },
          splitLine: { lineStyle: { color: '#eef2f7' } }, axisLabel: { color: C.muted } },
        { type: 'value', name: '成功率 / 吞吐', min: 0, max: 1, nameTextStyle: { color: C.muted, fontSize: 11 },
          splitLine: { show: false }, axisLabel: { color: C.muted } }
      ],
      series: [
        { name: '接入时延均值 (ms)', type: 'bar', barWidth: 60, yAxisIndex: 0,
          data: [lat.two, lat.four].map(function (v, i) { return { value: v, itemStyle: Object.assign({ color: em(i ? 'base' : 'ours') }, bd(i ? 'base' : 'ours')) }; }),
          label: { show: true, position: 'top', fontWeight: 'bold', fontSize: 12, formatter: '{c} ms' } },
        { name: '接入成功率', type: 'bar', yAxisIndex: 1, barWidth: 30, barGap: '-55%',
          data: [su.two, su.four], itemStyle: { color: '#e0a458' },
          label: { show: true, position: 'insideMiddle', fontSize: 11, rotate: 0, color: '#fff',
                   formatter: function (p) { return (+p.value).toFixed(4); } } }
      ]
    });
  }

  /* ---------- S18：突发强度抗冲击 ---------- */
  function cS18() {
    var rows = D.burst.rows;
    var cats = rows.map(function (r) { return r.label; });
    return base({
      legend: { top: 0, textStyle: { fontSize: 11.5 } },
      grid: { left: 62, right: 26, top: 40, bottom: 40 },
      xAxis: { type: 'category', data: cats, axisLine: { lineStyle: { color: C.line } }, axisLabel: { color: C.muted } },
      yAxis: [
        { type: 'value', name: '接入时延均值 (ms)', nameTextStyle: { color: C.muted, fontSize: 11 },
          splitLine: { lineStyle: { color: '#eef2f7' } }, axisLabel: { color: C.muted } },
        { type: 'value', name: '接入成功率', min: 0.4, max: 1.06, nameTextStyle: { color: C.muted, fontSize: 11 },
          splitLine: { show: false }, axisLabel: { color: C.muted } }
      ],
      series: [
        { name: '接入时延均值 (ms)', type: 'bar', barWidth: 54, yAxisIndex: 0,
          data: rows.map(function (r, i) { return { value: r.py.latency, itemStyle: { color: em(i ? 'ours' : 'base') } }; }),
          label: { show: true, position: 'top', fontWeight: 'bold', fontSize: 12, formatter: '{c} ms' } },
        { name: '接入成功率（Python）', type: 'line', yAxisIndex: 1, data: rows.map(function (r) { return r.py.success; }),
          symbolSize: 10, lineStyle: { width: 2.5, color: C.ok }, itemStyle: { color: C.ok },
          label: { show: true, position: 'bottom', fontSize: 11, color: C.ok, formatter: function (p) { return (+p.value).toFixed(4); } } },
        { name: '接入成功率（ns-3）', type: 'line', yAxisIndex: 1, data: rows.map(function (r) { return r.ns3.success; }),
          symbolSize: 10, lineStyle: { width: 2.5, type: 'dashed', color: C.ns3 }, itemStyle: { color: C.ns3 },
          label: { show: true, position: 'top', fontSize: 11, color: C.ns3, formatter: function (p) { return (+p.value).toFixed(4); } } }
      ]
    });
  }

  window.DEMO_CHARTS = {
    C: C, register: register, onShow: onShow, resizeAll: resizeAll,
    get: function (dom) { return made[dom] || null; },
    cS4: cS4, cS6: cS6, cS11: cS11, cS15: sensChart, cS16: cS16, cS18: cS18
  };
  // 对比开关 → 同步所有图表配色侧重（重新 setOption，不重算数据）
  if (window.DEMO && window.DEMO.onMode) {
    window.DEMO.onMode(function (m) { mode = m; onShow(window.DEMO.state.screen); });
  }
})();
