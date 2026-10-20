$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$out = Join-Path $repo '.build\BBSC-Launcher.exe'
[void][IO.Directory]::CreateDirectory((Split-Path -Parent $out))
& $compiler /nologo /target:winexe /reference:System.Windows.Forms.dll ("/out:" + $out) (Join-Path $repo 'launcher\Entry.cs')
if ($LASTEXITCODE -ne 0) { throw 'Native launcher build failed.' }
& powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'launcher\Launcher.ps1') -Validate
if ($LASTEXITCODE -ne 0) { throw 'WPF validation failed.' }
