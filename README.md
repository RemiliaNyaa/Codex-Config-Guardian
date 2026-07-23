# Codex Config Guardian

保护 Codex `config.toml` 中的用户设置不被 cc-switch 全量覆盖。

> **适用版本：cc-switch v3.17.0**
>
> 本脚本的检测机制依赖 cc-switch 的特定标记（`PROXY_MANAGED`、本地代理端口 `127.0.0.1:1572`、代理字段白名单等）。不同版本的 cc-switch 可能使用不同的标记或字段，如遇版本更新导致失效，需相应调整 `PROXY_MANAGED_TOP_KEYS`、`PROXY_MARKERS` 等配置。

## 问题背景

cc-switch 在代理接管 / 热切换 / 完整重启时，会使用数据库中的 provider 模板**全量重写** `~/.codex/config.toml`，导致以下用户自定义内容丢失：

- `[desktop]` 段（`sansFontSize`、`selected-avatar-id`、`followUpQueueMode` 等）
- `[features]` 段（`memories = true` 等）
- `[memories]` 段（`generate_memories`、`use_memories`）
- 顶层字段 `personality`
- 其他不在 provider 模板中的段

Guardian 脚本在后台监视 `config.toml`，当检测到 cc-switch 全量覆盖后，自动将丢失的用户段合并回去，同时保留代理字段不变。

## 使用方法

### 查看状态

```bash
python codex_config_guardian.py --status
```

### 手动触发一次检查

```bash
python codex_config_guardian.py --once -v
```

### 守护模式（持续后台运行）

```bash
python codex_config_guardian.py
```

### 详细日志

```bash
python codex_config_guardian.py -v          # 守护模式 + 详细日志
python codex_config_guardian.py --once -v   # 单次检查 + 详细日志
```

### 开机自启

已通过启动文件夹快捷方式实现开机自启，正常无需手动操作。

快捷方式直接指向 `pythonw.exe`（无窗口运行），无需 VBS 中转，兼容中文路径。

快捷方式位置：

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Codex Config Guardian.lnk
```

如需手动启动，在 PowerShell 中执行：

```powershell
Start-Process "E:\DevTools\Python\versions\cpython-3.12-windows-x86_64-none\pythonw.exe" -ArgumentList '"e:\RemiliaNyaa的本地文件库\项目\我的项目\修复cc-switch全量覆盖Codex_config\codex_config_guardian.py"'
```

## 文件默认保存位置

### Guardian 自身文件

| 文件 | 路径 |
|------|------|
| 守护脚本 | `e:\RemiliaNyaa的本地文件库\项目\我的项目\修复cc-switch全量覆盖Codex_config\codex_config_guardian.py` |
| 开机自启快捷方式 | `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Codex Config Guardian.lnk` |

### 运行时文件（自动生成）

所有运行时文件位于 `~/.codex/.config-guardian/` 目录下：

| 文件 | 路径 | 说明 |
|------|------|------|
| 用户配置快照 | `~/.codex/.config-guardian/user_baseline.toml` | 代理未激活时从 config.toml 生成，剥离代理字段后保存。合并时以此为准恢复用户段 |
| 日志 | `~/.codex/.config-guardian/guardian.log` | 运行日志，记录每次检查 / 合并 / 备份操作 |
| 配置备份 | `~/.codex/.config-guardian/backups/config_YYYYMMDD_HHMMSS.toml` | 每次合并前自动备份 config.toml，保留最近 20 份 |

> `~` 代表用户目录，即 `C:\Users\<用户名>`。

### 受保护的配置文件

| 文件 | 路径 | 说明 |
|------|------|------|
| Codex 配置 | `~/.codex/config.toml` | Guardian 监视和合并的目标文件 |

## 合并策略

| 分类 | 处理方式 | 示例字段 |
|------|---------|---------|
| 代理字段 | **保留 cc-switch 的值，不动** | `model_provider`、`model`、`model_reasoning_effort`、`disable_response_storage`、`experimental_bearer_token`、`[model_providers]` |
| MCP 服务器 | **只补缺失的，不动已有的** | `[mcp_servers.*]`（保留 Codex 动态写入的管道路径等） |
| 其他所有段 | **baseline 优先（深度合并）** | `[desktop]`、`[features]`、`[memories]`、`personality`、`[plugins]`、`[shell_environment_policy]`、`[marketplaces]`、`[windows]` 等 |

## cc-switch 的配置机制

cc-switch 的代理接管流程如下：

```
接管前:  保存当前 config.toml 到数据库 (proxy_live_backup 表)
接管时:  用 provider 模板全量重写 config.toml（注入代理地址 + PROXY_MANAGED）
退出时:  从数据库备份恢复 config.toml，然后删除备份
```

从 cc-switch 日志确认的实际流程：

| 时机 | 动作 | 日志原文 |
|------|------|---------|
| 启动 | 检测到接管残留，重新接管 | `codex 标记为已接管，但 backup=false，正在重新接管并补齐 Live` |
| 接管 | 备份当前配置到 DB | `已备份 codex Live 配置` |
| 接管 | 用模板重写 config.toml | `Codex Live 配置已接管，代理地址: http://127.0.0.1:15722/v1` |
| 退出 | 从备份恢复 | `codex Live 配置已从备份恢复` |
| 退出 | 删除备份 | `已删除所有 Live 配置备份` |

