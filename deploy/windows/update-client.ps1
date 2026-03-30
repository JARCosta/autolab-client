param(
  [string]$TaskName = "AutoLabClient"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

$IsAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $IsAdmin) {
  Write-Host "Administrator privileges are required to stop/start the scheduled task."
  $scriptPath = $MyInvocation.MyCommand.Path
  $args = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $scriptPath, "-TaskName", $TaskName)
  Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $args | Out-Null
  exit 0
}

Write-Host "Pulling latest client from git..."
if (Test-Path ".git") {
  git pull
} else {
  Write-Warning "No .git directory here; copy new files manually or clone again."
}

$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$Req = Join-Path $RepoRoot "requirements.txt"
if ((Test-Path $VenvPython) -and (Test-Path $Req)) {
  Write-Host "Updating Python dependencies in .venv..."
  & $VenvPython -m pip install --disable-pip-version-check -r $Req
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
  Write-Host "No scheduled task '$TaskName'. If the client runs manually, restart it yourself."
  exit 0
}

Write-Host "Restarting scheduled task '$TaskName' so new code loads..."
try {
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop
} catch {
  Write-Warning "Could not stop task (maybe not running): $_"
}
Start-Sleep -Seconds 2
Start-ScheduledTask -TaskName $TaskName
Write-Host "Done. Check Task Scheduler or run: Get-ScheduledTaskInfo -TaskName $TaskName"
