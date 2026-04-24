"""
Setup script for Datacenter Network Simulator.
Used by PyInstaller and for development installation.
"""
from setuptools import setup, find_packages

setup(
    name="datacenter-network-simulator",
    version="2.0.0",
    description="Visual datacenter network topology simulator with SNMP and gNMI simulation",
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
            "datacenter-network-sim=app.main:main",
        ],
    },
)
