"""低轨卫星-地面融合组网快速接入系统 · 模块化仿真骨架。

分层（对应 AGENTS.md 一·B）：
  L1 scene/orbit  -> scenario.py + orbit.py
  L2 channel      -> channel.py
  L3 protocol     -> protocol.py   （★SWAP POINT★ 后续整体换 ns-3）
  L4 eval/interface -> eval.py + interfaces.py
  L5 viz          -> viz.py        （后续换 Web/Three.js）

每个文件顶部标注 REPLACEABLE / SWAP POINT，便于单独替换或扩展，不影响其他层。
所有数据来自真实抓取/真实计算，无硬编码；建模假设集中在 config.py 与 channel.py。
"""
