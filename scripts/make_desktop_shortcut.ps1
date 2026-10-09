# Desktop shortcut "Auto Trading.lnk" -> dist\AutoTrading.exe. Refuses to overwrite a link that points elsewhere.
$ErrorActionPreference = 'Stop'
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$exe = Join-Path $repo 'dist\AutoTrading.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw "exe 가 없습니다: $exe" }
$link = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Auto Trading.lnk'
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $link) {
    $old = $shell.CreateShortcut($link)
    if ($old.TargetPath -ne $exe) { throw "같은 이름의 다른 바로 가기가 있어 건드리지 않습니다: $link" }
}
$lnk = $shell.CreateShortcut($link)
$lnk.TargetPath = $exe
$lnk.WorkingDirectory = $repo
$lnk.IconLocation = $exe + ',0'
$lnk.Description = 'Auto Trading - 실거래 홈 (읽기 전용, 주문 없음)'
$lnk.Save()
"shortcut: $link"