问题出在"接管时"这步——它用的是数据库里 provider 模板（`providers.settings_config.config`），而不是基于刚备份的当前配置做增量注入。模板里没有 `[desktop]`、`[memories]` 等段，所以这些段丢失。热切换时更严重——备份本身也是从模板重建的，所以退出时"恢复"的也是不完整的配置。

## 工作流程

```
代理未激活时:
  config.toml（用户真实配置）-> 剥离代理字段 -> 保存为 baseline

cc-switch 全量覆盖后:
  config.toml（代理配置，用户字段缺失）
    -> 检测到 personality / [memories] 等字段消失
    -> 从 baseline 合并用户段回去（代理字段不动）
    -> 写回 config.toml
    -> 更新 baseline（捕获最新状态）
```

## Guardian 如何处理两种配置状态

cc-switch 和 Guardian 都开机自启，config.toml 可能处于官方配置或代理覆盖配置两种状态。Guardian 每 2 秒轮询一次，自动适配：

| 状态 | Guardian 行为 | 是否修改 config.toml |
|------|-------------|-------------------|
| 代理未激活（官方配置） | 读取当前配置，剥离代理字段，更新 baseline | 否，只读不写 |
| 代理激活 + 用户字段齐全 | 正常状态，更新 baseline 捕获最新设置 | 否，只读不写 |
| 代理激活 + 用户字段缺失 | cc-switch 刚覆盖了 -> 合并 baseline 恢复用户段 | 是，合并写回 |

开机时无论谁先启动都能处理：

- cc-switch 先启动（覆盖了配置）-> Guardian 启动后首次检查就发现字段缺失 -> 立即合并恢复
- Guardian 先启动 -> 先更新 baseline -> cc-switch 随后覆盖 -> 下一次轮询（2 秒内）检测到并合并

## 什么时候修改 Codex 配置

**两种状态下都能改，改动都会保留。**

baseline 相当于保存的是最新版本的用户配置快照。每次 config.toml 处于良好状态（用户字段齐全）时，Guardian 都会更新 baseline，所以它始终保存的是最新的。

Guardian 通过检测 `PROXY_MANAGED` 标记区分官方配置 / 覆盖配置，但无论哪种状态，只要用户字段齐全就会更新 baseline。合并时只保留公共部分（用户管理的段），代理字段始终跳过不动。

### 在官方配置下修改（代理未激活）

```
你改 config.toml -> Guardian 检测到变化 -> 代理未激活 -> 更新 baseline
之后 cc-switch 接管覆盖 -> Guardian 从 baseline 恢复 -> 你的改动在
```

### 在代理激活状态下修改（如通过 Codex UI 改字号、改 desktop 设置）

```
Codex 写入 config.toml -> Guardian 检测到变化 -> 代理激活但字段齐全 -> 更新 baseline
之后 cc-switch 再次覆盖 -> Guardian 从 baseline 恢复 -> 你的改动在
```

两种情况下 Guardian 都会把你最新的改动同步到 baseline，下次 cc-switch 覆盖时都能恢复。

唯一需要注意的：不要在 cc-switch 正在覆盖的那一瞬间改配置（这个窗口不到 1 秒，正常操作碰不到）。如果担心，在官方配置下改是最干净的——那时 config.toml 就是纯原始配置，没有任何代理字段。

## 依赖

- Python 3.12+
- tomlkit（`pip install tomlkit`）

## 停止守护进程

在任务管理器中结束 `pythonw.exe` 进程即可。或在 PowerShell 中：

```powershell
Get-Process pythonw | Stop-Process -Force
```
