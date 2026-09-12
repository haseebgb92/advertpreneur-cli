param([Parameter(Mandatory=$true)][string]$SessionsDir)

Add-Type -AssemblyName PresentationFramework
Add-Type -AssemblyName PresentationCore
Add-Type -AssemblyName WindowsBase

$createdNew = $false
$mutex = New-Object System.Threading.Mutex($true, 'AdvertpreneurCliBeaconV0142', [ref]$createdNew)
if (-not $createdNew) { $mutex.Dispose(); exit 0 }

$heartbeatPath = Join-Path $SessionsDir 'beacon-heartbeat.json'

$window = New-Object System.Windows.Window
$window.Width = 390
$window.Height = 58
$window.WindowStyle = 'None'
$window.ResizeMode = 'NoResize'
$window.AllowsTransparency = $true
$window.Background = [System.Windows.Media.Brushes]::Transparent
$window.Topmost = $true
$window.ShowInTaskbar = $false
$window.WindowStartupLocation = 'Manual'
$window.Left = [SystemParameters]::WorkArea.Right - $window.Width - 18
$window.Top = [SystemParameters]::WorkArea.Top + 18

$outer = New-Object System.Windows.Controls.StackPanel
$outer.Orientation = 'Vertical'
$window.Content = $outer
$window.Add_MouseLeftButtonDown({ try { $window.DragMove() } catch {} })

function Brush([string]$hex) {
    try { return New-Object System.Windows.Media.SolidColorBrush([System.Windows.Media.ColorConverter]::ConvertFromString($hex)) }
    catch { return New-Object System.Windows.Media.SolidColorBrush([System.Windows.Media.Color]::FromRgb(243,161,38)) }
}

function Live-Sessions {
    if (-not (Test-Path -LiteralPath $SessionsDir)) { return @() }
    $rows = @()
    Get-ChildItem -LiteralPath $SessionsDir -Filter '*.json' -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne 'beacon-heartbeat.json' } | ForEach-Object {
        try {
            $s = (Get-Content -LiteralPath $_.FullName -Raw -ErrorAction Stop) | ConvertFrom-Json
            $pidValue = [int]($s.pid)
            if ($pidValue -gt 0 -and -not (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)) {
                Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
                return
            }
            if (-not $s.close) { $rows += $s }
        } catch {}
    }
    return @($rows | Sort-Object updated -Descending | Select-Object -First 6)
}

