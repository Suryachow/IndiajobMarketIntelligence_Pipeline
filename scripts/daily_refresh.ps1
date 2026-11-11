<#
.SYNOPSIS
    Daily refresh for the India Job Market Intelligence pipeline.

.DESCRIPTION
    Runs the full crawl -> normalize -> dedupe -> classify -> report pipeline once
    and appends the console output to logs/daily_refresh_<date>.log.

    The pipeline is idempotent, so running it again simply produces a fresh
    snapshot and a new/modified/closed change report against the previous day.

.PARAMETER NoCache
    Ignore the on-disk HTTP cache and pull genuinely fresh data from every portal.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\daily_refresh.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\daily_refresh.ps1 -NoCache

.NOTES
    Register it once with Windows Task Scheduler (run as your own user), e.g.:
        schtasks /Create /TN "IndiaJobsDailyRefresh" /SC DAILY /ST 06:00 /F ^
          /TR "powershell -NoProfile -ExecutionPolicy Bypass -File ""C:\Users\patib\OneDrive\Desktop\Crawl\scripts\daily_refresh.ps1"" -NoCache"
#>
[CmdletBinding()]
param(
    [switch]$NoCache
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# Resolve the project root: this script lives in <root>\scripts.
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# Locate the `py` launcher, falling back to well-known install locations so the
# task still works when Task Scheduler runs with a minimal PATH.
$Python = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $Python) {
    $candidates = @(
        (Join-Path $env:SystemRoot "py.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Launcher\py.exe")
    )
    $Python = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Python) { $Python = "py" }

# Log alongside the other deliverables.
$LogDir = Join-Path $Root "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ("daily_refresh_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))

function Write-Log([string]$Message) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

$args = @("run.py")
if ($NoCache) { $args += "--no-cache" }

Write-Log "daily refresh started (python: $Python) -> py $($args -join ' ')"
& $Python @args 2>&1 | Tee-Object -FilePath $LogFile -Append
$exit = $LASTEXITCODE

if ($exit -eq 0) {
    Write-Log "daily refresh finished OK"
} else {
    Write-Log "daily refresh FAILED (exit code $exit)"
}
exit $exit
