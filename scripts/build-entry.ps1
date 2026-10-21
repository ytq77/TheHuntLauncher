$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$out = Join-Path $repo '.build\BBSC-Launcher.exe'
[void][IO.Directory]::CreateDirectory((Split-Path -Parent $out))
& (Join-Path $PSScriptRoot 'make-icon.ps1')
$icon = Join-Path $repo '.build\TheHunt.ico'
& $compiler /nologo /target:winexe /reference:System.Windows.Forms.dll ("/win32icon:" + $icon) ("/out:" + $out) (Join-Path $repo 'launcher\Entry.cs')
if ($LASTEXITCODE -ne 0) { throw 'Native launcher build failed.' }
& powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'launcher\Launcher.ps1') -Validate
if ($LASTEXITCODE -ne 0) { throw 'WPF validation failed.' }
& powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'tests\test-feature-ui.ps1')
if ($LASTEXITCODE -ne 0) { throw 'WPF feature persistence checks failed.' }
& powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'tests\test-conversion-ui.ps1')
if ($LASTEXITCODE -ne 0) { throw 'WPF conversion workflow checks failed.' }
