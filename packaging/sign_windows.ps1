<#
 Sign Windows binaries with signtool. Usage:  .\sign_windows.ps1 file1.exe file2.exe
 Credentials come from the environment (never commit them):
   WIN_CERT_PFX_BASE64   base64 of your code-signing .pfx   (OV/EV certificate, or a cloud/HSM export)
   WIN_CERT_PASSWORD     its password
   WIN_TIMESTAMP_URL     optional, default http://timestamp.digicert.com
 If WIN_CERT_PFX_BASE64 is not set the files are left unsigned (exit 0) so unsigned CI builds still work.
 Azure Trusted Signing users: skip this script and use the azure/trusted-signing-action step in the workflow.
#>
param([Parameter(Mandatory = $true, ValueFromRemainingArguments = $true)][string[]]$Files)
if (-not $env:WIN_CERT_PFX_BASE64) { Write-Host "No signing certificate configured - leaving files unsigned."; exit 0 }
$pfx = Join-Path $env:RUNNER_TEMP "codesign.pfx"
if (-not $env:RUNNER_TEMP) { $pfx = Join-Path $env:TEMP "codesign.pfx" }
[IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:WIN_CERT_PFX_BASE64))
$ts = if ($env:WIN_TIMESTAMP_URL) { $env:WIN_TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
$signtool = (Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" | Sort-Object FullName | Select-Object -Last 1).FullName
try {
  foreach ($f in $Files) {
    & $signtool sign /f $pfx /p $env:WIN_CERT_PASSWORD /fd SHA256 /tr $ts /td SHA256 /d "Bot Forge Studio" $f
    if ($LASTEXITCODE -ne 0) { throw "signtool failed on $f" }
    & $signtool verify /pa $f
  }
} finally { Remove-Item $pfx -Force -ErrorAction SilentlyContinue }
