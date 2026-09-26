/* ============================================================================
   anim.js —— 确定性时间驱动动画引擎（零库）
   核心思想：每张动画 = 归一化时间 u∈[0,1] 的**纯函数** frame(u)。
   由此免费获得：可播放 / 可暂停 / 可拖动 / 可逐帧定位（截图自检可精确复现同一帧）。
   ★ 铁律：动画只改变「视觉呈现」（位置/透明度/高亮/进度条），
     绝不改变任何数字 —— 所有数值由 data.js 在 build() 阶段写入 DOM 并且始终存在。
   ========================================================================== */
(function () {
  'use strict';
  var R = {};                 // id → record
  var order = [];
  var raf = null, last = 0;

  function easeInOut(u) { return u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2; }
  function easeOut(u) { return 1 - Math.pow(1 - u, 3); }
  function lerp(a, b, u) { return a + (b - a) * u; }
  function clamp01(u) { return u < 0 ? 0 : (u > 1 ? 1 : u); }
  /** 把全局进度 u 映射到「第 i 段、共 n 段」内的局部进度（0..1）；seg() 便于编排顺序动画 */
  function seg(u, i, n, overlap) {
    var o = overlap === undefined ? 0 : overlap;
    var span = (1 + o * (n - 1)) / n;
    var s = u * (1 + o * (n - 1)) - i * span;
    return clamp01(s / span);
  }
  function fmtU(u, dur) { return (u * dur).toFixed(1) + ' / ' + dur.toFixed(0) + ' ms'; }

  /* ---------------- 注册 ---------------- */
  function reg(id, spec) {
    R[id] = { id: id, spec: spec, u: 0, playing: false, done: false, ctx: null, host: null };
    order.push(id);
    return R[id];
  }

  function hostOf(id) { return document.querySelector('.anim[data-anim="' + id + '"]'); }

  function mount(id) {
    var r = R[id]; if (!r) return;
    var host = hostOf(id); if (!host) return;
    if (!r.ctx) { r.host = host; r.ctx = (r.spec.build && r.spec.build(host)) || {}; }
    bar(id);
    draw(id);
  }

  /** 标准控制条：播放/暂停 + 重播 + 进度条 + 阶段读数 */
  function bar(id) {
    var r = R[id];
    var el = document.querySelector('.abar[data-bar="' + id + '"]');
    if (!el || el.dataset.wired) return;
    el.dataset.wired = '1';
    el.innerHTML =
      '<button type="button" class="ap" data-act="toggle">' + (r.playing ? '⏸ 暂停' : '▶ 播放') + '</button>' +
      '<button type="button" data-act="replay">↺ 重播</button>' +
      '<input type="range" min="0" max="1000" value="0" class="aslider"/>' +
      '<span class="aread"></span>' +
      '<span class="anote">' + (r.spec.note || '拖动进度条可定格任意时刻') + '</span>';
    el.querySelector('[data-act="toggle"]').onclick = function () { toggle(id); };
    el.querySelector('[data-act="replay"]').onclick = function () { seek(id, 0); play(id); };
    el.querySelector('.aslider').oninput = function () { pause(id); seek(id, this.value / 1000); };
  }

  function draw(id) {
    var r = R[id]; if (!r) return;
    var host = r.host || hostOf(id);
    if (!host || !r.ctx) return;
    try { r.spec.frame(clamp01(r.u), r.ctx, host); } catch (e) { console.error('[anim ' + id + '] ' + e.message); }
    var el = document.querySelector('.abar[data-bar="' + id + '"]');
    if (el) {
      var sl = el.querySelector('.aslider'); if (sl && document.activeElement !== sl) sl.value = Math.round(r.u * 1000);
      var rd = el.querySelector('.aread');
      if (rd) rd.innerHTML = r.spec.readout ? r.spec.readout(r.u, r.ctx) : fmtU(r.u, r.spec.dur || 1000);
    }
  }

  /* ---------------- 控制 ---------------- */
  function play(id) {
    var r = R[id]; if (!r) return;
    if (r.spec.loop) { r.playing = true; }
    else if (r.u >= 1) { r.u = 0; r.playing = true; }
    else r.playing = true;
    paintBtn(id);
  }
  function pause(id) { var r = R[id]; if (r) { r.playing = false; paintBtn(id); } }
  function toggle(id) { var r = R[id]; if (!r) return; r.playing ? pause(id) : play(id); }
  function seek(id, u) { var r = R[id]; if (!r) return; r.u = clamp01(u); r.playing = false; paintBtn(id); draw(id); }
  function paintBtn(id) {
    var el = document.querySelector('.abar[data-bar="' + id + '"] [data-act="toggle"]');
    if (el) el.textContent = R[id].playing ? '⏸ 暂停' : (R[id].u >= 1 ? '▶ 重播' : '▶ 播放');
  }
  /** 只把「当前可见屏」的动画推到终点 —— 自检截图用，保证画面是完整终态 */
  function finishVisible() {
    order.forEach(function (id) {
      var r = R[id]; if (!r || !r.host) return;
      if (r.host.offsetParent !== null) { r.playing = false; r.u = 1; paintBtn(id); draw(id); }
    });
  }
  function mountScreen(ids) { (ids || []).forEach(function (id) { if (R[id]) { r0(id); mount(id); } }); }
  function r0(id) { R[id].u = 0; R[id].playing = false; }

  /* ---------------- 主循环 ---------------- */
  function loop(ts) {
    raf = requestAnimationFrame(loop);
    var now = ts || 0, dt = last ? Math.min(120, now - last) : 0;
    last = now;
    order.forEach(function (id) {
      var r = R[id]; if (!r || !r.playing) return;
      if (r.host && r.host.offsetParent === null) return;   // 不可见则不计时（省算力）
      r.u += dt / (r.spec.dur || 1000);
      if (r.u >= 1) {
        if (r.spec.loop) { r.u -= 1; } else { r.u = 1; r.playing = false; paintBtn(id); }
      }
      draw(id);
    });
  }
  function start() { if (!raf) raf = requestAnimationFrame(loop); }

  window.ANIM = {
    reg: reg, mount: mount, mountScreen: mountScreen, play: play, pause: pause, toggle: toggle,
    seek: seek, finishVisible: finishVisible, start: start,
    easeInOut: easeInOut, easeOut: easeOut, lerp: lerp, seg: seg, clamp01: clamp01
  };
})();
