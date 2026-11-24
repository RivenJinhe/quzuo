param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8010
)

$ErrorActionPreference = 'Stop'
$backend = Join-Path $PSScriptRoot '后端'
$frontend = Join-Path $PSScriptRoot '前端'

Push-Location -LiteralPath $backend
try {
    $python = Join-Path $backend '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        Write-Host '正在创建后端虚拟环境……'
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    Write-Host '正在检查后端依赖……'
    & $python -m pip --disable-pip-version-check install --quiet -r requirements.txt
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    if (-not (Test-Path -LiteralPath 'web/dist/index.html' -PathType Leaf)) {
        Write-Host '正在构建前端页面……'
        pnpm.cmd --dir $frontend install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        pnpm.cmd --dir $frontend run build
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }

    if (-not (Test-Path -LiteralPath '.env')) {
        Copy-Item -LiteralPath '.env.example' -Destination '.env'
    }

    Write-Host "本地页面 http://127.0.0.1:$Port/app/"
    Write-Host "接口文档 http://127.0.0.1:$Port/docs"
    & $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
