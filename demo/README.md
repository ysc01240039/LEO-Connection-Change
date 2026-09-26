# demo —— 面向应急救灾的低轨卫星-地面融合组网快速接入系统 · 成果演示

> **交付形态**：离线单页 HTML。**双击 `index.html` 即开，断网可放，零 CDN、零后端、零构建工具、零 web font。**
> **数字权威**：`docs/双轨交叉验证对照表.md`（任务书硬要求①）——页面上每个数字都必须能在该表找到行。
> **施工依据**：`docs/demo实施蓝图.md`（20 屏施工表）；**施工结果与自检记录**：`docs/demo自检能力与优化登记.md` §十八。

---

## 一、怎么打开

直接双击 `index.html`。（推荐 Chrome / Edge；首次打开无需任何安装，**无需联网**。）

**5 分钟讲法**（顶栏已写明路径）：`S1`（30 s）→ 机制① `S2–S4` → 机制② `S5–S7` → 机制③ `S8–S10`（各 ~70 s）→ `S19`（20 s）。
其余屏（S11–S18、S20）备查不主动讲 —— 被追问时按侧栏目录直接跳。

---

## 二、目录结构

```
demo/
├── index.html                  # 唯一入口（零外部引用；脚本按序加载 4 个本地 js）
├── data.js                     # 预生成数据（tools/build_demo_data.py 产出；内嵌 run_id 与来源）
├── css/app.css                 # 样式（浅色主题；无 web font）
├── js/
│   ├── core.js                 #   数据图元 / 溯源抽屉 / 切屏 / 对比开关 / 通用图元
│   ├── charts.js               #   ECharts 封装（惰性初始化 + 切屏 resize）
│   ├── webgl3d.js              #   原生 WebGL 手写 651 星几何屏（零 3D 库）
│   └── screens.js              #   20 屏内容（全部由 data.js 驱动）
├── vendor/                     # 本地化第三方库（**不得引用 CDN**）
│   ├── echarts.min.js          #   图表：Apache-2.0（1,030,855 B）
│   └── CHECKSUMS.txt           #   字节数 + sha256 校验清单（入库前必核对）
├── assets/
│   ├── earth_blue_marble_1024x512.jpg   # 源素材（NASA Blue Marble，公有领域；LANCZOS 降采样）
│   └── earth_texture.js                 # 由上一行**生成**的 data: URI 副本（避免 file:// 跨源污染）
├── tools/                      # 数据生成 + 自检脚本（自包含）
│   ├── build_demo_data.py      #   由 results/ + 对照表 → data.js（含 4 类完整性断言）
│   ├── selfcheck_audit.js      #   自检 A：外部请求 / JS 错误 / 数字断言 / 独立副本 / 示意水印 / 渲染残缺
│   ├── selfcheck_shots.js      #   自检 B：逐屏遍历截图 + 每屏断言
│   ├── selfcheck_interact.js   #   自检 C：交互回归（切屏/对比开关/抽屉/动画/3D/图表落图）
│   └── fixtures/               #   自检工具自身的回归用例（3 个，含 2 个负向用例）
└── _selfcheck/                 # 自检产物（截图 + summary.txt，**gitignore，不入库**）
```

---

## 三、页面契约（写页面时必须遵守，自检脚本据此断言）

### 1. 数据图元（每个数字都要带出处）

```html
<span class="num" data-metric="切换总时延" data-value="12.06"
      data-golden="12.06" data-runid="wenchuan_s20260901_r1_20260922T070128Z">12.06 ms</span>
```

| 属性 | 含义 | 约束 |
|---|---|---|
| `data-metric` | 指标键 | 必须存在于 `window.DEMO_DATA.metrics` |
| `data-value`  | 页面显示值（纯数字） | 必须与 `DEMO_DATA.metrics[key]` **逐位相等**；由 `DEMO.n()` 与显示文本同源生成 |
| `data-golden` | **人工从对照表誊写的独立副本** | 必须与 `data-value` 及显示文本一致 —— 用于抓「指标键映射错位」这类自洽型错误 |
| `data-runid`  | 数据来源 run 标签 | 可放在祖先元素；缺失即自检失败 |

> **硬性约定（诚实红线）**：**数据图元带 `run_id`，示意图元带「示意」字样**。
> trace 只有事件级记录，逐条握手（msgA/msgB、msg1~msg4）、预迁移内部推送、星上调度决策**只能做机理示意**，
> 代码中以 `.schematic` 容器承载，且**容器内必须出现「示意」二字**（脚本断言）。

### 2. 屏幕与导航（逐屏截图用）

```html
<section class="screen" data-screen="S3">…</section>
<a class="nav" data-goto="S3">机制① 数据</a>
```

### 3. 数据结构（`data.js` 约定）

