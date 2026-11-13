param(
    [string]$Root = $PSScriptRoot,
    [string]$OutputDirectory = (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'TheHunt\Diagnostics'),
    [switch]$Deep
)
$ErrorActionPreference = 'Stop'
$rootPath = [IO.Path]::GetFullPath($Root).TrimEnd('\')
$utf8 = New-Object Text.UTF8Encoding($false)

# Do not follow symlinks or junctions into another profile, even in deep mode.
function Get-PlainItem([string]$Relative) {
    $path = $rootPath
    foreach ($part in ($Relative -split '/')) {
        $path = Join-Path $path $part
        if (-not (Test-Path -LiteralPath $path)) { return }
        $item = Get-Item -LiteralPath $path -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { return }
    }
    $item
}

function Read-Tail($Item, [int]$Limit) {
    $stream = [IO.File]::Open($Item.FullName, 'Open', 'Read', 'ReadWrite, Delete')
    try {
        $start = [Math]::Max(0, $stream.Length - $Limit)
        $stream.Position = $start
        $bytes = New-Object byte[] $Limit
        $count = 0
        while ($count -lt $Limit) {
            $read = $stream.Read($bytes, $count, $Limit - $count)
            if ($read -eq 0) { break }
            $count += $read
        }
        $value = $utf8.GetString($bytes, 0, $count).TrimStart([char]0xfeff)
        if ($start -gt 0) {
            $newline = $value.IndexOf("`n")
            if ($newline -lt 0) { return '' }
            $value = $value.Substring($newline + 1)
        }
        $value
    } finally { $stream.Dispose() }
}

function Add-Text([string]$Name, [string]$Value) {
    $writer = New-Object IO.StreamWriter($zip.CreateEntry($Name).Open(), $utf8)
    try { $writer.WriteLine($Value) } finally { $writer.Dispose() }
}

function Get-StreamSha256([IO.Stream]$Stream) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { [BitConverter]::ToString($sha.ComputeHash($Stream)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-ItemSha256($Item) {
    $stream = [IO.File]::Open($Item.FullName, 'Open', 'Read', 'ReadWrite, Delete')
    try { Get-StreamSha256 $stream } finally { $stream.Dispose() }
}

# Project known writer formats onto fixed text and bounded numeric fields.
# Raw messages, even unrecognized ones, never reach the ZIP.
function Get-Events([string]$Text) {
    foreach ($line in ($Text -split '\r?\n')) {
        switch -Regex ($line) {
            '^(正在核对游戏版本…|正在创建独立配置与存档副本…|正在复制本机 FSR 4 资源…|正在同步这台电脑的画质与操作设置…|游戏已启动，正在加载。道具准备情况会显示在这里。|游戏已退出，正在核对自己的世界与两代存档…|本次狩猎已结束，世界记录核对完成。)$' { $Matches[1]; break }
            '^私服认证失败，请核对账号、服务器状态及客户端/服务器版本。$' { 'Private server authentication failed'; break }
            '^私服尚未启用 Relay v2 认证，请同时更新客户端和服务器。$' { 'Relay v2 compatibility check failed'; break }
            '^(?:游戏退出，返回码 )(-?[0-9]{1,10})。' { 'Game exit code: ' + $Matches[1]; break }
            '\[WinError ([0-9]{1,5})\]' { 'WinError: ' + $Matches[1]; break }
            '^\[Errno ([0-9]{1,5})\]' { 'Errno: ' + $Matches[1]; break }
            '^Co-op: game server ' { 'Co-op: game server configured (address omitted)'; break }
            '^Co-op: The Hunter''s Dream as ' { 'Co-op: public sign-in (account and address omitted)'; break }
            '^Seamless co-op: its own saves in ' { 'Seamless co-op: separate save root'; break }
            '^Save backup: .*; the game starts without one$' { 'Save backup: failed'; break }
            '^Save backup: ' { 'Save backup: status recorded'; break }
            '^No eboot\.bin in ' { 'Game executable missing'; break }
            '^bb-probe\.exe (?:is missing|not found)' { 'BBport executable missing'; break }
            '^Game files: eboot\.bin is (?:not the 01\.09 build|modified)' { 'Game executable differs from the expected build'; break }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] [IW] Debug: BBSC (?:join placement skipped: local map |warp together: we are in map )(0x[0-9a-f]{1,8}), (?:the )?host (?:map |in )(0x[0-9a-f]{1,8})(?=[ (]|$)' {
                '[{0}] Join or follow: local map {1}, host map {2}' -f $Matches[1], $Matches[2], $Matches[3]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC connect-first: host is in map (0x[0-9a-f]{1,8}) \(guest in (0x[0-9a-f]{1,8})\);' {
                '[{0}] Initial placement: host map {1}, guest map {2}' -f $Matches[1], $Matches[2], $Matches[3]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC connect-first: no word from that host for a while ' {
                '[{0}] Initial placement: host location cleared' -f $Matches[1]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC join handoff: hint (none|transported|connect-first), local map (0x[0-9a-f]{1,8}), hint map (0x[0-9a-f]{1,8})$' {
                '[{0}] Join handoff: {1}, local map {2}, hint map {3}' -f $Matches[1], $Matches[2], $Matches[3], $Matches[4]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Mod: Relay: placement (received|ignored|revoked) generation=([0-9]{1,20}) room=([0-9]{1,20}) ttl=([0-9]{1,5})$' {
                '[{0}] Placement {1}: generation {2}, room {3}, TTL {4} ms' -f $Matches[1], $Matches[2], $Matches[3], $Matches[4], $Matches[5]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Mod: Relay: placement cleared reason=(context|expired|revoked|superseded) generation=([0-9]{1,20})$' {
                '[{0}] Placement cleared: {1}, generation {2}' -f $Matches[1], $Matches[2], $Matches[3]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC join recovery: (reload from 0x[0-9a-f]{1,8} into 0x[0-9a-f]{1,8}|arrived in map 0x[0-9a-f]{1,8})$' {
                '[{0}] Join recovery: {1}' -f $Matches[1], $Matches[2]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] W Debug: BBSC join recovery: ' {
                '[{0}] Join recovery stopped' -f $Matches[1]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC own world: complete recovery plan mode ([1-6]) slot ([0-9]) groups ([0-9]{1,5}) sequence ' {
                '[{0}] Recovery plan: mode {1}, slot {2}, groups {3}' -f $Matches[1], $Matches[2], $Matches[3], $Matches[4]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] ([A-Z]) (Mod|Debug): (BBSC mod loaded|BBSC mod ready|No Bloodborne CUSA03173 01\.09 image|Image verified|BBSC kit initialization|BBSC relay|BBSC guest return|BBSC own world|BBSC home save proof|BBSC save proof|BBSC guest drop|BBSC personal drops)(?=[: (]|$)' {
                '[{0}] {1} {2}: {3}' -f $Matches[1], $Matches[2], $Matches[3], $Matches[4]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC join teleport: guest moved from ' {
                '[{0}] Join: guest repositioned' -f $Matches[1]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC first spawn: same-map position applied \(map (0x[0-9a-f]{1,8})\)$' {
                '[{0}] First spawn: position applied in map {1}' -f $Matches[1], $Matches[2]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC first spawn: kept native position \((no-invitation|no-copy-before-reload|session|local-state|no-matching-grant|native-destination|write-rejected)\)$' {
                '[{0}] First spawn: fallback ({1})' -f $Matches[1], $Matches[2]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC join teleport: kept native position near the host$' {
                '[{0}] Join: kept native position near the host' -f $Matches[1]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC first spawn: armed in map (0x[0-9a-f]{1,8})$' {
                '[{0}] First spawn: armed in map {1}' -f $Matches[1], $Matches[2]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC first spawn: kept native position \(([a-z][a-z-]{1,31})\)(?: - ([a-z0-9/ ,-]{1,80}))?$' {
                if ($Matches[3]) { '[{0}] First spawn: fallback ({1}) - {2}' -f $Matches[1], $Matches[2], $Matches[3] }
                else { '[{0}] First spawn: fallback ({1})' -f $Matches[1], $Matches[2] }
                break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC first spawn: host position taken in map (0x[0-9a-f]{1,8}) \(native area (-?[0-9]{1,10}), host area (-?[0-9]{1,10})\) at ' {
                '[{0}] First spawn: host position taken in map {1} (native area {2}, host area {3})' -f $Matches[1], $Matches[2], $Matches[3], $Matches[4]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC first spawn: spawn descriptor set in map (0x[0-9a-f]{1,8}), ([0-9]{1,6}\.[0-9]{2}) m from the native spawn \(hit ([0-9]{1,3})\)$' {
                '[{0}] First spawn: spawn descriptor set in map {1}, {2} m from the native spawn (hit {3})' -f $Matches[1], $Matches[2], $Matches[3], $Matches[4]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC first spawn: guest arrived ([0-9]{1,6}\.[0-9]{2}) m from the spawn target \(height ([+-][0-9]{1,6}\.[0-9]{2})\)$' {
                '[{0}] First spawn: arrived {1} m from the target (height {2})' -f $Matches[1], $Matches[2], $Matches[3]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] I Debug: BBSC load cover: (fading out \([a-z]{1,16}\)|the game''s loading screen is up \(kind -?[0-9]{1,3}\)|the game''s loading screen closed after [0-9]{1,6} ms \([a-z]{1,16}\)|could not reopen the loading screen; only the black plate covers \([a-z]{1,16}\)|loading screen kept up until the guest is placed \([a-z]{1,16}\)|fading in after [0-9]{1,6} ms \([a-z]{1,16}; [a-z ,]{1,48}\))$' {
                '[{0}] Load cover: {1}' -f $Matches[1], $Matches[2]; break
            }
            '^\[(\d\d:\d\d:\d\d\.\d{3})\] ([EWC]) (Mod|Debug): ' {
                '[{0}] {1} {2}: message omitted' -f $Matches[1], $Matches[2], $Matches[3]; break
            }
        }
    }
}

