# Install a Windows Task Scheduler entry that runs the remaining video
# walk batch every night at 00:05 (quota resets at 00:00).
#
# Usage (PowerShell as Administrator):
#     powershell -ExecutionPolicy Bypass -File scripts/_install_video_task.ps1
#
# The task runs scripts\_batch_video_walks.py with the three remaining
# styles. Add -Remove to uninstall.

param(
    [switch]$Remove
)

$TaskName = "ccmon-batch-video-walks"
$ScriptPath = Join-Path $PSScriptRoot "_batch_video_walks.py"
$PythonExe = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
$PythonExe = (Resolve-Path $PythonExe).Path
$ScriptPath = (Resolve-Path $ScriptPath).Path

# Day 2: the three styles we haven't run yet. Edit if quota order
# changes.
$Day2Args = @("blackshiba", "crocodile", "tiger")

if ($Remove) {
    schtasks /Delete /TN $TaskName /F | Out-Null
    Write-Host "Removed scheduled task '$TaskName'."
    exit 0
}

$existing = schtasks /Query /TN $TaskName 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "Task '$TaskName' already exists. Use -Remove to reinstall."
    schtasks /Query /TN $TaskName
    exit 0
}

# Build the command string. We pass the styles as positional args to
# _batch_video_walks.py; they override the DEFAULT_STYLES.
$argList = $Day2Args -join '","'  # not used; build via /TR
$tr = '"' + $PythonExe + '" "' + $ScriptPath + '" "' + ($Day2Args -join '" "') + '"'

# 00:05 daily. /RL HIGHEST so it preempts anything else if quota
# hadn't reset by 00:00 exactly.
schtasks /Create `
    /TN $TaskName `
    /TR $tr `
    /SC DAILY `
    /ST 00:05 `
    /RL HIGHEST `
    /F | Out-Null

Write-Host "Installed scheduled task '$TaskName'."
Write-Host "  Runs daily at 00:05"
Write-Host "  Command: $tr"
schtasks /Query /TN $TaskName
