import asyncio
import json
import logging
import argparse
from pathlib import Path
from typing import Optional, Dict, Tuple
import glob
from datetime import datetime
from colorama import Fore, Style, init

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

        if fan_configs:
            # Use provided fan configurations
            for gpu_id, (pwm_path, mode_path) in fan_configs.items():
                pwm = Path(pwm_path)
                mode = Path(mode_path)
                if pwm.exists() and mode.exists():
                    fans[gpu_id] = {"pwm": pwm, "mode": mode}
                else:
                    logger.error(
                        f"Invalid paths for GPU {gpu_id}: {pwm_path}, {mode_path}"
                    )
        else:
            # Auto-detect fans and assign sequential IDs
            pattern = "/sys/class/hwmon/hwmon*/pwm?"
            gpu_count = 0
            for pwm_path in glob.glob(pattern):
                pwm = Path(pwm_path)
                mode = pwm.parent / f"{pwm.name}_enable"
                if mode.exists():
                    gpu_id = f"gpu{gpu_count}"
                    fans[gpu_id] = {"pwm": pwm, "mode": mode}
                    gpu_count += 1

        if not fans:
            raise ValueError("No valid fan control paths found")

        logger.info(f"Configured fans for GPUs: {list(fans.keys())}")
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

    def set_fan_mode(self, gpu_id: str, mode: int):
        """Set fan control mode for specified GPU"""
        if gpu_id not in self.fans:
            logger.error(f"Unknown GPU: {gpu_id}")
            return

        try:
            self.fans[gpu_id]["mode"].write_text(str(mode))
            logger.info(f"Set fan mode to {mode} for GPU {gpu_id}")
        except IOError as e:
            logger.error(f"Failed to set fan mode for GPU {gpu_id}: {e}")

    def set_pwm(self, gpu_id: str, pwm: int):
        """Set PWM value for specified GPU"""
        if gpu_id not in self.fans:
            logger.error(f"Unknown GPU: {gpu_id}")
            return

        try:
            self.fans[gpu_id]["pwm"].write_text(str(pwm))
            logger.info(f"Set PWM to {pwm} for GPU {gpu_id}")
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

                        for gpu_id, temp in temps.items():
                            if temp is None:
                                continue

                            # Force update on first temperature reading after connection
                            should_update = (
                                gpu_id not in self.temp_at_last_change
                                or temp > self.temp_at_last_change[gpu_id]
                                or temp + self.config.HYSTERESIS
                                <= self.temp_at_last_change[gpu_id]
                            )

                            logger.info(f"{Fore.CYAN}{gpu_id}: {temp/1000:.1f}°C")
                            if gpu_id in self.temp_at_last_change:
                                next_change_up = self.temp_at_last_change[gpu_id]
                                next_change_down = (
                                    self.temp_at_last_change[gpu_id]
                                    - self.config.HYSTERESIS
                                )
                                logger.info(
                                    f"{Fore.YELLOW}{gpu_id} Next change at: "
                                    f"↑{next_change_up/1000:.1f}°C "
                                    f"↓{next_change_down/1000:.1f}°C"
                                )

                            if should_update:
                                pwm = self.interpolate_pwm(temp)
                                self.set_pwm(gpu_id, pwm)
                                self.temp_at_last_change[gpu_id] = temp
                                logger.info(
                                    f"{Fore.GREEN}{gpu_id}: "
                                    f"Updated fan speed: {pwm/255*100:.1f}%"
                                )
                            else:
                                current_pwm = int(
                                    self.fans[gpu_id]["pwm"].read_text().strip()
                                )
                                logger.info(
                                    f"{Fore.BLUE}{gpu_id}: "
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
        "--host", type=str, default="localhost", help="Host address to listen on"
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

    config = FanControlConfig()
    if args.host:
        config.HOST = args.host
    if args.port:
        config.PORT = args.port

    # Handle both new and legacy arguments
    fan_configs = None
    if args.fan_config:
        fan_configs = {
            gpu_id: (pwm_path, mode_path)
            for gpu_id, pwm_path, mode_path in args.fan_config
        }
    elif args.pwm_path and args.mode_path:
        # Legacy single-GPU support
        fan_configs = {"gpu0": (args.pwm_path, args.mode_path)}

    if args.failsafe_speed is not None:
        if 0 <= args.failsafe_speed <= 100:
            config.FAILSAFE_FAN_PERCENT = args.failsafe_speed
        else:
            logger.error("Failsafe speed must be between 0 and 100")
            return

    if args.initial_speed is not None:
        if 0 <= args.initial_speed <= 100:
            config.INITIAL_FAN_PERCENT = args.initial_speed
        else:
            logger.error("Initial speed must be between 0 and 100")
            return

    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.info(f"{Fore.GREEN}Starting fan control server with configuration:")
        logger.info(
            f"{Fore.CYAN}Temperature thresholds: {[t/1000 for t in config.TEMPS]}°C"
        )
        logger.info(f"{Fore.CYAN}PWM values: {config.PWMS}")
        logger.info(f"{Fore.CYAN}Hysteresis: {config.HYSTERESIS/1000}°C")
        logger.info(f"{Fore.CYAN}Update interval: {config.SLEEP_INTERVAL}s")
        logger.info(f"{Fore.CYAN}Failsafe fan speed: {config.FAILSAFE_FAN_PERCENT}%")

        controller = FanController(config, fan_configs)

        server = await asyncio.start_server(
            controller.handle_client, config.HOST, config.PORT
        )

        logger.info(f"{Fore.GREEN}Server running on {config.HOST}:{config.PORT}")

        async with server:
            await server.serve_forever()

    except KeyboardInterrupt:
        logger.info(f"{Fore.YELLOW}[{timestamp}] Shutting down...")
    except Exception as e:
        logger.error(f"{Fore.RED}[{timestamp}] Fatal error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
