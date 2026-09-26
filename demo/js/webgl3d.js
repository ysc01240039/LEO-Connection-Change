/* ============================================================================
   webgl3d.js —— 原生 WebGL 手写「651 星 · 地球 · 轨道弧 · 仰角掩蔽」几何屏
   零第三方 3D 库（Three.js 备而不依）；数据来自 DEMO_DATA.constellation
   （651 颗真实 OneWeb TLE，skyfield SGP4 传播，12 帧 ECI 坐标，与仿真同源）。
   能力不足时（无 WebGL / 编译失败）自动降级提示，不抛错、不白屏。
   ========================================================================== */
(function () {
  'use strict';
  var D = window.DEMO_DATA;
  var R_EARTH = 6371.0, MASK_DEG = 25.0, SPAN = 3600.0;
  var SITE = { lat: 31.0083, lon: 103.5833, name: '汶川震中' };
  var st = { t: 0, play: true, yaw: -2.97, pitch: -0.34, drag: false, lx: 0, ly: 0, inited: false };
  var gl, progTex, progFlat, raf = null, meshes = {}, canvas, hud, deg, hudStep = -1;

  /* ------------------------------- mat4 ------------------------------- */
  function m4id() { return new Float32Array([1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1]); }
  function m4mul(a, b) {
    var o = new Float32Array(16);
    for (var i = 0; i < 4; i++) for (var j = 0; j < 4; j++) {
      var s = 0; for (var k = 0; k < 4; k++) s += a[k * 4 + j] * b[i * 4 + k];
      o[i * 4 + j] = s;
    }
    return o;
  }
  function m4persp(fovy, asp, n, f) {
    var t = 1 / Math.tan(fovy / 2), o = new Float32Array(16);
    o[0] = t / asp; o[5] = t; o[10] = (f + n) / (n - f); o[11] = -1; o[14] = 2 * f * n / (n - f);
    return o;
  }
  function m4rotZ(a) {
    var c = Math.cos(a), s = Math.sin(a);
    return new Float32Array([c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
  }
  function m4lookAt(e, c, u) {
    var z = norm(sub(e, c)), x = norm(cross(u, z)), y = cross(z, x), o = new Float32Array(16);
    o[0]=x[0]; o[4]=x[1]; o[8]=x[2]; o[12]=-dot(x,e);
    o[1]=y[0]; o[5]=y[1]; o[9]=y[2]; o[13]=-dot(y,e);
    o[2]=z[0]; o[6]=z[1]; o[10]=z[2]; o[14]=-dot(z,e);
    o[15]=1; return o;
  }
  function sub(a,b){return [a[0]-b[0],a[1]-b[1],a[2]-b[2]];}
  function dot(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2];}
  function cross(a,b){return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];}
  function norm(a){var l=Math.sqrt(dot(a,a))||1;return [a[0]/l,a[1]/l,a[2]/l];}

  /* ------------------------------- shaders ------------------------------- */
  var VS_TEX =
    'attribute vec3 aPos;attribute vec3 aNrm;attribute vec2 aUV;' +
    'uniform mat4 uMVP;uniform mat4 uMV;uniform float uTheta;' +
    'varying vec2 vUV;varying vec3 vN;' +
    'void main(){vUV=vec2(aUV.x+0.5+uTheta,aUV.y);vN=mat3(uMV)*aNrm;gl_Position=uMVP*vec4(aPos,1.0);}';
  var FS_TEX =
    'precision mediump float;uniform sampler2D uTex;uniform float uUseTex;uniform vec3 uLight;' +
    'varying vec2 vUV;varying vec3 vN;' +
    'void main(){vec3 n=normalize(vN);float d=max(0.0,dot(n,normalize(uLight)));' +
    'vec3 base;if(uUseTex>0.5){base=texture2D(uTex,vec2(fract(vUV.x),vUV.y)).rgb;}' +
    'else{base=vec3(0.09,0.20,0.34);}' +
    'vec3 c=base*(0.34+0.86*d)+vec3(0.05,0.11,0.21)*pow(1.0-d,3.0);' +
    'gl_FragColor=vec4(c,1.0);}';
  var VS_FLAT = 'attribute vec3 aPos;uniform mat4 uMVP;uniform float uSize;void main(){gl_Position=uMVP*vec4(aPos,1.0);gl_PointSize=uSize;}';
  var FS_FLAT =
    'precision mediump float;uniform vec3 uColor;uniform float uIsPoint;' +
    'void main(){if(uIsPoint>0.5){vec2 d=gl_PointCoord-vec2(0.5);if(length(d)>0.5)discard;}' +
    'gl_FragColor=vec4(uColor,1.0);}';

  function sh(type, src) {
    var s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  function prog(vs, fs, attrs, unis) {
    var p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    var o = { p: p, a: {}, u: {} };
    attrs.forEach(function (a) { o.a[a] = gl.getAttribLocation(p, a); });
    unis.forEach(function (u) { o.u[u] = gl.getUniformLocation(p, u); });
    return o;
  }
  function buf(arr, n) {
    var b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(arr), gl.STATIC_DRAW);
    return { b: b, n: n };
  }
  function bind(pr, name, m, size) {
    if (pr.a[name] === undefined || pr.a[name] < 0) return;
    gl.bindBuffer(gl.ARRAY_BUFFER, m.b);
    gl.enableVertexAttribArray(pr.a[name]);
    gl.vertexAttribPointer(pr.a[name], size, gl.FLOAT, false, 0, 0);
  }

  /* ------------------------------- 网格 ------------------------------- */
  function sphereMesh(seg) {
    var pos = [], nrm = [], uv = [], idx = [];
    for (var i = 0; i <= seg; i++) for (var j = 0; j <= seg; j++) {
      var v = j / seg * Math.PI, u = i / seg * 2 * Math.PI;
      var x = Math.sin(v) * Math.cos(u), y = Math.sin(v) * Math.sin(u), z = Math.cos(v);
      pos.push(x, y, z); nrm.push(x, y, z); uv.push(i / seg, 1 - j / seg);
    }
    for (var a = 0; a < seg; a++) for (var b = 0; b < seg; b++) {
      var p0 = a * (seg + 1) + b, p1 = p0 + 1, p2 = p0 + seg + 1, p3 = p2 + 1;
      idx.push(p0, p2, p1, p1, p2, p3);
    }
    return { pos: pos, nrm: nrm, uv: uv, idx: idx };
  }
  function graticule() {
    var segs = [], r = 1.002, i, a;
    for (var lat = -60; lat <= 60; lat += 30) {
      var la = lat * Math.PI / 180;
      for (i = 0; i < 180; i++) {
        var l0 = i / 180 * 2 * Math.PI, l1 = (i + 1) / 180 * 2 * Math.PI;
        segs.push(r*Math.cos(la)*Math.cos(l0), r*Math.cos(la)*Math.sin(l0), r*Math.sin(la),
                  r*Math.cos(la)*Math.cos(l1), r*Math.cos(la)*Math.sin(l1), r*Math.sin(la));
      }
    }
    for (var lon = 0; lon < 360; lon += 30) {
      var lo = lon * Math.PI / 180;
      for (i = 0; i < 90; i++) {
        var p0 = -Math.PI / 2 + i / 90 * Math.PI, p1 = -Math.PI / 2 + (i + 1) / 90 * Math.PI;
        segs.push(r*Math.cos(p0)*Math.cos(lo), r*Math.cos(p0)*Math.sin(lo), r*Math.sin(p0),
                  r*Math.cos(p1)*Math.cos(lo), r*Math.cos(p1)*Math.sin(lo), r*Math.sin(p1));
      }
    }
    return segs;
  }
  function eqRing() {
    var s = [], r = 1.004;
    for (var i = 0; i < 240; i++) {
      var a0 = i / 240 * 2 * Math.PI, a1 = (i + 1) / 240 * 2 * Math.PI;
      s.push(r*Math.cos(a0), r*Math.sin(a0), 0, r*Math.cos(a1), r*Math.sin(a1), 0);
    }
    return s;
  }
  function axisCross(s) {
    var r = 1 + s / R_EARTH;
    return [
      [r*1.012,0,0],[r*0.995,0,0], [0,r*1.012,0],[0,r*0.995,0], [0,0,r*1.012],[0,0,r*0.995]
    ];
  }

  /* --------------------------- 运行时坐标 --------------------------- */
  function framesCount() { return D.constellation.frames || 12; }
  function satXYZ(t) {
    var F = framesCount(), fr = t / SPAN * (F - 1);
    var i0 = Math.min(F - 1, Math.floor(fr)), i1 = Math.min(F - 1, i0 + 1), w = fr - i0;
    var A = D.constellation.pos[i0], B = D.constellation.pos[i1], out = new Float32Array(D.constellation.n * 3);
    for (var s = 0; s < D.constellation.n; s++) {
      var k = s * 3;
      out[k] = (A[k] + (B[k] - A[k]) * w) / R_EARTH;
      out[k+1] = (A[k+1] + (B[k+1] - A[k+1]) * w) / R_EARTH;
      out[k+2] = (A[k+2] + (B[k+2] - A[k+2]) * w) / R_EARTH;
    }
    return out;
  }
  function thetaTurns(t) { return -t / 86164.0; }   // 贴图 U 偏移（圈）：顶点 u 需采样 epoch 经度 u−ωt 的地图内容
  function siteXYZ(t) {
    /* ECI 下地球东向自转：站点的惯性经度角 = 地理经度 + ω⊕t（与卫星星历同参考系） */
    var la = SITE.lat * Math.PI / 180, lo = SITE.lon * Math.PI / 180 + (t / 86164.0) * 2 * Math.PI;
    return [Math.cos(la)*Math.cos(lo), Math.cos(la)*Math.sin(lo), Math.sin(la)];
  }
  function bestSat(sat, site) {
    var up = norm(site), best = { el: -90, i: -1, p: null };
    for (var i = 0; i < sat.length / 3; i++) {
      var p = [sat[i*3], sat[i*3+1], sat[i*3+2]];
      var v = norm(sub(p, site));
      var el = Math.asin(Math.max(-1, Math.min(1, dot(v, up)))) * 180 / Math.PI;
      if (el > best.el) best = { el: el, i: i, p: p };
    }
    return best;
  }

  /* ------------------------------- 初始化 ------------------------------- */
  function init() {
    canvas = document.getElementById('gl');
    if (!canvas) return false;
    hud = document.getElementById('gl-hud');
    deg = document.getElementById('gl-deg');
    try {
      /* preserveDrawingBuffer: true —— 使自检脚本可对画面做像素回读（也支持右键另存当前帧）。
         代价仅为一次额外拷贝，对演示性能无影响。 */
      var opt = { antialias: true, depth: true, preserveDrawingBuffer: true };
      gl = canvas.getContext('webgl', opt) || canvas.getContext('experimental-webgl', opt);
    } catch (e) { gl = null; }
    if (!gl) { degrade('本机 Chrome 未启用 WebGL'); return false; }

    progTex = prog(VS_TEX, FS_TEX, ['aPos', 'aNrm', 'aUV'], ['uMVP', 'uMV', 'uTheta', 'uTex', 'uUseTex', 'uLight']);
    progFlat = prog(VS_FLAT, FS_FLAT, ['aPos'], ['uMVP', 'uColor', 'uSize', 'uIsPoint']);

    var sm = sphereMesh(72);
    meshes.sphere = {
      pos: buf(sm.pos, sm.pos.length / 3), nrm: buf(sm.nrm, sm.nrm.length / 3),
      uv: buf(sm.uv, sm.uv.length / 2), idx: gl.createBuffer()
    };
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, meshes.sphere.idx);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(sm.idx), gl.STATIC_DRAW);
    var gratSegs = graticule(), eqSegs = eqRing();
    meshes.grat = buf(gratSegs, gratSegs.length / 3);
    meshes.eq = buf(eqSegs, eqSegs.length / 3);
    meshes.axes = buf([].concat.apply([], axisCross(0.10)), 6);
    meshes.sat = buf(new Float32Array(D.constellation.n * 3), D.constellation.n);
    meshes.orbits = D.constellation.orbits.map(function (o) { return buf(o, o.length / 3); });
    meshes.link = buf(new Float32Array([0,0,0, 0,0,0]), 2);
    meshes.site = buf(new Float32Array(12), 6);   // 2 万字符号占位（每帧更新）

    meshes.texOK = false;
    if (window.DEMO_EARTH && D.constellation.texture && D.constellation.texture.data_uri_bytes) {
      var img = new Image();
      img.onload = function () {
        try {
          var tx = gl.createTexture();
          gl.bindTexture(gl.TEXTURE_2D, tx);
          gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, 1);
          gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, img);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
          gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
          meshes.tex = tx; meshes.texOK = true;
        } catch (e) { console.warn('[3d] 贴图上传失败，改用程序化着色：' + e.message); }
      };
      img.src = window.DEMO_EARTH;
    }
    bindEvents();
    st.inited = true;
    loop();
    return true;
  }

  function degrade(msg) {
    var w = document.getElementById('gl-wrap');
    if (w) w.innerHTML = '<div style="padding:60px 20px;color:#9fb3c8;text-align:center;font-size:13px">' +
      '⚠ 3D 视图降级：' + msg + '<br/><span style="font-size:12px">星座几何的等价数值证据见 S13 双轨互证 / S20 证据索引（' +
      D.constellation.n + ' 颗卫星 · 12 帧 · |r| ' + D.constellation.r_km.join('–') + ' km）。</span></div>';
  }

  function bindEvents() {
    canvas.addEventListener('mousedown', function (e) { st.drag = true; st.lx = e.clientX; st.ly = e.clientY; });
    window.addEventListener('mouseup', function () { st.drag = false; });
    window.addEventListener('mousemove', function (e) {
      if (!st.drag) return;
      st.yaw += (e.clientX - st.lx) * 0.006;
      st.pitch = Math.max(-1.35, Math.min(1.35, st.pitch + (e.clientY - st.ly) * 0.005));
      st.lx = e.clientX; st.ly = e.clientY;
    });
    var slider = document.getElementById('gl-time');
    if (slider) slider.addEventListener('input', function () { st.t = +slider.value; st.play = false; syncPlay(); });
    var pb = document.getElementById('gl-play');
    if (pb) pb.addEventListener('click', function () { st.play = !st.play; syncPlay(); });
    var rs = document.getElementById('gl-reset');
    if (rs) rs.addEventListener('click', function () { st.yaw = -2.97; st.pitch = -0.34; st.t = 0; st.play = true; syncPlay(); });
  }
  function syncPlay() {
    var pb = document.getElementById('gl-play');
    if (pb) pb.textContent = st.play ? '⏸ 暂停' : '▶ 播放';
  }

  /* ------------------------------- 渲染 ------------------------------- */
  function resize() {
    if (!canvas) return;
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    var w = canvas.clientWidth || 800, h = canvas.clientHeight || 460;
    if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
      canvas.width = w * dpr; canvas.height = h * dpr;
    }
    gl.viewport(0, 0, canvas.width, canvas.height);
    return w / h;
  }

  function draw() {
    var asp = resize();
    gl.enable(gl.DEPTH_TEST);
    gl.clearColor(0.031, 0.051, 0.078, 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    var dist = 3.35 - 0.0;
    var cy = Math.cos(st.pitch), eye = [dist*cy*Math.sin(st.yaw), -dist*cy*Math.cos(st.yaw), dist*Math.sin(st.pitch) * -1];
    var view = m4lookAt(eye, [0,0,0], [0,0,1]);
    var proj = m4persp(38 * Math.PI / 180, asp, 0.1, 100);
    var mvp = m4mul(proj, view);
    var light = norm([0.65, -0.62, 0.44]);

    /* 球体 + 贴图 */
    gl.useProgram(progTex.p);
    gl.uniformMatrix4fv(progTex.u.uMVP, false, mvp);
    gl.uniformMatrix4fv(progTex.u.uMV, false, view);
    gl.uniform1f(progTex.u.uTheta, thetaTurns(st.t));
    gl.uniform1f(progTex.u.uUseTex, meshes.texOK ? 1 : 0);
    gl.uniform3fv(progTex.u.uLight, light);
    bind(progTex, 'aPos', meshes.sphere.pos, 3);
    bind(progTex, 'aNrm', meshes.sphere.nrm, 3);
    bind(progTex, 'aUV', meshes.sphere.uv, 2);
    if (meshes.texOK) {
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, meshes.tex);
      gl.uniform1i(progTex.u.uTex, 0);
    }
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, meshes.sphere.idx);
    gl.drawElements(gl.TRIANGLES, 72 * 72 * 6, gl.UNSIGNED_SHORT, 0);

    /* 经纬网 / 赤道 / 坐标轴（经纬网为地理格网，须随地球东向自转；赤道环轴对称、坐标轴为 ECI 参考不动） */
    gl.useProgram(progFlat.p);
    gl.uniformMatrix4fv(progFlat.u.uMVP, false, mvp);
    gl.uniform1f(progFlat.u.uIsPoint, 0);
    gl.uniformMatrix4fv(progFlat.u.uMVP, false, m4mul(mvp, m4rotZ((st.t / 86164.0) * 2 * Math.PI)));
    drawLines(meshes.grat, [0.30, 0.52, 0.62], 1);
    gl.uniformMatrix4fv(progFlat.u.uMVP, false, mvp);
    drawLines(meshes.eq, [0.45, 0.72, 0.52], 1);
    drawLines(meshes.axes, [0.62, 0.44, 0.36], 1);

    /* 轨道弧（6 条，一个轨道周期） */
    meshes.orbits.forEach(function (o, i) {
      drawLines(o, i % 2 ? [0.36, 0.55, 0.78] : [0.48, 0.66, 0.86], 1);
    });

    /* 651 颗卫星（12 帧线性插值） */
    var sat = satXYZ(st.t);
    gl.bindBuffer(gl.ARRAY_BUFFER, meshes.sat.b);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, sat);
    gl.uniform1f(progFlat.u.uSize, 3.0);
    gl.uniform3f(progFlat.u.uColor, 0.92, 0.95, 1.0);
    gl.uniform1f(progFlat.u.uIsPoint, 1);
    bind(progFlat, 'aPos', meshes.sat, 3);
    gl.drawArrays(gl.POINTS, 0, D.constellation.n);

    /* 站点 + 最佳服务星 + 链路 */
    var site = siteXYZ(st.t);
    var b = bestSat(sat, site);
    // 站点十字
    var sr = 1.012;
    var sa = [sr*site[0], sr*site[1], sr*site[2]];
    var perp = norm(cross(site, [0, 0, 1])), perp2 = norm(cross(site, perp));
    var k = 0.045;
    var siteBuf = [
      sa[0]-perp[0]*k, sa[1]-perp[1]*k, sa[2]-perp[2]*k, sa[0]+perp[0]*k, sa[1]+perp[1]*k, sa[2]+perp[2]*k,
      sa[0]-perp2[0]*k, sa[1]-perp2[1]*k, sa[2]-perp2[2]*k, sa[0]+perp2[0]*k, sa[1]+perp2[1]*k, sa[2]+perp2[2]*k
    ];
    gl.bindBuffer(gl.ARRAY_BUFFER, meshes.site.b);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, new Float32Array(siteBuf));
    gl.uniform1f(progFlat.u.uIsPoint, 0);
    drawLines(meshes.site, [1.0, 0.83, 0.35], 2);

    if (b.i >= 0) {
      var linkArr = [sr*site[0], sr*site[1], sr*site[2], b.p[0], b.p[1], b.p[2]];
      gl.bindBuffer(gl.ARRAY_BUFFER, meshes.link.b);
      gl.bufferSubData(gl.ARRAY_BUFFER, 0, new Float32Array(linkArr));
      drawLines(meshes.link, b.el >= MASK_DEG ? [1.0, 0.45, 0.35] : [0.55, 0.42, 0.40], 2);
      gl.uniform1f(progFlat.u.uSize, 9.0);
      gl.uniform3f(progFlat.u.uColor, 1.0, 0.45, 0.35);
      gl.uniform1f(progFlat.u.uIsPoint, 1);
      bind(progFlat, 'aPos', meshes.link, 3);
      // 只画第 2 个点（服务星）
      gl.drawArrays(gl.POINTS, 0, 2);
      gl.uniform1f(progFlat.u.uIsPoint, 0);
    }

    /* HUD（仅在整秒变化时改写 DOM，避免每帧写） */
    var step = Math.round(st.t);
    if (hud && step !== hudStep) {
      hudStep = step;
      var el = b.el;
      hud.innerHTML = 'T+<b>' + st.t.toFixed(0) + ' s</b> · 卫星 <b>' + D.constellation.n +
        '</b> 颗 · |r| <b>' + D.constellation.r_km[0] + '–' + D.constellation.r_km[1] +
        ' km</b> · 掩蔽 <b>' + MASK_DEG + '°</b>';
      if (deg) {
        deg.innerHTML = SITE.name + ' 最佳服务星仰角 <b>' + b.el.toFixed(1) + '°</b> ' +
          (b.el >= MASK_DEG ? '<span style="color:#5fd38a">✓</span>' : '<span style="color:#ffb4a0">✗</span>') +
          '<br/><span style="color:#7d8fa3">历元 ' + (D.constellation.epoch_utc || '').slice(0, 10) + '</span>';
      }
    }
    var slider = document.getElementById('gl-time');
    if (slider && st.play) slider.value = st.t;
  }

  function drawLines(mesh, color, w) {
    gl.uniform3fv(progFlat.u.uColor, color);
    gl.uniform1f(progFlat.u.uSize, 1);
    bind(progFlat, 'aPos', mesh, 3);
    gl.drawArrays(gl.LINES, 0, mesh.n);
  }

  var last = 0;
  function loop(ts) {
    raf = requestAnimationFrame(loop);
    if (!gl) return;
    if (st.play) {
      var now = ts || 0, dt = last ? (now - last) / 1000 : 0;
      last = now;
      st.t += dt * (SPAN / 26);   // 全时段 ≈26 s 走完
      if (st.t > SPAN) st.t -= SPAN;
    }
    try { draw(); } catch (e) {
      cancelAnimationFrame(raf); raf = null;
      degrade('渲染异常：' + e.message);
    }
  }

  window.DEMO_3D = {
    init: init,
    onShow: function () { if (!st.inited) init(); else if (gl) { draw(); } },
    /** 外部时间轴驱动：暂停内部播放，把时间设为 t 秒并立即重绘（供 ANIM 与甘特共用同一条时间轴） */
    drive: function (t) {
      if (!st.inited) init();
      if (!gl) return;
      st.play = false;
      st.t = Math.max(0, Math.min(SPAN, t));
      try { draw(); } catch (e) { degrade('渲染异常：' + e.message); }
    },
    stop: function () { if (raf) { cancelAnimationFrame(raf); raf = null; } },
    state: st
  };
})();