function Add-Log([string]$Relative) {
    $item = Get-PlainItem $Relative
    if ($item -and -not $item.PSIsContainer) {
        try { $events = @(Get-Events (Read-Tail $item 524288)) }
        catch { $events = @('Log unavailable during collection.') }
        Add-Text $Relative ((@('Filtered events only; unrecognized text omitted.') + $events) -join "`n")
    }
}

function Add-RecentLogs([string]$Folder, [string]$NamePattern) {
    $dir = Get-PlainItem $Folder
    if (-not $dir -or -not $dir.PSIsContainer) { return }
    Get-ChildItem -LiteralPath $dir.FullName -File |
        Where-Object { $_.Name -match $NamePattern -and -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 4 |
        ForEach-Object { Add-Log ($Folder + '/' + $_.Name) }
}

function Add-RecoverySummary {
    $folder = 'data/user/world-recovery'
    $requests = @(foreach ($slot in 0..9) {
        $item = Get-PlainItem "$folder/requests/world-save-$slot.bin"
        if (-not $item -or $item.PSIsContainer) { continue }
        try {
            $stream = [IO.File]::Open($item.FullName, 'Open', 'Read', 'ReadWrite, Delete')
            try {
                $header = New-Object byte[] 68
                if ($stream.Read($header, 0, 68) -ne 68 -or
                    [Text.Encoding]::ASCII.GetString($header, 0, 4) -cne 'THWR' -or
                    [BitConverter]::ToUInt32($header, 4) -ne 2 -or
                    [BitConverter]::ToUInt32($header, 48) -ne $slot) { continue }
                $mode = [BitConverter]::ToUInt32($header, 52)
                $groups = [BitConverter]::ToUInt32($header, 56)
                if ($mode -in 1..6 -and $groups -ge 1 -and $groups -le 10000 -and
                    $stream.Length -eq (68 + 258 * $groups)) {
                    [ordered]@{ slot = $slot; mode = $mode; groups = $groups }
                }
            } finally { $stream.Dispose() }
        } catch { continue }
    })
    $item = Get-PlainItem "$folder/latest-result.json"
    $state = $null
    if ($item -and -not $item.PSIsContainer -and $item.Length -le 4096) {
        try {
            $last = Read-Tail $item 4096 | ConvertFrom-Json
            if ($last.state -is [string] -and $last.state -cin 'complete','waiting-backup') {
                $state = $last.state
            }
        } catch { }
    }
    Add-Text 'recovery-summary.json' (([ordered]@{
        requests = $requests
        last_recorded_result = $state
    }) | ConvertTo-Json -Depth 4)
}

# Only names produced by the save/recovery writers are eligible for raw copies.
function Add-PrivateTree([string]$Folder, [string]$Pattern, [string]$Relative = '') {
    $dir = Get-PlainItem ($Folder + $Relative)
    if (-not $dir -or -not $dir.PSIsContainer) { return }
    foreach ($item in (Get-ChildItem -LiteralPath $dir.FullName -Force)) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        $name = $Relative + '/' + $item.Name
        if ($item.PSIsContainer -and ($name -split '/').Count -lt 7) {
            Add-PrivateTree $Folder $Pattern $name
        } elseif (-not $item.PSIsContainer -and $name.Substring(1) -match $Pattern) {
            $length = $item.Length
            $modified = $item.LastWriteTimeUtc
            $path = $Folder + $name
            $hashes[$path] = Get-ItemSha256 $item
            [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $item.FullName, $path)
            $item.Refresh()
            if ($item.Length -ne $length -or $item.LastWriteTimeUtc -ne $modified) {
                throw '收集期间文件发生变化。请关闭游戏和启动器后重试。'
            }
        }
    }
}

