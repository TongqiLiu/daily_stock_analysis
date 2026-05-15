#!/bin/bash
# sync-and-push.sh
# 在推送到 origin 之前自动同步 upstream 的最新更改

set -e

UPSTREAM_REMOTE="upstream"
UPSTREAM_BRANCH="main"
ORIGIN_REMOTE="origin"

echo "🔄 开始同步 upstream..."

# 检查是否配置了 upstream
if ! git remote | grep -q "^${UPSTREAM_REMOTE}$"; then
    echo "❌ 未找到 upstream remote，正在添加..."
    read -p "请输入 upstream 仓库地址 (例: https://github.com/ZhuLinsen/daily_stock_analysis.git): " upstream_url
    git remote add ${UPSTREAM_REMOTE} "$upstream_url"
fi

# 获取当前分支
CURRENT_BRANCH=$(git branch --show-current)

if [ -z "$CURRENT_BRANCH" ]; then
    echo "❌ 无法确定当前分支"
    exit 1
fi

echo "📍 当前分支: $CURRENT_BRANCH"

# 检查是否有未提交的更改
if ! git diff-index --quiet HEAD --; then
    echo "❌ 有未提交的更改，请先提交或暂存"
    git status --short
    exit 1
fi

# Fetch upstream
echo "⬇️  正在拉取 upstream/$UPSTREAM_BRANCH..."
git fetch ${UPSTREAM_REMOTE}

# 检查是否有冲突
echo "🔍 检查是否有更新需要合并..."
MERGE_BASE=$(git merge-base HEAD ${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH})
if [ "$(git rev-parse ${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH})" = "$MERGE_BASE" ]; then
    echo "✅ upstream 无新更新，直接推送"
else
    echo "📦 upstream 有新提交，开始合并..."

    # 尝试自动合并
    if git merge ${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH} --no-edit; then
        echo "✅ 自动合并成功"
    else
        echo "❌ 合并冲突，请手动解决冲突后再运行此脚本"
        echo ""
        echo "解决步骤："
        echo "  1. 解决冲突文件"
        echo "  2. git add <conflicted-files>"
        echo "  3. git commit"
        echo "  4. 重新运行此脚本"
        exit 1
    fi
fi

# 推送到 origin
echo "⬆️  正在推送到 origin/$CURRENT_BRANCH..."
git push ${ORIGIN_REMOTE} ${CURRENT_BRANCH}

echo "✅ 同步并推送完成！"
echo ""
echo "📊 当前状态："
git log --oneline -5
