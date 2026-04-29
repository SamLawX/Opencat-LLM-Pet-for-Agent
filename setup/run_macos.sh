#!/bin/bash
# OpenCat macOS Setup & Guardian Script
# One-click script to install dependencies and run the server.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
SERVICE_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$SERVICE_DIR/.venv"
PYTHON_EXE="$VENV_DIR/bin/python"
BACKEND_DIR="$SERVICE_DIR/backend"
PORT=9999

echo -e "\e[36m--- OpenCat (macOS) ---\e[0m"

# 1. Environment Setup
if [ ! -d "$VENV_DIR" ]; then
    echo -e "\e[33mFirst run detected! Creating virtual environment...\e[0m"
    
    # Check if python3 exists
    if ! command -v python3 &> /dev/null; then
        echo -e "\e[31mError: Python 3 is not installed. Please install it using Homebrew (e.g. brew install python3).\e[0m"
        exit 1
    fi
    
    python3 -m venv "$VENV_DIR"
    
    echo "Installing dependencies..."
    "$PYTHON_EXE" -m pip install --upgrade pip
    "$PYTHON_EXE" -m pip install -r "$BACKEND_DIR/requirements.txt"
    "$PYTHON_EXE" -m pip install -r "$SERVICE_DIR/mcp_server/requirements.txt"
    echo -e "\e[32mSetup complete!\e[0m"
else
    echo "Virtual environment found."
fi

# 2. Guardian Loop (Auto-restart on crash)
echo "Starting Backend Guardian on Port $PORT..."
echo "Press Ctrl+C to exit. The backend runs in the background and restarts automatically if it fails."

while true; do
    # lsof checks for listening ports better on macOS
    if ! lsof -i :$PORT -P -n | grep -q LISTEN; then
        echo -e "\e[33m$(date): Backend not running. Starting...\e[0m"
        nohup "$PYTHON_EXE" "$BACKEND_DIR/main.py" --host 0.0.0.0 --port "$PORT" >> "$BACKEND_DIR/service.log" 2>&1 &
    fi
    sleep 30
done
