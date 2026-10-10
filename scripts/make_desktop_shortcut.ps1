# Desktop Orbit shortcut -> dist\AutoTrading.exe. ASCII source supports Windows PowerShell 5.1.
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$exe = Join-Path $repo 'dist\AutoTrading.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "Executable missing: $exe" }
$orbitName = -join ([char[]]@(0xC624, 0xB974, 0xBE57))
$restartName = -join ([char[]]@(0xB300, 0xC2DC, 0xBCF4, 0xB4DC, 0x20, 0xB2E4, 0xC2DC, 0x20, 0xCF1C, 0xAE30))
$link = Join-Path ([Environment]::GetFolderPath('Desktop')) ($orbitName + '.lnk')
$legacyLink = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Auto Trading.lnk'
$shell = New-Object -ComObject WScript.Shell
if (-not (Test-Path -LiteralPath $link) -and (Test-Path -LiteralPath $legacyLink)) {
    $legacy = $shell.CreateShortcut($legacyLink)
    if ($legacy.TargetPath -ne $exe) { throw 'Refusing to change a different application.' }
    $backupDir = Join-Path $repo 'reports\ui\shortcut-backups'
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    $backup = Join-Path $backupDir ('before-orbit-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fffffff') + '.lnk')
    Copy-Item -LiteralPath $legacyLink -Destination $backup
    # Both destinations are literal, resolved desktop .lnk paths. No executable or data is moved.
    Move-Item -LiteralPath $legacyLink -Destination $link
}
if (Test-Path -LiteralPath $link) {
    $old = $shell.CreateShortcut($link)
    if ($old.TargetPath -ne $exe) { throw "Refusing to change a different shortcut: $link" }
    & (Join-Path $PSScriptRoot 'style_dashboard_shortcut.ps1') -ShortcutPath $link
} else {
    $lnk = $shell.CreateShortcut($link)
    $lnk.TargetPath = $exe
    $lnk.WorkingDirectory = $repo
    $lnk.IconLocation = (Join-Path $repo 'src\auto_trading\ui\assets\orbit-transparent.ico') + ',0'
    $lnk.Description = $orbitName + ' - latest dashboard auto-start (no orders)'
    $lnk.Save()
}
# User requested restoring this shortcut. It starts the same fresh, unprivileged UI path.
$restartLink = Join-Path ([Environment]::GetFolderPath('Desktop')) ($restartName + '.lnk')
$restart = $shell.CreateShortcut($restartLink)
if (Test-Path -LiteralPath $restartLink) {
    if ($restart.TargetPath -notin @($exe, (Join-Path $repo 'scripts\restart_dashboard.cmd'))) {
        throw 'Refusing to change a different restart shortcut.'
    }
    $backupDir = Join-Path $repo 'reports\ui\shortcut-backups'
    New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
    Copy-Item -LiteralPath $restartLink -Destination (Join-Path $backupDir ('restart-before-auto-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fffffff') + '.lnk'))
}
$restart.TargetPath = $exe
$restart.Arguments = '--restart-dashboard'
$restart.WorkingDirectory = $repo
$restart.IconLocation = (Join-Path $repo 'src\auto_trading\ui\assets\orbit-transparent.ico') + ',0'
$restart.Description = $orbitName + ' - latest UI (also automatic in app; no trading restart)'
$restart.Save()
"shortcut: $link"
"shortcut: $restartLink"
