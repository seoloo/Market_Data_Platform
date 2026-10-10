# Desktop shortcut "오르빗.lnk" -> dist\AutoTrading.exe. Existing execution fields are preserved.
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$exe = Join-Path $repo 'dist\AutoTrading.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "exe 가 없습니다: $exe" }
$link = Join-Path ([Environment]::GetFolderPath('Desktop')) '오르빗.lnk'
$legacyLink = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Auto Trading.lnk'
$shell = New-Object -ComObject WScript.Shell
if (-not (Test-Path -LiteralPath $link) -and (Test-Path -LiteralPath $legacyLink)) {
    $legacy = $shell.CreateShortcut($legacyLink)
    if ($legacy.TargetPath -ne $exe) { throw '기존 다른 앱 바로가기는 변경하지 않습니다.' }
    $backupDir = Join-Path $repo 'reports\ui\shortcut-backups'
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $backup = Join-Path $backupDir ('before-orbit-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fffffff') + '.lnk')
    Copy-Item -LiteralPath $legacyLink -Destination $backup
    # Both destinations are literal, resolved desktop .lnk paths. No executable or data is moved.
    Move-Item -LiteralPath $legacyLink -Destination $link
}
if (Test-Path -LiteralPath $link) {
    $old = $shell.CreateShortcut($link)
    if ($old.TargetPath -ne $exe) { throw "같은 이름의 다른 바로 가기가 있어 건드리지 않습니다: $link" }
    & (Join-Path $PSScriptRoot 'style_dashboard_shortcut.ps1') -ShortcutPath $link
    exit
}
$lnk = $shell.CreateShortcut($link)
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = $repo
$lnk.IconLocation = (Join-Path $repo 'src\auto_trading\ui\assets\obi.ico') + ',0'
$lnk.Description = '오르빗 - 실거래 홈 (읽기 전용, 주문 없음)'
$lnk.Save()
"shortcut: $link"
