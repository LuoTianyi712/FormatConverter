<#
.SYNOPSIS
    给 Windows 资源管理器右键菜单添加「用 UniConvert 转换」子菜单（对标格式工厂）。
.DESCRIPTION
    在 HKCU（当前用户，无需管理员）注册：
        右键任意文件 → 用 UniConvert 转换 → 转为 PDF / PNG / JPG / WebP / MP3 / MP4 / YAML / 纯文本

    命令走 dist\uniconvert-cli.exe（找不到时按参数 -ExePath 指定）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install_context_menu.ps1
    powershell -ExecutionPolicy Bypass -File install_context_menu.ps1 -Uninstall
    powershell -ExecutionPolicy Bypass -File install_context_menu.ps1 -DryRun   # 只看会改什么
#>
[CmdletBinding()]
param(
    [string]$ExePath = "",
    [switch]$Uninstall,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not $ExePath) {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $ExePath = Join-Path $repoRoot "dist\uniconvert-cli.exe"
}

$rootKey = "HKCU:\Software\Classes\*\shell\UniConvert"
$targets = [ordered]@{
    "01pdf"   = @{ label = "转为 PDF";        format = "pdf" }
    "02png"   = @{ label = "转为 PNG 图片";   format = "png" }
    "03jpg"   = @{ label = "转为 JPG 图片";   format = "jpg" }
    "04webp"  = @{ label = "转为 WebP（省流）"; format = "webp" }
    "05mp3"   = @{ label = "提取/转为 MP3";   format = "mp3" }
    "06mp4"   = @{ label = "转为 MP4";        format = "mp4" }
    "07yaml"  = @{ label = "转为 YAML";       format = "yaml" }
    "08txt"   = @{ label = "转为纯文本";      format = "txt" }
}

function Set-Reg {
    param([string]$Path, [string]$Name, [string]$Value)
    if ($DryRun) {
        Write-Host ("  reg: {0} :: {1} = {2}" -f $Path, $Name, $Value)
        return
    }
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -Path $Path -Force | Out-Null
    }
    if ($Name) { Set-ItemProperty -LiteralPath $Path -Name $Name -Value $Value }
}

if ($Uninstall) {
    Write-Host "卸载右键菜单…"
    if ($DryRun) {
        Write-Host ("  reg: 删除 {0}" -f $rootKey)
    } elseif (Test-Path -LiteralPath $rootKey) {
        Remove-Item -LiteralPath $rootKey -Recurse -Force
    }
    Write-Host "完成（重启资源管理器或稍候即生效）。"
    exit 0
}

if (-not (Test-Path -LiteralPath $ExePath)) {
    Write-Warning "找不到 CLI exe：$ExePath"
    Write-Warning "请先运行 python packaging\build_exe.py，或用 -ExePath 指定路径。"
    if (-not $DryRun) { exit 2 }
}

Write-Host "安装右键菜单 → $ExePath"
Set-Reg -Path $rootKey -Name "(Default)" -Value "用 UniConvert 转换"
Set-Reg -Path $rootKey -Name "Icon" -Value "$ExePath,0"
Set-Reg -Path $rootKey -Name "SubCommands" -Value ""

foreach ($key in $targets.Keys) {
    $item = $targets[$key]
    $subKey = Join-Path $rootKey "shell\$key"
    Set-Reg -Path $subKey -Name "MUIVerb" -Value $item.label
    Set-Reg -Path (Join-Path $subKey "command") -Name "(Default)" `
        -Value ('"{0}" convert "%1" -t {1}' -f $ExePath, $item.format)
}

Write-Host "完成。右键任意文件即可看到「用 UniConvert 转换」。"
Write-Host "卸载：powershell -ExecutionPolicy Bypass -File install_context_menu.ps1 -Uninstall"
