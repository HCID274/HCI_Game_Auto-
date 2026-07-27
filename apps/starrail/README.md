# 星铁日常自动化

正式日常由总仓根级 `Game_Daily_0530` 调度，并在星铁完成后立即清理和交接鸣潮。
旧的 06:00 日常与 08:00 清理任务已移除；模拟宇宙任务保留但默认禁用。

## 常用命令

```powershell
uv sync
uv run starrail-auto --help
uv run starrail-auto elevate daily
uv run starrail-auto elevate cleanup --delay 10
uv run starrail-auto plan list
uv run pytest -q
```

用户可编辑的培养计划、汇报偏好和日期提醒统一放在 [`UserContext/`](UserContext/README.md)。完整流程、模块边界和计划任务配置见 [`docs/架构蓝图.md`](docs/架构蓝图.md)。

密钥只写入不入库的 `.env`，字段模板见 `.env.example`。
