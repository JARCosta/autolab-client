param(
  [string]$Configuration = "Release",
  [string]$Runtime = "win-x64"
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Project = Join-Path $RepoRoot "windows\temperature-helper\TemperatureHelper.csproj"
$Output = Join-Path $RepoRoot "windows\temperature-helper\publish"

if (-not (Test-Path $Project)) {
  Write-Error "Missing helper project at $Project"
  exit 1
}

Write-Host "Publishing helper to $Output ..."
dotnet publish $Project -c $Configuration -r $Runtime --self-contained true -p:PublishSingleFile=true -o $Output

Write-Host "Done. Point AUTOLAB_LHM_HELPER_EXE at:"
Write-Host (Join-Path $Output "AutolabNode.TemperatureHelper.exe")