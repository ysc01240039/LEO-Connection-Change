#!/usr/bin/env node
/**
 * demo 自检脚本 A —— 外部请求 / JS 错误 / 数字断言 / 废弃值 / run_id 覆盖 / WebGL
 *
 * 用法：
 *   NODE_PATH="<node-workspace>/node_modules" node demo/tools/selfcheck_audit.js <index.html 绝对路径> [截图路径]
 *
 * 设计依据：docs/demo制作方案.md §十四「判定三原则」
 *   ① 外部请求必须 = 0（保证断网可放、零 CDN）
 *   ② JS 错误必须 = 0（保证不白屏）
 *   ③ 页面数字必须逐字等于权威数据（保证数据真实可溯源）
 *
 * 数据图元契约（页面须遵守）：
 *   <span data-metric="切换总时延" data-value="12.06" data-runid="…ns3_20260923T143011Z">12.06 ms</span>
 *   - data-metric：指标键，须存在于 window.DEMO_DATA.metrics
 *   - data-value ：页面显示值（纯数字字符串）
 *   - data-runid ：数据来源 run 标签（可放在祖先元素上）
 */
const fs = require('fs');
const path = require('path');
let puppeteer;
try { puppeteer = require('puppeteer-core'); }
catch (e) {
  console.error('[FATAL] 未找到 puppeteer-core。请设置 NODE_PATH 指向受管 node workspace 的 node_modules。');
  process.exit(2);
}

