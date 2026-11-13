param([Parameter(Mandatory=$true)][string]$Plan, [switch]$Headless, [switch]$NoRestart)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$utf8 = New-Object Text.UTF8Encoding $false
$session = Split-Path -Parent ([IO.Path]::GetFullPath($Plan))
try {
    $request = [IO.File]::ReadAllText([IO.Path]::GetFullPath($Plan)) | ConvertFrom-Json
    $root = [IO.Path]::GetFullPath([string]$request.root).TrimEnd('\')
} catch {
    $reason=$_.Exception.Message
    try { [IO.File]::WriteAllText((Join-Path $session 'update-result.json'),(@{success=$false;error=$reason;phase='read-plan'} | ConvertTo-Json),$utf8) } catch { Write-Warning 'Could not save invalid-plan diagnostic' }
    if ($Headless) { Write-Error -Message $reason -ErrorAction Continue }
    else { Add-Type -AssemblyName System.Windows.Forms; [void][Windows.Forms.MessageBox]::Show('无法读取更新计划，请保留诊断：'+$session,'The Hunt 更新失败') }
    exit 1
}
$client = $request.repository -eq 'TheHuntLauncher'
$entryName = if ($client) { 'TheHuntLauncher.exe' } else { 'Server.exe' }
$extract = Join-Path $session ('payload-' + [guid]::NewGuid().ToString('N'))
$lock = $null; $appMutex = $null; $mutexHeld = $false; $backup = $null; $applied = New-Object Collections.Generic.List[string]
$journalPath = Join-Path $root '.update-transaction.json'
$transaction = $null; $transactionStarted = $false; $restart = $false; $exitCode = 0
$progress = $null; $form = $null; $status = $null
if (-not $Headless) {
    Add-Type -AssemblyName System.Windows.Forms,System.Drawing
    [Windows.Forms.Application]::EnableVisualStyles()
    $form = New-Object Windows.Forms.Form
    $form.Text = 'The Hunt · 正在更新'; $form.ClientSize = New-Object Drawing.Size(520,175)
    $form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedDialog'; $form.ControlBox = $false
    $form.BackColor = [Drawing.Color]::FromArgb(23,28,32); $form.ForeColor = [Drawing.Color]::Gainsboro
    $status = New-Object Windows.Forms.Label
    $status.Location = New-Object Drawing.Point(24,25); $status.Size = New-Object Drawing.Size(472,65)
    $status.Font = New-Object Drawing.Font('Microsoft YaHei UI',10)
    $progress = New-Object Windows.Forms.ProgressBar
    $progress.Location = New-Object Drawing.Point(24,112); $progress.Size = New-Object Drawing.Size(472,18)
    $form.Controls.AddRange(@($status,$progress)); $form.Show()
}
function Report([string]$message,[int]$value) {
    if ($form) { $status.Text=$message; $progress.Value=[Math]::Min(100,$value); [Windows.Forms.Application]::DoEvents() }
    Write-Output $message
}
function Hash([string]$path) {
    $algorithm = [Security.Cryptography.SHA256]::Create(); $stream = [IO.File]::OpenRead($path)
    try { return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
function Write-Durable([string]$path, $value) {
    $temporary = $path + '.tmp-' + [guid]::NewGuid().ToString('N')
    try {
        $bytes = $utf8.GetBytes(($value | ConvertTo-Json -Depth 8))
        $stream = New-Object IO.FileStream($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
        if ([IO.File]::Exists($path)) { [IO.File]::Replace($temporary,$path,[NullString]::Value) }
        else { [IO.File]::Move($temporary,$path) }
    } finally { if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) } }
}
function Safe-Relative([string]$name) {
    if (-not $name -or $name.Contains('\') -or $name.Contains(':') -or $name.StartsWith('/') -or $name.EndsWith('/')) { throw 'Unsafe package path' }
    foreach ($part in $name.Split('/')) {
        if ($part -in @('','.','..') -or $part -match '[. ]$|[\x00-\x1f<>"|?*]|^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)') { throw 'Unsafe package filename' }
    }
    # Mirror scripts/package.py's runtime policy. No source-tree exceptions apply
    # to installed program updates; only the two exact public templates are exempt.
    $privateParts = @('.private','data','user','logs','captures','save-backups','save-conversion-backups',
        'conversion-jobs','update-backups','world-recovery','save-proof','home-save-proof','diagnostics',
        'guest-items','own-world','shared-progress','__pycache__','.git','.build','shadnet-state','game-state','backups')
    $privateFiles = @('shadnet.cfg','.server.lock','profile.json','account.clixml','settings-snapshot.json',
        'launcher-state.json','last-exit.json','save-transfer-result.json','.save-import.lock',
        'bbport.ini','input.ini','mods.json','patches.json','.env','features.json','server-admin.json',
        'conversion-current.json','.save-conversion.lock','.save-conversion-pending.json',
        'update-settings.json','desktop-settings.json','.update.lock','.update-transaction.json',
        'insight-fix.json','online-data.json')
    foreach ($part in $name.Split('/')) {
        if ($part -in $privateParts -or $part -match '^\.savedata-(import|conversion)-') { throw 'Private state found in update package' }
        if ($part -in @('.env.example','shadnet.cfg.example')) { continue }
        foreach ($private in $privateFiles) {
            if ($part -ieq $private -or $part.StartsWith($private+'.',[StringComparison]::OrdinalIgnoreCase)) { throw 'Private configuration found in update package' }
            # Atomic writers may append .<id>.tmp or replace the extension.
            if ($private.TrimStart('.').Contains('.') -and
                $part.StartsWith($private.Substring(0,$private.LastIndexOf('.'))+'.',[StringComparison]::OrdinalIgnoreCase) -and
                $part -match '\.(tmp|bak|old|new)([.-].*)?$') { throw 'Private temporary state found in update package' }
        }
    }
    if ($name -match '(^|/)(normal|bbsc)/db(/|$)') { throw 'Private database found in update package' }
    return $name.Replace('/',[IO.Path]::DirectorySeparatorChar)
}
function Check-Parents([string]$path) {
    $current = $path
    while ($current -and $current.Length -ge $root.Length) {
        if ((Test-Path -LiteralPath $current) -and ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Update target contains a junction or symbolic link' }
        if ($current -eq $root) { break }
        $current = Split-Path -Parent $current
    }
}
try {
    if (-not $client -and $request.repository -ne 'TheHuntServer') { throw 'Unknown update project' }
    if (-not (Test-Path -LiteralPath (Join-Path $root $entryName) -PathType Leaf)) { throw 'Update target is not an installed application' }
    $parentRoot = Split-Path -Parent $root
    if (-not $client -and (Split-Path -Leaf $root) -ieq 'server' -and
        ((Test-Path -LiteralPath (Join-Path $parentRoot 'TheHuntLauncher.exe')) -or
         (Test-Path -LiteralPath (Join-Path $parentRoot '.update-transaction.json')))) {
        throw 'Embedded server updates are disabled; update the parent client instead'
    }
    if (Test-Path -LiteralPath $journalPath) { throw ('Unfinished update transaction; preserve and inspect ' + $journalPath + ' and ' + (Join-Path $root 'update-backups')) }
    if ($client -and (Test-Path -LiteralPath (Join-Path $root 'server\.update-transaction.json'))) {
        throw ('Unfinished embedded server transaction; preserve ' + (Join-Path $root 'server\update-backups'))
    }
    Report '正在校验更新包……' 2
    if ($request.sha256 -notmatch '^[a-fA-F0-9]{64}$' -or (Hash $request.archive) -ne $request.sha256.ToLowerInvariant()) { throw 'Update archive hash mismatch' }
    [void][IO.Directory]::CreateDirectory($extract)
    $zip = [IO.Compression.ZipFile]::OpenRead([string]$request.archive)
    try {
        $names = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
        $top = $null; [long]$bytes = 0
        if ($zip.Entries.Count -gt 10000) { throw 'Too many package entries' }
        foreach ($item in $zip.Entries) {
            if (-not $item.Name) { continue }
            $slash = $item.FullName.IndexOf('/')
            if ($slash -lt 1) { throw 'Package must contain one application directory' }
            $folder = $item.FullName.Substring(0,$slash)
            if ($null -eq $top) { $top=$folder } elseif ($folder -cne $top) { throw 'Multiple application directories in package' }
            $relative = $item.FullName.Substring($slash+1)
            $safe = Safe-Relative $relative
            if (-not $names.Add($relative) -or (($item.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) { throw 'Duplicate file or symbolic link in package' }
            $bytes += $item.Length
            if ($bytes -gt 4GB) { throw 'Expanded package is too large' }
            $target = Join-Path $extract $safe
            [void][IO.Directory]::CreateDirectory((Split-Path -Parent $target))
            [IO.Compression.ZipFileExtensions]::ExtractToFile($item,$target,$false)
        }
    } finally { $zip.Dispose() }
    $info = [IO.File]::ReadAllText((Join-Path $extract 'BUILD-INFO.json')) | ConvertFrom-Json
    $newVersion = if ($client) { $info.version } else { $info.label }
    if ($newVersion -cne $request.version) { throw 'Package version does not match the selected release' }
    $manifestName = if ($client) { 'file-sha256.json' } else { 'BUILD-INFO.json' }
    $manifest = if ($client) { [IO.File]::ReadAllText((Join-Path $extract $manifestName)) | ConvertFrom-Json } else { $info.files }
    $expected = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    foreach ($field in $manifest.PSObject.Properties) {
        $relative = [string]$field.Name; $safe = Safe-Relative $relative
        if (-not $expected.Add($relative) -or [string]$field.Value -notmatch '^[a-fA-F0-9]{64}$' -or (Hash (Join-Path $extract $safe)) -ne ([string]$field.Value).ToLowerInvariant()) { throw 'Package manifest verification failed' }
    }
    [void]$expected.Add($manifestName)
    foreach ($file in $names) { if (-not $expected.Contains($file)) { throw ('Unlisted file in update package: ' + $file) } }
    if (-not $names.Contains($entryName) -or -not $names.Contains('Apply-Update.ps1')) { throw 'Required application files are absent' }
    Report '校验通过，正在等待原窗口退出……' 15
    $deadline = (Get-Date).AddSeconds(60)
    while ([int]$request.parent_pid -gt 0 -and (Get-Process -Id ([int]$request.parent_pid) -ErrorAction SilentlyContinue)) {
        if ((Get-Date) -gt $deadline) { throw 'Original application did not exit; no files replaced' }
        Start-Sleep -Milliseconds 200
        if ($form) { [Windows.Forms.Application]::DoEvents() }
    }
    Check-Parents $root
    $lock = [IO.File]::Open((Join-Path $root '.update.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
    if (Test-Path -LiteralPath $journalPath) { throw ('Unfinished update transaction; preserve and inspect ' + $journalPath + ' and ' + (Join-Path $root 'update-backups')) }
    if ($client -and (Test-Path -LiteralPath (Join-Path $root 'server\.update-transaction.json'))) {
        throw ('Unfinished embedded server transaction; preserve ' + (Join-Path $root 'server\update-backups'))
    }
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        $identityPath = if ($client) { $root } else { $root.ToLowerInvariant()+'\' }
        $identity = [BitConverter]::ToString($algorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($identityPath)))
    } finally { $algorithm.Dispose() }
    $mutexName = if ($client) { 'Local\BBSCLauncher-'+$identity } else { 'Local\TheHuntServer-'+$identity.Replace('-','') }
    $appMutex = New-Object Threading.Mutex($false,$mutexName)
    try { $mutexHeld = $appMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $mutexHeld = $true }
    if (-not $mutexHeld) { throw 'Another application window is still running; no files replaced' }
    $running = Get-Process -ErrorAction SilentlyContinue | Where-Object { try { $_.Path -and $_.Path.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase) } catch { $false } }
    if ($running) { throw 'Game or service processes are still running; no files replaced' }
    $backup = Join-Path $root ('update-backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
    [void][IO.Directory]::CreateDirectory((Join-Path $backup 'program'))
    $operations = @($names | Where-Object {
        $preserve = if ($client) { $_ -match '^server/(server\.json|bbsc/(worlds|scoreboards)\.cfg)$' } else { $_ -eq 'server.json' -or $_ -match '^(normal|bbsc)/(worlds|scoreboards)\.cfg$' }
        -not ($preserve -and (Test-Path -LiteralPath (Join-Path $root $_)))
    } | Sort-Object)
    Report '正在备份将被替换的程序文件，账号与存档保持原位……' 20
    $records = New-Object Collections.Generic.List[object]
    foreach ($relative in $operations) {
        $destination = Join-Path $root (Safe-Relative $relative); Check-Parents $destination
        $oldHash = $null
        if (Test-Path -LiteralPath $destination -PathType Leaf) {
            $old = Join-Path $backup ('program\'+$relative)
            [void][IO.Directory]::CreateDirectory((Split-Path -Parent $old)); [IO.File]::Copy($destination,$old,$false)
            $flush = [IO.File]::Open($old,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::Read)
            try { $flush.Flush($true) } finally { $flush.Dispose() }
            $oldHash = Hash $old
            if ((Hash $destination) -ne $oldHash) { throw 'Program changed while creating recovery backup' }
        } elseif (Test-Path -LiteralPath $destination) { throw 'A target file is occupied by a directory' }
        $records.Add(@{path=$relative;old_sha256=$oldHash;new_sha256=(Hash (Join-Path $extract $relative))})
    }
    Write-Durable (Join-Path $backup 'update.json') $request
    $transaction = @{schema=1;root=$root;backup=$backup;phase='applying';operations=$records.ToArray();archive=[string]$request.archive;extract=$extract}
    Write-Durable (Join-Path $backup 'transaction.json') $transaction
    [IO.File]::WriteAllText((Join-Path $backup 'RECOVERY.txt'),
        "Update recovery: close all client/server/game processes first.`r`n" +
        "transaction.json lists every program target and its original SHA256 (null means it did not exist).`r`n" +
        "Restore each original from program/ after verifying its old_sha256, and remove only listed targets with null old_sha256.`r`n" +
        "Verify ALL listed targets match their original hashes/absence before archiving .update-transaction.json out of the installation root.`r`n" +
        "Keep this backup and result.json. Do not copy private data or remove the marker before verification.`r`n",$utf8)
    # A complete, flushed backup manifest is published before the first replacement.
    # An interrupted transaction deliberately blocks all later entry points.
    Write-Durable $journalPath $transaction
    $transactionStarted = $true
    foreach ($relative in $operations) {
        $destination = Join-Path $root (Safe-Relative $relative)
        [void][IO.Directory]::CreateDirectory((Split-Path -Parent $destination))
        $temporary = $destination + '.update-' + [guid]::NewGuid().ToString('N')
        try {
            [IO.File]::Copy((Join-Path $extract $relative),$temporary,$false)
            if ([IO.File]::Exists($destination)) { [IO.File]::Replace($temporary,$destination,[NullString]::Value) } else { [IO.File]::Move($temporary,$destination) }
            $applied.Add($relative)
        } finally { if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) } }
        Report ('正在替换程序文件：'+$relative) (25+[int](70*$applied.Count/[Math]::Max(1,$operations.Count)))
    }
    foreach ($record in $records) {
        if ((Hash (Join-Path $root $record.path)) -ne $record.new_sha256) { throw 'Installed file verification failed' }
    }
    Write-Durable (Join-Path $backup 'result.json') @{success=$true;applied=$applied.ToArray()}
    $transaction.phase = 'committed'
    Write-Durable $journalPath $transaction
    [IO.File]::Delete($journalPath)
    $transactionStarted = $false
    Report '更新完成，正在重新打开程序。' 100
    $restart = -not $NoRestart
} catch {
    $reason = $_.Exception.Message
    $rollbackError = $null
    if ($transactionStarted) {
        try { $transaction.phase='rolling-back'; Write-Durable $journalPath $transaction } catch { $rollbackError=$_.Exception.Message }
        for ($i=$records.Count-1; $i -ge 0; $i--) {
            try {
                $record=$records[$i]; $relative=$record.path; $destination=Join-Path $root $relative; $old=Join-Path $backup ('program\'+$relative)
                Check-Parents $destination
                if ($record.old_sha256) {
                    if ((Hash $old) -ne $record.old_sha256) { throw 'Recovery backup hash mismatch' }
                    if ([IO.File]::Exists($destination) -and (Hash $destination) -eq $record.old_sha256) { continue }
                    $temporary=$destination+'.update-'+[guid]::NewGuid().ToString('N')
                    try {
                        [IO.File]::Copy($old,$temporary,$false)
                        if ([IO.File]::Exists($destination)) { [IO.File]::Replace($temporary,$destination,[NullString]::Value) }
                        else { [IO.File]::Move($temporary,$destination) }
                    } finally { if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) } }
                    if ((Hash $destination) -ne $record.old_sha256) { throw 'Restored file verification failed' }
                } else { [IO.File]::Delete($destination) }
            } catch { $rollbackError=$_.Exception.Message }
        }
    }
    $result=@{success=$false;error=$reason;rollback_error=$rollbackError;applied=$applied.ToArray();backup=$backup}
    try {
        if ($backup) { Write-Durable (Join-Path $backup 'result.json') $result }
        Write-Durable (Join-Path $session 'update-result.json') $result
        if ($transactionStarted -and -not $rollbackError) {
            $transaction.phase='rolled-back'; Write-Durable $journalPath $transaction
            [IO.File]::Delete($journalPath); $transactionStarted=$false
            $restart = -not $NoRestart
        }
    } catch { $rollbackError=$_.Exception.Message }
    $message = "更新未完成：$reason`n账号和存档目录未参与替换。"
    if ($backup) { $message += "`n程序备份与恢复记录：$backup" }
    if ($rollbackError) { $message += "`n自动恢复或证据保存未完成：$rollbackError。请保留事务记录，勿启动混装程序。" }
    if ($Headless) { Write-Error -Message $message -ErrorAction Continue }
    else { [void][Windows.Forms.MessageBox]::Show($form,$message,'The Hunt 更新失败','OK','Error') }
    $exitCode=1
} finally {
    if ($lock) { $lock.Dispose() }
    if ($appMutex) { if ($mutexHeld) { $appMutex.ReleaseMutex() }; $appMutex.Dispose() }
    if ($form) { $form.Close(); $form.Dispose() }
    # Retain plan/helper/results and all recovery backups, remove large payloads only.
    foreach ($payload in @($extract,[string]$request.archive)) {
        try {
            $full=[IO.Path]::GetFullPath($payload)
            $sessionBase=[IO.Path]::GetFullPath($session).TrimEnd('\')+'\'
            if ($full -eq [IO.Path]::GetFullPath($extract) -and
                $full.StartsWith($sessionBase,[StringComparison]::OrdinalIgnoreCase) -and
                (Split-Path -Leaf $full) -match '^payload-[a-f0-9]{32}$' -and (Test-Path -LiteralPath $full)) {
                Remove-Item -LiteralPath $full -Recurse -Force
            } elseif ($full -eq (Join-Path $sessionBase 'update.zip') -and [IO.File]::Exists($full)) {
                [IO.File]::Delete($full)
            }
        } catch {
            try { Write-Durable (Join-Path $session 'cleanup-error.json') @{path=$payload;error=$_.Exception.Message} } catch { Write-Warning 'Could not save payload cleanup diagnostic' }
        }
    }
}
# Restart errors never roll back a committed transaction.
if ($restart) {
    try { Start-Process -FilePath (Join-Path $root $entryName) -WorkingDirectory $root -WindowStyle Normal }
    catch {
        Write-Durable (Join-Path $session 'restart-error.json') @{error=$_.Exception.Message;root=$root}
        if (-not $Headless) { [void][Windows.Forms.MessageBox]::Show('程序文件已完成处理，请手动重新打开：'+(Join-Path $root $entryName),'The Hunt 更新') }
        $exitCode=1
    }
}
exit $exitCode
