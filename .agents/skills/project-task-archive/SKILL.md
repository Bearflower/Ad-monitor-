---
name: project-task-archive
description: Use when the user explicitly asks to finish, close, persist, or archive the current repository task, including the project's /finish workflow. Do not use merely because coding work ended.
---

# 项目任务归档

## 规则来源与语义

从仓库根目录完整读取 `.trae/rules/task-management.md`，并检查当前存在的任务记录，例如 `task_plan.md`、`progress.md`、`findings.md` 和 `docs/` 下的工作流或计划文档。

旧规则中的 `task.md`、`.trae/memories/...`、`code-document-curator` 和 Trae 会话对象可能在当前仓库或 Codex 中不存在。将它们理解为任务登记、过程持久化和文档一致性职责：优先更新仓库现有结构；不得伪造工具调用，也不得在未检查现状时创建一套并行任务系统。

`/finish` 默认表示完成并归档仓库内的当前工作记录，不等于归档 Codex 应用中的任务。只有用户明确要求管理 Codex 任务时才执行应用层归档。

## 归档流程

1. 确认当前任务、验收条件和实际完成状态。验证未通过或仍有必需工作时，记录为进行中或受阻，不得标成完成。
2. 查找当前任务是否已登记。若已登记，更新其状态；若未登记且用户要求持久化，使用仓库现有任务文件格式新增最小记录。
3. 记录完成时间、目标、主要决策、修改文件、验证命令与结果、困难与解决方式、遗留问题和后续建议。
4. 检查行为、配置、API、部署或操作方式的变化是否需要更新相关 `docs/`；没有文档影响时明确记录。
5. 只有仓库已采用 `.trae/memories`，或用户明确要求继续该格式时，才按 `YYYY-MM/DD/HHmm-uuid.md` 写过程记录；否则使用现有 `task_plan.md`、`progress.md`、`findings.md` 或对应 `docs/` 文档。
6. 归档内容不得包含凭证、`.env` 值、客户隐私数据或不必要的运行日志。不要自动提交、推送、部署或删除工作文件。

## 完成输出

汇报更新了哪些任务与文档、验证状态、未完成事项，以及过程记录位置。若任务不能完成，说明阻塞条件并保持真实状态。
