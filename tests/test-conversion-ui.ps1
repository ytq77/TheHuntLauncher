$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase,System.Xaml
$repoPath = Split-Path -Parent $PSScriptRoot
$taskRoot = Join-Path ([IO.Path]::GetTempPath()) ('the-hunt-conversion-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($taskRoot)
$script:worker = $null
$script:saveTask = $null
$script:adminTask = $null
$utf8 = New-Object Text.UTF8Encoding $false
try {
    $window = [Windows.Markup.XamlReader]::Parse([IO.File]::ReadAllText((Join-Path $repoPath 'launcher\Launcher.xaml')))
    $controls = @{}
    foreach ($name in @('ConversionNew','ConversionBaseline','ConversionInspect','ConversionConvert','ConversionVerify','ConversionExport',
                         'ConversionStartExport','ConversionStartReplace','ConversionSameRole','ConversionClearSlots','ConversionConfirmed','ConversionOpen','ConversionOpenOrdinaryBackup','ConversionStatus','ConversionPath')) {
        $controls[$name] = $window.FindName($name)
        if (-not $controls[$name]) { throw ('Missing conversion control: ' + $name) }
    }
    function Show-Error([string]$message) { $script:uiFailure = $message }
    function Set-BusyControls { Update-ConversionButtons $false }
    . (Join-Path $repoPath 'launcher\Conversion.ps1')
    $steps = @{ created='baseline'; baselined='inspect'; inspected='convert'; converted='verify'; 'awaiting-confirmation'='export' }
    foreach ($state in $steps.Keys) {
        Show-ConversionJob ([pscustomobject]@{state=$state; next=$steps[$state]; id=('a'*32); message='test'; directory=$taskRoot; slot=1})
        Update-ConversionButtons $false
        foreach ($name in $conversionActions.Keys) {
            $want = $conversionActions[$name] -in @('create','start-export','start-replace') -or $conversionActions[$name] -eq $steps[$state]
            if ($controls[$name].IsEnabled -ne $want) { throw ('Wrong step binding: ' + $name) }
        }
    }
    Update-ConversionButtons $true
    foreach ($name in $conversionActions.Keys) { if ($controls[$name].IsEnabled) { throw 'Busy operation was not blocked.' } }
    # The real backup after a replacement takes priority over the task copy.
    $ordinaryBackup = Join-Path $taskRoot 'old-ordinary\savedata'
    $taskBackup = Join-Path $taskRoot 'backup\ordinary\savedata'
    [void][IO.Directory]::CreateDirectory($ordinaryBackup)
    [void][IO.Directory]::CreateDirectory($taskBackup)
    Show-ConversionJob ([pscustomobject]@{state='applied'; next=$null; id=('a'*32); message='test'; directory=$taskRoot; ordinary_backup=$ordinaryBackup})
    Update-ConversionButtons $false
    if (-not $controls.ConversionOpenOrdinaryBackup.IsEnabled -or (Get-ConversionOrdinaryBackupPath) -ne $ordinaryBackup) { throw 'Applied original backup path failed.' }
    function Open-ConversionDirectory([string]$path) { $script:openedDirectory = $path }
    $controls.ConversionOpenOrdinaryBackup.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent)))
    if ($script:openedDirectory -ne $ordinaryBackup) { throw 'Original backup click opened the wrong directory.' }
    Show-ConversionJob ([pscustomobject]@{state='exported'; next=$null; id=('a'*32); message='test'; directory=$taskRoot})
    Update-ConversionButtons $false
    $controls.ConversionOpenOrdinaryBackup.RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent)))
    if ($script:openedDirectory -ne $taskBackup) { throw 'Export original backup fallback failed.' }
    Update-ConversionButtons $true
    if ($controls.ConversionOpenOrdinaryBackup.IsEnabled) { throw 'Backup browse remained enabled during an operation.' }
    $folder = Join-Path $taskRoot ('conversion-jobs\' + ('a'*32))
    [void][IO.Directory]::CreateDirectory($folder)
    [IO.File]::WriteAllText((Join-Path $taskRoot 'conversion-current.json'), '{"job":"' + ('a'*32) + '"}', $utf8)
    $record = @{id=('a'*32); state='applied'; message='test'; ordinary_backup=$ordinaryBackup} | ConvertTo-Json
    [IO.File]::WriteAllText((Join-Path $folder 'job.json'), $record, $utf8)
    Read-ConversionProgress
    if ((Get-ConversionOrdinaryBackupPath) -ne $ordinaryBackup) { throw 'Persisted original backup path was lost.' }
    $script:conversionJob.ordinary_backup = Join-Path $taskRoot 'missing'
    Update-ConversionButtons $false
    if ($controls.ConversionOpenOrdinaryBackup.IsEnabled) { throw 'Missing original backup remained enabled.' }
    # Exercise actual WPF event closures without starting a worker or game.
    function Start-Conversion([string]$action) { $script:observedAction = $action }
    foreach ($name in $conversionActions.Keys) {
        $controls[$name].IsEnabled = $true
        $script:observedAction = $null
        $controls[$name].RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Button]::ClickEvent)))
        if ($script:uiFailure -or $script:observedAction -ne $conversionActions[$name]) {
            throw ('Conversion event binding failed: ' + $name + ' ' + $script:uiFailure)
        }
    }
    'PASS: WPF conversion controls, busy gates, entry bindings and original backup browse/persistence.'
} finally {
    $fullPath = [IO.Path]::GetFullPath($taskRoot)
    $tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $fullPath.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase) -or (Split-Path -Leaf $fullPath) -notlike 'the-hunt-conversion-*') { throw 'Unexpected test cleanup path.' }
    Remove-Item -LiteralPath $fullPath -Recurse -Force
}
