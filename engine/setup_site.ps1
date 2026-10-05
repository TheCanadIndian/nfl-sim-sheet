# One-time setup: create a public GitHub repo holding only the projections page and
# turn on GitHub Pages. Run after `gh auth login`.
#   powershell -ExecutionPolicy Bypass -File .\setup_site.ps1 [-Repo nfl-sim-sheet]
param([string]$Repo = "nfl-sim-sheet")

$git = "C:\Program Files\Git\cmd\git.exe"
$gh = "C:\Program Files\GitHub CLI\gh.exe"
Set-Location $PSScriptRoot

& $gh auth status *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "Not logged in to GitHub. Run: & `"$gh`" auth login"; exit 1 }
& $gh auth setup-git                          # lets git push using the gh login (also in scheduled runs)
$user = (& $gh api user --jq .login).Trim()

$site = Join-Path $PSScriptRoot "site"
New-Item -ItemType Directory -Force $site | Out-Null
Copy-Item "projections\latest.html" "$site\index.html" -Force
New-Item -ItemType File -Force "$site\.nojekyll" | Out-Null   # serve the file as-is

if (-not (Test-Path "$site\.git")) {
    & $git -C $site init -b main
    & $git -C $site config user.name $user
    & $git -C $site config user.email "$user@users.noreply.github.com"
}
& $git -C $site add -A
& $git -C $site commit -m "First publish" --quiet

& $gh repo create $Repo --public --source $site --push --description "NFL box-score sim projections"
if ($LASTEXITCODE -ne 0) { Write-Host "Repo creation failed (does '$Repo' already exist? use -Repo <other-name>)"; exit 1 }
& $gh api -X POST "repos/$user/$Repo/pages" -f "source[branch]=main" -f "source[path]=/" | Out-Null

Write-Host ""
Write-Host "Your page (live within a few minutes): https://$user.github.io/$Repo/"
