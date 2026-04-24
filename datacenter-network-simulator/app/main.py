"""
Datacenter Network Simulator - Entry Point
"""
import sys
import os

# ── SNMPSim runner mode ───────────────────────────────────────────────────────
# When the frozen exe is launched with _SNMPSIM_RUNNER=1 it acts as the snmpsim
# subprocess instead of starting the UI.  This lets a single self-contained exe
# serve as both the main application and the snmpsim backend without requiring
# any external Python installation on the target machine.
if os.environ.get("_SNMPSIM_RUNNER") == "1":
    if getattr(sys, "frozen", False):
        # snmpsim's variation modules are .py files that it opens and exec()s at
        # runtime.  In a frozen exe those files can't be exec'd reliably, so
        # load_variation_modules() returns 1 (error) instead of a dict, causing
        # an AttributeError later.  Pointing snmpsim at an empty temp directory
        # makes it find no .py files → returns {} → starts cleanly.
        # We don't use variation modules in our simulation so this is safe.
        import tempfile as _tempfile
        _var_dir = _tempfile.mkdtemp(prefix="snmpsim_var_")
        # --variation-modules-dir uses action="append", so one flag is enough.
        sys.argv.insert(1, f"--variation-modules-dir={_var_dir}")

    from snmpsim.commands.responder import main as _snmpsim_main
    sys.exit(_snmpsim_main() or 0)
# ─────────────────────────────────────────────────────────────────────────────

import faulthandler

# Suppress gRPC C-core stderr noise (bind failures during initial retry are expected)
os.environ.setdefault("GRPC_VERBOSITY", "NONE")

# Suppress pysnmp-lextudio deprecation warning (third-party library, not actionable)
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning, message=".*pysnmp-lextudio.*")

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
from PySide6.QtCore import Qt, QObject, QEvent
from PySide6.QtGui import QPalette, QColor

# ── Windows dark title bar ────────────────────────────────────────────────────
if sys.platform == "win32":
    import ctypes
    _dwmapi = ctypes.windll.dwmapi
    _DWMWA_USE_IMMERSIVE_DARK_MODE = 20   # Windows 10 20H1+ / Windows 11
    _DWMWA_USE_IMMERSIVE_DARK_MODE_PRE20H1 = 19

    def _apply_dark_titlebar(hwnd: int) -> None:
        """Tell DWM to render the title bar in dark mode for the given HWND."""
        value = ctypes.c_int(1)
        # Try the modern attribute first; fall back to the pre-20H1 one
        ret = _dwmapi.DwmSetWindowAttribute(
            hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(value), ctypes.sizeof(value)
        )
        if ret != 0:  # S_OK == 0
            _dwmapi.DwmSetWindowAttribute(
                hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE_PRE20H1,
                ctypes.byref(value), ctypes.sizeof(value)
            )

    class _DarkTitleBarFilter(QObject):
        """Application-level event filter: dark-title-bar every top-level window."""
        def eventFilter(self, obj, event):
            try:
                from PySide6.QtWidgets import QWidget
                if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
                    hwnd = int(obj.winId())
                    if hwnd:
                        _apply_dark_titlebar(hwnd)
            except Exception:
                pass
            return False
else:
    def _apply_dark_titlebar(hwnd: int) -> None:
        pass

    class _DarkTitleBarFilter(QObject):
        def eventFilter(self, obj, event):
            return False


def _force_dark_palette(app: QApplication) -> None:
    """Apply a dark QPalette so Fusion renders correctly regardless of Windows theme."""
    p = QPalette()
    # Background surfaces
    p.setColor(QPalette.Window,           QColor(0x0d, 0x11, 0x17))
    p.setColor(QPalette.Base,             QColor(0x16, 0x1b, 0x22))
    p.setColor(QPalette.AlternateBase,    QColor(0x0d, 0x11, 0x17))
    # Buttons / panels
    p.setColor(QPalette.Button,           QColor(0x21, 0x26, 0x2d))
    p.setColor(QPalette.Dark,             QColor(0x0d, 0x11, 0x17))
    p.setColor(QPalette.Mid,              QColor(0x16, 0x1b, 0x22))
    p.setColor(QPalette.Midlight,         QColor(0x21, 0x26, 0x2d))
    p.setColor(QPalette.Light,            QColor(0x30, 0x36, 0x3d))
    p.setColor(QPalette.Shadow,           QColor(0x00, 0x00, 0x00))
    # Text
    p.setColor(QPalette.WindowText,       QColor(0xe6, 0xed, 0xf3))
    p.setColor(QPalette.Text,             QColor(0xe6, 0xed, 0xf3))
    p.setColor(QPalette.ButtonText,       QColor(0xe6, 0xed, 0xf3))
    p.setColor(QPalette.BrightText,       QColor(0xff, 0xff, 0xff))
    # Tooltips
    p.setColor(QPalette.ToolTipBase,      QColor(0x16, 0x1b, 0x22))
    p.setColor(QPalette.ToolTipText,      QColor(0xe6, 0xed, 0xf3))
    # Placeholder text
    p.setColor(QPalette.PlaceholderText,  QColor(0x6e, 0x76, 0x81))
    # Selection
    p.setColor(QPalette.Highlight,        QColor(0x1f, 0x6f, 0xeb))
    p.setColor(QPalette.HighlightedText,  QColor(0xff, 0xff, 0xff))
    # Links
    p.setColor(QPalette.Link,             QColor(0x58, 0xa6, 0xff))
    p.setColor(QPalette.LinkVisited,      QColor(0xbc, 0x8c, 0xff))
    # Disabled variants
    p.setColor(QPalette.Disabled, QPalette.WindowText,  QColor(0x6e, 0x76, 0x81))
    p.setColor(QPalette.Disabled, QPalette.Text,         QColor(0x6e, 0x76, 0x81))
    p.setColor(QPalette.Disabled, QPalette.ButtonText,   QColor(0x6e, 0x76, 0x81))
    p.setColor(QPalette.Disabled, QPalette.Highlight,    QColor(0x30, 0x36, 0x3d))
    p.setColor(QPalette.Disabled, QPalette.HighlightedText, QColor(0x6e, 0x76, 0x81))
    app.setPalette(p)


def main():
    print("[1] setting HighDPI policy", flush=True)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    print("[2] creating QApplication", flush=True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")  # stable cross-platform style; avoids WindowsVista backing-store issues
    _force_dark_palette(app)  # ensure dark palette regardless of Windows light/dark mode setting

    # Install filter so every top-level window (main + dialogs) gets a dark title bar
    _title_filter = _DarkTitleBarFilter()
    app.installEventFilter(_title_filter)

    app.setApplicationName("Datacenter Network Simulator")
    app.setOrganizationName("Datacenter Network Simulator")
    app.setApplicationVersion("2.0.0")

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
