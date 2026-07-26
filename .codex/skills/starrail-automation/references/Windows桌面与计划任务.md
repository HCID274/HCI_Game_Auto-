# Windows 桌面与计划任务

## GUI 前提

- 使用 `Interactive` 登录类型和 `RunLevel=Highest`。SYSTEM 会话没有用户桌面，不能可靠识图。
- 在导入 `pyautogui` 前调用 `SetProcessDpiAwareness(2)`。125% 缩放下未设置时会把 2560x1440 虚拟化为 2048x1152，导致点击错位。
- 正式模板基于 2560x1440。分辨率不符应截图并不可重试失败，不能通过重启 UU 掩盖环境问题。
- Apollo/Sunshine 类虚拟屏可能改变主屏或分辨率。先检查 Apollo 服务、虚拟显示设备和 `pyautogui.size()`，再改识图模板。

## 正式任务

```text
StarRail_Main_0600      06:00  启用
StarRail_Cleanup_0800   08:00  启用
StarRail_Universe_2330  23:30  禁用
```

任务动作指向 `scripts/run_scheduled_task.ps1`。该包装器切换到项目目录后使用 `uv run starrail-auto`。注册脚本先删除同名任务再注册，并使用 `IgnoreNew`，因此不会累积重复实例。

## 无副作用检查

```powershell
Get-ScheduledTask | Where-Object TaskName -Like 'StarRail*'
Get-ScheduledTaskInfo -TaskName StarRail_Main_0600
uv run starrail-auto --help
```

不要仅因代码入口更新而重新注册任务；只要包装器路径没变，修改包装器即可降低远程环境风险。
