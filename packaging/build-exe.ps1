# Build the standalone Windows .exe locally — mirrors the GitHub Actions release
# workflow so a local build matches what CI ships.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build-exe.ps1
#
# Output: dist\iRacingConfigTracker.exe
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

& $py -m pip install --upgrade pip
& $py -m pip install -e ".[gui,sim,toast]"
& $py -m pip install pyinstaller

# Stamp a dev version so the built exe knows what it is (CI overwrites this).
Set-Content -Path "src/irtracker/_buildinfo.py" -Value 'VERSION = "v0.0.0-dev"' -Encoding utf8

& $py -m PyInstaller --noconfirm --clean `
  --onefile --windowed `
  --name iRacingConfigTracker `
  --icon packaging/icon.ico `
  --add-data "src/irtracker/webui;irtracker/webui" `
  --collect-all webview `
  --collect-all clr_loader `
  --collect-all pythonnet `
  --collect-all bottle `
  --collect-all proxy_tools `
  --collect-all fpdf `
  --collect-all pystray `
  --collect-all PIL `
  packaging\launcher.py

Write-Host ""
Write-Host "Built: $root\dist\iRacingConfigTracker.exe"