const CHROME = process.env.DEMO_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const target = process.argv[2];
const shot = process.argv[3] || '';
if (!target) {
  console.error('用法: node selfcheck_audit.js <index.html 绝对路径> [截图输出路径]');
  process.exit(2);
}
const url = 'file:///' + path.resolve(target).replace(/\\/g, '/');
const DEPRECATED = ['12.02', '380.92'];   // 任务书废弃值，出现即判失败

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--use-gl=swiftshader', '--enable-unsafe-swiftshader']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 1000 });

  const external = [], errors = [], failed = [];
  page.on('request', r => {
    const u = r.url();
    if (!/^(file|data|blob):/i.test(u)) external.push(u);
  });
  page.on('pageerror', e => errors.push('pageerror: ' + (e && e.message ? e.message : String(e))));
  page.on('console', m => { if (m.type() === 'error') errors.push('console.error: ' + m.text()); });
  page.on('requestfailed', r => {
    const f = r.failure();
    failed.push(r.url() + ' :: ' + (f ? f.errorText : 'unknown'));
  });

  await page.goto(url, { waitUntil: 'networkidle0', timeout: 30000 });
  await new Promise(r => setTimeout(r, 700));

  /* ★逐屏巡航：动画只有切到该屏才会真正执行 —— 不巡航就发现不了「动画里取到 null」这类缺陷。
     每屏：切过去 → 把动画推到终点 → 停留，收集新增的 JS 错误。 */
  const navIds = await page.$$eval('[data-goto]', els => els.map(e => e.getAttribute('data-goto')));
  const navErr = {};
  for (const id of navIds) {
    const before = errors.length;
    await page.evaluate((x) => {
      const el = document.querySelector('[data-goto="' + x + '"]');
      if (el) el.click();
      if (window.ANIM) window.ANIM.finishVisible();
    }, id);
    await new Promise(r => setTimeout(r, 260));
    await page.evaluate(() => { if (window.ANIM) window.ANIM.finishVisible(); });
    await new Promise(r => setTimeout(r, 160));
    if (errors.length > before) navErr[id] = errors.slice(before, before + 3);
  }
  await page.evaluate(() => {
    const g = document.querySelector('[data-goto]');
    if (window.DEMO && g) window.DEMO.show(g.getAttribute('data-goto'));
  });

  const res = await page.evaluate((DEP) => {
    const nodes = [...document.querySelectorAll('[data-metric]')];
    const D = (window.DEMO_DATA && window.DEMO_DATA.metrics) || {};
    const mismatches = [], missingKey = [], noRunId = [];
    nodes.forEach(n => {
      const k = n.getAttribute('data-metric');
      const v = n.getAttribute('data-value');
      if (!(k in D)) { missingKey.push(k); return; }
      const a = parseFloat(v), b = parseFloat(D[k]);
      if (!isFinite(a) || Math.abs(a - b) > 1e-9) mismatches.push(k + ' 页面=' + v + ' 数据=' + D[k]);
      let e = n, has = false;
      while (e) { if (e.getAttribute && e.getAttribute('data-runid')) { has = true; break; } e = e.parentElement; }
      if (!has) noRunId.push(k);
    });
    /* ---- 独立誊写副本（data-golden）双向核对 ----
       抓「指标键映射错位」这类自洽型错误：data-value 来自 data.js（自动生成），
       data-golden 由人工从《双轨交叉验证对照表》抄写 → 两条路径不一致即失败。 */
    const goldens = [...document.querySelectorAll('[data-golden]')];
    const goldBad = [], goldText = [];
    goldens.forEach(n => {
      const g = parseFloat(n.getAttribute('data-golden'));
      const v = parseFloat(n.getAttribute('data-value'));
      const shown = (() => {
        const m = (n.textContent || '').replace(/,/g, '').match(/-?\d+(?:\.\d+)?/);
        return m ? parseFloat(m[0]) : NaN;
      })();
      if (!isFinite(g) || Math.abs(v - g) > 1e-9) {
        goldBad.push((n.getAttribute('data-metric') || '?') + ' data-value=' + v + ' ≠ golden=' + g);
      }
      /* 显示文本必须与 golden 数值一致（允许四舍五入到展示精度） */
      const tol = Math.abs(g) >= 100 ? 0.05 : (Math.abs(g) >= 1 ? 0.005 : 0.0005);
      if (!isFinite(shown) || Math.abs(shown - g) > tol) {
        goldText.push((n.getAttribute('data-metric') || '?') + ' 显示="' + (n.textContent || '').trim() + '" ≠ golden=' + g);
      }
    });
    /* ---- 「示意」水印红线：每个机理动画容器所在卡片内必须出现「示意」二字 ---- */
    const sch = [...document.querySelectorAll('.schematic, .schematic-box')];
    const schBad = sch.filter(e => !(e.textContent || '').includes('示意')).length;
    const anims = [...document.querySelectorAll('.anim')];
    const schAnims = [...document.querySelectorAll('.anim[data-schematic="1"]')];
    const animNoWm = schAnims.filter(e => {
      const card = e.closest('.card') || e.parentElement;
      return !(card && (card.textContent || '').includes('示意'));
    }).map(e => e.getAttribute('data-anim'));
    /* ---- ★SVG 内不得出现 HTML 元素★：外来内联内容会让解析器提前闭合 <svg>，
           其后的节点会退化成 HTML 文本堆叠（本项目已踩：把 <span> 放进 <text> 里） ---- */
    const svgHtml = [];
    document.querySelectorAll('svg').forEach((sv, si) => {
      const bad = sv.querySelectorAll('span,div,b,i,em,strong,p,ul,li,table,button');
      if (bad.length) svgHtml.push('svg#' + si + ' 含 ' + bad.length + ' 个 HTML 元素（如 <' + bad[0].tagName.toLowerCase() + '>）');
    });
    /* ---- 每个动画都必须有控制条（否则动画不可控/不可自检定格） ---- */
    const animNoBar = anims.filter(e =>
      !document.querySelector('.abar[data-bar="' + e.getAttribute('data-anim') + '"]')
    ).map(e => e.getAttribute('data-anim'));
    /* ---- 不得出现任何 http(s) 外链（零 CDN 的静态证据） ---- */
    const ext = [...document.querySelectorAll('[src],[href]')]
      .map(e => e.getAttribute('src') || e.getAttribute('href') || '')
      .filter(u => /^https?:/i.test(u));
    /* ---- 全屏文本不得出现 undefined / NaN 等渲染残缺 ---- */
    const dangling = [];
    document.querySelectorAll('.screen').forEach(sec => {
      const t = sec.innerText || '';
      const m = t.match(/undefined|NaN|\[object Object\]|null ?(ms|项|次|%|颗)/);
      if (m) dangling.push(sec.getAttribute('data-screen') + ' → ' + m[0]);
    });
    const txt = (document.body && document.body.innerText) || '';
    const deprecated = DEP.filter(v => txt.includes(v));
    const cv = document.createElement('canvas');
    const gl = cv.getContext('webgl') || cv.getContext('experimental-webgl');
    const canvases = [...document.querySelectorAll('canvas')].length;
    const hasDemoData = !!window.DEMO_DATA;
    const charts = [...document.querySelectorAll('.chart,[data-chart]')].length;
    return { metricCount: nodes.length, mismatches, missingKey, noRunId, deprecated, dangling,
             goldCount: goldens.length, goldBad, goldText, schCount: sch.length, schBad, ext,
             animCount: anims.length, animSch: schAnims.length, animNoWm, animNoBar, svgHtml,
             webgl: !!gl, canvases, hasDemoData, charts, title: document.title };
  }, DEPRECATED);

  if (shot) {
    await page.screenshot({ path: shot, fullPage: false });
  }
  await browser.close();

  const fail = [];
  if (external.length)  fail.push('外部请求 ' + external.length + ' 个');
  if (errors.length)    fail.push('JS 错误 ' + errors.length + ' 处');
  if (res.mismatches.length) fail.push('数字不符 ' + res.mismatches.length + ' 处');
  if (res.missingKey.length) fail.push('指标键缺失 ' + res.missingKey.length + ' 个');
  if (res.noRunId.length)    fail.push('缺 run_id 的图元 ' + res.noRunId.length + ' 个');
  if (res.deprecated.length) fail.push('出现废弃值 ' + res.deprecated.join('/'));
  if (res.goldBad.length)    fail.push('golden 数值不符 ' + res.goldBad.length + ' 处');
  if (res.goldText.length)   fail.push('golden 显示文本不符 ' + res.goldText.length + ' 处');
  if (res.schBad)            fail.push('示意图元缺「示意」字样 ' + res.schBad + ' 个');
  if (res.ext.length)        fail.push('存在 http(s) 外链 ' + res.ext.length + ' 个');
  if (res.dangling.length)   fail.push('渲染残缺（undefined/NaN）' + res.dangling.length + ' 处');
  if (res.animNoWm.length)   fail.push('机理动画缺「示意」水印 ' + res.animNoWm.length + ' 个');
  if (res.animNoBar.length)  fail.push('动画缺控制条 ' + res.animNoBar.length + ' 个');
  const navBad = Object.keys(navErr);
  if (navBad.length) fail.push('逐屏巡航发现 JS 错误：' + navBad.join(','));
  if (res.svgHtml.length) fail.push('SVG 内混入 HTML 元素 ' + res.svgHtml.length + ' 处');

  const L = [];
  L.push('=== demo 自检报告（' + new Date().toISOString() + '）===');
  L.push('目标：' + url);
  L.push('标题：' + (res.title || '(空)'));
  L.push('');
  L.push('[原则①] 外部请求数 = ' + external.length + (external.length ? '  ✗' : '  ✓（零 CDN，断网可放）'));
  external.slice(0, 5).forEach(u => L.push('        - ' + u));
  L.push('[原则①] 静态 http(s) 外链 = ' + res.ext.length + (res.ext.length ? '  ✗' : '  ✓'));
  res.ext.slice(0, 5).forEach(u => L.push('        - ' + u));
  L.push('[原则②] JS 错误    = ' + errors.length + (errors.length ? '  ✗' : '  ✓'));
  errors.slice(0, 5).forEach(e => L.push('        - ' + e));
  L.push('[原则②] 失败请求   = ' + failed.length + (failed.length ? '  ✗' : '  ✓'));
  failed.slice(0, 5).forEach(e => L.push('        - ' + e));
  L.push('[原则③] 数据图元   = ' + res.metricCount + ' 个（窗口内 DEMO_DATA=' + res.hasDemoData + '）');
  L.push('        数字不符   = ' + res.mismatches.length + (res.mismatches.length ? '  ✗' : '  ✓'));
  res.mismatches.slice(0, 8).forEach(e => L.push('        - ' + e));
  L.push('        指标键缺失 = ' + res.missingKey.length + (res.missingKey.length ? '  ✗' : '  ✓'));
  res.missingKey.slice(0, 8).forEach(e => L.push('        - ' + e));
  L.push('        缺 run_id  = ' + res.noRunId.length + (res.noRunId.length ? '  ✗' : '  ✓'));
  res.noRunId.slice(0, 8).forEach(e => L.push('        - ' + e));
  L.push('        废弃值     = ' + (res.deprecated.length ? res.deprecated.join('/') + '  ✗' : '无  ✓'));
  L.push('[独立副本] data-golden 图元 = ' + res.goldCount + ' 个（人工从对照表誊写，与 data.js 双路径互证）');
  L.push('        数值互证   = ' + res.goldBad.length + (res.goldBad.length ? '  ✗' : '  ✓'));
  res.goldBad.slice(0, 8).forEach(e => L.push('        - ' + e));
  L.push('        显示文本   = ' + res.goldText.length + (res.goldText.length ? '  ✗' : '  ✓'));
  res.goldText.slice(0, 8).forEach(e => L.push('        - ' + e));
  L.push('[诚实红线] .schematic 图元 = ' + res.schCount + ' 个，缺「示意」字样 = ' + res.schBad +
         (res.schBad ? '  ✗' : '  ✓'));
  L.push('[渲染完整性] 全屏文本中 undefined/NaN = ' + res.dangling.length +
         (res.dangling.length ? '  ✗' : '  ✓'));
  res.dangling.slice(0, 8).forEach(e => L.push('        - ' + e));
  L.push('[逐屏巡航] 遍历 ' + navIds.length + ' 屏并推进各屏动画：JS 错误 = ' +
         (navBad.length ? '  ✗ ' + navBad.join(' ') : '+0  ✓'));
  navBad.slice(0, 3).forEach(id => navErr[id].forEach(e => L.push('        - ' + id + ' :: ' + e)));
  L.push('[结构完整性] SVG 内混入 HTML 元素 = ' + res.svgHtml.length +
         (res.svgHtml.length ? '  ✗ ' + res.svgHtml.slice(0, 2).join(' | ') : '  ✓'));
  L.push('[动画] .anim 容器 = ' + res.animCount + ' 个（其中机理示意 ' + res.animSch + ' 个）；缺「示意」水印 = ' + res.animNoWm.length +
         (res.animNoWm.length ? '  ✗ ' + res.animNoWm.join(',') : '  ✓') +
         '；缺控制条 = ' + res.animNoBar.length + (res.animNoBar.length ? '  ✗ ' + res.animNoBar.join(',') : '  ✓'));
  L.push('');
  L.push('[环境] WebGL=' + res.webgl + '  canvas=' + res.canvases + '  图表容器=' + res.charts);
  if (shot) L.push('[截图] ' + shot);
  L.push('');
  L.push(fail.length ? ('结果：✗ FAIL —— ' + fail.join('；')) : '结果：✓ PASS（三原则 + 独立副本 + 诚实红线全过）');
  /* 同步落盘报告：process.exit() 会丢弃尚未 flush 的管道 stdout，报告必须可取证 */
  try { fs.writeFileSync(path.join(path.dirname(shot || target), '_selfcheck', 'audit_report.txt'), L.join('\r\n') + '\r\n', 'utf8'); } catch (e2) {}
  console.log(L.join('\n'));
  process.exit(fail.length ? 1 : 0);
})().catch(e => { console.error('[FATAL] ' + e.message); process.exit(2); });
