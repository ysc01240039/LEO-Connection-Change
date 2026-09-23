#!/usr/bin/env bash
# =============================================================================
# 一键提交脚本（可选，非必须）：直接提交到 main
#
# ★ 2026-09-22 起改为「直推 main」：不再创建特性分支 / 不再走 PR。
#   原因：云端多会话协同下，分支 + PR 流程不便；统一直接落 main 最简单。
#
# 用法（在仓库根或任意位置皆可）：
#   bash exp/do_commit.sh                   # 同步最新(pull --rebase) + 暂存 + 提交 + 推送 main
#   bash exp/do_commit.sh --no-push         # 仅本地提交，不推送
#   bash exp/do_commit.sh --no-sync         # 不先 pull（离线时用）
#   bash exp/do_commit.sh sim/foo.py results/bar.json   # 追加暂存指定文件
#   MSG="feat: ..." bash exp/do_commit.sh   # 自定义提交信息
#
# 若改动已由他人提交完毕，脚本检测到「索引与 HEAD 无差异」会**自动跳过提交**。
#
# 设计要点（★纯 git / POSIX sh 实现，不依赖 python★）：
#   · 只暂存【本次相关工作】（FILES 列表 + 命令行追加路径），
#     不含 perf/ 下无关的未跟踪审计脚本；
#   · 直接落到 main（不再创建特性分支）；
#   · 提交前先 pull --rebase origin main 保持与 GitHub 最新一致（best-effort）；
#   · ★行尾安全★：仓库 main 文本文件为 LF，本机 core.autocrlf=true。
#     用 `git add --renormalize` 强制归一 + 三重校验（无删除项 /
#     普通与忽略CR的 --stat 一致 / 暂存 blob 无 CR）。任一不过即中止，
#     避免出现「整篇文件被判为已替换」的错误提交（2026-09-16 曾踩坑）。
# =============================================================================
set -e
cd "$(dirname "$0")/.."          # -> 仓库根目录

# ---- 参数解析 ----
PUSH=1
SYNC=1
EXTRA=()
for a in "$@"; do
  case "$a" in
    --no-push) PUSH=0 ;;
    --no-sync) SYNC=0 ;;
    --*)       echo "未知参数: $a" >&2; exit 2 ;;
    *)         EXTRA+=("$a") ;;
  esac
done
MSG="${MSG:-feat: 双轨仿真例行更新（直接落 main）}"

# ---- 切到 main（若已在 main 则跳过）----
cur=$(git rev-parse --abbrev-ref HEAD)
echo "==> 当前分支：$cur"
if [ "$cur" != "main" ]; then
  echo "==> 切换到 main"
  git switch main
fi

# ---- 同步 GitHub 最新（best-effort，失败不阻断）----
if [ "$SYNC" = "1" ]; then
  echo "==> 同步 origin/main 最新（pull --rebase，best-effort）"
  git -c http.schannelCheckRevoke=false pull --rebase origin main \
    || echo "    ⚠ pull 失败（可能离线/凭据受限），继续使用本地 main"
fi

# ---- 本次相关文件（就地修改）+ 新增文件 ----
BASE_FILES=(
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
FILES=("${BASE_FILES[@]}" "${EXTRA[@]}")

echo "==> 暂存本次相关文件（含行尾归一）"
git add -A -- "${FILES[@]}"
git add --renormalize -- "${FILES[@]}"

echo "==> 行尾安全校验（仅用 git，无 python）"
n_del=$(git diff --cached --diff-filter=D --name-only | wc -l | tr -d ' ')
if [ "$n_del" != "0" ]; then
  echo "    ✗ 检出 $n_del 个删除项 → 中止"
  git diff --cached --diff-filter=D --name-status
  exit 1
fi
s_plain=$(git diff --cached --stat HEAD | tail -1)
s_ignore=$(git diff --cached --ignore-cr-at-eol --stat HEAD | tail -1)
if [ "$s_plain" != "$s_ignore" ]; then
  echo "    ✗ 检出到行尾噪声（CRLF 未归一）→ 中止"
  echo "      普通     : $s_plain"
  echo "      忽略行尾 : $s_ignore"
  echo "      请检查 core.autocrlf 或是否漏跑 --renormalize"
  exit 1
fi
bad=""
for f in $(git diff --cached --name-only HEAD); do
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
  echo "==> 提交到 main"
  git commit -m "$MSG"
fi

echo
if [ "$PUSH" = "0" ]; then
  echo "✅ 本地提交完成（已跳过推送）。下一步：git push origin main"
else
  echo "==> 推送到 origin/main"
  git -c http.schannelCheckRevoke=false push origin main
  echo
  echo "✅ 完成：已提交并推送到 origin/main"
fi
