# Generates minimal placeholder PNG icons (color + outline) so the manifest
# can actually sideload. Replace with branded artwork before sharing.
#
# Outputs:
#   teams_bot/manifest/icon-color.png   (192x192, solid color w/ "IT" letters)
#   teams_bot/manifest/icon-outline.png (32x32, transparent w/ white outline)

Add-Type -AssemblyName System.Drawing

$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# --- Color 192x192 ---
$color = New-Object System.Drawing.Bitmap 192,192
$g = [System.Drawing.Graphics]::FromImage($color)
$g.SmoothingMode = 'AntiAlias'
$g.TextRenderingHint = 'AntiAliasGridFit'
$bg = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 0, 120, 212))
$g.FillRectangle($bg, 0, 0, 192, 192)
$font = New-Object System.Drawing.Font 'Segoe UI', 80, ([System.Drawing.FontStyle]::Bold)
$fg   = [System.Drawing.Brushes]::White
$fmt  = New-Object System.Drawing.StringFormat
$fmt.Alignment = 'Center'
$fmt.LineAlignment = 'Center'
$g.DrawString('IT', $font, $fg, (New-Object System.Drawing.RectangleF 0,0,192,192), $fmt)
$g.Dispose()
$color.Save((Join-Path $here 'icon-color.png'), [System.Drawing.Imaging.ImageFormat]::Png)
$color.Dispose()

# --- Outline 32x32 ---
$outline = New-Object System.Drawing.Bitmap 32,32
$g2 = [System.Drawing.Graphics]::FromImage($outline)
$g2.SmoothingMode = 'AntiAlias'
$pen = New-Object System.Drawing.Pen ([System.Drawing.Color]::White), 2
$g2.DrawEllipse($pen, 4, 4, 24, 24)
$g2.Dispose()
$outline.Save((Join-Path $here 'icon-outline.png'), [System.Drawing.Imaging.ImageFormat]::Png)
$outline.Dispose()

Write-Host 'Wrote icon-color.png + icon-outline.png' -ForegroundColor Green
