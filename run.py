import sys #connection between code and OS (terminal)
from PyQt5.QtWidgets import QApplication
from app.ui.ui import MainWindow

app = QApplication(sys.argv) #create app object with user preferences as parameter

window = MainWindow()
window.show()

sys.exit(app.exec_())