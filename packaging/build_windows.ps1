# Build the Bot Forge Studio Windows app for Windows 11 (x64): portable folder + zip + installer.
# Must be run on Windows 11 / Windows Server 2022+ (PyInstaller cannot cross-compile). Run from the repo root in PowerShell:
#   py -3.12 -m venv .venv; .\.venv\Scripts\Activate.ps1; .\packaging\build_windows.ps1
# Requires Inno Setup (iscc) for the installer; set WIN_CERT_* (see sign_windows.ps1) to sign.
$ErrorActionPreference = "Stop"
$Root = Split-Path $PSScriptRoot -Parent
$Version = (Select-String -Path "$Root\botstudio\__init__.py" -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
Set-Location $Root
python -m pip install --upgrade pip
python -m pip install "PyQt6>=6.5" pyinstaller
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\BotStudio.spec
# BotStudio.exe is a windowed app: `& exe` would not wait for it, so start it explicitly and check the real exit code.
$env:QT_QPA_PLATFORM = "offscreen"
$p = Start-Process -FilePath "dist\BotStudio\BotStudio.exe" -ArgumentList "--selftest" -Wait -PassThru -NoNewWindow
Remove-Item Env:\QT_QPA_PLATFORM -ErrorAction SilentlyContinue
if ($p.ExitCode -ne 0) { throw "selftest failed (exit $($p.ExitCode))" }
Write-Host "Frozen app selftest passed."
& "$PSScriptRoot\sign_windows.ps1" "dist\BotStudio\BotStudio.exe"
Compress-Archive -Path dist\BotStudio -DestinationPath "dist\BotForgeStudio-$Version-Windows11-portable.zip" -Force
$iscc = (Get-Command iscc -ErrorAction SilentlyContinue)
if (-not $iscc) {
  $cand = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
  if ($cand) { $iscc = Get-Item $cand; Set-Alias iscc $cand -Scope Script }
}
if ($iscc) {
  & iscc "/DAppVersion=$Version" packaging\installer.iss
  & "$PSScriptRoot\sign_windows.ps1" "dist\BotForgeStudio-$Version-Windows11-Setup.exe"
} else { Write-Warning "Inno Setup (iscc) not found - skipped the installer." }
Get-ChildItem dist | Format-Table Name, Length
