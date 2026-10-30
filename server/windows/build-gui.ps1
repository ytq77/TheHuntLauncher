$ErrorActionPreference = 'Stop'
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$updates = Join-Path $PSScriptRoot 'gui\HuntUpdates.cs'
$sharedUpdates = Join-Path $PSScriptRoot '..\..\launcher\HuntUpdates.cs'
if (Test-Path -LiteralPath $sharedUpdates) { $updates = $sharedUpdates }
$themeSource = Join-Path $PSScriptRoot 'gui\HuntTheme.cs'
$themeXaml = Join-Path $PSScriptRoot 'gui\DesktopTheme.xaml'
$typographySource = Join-Path $PSScriptRoot 'gui\HuntTypography.cs'
$assets = Join-Path $PSScriptRoot 'gui\assets'
if (Test-Path -LiteralPath $sharedUpdates) {
    $themeSource = Join-Path $PSScriptRoot '..\..\launcher\HuntTheme.cs'
    $themeXaml = Join-Path $PSScriptRoot '..\..\launcher\DesktopTheme.xaml'
    $typographySource = Join-Path $PSScriptRoot '..\..\launcher\HuntTypography.cs'
    $assets = Join-Path $PSScriptRoot '..\..\assets'
}
$output = Join-Path $PSScriptRoot 'build-ui'
[void][IO.Directory]::CreateDirectory($output)
$assetOutput = [IO.Path]::GetFullPath((Join-Path $output 'assets'))
$outputBase = [IO.Path]::GetFullPath($output).TrimEnd('\') + '\'
if (-not $assetOutput.StartsWith($outputBase, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected build asset path.' }
if (Test-Path -LiteralPath $assetOutput) { Remove-Item -LiteralPath $assetOutput -Recurse -Force }
Copy-Item -LiteralPath $assets -Destination $output -Recurse -Force
& (Join-Path $PSScriptRoot 'make-icon.ps1') -OutputDirectory $output
if ($LASTEXITCODE) { throw 'Icon generation failed.' }
$references = @('PresentationCore.dll','PresentationFramework.dll','WindowsBase.dll') | ForEach-Object {
    '/reference:' + (Join-Path $framework ('WPF\' + $_))
}
$references += '/reference:' + (Join-Path $framework 'System.Xaml.dll')
$references += '/reference:System.Web.Extensions.dll'
$references += '/reference:System.Xml.Linq.dll'
& (Join-Path $framework 'csc.exe') /nologo /target:winexe /utf8output /codepage:65001 $references `
    ('/win32icon:' + (Join-Path $output 'TheHunt.ico')) `
    ('/resource:' + (Join-Path $PSScriptRoot 'gui\ServerWindow.xaml') + ',TheHuntServer.Window.xaml') `
    ('/resource:' + $themeXaml + ',TheHunt.Theme.xaml') `
    ('/out:' + (Join-Path $output 'Server.exe')) (Join-Path $PSScriptRoot 'gui\ServerWindow.cs') $updates $themeSource $typographySource
if ($LASTEXITCODE) { throw 'Desktop window compilation failed.' }
$process = Start-Process -FilePath (Join-Path $output 'Server.exe') -ArgumentList '--validate' -Wait -PassThru -WindowStyle Hidden
if ($process.ExitCode) { throw 'WPF window validation failed.' }
& powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'test-updates.ps1') -AssemblyPath (Join-Path $output 'Server.exe')
if ($LASTEXITCODE) { throw 'Update selection checks failed.' }
Write-Output 'Native WPF manager compiled and validated.'
