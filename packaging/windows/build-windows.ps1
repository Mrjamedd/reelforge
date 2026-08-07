param(
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$Venv = Join-Path $RepoRoot ".venv-packaging"
$Python = Join-Path $Venv "Scripts\python.exe"
$Pip = Join-Path $Venv "Scripts\pip.exe"
$Spec = Join-Path $RepoRoot "packaging\reelpush_studio.spec"
$Icon = Join-Path $RepoRoot "packaging\assets\reelpush-studio.ico"
$IconScript = Join-Path $RepoRoot "packaging\windows\make-icon.py"
$InstallerScript = Join-Path $RepoRoot "packaging\windows\ReelPushStudio.iss"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is required to build ReelPush Studio."
}

if (-not (Test-Path $Venv)) {
    python -m venv $Venv
}

& $Python -m pip install --upgrade pip
& $Pip install pyinstaller

if (-not (Test-Path $Icon)) {
    & $Python $IconScript
}

Push-Location $RepoRoot
try {
    & $Python -m PyInstaller --clean --noconfirm $Spec
}
finally {
    Pop-Location
}

if ($SkipInstaller) {
    Write-Host "Executable created at dist\ReelPush Studio\ReelPush Studio.exe"
    exit 0
}

$InnoCandidates = @(@(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { $_ -and (Test-Path $_) })

if ($InnoCandidates.Count -eq 0) {
    Write-Host "Executable created at dist\ReelPush Studio\ReelPush Studio.exe"
    Write-Host "Install Inno Setup 6 and rerun this script to create the installer."
    exit 0
}

& $InnoCandidates[0] $InstallerScript
Write-Host "Installer created under dist\installer."
