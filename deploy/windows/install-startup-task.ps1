param(
  [string]$TaskName = "AutoLabClient",
  [string]$PythonExe = "py"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Runner = Join-Path $RepoRoot "deploy\windows\run-client.ps1"

if (-not (Test-Path (Join-Path $RepoRoot ".env"))) {
  Write-Error "Missing .env in $RepoRoot. Copy .env.example first."
  exit 1
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
  Write-Host "Scheduled task '$TaskName' already exists."
  exit 0
}

$action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -PythonExe `"$PythonExe`""

try {
  $trigger = New-ScheduledTaskTrigger -AtStartup
  $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal | Out-Null
  Write-Host "Created startup task '$TaskName' (SYSTEM, AtStartup)."
} catch {
  $currentUser = "$env:USERDOMAIN\$env:USERNAME"
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -User $currentUser | Out-Null
  Write-Warning "Created fallback logon task '$TaskName' for $currentUser."
  Write-Warning "Run this script as Administrator to create a true AtStartup SYSTEM task."
}

Write-Host "Done. Start now with: Start-ScheduledTask -TaskName $TaskName"
