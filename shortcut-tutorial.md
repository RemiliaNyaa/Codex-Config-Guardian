# Windows 快捷方式（.lnk）教程

## 什么是快捷方式

快捷方式（`.lnk`）是 Windows 的一个特殊文件，它指向另一个程序或文件。双击快捷方式等同于执行它指向的目标。

与其他启动方式（VBS、批处理）不同，快捷方式**原生支持 Unicode**，中文路径不会有编码问题。

## 快捷方式的核心属性

| 属性 | 说明 | 示例 |
|------|------|------|
| **目标（TargetPath）** | 要运行的程序路径 | `E:\...\pythonw.exe` |
| **参数（Arguments）** | 传给程序的命令行参数 | `"e:\...\script.py"` |
| **起始位置（WorkingDirectory）** | 程序的工作目录 | `e:\...\项目目录` |
| **运行方式（WindowStyle）** | 窗口显示方式 | `1` 普通 / `3` 最大化 / `7` 最小化 |

> 目标是程序本体，参数是告诉程序要做什么。比如目标是 pythonw.exe，参数是脚本路径，合起来就是"用 Python 运行这个脚本"。

## 手动创建快捷方式

1. 右键桌面或文件夹 -> **新建** -> **快捷方式**
2. 输入目标位置（程序路径 + 参数）：
   ```
   "E:\DevTools\Python\versions\cpython-3.12-windows-x86_64-none\pythonw.exe" "e:\你的脚本路径\script.py"
   ```
3. 点击下一步，输入名称，完成
4. 右键新建的快捷方式 -> **属性**，可修改起始位置、运行方式等

## 用 PowerShell 创建快捷方式

```powershell
# 1. 创建 WScript.Shell COM 对象
$WshShell = New-Object -ComObject WScript.Shell

# 2. 指定快捷方式保存路径
$shortcut = $WshShell.CreateShortcut("C:\路径\你的快捷方式.lnk")

# 3. 设置属性
$shortcut.TargetPath = "E:\DevTools\Python\versions\cpython-3.12-windows-x86_64-none\pythonw.exe"
$shortcut.Arguments = '"e:\你的脚本路径\script.py"'
$shortcut.WorkingDirectory = "e:\你的脚本目录"
$shortcut.WindowStyle = 7  # 1=普通 3=最大化 7=最小化
$shortcut.Description = "快捷方式说明"

# 4. 保存
$shortcut.Save()
```

## 实现开机自启

把快捷方式放到 Windows 启动文件夹即可：

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
```

开机登录后，Windows 会自动执行这个文件夹里的所有快捷方式。

### 用 PowerShell 自动放到启动文件夹

```powershell
$WshShell = New-Object -ComObject WScript.Shell
$startup = [System.Environment]::GetFolderPath('Startup')

$shortcut = $WshShell.CreateShortcut("$startup\我的程序.lnk")
$shortcut.TargetPath = "E:\程序路径\program.exe"
$shortcut.Arguments = '"参数"'
$shortcut.WindowStyle = 7
$shortcut.Save()

Write-Host "快捷方式已创建: $startup\我的程序.lnk"
```

## 实际示例：Codex Config Guardian

以本项目的守护脚本为例：

```powershell
$WshShell = New-Object -ComObject WScript.Shell
$startup = [System.Environment]::GetFolderPath('Startup')

$shortcut = $WshShell.CreateShortcut("$startup\Codex Config Guardian.lnk")
$shortcut.TargetPath = "E:\DevTools\Python\versions\cpython-3.12-windows-x86_64-none\pythonw.exe"
$shortcut.Arguments = '"e:\RemiliaNyaa的本地文件库\项目\我的项目\修复cc-switch全量覆盖Codex_config\codex_config_guardian.py"'
$shortcut.WorkingDirectory = "e:\RemiliaNyaa的本地文件库\项目\我的项目\修复cc-switch全量覆盖Codex_config"
$shortcut.WindowStyle = 7
$shortcut.Description = "Codex Config Guardian"
$shortcut.Save()
```

对应的手动操作就是：右键启动文件夹 -> 新建快捷方式 -> 输入：

```
"E:\DevTools\Python\versions\cpython-3.12-windows-x86_64-none\pythonw.exe" "e:\RemiliaNyaa的本地文件库\项目\我的项目\修复cc-switch全量覆盖Codex_config\codex_config_guardian.py"
```

## pythonw.exe vs python.exe

| 程序 | 窗口 | 适用场景 |
|------|------|---------|
| `python.exe` | 有控制台窗口 | 调试、需要看输出 |
| `pythonw.exe` | 无窗口 | 后台守护进程、开机自启 |

用 `pythonw.exe` 运行的脚本不会弹出黑窗口，适合后台常驻程序。日志写到文件而不是控制台。

## 常用技巧

### 查看启动文件夹位置

```powershell
[System.Environment]::GetFolderPath('Startup')
# 输出: C:\Users\用户名\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup
```

### 删除快捷方式

```powershell
Remove-Item "$startup\我的程序.lnk"
```

### 查看快捷方式属性

右键快捷方式 -> 属性，可以看到"目标"字段里是 `程序路径 参数` 的组合。
