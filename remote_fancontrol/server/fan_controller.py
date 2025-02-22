import asyncio
import json
import logging
import argparse
from pathlib import Path
from typing import Optional, Dict, Tuple
import glob
from datetime import datetime
from colorama import Fore, Style, init
import re

from ..common.config import FanControlConfig

init(autoreset=True)  # Initialize colorama


class ColoredFormatter(logging.Formatter):
    FORMATS = {
        logging.DEBUG: Style.RESET_ALL
        + "["
        + Fore.YELLOW
        + "%(asctime)s"
        + Style.RESET_ALL
        + "]: "
        + Fore.CYAN
        + "%(levelname)s: %(message)s"
        + Style.RESET_ALL,
        logging.INFO: Style.RESET_ALL
        + "["
        + Fore.YELLOW
        + "%(asctime)s"
        + Style.RESET_ALL
        + "]: "
        + Fore.GREEN
        + "%(levelname)s: %(message)s"
        + Style.RESET_ALL,
        logging.WARNING: Style.RESET_ALL
        + "["
        + Fore.YELLOW
        + "%(asctime)s"
        + Style.RESET_ALL
        + "]: "
        + Fore.YELLOW
        + "%(levelname)s: %(message)s"
        + Style.RESET_ALL,
        logging.ERROR: Style.RESET_ALL
        + "["
        + Fore.YELLOW
        + "%(asctime)s"
        + Style.RESET_ALL
        + "]: "
        + Fore.RED
        + "%(levelname)s: %(message)s"
        + Style.RESET_ALL,
        logging.CRITICAL: Style.RESET_ALL
        + "["
        + Fore.YELLOW
        + "%(asctime)s"
        + Style.RESET_ALL
        + "]: "
        + Fore.RED
        + Style.BRIGHT
        + "%(levelname)s: %(message)s"
        + Style.RESET_ALL,
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)


logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",  # Simple format since we handle formatting in ColoredFormatter
    handlers=[logging.NullHandler()],  # Prevent double logging
)
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(ColoredFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)


