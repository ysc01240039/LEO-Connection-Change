/* ============================================================================
   core.js —— 页面基础设施（数据图元 / 溯源抽屉 / 切屏 / 对比开关 / 通用图元）
   铁律：页面只读 window.DEMO_DATA，绝不硬编码数字。
        data-metric + data-value 由同一个值生成 → 显示与断言不可能漂移；
        data-golden 为**人工从《双轨交叉验证对照表》誊写的独立副本**，由自检脚本
        交叉比对（两条独立路径互证），防止「键映射错位」这类自洽型错误。
   ========================================================================== */
(function () {
  'use strict';
  var D = window.DEMO_DATA;
  if (!D) { document.body.innerHTML = '<pre style="color:#c0392b">data.js 未加载</pre>'; return; }

  var state = { screen: 'S1', mode: 'ours' };   // mode: ours | base
  var listeners = { screen: [], mode: [] };

  /* ---------------- 数值 ---------------- */
  function V(key) {
    if (!(key in D.metrics)) {
      if (window.console) console.error('[demo] 指标键不存在: ' + key);
      return NaN;
    }
    return D.metrics[key];
  }
  function fmt(v, d) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    if (typeof d === 'number') return v.toFixed(d);
    var s = String(v);
    return s;
  }
  /**
   * 数据图元：<span class="num" data-metric data-value data-golden data-runid>显示值</span>
   * @param key 指标键
   * @param o   {d:小数位, unit:单位, pre:前缀, post:后缀, cls:样式类, g:人工誊写值}
   */
  function n(key, o) {
    o = o || {};
    var v = V(key);
    var txt = fmt(v, o.d);
    if (isNaN(v)) txt = '—';
    var attr = ' class="num ' + (o.cls || '') + '" data-metric="' + key + '" data-value="' + v +
               '" data-runid="' + (D.runs[key] || '') + '"';
    if (o.g !== undefined) attr += ' data-golden="' + o.g + '"';
    if (o.id) attr += ' id="' + o.id + '"';
    var body = (o.pre || '') + txt + (o.unit ? '<span class="unit">' + o.unit + '</span>' : '') + (o.post || '');
    return '<span' + attr + '>' + body + '</span>';
  }
  /* ---------------- 徽标 ---------------- */
  function runBadge(key) {
    var r = D.runs[key] || '';
    return r ? '<span class="bd run" title="数据来源 run">' + r + '</span>' : '';
  }

  /* ---------------- 溯源抽屉 ---------------- */
  function openDrawer(key) {
    var d = document.getElementById('drawer');
    if (!d) return;
    var s = D.source[key] || {};
    var html = '<span class="close" onclick="DEMO.closeDrawer()">✕</span>' +
      '<h3>数据溯源</h3><div class="note">点击任意带虚线下划线的数字即可展开其出处。</div>' +
      '<div class="k">指标键</div><div class="v">' + key + '</div>' +
      '<div class="k">数值</div><div class="v">' + V(key) + '</div>' +
      '<div class="k">run_id</div><div class="v">' + (D.runs[key] || '—') + '</div>' +
      '<div class="k">权威文件 / 章节</div><div class="v">' + (s.file || '—') + '　' + (s.section || '') + '</div>' +
      (s.note ? '<div class="k">说明</div><div class="v">' + s.note + '</div>' : '') +
      '<div class="k">复现命令</div><div class="v">' + (D.evidence.filter(function (e) {
        return e.run === D.runs[key];
      }).map(function (e) { return e.how; })[0] || '见 S20 证据索引') + '</div>' +
      '<div class="k">口径边界</div><div class="v">接入时延仅统计成功终端；伪造拦截率分母 = 进入认证环节的伪造终端</div>';
    d.innerHTML = html;
    d.classList.add('on');
  }
  function closeDrawer() {
    var d = document.getElementById('drawer');
    if (d) d.classList.remove('on');
  }

  /* ---------------- 切屏 ---------------- */
  function show(id) {
    state.screen = id;
    var secs = document.querySelectorAll('.screen');
    for (var i = 0; i < secs.length; i++) {
      secs[i].classList.toggle('on', secs[i].getAttribute('data-screen') === id);
    }
    var navs = document.querySelectorAll('[data-goto]');
    for (var j = 0; j < navs.length; j++) {
      navs[j].classList.toggle('on', navs[j].getAttribute('data-goto') === id);
    }
    window.scrollTo(0, 0);
    listeners.screen.forEach(function (f) { try { f(id); } catch (e) { console.error(e); } });
  }
  function onScreen(f) { listeners.screen.push(f); }
  function onMode(f) { listeners.mode.push(f); }

  /* ---------------- 对比开关 ---------------- */
  function setMode(m) {
    state.mode = m;
    var bs = document.querySelectorAll('.switch button');
    for (var i = 0; i < bs.length; i++) bs[i].classList.toggle('on', bs[i].getAttribute('data-mode') === m);
    document.body.setAttribute('data-mode', m);
    var bn = document.getElementById('mode-banner');
    if (bn) bn.style.display = (m === 'base') ? 'block' : 'none';
    listeners.mode.forEach(function (f) { try { f(m); } catch (e) { console.error(e); } });
  }

  /* ---------------- 通用图元 ---------------- */
  /** 横向条形对比：items=[{label, v, cls, text, w}]，wmax 归一化上限 */
  function bars(items, wmax) {
    var mx = wmax || Math.max.apply(null, items.map(function (i) { return i.w === undefined ? i.v : i.w; })) || 1;
    return '<div class="bars">' + items.map(function (i) {
      var w = Math.max(0.6, ((i.w === undefined ? i.v : i.w) / mx) * 100);
      return '<div class="brow"><div class="bl">' + i.label + '</div>' +
        '<div class="bt"><div class="fill ' + (i.cls || 'ours') + '" style="width:' + w.toFixed(2) + '%"></div></div>' +
        '<div class="bv">' + i.text + '</div></div>';
    }).join('') + '</div>';
  }
  /** 表格：cols=[...], rows=[[html,...]]；外层带横向滚动，避免窄卡片裁掉末列 */
  function table(cols, rows, o) {
    o = o || {};
    return '<div class="tb-wrap">' +
      '<table class="tb">' +
      '<thead><tr>' + cols.map(function (c) { return '<th>' + c + '</th>'; }).join('') + '</tr></thead>' +
      '<tbody>' + rows.map(function (r) {
        return '<tr' + (r.__cls ? ' class="' + r.__cls + '"' : '') + '>' +
          r.map(function (c, i) { return '<td' + (i === 0 && o.firstLeft === false ? ' style="text-align:right"' : '') + '>' + c + '</td>'; }).join('') +
          '</tr>';
      }).join('') + '</tbody></table></div>';
  }
  function card(title, badges, body, o) {
    o = o || {};
    return '<div class="card"' + (o.id ? ' id="' + o.id + '"' : '') + '>' +
      '<div class="card-hd"><h3>' + title + '</h3><div class="spacer"></div>' + (badges || '') + '</div>' +
      '<div class="card-bd">' + body + '</div></div>';
  }
  /** 面板：仅一条徽标行 + 主体（动画主视觉用，避免标题堆叠吃掉画面） */
  function panel(body, badges, o) {
    o = o || {};
    return '<div class="card' + (o.cls ? ' ' + o.cls : '') + '">' +
      '<div class="card-hd"><div class="spacer"></div>' + (badges || '') + '</div>' +
      '<div class="card-bd">' + body + '</div></div>';
  }
  /** 指标卡：标签 + 数值 + 一行注（紧凑，用于 3~4 列排布） */
  function tile(lab, valHtml, hintHtml, key, cls) {
    return '<div class="kpi' + (cls ? ' ' + cls : '') + '"' +
      (key ? ' data-reveal="' + key + '"' : '') + '>' +
      '<div class="lab">' + lab + '</div>' + valHtml +
      (hintHtml ? '<div class="hint">' + hintHtml + '</div>' : '') + '</div>';
  }
  /** 折叠块（附录用） */
  function fold(title, badges, body, open) {
    return '<details class="fold"' + (open ? ' open' : '') + '><summary>' + title +
      (badges ? '<span class="fbd">' + badges + '</span>' : '') + '</summary>' +
      '<div class="fold-bd">' + body + '</div></details>';
  }
  function deltaHTML(v, o) {
    o = o || {};
    var cls = Math.abs(v) < (o.eps || 0.05) ? 'flat' : (v > 0 ? 'up' : 'down');
    var sign = v > 0 ? '+' : '';
    return '<span class="delta ' + cls + '">' + sign + v.toFixed(o.d === undefined ? 1 : o.d) + '%</span>';
  }

  /* ---------------- 生命周期 ---------------- */
  function boot() {
    document.addEventListener('click', function (e) {
      var t = e.target;
      while (t && t !== document.body) {
        var g = t.getAttribute && t.getAttribute('data-goto');
        if (g) { show(g); return; }
        var tb = t.getAttribute && t.getAttribute('data-tab');
        if (tb) {
          var box = t.closest('.tabbox');
          if (box) {
            [].forEach.call(box.querySelectorAll('[data-tab]'), function (b) {
              b.classList.toggle('on', b === t);
            });
            [].forEach.call(box.querySelectorAll('[data-pane]'), function (p) {
              p.classList.toggle('on', p.getAttribute('data-pane') === tb);
            });
          }
          return;
        }
        if (t.getAttribute && t.getAttribute('data-metric')) { openDrawer(t.getAttribute('data-metric')); return; }
        if (t.classList && t.classList.contains('pickable')) { openDrawer(t.getAttribute('data-key')); return; }
        t = t.parentElement;
      }
    });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeDrawer(); });
    setMode('ours');
  }

  window.DEMO = {
    D: D, state: state, V: V, n: n,
    bars: bars, table: table, card: card, panel: panel, tile: tile, fold: fold,
    runBadge: runBadge, deltaHTML: deltaHTML,
    show: show, setMode: setMode, onScreen: onScreen, onMode: onMode,
    openDrawer: openDrawer, closeDrawer: closeDrawer, boot: boot
  };
})();
