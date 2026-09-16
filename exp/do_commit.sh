#!/usr/bin/env bash
# =============================================================================
# 一键提交脚本（可选，非必须）：T2 切换基线矩阵（5 基线 + 1 消融臂）
#                          + T3 接入方案对比（3 方案 × 3 场景）—— Python / ns-3 双轨
#
# 用法（在仓库根或任意位置皆可）：
#   bash exp/do_commit.sh              # 暂存 + 提交 + 推送
#   bash exp/do_commit.sh --no-push    # 仅暂存 + 提交，不推送
#
# 若改动已由他人提交完毕，脚本会检测到「索引与 HEAD 无差异」并**自动跳过提交**。
#
# 设计要点（★纯 git / POSIX sh 实现，不依赖 python★）：
#   · 只暂存【本次相关工作】，不含 perf/ 下无关的未跟踪审计脚本；
#   · 自动创建独立分支（默认 feat/t2-t3-baseline-matrix），不直接动 main；
#   · ★行尾安全★：本仓库 main 的文本文件为 LF，而本机 core.autocrlf=true。
#     脚本用 `git add --renormalize` 强制归一，并做三重校验：
#       ① 无删除项；② 普通 --stat 与 --ignore-cr-at-eol --stat 完全一致；③ 暂存 blob 无 CR。
#     任一不过即中止，避免出现「整篇文件被判为已替换」的错误提交（2026-09-16 曾踩坑）。
# =============================================================================
set -e
cd "$(dirname "$0")/.."          # -> 仓库根目录

BRANCH="feat/t2-t3-baseline-matrix"
BASE="main"                      # 以 main 为基线（保留其全部既有文件，如 docs/references/joe_papers）
MSG="feat(T2/T3): 切换基线矩阵(含消融臂/归因更正) + 接入方案3场景隔离对照(双轨); 新增工作隔离于 exp/"

# 本次相关的既有文件（就地修改）+ 新增文件
FILES=(
  sim/protocol.py
  sim/eval.py
  sim/config.py
  sim/scenario.py
  sim/orbit.py
  sim/ns3_io.py
  .ns3_ref/leo_access.cc
  run_sim.py
  run_ns3.py
  docs/技术决策确认.md
  docs/学术基线对照.md
  docs/仿真接口约定.md
  docs/双轨交叉验证对照表.md
  README.md
  exp
  perf/crossval_ho.py
  perf/crossval_ho_table.txt
  perf/crossval_ho_results.json
)

echo "==> 当前分支：$(git rev-parse --abbrev-ref HEAD)"
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  echo "==> 切换到已存在分支 $BRANCH"
  git checkout "$BRANCH"
else
  echo "==> 从 $BASE 创建并切换到新分支 $BRANCH"
  git checkout -b "$BRANCH" "$BASE"
fi

echo "==> 暂存本次相关文件（含行尾归一）"
git add -A -- "${FILES[@]}"
git add --renormalize -- "${FILES[@]}"

echo "==> 行尾安全校验（仅用 git，无 python）"
n_del=$(git diff --cached --diff-filter=D --name-only | wc -l | tr -d ' ')
if [ "$n_del" != "0" ]; then
  echo "    ✗ 检出 $n_del 个删除项（可能索引未以 $BASE 为基）→ 中止"
  git diff --cached --diff-filter=D --name-status
  exit 1
fi
s_plain=$(git diff --cached --stat "$BASE" | tail -1)
s_ignore=$(git diff --cached --ignore-cr-at-eol --stat "$BASE" | tail -1)
if [ "$s_plain" != "$s_ignore" ]; then
  echo "    ✗ 检出到行尾噪声（CRLF 未归一）→ 中止"
  echo "      普通     : $s_plain"
  echo "      忽略行尾 : $s_ignore"
  echo "      请检查 core.autocrlf 或是否漏跑 --renormalize"
  exit 1
fi
bad=""
for f in $(git diff --cached --name-only "$BASE"); do
  if git cat-file blob ":$f" 2>/dev/null | grep -q $'\r'; then
    bad="$bad $f"
  fi
done
if [ -n "$bad" ]; then
  echo "    ✗ 以下暂存文件含 CRLF：$bad → 中止"
  exit 1
fi
echo "    ✓ 无删除项 · 行尾一致 · 暂存 blob 全为 LF"

echo "==> 待提交清单"
git status --short

if git diff --cached --quiet HEAD 2>/dev/null; then
  echo "==> 索引与 HEAD 无差异（改动已提交），跳过提交"
else
  echo "==> 提交"
  git commit -m "$MSG"
fi

echo
if [ "$1" = "--no-push" ]; then
  echo "✅ 本地提交完成（已跳过推送）。下一步：git push -u origin $BRANCH"
else
  echo "==> 推送到远端"
  git push -u origin "$BRANCH"
  echo
  echo "✅ 全部完成：已提交并推送到 origin/$BRANCH"
fi
