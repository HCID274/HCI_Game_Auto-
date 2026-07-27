# HCI Game Automation

星穹铁道与鸣潮的 Windows 无人值守自动化总仓库。两个游戏保持独立应用边界，
由根级总控统一处理顺序、桌面交接、互斥锁与计划任务。

## 常用命令

```powershell
# 完整静态、单元和无副作用联动验证
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/validate.ps1

# 查看完整日常链路但不启动任何游戏
powershell -NoProfile -ExecutionPolicy Bypass -File orchestrator/run.ps1 -Mode daily-chain -DryRun

# 管理员安装并切换正式计划任务
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1
```

应用独立入口：

```powershell
uv run --project apps/starrail starrail-auto --help
uv run --project apps/wuwa wuwa-auto --help
```

架构和退出边界见 [docs/architecture.md](docs/architecture.md)。游戏专属说明分别见
`apps/starrail/docs/架构蓝图.md` 与 `apps/wuwa/docs/架构蓝图.md`。
稳定后的公共 UU/桌面包提取计划见 [docs/roadmap.md](docs/roadmap.md)。
