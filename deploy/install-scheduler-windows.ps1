<#
.SYNOPSIS
    Runs the MyOrg scheduler as a supervised background service on Windows.

.DESCRIPTION
    The development host here is Windows while the systemd units target Linux, so the loop
    would only ever have been tested on the platform it does not run on. This registers the
    same `--supervised` command as a Scheduled Task that starts when the registering user
    logs on and is restarted by Windows if it stops -- the nearest equivalent of
    Restart=on-failure for a task that runs as an interactive user.

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
# The scheduler itself never reads MYORG_AUTH_SECRET -- only the API and `admin bootstrap`
# do -- so its absence is not a reason to refuse the task. Say so and carry on.
if (-not $env:MYORG_AUTH_SECRET) {
    Write-Warning "MYORG_AUTH_SECRET is not set for this account. The scheduler does not need it; the API server and 'admin bootstrap' do."
}
if (-not $env:MYORG_NOTIFY_COMMAND) {
    Write-Warning "MYORG_NOTIFY_COMMAND is not set for this account: the loop will run unattended and unheard (see docs/OPERATIONS-RUNBOOK.md#being-told)."
}

$action = New-ScheduledTaskAction -Execute $Python `
    -Argument "-m runtime.scheduler --supervised --interval $IntervalSeconds" `
    -WorkingDirectory $RepoRoot

# At logon of the registering user, not at boot: the task runs as that user interactively
# (so its `gh` login works), and such a task cannot run before the user is logged on. A boot
# trigger would also need an elevated shell to register -- this one does not.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

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
# The loop prints to stdout, and a scheduled task has no console -- that output is gone.
# What it *does* is visible elsewhere: python -m runtime.health, /metrics, notify list.
Write-Output "Its console output is not captured. Watch it with: python -m runtime.health,"
Write-Output "python -m runtime.notify list, /metrics, and Task Scheduler > History."
