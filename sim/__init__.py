"""低轨卫星-地面融合组网快速接入系统 · 模块化仿真骨架。

分层（对应 AGENTS.md 一·B）：
  L1 scene/orbit  -> scenario.py + orbit.py
  L2 channel      -> channel.py
  L3 protocol     -> protocol.py   （★SWAP POINT★ 后续整体换 ns-3）
  L4 eval/interface -> eval.py + interfaces.py
  L5 viz          -> viz.py        （后续换 Web/Three.js）

每个文件顶部标注 REPLACEABLE / SWAP POINT，便于单独替换或扩展，不影响其他层。
数据来自真实抓取/真实计算；设备/协议层参数为 config.py 与 channel.py 中的显式建模假设
（常量溯源表内嵌于 HTML 报告，可审计）。
"""
