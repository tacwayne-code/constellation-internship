param(
    [switch]$NonInteractive,
    [switch]$ValidateUi,
    [string]$PreviewPath = '',
    [string]$TargetPath = '',
    [string]$ListenHost = '',
    [string]$PlcHost = '192.168.0.100',
    [int]$WebPort = 8765,
    [int]$PlcPort = 102,
    [int]$Rack = 0,
    [int]$Slot = 1,
    [string]$HistoryPath = ''
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Install.Core.ps1')
if ($NonInteractive) {
    Install-WcsPackage -PackageRoot $PSScriptRoot -TargetPath $TargetPath -ListenHost $ListenHost -PlcHost $PlcHost -WebPort $WebPort -PlcPort $PlcPort -Rack $Rack -Slot $Slot -HistoryPath $HistoryPath | ConvertTo-Json
    exit 0
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object Windows.Forms.Form
$form.Text = 'WCS 真机调试套件 · 安装'
$form.ClientSize = New-Object Drawing.Size(700, 595)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.Font = New-Object Drawing.Font('Microsoft YaHei UI', 10)
function Add-Label($text, $x, $y, $width=650, $height=28) {
    $control = New-Object Windows.Forms.Label
    $control.Text=$text; $control.Location=New-Object Drawing.Point($x,$y); $control.Size=New-Object Drawing.Size($width,$height)
    $form.Controls.Add($control)
}
function Add-Text($text,$x,$y,$width=490) {
    $control=New-Object Windows.Forms.TextBox
    $control.Text=$text; $control.Location=New-Object Drawing.Point($x,$y); $control.Size=New-Object Drawing.Size($width,28)
    $form.Controls.Add($control); return $control
}
Add-Label '自带运行环境 · 不需要 Python / Node / TIA · 不启动软 PLC' 24 18
Add-Label '安装位置' 24 65 125
$targetBox = Add-Text (Join-Path $env:LOCALAPPDATA 'Programs\WcsPlcCommissioning') 150 62 430
$browse=New-Object Windows.Forms.Button
$browse.Text='浏览'; $browse.Location=New-Object Drawing.Point(590,60); $browse.Size=New-Object Drawing.Size(85,31)
$browse.Add_Click({
    $dialog=New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description='请选择独立安装文件夹（不能与安装包解压位置重叠）'
    if ($dialog.ShowDialog() -eq 'OK') { $targetBox.Text=$dialog.SelectedPath }
    $dialog.Dispose()
}); $form.Controls.Add($browse)
Add-Label '公司网卡 IP' 24 113 125
$lanBox=New-Object Windows.Forms.ComboBox
$lanBox.DropDownStyle='DropDownList'; $lanBox.Location=New-Object Drawing.Point(150,110); $lanBox.Size=New-Object Drawing.Size(525,28)
$addresses=@(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object {
    $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' -and $_.AddressState -eq 'Preferred'
} | Sort-Object @{Expression={if ($_.IPAddress -like '192.168.1.*') {0} elseif ($_.IPAddress -like '192.168.0.*') {2} else {1}}},IPAddress)
foreach ($address in $addresses) { [void]$lanBox.Items.Add($address.IPAddress + '  |  ' + $address.InterfaceAlias) }
if ($lanBox.Items.Count -gt 0) { $lanBox.SelectedIndex=0 }
$form.Controls.Add($lanBox)
Add-Label '选择新主机的公司局域网地址；PLC 网卡地址请在 Windows 中另行设置。' 150 144 525 40
Add-Label '网页端口' 24 194 125
$webBox=Add-Text '8765' 150 191 110
Add-Label 'PLC 地址' 24 237 125
$plcBox=Add-Text '192.168.0.100' 150 234 180
Add-Label 'PLC 端口' 365 237 95
$plcPortBox=Add-Text '102' 470 234 100
Add-Label '机架 / 槽号' 24 280 125
$rackBox=Add-Text '0' 150 277 75
$slotBox=Add-Text '1' 240 277 75
Add-Label '继承任务记录' 24 324 125
$historyBox=Add-Text '' 150 321 430
$historyBrowse=New-Object Windows.Forms.Button
$historyBrowse.Text='选择'; $historyBrowse.Location=New-Object Drawing.Point(590,319); $historyBrowse.Size=New-Object Drawing.Size(85,31)
$historyBrowse.Add_Click({
    $dialog=New-Object Windows.Forms.OpenFileDialog
    $dialog.Filter='任务记录 (physical-commands.sqlite3)|physical-commands.sqlite3|SQLite (*.sqlite3)|*.sqlite3'
    if ($dialog.ShowDialog() -eq 'OK') { $historyBox.Text=$dialog.FileName }
    $dialog.Dispose()
}); $form.Controls.Add($historyBrowse)
Add-Label '可选：旧后台 data\physical-commands.sqlite3；升级安装会保留原配置、密钥和记录。' 150 354 525 45
$shortcut=New-Object Windows.Forms.CheckBox
$shortcut.Text='创建桌面启动快捷方式'; $shortcut.Checked=$true; $shortcut.Location=New-Object Drawing.Point(24,407); $shortcut.Size=New-Object Drawing.Size(260,30); $form.Controls.Add($shortcut)
$firewall=New-Object Windows.Forms.CheckBox
$firewall.Text='允许公司局域网访问（需要管理员授权）'; $firewall.Checked=$true; $firewall.Location=New-Object Drawing.Point(300,407); $firewall.Size=New-Object Drawing.Size(380,30); $form.Controls.Add($firewall)
Add-Label '更换后台前，先退出旧后台，避免两个上位机同时控制同一 PLC。' 24 450 650 36
$status=New-Object Windows.Forms.Label
$status.Text='安装完成后由你启动后台；安装过程不连接 PLC。'; $status.Location=New-Object Drawing.Point(24,496); $status.Size=New-Object Drawing.Size(495,70); $form.Controls.Add($status)
$install=New-Object Windows.Forms.Button
$install.Text='安装'; $install.Location=New-Object Drawing.Point(545,505); $install.Size=New-Object Drawing.Size(130,45)
$install.Add_Click({
    $install.Enabled=$false
    try {
        if (-not $lanBox.SelectedItem) { throw '没有找到可用网卡，请先连接公司局域网。' }
        $status.Text='正在校验文件、复制运行环境并验证配置…'; [Windows.Forms.Application]::DoEvents()
        $result=Install-WcsPackage -PackageRoot $PSScriptRoot -TargetPath $targetBox.Text -ListenHost (($lanBox.SelectedItem -split '\s+')[0]) -PlcHost $plcBox.Text.Trim() -WebPort ([int]$webBox.Text) -PlcPort ([int]$plcPortBox.Text) -Rack ([int]$rackBox.Text) -Slot ([int]$slotBox.Text) -HistoryPath $historyBox.Text.Trim()
        $notes=''
        if ($shortcut.Checked) {
            try {
                $shell=New-Object -ComObject WScript.Shell
                $link=$shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'WCS 真机调试后台.lnk'))
                $link.TargetPath=Join-Path $result.Path 'StartBackend.cmd'; $link.WorkingDirectory=$result.Path
                $link.IconLocation=(Join-Path $result.Path 'WcsPlcConsole.exe') + ',0'; $link.Save()
            } catch { $notes += "`n桌面快捷方式创建失败，可从安装目录启动。" }
        }
        if ($firewall.Checked) {
            try {
                $helper=Join-Path $result.Path 'tools\firewall.ps1'
                $process=Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"' + $helper + '"')) -WindowStyle Hidden -Wait -PassThru
                if ($process.ExitCode -ne 0) { throw 'Firewall setup was not completed.' }
            } catch { $notes += "`n防火墙规则未完成；需要远程访问时双击 AllowLan.cmd。" }
        }
        $status.Text='安装完成；双击桌面快捷方式或 StartBackend.cmd 启动。'
        [Windows.Forms.MessageBox]::Show("安装完成：$($result.Path)`n网页：$($result.Url)`n启动后在 data\physical-control-key.txt 查看新密钥。$notes", '安装完成') | Out-Null
    } catch {
        $status.Text='安装未完成，请检查以下提示。'
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, '安装提示') | Out-Null
    } finally { $install.Enabled=$true }
}); $form.Controls.Add($install)
if ($ValidateUi) {
    $form.CreateControl()
    $form.PerformLayout()
    if ($PreviewPath) {
        $bitmap=New-Object Drawing.Bitmap($form.Width,$form.Height)
        try {
            $form.DrawToBitmap($bitmap,(New-Object Drawing.Rectangle(0,0,$form.Width,$form.Height)))
            $bitmap.Save($PreviewPath,[Drawing.Imaging.ImageFormat]::Png)
        } finally { $bitmap.Dispose() }
    }
    @{status='ok'; controls=$form.Controls.Count; network_options=$lanBox.Items.Count} | ConvertTo-Json
    $form.Dispose()
    exit 0
}
[void]$form.ShowDialog()
$form.Dispose()
