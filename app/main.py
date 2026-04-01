"""
SNMP Network Topology Simulator - Entry Point
"""
import sys
import os
import faulthandler

# Ensure project root is on the path (handles both dev and PyInstaller modes)
if getattr(sys, "frozen", False):
    # Running as PyInstaller bundle
    _base_dir = sys._MEIPASS
else:
    _base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if _base_dir not in sys.path:
    sys.path.insert(0, _base_dir)

# Change working directory to project root so relative paths work
os.chdir(_base_dir)

# Write a stack trace to crash.log if the process receives a fatal signal
_crash_log = open(os.path.join(_base_dir, "crash.log"), "w")
faulthandler.enable(_crash_log)

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt


def main():
    print("[1] setting HighDPI policy", flush=True)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    print("[2] creating QApplication", flush=True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # stable cross-platform style; avoids WindowsVista backing-store issues
    app.setApplicationName("SNMP Network Topology Simulator")
    app.setOrganizationName("SNMPSim Tools")
    app.setApplicationVersion("1.0.0")

    # Import MainWindow AFTER QApplication so all Qt objects in imported
    # modules (e.g. QColor in DEVICE_COLORS) are created with a live app.
    print("[3] importing MainWindow", flush=True)
    from ui.main_window import MainWindow

    print("[4] constructing MainWindow", flush=True)
    window = MainWindow()
    window.resize(1280, 800)
    window.show()

    print("[5] entering exec()", flush=True)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
