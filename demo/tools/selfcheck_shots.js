#!/usr/bin/env node
/**
 * demo 自检脚本 B —— 逐屏遍历：切屏 → 截图 → 该屏数字断言
 *
 * 用法：
 *   NODE_PATH="<node-workspace>/node_modules" node demo/tools/selfcheck_shots.js <index.html 绝对路径> [输出目录]
 *
 * 屏幕约定（页面须遵守）：
 *   - 可切换的屏：<section class="screen" data-screen="S3">…</section>
 *   - 切换按钮  ：<a class="nav" data-goto="S3">…</a>   （或任意带 data-goto 的元素）
 *   - 若页面无 data-screen，则只截首页一张。
 *
 * 产物：<输出目录>/S1.png … + 每屏断言摘要（stdout）+ summary.txt
 * 默认输出目录：demo/_selfcheck/（已 gitignore，不入库）
 */
const fs = require('fs');
const path = require('path');
let puppeteer;
try { puppeteer = require('puppeteer-core'); }
catch (e) {
  console.error('[FATAL] 未找到 puppeteer-core，请设置 NODE_PATH。');
  process.exit(2);
}

const CHROME = process.env.DEMO_CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const target = process.argv[2];
if (!target) { console.error('用法: node selfcheck_shots.js <index.html 绝对路径> [输出目录]'); process.exit(2); }
const outDir = process.argv[3] || path.join(path.dirname(path.resolve(target)), '_selfcheck');
const url = 'file:///' + path.resolve(target).replace(/\\/g, '/');
const VIEW = { width: 1600, height: 1000 };

(async () => {
  if (!fs.existsSync(outDir)) fs.mkdirSync(outDir, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--use-gl=swiftshader', '--enable-unsafe-swiftshader']
  });
  const page = await browser.newPage();
  await page.setViewport(VIEW);
  const errors = [];
  page.on('pageerror', e => errors.push(String(e && e.message || e)));
  page.on('console', m => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

  await page.goto(url, { waitUntil: 'networkidle0', timeout: 30000 });
  await new Promise(r => setTimeout(r, 700));

  const screens = await page.$$eval('[data-screen]', els => els.map(e => e.getAttribute('data-screen')));
  const report = [];
  const shoot = async (name) => {
    const file = path.join(outDir, name + '.png');
    await page.screenshot({ path: file, fullPage: true });
    const info = await page.evaluate(() => {
      const bad = [], ok = [];
      const _vis = [...document.querySelectorAll('.screen')]
        .filter(e => getComputedStyle(e).display !== 'none');
      const _scope = _vis.length ? _vis[0] : document;
      _scope.querySelectorAll('[data-metric]').forEach(n => {
        const v = n.getAttribute('data-value');
        const want = (window.DEMO_DATA && window.DEMO_DATA.metrics || {})[n.getAttribute('data-metric')];
        if (want === undefined) return;
        (Math.abs(parseFloat(v) - parseFloat(want)) <= 1e-9 ? ok : bad)
          .push(n.getAttribute('data-metric') + '=' + v);
      });
      const _cur = [...document.querySelectorAll('.screen')].filter(e => getComputedStyle(e).display !== 'none');
      return { ok: ok.length, bad, title: document.title,
               screenText: (_cur[0] ? _cur[0].getAttribute('data-screen') : '(all)') };
    });
    report.push({ name, file, ...info });
    console.log('  ' + name.padEnd(6) + ' → ' + path.basename(file)
      + '  [' + info.screenText + '] 可断言 ' + info.ok + ' 项' + (info.bad.length ? '  ✗ 不符 ' + info.bad.length : '  ✓'));
  };

  if (screens.length <= 1) {
    await page.evaluate(() => { if (window.ANIM) window.ANIM.finishVisible(); });
    await shoot('S1');
  } else {
    for (const s of screens) {
      const clicked = await page.evaluate((id) => {
        const el = document.querySelector('[data-goto="' + id + '"]');
        if (!el) return false;
        el.click(); return true;
      }, s);
      await new Promise(r => setTimeout(r, 320));
      if (!clicked) await page.evaluate((id) => {
        document.querySelectorAll('.screen').forEach(e => {
          e.style.display = (e.getAttribute('data-screen') === id) ? '' : 'none';
        });
      }, s);
      /* 定格动画到终点：保证截图是「完整终态」（数字全部揭示），且可重复 */
      await page.evaluate(() => { if (window.ANIM) window.ANIM.finishVisible(); });
      await new Promise(r => setTimeout(r, 420));
      await shoot(s);
    }
  }

  await browser.close();
  const sum = [
    '=== 逐屏截图自检（' + new Date().toISOString() + '）===',
    '目标：' + url,
    '输出：' + outDir,
    '屏数：' + report.length,
    'JS 错误：' + errors.length + (errors.length ? '  ✗ ' + errors.slice(0, 3).join(' | ') : '  ✓'),
    '',
  ].concat(report.map(r => '  ' + r.name.padEnd(6) + ' 可断言 ' + String(r.ok).padStart(3)
    + ' 项' + (r.bad.length ? '  ✗ ' + r.bad.slice(0, 4).join(', ') : '  ✓')
    + '  → ' + path.basename(r.file)));
  fs.writeFileSync(path.join(outDir, 'summary.txt'), sum.join('\r\n') + '\r\n', 'utf8');
  console.log(sum.join('\n'));
  process.exit(errors.length ? 1 : 0);
})().catch(e => { console.error('[FATAL] ' + e.message); process.exit(2); });
