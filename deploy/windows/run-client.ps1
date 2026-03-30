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

$VenvDir = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
  Write-Host "Creating virtual environment at .venv ..."
  if ($PythonExe -eq "py") {
    & py -m venv $VenvDir
  } else {
    & $PythonExe -m venv $VenvDir
  }
}

if (-not (Test-Path $VenvPython)) {
  Write-Error @"
Could not create .venv. Install Python 3 and ensure the launcher works, or run:
  .\deploy\windows\run-client.ps1 -PythonExe 'C:\Path\To\python.exe'
Scheduled tasks as SYSTEM often lack 'py' on PATH; create the venv once interactively, or pass -PythonExe to a full python.exe path.
"@
  exit 1
}

$Req = Join-Path $RepoRoot "requirements.txt"
Write-Host "Installing dependencies into .venv (if needed)..."
& $VenvPython -m pip install --disable-pip-version-check -r $Req

if ($ExtraArgs) {
  & $VenvPython -m autolab_client @($ExtraArgs.Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries))
} else {
  & $VenvPython -m autolab_client
}
