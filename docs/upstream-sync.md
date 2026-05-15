# Upstream 同步指南

本文档说明如何保持你的 fork 与上游仓库 (ZhuLinsen/daily_stock_analysis) 同步。

## 方案一：使用同步脚本（推荐）

每次推送前自动同步 upstream：

```bash
# 替代 git push origin main
./scripts/sync-and-push.sh
```

**脚本功能**：
1. ✅ 自动检查 upstream 是否有更新
2. ✅ 自动合并 upstream/main 到当前分支
3. ✅ 合并成功后自动推送到 origin
4. ⚠️ 如有冲突，会提示手动解决

**首次使用**：
```bash
# 确保已添加 upstream remote
git remote add upstream https://github.com/ZhuLinsen/daily_stock_analysis.git

# 使用脚本推送
./scripts/sync-and-push.sh
```

## 方案二：GitHub Actions 自动同步

### 功能
- 📅 **定期同步**：每周一和周四自动检查 upstream 更新
- 🤖 **自动 PR**：发现更新后自动创建 PR（无冲突时）
- ⚠️ **冲突提醒**：有冲突时创建 issue 提醒

### 手动触发
在 GitHub 仓库页面：
1. 进入 **Actions** 标签
2. 选择 **Sync Upstream** 工作流
3. 点击 **Run workflow**

### 工作流行为
```
upstream 有更新 → 创建 sync-upstream-{timestamp} 分支
                 ↓
           尝试自动合并
                 ↓
    ┌────────────┴────────────┐
    ↓                         ↓
 无冲突                    有冲突
    ↓                         ↓
自动创建 PR              创建 issue 提醒
```

## 方案三：手动同步

```bash
# 1. 拉取 upstream
git fetch upstream
git merge upstream/main

# 2. 解决冲突（如有）
git add .
git commit

# 3. 推送到 origin
git push origin main
```

## 配置 upstream remote

**查看当前 remote**：
```bash
git remote -v
```

**添加 upstream**：
```bash
git remote add upstream https://github.com/ZhuLinsen/daily_stock_analysis.git
```

**验证配置**：
```bash
git fetch upstream
git log --oneline -5 upstream/main
```

## 冲突解决流程

### 使用脚本时遇到冲突
```bash
./scripts/sync-and-push.sh
# ❌ 合并冲突，请手动解决冲突后再运行此脚本

# 1. 查看冲突文件
git status

# 2. 手动编辑冲突文件，解决 <<<<<<< HEAD 标记

# 3. 标记为已解决
git add <conflicted-files>
git commit

# 4. 重新运行脚本
./scripts/sync-and-push.sh
```

### 常见冲突场景

#### 配置文件冲突 (.env.example, config_registry.py)
- **策略**：优先保留 upstream 版本，再手动添加本地特有配置
- **命令**：`git checkout --theirs <file>` 然后手动调整

#### 功能代码冲突 (src/, api/)
- **策略**：逐块审查，确保本地改动不丢失
- **工具**：使用 VS Code / IDE 的三方合并工具

#### 文档冲突 (README.md, docs/)
- **策略**：合并双方内容，保持完整性

## 最佳实践

### 推送前同步
```bash
# 养成习惯：推送前先同步
./scripts/sync-and-push.sh
```

### 定期检查
```bash
# 每周查看 upstream 更新
git fetch upstream
git log --oneline main..upstream/main
```

### 分支工作流
```bash
# 本地开发使用分支
git checkout -b feature/my-feature
# 开发完成后再同步 main
git checkout main
./scripts/sync-and-push.sh
```

## 验证同步状态

```bash
# 查看本地与 upstream 的差异
git fetch upstream
git log --oneline --graph --all --decorate -10

# 检查是否有未合并的 upstream 提交
git log main..upstream/main

# 检查是否有未推送的本地提交
git log origin/main..main
```

## 故障排查

### 问题：脚本报错 "未找到 upstream remote"
```bash
git remote add upstream https://github.com/ZhuLinsen/daily_stock_analysis.git
```

### 问题：GitHub Actions 没有创建 PR
**可能原因**：
1. upstream 无新提交
2. 合并有冲突（会创建 issue）
3. GITHUB_TOKEN 权限不足

**检查**：
```bash
gh run list --workflow=sync-upstream.yml --limit 5
gh run view <run-id> --log
```

### 问题：自动 PR 包含不想要的提交
**解决**：在 PR 中 review，选择性合并或关闭 PR 后手动同步

## 配置调整

### 修改自动同步频率
编辑 `.github/workflows/sync-upstream.yml`：
```yaml
schedule:
  # 改为每天运行
  - cron: '0 2 * * *'
```

### 修改 upstream 地址
编辑 `scripts/sync-and-push.sh` 中的 `UPSTREAM_REMOTE` 和 `UPSTREAM_BRANCH`

## 相关文件

- `scripts/sync-and-push.sh` - 同步推送脚本
- `.github/workflows/sync-upstream.yml` - 自动同步工作流
- `AGENTS.md` - 仓库协作规则
