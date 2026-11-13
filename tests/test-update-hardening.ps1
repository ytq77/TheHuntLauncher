param([Parameter(Mandatory=$true)][string]$BuildDirectory)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$build = [IO.Path]::GetFullPath($BuildDirectory)
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$references = @('PresentationCore.dll','PresentationFramework.dll','WindowsBase.dll') | ForEach-Object {
    '/reference:' + (Join-Path $framework ('WPF\' + $_))
}
$references += '/reference:' + (Join-Path $framework 'System.Xaml.dll')
$references += '/reference:System.Web.Extensions.dll'
$references += '/reference:' + (Join-Path $build 'HuntUpdates.dll')
$references += '/reference:' + (Join-Path $build 'TheHuntLauncher.exe')
$executable = Join-Path $build 'UpdateHardeningTest.exe'
& (Join-Path $framework 'csc.exe') /nologo /utf8output /target:exe /codepage:65001 $references ('/out:' + $executable) (Join-Path $PSScriptRoot 'update-hardening-ui-test.cs')
if ($LASTEXITCODE) { throw 'Hardening regression compile failed' }
$area = Join-Path $build ('fixtures-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($area)
$arguments = @('"' + $repo + '"', '"' + $area + '"', '"' + (Join-Path $build 'Server.exe') + '"')
$process = Start-Process -FilePath $executable -ArgumentList $arguments -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $area 'stdout.txt') -RedirectStandardError (Join-Path $area 'stderr.txt')
$handle = $process.Handle
if (-not $process.WaitForExit(30000)) {
    Stop-Process -Id $process.Id -Force
    throw "Hardening regression timed out; evidence: $area"
}
Get-Content -LiteralPath (Join-Path $area 'stdout.txt')
$process.Refresh()
if ($process.ExitCode -ne 0 -or -not (Select-String -LiteralPath (Join-Path $area 'stdout.txt') -SimpleMatch 'PASS: ALL hardening regressions completed' -Quiet)) {
    Get-Content -LiteralPath (Join-Path $area 'stderr.txt')
    throw "Hardening regression failed; evidence: $area"
}
