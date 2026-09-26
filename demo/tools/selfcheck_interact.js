#!/usr/bin/env node
/**
 * demo 自检脚本 C —— 交互与动画回归
 *
 * 用法：
 *   NODE_PATH="<node-workspace>/node_modules" node demo/tools/selfcheck_interact.js <index.html 绝对路径>
 *
 * 守什么：
 *   ① 切屏：点侧栏每一项 → 目标屏唯一可见；
 *   ② 对比开关：生效、且**不改变任何数值**；
 *   ③ 溯源抽屉：点数字 → 打开且含 run_id 与权威文件；
 *   ④ 动画：可播放/暂停/拖动定格；**拖到任意位置数字都不变**（动画只做揭示，不改数字）；
 *   ⑤ 3D：初始化 + 外部时间轴 drive() 生效 + 像素回读非空；
 *   ⑥ 图表：ECharts 全部落图（非 0×0）；
 *   ⑦ 页签与折叠附录可用。
 * 退出码 0 = PASS。
 */
const fs = require('fs');
const path = require('path');
let puppeteer;
try { puppeteer = require('puppeteer-core'); }
catch (e) { console.error('[FATAL] 未找到 puppeteer-core，请设置 NODE_PATH。'); process.exit(2); }

const CHROME = process.env.DEMO_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const target = process.argv[2];
if (!target) { console.error('用法: node selfcheck_interact.js <index.html 绝对路径>'); process.exit(2); }
const url = 'file:///' + path.resolve(target).replace(/\\/g, '/');
const steps = [], errors = [];
const ck = (name, ok, extra) => steps.push({ name, ok: !!ok, extra: extra || '' });

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME, headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--use-gl=swiftshader', '--enable-unsafe-swiftshader']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000 });
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push('console.error: ' + m.text()); });
  await page.goto(url, { waitUntil: 'networkidle0', timeout: 30000 });
  await new Promise(r => setTimeout(r, 600));

  /* ① 逐屏导航 */
  const ids = await page.$$eval('[data-goto]', els => els.map(e => e.getAttribute('data-goto')));
  let navBad = [];
  for (const id of ids) {
    const r = await page.evaluate((x) => {
      document.querySelector('[data-goto="' + x + '"]').click();
      const vis = [...document.querySelectorAll('.screen')].filter(e => getComputedStyle(e).display !== 'none');
      return { n: vis.length, id: vis[0] ? vis[0].getAttribute('data-screen') : null };
    }, id);
    if (r.n !== 1 || r.id !== id) navBad.push(id + '→' + r.id + '(n=' + r.n + ')');
  }
  ck('侧栏切屏 ' + ids.length + ' 屏，每屏唯一可见', navBad.length === 0, navBad.join(' '));

  /* ② 对比开关 */
  const before = await page.evaluate(() => {
    DEMO.show('A5');
    const el = document.querySelector('[data-metric="marg/premig_on/切换总时延均值_ms"]');
    return { v: el.getAttribute('data-value'), txt: el.textContent.trim() };
  });
  await page.evaluate(() => DEMO.setMode('base'));
  await new Promise(r => setTimeout(r, 250));
  const after = await page.evaluate(() => {
    const el = document.querySelector('[data-metric="marg/premig_on/切换总时延均值_ms"]');
    const bn = document.getElementById('mode-banner');
    return { mode: document.body.getAttribute('data-mode'), v: el.getAttribute('data-value'),
             txt: el.textContent.trim(), banner: bn ? getComputedStyle(bn).display : 'none' };
  });
  ck('对比开关 → data-mode=base', after.mode === 'base');
  ck('对比开关不改变任何数值', before.v === after.v && before.txt === after.txt, before.v + ' → ' + after.v);
  ck('对比开关显示口径横幅', after.banner !== 'none');
  await page.evaluate(() => DEMO.setMode('ours'));
  await new Promise(r => setTimeout(r, 200));

  /* ③ 溯源抽屉 */
  const dr = await page.evaluate(() => {
    DEMO.show('X1');
    const el = document.querySelector('[data-metric="py/wenchuan_storm2/接入时延均值_ms"]');
    el.click();
    const d = document.getElementById('drawer');
    return { on: d.classList.contains('on'), t: d.innerText || '' };
  });
  ck('点击数字 → 溯源抽屉打开', dr.on);
  ck('抽屉含 run_id 行', /run_id/.test(dr.t));
  ck('抽屉含权威文件名', /双轨交叉验证对照表|metrics\.json|crossval_ho_results|t3_table/.test(dr.t));
  ck('抽屉可关闭', await page.evaluate(() => { DEMO.closeDrawer(); return !document.getElementById('drawer').classList.contains('on'); }) === true);

  /* ④ 动画：控制 + 定格 + 数值不变 */
  const A = await page.evaluate(async () => {
    DEMO.show('A3');
    await new Promise(r => setTimeout(r, 400));
    const gv = () => document.querySelector('[data-metric="marg/rach2step/latency"]');
    const v0 = gv().getAttribute('data-value');
    ANIM.seek('a3', 0); const t0 = gv().textContent.trim();
    ANIM.seek('a3', 0.5); const t5 = gv().textContent.trim();
    ANIM.seek('a3', 1); const t1 = gv().textContent.trim();
    const segW = [...document.querySelectorAll('.anim[data-anim="a3"] .seg')].map(x => +x.getAttribute('width'));
    ANIM.play('a3');
    await new Promise(r => setTimeout(r, 250));
    const playing = ANIM.state ? true : true;
    const a1 = document.querySelector('.abar[data-bar="a3"] [data-act="toggle"]').textContent;
    ANIM.pause('a3');
    const a2 = document.querySelector('.abar[data-bar="a3"] [data-act="toggle"]').textContent;
    return { v0, t0, t5, t1, segW0: segW[0], segNonZero: segW.filter(w => w > 0).length, a1: a1, a2: a2,
             hasBar: !!document.querySelector('.abar[data-bar="a3"]') };
  });
  ck('A3 动画有控制条', A.hasBar);
  ck('拖动定格：数值在三处定格下完全不变', A.t0 === A.t5 && A.t5 === A.t1, A.t0 + ' / ' + A.t5 + ' / ' + A.t1);
  ck('定格到终点后分段条全部生长（终态可见）', A.segW0 > 0 && A.segNonZero >= 3, '非零分段=' + A.segNonZero);
  ck('播放按钮状态随播放/暂停切换', /暂停/.test(A.a1) && /播放|重播/.test(A.a2), A.a1 + ' → ' + A.a2);

  const F = await page.evaluate(async () => {
    DEMO.show('A4');
    await new Promise(r => setTimeout(r, 300));
    ANIM.seek('a4', 0);
    const c0 = document.getElementById('a4-c-ok').textContent;
    ANIM.seek('a4', 1);
    const c1 = document.getElementById('a4-c-ok').textContent;
    const rate = document.getElementById('a4-rate').textContent;
    return { c0, c1, rate };
  });
  ck('A4 计数器随动画从 0 累计到真实值', +F.c0 === 0 && +F.c1 === 809, F.c0 + ' → ' + F.c1);
  ck('A4 终态拦截率 = 0.8537', F.rate === '0.8537', F.rate);

  const R = await page.evaluate(async () => {
    DEMO.show('A1');
    await new Promise(r => setTimeout(r, 300));
    ANIM.seek('a1', 1);
    const on = [...document.querySelectorAll('#sc-A1 .kpi')].filter(e => e.classList.contains('on')).length;
    const lit = [...document.querySelectorAll('#a1-terms .t')].filter(e => e.getAttribute('fill') === '#c0392b').length;
    return { on, total: document.querySelectorAll('#sc-A1 .kpi').length, lit };
  });
  ck('A1 终态：全部 KPI 揭示', R.on === R.total && R.total > 0, R.on + '/' + R.total);
  ck('A1 终态：终端点阵点亮', R.lit > 100, '点亮 ' + R.lit);

  const AN = await page.evaluate(() => {
    const anis = [...document.querySelectorAll('.anim')];
    const schs = [...document.querySelectorAll('.anim[data-schematic="1"]')];
    return { n: anis.length, sch: schs.length,
             noBar: anis.filter(e => !document.querySelector('.abar[data-bar="' + e.getAttribute('data-anim') + '"]')).length,
             noWm: schs.filter(e => { const c = e.closest('.card'); return !(c && c.textContent.includes('示意')); }).length };
  });
  ck('全部动画均挂控制条；机理示意动画均带「示意」水印', AN.noBar === 0 && AN.noWm === 0,
     '动画 ' + AN.n + ' 个（示意 ' + AN.sch + '）；缺控制条 ' + AN.noBar + '；缺水印 ' + AN.noWm);

  /* ⑤ 3D */
  const g3 = await page.evaluate(async () => {
    DEMO.show('A5');
    await new Promise(r => setTimeout(r, 900));
    const c = document.getElementById('gl');
    if (!c) return { init: false, err: 'canvas 缺失' };
    ANIM.pause('a5');            // 先停掉共用时间轴，避免它继续改写 3D 时刻
    DEMO_3D.drive(1200);
    await new Promise(r => setTimeout(r, 200));
    const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
    let nonBg = 0;
    try {
      const w = c.width, h = c.height, buf = new Uint8Array(4);
      for (let i = 0; i < 40; i++) {
        const x = Math.floor(w * (0.3 + 0.4 * (i % 8) / 8)), y = Math.floor(h * (0.3 + 0.4 * ((i / 8) | 0) / 5));
        gl.readPixels(x, y, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, buf);
        if (buf[0] + buf[1] + buf[2] > 40) nonBg++;
      }
    } catch (e) { return { init: false, err: e.message }; }
    return { init: DEMO_3D.state.inited, t: DEMO_3D.state.t, nonBg: nonBg, play: DEMO_3D.state.play,
             hud: (document.getElementById('gl-hud').textContent || '').slice(0, 40) };
  });
  ck('A5 的 3D 已初始化（WebGL 可用）', g3.init === true, g3.err || '');
  ck('3D 受外部时间轴驱动（drive 生效且暂停内部播放）', Math.abs(g3.t - 1200) < 1 && g3.play === false, 't=' + g3.t);
  ck('3D 画面非空（像素回读）', g3.nonBg >= 3, '非背景采样点=' + g3.nonBg);
  ck('3D HUD 显示星座规模与历元', /651/.test(g3.hud), g3.hud);

  /* ⑥ 图表 */
  const charts = await page.evaluate(async () => {
    DEMO.show('A7');
    await new Promise(r => setTimeout(r, 900));
    const cs = [...document.querySelectorAll('#sc-A7 .chart canvas')];
    return cs.map(c => c.width + 'x' + c.height).join(',');
  });
  ck('A7 三张 ECharts 均已落图（非 0×0）', charts && !/^0x0/.test(charts) && charts.split(',').length >= 3, charts);

  /* ⑦ 页签与折叠附录 */
  const tabs = await page.evaluate(async () => {
    DEMO.show('A6');
    await new Promise(r => setTimeout(r, 250));
    document.querySelector('[data-tab="p2"]').click();
    await new Promise(r => setTimeout(r, 150));
    const p1 = document.querySelector('[data-pane="p1"]');
    const p2 = document.querySelector('[data-pane="p2"]');
    return { p1: getComputedStyle(p1).display, p2: getComputedStyle(p2).display };
  });
  ck('A6 页签可切换（面板互斥显示）', tabs.p1 === 'none' && tabs.p2 !== 'none', JSON.stringify(tabs));
  const folds = await page.evaluate(async () => {
    DEMO.show('X1');
    await new Promise(r => setTimeout(r, 250));
    const f = document.querySelectorAll('.fold');
    const closed = [...f].filter(x => !x.open).length;
    f[0].open = true;
    const open0 = f[0].open;
    const rows = f[0].querySelectorAll('tbody tr').length;
    return { n: f.length, closed, open0, rows };
  });
  ck('X1 附录折叠块可用且首块可展开', folds.n >= 6 && folds.open0 === true, folds.n + ' 块，首块 ' + folds.rows + ' 行');

  await browser.close();
  const fail = steps.filter(s => !s.ok);
  const L = ['=== demo 交互与动画回归报告（' + new Date().toISOString() + '）===', '目标：' + url, ''];
  steps.forEach(s => L.push((s.ok ? '  ✓ ' : '  ✗ ') + s.name + (s.extra ? '   [' + s.extra + ']' : '')));
  L.push('');
  L.push('JS 错误：' + errors.length + (errors.length ? '  ✗ ' + errors.slice(0, 3).join(' | ') : '  ✓'));
  L.push('');
  L.push(fail.length || errors.length
    ? ('结果：✗ FAIL —— ' + (fail.length ? fail.length + ' 项断言未过' : '') + (errors.length ? ' JS 错误 ' + errors.length + ' 处' : ''))
    : '结果：✓ PASS（' + steps.length + ' 项交互/动画断言全过）');
  console.log(L.join('\n'));
  process.exit((fail.length || errors.length) ? 1 : 0);
})().catch(e => { console.error('[FATAL] ' + e.message); process.exit(2); });
