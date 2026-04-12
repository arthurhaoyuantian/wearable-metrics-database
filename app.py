import logging
import sys  # connection between code and OS (terminal)

from PyQt5.QtWidgets import QApplication

from src.logging_config import setup_logging
from src.ui.ui import MainWindow

setup_logging()
logging.getLogger(__name__).info("Smartwatch EHR UI starting")

app = QApplication(sys.argv)  # create app object with user preferences as parameter

window = MainWindow()
window.show()

sys.exit(app.exec_())