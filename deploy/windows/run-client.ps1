param(
  [string]$PythonExe = "py",
  [string]$ExtraArgs = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

if (-not (Test-Path ".env")) {
  Write-Error "Missing .env in $RepoRoot. Copy .env.example first."
  exit 1
}

if ($PythonExe -eq "py") {
  & py -m autolab_client $ExtraArgs
} else {
  & $PythonExe -m autolab_client $ExtraArgs
}
