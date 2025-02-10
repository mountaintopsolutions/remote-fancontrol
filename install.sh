#!/bin/bash

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_SERVICE="remote-fancontrol-server.service"
CLIENT_SERVICE="remote-fancontrol-client.service"

function print_usage() {
    echo "Usage: $0 [--install-server|--install-client] [--help]"
    echo ""
    echo "Options:"
    echo "  --install-server  Install the fan control server"
    echo "  --install-client  Install the fan control client"
    echo "  --help           Show this help message"
    echo ""
    echo "Note: Specify either --install-server or --install-client."
    echo "      Running both on the same machine instance is not recommended, the client should be run on a VM or remote machine."
}

function install_service() {
    local service_file="$1"
    local service_name=$(basename "$service_file" .service)
    
    echo "Installing ${service_name} service..."
    
    # Copy service file
    cp "${SCRIPT_DIR}/${service_file}" "/etc/systemd/system/"
    
    # Reload systemd
    systemctl daemon-reload
    
    echo "Service installed. To enable and start:"
    echo "systemctl enable ${service_name}"
    echo "systemctl start ${service_name}"
}

# Parse command line arguments
INSTALL_SERVER=0
INSTALL_CLIENT=0

if [ $# -eq 0 ]; then
    # Default: install neither
    print_usage
    exit 1
fi

while [ "$1" != "" ]; do
    case $1 in
        --install-server )  INSTALL_SERVER=1
                          ;;
        --install-client )  INSTALL_CLIENT=1
                          ;;
        --help )           print_usage
                          exit
                          ;;
        * )               echo "Unknown option: $1"
                         print_usage
                         exit 1
    esac
    shift
done

# Warn if both are selected
if [ $INSTALL_SERVER -eq 1 ] && [ $INSTALL_CLIENT -eq 1 ]; then
    echo "Warning: Installing both server and client on the same machine is not recommended."
    echo "Press Ctrl+C to cancel or wait 5 seconds to continue..."
    sleep 5
fi

## TODO: Add ability to install in either global or virtualenv
# Install system-wide Python package and dependencies
#echo "Installing Python package system-wide..."
#python3 -m pip install --break-system-packages -r requirements.txt
#python3 -m pip install --break-system-packages .

# Install services based on options
if [ $INSTALL_SERVER -eq 1 ]; then
    if [ -f "${SCRIPT_DIR}/${SERVER_SERVICE}" ]; then
        install_service "${SERVER_SERVICE}"
    else
        echo "Warning: Server service file not found"
    fi
fi

if [ $INSTALL_CLIENT -eq 1 ]; then
    if [ -f "${SCRIPT_DIR}/${CLIENT_SERVICE}" ]; then
        install_service "${CLIENT_SERVICE}"
    else
        echo "Warning: Client service file not found"
    fi
fi

# Install package files and setup virtualenv
function setup_environment() {
    local install_type=$1
    
    echo "Setting up ${install_type} environment..."
    mkdir -p /opt/remote-fancontrol
    
    # Copy files, excluding certain paths
    rsync -av --exclude '.git' \
              --exclude '.venv' \
              --exclude '*.egg-info' \
              --exclude '__pycache__' \
              "${SCRIPT_DIR}/" /opt/remote-fancontrol/
    
    # Create and setup virtualenv
    if [ ! -d "/opt/remote-fancontrol/venv" ]; then
        echo "Creating new virtualenv..."
        python3 -m venv /opt/remote-fancontrol/venv
    fi
    
    echo "Installing dependencies..."
    /opt/remote-fancontrol/venv/bin/pip install --upgrade pip
    /opt/remote-fancontrol/venv/bin/pip install -e /opt/remote-fancontrol
    
    # Set permissions
    chown -R root:root /opt/remote-fancontrol
}

# Install config files
if [ $INSTALL_SERVER -eq 1 ]; then
    mkdir -p /etc/remote-fancontrol
    if [ ! -f "/etc/remote-fancontrol/fancontrol-server.json" ]; then
        echo "Installing default server configuration to /etc/remote-fancontrol/fancontrol-server.json"
        echo "Please edit this file to match your system configuration."
        cp "${SCRIPT_DIR}/fancontrol-server.json" "/etc/remote-fancontrol/"
    else
        echo "Server configuration already exists at /etc/remote-fancontrol/fancontrol-server.json"
        echo "Copying default server configuration to /etc/remote-fancontrol/fancontrol-server.json.new"
        echo "Please verify the configuration matches your system."
        cp "${SCRIPT_DIR}/fancontrol-server.json" "/etc/remote-fancontrol/fancontrol-server.json.new"
    fi
    setup_environment "server"
fi

if [ $INSTALL_CLIENT -eq 1 ]; then
    mkdir -p /etc/remote-fancontrol
    if [ ! -f "/etc/remote-fancontrol/fancontrol-client.json" ]; then
        echo "Installing default client configuration to /etc/remote-fancontrol/fancontrol-client.json"
        echo "Please edit this file to set the correct server host address."
        cp "${SCRIPT_DIR}/fancontrol-client.json" "/etc/remote-fancontrol/"
    else
        echo "Client configuration already exists at /etc/remote-fancontrol/fancontrol-client.json"
        echo "Copying default client configuration to /etc/remote-fancontrol/fancontrol-client.json.new"
        echo "Please verify the configuration matches your system."
        cp "${SCRIPT_DIR}/fancontrol-client.json" "/etc/remote-fancontrol/fancontrol-client.json.new"
    fi
    setup_environment "client"
fi

echo "Installation complete."
if [ $INSTALL_SERVER -eq 1 ]; then
    echo "Server commands available: remote-fancontrol-server --help"
fi
if [ $INSTALL_CLIENT -eq 1 ]; then
    echo "Client commands available: remote-fancontrol-client --help"
fi 