from setuptools import setup, find_packages

setup(
    name="remote_fancontrol",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "asyncio>=3.4.3",
        "colorama>=0.4.6",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-asyncio>=0.20.0",
            "pylint>=2.17.0",
            "black>=23.0.0",
            "mypy>=1.0.0",
            "flake8>=6.0.0",
            "isort>=5.12.0",
        ]
    },
    entry_points={
        "console_scripts": [
            "remote-fancontrol-server=remote_fancontrol.server.fan_controller:main",
            "remote-fancontrol-client=remote_fancontrol.client.temperature_monitor:main",
        ],
    },
    python_requires=">=3.7",
)