function Render($rows) {
    $outer.Children.Clear()
    $count = @($rows).Count
    foreach ($s in @($rows)) {
        $border = New-Object System.Windows.Controls.Border
        $border.CornerRadius = New-Object System.Windows.CornerRadius(18)
        $border.Background = Brush '#23252A'
        $border.BorderBrush = Brush '#46484E'
        $border.BorderThickness = New-Object System.Windows.Thickness(1)
        $border.Padding = New-Object System.Windows.Thickness(13,7,13,7)
        $border.Margin = New-Object System.Windows.Thickness(0,0,0,5)

        $grid = New-Object System.Windows.Controls.Grid
        $grid.ColumnDefinitions.Add((New-Object System.Windows.Controls.ColumnDefinition -Property @{Width='Auto'}))
        $grid.ColumnDefinitions.Add((New-Object System.Windows.Controls.ColumnDefinition -Property @{Width='*'}))
        $dot = New-Object System.Windows.Shapes.Ellipse
        $dot.Width = 9; $dot.Height = 9; $dot.Fill = Brush ([string]$s.color)
        $dot.Margin = New-Object System.Windows.Thickness(0,0,10,0); $dot.VerticalAlignment = 'Center'
        [System.Windows.Controls.Grid]::SetColumn($dot,0)

        $stack = New-Object System.Windows.Controls.StackPanel
        $title = New-Object System.Windows.Controls.TextBlock
        $provider = if ($s.provider) { ([string]$s.provider).ToUpper() } else { 'CLI' }
        $model = if ($s.model) { [string]$s.model } else { '' }
        $title.Text = if ($count -gt 1) { "$provider · $model · $($s.status)" } else { "$provider · $model · $($s.status)" }
        $title.Foreground = [System.Windows.Media.Brushes]::White; $title.FontSize = 12; $title.FontWeight = 'SemiBold'
        $detail = New-Object System.Windows.Controls.TextBlock
        $activity = if ($s.activity) { [string]$s.activity } elseif ($s.detail) { [string]$s.detail } else { [string]$s.project }
        if ([string]$s.status -eq 'Working') {
            $file = if ($s.current_file) { [string]$s.current_file } else { '' }
            $elapsed = ''
            try {
                if ([double]$s.started_at -gt 0) {
                    $seconds = [Math]::Max(0, [int]([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double]$s.started_at))
                    $elapsed = if ($seconds -ge 60) { ' · {0}:{1:00}' -f [int]($seconds / 60), ($seconds % 60) } else { " · ${seconds}s" }
                }
            } catch {}
            $detail.Text = 'coding' + $(if ($file) { " · $file" } else { '' }) + $elapsed
        } else {
            $detail.Text = $activity
        }
        $detail.Foreground = Brush '#9A9EA6'; $detail.FontSize = 10; $detail.TextTrimming = 'CharacterEllipsis'
        $stack.Children.Add($title) | Out-Null; $stack.Children.Add($detail) | Out-Null
        [System.Windows.Controls.Grid]::SetColumn($stack,1)
        $grid.Children.Add($dot) | Out-Null; $grid.Children.Add($stack) | Out-Null
        $border.Child = $grid
        $quota = if ($s.quota) { [string]$s.quota } else { 'quota unavailable/not applicable' }
        $effort = if ($s.effort) { "Reasoning: $($s.effort)`n" } else { '' }
        $fileTip = if ($s.current_file) { "`nFile: $($s.current_file)" } else { '' }
        $border.ToolTip = "Session: $($s.session_id)`nProject: $($s.project_path)`nProvider/model: $provider / $model$fileTip`n$effortQuota: $quota`nTool calls: $($s.tool_calls)"
        $outer.Children.Add($border) | Out-Null
    }
    $window.Height = [Math]::Max(58, [Math]::Min(350, $count * 58))
    $window.Left = [SystemParameters]::WorkArea.Right - $window.Width - 18
}

$emptySince = $null
$lastKey = ''
$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(450)
$timer.Add_Tick({
    try {
        if (-not (Test-Path -LiteralPath $SessionsDir)) { New-Item -ItemType Directory -Path $SessionsDir -Force | Out-Null }
        $hb = @{ updated = ([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0); pid = $PID } | ConvertTo-Json -Compress
        Set-Content -LiteralPath $heartbeatPath -Value $hb -Encoding UTF8 -Force
        $rows = @(Live-Sessions)
        if ($rows.Count -eq 0) {
            if ($null -eq $emptySince) { $script:emptySince = Get-Date }
            if (((Get-Date) - $emptySince).TotalSeconds -gt 8) { $timer.Stop(); $window.Close(); return }
        } else { $script:emptySince = $null }
        $clockKey = if (@($rows | Where-Object { [string]$_.status -eq 'Working' }).Count -gt 0) { [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() } else { 0 }
        $key = (($rows | ForEach-Object { "$($_.session_id)|$($_.updated)|$($_.status)|$($_.detail)|$($_.activity)|$($_.current_file)|$($_.tool_calls)|$($_.quota)" }) -join ';') + "|$clockKey"
        if ($key -ne $lastKey) { $script:lastKey = $key; Render $rows }
    } catch {}
})
$window.Add_Closed({ try { $timer.Stop(); Remove-Item -LiteralPath $heartbeatPath -Force -ErrorAction SilentlyContinue; $mutex.ReleaseMutex(); $mutex.Dispose() } catch {} })
$timer.Start(); $window.ShowDialog() | Out-Null
