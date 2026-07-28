@{
    SchemaVersion = 1
    MutexName = 'Global\HCID274_GameAutomation'

    Apps = @{
        StarRail = @{
            RelativePath = 'apps\starrail'
            Commands = @{
                Daily = @('run', 'starrail-auto', 'daily', '--timeout', '1800')
                Cleanup = @('run', 'starrail-auto', 'cleanup')
                UuStart = @('run', 'starrail-auto', 'uu', 'start')
                UuStop = @('run', 'starrail-auto', 'uu', 'stop')
                Smoke = @('run', 'starrail-auto', 'smoke')
                Health = @('run', 'starrail-auto', '--help')
            }
        }
        Wuwa = @{
            RelativePath = 'apps\wuwa'
            Commands = @{
                Daily = @('run', 'wuwa-auto', 'daily')
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
            ExecutionLimitHours = 3
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
