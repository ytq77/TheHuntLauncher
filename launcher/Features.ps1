# Shared catalogue: controls and session runner use the same names/defaults.
$script:featureControls = @{}
$script:featureExpanders = @()
$script:featureCatalog = [IO.File]::ReadAllText((Join-Path $taskRoot 'feature-catalog.json')) | ConvertFrom-Json
$script:featurePath = Join-Path $taskRoot 'features.json'

function Resolve-Features([hashtable]$values) {
    $entries = @($script:featureCatalog.settings | Where-Object { $_.type -in @('bool','int') })
    foreach ($key in $values.Keys) {
        if ($key -notin $entries.id) { throw '功能配置包含当前版本不支持的项目，请恢复推荐后保存。' }
    }
    $result = @{}
    foreach ($item in $entries) {
        $value = if ($values.ContainsKey($item.id)) { $values[$item.id] } else { $item.default }
        if ($item.type -eq 'bool' -and $value -isnot [bool]) { throw ($item.title + '必须为开启或关闭。') }
        if ($item.type -eq 'int' -and (($value -isnot [int] -and $value -isnot [long]) -or $value -lt $item.min -or $value -gt $item.max)) {
            throw ($item.title + '必须是 ' + $item.min + '–' + $item.max + ' 的整数。')
        }
        if (-not $item.editable -and $value -ne $item.default) { throw ($item.title + '是本包的必要基础功能。') }
        $result[$item.id] = $value
    }
    return $result
}
function Set-FeatureControls([hashtable]$values) {
    foreach ($item in $script:featureCatalog.settings) {
        if ($item.type -eq 'bool') { $script:featureControls[$item.id].IsChecked = $values[$item.id] }
        if ($item.type -eq 'int') { $script:featureControls[$item.id].Text = [string]$values[$item.id] }
    }
    Update-FeatureSummary
}
function Get-FeatureControls {
    $values = @{}
    foreach ($item in $script:featureCatalog.settings) {
        if ($item.type -eq 'bool') { $values[$item.id] = [bool]$script:featureControls[$item.id].IsChecked }
        if ($item.type -eq 'int') {
            $text = $script:featureControls[$item.id].Text.Trim()
            $number = 0
            if ($text -notmatch '^\d+$' -or -not [int]::TryParse($text, [ref]$number)) { throw ($item.title + '需要填写整数百分比。') }
            $values[$item.id] = $number
        }
    }
    return (Resolve-Features $values)
}
function Update-FeatureSummary {
    $shared = if ($script:featureControls.shared_progress.IsChecked) { '开' } else { '关' }
    $scaling = if ($script:featureControls.scaling.IsChecked) { '开' } else { '关' }
    $controls.FeatureSummary.Text = '共享进度：' + $shared + '  ·  敌人强化：' + $scaling + '  ·  队友互伤由纷争之铃控制'
}
function Save-Features {
    if ($script:worker -or $script:saveTask -or $script:adminTask -or (Get-Process bb-probe -ErrorAction SilentlyContinue)) { throw '请先正常退出游戏，并等待管理操作结束后保存功能设置。' }
    $values = Get-FeatureControls
    if ($values.shared_progress -and -not $script:savedFeatures.shared_progress) {
        $message = "客机归返后，将把房主已有的 Boss、捷径、门和提灯进度追加到客机的无缝存档。`n`n再次关闭不会撤销已经合并的进度。建议使用专门的合作角色，并先备份双方存档。`n`n确认开启永久共享进度？"
        if ([Windows.MessageBox]::Show($window, $message, '永久共享世界进度', 'YesNo', 'Question', 'No') -ne 'Yes') { return }
    }
    $json = @{ schema=1; values=$values } | ConvertTo-Json -Depth 4
    $temp = $script:featurePath + '.tmp'
    [IO.File]::WriteAllText($temp, $json, $utf8)
    if (Test-Path -LiteralPath $script:featurePath) { [IO.File]::Replace($temp, $script:featurePath, [NullString]::Value) }
    else { [IO.File]::Move($temp, $script:featurePath) }
    $script:savedFeatures = $values
    $script:featuresDirty = $false
    $controls.FeatureStatus.Text = '功能配置已保存，下次启动游戏生效。'
    $controls.StatusText.Text = $controls.FeatureStatus.Text
    Update-FeatureSummary
}
function New-FeatureText([string]$text, [int]$size = 12) {
    $block = New-Object Windows.Controls.TextBlock
    $block.Text = $text
    $block.TextWrapping = 'Wrap'
    $block.FontSize = $size
    $block.Foreground = '#A6AAA8'
    $block.LineHeight = 22
    return $block
}
foreach ($group in $script:featureCatalog.groups) {
    $expander = New-Object Windows.Controls.Expander
    $expander.Header = $group + '  ·  ' + @($script:featureCatalog.settings | Where-Object { $_.category -eq $group }).Count + ' 项'
    $expander.Foreground = '#D7D5CE'
    $expander.FontSize = 16
    $expander.Margin = '0,0,0,14'
    $panel = New-Object Windows.Controls.StackPanel
    foreach ($item in @($script:featureCatalog.settings | Where-Object { $_.category -eq $group })) {
        $card = New-Object Windows.Controls.Border
        $card.Background = '#191A1C'
        $card.BorderBrush = '#37383A'
        $card.BorderThickness = 1
        $card.Padding = 16
        $card.Margin = '0,10,0,0'
        $body = New-Object Windows.Controls.StackPanel
        if ($item.type -eq 'bool') {
            $featureControl = New-Object Windows.Controls.CheckBox
            $featureControl.Content = $item.title
            $featureControl.FontSize = 15
            $featureControl.Foreground = '#E0DDD3'
            $featureControl.IsEnabled = [bool]$item.editable
            $script:featureControls[$item.id] = $featureControl
            [void]$body.Children.Add($featureControl)
        } else {
            $title = New-FeatureText $item.title 15
            $title.Foreground = '#E0DDD3'
            [void]$body.Children.Add($title)
            if ($item.type -eq 'int') {
                $featureControl = New-Object Windows.Controls.TextBox
                $featureControl.Width = 90
                $featureControl.HorizontalAlignment = 'Left'
                $featureControl.Margin = '0,8,0,0'
                $script:featureControls[$item.id] = $featureControl
                [void]$body.Children.Add($featureControl)
            }
        }
        $description = New-FeatureText $item.description
        $description.Margin = '0,8,0,0'
        [void]$body.Children.Add($description)
        $scope = New-FeatureText $item.scope 11
        $scope.Foreground = '#B8B2A2'
        $scope.Margin = '0,6,0,0'
        [void]$body.Children.Add($scope)
        $card.Child = $body
        [void]$panel.Children.Add($card)
    }
    $expander.Content = $panel
    $script:featureExpanders += $expander
    [void]$controls.FeatureItems.Children.Add($expander)
}
$script:savedFeatures = Resolve-Features @{}
Set-FeatureControls $script:savedFeatures
if (Test-Path -LiteralPath $script:featurePath) {
    try {
        $document = [IO.File]::ReadAllText($script:featurePath) | ConvertFrom-Json
        if ($document.schema -ne 1 -or -not $document.values -or $document.values -isnot [PSCustomObject]) { throw '功能配置格式不正确。' }
        $provided = @{}
        $document.values.PSObject.Properties | ForEach-Object { $provided[$_.Name] = $_.Value }
        $script:savedFeatures = Resolve-Features $provided
        Set-FeatureControls $script:savedFeatures
    } catch { $controls.FeatureStatus.Text = '功能配置无法读取，请恢复推荐后保存；游戏启动会核对配置。' }
}
$controls.SaveFeatures.Add_Click({ try { Save-Features } catch { Show-Error $_.Exception.Message } })
$controls.RecommendFeatures.Add_Click({
    Set-FeatureControls (Resolve-Features @{})
    $controls.FeatureStatus.Text = '已填入推荐方案，点击「保存功能设置」后生效。'
})
$controls.ExpandFeatures.Add_Click({
    $expand = @($script:featureExpanders | Where-Object { -not $_.IsExpanded }).Count -gt 0
    foreach ($item in $script:featureExpanders) { $item.IsExpanded = $expand }
    $controls.ExpandFeatures.Content = if ($expand) { '收起全部' } else { '展开全部' }
})
$script:featuresDirty = $false
foreach ($item in $script:featureCatalog.settings) {
    $changed = {
        $script:featuresDirty = $true
        $controls.FeatureStatus.Text = '有未保存的功能变更，请点击「保存功能设置」。'
    }
    if ($item.type -eq 'bool' -and $item.editable) {
        $script:featureControls[$item.id].Add_Checked($changed)
        $script:featureControls[$item.id].Add_Unchecked($changed)
    }
    if ($item.type -eq 'int') { $script:featureControls[$item.id].Add_TextChanged($changed) }
}
