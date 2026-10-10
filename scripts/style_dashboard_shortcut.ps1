# Cosmetic-only update. Existing target, arguments, working directory and window style are preserved.
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$ShortcutPath = (Join-Path ([Environment]::GetFolderPath('Desktop')) ((-join ([char[]]@(0xB300, 0xC2DC, 0xBCF4, 0xB4DC, 0x20, 0xB2E4, 0xC2DC, 0x20, 0xCF1C, 0xAE30))) + '.lnk'))
)

$ErrorActionPreference = 'Stop'
$uiRepo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$uiExpectedTarget = Join-Path $uiRepo 'scripts\restart_dashboard.cmd'
$uiAppTarget = Join-Path $uiRepo 'dist\AutoTrading.exe'
$uiIcon = Join-Path $uiRepo 'src\auto_trading\ui\assets\orbit-transparent.ico'
$uiResolvedLink = [IO.Path]::GetFullPath($ShortcutPath)
if (-not (Test-Path -LiteralPath $uiResolvedLink -PathType Leaf)) { throw 'Shortcut missing.' }
if ([IO.Path]::GetExtension($uiResolvedLink) -ne '.lnk') { throw 'Only .lnk files are allowed.' }
if (-not (Test-Path -LiteralPath $uiIcon -PathType Leaf)) { throw 'Icon missing.' }
$uiShell = New-Object -ComObject WScript.Shell
$uiLink = $uiShell.CreateShortcut($uiResolvedLink)
if ($uiLink.TargetPath -notin @($uiExpectedTarget, $uiAppTarget)) { throw 'Unexpected shortcut target.' }
$uiBefore = @($uiLink.TargetPath, $uiLink.Arguments, $uiLink.WorkingDirectory, $uiLink.WindowStyle)
if ($PSCmdlet.ShouldProcess($uiResolvedLink, 'Change icon/description only (with backup)')) {
    $uiBackupDir = Join-Path $uiRepo 'reports\ui\shortcut-backups'
    New-Item -ItemType Directory -Path $uiBackupDir -Force | Out-Null
    $uiBackup = Join-Path $uiBackupDir ('dashboard-restart-' + [DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fffffff') + '.lnk')
    Copy-Item -LiteralPath $uiResolvedLink -Destination $uiBackup
    try {
        $uiLink.IconLocation = $uiIcon + ',0'
        $uiLink.Description = (-join ([char[]]@(0xC624, 0xB974, 0xBE57))) + ' - latest UI, no trading settings changed'
        $uiLink.Save()
        $uiAfterLink = $uiShell.CreateShortcut($uiResolvedLink)
        $uiAfter = @($uiAfterLink.TargetPath, $uiAfterLink.Arguments, $uiAfterLink.WorkingDirectory, $uiAfterLink.WindowStyle)
        if (Compare-Object -ReferenceObject $uiBefore -DifferenceObject $uiAfter) {
            throw 'Execution fields changed; restoring the original.'
        }
    } catch {
        Copy-Item -LiteralPath $uiBackup -Destination $uiResolvedLink -Force
        throw
    }
    [PSCustomObject]@{ Shortcut = $uiResolvedLink; Icon = $uiAfterLink.IconLocation; Backup = $uiBackup }
}
