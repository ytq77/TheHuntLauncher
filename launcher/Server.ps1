$script:adminTask = $null
$script:adminAction = 'status'
$script:adminConfigPath = Join-Path $taskRoot 'server-admin.json'

function Admin-Connection {
    $port = 0
    if (-not [int]::TryParse($controls.AdminPort.Text, [ref]$port) -or $port -lt 1 -or $port -gt 65535) { throw 'SSH 端口必须在 1–65535。' }
    $target = $controls.AdminTarget.Text.Trim()
    $directory = $controls.AdminDirectory.Text.Trim()
    if ($target -notmatch '^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,200}$') { throw '请填写 SSH 别名或 用户@主机，不含空格和命令。' }
    if ($directory -notmatch '^/opt/[A-Za-z0-9_./-]+$' -or '..' -in $directory.Split('/')) { throw '请填写 /opt 下的实际 BBSC Compose 目录。' }
    return @{ target=$target; port=$port; directory=$directory; identity=$controls.AdminIdentity.Text.Trim(); known_hosts=$controls.AdminKnownHosts.Text.Trim(); sudo=[bool]$controls.AdminSudo.IsChecked }
}
function Start-Admin([string]$action) {
    if ($script:adminTask -or $script:worker -or $script:saveTask -or (Get-Process bb-probe -ErrorAction SilentlyContinue)) { throw '请先退出本机游戏，并等待当前操作结束。' }
    $connection = Admin-Connection
    $request = @{ connection=$connection; action=$action }
    if ($action -ne 'status') {
        $name = $controls.AdminName.Text.Trim()
        $password = $controls.AdminPassword.Password
        if ($name -notmatch '^[A-Za-z0-9_-]{3,16}$' -or -not $password) { throw '请填写 3–16 位账号名和待验证／新建的密码。' }
        $fields = @{ name=$name; password=$password }
        if ($action -eq 'create') {
            if ($controls.AdminEmail.Text.Trim() -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') { throw '新建账号需要不重复的有效邮箱标识。' }
            $message = '将在 ' + $connection.target + ' 的 ' + $connection.directory + ' 新建账号：' + $name + "。`n`n先备份账号数据库，再通过服务注册并验证密码。不会改变现有游戏登录。继续？"
            if ([Windows.MessageBox]::Show($window, $message, '新建联机账号', 'YesNo', 'Question', 'No') -ne 'Yes') { return }
            $fields.email = $controls.AdminEmail.Text.Trim()
            $fields.confirmed = $true
        } else {
            if ([Windows.MessageBox]::Show($window, '这会短暂登录指定账号进行验证。请确认该账号当前未在任何电脑游玩。继续？', '验证账号密码', 'YesNo', 'Question', 'No') -ne 'Yes') { return }
        }
        $request.fields = $fields
    }
    $python = Join-Path $taskRoot 'runtime\bin\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw '请使用完整安装包；源码目录不执行服务器管理。' }
    [IO.File]::WriteAllText($script:adminConfigPath, ($connection | ConvertTo-Json), $utf8)
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $python
    $info.Arguments = '-B "' + (Join-Path $taskRoot 'server_admin.py') + '"'
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
    $script:adminOutput = $process.StandardOutput.ReadToEndAsync()
    $script:adminError = $process.StandardError.ReadToEndAsync()
    try {
        $process.StandardInput.Write(($request | ConvertTo-Json -Depth 6 -Compress))
        $process.StandardInput.Close()
    } catch { $process.Dispose(); throw '无法提交管理请求，请重试。' }
    finally { $controls.AdminPassword.Clear(); $password = $null; $request = $null }
    $script:adminTask = $process
    $script:adminAction = $action
    $controls.AdminStatus.Text = '正在通过 SSH 检查服务器，请稍候…'
    Set-BusyControls
}
function Update-AdminTask {
    if (-not $script:adminTask -or -not $script:adminTask.HasExited -or -not $script:adminOutput.IsCompleted -or -not $script:adminError.IsCompleted) { return }
    try {
        $result = $script:adminOutput.Result | ConvertFrom-Json
        if ($script:adminTask.ExitCode -ne 0 -or -not $result.ok) {
            if ($result.error) { throw $result.error }
            throw '管理请求未完成，请检查 SSH 及服务器环境。'
        }
        if ($result.accounts) {
            $lines = @($result.accounts | ForEach-Object { ('{0}  ·  {1}  ·  {2}' -f $_.id,$_.name,$(if ($_.banned) {'已封禁'} elseif ($_.admin) {'管理员'} else {'普通账号'})) })
            $controls.AdminAccounts.Text = $lines -join "`n"
        } else { $controls.AdminAccounts.Text = '没有返回账号记录。' }
        if ($script:adminAction -eq 'status') {
            $lines = @($result.services | ForEach-Object { $_.Service + '：' + $_.State + ' / ' + $_.Health })
            $controls.AdminStatus.Text = '服务器：' + $result.server_hostname + "`n" + ($lines -join "`n") + "`n" + $result.notice
        } else {
            $controls.AdminStatus.Text = $result.message
            if ($result.backup) { $controls.AdminStatus.Text += "`n服务器账号备份：backups/" + $result.backup }
        }
    } catch { $controls.AdminStatus.Text = $_.Exception.Message }
    finally { $script:adminTask.Dispose(); $script:adminTask = $null; Set-BusyControls }
}
if (Test-Path -LiteralPath $script:adminConfigPath) {
    try {
        $connection = [IO.File]::ReadAllText($script:adminConfigPath) | ConvertFrom-Json
        $controls.AdminTarget.Text = $connection.target
        $controls.AdminPort.Text = [string]$connection.port
        $controls.AdminDirectory.Text = $connection.directory
        $controls.AdminIdentity.Text = $connection.identity
        $controls.AdminKnownHosts.Text = $connection.known_hosts
        $controls.AdminSudo.IsChecked = [bool]$connection.sudo
    } catch { $controls.AdminStatus.Text = '管理员连接设置无法读取，请重新填写。' }
}
$controls.PickAdminIdentity.Add_Click({
    $path = Pick-File '选择已有 SSH 私钥文件（不读取或复制密钥内容）' '所有文件|*.*'
    if ($path) { $controls.AdminIdentity.Text = $path }
})
$controls.PickAdminKnownHosts.Add_Click({
    $path = Pick-File '选择已有的可信 SSH known_hosts 文件' '所有文件|*.*'
    if ($path) { $controls.AdminKnownHosts.Text = $path }
})
$controls.AdminRefresh.Add_Click({ try { Start-Admin 'status' } catch { Show-Error $_.Exception.Message } })
$controls.AdminCreate.Add_Click({ try { Start-Admin 'create' } catch { Show-Error $_.Exception.Message } })
$controls.AdminVerify.Add_Click({ try { Start-Admin 'verify' } catch { Show-Error $_.Exception.Message } })
