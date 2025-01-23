# remote-fancontrol

Fancontrol script modified to get its values from a daemon on a remote VM, useful when using system fan headers to control fans of GPUs that are PCIE Passthroughed.

## Overview

A client-server application that allows controlling GPU fans when the GPU is passed through to a VM. The client runs in the VM to monitor temperatures, while the server runs on the host to control the fans.

## Features

- Remote temperature monitoring from VM
- PWM fan control from host system
- Multi-GPU support with individual fan control
- Automatic GPU detection
- Configurable temperature/fan speed curves
- Hysteresis support to prevent rapid fan changes
- Simple client-server architecture

## Requirements

- Python 3.7+
- AMD GPU with hwmon support
- Root/sudo access on host system

## Installation

### Basic Installation
```bash
# Clone the repository
git clone https://github.com/yourusername/remote-fancontrol.git
cd remote-fancontrol

# Install in editable mode (for development)
pip install -e .

# Or install with development dependencies
pip install -e ".[dev]"

# Or install directly from the requirements file
pip install -r requirements.txt
```

### System-wide Installation
For system-wide installation with systemd services:
```bash
# Install the server (on the host machine)
sudo ./install.sh --install-server

# Install the client (on the VM)
sudo ./install.sh --install-client
```

After installation, edit the service configuration in:
- Server: `/etc/systemd/system/remote-fancontrol-server.service`
- Client: `/etc/systemd/system/remote-fancontrol-client.service`

Then enable and start the service:
```bash
# For server (on host)
sudo systemctl enable remote-fancontrol-server
sudo systemctl start remote-fancontrol-server

# For client (on VM)
sudo systemctl enable remote-fancontrol-client
sudo systemctl start remote-fancontrol-client
```

Note: The server and client should be installed on different machines - the server on the host controlling the fans, and the client on the VM monitoring temperatures.

## Usage

### Host System (Server)

There are two ways to configure the fan controls:

1. Using a configuration file:
```json
# /etc/remote-fancontrol/fancontrol-server.json
{
    "temps": [35000, 55000, 80000, 90000],
    "pwms": [0, 100, 153, 255],
    "hysteresis": 6000,
    "sleep_interval": 1.0,
    "port": 7777,
    "host": "0.0.0.0",
    "failsafe_fan_percent": 80,
    "initial_fan_percent": 0,
    "fans": {
        "gpu0": {
            "pwm_path": "/sys/class/hwmon/hwmon3/pwm4",
            "mode_path": "/sys/class/hwmon/hwmon3/pwm4_enable"
        }
    }
}
```

Then run:
```bash
python -m remote_fancontrol.server.fan_controller
# or
remote-fancontrol-server
```

2. Using command line arguments:
```bash
# Multi-GPU configuration
sudo python -m remote_fancontrol.server.fan_controller \
    --fan-config gpu0 /sys/class/hwmon/hwmon3/pwm4 /sys/class/hwmon/hwmon3/pwm4_enable \
    --fan-config gpu1 /sys/class/hwmon/hwmon4/pwm4 /sys/class/hwmon/hwmon4/pwm4_enable \
    --host 0.0.0.0 \
    --port 7777

# Legacy single-GPU configuration
sudo python -m remote_fancontrol.server.fan_controller \
    --pwm-path /sys/class/hwmon/hwmon3/pwm4 \
    --mode-path /sys/class/hwmon/hwmon3/pwm4_enable \
    --host 0.0.0.0 \
    --port 7777
```

Server arguments:
- `--fan-config`: GPU ID and its PWM/mode paths (can be specified multiple times)
- `--pwm-path`: Legacy: Path to PWM control file
- `--mode-path`: Legacy: Path to PWM mode control file
- `--host`: Host address to listen on (default: 0.0.0.0, use specific IP or interface to restrict access)
- `--port`: Port to listen on (default: 7777)
- `--debug`: Enable debug logging
- `--failsafe-speed`: Failsafe fan speed percentage (0-100)
- `--initial-speed`: Initial fan speed percentage (0-100)

Note: The default host 0.0.0.0 allows connections from any interface. To restrict access, specify a particular interface IP (e.g., 192.168.1.100).

### Virtual Machine (Client)

Run the client:
```bash
python -m remote_fancontrol.client.temperature_monitor
# or
remote-fancontrol-client
```

Or specify GPU temperature sensors:

```
python -m amdgpu-fancontrol.client.temperature_monitor \
    --host <host-ip> \
    --port 7777 \
    --gpu-paths /sys/class/hwmon/hwmon1/temp1_input /sys/class/hwmon/hwmon2/temp1_input \
    --interval 0.5
```

Client arguments:
- `--gpu-paths`: Paths to temperature sensors (optional)
- `--host`: Server address
- `--port`: Server port
- `--interval`: Update interval in seconds
- `--debug`: Enable debug logging

## Configuration

The server and client use JSON configuration files located in `/etc/remote-fancontrol/`:

Server configuration (`/etc/remote-fancontrol/fancontrol-server.json`):
```json
{
    "temps": [35000, 55000, 80000, 90000],
    "pwms": [0, 100, 153, 255],
    "hysteresis": 6000,
    "sleep_interval": 1.0,
    "port": 7777,
    "host": "0.0.0.0",
    "failsafe_fan_percent": 80,
    "initial_fan_percent": 0
}
```

Client configuration (`/etc/remote-fancontrol/fancontrol-client.json`):
```json
{
    "sleep_interval": 1.0,
    "port": 7777,
    "host": "192.168.70.31"
}
```

After installation, edit these files to match your system configuration. The server configuration must be updated with your GPU fan paths, and the client configuration must be updated with your server's IP address.

## Systemd Service

Create `/etc/systemd/system/remote-fancontrol.service`:

```
[Unit]
Description=Remote Fan Control Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python -m amdgpu-fancontrol.server.fan_controller \
    --host 0.0.0.0 \
    --port 7777
Restart=always
User=root

[Install]
WantedBy=multi-user.target
```

Install and start the service:

```
sudo ./install-service.sh install
sudo systemctl start remote-fancontrol
```

## Troubleshooting

- **Permission errors**: Run server with sudo/root
- **Connection issues**: Check firewall settings and host IP
- **No temperature data**: Verify GPU passthrough and hwmon paths
- **GPU not detected**: Use --gpu-paths or --fan-config to specify paths manually
- **Multiple GPUs**: Make sure GPU IDs match between client and server

## Safety Features

- Path validation on startup
- Automatic fan control reset on shutdown
- Per-GPU hysteresis to prevent rapid fan changes
- Smooth temperature/speed transitions
- Automatic fallback to maximum fan speed on connection loss
