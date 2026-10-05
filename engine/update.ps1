# Scheduled update: refresh nflverse data and rebuild projections.
#   .\update.ps1          free run: data refresh + projections + local page (projections\latest.html)
#   .\update.ps1 -News    also runs Claude headlessly to check injury news and update this
#                         week's overrides file (background runs can't publish the shared
#                         link; open projections\latest.html)
# Logs: logs\update_<timestamp>.log
#   .\update.ps1 -Hockey -Auto   frequent NHL check: full update only when a game is close,
#                                lines are stale, or last night needs grading (hockey\should_run.py)
#   .\update.ps1 -Learn          weekly self-tuning (learn.py nfl + nhl), then rebuild and publish
param([switch]$News, [switch]$Hockey, [switch]$Auto, [switch]$Learn)

Set-Location $PSScriptRoot
$env:PYTHONIOENCODING = "utf-8"
$py = Join-Path $PSScriptRoot "football\Scripts\python.exe"
$now = Get-Date
$season = if ($now.Month -ge 3) { $now.Year } else { $now.Year - 1 }
$log = Join-Path $PSScriptRoot ("logs\update_{0:yyyy-MM-dd_HHmm}{1}.log" -f $now, $(if ($News) { "_news" } else { "" }))
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null

function Log($msg) { "[{0:HH:mm:ss}] {1}" -f (Get-Date), $msg | Out-File -Append -Encoding utf8 $log }
function Run($exe, [string[]]$argv) {
    & $exe @argv 2>&1 | ForEach-Object { "$_" } | Out-File -Append -Encoding utf8 $log
    return $LASTEXITCODE
}

if ($Learn) {
    # Self-tuning: try small setting changes on recent seasons, adopt at most one per sport if it
    # improves both test windows (see learn.py). Then rebuild with whatever was adopted.
    Log "self-tune NFL"
    $rc = Run $py @("learn.py", "nfl")
    Log "self-tune NFL exit $rc"
    Log "self-tune NHL"
    $rc = Run $py @("learn.py", "nhl")
    Log "self-tune NHL exit $rc"
    $rc = Run $py @("project.py")
    $rc = Run $py @("project.py", "--blind")
    $rc = Run $py @("project.py")
    $rc = Run $py @("results.py")
    $rc = Run $py @("hockey\project.py")
    $rc = Run $py @("hockey\results.py")
    $site = Join-Path $PSScriptRoot "site"
    $git = "C:\Program Files\Git\cmd\git.exe"
    if ((Test-Path "$site\.git") -and (Test-Path $git)) {
        Run $py @("site.py") | Out-Null
        Run $git @("-C", $site, "add", "-A") | Out-Null
        Run $git @("-C", $site, "commit", "--quiet", "-m", ("Self-tune {0:yyyy-MM-dd}" -f (Get-Date))) | Out-Null
        $rc = Run $git @("-C", $site, "push", "--quiet")
        Log "publish exit $rc"
    }
    Log "done"
    return
}

