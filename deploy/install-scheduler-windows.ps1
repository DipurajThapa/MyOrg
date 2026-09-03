<#
.SYNOPSIS
    Runs the MyOrg scheduler as a supervised background service on Windows.

.DESCRIPTION
    The development host here is Windows while the systemd units target Linux, so the loop
    would only ever have been tested on the platform it does not run on. This registers the
    same `--supervised` command as a Scheduled Task that starts at boot and is restarted by
    Windows if it stops -- the nearest equivalent of Restart=on-failure.

    Stopping the task sends a break to the process, which finishes the pass it is in rather
    than stranding a claimed step.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy/install-scheduler-windows.ps1 `
        -RepoRoot C:\AgenticAI\MyOrg -Python C:\AgenticAI\MyOrg\.venv\Scripts\python.exe
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$TaskName = "MyOrgScheduler",
    [int]$IntervalSeconds = 60
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $RepoRoot)) { throw "RepoRoot does not exist: $RepoRoot" }
if (-not (Test-Path -LiteralPath $Python))   { throw "Python interpreter not found: $Python" }
if (-not $env:MYORG_AUTH_SECRET) {
    throw "MYORG_AUTH_SECRET must be set for the account that will run the task"
}

$action = New-ScheduledTaskAction -Execute $Python `
    -Argument "-m runtime.scheduler --supervised --interval $IntervalSeconds" `
    -WorkingDirectory $RepoRoot

$trigger = New-ScheduledTaskTrigger -AtStartup

# RestartCount/RestartInterval are the Windows counterpart of Restart=on-failure.
# ExecutionTimeLimit 0 means "no wall-clock cap": the ceilings live inside the runtime.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

# No -User: the task runs as the account registering it, so that account's own `gh auth
# login` serves scripts/notify_github.py (MYORG_NOTIFY_COMMAND). Register it as the
# operator who should own the notices, not as an administrator standing in.
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "MyOrg autonomous scheduler (supervised sweep loop)" `
    -Force | Out-Null

Write-Output "Registered scheduled task '$TaskName'."
Write-Output "Start it now with:  Start-ScheduledTask -TaskName $TaskName"
Write-Output "Stop it with:       Stop-ScheduledTask  -TaskName $TaskName"
Write-Output "Read its output in the runtime log configured by MYORG_LOG_LEVEL."
