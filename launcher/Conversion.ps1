$script:conversionTask = $null
$script:conversionJob = $null
$conversionActions = @{ ConversionNew='create'; ConversionBaseline='baseline'; ConversionInspect='inspect';
    ConversionConvert='convert'; ConversionVerify='verify'; ConversionExport='export';
    ConversionStartExport='start-export'; ConversionStartReplace='start-replace' }

function Update-ConversionButtons([bool]$busy) {
    foreach ($name in $conversionActions.Keys) {
        $action = $conversionActions[$name]
        $controls[$name].IsEnabled = -not $busy -and ($action -in @('create','start-export','start-replace') -or
            ($script:conversionJob -and $script:conversionJob.next -eq $action))
    }
    foreach ($name in @('ConversionSameRole','ConversionClearSlots','ConversionConfirmed')) { $controls[$name].IsEnabled = -not $busy }
}
function Show-ConversionJob($job) {
    $script:conversionJob = $job
    if ($job -and $job.state -ne 'none') {
        $controls.ConversionStatus.Text = $job.message
        $controls.ConversionPath.Text = $job.directory
        if ($null -ne $job.slot) { $controls.ConversionStatus.Text = ('目标槽位：userdata{0:D4}' -f [int]$job.slot) + "`n" + $job.message }
        if ($job.export) { $controls.ConversionPath.Text = $job.export }
    } else {
        $controls.ConversionStatus.Text = '请确认两份存档属于同一角色，再选择离线导出或替换。'
        $controls.ConversionPath.Text = ''
    }
}
function Read-ConversionProgress {
    $pointer = Join-Path $taskRoot 'conversion-current.json'
    if (-not (Test-Path -LiteralPath $pointer)) { return }
    try {
        $id = ([IO.File]::ReadAllText($pointer) | ConvertFrom-Json).job
        if ($id -notmatch '^[0-9a-f]{32}$') { return }
        $folder = Join-Path $taskRoot ('conversion-jobs\' + $id)
        $job = [IO.File]::ReadAllText((Join-Path $folder 'job.json')) | ConvertFrom-Json
        $next = @{ created='baseline'; baselined='inspect'; inspected='convert'; converted='verify'; 'awaiting-confirmation'='export' }
        $view = [pscustomobject]@{ id=$job.id; state=$job.state; message=$job.message; slot=$job.slot;
            next=$next[$job.state]; directory=$folder; export=$job.export }
        if ($job.state -eq 'running' -and -not $script:conversionTask) {
            $view.message = '检测到未结束的测试记录。若游戏和旧操作均已退出，请新建测试；原始存档未替换。'
            $view.next = $null
        }
        Show-ConversionJob $view
    } catch { } # Atomic job records are reread on the next UI tick.
}
function Start-Conversion([string]$action) {
    if ($script:worker -or $script:saveTask -or $script:adminTask -or $script:conversionTask -or
        (Get-Process bb-probe -ErrorAction SilentlyContinue)) { throw '请先正常退出游戏，并等待当前操作完成。' }
    $python = Join-Path $taskRoot 'runtime\bin\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw '请使用完整测试包。' }
    $request = @{ action=$action }
    if ($action -in @('create','start-export','start-replace')) {
        if (-not $controls.ConversionSameRole.IsChecked) { throw '请先确认普通版与无缝版对应同一角色。' }
        foreach ($name in @('InstallPath','GamePath','GraphicsPath')) {
            if (-not (Test-Path -LiteralPath $controls[$name].Text -PathType Leaf)) { throw '请先在「游戏与联机」填写本机游戏和画质配置路径。' }
        }
        $request.profile = @{ install=[IO.Path]::GetDirectoryName($controls.InstallPath.Text);
            game=[IO.Path]::GetDirectoryName($controls.GamePath.Text); graphics_source=$controls.GraphicsPath.Text }
        $request.same_role = $true
        if ($action -ne 'create') {
            $request.action = 'begin'
            $request.flow = 'offline-v1'
            $request.output = $(if ($action -eq 'start-replace') { 'replace' } else { 'export' })
        }
        $controls.ConversionClearSlots.IsChecked = $false
        $controls.ConversionConfirmed.IsChecked = $false
    } else {
        if (-not $script:conversionJob -or -not $script:conversionJob.id) { throw '请先新建测试副本。' }
        $request.job = $script:conversionJob.id
        if ($action -eq 'convert') {
            if (-not $controls.ConversionClearSlots.IsChecked) { throw '请先检查并卸下无缝五件道具的快捷栏／随身物品装备，再勾选确认。' }
            $request.cleared = $true
        }
        if ($action -eq 'export') {
            if (-not $controls.ConversionConfirmed.IsChecked) { throw '请先用普通版重载，并确认道具与较新进度均正确。' }
            $request.confirmed = $true
        }
    }
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $python
    $info.Arguments = '-B "' + (Join-Path $taskRoot 'conversion_jobs.py') + '"'
    $info.WorkingDirectory = $taskRoot
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WindowStyle = 'Hidden'
    $info.RedirectStandardInput = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.EnvironmentVariables['PYTHONHOME'] = Join-Path $taskRoot 'runtime'
    $info.EnvironmentVariables['PYTHONUTF8'] = '1'
    $info.EnvironmentVariables['PATH'] = (Join-Path $taskRoot 'runtime\bin') + ';' + $env:PATH
    $process = [Diagnostics.Process]::Start($info)
    $script:conversionOutput = $process.StandardOutput.ReadToEndAsync()
    $script:conversionError = $process.StandardError.ReadToEndAsync()
    $process.StandardInput.Write(($request | ConvertTo-Json -Depth 6 -Compress))
    $process.StandardInput.Close()
    $script:conversionTask = $process
    $controls.ConversionStatus.Text = $(if ($request.flow -eq 'offline-v1') { '正在备份并离线转换，不会打开游戏…' } else { '正在准备独立副本；游戏窗口打开后，请只加载本轮目标角色。' })
    Set-BusyControls
}
function Update-ConversionTask {
    Read-ConversionProgress
    if (-not $script:conversionTask -or -not $script:conversionTask.HasExited -or -not $script:conversionOutput.IsCompleted -or -not $script:conversionError.IsCompleted) { return }
    try {
        $result = $script:conversionOutput.Result | ConvertFrom-Json
        if ($script:conversionTask.ExitCode -ne 0 -or -not $result.ok) {
            if ($result.error) { throw $result.error }
            throw '转换工具未返回有效结果，请保留测试目录并查看日志。'
        }
        Show-ConversionJob $result.job
    } catch { $controls.ConversionStatus.Text = $_.Exception.Message }
    finally { $script:conversionTask.Dispose(); $script:conversionTask = $null; Set-BusyControls }
}
foreach ($name in $conversionActions.Keys) {
    $action = $conversionActions[$name]
    $handler = { try { Start-Conversion $action } catch { Show-Error $_.Exception.Message } }.GetNewClosure()
    $controls[$name].Add_Click($handler)
}
$controls.ConversionOpen.Add_Click({
    if ($script:conversionJob -and (Test-Path -LiteralPath $script:conversionJob.directory)) {
        [void][Diagnostics.Process]::Start('explorer.exe', ('"' + $script:conversionJob.directory + '"'))
    }
})
Read-ConversionProgress
Update-ConversionButtons $false