if ($Hockey) {
    if ($Auto) {
        # Quick check (one small request). Skips go to one running log instead of a file per check.
        $why = & $py "hockey\should_run.py" 2>&1 | Out-String
        $skip = ($LASTEXITCODE -eq 10)
        "[{0:yyyy-MM-dd HH:mm}] {1}" -f (Get-Date), $why.Trim() | Out-File -Append -Encoding utf8 (Join-Path $PSScriptRoot "logs\nhl_checks.log")
        if ($skip) { return }
        Log "NHL check: $($why.Trim())"
    }
    # NHL anytime-goal sheet: refresh this season's results/schedule, project the next slate,
    # publish to site\nhl\. Season starts in late September.
    $nhlSeason = if ($now.Month -ge 8) { "{0}{1}" -f $now.Year, ($now.Year + 1) } else { "{0}{1}" -f ($now.Year - 1), $now.Year }
    Log "NHL refresh ($nhlSeason)"
    $rc = Run $py @("hockey\fetch.py", "--seasons", $nhlSeason)
    Log "NHL project"
    $rc = Run $py @("hockey\project.py")
    Log "NHL project exit $rc"
    $rc = Run $py @("hockey\linecheck.py")          # sheet lines/PP1/positions vs shift charts
    $rc = Run $py @("hockey\results.py")
    Log "NHL results exit $rc"
    $site = Join-Path $PSScriptRoot "site"
    $git = "C:\Program Files\Git\cmd\git.exe"
    if ((Test-Path "$site\.git") -and (Test-Path $git) -and (Test-Path "hockey\projections\index.html")) {
        Run $py @("site.py") | Out-Null          # rebuilds the whole site (home + NFL + NHL)
        Run $git @("-C", $site, "add", "-A") | Out-Null
        Run $git @("-C", $site, "commit", "--quiet", "-m", ("NHL update {0:yyyy-MM-dd HH:mm}" -f (Get-Date))) | Out-Null
        $rc = Run $git @("-C", $site, "push", "--quiet")
        Log "publish NHL exit $rc"
    }
    Run $py @("hockey\should_run.py", "--mark") | Out-Null
    Log "done"
    return
}

Log "refresh data (season $season)"
$rc = Run $py @("build_nfl_db.py", "--seasons", "$season", "--db", "nfl.db",
                "--skip", "ngs_pass", "ngs_rush", "ngs_rec", "player_week")
if ($rc -ne 0) { Log "data refresh failed (exit $rc); projecting with existing data" }

$projected = $false
if ($News) {
    $claude = Get-ChildItem "$env:USERPROFILE\.vscode\extensions\anthropic.claude-code-*\resources\native-binary\claude.exe" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $claude) {
        Log "Claude CLI not found (VS Code extension missing?); running free update only"
    } else {
        $brief = & $py "scheduled\week_context.py" 2>&1 | Out-String
        $prompt = (Get-Content "scheduled\news_check.md" -Raw) + "`n" + $brief
        Log "news check with $($claude.FullName)"
        # Prompt goes in on stdin: PowerShell 5.1 mangles quotes in long native arguments.
        $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $prompt | & $claude.FullName -p --allowedTools PowerShell Read Write Edit Glob Grep WebSearch WebFetch 2>&1 |
            ForEach-Object { "$_" } | Out-File -Append -Encoding utf8 $log
        $rc = $LASTEXITCODE
        Log "news check exit $rc"
        $projected = ($rc -eq 0)
    }
}

if (-not $projected) {
    Log "project"
    $rc = Run $py @("project.py")
    Log "project exit $rc"
}

Log "market-blind projections"
$rc = Run $py @("project.py", "--blind")
Log "market-blind exit $rc"
# Rebuild the main page so its Model agreement tab reads this run's blind projections.
$rc = Run $py @("project.py")
Log "main page rebuilt with blind comparison, exit $rc"
Log "backfill finished games missing from the archives"
$rc = Run $py @("backfill_missing.py")
$rc = Run $py @("backfill_missing.py", "--blind")
Log "results"
$rc = Run $py @("results.py")
Log "results exit $rc"

# Publish to GitHub Pages if setup_site.ps1 has been run:
# site.py assembles home (index.html), nfl\ (this week, results, weeks, blind) and nhl\.
$site = Join-Path $PSScriptRoot "site"
$git = "C:\Program Files\Git\cmd\git.exe"
if ((Test-Path "$site\.git") -and (Test-Path $git)) {
    Run $py @("site.py") | Out-Null              # home page + /nfl/ + /nhl/ with one navigation bar
    Run $git @("-C", $site, "add", "-A") | Out-Null
    Run $git @("-C", $site, "commit", "--quiet", "-m", ("Update {0:yyyy-MM-dd HH:mm}" -f (Get-Date))) | Out-Null
    $rc = Run $git @("-C", $site, "push", "--quiet")
    Log "publish to GitHub Pages exit $rc"
}
Log "done"
