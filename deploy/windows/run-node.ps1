param(
  [string]$PythonExe = "py",
  [string]$ExtraArgs = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

# One-time: attempt to build the LibreHardwareMonitor helper if missing.
# This helps SYSTEM AtStartup tasks that don't ship a prebuilt helper.
$HelperEnv = $env:AUTOLAB_LHM_HELPER_EXE
if ($HelperEnv) {
  $helperPath = $HelperEnv
} else {
  $helperPath = Join-Path $RepoRoot "windows\temperature-helper\publish\AutolabNode.TemperatureHelper.exe"
}

if (-not (Test-Path $helperPath)) {
  Write-Host "LibreHardwareMonitor helper not found at $helperPath. Attempting one-time build..."
  $dotnet = Get-Command dotnet -ErrorAction SilentlyContinue
  if ($null -ne $dotnet) {
    try {
      & (Join-Path $RepoRoot "deploy\windows\build-temperature-helper.ps1")
      if (Test-Path $helperPath) {
        Write-Host "Built temperature helper at $helperPath"
      } else {
        Write-Warning "Build script finished but helper not found at $helperPath"
      }
    } catch {
      Write-Warning "Failed to build temperature helper: $_"
    }
  } else {
    Write-Warning "dotnet not found; skipping helper build. Install .NET SDK or prebuild the helper if you want automatic builds."
  }
}

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
  .\deploy\windows\run-node.ps1 -PythonExe 'C:\Path\To\python.exe'
Scheduled tasks as SYSTEM often lack 'py' on PATH; create the venv once interactively, or pass -PythonExe to a full python.exe path.
"@
  exit 1
}

$Req = Join-Path $RepoRoot "requirements.txt"
Write-Host "Installing dependencies into .venv (if needed)..."
& $VenvPython -m pip install --disable-pip-version-check -r $Req

if ($ExtraArgs) {
  & $VenvPython -m autolab_node @($ExtraArgs.Split(" ", [System.StringSplitOptions]::RemoveEmptyEntries))
} else {
  & $VenvPython -m autolab_node
}
