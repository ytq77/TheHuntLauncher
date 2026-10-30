param([Parameter(Mandatory=$true)][string]$AssemblyPath,[switch]$ExpectMissing)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase,System.Xaml
[void][Reflection.Assembly]::LoadFrom((Join-Path (Split-Path $AssemblyPath) 'HuntUpdates.dll'))
[void][Reflection.Assembly]::LoadFrom($AssemblyPath)
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('the-hunt-game-window-'+[Guid]::NewGuid().ToString('N'))
$bin=Join-Path $fixture 'bin'; [void][IO.Directory]::CreateDirectory($bin)
$process=$null
try {
    $framework=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
    $references=@('PresentationCore.dll','PresentationFramework.dll','WindowsBase.dll') | ForEach-Object { '/reference:'+(Join-Path $framework ('WPF\'+$_)) }
    $references += '/reference:'+(Join-Path $framework 'System.Xaml.dll')
    $exe=Join-Path $bin 'bb-probe.exe'
    & (Join-Path $framework 'csc.exe') /nologo /target:winexe $references ('/out:'+$exe) (Join-Path $PSScriptRoot 'game-window-probe.cs')
    if($LASTEXITCODE) { throw 'Window probe compilation failed' }
    $result=Join-Path $fixture 'handle.txt'
    $process=Start-Process -FilePath $exe -ArgumentList ('"'+$result+'"') -PassThru -WindowStyle Hidden
    $deadline=[DateTime]::UtcNow.AddSeconds(10)
    while(-not (Test-Path -LiteralPath $result)) { if($process.HasExited -or [DateTime]::UtcNow -gt $deadline) { throw 'Window probe did not initialize' }; Start-Sleep -Milliseconds 50 }
    $expected=[long][IO.File]::ReadAllText($result)
    $flags=[Reflection.BindingFlags]'Instance,NonPublic'
    $desktop=[Runtime.Serialization.FormatterServices]::GetUninitializedObject([DesktopWindow])
    [DesktopWindow].GetField('root',$flags).SetValue($desktop,$fixture)
    $actual=[DesktopWindow].GetMethod('GameHandle',$flags).Invoke($desktop,@()).ToInt64()
    if($ExpectMissing) {
        if($actual -ne 0) { throw 'Expected to reproduce the old wrong-path lookup' }
        'REPRODUCED: old launcher cannot find the real bin/bb-probe.exe window.'
    } else {
        if($actual -ne $expected -or $expected -eq 0) { throw ('Wrong game window: actual '+$actual+' expected '+$expected) }
        'PASS: game window discovery matches the real bin/bb-probe.exe layout and exact native HWND.'
    }
} finally {
    if($process -and -not $process.HasExited) { $process.Kill(); $process.WaitForExit() }
    if($process) { $process.Dispose() }
    $full=[IO.Path]::GetFullPath($fixture); $base=[IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if(-not $full.StartsWith($base,[StringComparison]::OrdinalIgnoreCase) -or (Split-Path -Leaf $full) -notlike 'the-hunt-game-window-*') { throw 'Unexpected window-test cleanup path' }
    Remove-Item -LiteralPath $full -Recurse -Force
}
