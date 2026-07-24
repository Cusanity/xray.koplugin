# Build XRayGenerator.exe — single portable executable
# Usage: .\build-exe.ps1
# Output: dist\XRayGenerator.exe

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "=== Installing / updating PyInstaller ===" -ForegroundColor Cyan
pip install --upgrade pyinstaller

Write-Host ""
Write-Host "=== Installing X-Ray GUI dependencies (from requirements.txt) ===" -ForegroundColor Cyan
pip install --upgrade -r requirements.txt

Write-Host ""
Write-Host "=== Building XRayGenerator.exe ===" -ForegroundColor Cyan
pyinstaller generator_gui.spec --noconfirm

if ($LASTEXITCODE -ne 0) {
    Write-Host "Build FAILED." -ForegroundColor Red
    exit 1
}

$exe = Join-Path $ScriptDir "dist\XRayGenerator.exe"
if (Test-Path $exe) {
    $size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host ""
    Write-Host "=== Build complete ===" -ForegroundColor Green
    Write-Host "  $exe  ($size MB)"
    Write-Host ""
    Write-Host "Copy XRayGenerator.exe to any Windows machine — no Python required." -ForegroundColor Yellow
    Write-Host "On first launch it extracts itself (~5 s); subsequent launches are faster." -ForegroundColor Yellow
    Write-Host "Place .env next to the .exe to persist API keys." -ForegroundColor Yellow
} else {
    Write-Host "Exe not found after build — check PyInstaller output above." -ForegroundColor Red
    exit 1
}
