$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$out = Join-Path $repo '.build\BBSC-Launcher.exe'
[void][IO.Directory]::CreateDirectory((Split-Path -Parent $out))
& (Join-Path $PSScriptRoot 'make-icon.ps1')
$icon = Join-Path $repo '.build\TheHunt.ico'
& $compiler /nologo /target:winexe /codepage:65001 /reference:System.Windows.Forms.dll ("/win32icon:" + $icon) ("/out:" + $out) (Join-Path $repo 'launcher\Entry.cs')
if ($LASTEXITCODE -ne 0) { throw 'Native launcher build failed.' }
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$references = @('PresentationCore.dll','PresentationFramework.dll','WindowsBase.dll') | ForEach-Object { '/reference:' + (Join-Path $framework ('WPF\' + $_)) }
$references += '/reference:System.Web.Extensions.dll'
$references += '/reference:' + (Join-Path $framework 'System.Xaml.dll')
& $compiler /nologo /target:library /codepage:65001 $references ("/out:" + (Join-Path $repo '.build\HuntUpdates.dll')) (Join-Path $repo 'launcher\HuntUpdates.cs')
if ($LASTEXITCODE -ne 0) { throw 'Update module build failed.' }
& powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'tests\test-updates.ps1') -AssemblyPath (Join-Path $repo '.build\HuntUpdates.dll')
if ($LASTEXITCODE -ne 0) { throw 'Update selection checks failed.' }
& powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'launcher\Launcher.ps1') -Validate
if ($LASTEXITCODE -ne 0) { throw 'WPF validation failed.' }
& powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'tests\test-feature-ui.ps1')
if ($LASTEXITCODE -ne 0) { throw 'WPF feature persistence checks failed.' }
& powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'tests\test-conversion-ui.ps1')
if ($LASTEXITCODE -ne 0) { throw 'WPF conversion workflow checks failed.' }
& powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'tests\test-local-server-ui.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Local server entry checks failed.' }
