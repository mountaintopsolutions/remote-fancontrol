import asyncio
import json
import glob
import logging
import argparse
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from colorama import Fore, Style, init

from ..common.config import FanControlConfig

init(autoreset=True)  # Initialize colorama


# Custom formatter for colored output
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


# Setup logging with colored output
logging.basicConfig(
    level=logging.INFO, format="%(message)s", handlers=[logging.NullHandler()]
)
logger = logging.getLogger(__name__)
handler = logging.StreamHandler()
handler.setFormatter(ColoredFormatter())
logger.addHandler(handler)
logger.setLevel(logging.INFO)


class TemperatureMonitor:
    def __init__(self, config: FanControlConfig, gpu_paths: Optional[List[str]] = None):
        self.config = config
        self.gpu_temps = {}  # Changed from list to dict

        # First try command line paths
        if gpu_paths:
            for i, path in enumerate(gpu_paths):
                if Path(path).exists():
                    self.gpu_temps[f"gpu{i}"] = Path(path)
                else:
                    logger.error(f"Temperature sensor not found: {path}")

        # Then try config file paths
        elif self.config.gpus:
            for gpu_id, gpu_config in self.config.gpus.items():
                path = Path(gpu_config["temp_path"])
                if path.exists():
                    self.gpu_temps[gpu_id] = path
                    logger.info(f"Using temperature sensor for {gpu_id}: {path}")
                else:
                    logger.error(f"Temperature sensor not found for {gpu_id}: {path}")

        # Finally try auto-detection
        if not self.gpu_temps:
            logger.info("No GPU paths configured, attempting auto-detection")
            pattern = "/sys/class/hwmon/hwmon*/temp1_input"
            for i, path in enumerate(glob.glob(pattern)):
                self.gpu_temps[f"gpu{i}"] = Path(path)

        if not self.gpu_temps:
            raise ValueError("No valid temperature sensor paths found")

        logger.info(f"Monitoring {len(self.gpu_temps)} temperature sensors")
        self.total_reconnects = 0

    def _is_gpu_temp(self, hwmon_path: Path) -> bool:
        """Check if hwmon path belongs to a GPU"""
        try:
            name_file = hwmon_path / "name"
            if name_file.exists():
                name = name_file.read_text().strip()
                return "amdgpu" in name.lower()
            return False
        except (IOError, OSError):
            return False

    async def read_temperatures(self) -> Dict[str, Optional[int]]:
        """Read current temperatures from all monitored GPUs"""
        temperatures = {}

        for gpu_id, temp_path in self.gpu_temps.items():
            try:
                if temp_path.exists():
                    temp = int(temp_path.read_text().strip())
                    temperatures[gpu_id] = temp
                    logger.debug(f"{Fore.CYAN}{gpu_id}: {temp/1000:.1f}°C")
                else:
                    temperatures[gpu_id] = None
                    logger.error(f"Temperature file not found: {temp_path}")
            except (ValueError, IOError) as e:
                temperatures[gpu_id] = None
                logger.error(f"Failed to read temperature for {gpu_id}: {e}")

        return temperatures

    async def connect(self) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """Connect to the fan control server with retries"""
        attempt = 0
        while True:
            attempt += 1
            try:
                reader, writer = await asyncio.open_connection(
                    self.config.HOST, self.config.PORT
                )
                if attempt > 1:
                    self.total_reconnects += 1
                    logger.info(
                        f"{Fore.GREEN}Reconnected after {attempt} attempts "
                        f"(Total reconnects: {self.total_reconnects})"
                    )
                return reader, writer
            except (ConnectionRefusedError, OSError) as e:
                if attempt == 1:
                    logger.error(f"{Fore.RED}Failed to connect: {e}")
                await asyncio.sleep(min(30, attempt))

    async def monitor_loop(self):
        """Main monitoring loop"""
        reconnect = True
        while reconnect:
            try:
                reader, writer = await self.connect()

                while True:
                    temps = await self.read_temperatures()
                    if any(temp is not None for temp in temps.values()):
                        message = json.dumps(
                            {
                                "temperatures": temps,
                                "timestamp": asyncio.get_event_loop().time(),
                            }
                        )
                        writer.write(f"{message}\n".encode())
                        await writer.drain()

                    await asyncio.sleep(self.config.SLEEP_INTERVAL)

            except asyncio.CancelledError:
                logger.info("Shutting down...")
                if "writer" in locals():
                    writer.close()
                    await writer.wait_closed()
                reconnect = False
                raise
            except Exception as e:
                if isinstance(e, ConnectionResetError):
                    logger.error("Connection lost")
                elif isinstance(e, (BrokenPipeError, ConnectionRefusedError)):
                    if "writer" in locals():
                        writer.close()
                        await writer.wait_closed()
                else:
                    logger.error(f"Error in monitor loop: {e}")

                if "writer" in locals():
                    try:
                        writer.close()
                        await writer.wait_closed()
                    except Exception:
                        pass

                await asyncio.sleep(1)  # Brief pause before reconnect


def parse_args():
    parser = argparse.ArgumentParser(description="GPU Temperature Monitor Client")
    parser.add_argument("--host", type=str, help="Fan control server host address")
    parser.add_argument("--port", type=int, help="Fan control server port")
    parser.add_argument(
        "--gpu-paths",
        nargs="+",
        help="Specific temperature input paths to monitor (optional)",
    )
    parser.add_argument(
        "--interval", type=float, help="Temperature polling interval in seconds"
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()


async def main():
    args = parse_args()

    if args.debug:
        logger.setLevel(logging.DEBUG)

    # Load config file with client defaults
    config = FanControlConfig.load_config("client")

    # Override with command line arguments if provided
    if args.host:
        config.HOST = args.host
    if args.port:
        config.PORT = args.port
    if args.interval:
        config.SLEEP_INTERVAL = args.interval

    try:
        logger.info(f"{Fore.GREEN}Starting temperature monitor...")
        logger.info(f"{Fore.CYAN}Server: {config.HOST}:{config.PORT}")
        logger.info(f"{Fore.CYAN}Update interval: {config.SLEEP_INTERVAL}s")

        monitor = TemperatureMonitor(config, args.gpu_paths)
        await monitor.monitor_loop()
    except KeyboardInterrupt:
        logger.info(f"{Fore.YELLOW}Shutting down...")
    except Exception as e:
        logger.error(f"{Fore.RED}Fatal error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
