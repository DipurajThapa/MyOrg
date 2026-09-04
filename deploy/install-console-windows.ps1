<#
.SYNOPSIS
    Runs the MyOrg API and operator console as a supervised background service on Windows.

.DESCRIPTION
    The scheduler already survives logout and reboot; the API did not, and the API is the
    only thing in this product a person can look at. Close the terminal it was started in
    and the console is gone until somebody remembers a long command with an environment
    variable in it -- which is exactly what happened during a real session.

    This registers `python -m runtime.api` as a Scheduled Task with the same shape as the
    scheduler's: starts at logon of the registering user, restarted by Windows if it stops,
    no console window, one log file.

    The secret must already exist and must be stable. A fresh secret on every start
    invalidates every token issued before it, so this refuses rather than inventing one --
    and it refuses to store one for you, because a secret this script generated is a secret
    nobody chose. Set it once, for this account, and it survives reboots:

        [Environment]::SetEnvironmentVariable(
            'MYORG_AUTH_SECRET',
            (python -c "import secrets;print(secrets.token_hex(32))"),
            'User')

    Then open a new shell so the variable is present, and run this.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File deploy/install-console-windows.ps1 `
        -RepoRoot C:\AgenticAI\MyOrg -Python C:\AgenticAI\MyOrg\.venv\Scripts\python.exe `
        -ConsoleActor dipuraj
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$RepoRoot,
    [Parameter(Mandatory = $true)][string]$Python,
    [Parameter(Mandatory = $true)][string]$ConsoleActor,
    [string]$TaskName = "MyOrgConsole",
    [string]$ConsoleOrg = "default",
    [int]$Port = 8080
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $RepoRoot)) { throw "RepoRoot does not exist: $RepoRoot" }
if (-not (Test-Path -LiteralPath $Python))   { throw "Python interpreter not found: $Python" }

# Unlike the scheduler, this one cannot run without the secret: `runtime.api` exits
# immediately without it, and under pythonw that exit is silent.
$secret = [Environment]::GetEnvironmentVariable('MYORG_AUTH_SECRET', 'User')
if (-not $secret) { $secret = $env:MYORG_AUTH_SECRET }
if (-not $secret) {
    throw @"
MYORG_AUTH_SECRET is not set for this account, and the API cannot start without it.
Set it once so it survives reboots, then open a new shell and run this again:

  [Environment]::SetEnvironmentVariable('MYORG_AUTH_SECRET', `
      (python -c "import secrets;print(secrets.token_hex(32))"), 'User')

It must stay the same. A new secret invalidates every token already issued.
"@
}
if ($secret.Length -lt 32) { throw "MYORG_AUTH_SECRET must be at least 32 characters." }

# The console is off unless a human is named for it. Registering the task without one would
# stand up an API whose only page answers 404 -- a service that starts and does nothing
# visible is worse than no service.
$Pythonw = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
if (-not (Test-Path -LiteralPath $Pythonw)) { throw "pythonw.exe not found beside $Python" }

$LogFile = Join-Path $RepoRoot "runtime\runs\_api.log"
$Database = Join-Path $RepoRoot "runtime\data\myorg.db"

# A scheduled task inherits the *user* environment at logon, not this shell's. Persist the
# console settings there so the task sees them on every boot, not only today.
foreach ($pair in @(
    @{ Name = 'MYORG_CONSOLE_ACTOR'; Value = $ConsoleActor },
    @{ Name = 'MYORG_CONSOLE_ORG';   Value = $ConsoleOrg },
    @{ Name = 'MYORG_PORT';          Value = "$Port" },
    @{ Name = 'MYORG_DB';            Value = $Database },
    @{ Name = 'MYORG_API_LOG_FILE';  Value = $LogFile })) {
    [Environment]::SetEnvironmentVariable($pair.Name, $pair.Value, 'User')
    Set-Item -Path "Env:$($pair.Name)" -Value $pair.Value
}

$action = New-ScheduledTaskAction -Execute $Pythonw `
    -Argument "-m runtime.api" -WorkingDirectory $RepoRoot

# At logon of the registering user, matching the scheduler: the console is a loopback
# service for the person sitting here, and a boot trigger would need an elevated shell.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "MyOrg API and operator console (loopback only)" `
    -Force | Out-Null

Write-Output "Registered scheduled task '$TaskName'."
Write-Output "Start it now with:  Start-ScheduledTask -TaskName $TaskName"
Write-Output "Stop it with:       Stop-ScheduledTask  -TaskName $TaskName"
Write-Output ""
Write-Output "Then open:  http://127.0.0.1:$Port/"
Write-Output "It runs in the background (no window). Its lines go to:"
Write-Output "  $LogFile"
Write-Output ""
Write-Output "The console answers the loopback interface only, and only while"
Write-Output "MYORG_CONSOLE_ACTOR names a human. To turn it off without removing the task:"
Write-Output "  [Environment]::SetEnvironmentVariable('MYORG_CONSOLE_ACTOR', `$null, 'User')"
