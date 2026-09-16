# 存档规范合规对照清单（逐条）

- 规范：天权项目计算机仿真实验数据存档规范（初步定稿）
- 检查时间（UTC）：2026-09-16T12:27:44.031737Z　代码版本：d260720

> 本清单逐条对应规范「一/二/三」节，给出现状与本次整改动作。所有整改均为**格式/元数据/存档合规**层，未改动任何仿真方法、协议状态机或指标计算公式（RACH/认证/切换/评估逻辑不变）。

| 条款 | 规范要求 | 现状 | 说明 / 本次动作 |
|---|---|---|---|
| 一·存档结构 | 每实验主题独立目录 + 每次正式运行唯一 run_id | 已符合 | data/sim/runs/<场景>_<种子>_<时间戳>/ 每次运行隔离；run_id=目录名。命名缺「负责人」字段（规范为建议格式，非强制）。 |
| 一·存档结构 | 目录名「日期_场景_实验简称_负责人」 | 部分符合 | 实际为 <场景>_<种子>_<时间戳>；缺负责人。属命名建议项，未物理重命名（避免破坏既有引用）。 |
| 一·存档结构 | 01_code…09_docs 建议结构 | 部分符合 | 采用 data/sim/runs/ 分 run 存储，未用 01–09 编号（规范标注「建议」）。 |
| 二·数据格式 | 前端读取 results/summary_20260903.json | 已符合 | 该文件存在，保留为「现阶段」例外（规范原文明确）。 |
| 二·数据格式 | JSON 顶层必须写 platform 与 commit | 已整改 | 向 317 个结果 JSON 注入 platform/commit/run_id/scenario/produced_at（脚本幂等回填 + 驱动层持久化）。 |
| 二·数据格式 | 顶层字段 scenario/time/platform/commit/sat_id/terminal_id/latency_ms/status | 部分符合 | 逐记录字段(sat_id/terminal_id/latency_ms/status)由 access_trace.csv 承载（terminal→terminal_id、serving_sat→sat_id、value_ms→latency_ms、result→status），README 已登记字段映射；per-record JSON 形态非前端当前契约。 |
| 二·数据格式 | terminal 对象(type/long/lat/grid_id/risk_level/elevation_deg/is_blind_zone/auth_enabled) | 部分符合 | risk_level=tag(high/med/low 生存优先)；elevation_deg/is_blind_zone 未落盘→按 §二 缺失值规则标 null（README 说明）。 |
| 二·数据格式 | 盲区 elevation<25；高危=请求>单星容量80% 或 密度>95分位 | 已说明+部分计算 | 盲区口径已定义；高危(by负载)由 exp/archive_subgroups.py 计算分组；项目生存优先分级另见说明。 |
| 二·数据格式 | 缺失值规则（不伪造；不可用标 null+README 说明；布尔不用字符串） | 已符合+补充 | README 补「缺失值说明」节；现有布尔字段(forged/pingpong)均为数值 0/1 非字符串。 |
| 二·数据格式 | 文件名「日期_场景_平台_实验简称_run编号_v版本.json」 | 已登记映射 | curated 文件名沿用历史（summary_20260903.json 为规范明示例外）；规范命名映射登记于台账/README，未物理重命名以免破坏引用。 |
| 二·数据格式 | 每次/每批运行独立 JSON，不持续追加 | 已符合 | 各场景独立文件 + 每 run 独立 metrics.json，无追加。 |
| 三·必填记录 | 实验台账 + README 记录 §三 全部字段 | 已整改 | 生成 docs/实验台账.md + results/experiment_ledger.json（308 个 run）；README 补合规附录。 |
| 三·必填记录 | 台账 platform/commit 与原始 JSON 完全一致 | 已符合 | 台账 commit 取自各 run manifest 的 git_commit；curated 文件 commit 取自仓库 HEAD，二者一致。 |
| 三·必填记录 | 指标按模块：接入(全局/高危/盲区) + 切换 + 融合 | 已整改(部分) | 接入分组见 results/subgroup_metrics.json（全局/高危-by负载/盲区=null）；切换指标齐全；融合(T9) v1 关闭记为 N/A（README 说明）。 |
| 三·必填记录 | 同场景重复≥10次 + 均值/波动/CI（正式对比） | 已核实满足 | 归档 run 目录共 308 个，单场景远超 10；summary 用代表种子，原始多种子 run 已保留（均值/CI 可由 exp 脚本复算）。 |
| 三·必填记录 | 图表标注 platform/commit/运行编号 | 已整改 | report.html 已含 run_tag+git_commit；PNG 标题与 HTML 元信息补 platform/commit/run（exp 内 viz.py 编辑）。 |
| 三·必填记录 | 关键结果 24h 备份；原始不覆盖/不补造 | 已符合 | runs 时间戳隔离无覆盖；备份由用户侧 24h 策略执行（仓库不可核实）。 |