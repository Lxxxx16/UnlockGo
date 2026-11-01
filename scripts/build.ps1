[CmdletBinding()]
param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Virtual environment not found: $python"
}

Push-Location $projectRoot
try {
    if (-not $SkipInstall) {
        Write-Host "Installing build dependencies..."
        & $python -m pip install --disable-pip-version-check -r requirements-build.txt
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to install build dependencies."
        }
    }

    Write-Host "Building dist\UnlockGo.exe..."
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --onefile `
        --windowed `
        --name UnlockGo `
        --icon "app\assets\unlockgo.ico" `
        --add-data "app\assets;app\assets" `
        main.py

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller build failed."
    }

    $output = Join-Path $projectRoot "dist\UnlockGo.exe"
    if (-not (Test-Path -LiteralPath $output -PathType Leaf)) {
        throw "Expected output was not created: $output"
    }

    $file = Get-Item -LiteralPath $output
    Write-Host ("Build complete: {0} ({1:N2} MB)" -f $file.FullName, ($file.Length / 1MB))
}
finally {
    Pop-Location
}