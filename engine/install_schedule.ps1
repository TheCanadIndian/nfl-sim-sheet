# Register the weekly update schedule in Windows Task Scheduler (times are this PC's clock, Eastern).
#   .\install_schedule.ps1           install / replace
#   .\install_schedule.ps1 -Remove   remove all tasks
# Tasks live under Task Scheduler Library > "NFL Box Score". They wake the PC from sleep to run
# (needs "Allow wake timers" on in Power Options); a run missed because the PC was off or had no
# internet starts when it's next available.
param([switch]$Remove)

$folder = "\NFL Box Score\"
$script = Join-Path $PSScriptRoot "update.ps1"

Get-ScheduledTask -TaskPath $folder -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false
if ($Remove) { "Removed all tasks in $folder"; return }

# name, day, time, news?   (news runs use Claude; keep these to three a week)
$runs = @(
    @("Tue 0900 results",          "Tuesday",   "09:00", $false),
    @("Wed 1800 injury report",    "Wednesday", "18:00", $false),
    @("Thu 1630 NEWS before TNF",  "Thursday",  "16:30", $true),
    @("Thu 1800 injury report",    "Thursday",  "18:00", $false),
    @("Fri 1800 injury report",    "Friday",    "18:00", $false),
    @("Sun 1030 NEWS early games", "Sunday",    "10:30", $true),
    @("Sun 1145 early inactives",  "Sunday",    "11:45", $false),
    @("Sun 1515 late inactives",   "Sunday",    "15:15", $false),
    @("Mon 1730 NEWS before MNF",  "Monday",    "17:30", $true)
)
# NHL: a quick check every $nhlEveryMinutes minutes, all day. It does a full update only when a
# game starts within ~75 minutes (catches start-time changes and the official roster, posted
# ~30 min before puck drop), when lines are 4+ hours old on a game day, or to grade last night.
# Otherwise it exits in about a second. Change the interval here (e.g. 60 for hourly).
$nhlEveryMinutes = 30

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Minutes 45) -MultipleInstances IgnoreNew

foreach ($r in $runs) {
    $name, $day, $time, $news = $r
    $argv = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" + $(if ($news) { " -News" } else { "" })
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argv -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $day -At $time
    Register-ScheduledTask -TaskPath $folder -TaskName $name -Action $action -Trigger $trigger `
        -Settings $settings -Description "NFL box-score projections update" | Out-Null
    "registered  $name"
}

# Weekly self-tuning (learn.py, both sports): Tuesday after results are graded. Up to ~1 hour.
$learnSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 3) -MultipleInstances IgnoreNew
$argv = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Learn"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argv -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Tuesday -At "10:00"
Register-ScheduledTask -TaskPath $folder -TaskName "Tue 1000 self-tune" -Action $action -Trigger $trigger `
    -Settings $learnSettings -Description "Self-tuning: test small model changes, adopt only proven ones" | Out-Null
"registered  Tue 1000 self-tune"

$argv = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -Hockey -Auto"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argv -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(10) `
    -RepetitionInterval (New-TimeSpan -Minutes $nhlEveryMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$name = "NHL check every $nhlEveryMinutes min"
Register-ScheduledTask -TaskPath $folder -TaskName $name -Action $action -Trigger $trigger `
    -Settings $settings -Description "NHL anytime goal projections (runs only when useful)" | Out-Null
"registered  $name"