class FanController:
    def __init__(
        self,
        config: FanControlConfig,
        fan_configs: Optional[Dict[str, Tuple[str, str]]] = None,
    ):
        """
        Args:
            config: FanControlConfig instance
            fan_configs: Dict mapping GPU IDs to (pwm_path, mode_path) tuples
        """
        self.config = config
        self.fans = self._setup_fans(fan_configs)
        self.last_temps = {}
        self.temp_at_last_change = {}

        # Set initial fan speeds
        for gpu_id in self.fans:
            self.set_fan_mode(gpu_id, 1)  # Set to manual mode
            self.set_initial_speed(gpu_id)  # Set initial speed

    def _setup_fans(
        self, fan_configs: Optional[Dict[str, Tuple[str, str]]] = None
    ) -> Dict[str, Dict[str, Path]]:
        """Setup fan control paths for each GPU

        Returns:
            Dict mapping GPU IDs to their control paths
        """
        fans = {}

        # First try config file
        config_fans = getattr(self.config, "fans", None)
        if config_fans:
            for fan_id, paths in config_fans.items():
                # Get the reference GPU for this fan (defaults to fan_id if not specified)
                ref_gpu = paths.get("reference_gpu", fan_id)
                fan_configured = False

                # Check for hwmon_name in config
                if "hwmon_name" in paths:
                    hwmon_path = self._find_hwmon_by_name(paths["hwmon_name"])
                    if hwmon_path:
                        pwm = hwmon_path / paths.get("pwm_file", "pwm1")
                        mode = hwmon_path / paths.get("mode_file", "pwm1_enable")
                        if pwm.exists() and mode.exists():
                            fans[fan_id] = {
                                "pwm": pwm,
                                "mode": mode,
                                "reference_gpu": ref_gpu,
                            }
                            logger.info(
                                f"Configured fan {fan_id} using hwmon {paths['hwmon_name']}"
                            )
                            fan_configured = True
                        else:
                            logger.error(
                                f"Invalid paths for fan {fan_id} in hwmon {hwmon_path}: {pwm}, {mode}"
                            )
                    else:
                        logger.error(
                            f"No hwmon device found matching: {paths['hwmon_name']}"
                        )

                # Fall back to direct paths if specified and hwmon config failed
                if not fan_configured and "pwm_path" in paths and "mode_path" in paths:
                    pwm = Path(paths["pwm_path"])
                    mode = Path(paths["mode_path"])
                    if pwm.exists() and mode.exists():
                        fans[fan_id] = {
                            "pwm": pwm,
                            "mode": mode,
                            "reference_gpu": ref_gpu,
                        }
                        logger.info(f"Configured fan {fan_id} using direct paths")
                    else:
                        logger.error(
                            f"Invalid paths for fan {fan_id}: {paths['pwm_path']}, {paths['mode_path']}"
                        )
                elif not fan_configured:
                    logger.error(
                        f"Fan {fan_id} configuration must specify either hwmon_name or pwm_path/mode_path"
                    )

        # Then try command line arguments if no fans configured yet
        if not fans and fan_configs:
            for gpu_id, (pwm_path, mode_path) in fan_configs.items():
                pwm = Path(pwm_path)
                mode = Path(mode_path)
                if pwm.exists() and mode.exists():
                    fans[gpu_id] = {"pwm": pwm, "mode": mode, "reference_gpu": gpu_id}
                    logger.info(f"Configured fan {gpu_id} using command line paths")
                else:
                    logger.error(
                        f"Invalid paths for GPU {gpu_id}: {pwm_path}, {mode_path}"
                    )

        # Auto-detect only if no fans configured
        if not fans:
            pattern = "/sys/class/hwmon/hwmon*/pwm?"
            gpu_count = 0
            for pwm_path in glob.glob(pattern):
                pwm = Path(pwm_path)
                mode = pwm.parent / f"{pwm.name}_enable"
                if mode.exists():
                    gpu_id = f"gpu{gpu_count}"
                    fans[gpu_id] = {"pwm": pwm, "mode": mode, "reference_gpu": gpu_id}
                    gpu_count += 1
                    logger.info(f"Auto-detected fan {gpu_id}")
            if gpu_count > 0:
                logger.info("Using auto-detected fan configuration")

        if not fans:
            raise ValueError("No valid fan control paths found")

        logger.info(f"Configured fans: {list(fans.keys())}")
        return fans

    @staticmethod
    def _get_gpu_id(hwmon_path: Path) -> Optional[str]:
        """Get GPU identifier from hwmon path"""
        try:
            name_file = hwmon_path / "name"
            if name_file.exists():
                name = name_file.read_text().strip()
                if "amdgpu" in name.lower():
                    device_path = hwmon_path / "device"
                    if device_path.exists() and device_path.is_symlink():
                        pci_id = device_path.resolve().name
                        return f"amdgpu-{pci_id}"
                    return name
            return None
        except (IOError, OSError) as e:
            logger.debug(f"Error reading GPU name: {e}")
            return None

    def _find_hwmon_by_name(self, pattern: str) -> Optional[Path]:
        """Find hwmon device path by name pattern

        Args:
            pattern: Regex pattern to match against hwmon name

        Returns:
            Path to matching hwmon device or None if not found
        """
        # Convert pattern to regex if it's not already
        if not pattern.startswith("^") and not pattern.endswith("$"):
            pattern = f".*{pattern}.*"

        try:
            for hwmon in Path("/sys/class/hwmon").glob("hwmon*"):
                name_file = hwmon / "name"
                if name_file.exists():
                    name = name_file.read_text().strip()
                    if re.match(pattern, name, re.IGNORECASE):
                        logger.debug(f"Found matching hwmon device: {name} at {hwmon}")
                        return hwmon
            logger.debug(f"No hwmon device found matching pattern: {pattern}")
            return None
        except (IOError, OSError) as e:
            logger.error(f"Error searching for hwmon device: {e}")
            return None

    def set_fan_mode(self, gpu_id: str, mode: int):
        """Set fan control mode for specified GPU"""
        if gpu_id not in self.fans:
            logger.error(f"Unknown GPU: {gpu_id}")
            return

        try:
            self.fans[gpu_id]["mode"].write_text(str(mode))
            logger.debug(f"Set fan mode to {mode} for GPU {gpu_id}")
        except IOError as e:
            logger.error(f"Failed to set fan mode for GPU {gpu_id}: {e}")

    def set_pwm(self, gpu_id: str, pwm: int):
        """Set PWM value for specified GPU"""
        if gpu_id not in self.fans:
            logger.error(f"Unknown GPU: {gpu_id}")
            return

        try:
            self.fans[gpu_id]["pwm"].write_text(str(pwm))
            logger.debug(f"Set PWM to {pwm} for GPU {gpu_id}")
        except IOError as e:
            logger.error(f"Failed to set PWM for GPU {gpu_id}: {e}")

    def interpolate_pwm(self, temp: int) -> int:
        """Calculate PWM value based on temperature"""
        logger.debug(f"Interpolating PWM for temperature {temp/1000:.1f}°C")

        if temp <= self.config.TEMPS[0]:
            logger.debug(
                f"Temperature below minimum, using minimum PWM: {self.config.PWMS[0]}"
            )
            return self.config.PWMS[0]
        elif temp >= self.config.TEMPS[-1]:
            logger.debug(
                f"Temperature above maximum, using maximum PWM: {self.config.PWMS[-1]}"
            )
            return self.config.PWMS[-1]

        for i in range(1, len(self.config.TEMPS)):
            if temp <= self.config.TEMPS[i]:
                temp_range = self.config.TEMPS[i] - self.config.TEMPS[i - 1]
                pwm_range = self.config.PWMS[i] - self.config.PWMS[i - 1]
                temp_delta = temp - self.config.TEMPS[i - 1]

                pwm = self.config.PWMS[i - 1] + (temp_delta * pwm_range // temp_range)
                logger.debug(
                    f"Interpolated between {self.config.TEMPS[i-1]/1000:.1f}°C "
                    f"and {self.config.TEMPS[i]/1000:.1f}°C: {pwm}"
                )
                return pwm

        return self.config.PWMS[-1]

    def set_failsafe_speed(self, gpu_id: str):
        """Set failsafe fan speed for specified GPU"""
        if gpu_id not in self.fans:
            logger.error(f"Unknown GPU: {gpu_id}")
            return

        try:
            # Convert percentage to PWM value (0-255)
            pwm = int(self.config.FAILSAFE_FAN_PERCENT * 255 / 100)
            self.fans[gpu_id]["pwm"].write_text(str(pwm))
            logger.warning(
                f"{Fore.YELLOW}[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                f"{gpu_id}: Set to failsafe speed: {self.config.FAILSAFE_FAN_PERCENT}%"
            )
        except IOError as e:
            logger.error(f"Failed to set failsafe speed for GPU {gpu_id}: {e}")

    def set_initial_speed(self, gpu_id: str):
        """Set initial fan speed for specified GPU"""
        if gpu_id not in self.fans:
            logger.error(f"Unknown GPU: {gpu_id}")
            return

        try:
            # Convert percentage to PWM value (0-255)
            pwm = int(self.config.INITIAL_FAN_PERCENT * 255 / 100)
            self.fans[gpu_id]["pwm"].write_text(str(pwm))
            logger.info(
                f"{Fore.CYAN}{gpu_id}: Set to initial speed: "
                f"{self.config.INITIAL_FAN_PERCENT}%"
            )
        except IOError as e:
            logger.error(f"Failed to set initial speed for GPU {gpu_id}: {e}")

    async def handle_client(self, reader, writer):
        """Handle incoming temperature data from client"""
        client_addr = writer.get_extra_info("peername")
        logger.debug(f"New client connection from {client_addr}")

        # Set all fans to manual mode and reset fan speeds
        for gpu_id in self.fans:
            self.set_fan_mode(gpu_id, 1)
            # Reset temperature history for fresh start
            if gpu_id in self.temp_at_last_change:
                del self.temp_at_last_change[gpu_id]

        try:
            while True:
                try:
                    data = await asyncio.wait_for(reader.readline(), timeout=5.0)
                    if not data:
                        break

                    try:
                        message = json.loads(data.decode())
                        temps = message["temperatures"]

                        # Track which GPUs have been processed to avoid duplicate updates
                        processed_gpus = set()

                        # Process each fan based on its reference GPU
                        for fan_id, fan_info in self.fans.items():
                            ref_gpu = fan_info["reference_gpu"]

                            # Skip if we don't have temperature data for the reference GPU
                            if ref_gpu not in temps or temps[ref_gpu] is None:
                                continue

                            temp = temps[ref_gpu]

                            # Force update on first temperature reading after connection
                            should_update = (
                                ref_gpu not in self.temp_at_last_change
                                or temp > self.temp_at_last_change[ref_gpu]
                                or temp + self.config.HYSTERESIS
                                <= self.temp_at_last_change[ref_gpu]
                            )

                            # Only show temperature updates in debug mode
                            if ref_gpu not in processed_gpus:
                                logger.debug(f"{Fore.CYAN}{ref_gpu}: {temp/1000:.1f}°C")
                                processed_gpus.add(ref_gpu)

                            if (
                                ref_gpu in self.temp_at_last_change
                                and ref_gpu not in processed_gpus
                            ):
                                next_change_up = self.temp_at_last_change[ref_gpu]
                                next_change_down = (
                                    self.temp_at_last_change[ref_gpu]
                                    - self.config.HYSTERESIS
                                )
                                logger.debug(
                                    f"{Fore.YELLOW}{ref_gpu} Next change at: "
                                    f"↑{next_change_up/1000:.1f}°C "
                                    f"↓{next_change_down/1000:.1f}°C"
                                )

                            if should_update:
                                pwm = self.interpolate_pwm(temp)
                                self.set_pwm(fan_id, pwm)
                                self.temp_at_last_change[ref_gpu] = temp
                                logger.debug(
                                    f"{Fore.GREEN}Fan {fan_id} (ref: {ref_gpu}): "
                                    f"Updated fan speed: {pwm/255*100:.1f}%"
                                )
                            else:
                                current_pwm = int(
                                    self.fans[fan_id]["pwm"].read_text().strip()
                                )
                                logger.debug(
                                    f"{Fore.BLUE}Fan {fan_id} (ref: {ref_gpu}): "
                                    f"Current fan speed: {current_pwm/255*100:.1f}%"
                                )

                    except json.JSONDecodeError as e:
                        logger.error(
                            f"{Fore.RED}[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Invalid message format: {e}"
                        )

                except asyncio.TimeoutError:
                    # No data received within timeout, set failsafe speeds
                    logger.warning(
                        f"{Fore.YELLOW}Client timeout - setting failsafe speeds"
                    )
                    for gpu_id in self.fans:
                        self.set_failsafe_speed(gpu_id)
                    continue

        except asyncio.CancelledError:
            # Reset all fans to auto mode
            for gpu_id in self.fans:
                self.set_fan_mode(gpu_id, 2)
            writer.close()
            await writer.wait_closed()
            raise
        finally:
            # Set failsafe speeds before disconnecting
            for gpu_id in self.fans:
                self.set_failsafe_speed(gpu_id)

    async def set_failsafe_with_retry(self, gpu_id: str):
        """Set failsafe speed with retry until successful"""
        while True:
            try:
                # Convert percentage to PWM value (0-255)
                pwm = int(self.config.FAILSAFE_FAN_PERCENT * 255 / 100)
                self.fans[gpu_id]["pwm"].write_text(str(pwm))
                logger.warning(
                    f"{Fore.YELLOW}{gpu_id}: Set to failsafe speed: "
                    f"{self.config.FAILSAFE_FAN_PERCENT}%"
                )
                break
            except IOError as e:
                logger.error(f"Failed to set failsafe speed for GPU {gpu_id}: {e}")
                await asyncio.sleep(1)  # Wait before retry

    async def cleanup(self):
        """Clean up fan control on shutdown"""
        # First set all fans to automatic mode
        for gpu_id in self.fans:
            try:
                self.set_fan_mode(gpu_id, 2)  # 2 = automatic mode
            except IOError as e:
                logger.error(f"Failed to set automatic mode for GPU {gpu_id}: {e}")

        # Then try to set failsafe speeds
        tasks = []
        for gpu_id in self.fans:
            task = asyncio.create_task(self.set_failsafe_with_retry(gpu_id))
            tasks.append(task)

        # Wait for all failsafe settings to complete
        await asyncio.gather(*tasks)


def parse_args():
    parser = argparse.ArgumentParser(description="AMD GPU Fan Controller Server")
    parser.add_argument(
        "--fan-config",
        nargs=3,
        action="append",
        metavar=("GPU_ID", "PWM_PATH", "MODE_PATH"),
        help="Fan configuration for a GPU (can be specified multiple times)",
    )
    parser.add_argument(
        "--hwmon-config",
        nargs=4,
        action="append",
        metavar=("GPU_ID", "HWMON_NAME", "PWM_FILE", "MODE_FILE"),
        help="Fan configuration using hwmon name (can be specified multiple times)",
    )
    parser.add_argument(
        "--pwm-path",
        type=str,
        help="Legacy: Path to PWM control file (e.g., /sys/class/hwmon/hwmon5/pwm4)",
    )
    parser.add_argument(
        "--mode-path",
        type=str,
        help="Legacy: Path to PWM mode control file (e.g., /sys/class/hwmon/hwmon5/pwm4_enable)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Host address to listen on (default: 0.0.0.0, use specific IP or interface to restrict access)",
    )
    parser.add_argument("--port", type=int, default=7777, help="Port to listen on")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--failsafe-speed",
        type=int,
        help="Failsafe fan speed percentage (0-100) when client disconnects",
    )
    parser.add_argument(
        "--initial-speed",
        type=int,
        help="Initial fan speed percentage (0-100) before client connects",
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    config = FanControlConfig.load_config("server")
    logger.info(f"{Fore.GREEN}Loading configuration...")

    # Log config source
    config_path = None
    for path in [
        Path("/etc/remote-fancontrol/fancontrol-server.json"),
        Path.home() / ".config/remote-fancontrol/fancontrol-server.json",
        Path.cwd() / "fancontrol-server.json",
    ]:
        if path.exists():
            config_path = path
            break

    if config_path:
        logger.info(f"{Fore.CYAN}Using config file: {config_path}")
    else:
        logger.info(f"{Fore.YELLOW}Using default configuration")

    # Override with command line arguments
    if args.host:
        logger.info(f"{Fore.YELLOW}Overriding host from command line: {args.host}")
        config.HOST = args.host
    if args.port:
        logger.info(f"{Fore.YELLOW}Overriding port from command line: {args.port}")
        config.PORT = args.port

    # Handle hwmon configuration
    if args.hwmon_config:
        if not hasattr(config, "fans"):
            config.fans = {}
        for gpu_id, hwmon_name, pwm_file, mode_file in args.hwmon_config:
            config.fans[gpu_id] = {
                "hwmon_name": hwmon_name,
                "pwm_file": pwm_file,
                "mode_file": mode_file,
            }
            logger.info(
                f"{Fore.YELLOW}Using hwmon configuration for {gpu_id}:"
                f" {hwmon_name} ({pwm_file}, {mode_file})"
            )

    # Handle both new and legacy arguments
    fan_configs = None
    if args.fan_config:
        fan_configs = {
            gpu_id: (pwm_path, mode_path)
            for gpu_id, pwm_path, mode_path in args.fan_config
        }
        logger.info(f"{Fore.YELLOW}Using fan configuration from command line:")
        for gpu_id, (pwm, mode) in fan_configs.items():
            logger.info(f"{Fore.CYAN}  {gpu_id}: PWM={pwm}, MODE={mode}")
    elif args.pwm_path and args.mode_path:
        # Legacy single-GPU support
        fan_configs = {"gpu0": (args.pwm_path, args.mode_path)}
        logger.info(
            f"{Fore.YELLOW}Using legacy fan configuration: "
            f"PWM={args.pwm_path}, MODE={args.mode_path}"
        )

    if args.failsafe_speed is not None:
        if 0 <= args.failsafe_speed <= 100:
            logger.info(
                f"{Fore.YELLOW}Overriding failsafe speed from command line: "
                f"{args.failsafe_speed}%"
            )
            config.FAILSAFE_FAN_PERCENT = args.failsafe_speed
        else:
            logger.error("Failsafe speed must be between 0 and 100")
            return

    if args.initial_speed is not None:
        if 0 <= args.initial_speed <= 100:
            logger.info(
                f"{Fore.YELLOW}Overriding initial speed from command line: "
                f"{args.initial_speed}%"
            )
            config.INITIAL_FAN_PERCENT = args.initial_speed
        else:
            logger.error("Initial speed must be between 0 and 100")
            return

    try:
        logger.info(f"\n{Fore.GREEN}Server configuration:")
        logger.info(
            f"{Fore.CYAN}Temperature thresholds: {[t/1000 for t in config.TEMPS]}°C"
        )
        logger.info(f"{Fore.CYAN}PWM values: {config.PWMS}")
        logger.info(f"{Fore.CYAN}Hysteresis: {config.HYSTERESIS/1000}°C")
        logger.info(f"{Fore.CYAN}Update interval: {config.SLEEP_INTERVAL}s")
        logger.info(f"{Fore.CYAN}Network: {config.HOST}:{config.PORT}")
        logger.info(f"{Fore.CYAN}Failsafe fan speed: {config.FAILSAFE_FAN_PERCENT}%")
        logger.info(f"{Fore.CYAN}Initial fan speed: {config.INITIAL_FAN_PERCENT}%")

        controller = FanController(config, fan_configs)

        server = await asyncio.start_server(
            controller.handle_client, config.HOST, config.PORT
        )

        logger.info(f"\n{Fore.GREEN}Server running on {config.HOST}:{config.PORT}")

        async with server:
            await server.serve_forever()

    except KeyboardInterrupt:
        logger.info(f"{Fore.YELLOW}Shutting down...")
    except Exception as e:
        logger.error(f"{Fore.RED}Fatal error: {e}")
        raise
    finally:
        if "controller" in locals():
            await controller.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
