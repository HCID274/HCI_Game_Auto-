@{
    SchemaVersion = 1
    MutexName = 'Global\HCID274_GameAutomation'

    Apps = @{
        StarRail = @{
            RelativePath = 'apps\starrail'
            Commands = @{
                Daily = @('run', 'python', '-m', 'starrail_auto', 'daily', '--timeout', '1800')
                UpdateRecovery = @('run', 'python', '-m', 'starrail_auto.m7a.launcher_update')
                Cleanup = @('run', 'python', '-m', 'starrail_auto', 'cleanup')
                UuStart = @('run', 'python', '-m', 'starrail_auto', 'uu', 'start')
                UuStop = @('run', 'python', '-m', 'starrail_auto', 'uu', 'stop')
                Smoke = @('run', 'python', '-m', 'starrail_auto', 'smoke')
                Health = @('run', 'python', '-m', 'starrail_auto', '--help')
            }
        }
        Wuwa = @{
            RelativePath = 'apps\wuwa'
            Commands = @{
                Daily = @('run', 'wuwa-auto', 'daily')
                DailyOnly = @('run', 'wuwa-auto', 'daily-only')
                FarmEcho = @('run', 'wuwa-auto', 'farm-echo')
                WeeklyGarden = @('run', 'wuwa-auto', 'weekly-garden')
                UuStart = @('run', 'wuwa-auto', 'uu', 'start')
                Cleanup = @('run', 'wuwa-auto', 'cleanup')
                Smoke = @('run', 'wuwa-auto', 'smoke')
                Health = @('run', 'wuwa-auto', '--help')
            }
        }
    }

    Tasks = @{
        Daily = @{
            Name = 'Game_Daily_0530'
            At = '05:30'
            # 2026-08-20: a version-day Wuwa download may legally run for
            # hours inside the chain; normal days still finish in ~2h.
            ExecutionLimitHours = 6
            Description = '05:30 Star Rail -> safe cleanup -> Wuthering Waves daily chain'
        }
        WeeklyGarden = @{
            Name = 'Game_Wuwa_WeeklyGarden_Sunday'
            At = '08:00'
            DayOfWeek = 'Sunday'
            LockWaitMinutes = 210
            ExecutionLimitHours = 8
            Description = 'Sunday Wuthering Waves weekly garden through the shared orchestrator'
        }
    }

    RemovedTasks = @(
        'StarRail_Main_0600'
        'StarRail_Cleanup_0800'
    )
}
