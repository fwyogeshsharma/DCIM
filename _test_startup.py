import sys, os, traceback
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

log = open("_test_startup.log", "w")

def p(msg):
    print(msg)
    log.write(msg + "\n")
    log.flush()

try:
    p("--- importing QApplication ---")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    p("--- setting HighDpi policy (static, before QApp) ---")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    p("--- creating QApplication ---")
    app = QApplication(sys.argv)
    p("QApplication: OK")

    p("--- importing _create_splash ---")
    from app.main import _create_splash
    p("--- calling _create_splash ---")
    s = _create_splash()
    p("Splash: OK")

    p("--- importing MainWindow ---")
    from ui.main_window import MainWindow
    p("MainWindow import: OK")

    p("--- constructing MainWindow ---")
    w = MainWindow()
    p("MainWindow(): OK")

    p("ALL STARTUP OK")

except Exception as e:
    msg = traceback.format_exc()
    p("EXCEPTION:\n" + msg)

log.close()
