---
name: project-git-workflow
description: Use when the user explicitly asks to stage, commit, push, or run the repository's /git workflow. Do not use for ordinary code changes or read-only Git inspection.
---

# 项目 Git 工作流

## 规则来源与授权边界

从仓库根目录完整读取 `.trae/rules/git-workflow.md`。只有用户明确要求的 Git 写操作才被授权：要求提交不等于授权推送，要求推送不等于授权部署；`/git` 按项目约定表示提交并推送，但仍不得自动部署。

不得修改 Git 配置，不得执行 `push --force`、`reset --hard`、`checkout .`、`restore .`、`clean -f`、`branch -D` 或等价的破坏性命令。不要用 `git add -A`、`git add .` 或宽泛 glob 暂存未知变更。

## 执行流程

1. 运行 `git status --short --branch`，确认当前分支、上游和所有用户改动。
2. 使用 `git diff`、`git diff --cached` 检查实际内容；运行 `git log --oneline -5` 了解提交风格。
3. 确认相关验证已运行且结果新鲜。检查暂存范围中没有 `.env`、凭证、数据库、日志、缓存、大型产物或其他敏感文件。
4. 仅用 `git add <明确文件>` 暂存本次任务相关文件；再次检查 `git diff --cached`。
5. 提交信息简洁表达改动目的。未经用户明确要求，不修改或重写已有提交。
6. 只有用户要求推送时才推送。先用 `git branch -vv` 检查 upstream；没有 upstream 时使用当前分支的 `git push -u origin <branch>`。
7. 推送因远端更新失败时停止并报告。不要用 force push 绕过；需要 rebase 或合并时先评估工作区和冲突风险。

## 汇报

报告提交哈希、提交信息、分支、推送目标和验证结果。若用户输入 `/git`，完成后可以说明部署需另行明确授权，但不要自动触发部署。
