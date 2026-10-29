function Test-LocalServerOpen {
    $entry = Join-Path $taskRoot 'server\Server.exe'
    foreach ($process in @(Get-Process -Name Server -ErrorAction SilentlyContinue)) {
        try {
            if ($process.Path -and [string]::Equals($process.Path, $entry, [StringComparison]::OrdinalIgnoreCase)) { return $true }
        } catch { }
    }
    return $false
}

function Open-LocalServer {
    $entry = Join-Path $taskRoot 'server\Server.exe'
    if (-not (Test-Path -LiteralPath $entry -PathType Leaf)) {
        throw '缺少内置服务端，请使用包含本地开服组件的完整安装包。'
    }
    # The manager owns its services independently of the game and launcher.
    # This button explicitly opens its interactive window.
    Start-Process -FilePath $entry -WorkingDirectory (Split-Path -Parent $entry) -WindowStyle Normal
}

function Use-LocalServer {
    if ($script:worker -or $script:saveTask -or $script:adminTask -or $script:conversionTask) {
        throw '请等待当前游戏或操作结束后再切换联机地址。'
    }
    $hostAddress = '127.0.0.1'
    $config = Join-Path $taskRoot 'server\server.json'
    if (Test-Path -LiteralPath $config) {
        $hostAddress = [string](([IO.File]::ReadAllText($config) | ConvertFrom-Json).address)
    }
    $parsedAddress = $null
    if (-not [Net.IPAddress]::TryParse($hostAddress, [ref]$parsedAddress) -or $parsedAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or $hostAddress -eq '0.0.0.0') {
        throw '请先在本地开服管理窗口配置有效的本机 IPv4 地址。'
    }
    $controls.Shadnet.Text = $hostAddress + ':31413'
    $controls.Webapi.Text = 'http://' + $hostAddress + ':31415'
    $controls.Community.Text = 'http://' + $hostAddress + ':20443'
    $controls.Relay.Text = $hostAddress + ':20444'
    Show-Settings
    $controls.StatusText.Text = '已填入本地服务配置的地址；请填写本服务创建的账号并保存。127.0.0.1 仅限同机，同伴使用开服电脑的局域网或 VPN 地址。'
}

$controls.OpenLocalServer.Add_Click({
    try { Open-LocalServer } catch { Show-Error $_.Exception.Message }
})
$controls.UseLocalServer.Add_Click({
    try { Use-LocalServer } catch { Show-Error $_.Exception.Message }
})