if ((Get-Item -LiteralPath $rootPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw '请从实际安装目录运行收集器。'
}
$build = Get-PlainItem 'BUILD-INFO.json'
$dll = Get-PlainItem 'mod/bbsc.dll'
if (-not $build -or $build.PSIsContainer -or $build.Length -gt 262144 -or -not $dll -or $dll.PSIsContainer) {
    throw '请从实际使用的 The Hunt 客户端根目录运行收集器。'
}
if ($Deep) {
    Write-Host '警告：深度 ZIP 含完整存档、角色资料和恢复记录等私人信息。仅供本机保留；工具不会上传。' -ForegroundColor Yellow
    if ((Read-Host '请先关闭游戏和启动器。确认创建私人 ZIP 请输入 PRIVATE；其他输入取消') -cne 'PRIVATE') {
        Write-Host '已取消。'
        return
    }
    if (Get-Process -Name 'TheHuntLauncher','BBSC-Launcher','bb-probe','Bloodborne','eboot','shadPS4' -ErrorAction SilentlyContinue) {
        throw '请关闭游戏和启动器后再运行深度收集。'
    }
}
$out = [IO.Path]::GetFullPath($OutputDirectory)
if ($out.StartsWith('\\') -or ($out.TrimEnd('\') + '\').StartsWith($rootPath + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw '请将诊断 ZIP 保存到客户端目录之外的本机目录。'
}
$info = Read-Tail $build 262144 | ConvertFrom-Json
$version = [ordered]@{}
if ($info.version -is [string] -and $info.version -match '^[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}(?:-preview\.[0-9]{1,5})?\z') { $version['version'] = $info.version }
foreach ($key in 'bbport','bbsc') {
    if ($info.$key.version -is [string] -and $info.$key.version -match '^[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}(?:_items)?\z') { $version[$key] = $info.$key.version }
    if ($info.$key.commit -is [string] -and $info.$key.commit -match '^[a-f0-9]{40}\z') { $version[$key + '_commit'] = $info.$key.commit }
}
foreach ($key in 'core_sha256','bbsc_sha256','save_bridge_sha256') {
    if ($info.$key -is [string] -and $info.$key -match '^[a-f0-9]{64}\z') { $version[$key] = $info.$key }
}
if (-not $version['version']) { throw '无法识别客户端版本。' }
$version['installed_dll_sha256'] = Get-ItemSha256 $dll

Add-Type -AssemblyName System.IO.Compression, System.IO.Compression.FileSystem
[void][IO.Directory]::CreateDirectory($out)
$label = if ($Deep) { 'TheHunt-Diagnostics-PRIVATE-' } else { 'TheHunt-Diagnostics-' }
$archive = Join-Path $out ($label + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8) + '.zip')
$temporary = $archive + '.tmp'
$hashes = @{}
try {
    $zip = [IO.Compression.ZipFile]::Open($temporary, 'Create')
    try {
        Add-Text 'BUILD-INFO.json' ($version | ConvertTo-Json)
        Add-Text 'collected-at.json' ('{"utc":"' + [DateTime]::UtcNow.ToString('o') + '"}')
        Add-Log 'startup.log'
        Add-RecentLogs 'data/user' '^launch-\d{8}-\d{6}\.log$'
        Add-RecentLogs 'mod/logs' '^bbsc-mod-\d{8}-\d{6}-\d{1,10}\.log$'
        Add-RecoverySummary
        $last = Get-PlainItem 'data/user/last-exit.json'
        if ($last -and -not $last.PSIsContainer -and $last.Length -le 4096) {
            try { $exit = Read-Tail $last 4096 | ConvertFrom-Json } catch { $exit = $null }
            if ($exit.exit_code -is [int] -or $exit.exit_code -is [long]) {
                Add-Text 'data/user/last-exit.json' ('{"exit_code":' + $exit.exit_code + '}')
            }
        }
        if ($Deep) {
            $save = '(?:userdata|backup)00(?:0\d|10)'
            Add-PrivateTree 'data/user/savedata/1/CUSA00207/SPRJ0005' "^$save$"
            Add-PrivateTree 'mod/own-world' '^own-save-[0-9]\.bin(?:\.awaiting-save|\.restored)?$'
            Add-PrivateTree 'data/user/world-recovery' (
                '^(?:latest-(?:observation|result)\.json|review-required\.json|runs/[a-f0-9]{32}\.json|' +
                'baselines/world-save-[0-9]\.(?:bin|bbow)|requests/world-save-[0-9]\.bin|' +
                'ready/[0-9]\.ready|acks/[0-9]\.ack|' +
                'save-proof/[a-f0-9]{32}/(?:context\.json|native\.jsonl|expected-bank\.bin|input-save\.bin)|' +
                'home-save-proof/[a-f0-9]{32}-[0-9]-\d+/(?:native\.jsonl|restored-bank\.bin)|' +
                'jobs/[a-f0-9]{32}-\d+-\d+/(?:job\.json|journal\.bin|baseline\.(?:bin|bbow)|' +
                'native-(?:save-proof\.jsonl|restored-bank\.bin)|(?:attempts/[a-f0-9]{32}/)?(?:before|expected)/' +
                $save + '|attempts/[a-f0-9]{32}/install\.json))$')
            Add-Text 'evidence-manifest.json' (([ordered]@{ schema = 1; files = $hashes }) | ConvertTo-Json -Depth 4)
        }
    } finally { $zip.Dispose() }
    if ($Deep) {
        $check = [IO.Compression.ZipFile]::OpenRead($temporary)
        try {
            foreach ($name in $hashes.Keys) {
                $stream = $check.GetEntry($name).Open()
                try {
                    if ((Get-StreamSha256 $stream) -ne $hashes[$name]) {
                        throw '诊断 ZIP 中的私人文件校验失败。'
                    }
                } finally { $stream.Dispose() }
            }
        } finally { $check.Dispose() }
    }
    [IO.File]::Move($temporary, $archive)
} finally {
    if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
}
Write-Host '已创建本地 ZIP。收集器不会上传；请先检查内容再决定是否分享。'
Write-Output $archive
