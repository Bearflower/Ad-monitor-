# Adwatch macOS 调度手册

## 范围

当前工具只管理一个用户级 LaunchAgent：

```text
com.adwatch.daily
```

它在 `Asia/Shanghai` 每天 09:00 执行当前项目的 `adwatch run daily`。
`RunAtLoad=true` 允许登录后补触发；应用自身的报告日成功记录和本机互斥锁负责
幂等与并发保护。不存在 weekly 或 monthly 任务。

安装是独立授权操作。普通开发、`init`、`collect`、`run daily` 和 dashboard
都不会安装或修改 LaunchAgent。

## 前置检查

1. 在当前项目根目录创建并安装 `.venv/bin/adwatch`。
2. Mac 系统时区和 `.env` 中 `ADWATCH_TIMEZONE` 均为 `Asia/Shanghai`。
3. `ADWATCH_DAILY_RUN_TIME=09:00`。
4. `.env` 已配置 `ZINIAO_SHOPEE_STORE_ID`。
5. 紫鸟客户端已登录，`ziniao-cli store list` 能看到对应 Shopee 店铺。
6. `.venv/bin/adwatch doctor` 的六项检查全部通过，其中 `shopee_page` 会执行只读页面探针。

调度会设置确定性 PATH，不继承登录 shell 的 PATH：

```text
<project>/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin
```

因此 `ziniao-cli` 必须位于上述目录之一。

## 查询状态

查询只读取 plist 和 launchd 状态，不安装或卸载任务：

```bash
.venv/bin/adwatch schedule status
```

可能输出：

```text
schedule=healthy
schedule=not_installed
schedule=unhealthy
schedule=failed
```

未安装或不健康时命令返回非零。底层命令输出和本机敏感路径不会进入公开错误。

## 安装

取得用户对本次安装的明确授权后，在项目根目录执行：

```bash
.venv/bin/adwatch schedule install
```

安装顺序：

1. 校验 09:00、上海时区、项目可执行文件和 doctor。
2. 检查 `~/Library/LaunchAgents/com.adwatch.daily.plist` 与 launchd 状态。
3. 原子写入只含当前绝对路径、`run daily`、日志路径和固定环境的 plist。
4. 若标签被 disabled，在 bootstrap 前显式 enable。
5. bootstrap 到当前 `gui/<uid>` 域并再次验证。

健康的重复安装不会再次 bootstrap。若本次写入后 bootstrap 失败，新建 plist 会被
清理；覆盖已有文件时会恢复原内容，便于人工恢复。

成功输出：

```text
schedule=installed
```

## 卸载

取得用户对本次卸载的明确授权后执行：

```bash
.venv/bin/adwatch schedule uninstall
```

卸载只处理内容与当前期望值精确一致的
`com.adwatch.daily.plist`。未知、损坏或漂移的 plist 会被拒绝，避免删除其他任务。
识别成功后，程序先把原字节备份到：

```text
var/backups/launchagent/
```

随后才执行 bootout 和删除。bootout 失败时保留 plist 与备份。

成功输出：

```text
schedule=uninstalled
```

## 每日执行与补跑

09:00 任务依次执行：获取互斥锁、打开或复用 Shopee 会话、回填缺口、检查数据
质量、评估反馈、生成建议、写本地日报和运行日志。报告日为触发当天，分析数据日
为报告日前一日。

同一报告日已有成功记录时直接幂等退出。采集或分析失败不会覆盖上一版日报；跨
活动建议在一个事务中保存，不会留下半批新建议。下次触发可以安全重试。

需要人工指定报告日时：

```bash
.venv/bin/adwatch run daily --date 2026-09-11
```

单独补采某个数据日时：

```bash
.venv/bin/adwatch collect --date 2026-09-10
```

命令固定为 Shopee，不接受 `--mode`、通知或广告写入参数。

## 排障

先运行：

```bash
.venv/bin/adwatch doctor
.venv/bin/adwatch schedule status
```

本地证据位于：

```text
var/adwatch.sqlite3
var/reports/shopee-ads-YYYY-MM-DD.md
var/logs/launchd.out.log
var/logs/launchd.err.log
var/backups/launchagent/
```

- `not_installed`：确认已获得安装授权后再执行 install。
- `unhealthy`：检查时区、plist 漂移、disabled 和 loaded 状态；健康配置可重复
  执行 install 修复。
- `failed`：先处理 doctor 中失败的运行时、数据库、ziniao-cli、店铺或端口检查。
- 紫鸟登录失效：在紫鸟客户端重新登录目标 Shopee 店铺，再重新运行每日任务。

不要手工删除不认识的 plist，也不要用旧项目路径运行调度命令。
