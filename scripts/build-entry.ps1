$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$out = Join-Path $repo '.build\BBSC-Launcher.exe'
[void][IO.Directory]::CreateDirectory((Split-Path -Parent $out))
& (Join-Path $PSScriptRoot 'make-icon.ps1')
$icon = Join-Path $repo '.build\TheHunt.ico'
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$references = @('PresentationCore.dll','PresentationFramework.dll','WindowsBase.dll') | ForEach-Object { '/reference:' + (Join-Path $framework ('WPF\' + $_)) }
$references += '/reference:System.Web.Extensions.dll'
$references += '/reference:' + (Join-Path $framework 'System.Xaml.dll')
$references += '/reference:System.Xml.Linq.dll'
$references += '/reference:System.Security.dll'
& $compiler /nologo /target:library /codepage:65001 $references ("/resource:" + (Join-Path $repo 'launcher\DesktopTheme.xaml') + ',TheHunt.Theme.xaml') ("/out:" + (Join-Path $repo '.build\HuntUpdates.dll')) (Join-Path $repo 'launcher\HuntUpdates.cs') (Join-Path $repo 'launcher\HuntTheme.cs') (Join-Path $repo 'launcher\HuntTypography.cs')
if ($LASTEXITCODE -ne 0) { throw 'Update module build failed.' }
& $compiler /nologo /target:winexe /codepage:65001 $references ("/reference:" + (Join-Path $repo '.build\HuntUpdates.dll')) ("/win32icon:" + $icon) ("/out:" + $out) (Join-Path $repo 'launcher\Entry.cs') (Join-Path $repo 'launcher\DesktopWindow.cs') (Join-Path $repo 'launcher\DesktopCredentials.cs') (Join-Path $repo 'launcher\TrophySession.cs') (Join-Path $repo 'launcher\TrophyToast.cs')
if ($LASTEXITCODE -ne 0) { throw 'Native WPF launcher build failed.' }
& powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'tests\test-updates.ps1') -AssemblyPath (Join-Path $repo '.build\HuntUpdates.dll')
if ($LASTEXITCODE -ne 0) { throw 'Update selection checks failed.' }
& powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'tests\test-desktop.ps1') -AssemblyPath $out
if ($LASTEXITCODE -ne 0) { throw 'Native WPF desktop checks failed.' }
& powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $repo 'tests\test-game-window.ps1') -AssemblyPath $out
if ($LASTEXITCODE -ne 0) { throw 'Packaged game window discovery check failed.' }
