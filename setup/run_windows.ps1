# OpenCat Windows Setup & Guardian Script
# One-click script to install dependencies and run the server.

$ScriptDir = $PSScriptRoot
$ServiceDir = Split-Path $ScriptDir -Parent
$VenvDir = Join-Path $ServiceDir ".venv"
$PythonExe = Join-Path $VenvDir "Scripts\python.exe"
$BackendDir = Join-Path $ServiceDir "backend"
$LogFile = Join-Path $BackendDir "service.log"
$Port = 9999

Write-Host "--- OpenCat (Windows) ---" -ForegroundColor Cyan

# 1. Environment Setup
if (-not (Test-Path $VenvDir)) {
    Write-Host "First run detected! Creating virtual environment..." -ForegroundColor Yellow
    python -m venv $VenvDir
    
    Write-Host "Installing dependencies..."
    & $PythonExe -m pip install --upgrade pip
    & $PythonExe -m pip install -r (Join-Path $BackendDir "requirements.txt")
    & $PythonExe -m pip install -r (Join-Path $ServiceDir "mcp_server\requirements.txt")
    Write-Host "Setup complete!" -ForegroundColor Green
} else {
    Write-Host "Virtual environment found."
}

# 2. Guardian Loop (Auto-restart on crash)
function Check-Port {
    param($Port)
    $connection = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    return $connection -ne $null
}

Write-Host "Starting Backend Guardian on Port $Port..."
Write-Host "Keep this window open, or minimize it. The server will restart automatically if it fails."

while ($true) {
    if (-not (Check-Port $Port)) {
        Write-Host "$(Get-Date): Backend not running. Starting..." -ForegroundColor Yellow
        # Start backend in a hidden window, logging output to service.log
        Start-Process -FilePath $PythonExe -ArgumentList "$BackendDir\main.py --host 0.0.0.0 --port $Port" -WorkingDirectory $BackendDir -WindowStyle Hidden
    }
    Start-Sleep -Seconds 30
}
