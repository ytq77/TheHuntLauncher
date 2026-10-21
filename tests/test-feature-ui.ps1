$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework,PresentationCore,WindowsBase,System.Xaml
$repoPath = Split-Path -Parent $PSScriptRoot
$taskRoot = Join-Path ([IO.Path]::GetTempPath()) ('the-hunt-features-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($taskRoot)
$utf8 = New-Object Text.UTF8Encoding $false
$script:worker = $null
$script:saveTask = $null
try {
    Copy-Item -LiteralPath (Join-Path $repoPath 'launcher\feature-catalog.json') -Destination $taskRoot
    $window = [Windows.Markup.XamlReader]::Parse([IO.File]::ReadAllText((Join-Path $repoPath 'launcher\Launcher.xaml')))
    $controls = @{}
    foreach ($name in @('FeatureItems','FeatureSummary','FeatureStatus','StatusText','SaveFeatures','RecommendFeatures','ExpandFeatures')) {
        $controls[$name] = $window.FindName($name)
        if (-not $controls[$name]) { throw ('Missing feature control: ' + $name) }
    }
    function Show-Error([string]$message) { throw $message }
    . (Join-Path $repoPath 'launcher\Features.ps1')
    if ($script:featureControls.Count -ne 31 -or $script:featureExpanders.Count -ne 6) { throw 'Catalogue controls are incomplete.' }
    if ($script:featureControls.shared_progress.IsChecked -or -not $script:featureControls.death_penalty.IsChecked) { throw 'Wrong recommendations.' }
    if ($script:featureControls.hooks.IsEnabled) { throw 'Required hook switch must stay locked.' }
    $script:featureControls.scale_boss_hp.Text = '60'
    $script:featureControls.scale_damage.Text = '0'
    $script:featureControls.invasions.IsChecked = $false
    Save-Features
    $saved = [IO.File]::ReadAllText((Join-Path $taskRoot 'features.json')) | ConvertFrom-Json
    if ($saved.values.scale_boss_hp -ne 60 -or $saved.values.scale_damage -ne 0 -or $saved.values.invasions -ne $false) { throw 'Saved values do not match controls.' }
    $before = [IO.File]::ReadAllText((Join-Path $taskRoot 'features.json'))
    $script:featureControls.scale_boss_hp.Text = '301'
    $rejected = $false
    try { Save-Features } catch { $rejected = $true }
    if (-not $rejected -or [IO.File]::ReadAllText((Join-Path $taskRoot 'features.json')) -ne $before) { throw 'Invalid percentage was saved.' }
    Set-FeatureControls (Resolve-Features @{})
    Save-Features
    $saved = [IO.File]::ReadAllText((Join-Path $taskRoot 'features.json')) | ConvertFrom-Json
    if ($saved.values.scale_boss_hp -ne 100 -or $saved.values.shared_progress -ne $false) { throw 'Recommendation reset failed.' }
    foreach ($name in @('AdminTarget','AdminPort','AdminDirectory','AdminIdentity','AdminKnownHosts','AdminSudo','AdminPassword','AdminEmail','AdminName','PickAdminIdentity','PickAdminKnownHosts','AdminRefresh','AdminCreate','AdminVerify','AdminAccounts','AdminStatus')) {
        $controls[$name] = $window.FindName($name)
        if (-not $controls[$name]) { throw ('Missing server control: ' + $name) }
    }
    . (Join-Path $repoPath 'launcher\Server.ps1')
    $controls.AdminTarget.Text = 'ubuntu@example.test'
    $controls.AdminDirectory.Text = '/opt/test-bbsc'
    $controls.AdminPort.Text = '2222'
    $controls.AdminSudo.IsChecked = $true
    $admin = Admin-Connection
    if ($admin.port -ne 2222 -or -not $admin.sudo -or $admin.target -ne 'ubuntu@example.test') { throw 'Server field binding failed.' }
    $controls.AdminTarget.Text = 'host;id'
    $rejected = $false
    try { Admin-Connection | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw 'Invalid SSH target accepted.' }
    'PASS: WPF feature controls, typed persistence, invalid-value rejection, and reset.'
    'PASS: WPF server controls, connection binding, and invalid-target rejection.'
} finally {
    $fullPath = [IO.Path]::GetFullPath($taskRoot)
    $tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $fullPath.StartsWith($tempBase, [StringComparison]::OrdinalIgnoreCase) -or (Split-Path -Leaf $fullPath) -notlike 'the-hunt-features-*') { throw 'Unexpected test cleanup path.' }
    Remove-Item -LiteralPath $fullPath -Recurse -Force
}
