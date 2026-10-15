[CmdletBinding()]
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$logDirectory = Join-Path $projectRoot "logs\services"
New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

function Test-LocalPort {
    param([int]$Port)
    return Test-NetConnection -ComputerName localhost -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue
}

function Start-ProjectService {
    param(
        [string]$Name,
        [string]$Module,
        [int]$Port
    )

    if (Test-LocalPort -Port $Port) {
        Write-Host "[跳过] $Name 已在端口 $Port 运行。" -ForegroundColor Yellow
        return
    }

    $stdoutPath = Join-Path $logDirectory "$Name.out.log"
    $stderrPath = Join-Path $logDirectory "$Name.err.log"
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $filePath = $venvPython
        $arguments = @("-m", $Module)
    } else {
        $uvCommand = Get-Command uv -ErrorAction Stop
        $filePath = $uvCommand.Source
        $arguments = @("run", "python", "-m", $Module)
    }
    Start-Process `
        -FilePath $filePath `
        -ArgumentList $arguments `
        -WorkingDirectory $projectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath
    Write-Host "[启动] $Name 正在启动，日志：$stdoutPath" -ForegroundColor Green
}

Write-Host "=== 本机知识库启动检查 ===" -ForegroundColor Cyan
$dependencies = @(
    @{ Name = "Milvus"; Port = 19530 },
    @{ Name = "MinIO S3 API"; Port = 9000 },
    @{ Name = "MongoDB"; Port = 27017 },
    @{ Name = "MinerU 本地 API"; Port = 8100 }
)

foreach ($dependency in $dependencies) {
    if (Test-LocalPort -Port $dependency.Port) {
        Write-Host "[正常] $($dependency.Name) :$($dependency.Port)" -ForegroundColor Green
    } else {
        Write-Warning "$($dependency.Name) :$($dependency.Port) 不可连接；相关功能可能失败。"
    }
}

Start-ProjectService -Name "file_import_service" -Module "app.import_process.api.file_import_service" -Port 8000
Start-ProjectService -Name "query_service" -Module "app.query_process.api.query_service" -Port 8001

Write-Host "`n导入页面： http://127.0.0.1:8000/import.html"
Write-Host "状态页面： http://127.0.0.1:8000/status.html"
Write-Host "查询页面： http://127.0.0.1:8001/chat.html"
Write-Host "服务日志： $logDirectory"

if (-not $NoBrowser) {
    Start-Process "http://127.0.0.1:8000/status.html"
}
