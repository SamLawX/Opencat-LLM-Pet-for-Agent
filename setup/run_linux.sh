#!/bin/bash
# OpenCat Linux Setup & Guardian Script
# One-click script to install dependencies and run the server.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
SERVICE_DIR="$(dirname "$SCRIPT_DIR")"
VENV_DIR="$SERVICE_DIR/.venv"
PYTHON_EXE="$VENV_DIR/bin/python"
BACKEND_DIR="$SERVICE_DIR/backend"
PORT=9999

echo -e "\e[36m--- OpenCat (Linux) ---\e[0m"

# 1. Environment Setup
if [ ! -d "$VENV_DIR" ]; then
    echo -e "\e[33mFirst run detected! Creating virtual environment...\e[0m"
    
    # Try apt install if venv module is missing (common on Ubuntu/Debian)
    if ! python3 -c "import venv" &> /dev/null; then
        echo -e "\e[31mMissing 'venv' module. Attempting to install 'python3-venv'. This may ask for sudo password:\e[0m"
        sudo apt update && sudo apt install -y python3-venv
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

# 2. System Service Registration
if command -v systemctl &> /dev/null; then
    if [ ! -f "/etc/systemd/system/opencat.service" ]; then
        echo -e "\e[33mSystemd detected. Registering OpenCat as a system service...\e[0m"
        
        # Kill any existing background processes before taking over
        pkill -f "$BACKEND_DIR/main.py" 2>/dev/null || true
        
        # Apply strict root-only permissions
        echo -e "\e[33mApplying strict root-only permissions to $SERVICE_DIR...\e[0m"
        sudo chown -R root:root "$SERVICE_DIR"
        sudo find "$SERVICE_DIR" -type d -exec chmod 755 {} +
        sudo find "$SERVICE_DIR" -type f -exec chmod 644 {} +
        sudo chmod +x "$SCRIPT_DIR/run_linux.sh"
        
        # Make python/pip executable in venv
        sudo find "$VENV_DIR/bin" -type f -exec chmod 755 {} +
        
        SERVICE_FILE="/tmp/opencat.service"
        cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=OpenCat Backend Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$BACKEND_DIR
ExecStart=$PYTHON_EXE $BACKEND_DIR/main.py --host 0.0.0.0 --port $PORT
Restart=always
RestartSec=5
StandardOutput=append:$BACKEND_DIR/service.log
StandardError=append:$BACKEND_DIR/service.log

[Install]
WantedBy=multi-user.target
EOF

        echo -e "\e[33mThis requires sudo privileges to install to /etc/systemd/system/...\e[0m"
        sudo cp "$SERVICE_FILE" /etc/systemd/system/opencat.service
        rm "$SERVICE_FILE" 2>/dev/null || true
        sudo systemctl daemon-reload
        sudo systemctl enable opencat.service
        sudo systemctl restart opencat.service
        
        echo -e "\e[32mOpenCat registered and started as a system service!\e[0m"
        echo "To check status: systemctl status opencat"
        echo "To stop: sudo systemctl stop opencat"
        echo "To view logs: tail -f $BACKEND_DIR/service.log"
        exit 0
    else
        echo -e "\e[32mOpenCat system service is already registered.\e[0m"
        echo -e "\e[33mRestarting the service to ensure the latest code is running...\e[0m"
        sudo systemctl restart opencat.service
        echo "To check status: systemctl status opencat"
        echo "To view logs: tail -f $BACKEND_DIR/service.log"
        exit 0
    fi
else
    # 3. Guardian Loop (Fallback for non-systemd instances like WSL1/Docker)
    echo "Starting Backend Guardian on Port $PORT..."
    echo "Press Ctrl+C to exit. The backend runs in the background and restarts automatically if it fails."

    while true; do
        # ss -tuln checks for listening ports on Linux
        if ! ss -tuln | grep -q ":$PORT "; then
            echo -e "\e[33m$(date): Backend not running. Starting...\e[0m"
            nohup "$PYTHON_EXE" "$BACKEND_DIR/main.py" --host 0.0.0.0 --port "$PORT" >> "$BACKEND_DIR/service.log" 2>&1 &
        fi
        sleep 30
    done
fi
