# Cosmetic-only update. Existing target, arguments, working directory and window style are preserved.
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$ShortcutPath = (Join-Path ([Environment]::GetFolderPath('Desktop')) '대시보드 다시 켜기.lnk')
)

$ErrorActionPreference = 'Stop'
$uiRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$uiExpectedTarget = Join-Path $uiRepo 'scripts\restart_dashboard.cmd'
$uiIcon = Join-Path $uiRepo 'src\auto_trading\ui\assets\auto-trading.ico'
$uiResolvedLink = [IO.Path]::GetFullPath($ShortcutPath)
if (-not (Test-Path -LiteralPath $uiResolvedLink -PathType Leaf)) { throw '바로가기가 없습니다.' }
if ([IO.Path]::GetExtension($uiResolvedLink) -ne '.lnk') { throw '.lnk 파일만 허용합니다.' }
if (-not (Test-Path -LiteralPath $uiIcon -PathType Leaf)) { throw '아이콘 파일이 없습니다.' }
$uiShell = New-Object -ComObject WScript.Shell
$uiLink = $uiShell.CreateShortcut($uiResolvedLink)
if ($uiLink.TargetPath -ne $uiExpectedTarget) { throw '기존 실행 대상이 달라 수정하지 않습니다.' }
$uiBefore = @($uiLink.TargetPath, $uiLink.Arguments, $uiLink.WorkingDirectory, $uiLink.WindowStyle)
if ($PSCmdlet.ShouldProcess($uiResolvedLink, '아이콘과 설명만 변경 (원본 백업)')) {
    $uiBackupDir = Join-Path $uiRepo 'reports\ui\shortcut-backups'
    New-Item -ItemType Directory -Path $uiBackupDir -Force | Out-Null
    $uiBackup = Join-Path $uiBackupDir ('dashboard-restart-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fffffff') + '.lnk')
    Copy-Item -LiteralPath $uiResolvedLink -Destination $uiBackup
    try {
        $uiLink.IconLocation = $uiIcon + ',0'
        $uiLink.Description = 'Auto Trading · 운영 대시보드 다시 켜기 (매매 설정 변경 없음)'
        $uiLink.Save()
        $uiAfterLink = $uiShell.CreateShortcut($uiResolvedLink)
        $uiAfter = @($uiAfterLink.TargetPath, $uiAfterLink.Arguments, $uiAfterLink.WorkingDirectory, $uiAfterLink.WindowStyle)
        if (Compare-Object -ReferenceObject $uiBefore -DifferenceObject $uiAfter) {
            throw '실행 정보가 달라져 원본으로 되돌립니다.'
        }
    } catch {
        Copy-Item -LiteralPath $uiBackup -Destination $uiResolvedLink -Force
        throw
    }
    [PSCustomObject]@{ Shortcut = $uiResolvedLink; Icon = $uiAfterLink.IconLocation; Backup = $uiBackup }
}
