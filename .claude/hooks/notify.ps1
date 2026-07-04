$ErrorActionPreference = 'SilentlyContinue'

$raw = [Console]::In.ReadToEnd()
$data = $null
try { $data = $raw | ConvertFrom-Json } catch {}

$title = "Claude Code"
$text = $null

if ($data.hook_event_name -eq 'Stop') {
    $text = "Finished responding"
}
elseif ($data.hook_event_name -eq 'Notification' -and $data.notification_type -eq 'permission_prompt') {
    if ($data.message) { $text = $data.message } else { $text = "Needs your permission" }
}

if (-not $text) { exit 0 }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Information
$icon.Visible = $true
$icon.ShowBalloonTip(6000, $title, $text, [System.Windows.Forms.ToolTipIcon]::Info)
Start-Sleep -Seconds 6
$icon.Dispose()
