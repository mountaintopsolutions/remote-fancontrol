## Overview

A client-server application that allows controlling GPU fans when the GPU(s) are passed through to a VM. The client runs in the VM that the GPU is passed through to, to monitor temperatures, while the server runs on the host to control the fans via motherboard PWM headers.

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

### System-wide Installation

For system-wide installation with systemd services:

```bash
# Install the server (on the host machine)
sudo ./install.sh --install-server

# Install the client (on the VM)
sudo ./install.sh --install-client
```

### Development Installation

```bash
# Clone the repository
git clone https://github.com/mountaintopsolutions/remote-fancontrol.git
cd remote-fancontrol

# Install in editable mode (for development)
pip install -e .

# Or install with development dependencies
pip install -e ".[dev]"

# Or install directly from the requirements file
pip install -r requirements.txt
```

Note: You still need to enable / start the systemd services manually after you have configured the files in /etc/remote-fancontrol/ for your system.

Then enable and start the service:
```bash
# For server (on host)
sudo systemctl enable remote-fancontrol-server
sudo systemctl start remote-fancontrol-server

# For client (on VM)
sudo systemctl enable remote-fancontrol-client
sudo systemctl start remote-fancontrol-client
```

Note: The server and client should be installed on different machines - the server on the host controlling the fans, and the client on the VM monitoring device temperatures.

## Configuration files & Manual Usage

### Host System (Server)

There are two ways to configure the fan controls:

1. See below to configure the server config file, then run:
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

As with the server, there are two ways to configure the client:

1. See below to configure the client config file, then run:
```bash
python -m remote_fancontrol.client
# or
remote-fancontrol-client
```

2. Using command line arguments to specify GPU temperature sensors:

```
python -m remote_fancontrol.client \
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
    "host": "192.168.70.31",
    "gpus": {
        "gpu0": {
            "temp_path": "/sys/class/hwmon/hwmon1/temp1_input"
        },
        "gpu1": {
            "temp_path": "/sys/class/hwmon/hwmon2/temp1_input"
        }
    }
}
```

After installation, edit these files to match your system configuration:
- Server: Update fan control paths in `fancontrol-server.json`
- Client: Update server IP and GPU temperature sensor paths in `fancontrol-client.json`

- Copy them to /etc/remote-fancontrol/ on the server and the client.

Restart the service:
Server: 
```
sudo systemctl restart remote-fancontrol-server
```

Client:
```
sudo systemctl restart remote-fancontrol-client
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
- Automatic fallback to predefined fan speed on connection loss (default: 80% of maximum fan speed)
- Non-intrusive startup by default, 0% fan speed until the client connects to the server (configurable).
