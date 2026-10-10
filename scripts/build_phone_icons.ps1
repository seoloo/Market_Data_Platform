# Build the iPhone home-screen icons (opaque square PNG) from orbit-obi.png.
# Usage: powershell -NoProfile -File scripts\build_phone_icons.ps1
Add-Type -AssemblyName System.Drawing
$assets = Join-Path $PSScriptRoot '..\src\auto_trading\ui\assets'
$src = [System.Drawing.Image]::FromFile((Resolve-Path (Join-Path $assets 'orbit-obi.png')))
foreach ($size in 180, 192, 512) {
    $bmp = New-Object System.Drawing.Bitmap $size, $size
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = 'HighQuality'
    $g.InterpolationMode = 'HighQualityBicubic'
    $rect = New-Object System.Drawing.Rectangle 0, 0, $size, $size
    $top = [System.Drawing.Color]::FromArgb(255, 35, 56, 104)
    $bottom = [System.Drawing.Color]::FromArgb(255, 12, 22, 48)
    $brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush $rect, $top, $bottom, 90
    $g.FillRectangle($brush, $rect)
    $pad = [int]($size * 0.06)
    $g.DrawImage($src, $pad, $pad, $size - 2 * $pad, $size - 2 * $pad)
    $name = if ($size -eq 180) { 'apple-touch-icon.png' } else { "orbit-app-$size.png" }
    $bmp.Save((Join-Path $assets $name), [System.Drawing.Imaging.ImageFormat]::Png)
    $g.Dispose(); $bmp.Dispose(); $brush.Dispose()
}
$src.Dispose()