```js
window.DEMO_DATA = {
  generated_at, builder, repo_head, authority,
  metrics:  { "py/wenchuan_storm2/接入时延均值_ms": 193.44, … },   // 969 项，供 data-metric 断言
  runs:     { "py/wenchuan_storm2/接入时延均值_ms": "wenchuan_storm2_s20260901_r1_…", … },
  source:   { … : { file, section, note } },
  scenarios, funnel, timeline, crossval, two_vs_four, premig, t8, priority,
  sensitivity, multiseed, baseline_matrix, t3, resources, burst, coverage,
  constellation, limits, evidence, run_meta, counts
};
```

> 页面**不解析**任何 `results/*.json`、**不读**任何 CSV —— 只读 `data.js`；改数据 → 重跑脚本 → 刷新即可。

---

## 四、自检（每轮改动后必跑，三套）

```bash
WS="C:/Users/ASUS/.workbuddy/binaries/node/workspace"
NODE="C:/Users/ASUS/.workbuddy/binaries/node/versions/22.22.2/node.exe"
IDX="E:/pytorchFile/NationalCreation1/demo/index.html"     # 必须用 Windows 风格路径（Git Bash 的 $PWD 会解析错）

# A) 静态断言（机器判，退出码 0=PASS）
NODE_PATH="$WS/node_modules" "$NODE" demo/tools/selfcheck_audit.js "$IDX" demo/_selfcheck/audit.png

# B) 逐屏遍历截图（人看，产物在 demo/_selfcheck/）
NODE_PATH="$WS/node_modules" "$NODE" demo/tools/selfcheck_shots.js "$IDX" demo/_selfcheck

# C) 交互回归（切屏 / 对比开关 / 溯源抽屉 / 分步动画 / 3D 控件与像素 / 图表落图）
NODE_PATH="$WS/node_modules" "$NODE" demo/tools/selfcheck_interact.js "$IDX"

# D) 纯断网截图（验证「断网可放」）
"C:/Program Files/Google/Chrome/Application/chrome.exe" --headless --disable-gpu --hide-scrollbars \
  --host-resolver-rules="MAP * ~NOTFOUND" --window-size=1600,1100 \
  --screenshot="$IDX_OFFLINE" "file:///E:/pytorchFile/NationalCreation1/demo/index.html"
```

**判定三原则**（任一不过即 FAIL）：

1. **外部请求 = 0** → 保证断网可放、零 CDN（另加静态 `http(s)` 外链 = 0）
2. **JS 错误 = 0** → 保证不白屏
3. **页面数字逐字 == 权威数据** → 保证数据真实可溯源

**另加 5 项硬检查**：不得出现废弃值 `12.02` / `380.92`；每个数据图元必须带 `run_id`；
`data-golden` 独立副本必须双向一致；每个 `.schematic` 必须含「示意」；全 20 屏文本不得出现 `undefined` / `NaN`。

> **自检工具本身也要验证**：`tools/fixtures/` 下有 2 个**负向用例**（`contract_bad.html` / `golden_bad.html`），
> 它们**必须**被判 FAIL（退出码 1）—— 否则说明检查形同虚设。实测：4 项新检查全部抓出。

---

## 五、体积清单（上限 ≤ 4 MB，实测 ≈ 2.0 MB）

| 项 | 实测 |
|---|---|
| `vendor/echarts.min.js` | 1,030,855 B（1.03 MB）—— 页面**唯一**的第三方库 |
| `data.js` | 549,635 B（0.54 MB；含 651 星 12 帧坐标 172 KB + 事件回放 106 KB） |
| `assets/earth_texture.js`（生成） | 121,705 B |
| `assets/earth_blue_marble_1024x512.jpg`（源素材） | 91,042 B |
| `js/`（4 个文件） | ≈160 KB（screens 121 KB / webgl3d 18 KB / charts 13 KB / core 8 KB） |
| `css/` + `index.html` + `tools/` | ≈150 KB |
| **合计（不含 gitignore 的 `_selfcheck/`）** | **≈ 2.0 MB** |

**3D 依赖说明**：Three.js **不再随包分发**（`vendor/` 内已移除）。理由与恢复方式见
`vendor/CHECKSUMS.txt` —— 3D 最终采用**原生 WebGL 手写**，Three.js 会变成无人引用的 0.61 MB 死资产。

---

## 六、复现数据

```bash
# 用受管 venv（含 skyfield）
C:/Users/ASUS/.workbuddy/binaries/python/envs/default/Scripts/python.exe demo/tools/build_demo_data.py
```

脚本在写 `data.js` 之前执行 **4 类完整性断言**，任一不过即**拒绝生成**（退出码 1）：

| # | 断言 | 意义 |
|---|---|---|
| A | 对照表 §2 ↔ `results/{场景}_metrics.json` 逐位一致 | 文档口径与数据源不漂移 |
| B | 对照表 §3 ↔ ns-3 run `metrics.json` 逐位一致 | 同上（ns-3 轨） |
| C | trace 逐事件重算的 成功率/拦截率/漏斗守恒 ↔ `metrics.json` | 聚合指标可被事件级证据复算 |
| D | run `manifest.scenario_config` ↔ `sim/scenario.py` | 参数卡不是手抄的 |
| E | 全部指标值不含废弃值 `12.02` / `380.92` | 任务书旧值红线 |
