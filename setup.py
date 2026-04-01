"""
Setup script for SNMP Network Topology Simulator.
Used by PyInstaller and for development installation.
"""
from setuptools import setup, find_packages

setup(
    name="snmp-topology-simulator",
    version="1.0.0",
    description="Visual SNMP network topology simulator with SNMPSim integration",
    packages=find_packages(),
    python_requires=">=3.11",
    install_requires=[
        "PySide6>=6.6.0",
        "networkx>=3.2",
        "pysnmp>=4.4.12",
        "snmpsim-lextudio>=1.0.4",
        "Jinja2>=3.1.2",
    ],
    entry_points={
        "console_scripts": [
            "snmp-topology-sim=app.main:main",
        ],
    },
)
