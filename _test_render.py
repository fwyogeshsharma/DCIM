"""Isolate which widget causes the crash when rendered."""
import sys, os
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer

QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)
app = QApplication(sys.argv)

from ui.main_window import MainWindow
w = MainWindow()
w.resize(1280, 800)
w.show()

# Quit after 2 seconds to see if it can render at all
QTimer.singleShot(2000, app.quit)

print("entering exec()")
code = app.exec()
print(f"exec() returned: {code}")
sys.exit(code)
