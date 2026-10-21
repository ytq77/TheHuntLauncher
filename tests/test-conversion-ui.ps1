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
                         'ConversionSameRole','ConversionClearSlots','ConversionConfirmed','ConversionOpen','ConversionStatus','ConversionPath')) {
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
            $want = $conversionActions[$name] -eq 'create' -or $conversionActions[$name] -eq $steps[$state]
            if ($controls[$name].IsEnabled -ne $want) { throw ('Wrong step binding: ' + $name) }
        }
    }
    Update-ConversionButtons $true
    foreach ($name in $conversionActions.Keys) { if ($controls[$name].IsEnabled) { throw 'Busy operation was not blocked.' } }
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
    'PASS: WPF conversion controls, step gates, busy state, and all six click bindings.'
} finally {
    $fullPath = [IO.Path]::GetFullPath($taskRoot)
    $tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $fullPath.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase) -or (Split-Path -Leaf $fullPath) -notlike 'the-hunt-conversion-*') { throw 'Unexpected test cleanup path.' }
    Remove-Item -LiteralPath $fullPath -Recurse -Force
}
