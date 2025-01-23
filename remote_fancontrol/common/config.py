from dataclasses import dataclass, field
from typing import List, Dict, Any
import json
import os
from pathlib import Path


@dataclass
class FanControlConfig:
    # Temperature thresholds in mK
    TEMPS: List[int]
    # PWM values corresponding to temperature thresholds
    PWMS: List[int]
    # Hysteresis in mK
    HYSTERESIS: int
    # Update interval in seconds
    SLEEP_INTERVAL: float
    # Default port for client-server communication
    PORT: int
    # Failsafe fan speed percentage (0-100)
    FAILSAFE_FAN_PERCENT: int
    # Initial fan speed percentage (0-100)
    INITIAL_FAN_PERCENT: int
    # Default host for server
    HOST: str = "0.0.0.0"
    # Fan configuration mapping
    fans: Dict[str, Dict[str, str]] = field(default_factory=dict)

    @classmethod
    def load_config(cls, config_type: str = "server") -> "FanControlConfig":
        """Load configuration from JSON file

        Args:
            config_type: Either "server" or "client"

        Returns:
            FanControlConfig instance
        """
        # Search paths in order
        config_name = f"fancontrol-{config_type}.json"
        search_paths = [
            Path(f"/etc/remote-fancontrol/{config_name}"),  # System-wide
            Path.home() / ".config/remote-fancontrol" / config_name,  # User config
            Path.cwd() / config_name,  # Project directory
        ]

        # Default configs
        defaults = {
            "server": {
                "temps": [35000, 55000, 80000, 90000],
                "pwms": [0, 100, 153, 255],
                "hysteresis": 6000,
                "sleep_interval": 1.0,
                "port": 7777,
                "host": "0.0.0.0",
                "failsafe_fan_percent": 80,
                "initial_fan_percent": 0,
            },
            "client": {
                "sleep_interval": 1.0,
                "port": 7777,
                "host": "192.168.70.31",
                "temps": [],
                "pwms": [],
                "hysteresis": 0,
                "failsafe_fan_percent": 0,
                "initial_fan_percent": 0,
            },
        }

        # Find and load config file
        config_data = None
        for path in search_paths:
            if path.exists():
                try:
                    with open(path) as f:
                        config_data = json.load(f)
                    break
                except json.JSONDecodeError as e:
                    print(f"Error reading config from {path}: {e}")

        # Use defaults if no config file found
        if config_data is None:
            config_data = defaults[config_type]

        # Create config directories if they don't exist
        os.makedirs("/etc/remote-fancontrol", exist_ok=True)
        os.makedirs(Path.home() / ".config/remote-fancontrol", exist_ok=True)

        # Save default config if none exists
        if not any(p.exists() for p in search_paths):
            default_path = search_paths[0]  # Use system-wide path
            try:
                with open(default_path, "w") as f:
                    json.dump(defaults[config_type], f, indent=4)
            except PermissionError:
                # Try user config if system-wide fails
                default_path = search_paths[1]
                with open(default_path, "w") as f:
                    json.dump(defaults[config_type], f, indent=4)

        # Add fans to config data if present in file but not in defaults
        if config_data.get("fans") and "fans" not in defaults[config_type]:
            defaults[config_type]["fans"] = {}

        return cls(
            TEMPS=config_data.get("temps", defaults[config_type]["temps"]),
            PWMS=config_data.get("pwms", defaults[config_type]["pwms"]),
            HYSTERESIS=config_data.get(
                "hysteresis", defaults[config_type]["hysteresis"]
            ),
            SLEEP_INTERVAL=config_data.get(
                "sleep_interval", defaults[config_type]["sleep_interval"]
            ),
            PORT=config_data.get("port", defaults[config_type]["port"]),
            HOST=config_data.get("host", defaults[config_type]["host"]),
            FAILSAFE_FAN_PERCENT=config_data.get(
                "failsafe_fan_percent", defaults[config_type]["failsafe_fan_percent"]
            ),
            INITIAL_FAN_PERCENT=config_data.get(
                "initial_fan_percent", defaults[config_type]["initial_fan_percent"]
            ),
            fans=config_data.get("fans", defaults[config_type].get("fans", {})),
        )

    def __post_init__(self):
        if len(self.TEMPS) != len(self.PWMS):
            raise ValueError("Temperature and PWM arrays must have the same length")
        if not 0 <= self.FAILSAFE_FAN_PERCENT <= 100:
            raise ValueError("Failsafe fan percentage must be between 0 and 100")
        if not 0 <= self.INITIAL_FAN_PERCENT <= 100:
            raise ValueError("Initial fan percentage must be between 0 and 100")
