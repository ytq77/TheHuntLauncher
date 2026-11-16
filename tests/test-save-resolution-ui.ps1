param(
    [Parameter(Mandatory=$true)][string]$AssemblyPath,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [string]$RuntimeRoot,
    [switch]$ViewOnly
)
$ErrorActionPreference='Stop'
$repo=Split-Path $PSScriptRoot
$OutputDirectory=[IO.Path]::GetFullPath($OutputDirectory)
[void][IO.Directory]::CreateDirectory($OutputDirectory)
$fixture=Join-Path $OutputDirectory ('fixture-'+[Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($fixture)
$utf8=New-Object Text.UTF8Encoding $false
$flags=[Reflection.BindingFlags]'Instance,NonPublic'
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase,System.Xaml,System.Web.Extensions
[void][Reflection.Assembly]::LoadFrom((Join-Path (Split-Path $AssemblyPath) 'HuntUpdates.dll'))
[void][Reflection.Assembly]::LoadFrom([IO.Path]::GetFullPath($AssemblyPath))
Add-Type @'
using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text;
public static class ResolutionTestDialog {
    delegate bool Visitor(IntPtr window,IntPtr param);
    [DllImport("user32.dll")] static extern bool EnumWindows(Visitor callback,IntPtr param);
    [DllImport("user32.dll",CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr window,StringBuilder text,int size);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window,out uint process);
    [DllImport("user32.dll")] static extern IntPtr GetDlgItem(IntPtr window,int id);
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr window,int message,IntPtr wparam,IntPtr lparam);
    public static bool Choose(string title,bool yes) {
        bool chosen=false;
        EnumWindows(delegate(IntPtr window,IntPtr ignored) {
            uint process; GetWindowThreadProcessId(window,out process);
            var text=new StringBuilder(256); GetWindowText(window,text,text.Capacity);
            if(process==(uint)Process.GetCurrentProcess().Id && text.ToString()==title) {
                var button=GetDlgItem(window,yes ? 6:7);
                if(button!=IntPtr.Zero) { SendMessage(button,0x00f5,IntPtr.Zero,IntPtr.Zero); chosen=true; return false; }
            }
            return true;
        },IntPtr.Zero);
        return chosen;
    }
}
'@
function Assert($value,[string]$message) { if(-not $value) { throw $message } }
function Invoke-Private($object,[string]$name,[object[]]$arguments=@()) {
    $raw=New-Object object[] $arguments.Length
    for($i=0;$i -lt $arguments.Length;$i++) { $raw[$i]=$arguments[$i].PSObject.BaseObject }
    return $object.GetType().GetMethod($name,$flags).Invoke($object,$raw)
}
function Field($object,[string]$name) { return $object.GetType().GetField($name,$flags).GetValue($object) }
function Await-Task($task) {
    $deadline=[DateTime]::UtcNow.AddSeconds(60)
    while(-not $task.IsCompleted) {
        if([DateTime]::UtcNow -gt $deadline) { throw 'Resolution UI task timeout' }
        $frame=New-Object Windows.Threading.DispatcherFrame
        $tick=New-Object Windows.Threading.DispatcherTimer
        $tick.Interval=[TimeSpan]::FromMilliseconds(15); $tick.Tag=$frame
        $tick.Add_Tick({ $this.Stop(); $this.Tag.Continue=$false })
        $tick.Start(); [Windows.Threading.Dispatcher]::PushFrame($frame)
    }
    return $task.GetAwaiter().GetResult()
}
function Hash-File([string]$path) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash([IO.File]::ReadAllBytes($path))).Replace('-','').ToLowerInvariant() }
    finally { $sha.Dispose() }
}
function Save-ChildReceipt($row,[string]$label) {
    [IO.File]::WriteAllText((Join-Path $OutputDirectory ($label+'-process.json')),($row | ConvertTo-Json -Depth 6),$utf8)
}
function Invoke-FixturePython([string]$script,[string[]]$arguments,[string]$label) {
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=Join-Path $fixture 'runtime\bin\python.exe'
    $info.Arguments='-B -c '+[DesktopWindow]::Quote($script)
    foreach($argument in $arguments) { $info.Arguments+=' '+[DesktopWindow]::Quote($argument) }
    $info.WorkingDirectory=$fixture; $info.UseShellExecute=$false; $info.CreateNoWindow=$true
    $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
    $process=[Diagnostics.Process]::Start($info)
    $row=@{pid=$process.Id;executable=$info.FileName;arguments=$info.Arguments;fixture=$fixture;started_utc=[DateTime]::UtcNow.ToString('o');completed=$false}
    try {
        $held=$process.Handle
        $stdout=$process.StandardOutput.ReadToEndAsync(); $stderr=$process.StandardError.ReadToEndAsync()
        Save-ChildReceipt $row $label
        if(-not $process.WaitForExit(30000)) { throw ('Fixture child timeout: '+$label) }
        Assert ([Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($stdout,$stderr),10000)) 'Fixture output drain timeout'
        $code=$process.ExitCode
        [IO.File]::WriteAllText((Join-Path $OutputDirectory ($label+'.stdout')),$stdout.GetAwaiter().GetResult(),$utf8)
        [IO.File]::WriteAllText((Join-Path $OutputDirectory ($label+'.stderr')),$stderr.GetAwaiter().GetResult(),$utf8)
        $row.completed=$true; $row.exit_code=$code
        $row.stdout_sha256=Hash-File (Join-Path $OutputDirectory ($label+'.stdout'))
        $row.stderr_sha256=Hash-File (Join-Path $OutputDirectory ($label+'.stderr'))
        Assert ($code -is [int] -and $code -eq 0) ('Fixture child failed: '+$label)
    } finally {
        if(-not $process.HasExited) { $process.Kill(); Assert ($process.WaitForExit(10000)) 'Owned fixture child did not exit after termination' }
        $row.exit_code=$process.ExitCode; $row.ended_utc=[DateTime]::UtcNow.ToString('o')
        Save-ChildReceipt $row $label; $process.Dispose()
    }
}
function Map([string]$text) { return $script:parser.DeserializeObject($text) }
function Plan($desktop) { return Field $desktop 'resolutionPlan' }
function Choose-Apply($desktop,[string]$optionId,[bool]$yes) {
    $plan=Plan $desktop
    $option=@($plan['options'] | Where-Object { $_['id'] -eq $optionId })[0]
    Assert ($null -ne $option) ('Missing production option '+$optionId)
    $script:dialogYes=$yes; $script:dialogChosen=$false
    $dialog=New-Object Windows.Threading.DispatcherTimer
    $dialog.Interval=[TimeSpan]::FromMilliseconds(40)
    $dialog.Add_Tick({ if([ResolutionTestDialog]::Choose('确认存档选择',$script:dialogYes)) { $script:dialogChosen=$true; $this.Stop() } })
    $dialog.Start()
    try {
        $task=Invoke-Private $desktop 'ApplySaveResolution' @($plan,$option,(Field $desktop 'resolutionGeneration'))
        Await-Task $task
        Assert $script:dialogChosen 'The actual native confirmation was not exercised'
    } finally { $dialog.Stop() }
}
function Choose-Abandon($desktop,[bool]$yes) {
    $script:dialogYes=$yes; $script:dialogChosen=$false
    $dialog=New-Object Windows.Threading.DispatcherTimer
    $dialog.Interval=[TimeSpan]::FromMilliseconds(40)
    $dialog.Add_Tick({ if([ResolutionTestDialog]::Choose('完整另存当前现场并放弃上次选择',$script:dialogYes)) { $script:dialogChosen=$true; $this.Stop() } })
    $dialog.Start()
    try { Await-Task (Invoke-Private $desktop 'AbandonSaveResolution'); Assert $script:dialogChosen 'Actual abandonment confirmation not exercised' }
    finally { $dialog.Stop() }
}
function Choose-Resume($desktop) {
    $script:dialogChosen=$false
    $dialog=New-Object Windows.Threading.DispatcherTimer
    $dialog.Interval=[TimeSpan]::FromMilliseconds(40)
    $dialog.Add_Tick({ if([ResolutionTestDialog]::Choose('继续上次的选择',$true)) { $script:dialogChosen=$true; $this.Stop() } })
    $dialog.Start()
    try { Await-Task (Invoke-Private $desktop 'ResumeSaveResolution'); Assert $script:dialogChosen 'Actual native resume confirmation was not exercised' }
    finally { $dialog.Stop() }
}
$parser=New-Object Web.Script.Serialization.JavaScriptSerializer
[Threading.SynchronizationContext]::SetSynchronizationContext((New-Object Windows.Threading.DispatcherSynchronizationContext))
$passed=New-Object 'Collections.Generic.List[string]'
try {
    foreach($name in @('Launcher.xaml','feature-catalog.json','trophy-catalog.json')) { Copy-Item -LiteralPath (Join-Path $repo ('launcher\'+$name)) -Destination $fixture }
    # Assets are immutable source assets, never a real installation.
    Copy-Item -LiteralPath (Join-Path $repo 'assets') -Destination (Join-Path $OutputDirectory 'assets') -Recurse
    $desktop=New-Object DesktopWindow($fixture); $window=$desktop.Window; $desktop.Page('Save')
    $view=Map '{"ok":true,"needed":true,"show_choices":true,"summary":"synthetic view state","digest":"view-only","options":[{"id":"preserve","label":"先保全原件，暂不改变","description":"会先保全","recommended":false},{"id":"accept-all","label":"保留所有角色现在的存档（推荐）","description":"所有待补联机进度作废；先保全，不合并","recommended":true},{"id":"future-action","label":"未知动作","description":"不支持","recommended":false}],"characters":[{"slot":0,"name":"Synthetic","primary":"unreadable","backup":"readable"}],"warnings":[],"pending":null,"selection":null}'
    $view['readability_note']='部分代次当前版本无法读取，不代表永久损坏。'
    $view['characters'][0]['primary_detail']='当前版本无法读取：合成格式不支持'
    $generation=Field $desktop 'resolutionGeneration'
    Invoke-Private $desktop 'ShowSaveResolution' @($view,$generation)
    $buttons=@($window.FindName('SaveResolutionOptions').Children | Where-Object { $_ -is [Windows.Controls.Button] })
    Assert ($buttons[0].Content -like '*推荐*') 'Recommended action was not first'
    Assert (-not $buttons[0].IsDefault -and -not $buttons[2].IsEnabled) 'Default destructive action or unknown action enabled'
    Assert ($window.FindName('SaveResolutionCharacters').Text -like '*主档读不出*备档可读*') 'Primary and backup readability collapsed'
    Assert ($window.FindName('SaveResolutionCharacters').Text -like '*主档说明*合成格式不支持*不代表永久损坏*') 'Generation detail or backend readability note was hidden'
    $desktop.Validate((Join-Path $OutputDirectory 'resolution-page.png'),'Save')
    $window.FindName('SaveResolutionPanel').BringIntoView(); $window.UpdateLayout()
    $desktop.Validate((Join-Path $OutputDirectory 'resolution-view.png'),'Save')
    $window.FindName('CancelSaveResolution').RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Primitives.ButtonBase]::ClickEvent)))
    Assert ($null -eq (Plan $desktop)) 'Cancel retained the plan'
    Invoke-Private $desktop 'ShowSaveResolution' @($view,$generation)
    Assert ($null -eq (Plan $desktop)) 'Late response resurrected a cancelled plan'
    $generation=Field $desktop 'resolutionGeneration'; Invoke-Private $desktop 'ShowSaveResolution' @($view,$generation)
    $window.FindName('InstallPath').Text='synthetic-source-changed'
    Assert ($null -eq (Plan $desktop)) 'Source change retained the plan'
    $generation=Field $desktop 'resolutionGeneration'; Invoke-Private $desktop 'ShowSaveResolution' @($view,$generation)
    $desktop.Page('Home'); $desktop.Page('Save'); Invoke-Private $desktop 'ShowSaveResolution' @($view,$generation)
    Assert ($null -eq (Plan $desktop)) 'Leaving and returning accepted the old response'
    $generation=Field $desktop 'resolutionGeneration'; Invoke-Private $desktop 'ShowSaveResolution' @($view,$generation)
    $desktop.GetType().GetField('operation',$flags).SetValue($desktop,$true); Invoke-Private $desktop 'SetBusy'
    Assert (-not $window.FindName('SaveResolutionOptions').IsEnabled -and -not $window.FindName('RefreshSaveResolution').IsEnabled) 'Single-flight controls remained enabled'
    $window.Close()
    Assert ($null -eq (Plan $desktop)) 'Close attempt retained the plan'
    $desktop.GetType().GetField('operation',$flags).SetValue($desktop,$false); Invoke-Private $desktop 'SetBusy'
    $unknown=Map '{"backups":[{"kind":"future-backup","id":"x","time":"2026-11-16T01:00:00Z","file_count":1,"total_bytes":8,"fingerprint":"x"}],"warnings":[],"pending":null}'
    Invoke-Private $desktop 'ShowSaveBackups' @($unknown); Invoke-Private $desktop 'SetBusy'
    Assert (-not $window.FindName('RestoreSaveBackup').IsEnabled) 'Unknown backup kind enabled restore'
    $specialViews=Map '{"backups":[{"kind":"resolution-before","id":"partial","time":"2026-11-16T01:00:00Z","label":"处理前的存档（含读不出的角色）","file_count":3,"total_bytes":4096,"fingerprint":"partial","characters":[{"slot":0,"name":"Partial","primary":"unreadable","backup":"readable","primary_detail":"当前版本无法读取：合成格式不支持"}],"readability_note":"部分代次无法读取，不代表永久损坏。"},{"kind":"resolution-before","id":"profile","time":"2026-11-16T02:00:00Z","label":"处理前的存档（角色列表或身份待核对）","file_count":3,"total_bytes":4096,"fingerprint":"profile","characters":[{"slot":1,"name":"Profile","primary":"readable","backup":"readable"}],"readability_note":"角色列表无法核对；可读角色不等于整套可启动。"},{"kind":"resolution-before","id":"identity","time":"2026-11-16T03:00:00Z","label":"换回前的存档（角色列表或身份待核对）","file_count":3,"total_bytes":4096,"fingerprint":"identity","characters":[{"slot":2,"name":"Identity","primary":"readable","backup":"readable","identity_conflict":true}],"readability_note":"主备两代身份不一致，不能称整套一致。"}],"warnings":[],"pending":null}'
    Invoke-Private $desktop 'ShowSaveBackups' @($specialViews); Invoke-Private $desktop 'SetBusy'
    $specialItems=@($window.FindName('SaveBackupList').Items)
    Assert ($specialItems[0].Content -like '*2026-*角色槽位 1*主档读不出*备档可读*合成格式不支持*不代表永久损坏*') 'Partial special row omitted its timestamp, per-generation detail or note'
    Assert ($specialItems[1].Content -like '*2026-*角色槽位 2*主档可读*备档可读*角色列表无法核对*' -and $specialItems[1].Content -notlike '*主档读不出*') 'Profile issue was hidden or misrepresented as an unreadable role'
    Assert ($specialItems[2].Content -like '*2026-*角色槽位 3*主档可读*备档可读*两代角色身份不一致*不能称整套一致*') 'Identity issue was hidden behind individually readable generations'
    $window.FindName('SaveBackupList').SelectedItem=$specialItems[1]
    Assert ($window.FindName('SelectedSaveBackup').Text -like '*角色列表无法核对*') 'Selected special details omitted the backend note'
    $desktop.Validate((Join-Path $OutputDirectory 'special-row-page.png'),'Save')
    $window.FindName('SelectedSaveBackup').BringIntoView(); $window.UpdateLayout()
    $desktop.Validate((Join-Path $OutputDirectory 'special-row-profile.png'),'Save')
    $noReadable=Map '{"summary":"全部角色代次当前版本无法读取，不代表永久损坏。","characters":[{"slot":0,"primary":"unreadable","backup":"unreadable"}],"readability_note":"原件只封存，不列为可换回项。"}'
    $restoreDescription=Map '{"id":"restore-all","label":"恢复所选整套备份","description":"当前版本会先完整保全，但只封存，不列为可换回项；不能保证日后恢复，不合并进度。"}'
    $confirmation=Invoke-Private $desktop 'ResolutionConfirmation' @($noReadable,$restoreDescription)
    Assert ($confirmation.Contains($restoreDescription['description']) -and $confirmation -like '*主档读不出*备档读不出*只封存*' -and $confirmation -notlike '*可在列表换回*') 'UI changed the bound backend description or added an invalid undo promise'
    $normal=Map '{"ok":true,"needed":false,"show_choices":false,"summary":"no anomaly","digest":"clean","options":[],"characters":[],"warnings":[],"pending":null,"selection":null}'
    Invoke-Private $desktop 'ShowSaveResolution' @($normal,(Field $desktop 'resolutionGeneration'))
    Assert ($window.FindName('SaveResolutionPanel').Visibility -eq [Windows.Visibility]::Collapsed) 'Normal state displayed anomaly choices'
    $pending=Map '{"ok":true,"needed":true,"show_choices":true,"summary":"interrupted","digest":null,"options":[{"id":"accept-all","label":"must not show","description":"must not show","recommended":true}],"characters":[],"warnings":[],"pending":{"can_resume":true,"can_abandon":true,"abandon_digest":"synthetic-abandon","abandon_message":"preserve current before abandon","fingerprint":"synthetic-pending","message":"continue original choice"},"selection":null}'
    Invoke-Private $desktop 'ShowSaveResolution' @($pending,(Field $desktop 'resolutionGeneration'))
    Assert ($window.FindName('SaveResolutionOptions').Children.Count -eq 0 -and $window.FindName('ResumeSaveResolution').IsEnabled) 'Pending transaction allowed a new choice or hid resume'
    Assert ($window.FindName('AbandonSaveResolution').IsEnabled) 'Pending view omitted the distinct abandonment exit'
    $passed.Add('Native WPF view-only: recommendation order, per-row timestamps, explicit primary/backup readability, generation detail, profile/identity notes, bound confirmation text, no default, unknown types, Cancel, source, navigation, Close and stale response invalidation.')
    if(-not $ViewOnly) {
        Assert ($RuntimeRoot -and (Test-Path -LiteralPath (Join-Path $RuntimeRoot 'bin\python.exe'))) 'A known packaged Python runtime is required for real chain checks'
        Copy-Item -LiteralPath $RuntimeRoot -Destination (Join-Path $fixture 'runtime') -Recurse
        foreach($file in Get-ChildItem -LiteralPath (Join-Path $repo 'launcher') -Filter '*.py') { Copy-Item -LiteralPath $file.FullName -Destination $fixture }
        $setup=@'
import json, sys
from pathlib import Path
root, repo = map(Path, sys.argv[1:])
sys.path[:0] = [str(root), str(repo/'tests')]
from synthetic_world_fixture import fixture_pair
import save_transfer as t
raw, _ = fixture_pair()
role = root/'data/user/savedata/1/CUSA00207/SPRJ0005'
role.mkdir(parents=True)
for stem in ('userdata', 'backup'): (role/f'{stem}0000').write_bytes(raw)
(role/'userdata0010').write_bytes(b'profile'+bytes(100))
t.validate_savedata(role.parents[2])
marker = root/'data/user/world-recovery/review-required.json'
marker.parent.mkdir(parents=True)
marker.write_text(json.dumps({'reason':'synthetic opaque anomaly'}), encoding='utf-8')
print('fixture ready, production validation passed; no game or process-check stub')
'@
        $env:PYTHONHOME=Join-Path $fixture 'runtime'; $env:PYTHONUTF8='1'
        Invoke-FixturePython $setup @($fixture,$repo) 'fixture-setup'
        $desktop.Page('Save')
        $primary=Join-Path $fixture 'data\user\savedata\1\CUSA00207\SPRJ0005\userdata0000'
        $beforePrimary=Hash-File $primary
        $shared=[IO.File]::Open($primary,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::None)
        try {
            $refused=$false
            try { Await-Task (Invoke-Private $desktop 'RefreshSaveResolution') } catch { $refused=$true }
            Assert $refused 'Real Windows sharing denial did not fail the backend request'
            Assert ($null -eq (Plan $desktop) -and $window.FindName('SaveResolutionOptions').Children.Count -eq 0) 'Unreadable fixture exposed confirmable save choices'
            $message=$window.FindName('SaveResolutionStatus').Text
            Assert ($message -like '*正常退出*' -and $message -like '*权限*' -and $message -like '*不据此判定存档损坏*') 'Actual backend permission/sharing JSON lost local steps in the UI'
            [IO.File]::WriteAllText((Join-Path $OutputDirectory 'sharing-error-text.txt'),$message,$utf8)
            $desktop.Validate((Join-Path $OutputDirectory 'sharing-error-page.png'),'Save')
        } finally { $shared.Dispose() }
        Assert ((Hash-File $primary) -eq $beforePrimary) 'Read-only sharing test changed a primary'
        $unknownLock=Join-Path $fixture '.save-import.lock'
        [IO.File]::WriteAllText($unknownLock,'{unsupported synthetic control',$utf8)
        Await-Task (Invoke-Private $desktop 'RefreshSaveResolution')
        Assert (-not (Field $desktop 'pendingResolution')['can_resume'] -and -not (Field $desktop 'pendingResolution')['can_abandon']) 'Unknown lock offered a mutating exit'
        Assert ($window.FindName('SaveResolutionPending').Text -like '*当前流程不能安全继续*' -and $window.FindName('SaveResolutionPending').Text -notlike '*权限*') 'Unknown record was mislabeled as transient IO'
        [IO.File]::Move($unknownLock,(Join-Path $fixture 'unsupported-control.kept'))
        $passed.Add('Real Windows FileShare.None on one generated primary -> actual Request/save_resolution.main JSON -> error panel with specific local steps/no choices; release restores access. Actual unknown synthetic control remains unsupported and is not mislabeled as retryable IO. No ACL/global changes.')
        $late=Invoke-Private $desktop 'RefreshSaveResolution'
        Assert $desktop.Busy 'Production plan request was not single-flight'
        $rejected=$false
        try { Await-Task (Invoke-Private $desktop 'RefreshSaveResolution') } catch { $rejected=$true }
        Assert $rejected 'Repeated production refresh did not reject the second operation'
        $desktop.Page('Home'); $desktop.Page('Save'); Await-Task $late
        Assert ($null -eq (Plan $desktop)) 'Actual late Python response repopulated a plan after navigation'
        $late=Invoke-Private $desktop 'RefreshSaveResolution'
        $window.FindName('CancelSaveResolution').RaiseEvent((New-Object Windows.RoutedEventArgs([Windows.Controls.Primitives.ButtonBase]::ClickEvent)))
        Await-Task $late
        Assert ($null -eq (Plan $desktop)) 'Actual late Python response repopulated a cancelled plan'
        Await-Task (Invoke-Private $desktop 'RefreshSaveBackups')
        Assert ((Plan $desktop)['needed']) 'Production JSON chain did not expose the opaque anomaly'
        Choose-Apply $desktop 'accept-all' $false
        Assert (Test-Path (Join-Path $fixture 'data\user\world-recovery\review-required.json')) 'Declining confirmation changed the anomaly'
        Assert (-not (Test-Path (Join-Path $fixture 'save-backups'))) 'Declining confirmation created a transaction'
        Await-Task (Invoke-Private $desktop 'RefreshSaveResolution')
        Choose-Apply $desktop 'accept-all' $true
        Assert (-not (Test-Path (Join-Path $fixture '.save-import.lock'))) 'Completed actual backend transaction retained its lock'
        Assert (-not (Test-Path (Join-Path $fixture 'data\user\world-recovery\review-required.json'))) 'Actual accept-all did not retire the marker'
        $special=@($window.FindName('SaveBackupList').Items | Where-Object { $_.Tag['kind'] -eq 'resolution-before' })
        Assert ($special.Count -ge 1) 'Completed production original was not listed separately'
        foreach($item in $special) {
            Assert ($item.Content -match '\d{4}-\d{2}-\d{2}' -and $item.Content -like '*角色槽位 1*主档可读*备档可读*') 'Production special row omitted timestamp or per-generation readability'
            if($item.Tag['readability_note']) { Assert ($item.Content.Contains($item.Tag['readability_note'])) 'Production special row hid its backend readability note' }
        }
        $window.FindName('SaveBackupList').SelectedItem=$special[0]
        Assert ($window.FindName('RestoreSaveBackup').Content -eq '换回处理前状态') 'Special row used ordinary restore wording'
        Await-Task (Invoke-Private $desktop 'RestoreSaveBackup')
        Assert ((Plan $desktop)['selection']['kind'] -eq 'resolution-before') 'Actual special dispatch lost selection kind'
        Assert (-not (Plan $desktop)['needed'] -and (Plan $desktop)['show_choices']) 'Healthy explicit history was mislabeled or hidden'
        Assert ($window.FindName('SaveResolutionPanel').Visibility -eq [Windows.Visibility]::Visible -and $window.FindName('SaveResolutionOptions').Children.Count -gt 0) 'Healthy history has no real visible options'
        Choose-Apply $desktop 'restore-case' $true
        Assert (Test-Path (Join-Path $fixture 'data\user\save-resolution-review.json')) 'Actual restore-case did not persist the review gate'
        Assert ((Plan $desktop)['needed']) 'Actual restore-case did not return to the anomaly page'
        $desktop.Validate((Join-Path $OutputDirectory 'resolution-real-page.png'),'Save')
        $window.FindName('SaveResolutionPanel').BringIntoView(); $window.UpdateLayout()
        $desktop.Validate((Join-Path $OutputDirectory 'resolution-real-chain.png'),'Save')
        $passed.Add('Real DesktopWindow.Request -> packaged Python -> production save_resolution CLI: filesystem-validated synthetic saves, asynchronous response invalidation, duplicate refresh refusal, native No/Yes, accept-all, special list/selection, restore-case and persistent gate. No prepare/review/process-check stubs.')
        # The new restore-case's original is the clean branch from before the switch.
        $clean=@($window.FindName('SaveBackupList').Items | Where-Object { $_.Tag['kind'] -eq 'resolution-before' -and $_.Tag['label'] -like '换回前的存档*' })[0]
        Assert ($null -ne $clean) 'The branch from before the switch has no distinct selectable name'
        $window.FindName('SaveBackupList').SelectedItem=$clean
        Await-Task (Invoke-Private $desktop 'RestoreSaveBackup'); Choose-Apply $desktop 'restore-case' $true
        Assert (@((Plan $desktop)['options'] | Where-Object { $_['id'] -eq 'use-current' -and $_['recommended'] }).Count -eq 1) 'Clean restored branch has no recommended normal gate exit'
        $ordinary=Join-Path $fixture 'synthetic-ordinary'; $game=Join-Path $fixture 'synthetic-game'
        [void][IO.Directory]::CreateDirectory($ordinary); [void][IO.Directory]::CreateDirectory($game)
        foreach($name in @('Bloodborne.exe','bbport.ini')) { [IO.File]::WriteAllText((Join-Path $ordinary $name),'synthetic file only',$utf8) }
        [IO.File]::WriteAllText((Join-Path $game 'eboot.bin'),'synthetic file only',$utf8)
        $window.FindName('InstallPath').Text=Join-Path $ordinary 'Bloodborne.exe'
        $window.FindName('GraphicsPath').Text=Join-Path $ordinary 'bbport.ini'
        $window.FindName('GamePath').Text=Join-Path $game 'eboot.bin'
        $window.FindName('Account').Text='synthetic-hunter'; $window.FindName('Password').Password='synthetic-password'
        $window.FindName('PublicOnline').IsChecked=$false
        $window.FindName('Shadnet').Text='127.0.0.1:31413'; $window.FindName('Relay').Text='127.0.0.1:20444'
        $window.FindName('Webapi').Text='http://127.0.0.1:31415'; $window.FindName('Community').Text='http://127.0.0.1:20443'
        Await-Task (Invoke-Private $desktop 'RefreshSaveResolution')
        Choose-Apply $desktop 'use-current' $true
        Assert (-not (Test-Path (Join-Path $fixture 'data\user\save-resolution-review.json'))) 'Explicit normal exit retained its gate'
        $state=Get-Content -LiteralPath (Join-Path $fixture 'launcher-state.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        Assert ($state.phase -eq 'error' -and $state.message -like '*署名文件*') 'Normal launch did not reach the real client startup notice barrier'
        Assert (-not $desktop.Busy) 'Normal launch barrier retained Busy'
        $passed.Add('Actual restore of the clean before-switch branch -> persistent gate -> recommended use-current -> native Yes -> real DesktopWindow.Launch and client.main. Intentionally stops at missing synthetic mod notices before profile/network/prepare/game. This is not a game-launch or prepare-chain pass.')
        # Stop an independent real backend only after its durable lock is published.
        $marker=Join-Path $fixture 'data\user\world-recovery\review-required.json'
        [void][IO.Directory]::CreateDirectory((Split-Path $marker)); [IO.File]::WriteAllText($marker,'{"reason":"synthetic resume anomaly"}',$utf8)
        $handshake=Join-Path $OutputDirectory 'after-lock.handshake'
        $driver=@'
import sys,time
from pathlib import Path
root,signal=map(Path,sys.argv[1:]);sys.path.insert(0,str(root))
import save_resolution as s
def observe(point):
    if point=='after:lock':
        signal.write_text(point,encoding='utf-8')
        while True: time.sleep(.05)
s._checkpoint=observe
p=s.plan(root)
s.apply(root,p['digest'],'accept-all',confirmed=True)
raise RuntimeError('Controlled strong-kill boundary was not reached')
'@
        $info=New-Object Diagnostics.ProcessStartInfo
        $info.FileName=Join-Path $fixture 'runtime\bin\python.exe'
        $info.Arguments='-B -c '+[DesktopWindow]::Quote($driver)+' '+[DesktopWindow]::Quote($fixture)+' '+[DesktopWindow]::Quote($handshake)
        $info.WorkingDirectory=$fixture; $info.UseShellExecute=$false; $info.CreateNoWindow=$true
        $info.RedirectStandardOutput=$true; $info.RedirectStandardError=$true
        $process=[Diagnostics.Process]::Start($info)
        $killRow=@{pid=$process.Id;executable=$info.FileName;arguments=$info.Arguments;fixture=$fixture;started_utc=[DateTime]::UtcNow.ToString('o');completed=$false;method='OS Process.Kill after real lock; not power loss'}
        try {
            $held=$process.Handle; Save-ChildReceipt $killRow 'strong-kill'
            $stdout=$process.StandardOutput.ReadToEndAsync(); $stderr=$process.StandardError.ReadToEndAsync()
            $deadline=[DateTime]::UtcNow.AddSeconds(30)
            while(-not (Test-Path -LiteralPath $handshake)) { if($process.HasExited -or [DateTime]::UtcNow -gt $deadline) { throw 'Real backend did not reach after:lock handshake' }; Start-Sleep -Milliseconds 20 }
            Assert (Test-Path (Join-Path $fixture '.save-import.lock')) 'Handshake preceded the actual lock publication'
            $process.Kill(); Assert ($process.WaitForExit(10000)) 'Kill child exit timeout'
            Assert ($process.ExitCode -is [int] -and $process.ExitCode -ne 0) 'OS kill did not produce a real nonzero exit status'
            Assert ([Threading.Tasks.Task]::WaitAll([Threading.Tasks.Task[]]@($stdout,$stderr),10000)) 'Kill child output drain timeout'
            [IO.File]::WriteAllText((Join-Path $OutputDirectory 'strong-kill.stdout'),$stdout.GetAwaiter().GetResult(),$utf8)
            [IO.File]::WriteAllText((Join-Path $OutputDirectory 'strong-kill.stderr'),$stderr.GetAwaiter().GetResult(),$utf8)
            $killRow.completed=$true
            $killRow.stdout_sha256=Hash-File (Join-Path $OutputDirectory 'strong-kill.stdout')
            $killRow.stderr_sha256=Hash-File (Join-Path $OutputDirectory 'strong-kill.stderr')
        } finally {
            if(-not $process.HasExited) { $process.Kill(); Assert ($process.WaitForExit(10000)) 'Owned kill child did not exit' }
            $killRow.exit_code=$process.ExitCode; $killRow.ended_utc=[DateTime]::UtcNow.ToString('o')
            Save-ChildReceipt $killRow 'strong-kill'; $process.Dispose()
        }
        $desktop.Page('Save'); Await-Task (Invoke-Private $desktop 'RefreshSaveBackups')
        Assert ((Field $desktop 'pendingResolution')['can_resume']) 'Real interrupted A lock did not expose a resumable UI'
        Assert ($window.FindName('SaveResolutionOptions').Children.Count -eq 0 -and $window.FindName('ResumeSaveResolution').IsEnabled) 'Actual pending UI allowed replacement choices or disabled resume'
        Choose-Resume $desktop
        Assert (-not (Test-Path (Join-Path $fixture '.save-import.lock'))) 'Actual native resume did not complete its transaction'
        $passed.Add('Independent production apply with only a checkpoint observer -> external Process.Kill after durable lock -> actual backup refresh/A pending -> native resume Yes -> production resume. Other strong-kill boundaries remain the backend suite responsibility.')
        # Second production pending uses an observer exception, not OS kill.
        [IO.File]::WriteAllText($marker,'{"reason":"second synthetic pending"}',$utf8)
        $abandonSetup=@'
import sys,json,os
from pathlib import Path
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
import save_resolution as s
class Pause(Exception):pass
def point(name):
    if name=='after:lock':raise Pause()
s._checkpoint=point
p=s.plan(root)
try:s.apply(root,p['digest'],'accept-all',confirmed=True)
except Pause:pass
else:raise AssertionError('real lock was not published')
assert (root/'.save-import.lock').is_file()
print(json.dumps({'pid':os.getpid(),'stopped':'checkpoint exception after actual lock; not OS kill'}))
'@
        Invoke-FixturePython $abandonSetup @($fixture) 'abandon-setup'
        $later=Join-Path $fixture 'data\user\savedata\later-synthetic-progress'
        [IO.File]::WriteAllText($later,'new current progress never merged',$utf8)
        Await-Task (Invoke-Private $desktop 'RefreshSaveBackups')
        Assert (-not (Field $desktop 'pendingResolution')['can_resume'] -and (Field $desktop 'pendingResolution')['can_abandon']) 'Changed production prefix has no forward abandonment exit'
        Assert (-not $window.FindName('ResumeSaveResolution').IsEnabled -and $window.FindName('AbandonSaveResolution').IsEnabled) 'Actual pending buttons do not reflect independent eligibility'
        $oldLock=[Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $fixture '.save-import.lock')))
        Choose-Abandon $desktop $false
        Assert ([Convert]::ToBase64String([IO.File]::ReadAllBytes((Join-Path $fixture '.save-import.lock'))) -eq $oldLock) 'Declined abandonment changed the active choice'
        Await-Task (Invoke-Private $desktop 'RefreshSaveBackups'); Choose-Abandon $desktop $true
        Assert (-not (Test-Path (Join-Path $fixture '.save-import.lock')) -and (Test-Path (Join-Path $fixture 'data\user\save-resolution-review.json'))) 'Real abandonment failed to retire its lock behind a review gate'
        Assert ([IO.File]::ReadAllText($later) -eq 'new current progress never merged') 'Abandonment overwrote later current progress'
        Assert (-not $desktop.Busy -and (Plan $desktop)['needed']) 'Abandonment did not return to the processing page'
        $desktop.Validate((Join-Path $OutputDirectory 'abandon-complete-page.png'),'Save')
        $passed.Add('Real second pending A via checkpoint exception (not OS kill), later file invalidates resume -> production dual eligibility -> native No leaves old lock -> native Yes -> JSON/CLI abandonment -> same progress, gate and no automatic launch. Healthy explicit history had needed=false/show_choices=true and visible options.')

    } else { $passed.Add('NOT RUN: real Python backend chain. ViewOnly does not establish transaction safety.') }
    [IO.File]::WriteAllText((Join-Path $OutputDirectory 'result.txt'),[string]::Join("`r`n",$passed),$utf8)
    $passed
} catch {
    [IO.File]::WriteAllText((Join-Path $OutputDirectory 'failure.txt'),($_ | Out-String),$utf8)
    throw
} finally {
    if($desktop) { $desktop.GetType().GetField('operation',$flags).SetValue($desktop,$false); $desktop.Window.Close() }
    # Preserve the unique synthetic fixture and all failures for inspection. No cleanup/delete.
}
