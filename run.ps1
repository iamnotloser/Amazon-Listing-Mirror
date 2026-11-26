# 启动竞品 LISTING 分析器
# 用法：powershell -ExecutionPolicy Bypass -File run.ps1 [-Port 8010] [-Tunnel]
param(
  [int]$Port = 8010,
  [switch]$Tunnel
)

$env:PORT = "$Port"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

if ($Tunnel) {
  $cf = Get-Command cloudflared -ErrorAction SilentlyContinue
  if ($cf) {
    Start-Process -FilePath $cf.Source -ArgumentList "tunnel","--url","http://127.0.0.1:$Port" -WindowStyle Minimized
    Write-Host "Cloudflare 隧道已启动" -ForegroundColor Green
  } else {
    Write-Host "未找到 cloudflared，仅本地访问" -ForegroundColor Yellow
  }
}

Write-Host "服务地址：http://127.0.0.1:$Port" -ForegroundColor Cyan
python "$root\web\app.py"
