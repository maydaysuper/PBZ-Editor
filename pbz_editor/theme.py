"""Fluent-inspired desktop surfaces, controls and interaction states."""
from pathlib import Path

STYLE = """
QWidget { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; color: #242424; }
QMainWindow, QDialog { background: #f3f3f3; }
QFrame#header { background: white; border: 0; border-bottom: 1px solid #e5e5e5; }
QFrame#saveCard { background: #f3f3f3; border: 0; border-bottom: 1px solid #e5e5e5; }
QFrame#inspector { background: white; border: 0; }
QFrame#selectionCard { background: #f6f6f6; border: 0; border-top: 1px solid #e5e5e5; }
QFrame#sidebar { background: #f3f3f3; border: 0; border-right: 1px solid #e5e5e5; }
QLabel#section { font-size: 19px; font-weight: 600; color: #242424; }
QLabel#sidebarTitle { font-size: 15px; font-weight: 600; }
QLabel#muted { color: #707070; font-size: 12px; }
QLabel#caption, QLabel#count { color: #858585; font-size: 11px; }
QLabel#itemName { font-size: 17px; font-weight: 600; color: #242424; }
QLabel#progressInfo { color: #424242; font-size: 13px; font-weight: 600; }
QLabel#status { background: #f3f3f3; color: #707070; font-size: 11px; padding: 6px 16px; border-top: 1px solid #e5e5e5; }
QLabel#saveState { color: #707070; font-size: 12px; padding: 4px 10px; }
QLabel#saveState[dirty="true"] { color: #0f6cbd; }
QPushButton { background: white; border: 1px solid #d1d1d1; border-radius: 4px; padding: 7px 12px; }
QPushButton:hover { background: #f5f5f5; border-color: #bdbdbd; }
QPushButton:pressed { background: #ebebeb; }
QPushButton:disabled { color: #a5a5a5; background: #f5f5f5; border-color: #e5e5e5; }
QPushButton#quiet { background: transparent; border-color: transparent; color: #424242; }
QPushButton#quiet:hover { background: #ebebeb; }
QPushButton#quiet:disabled { color: #b3b3b3; }
QPushButton#primary { background: #0f6cbd; color: white; border: 1px solid #0f6cbd; font-weight: 600; padding: 7px 16px; }
QPushButton#primary:hover { background: #115ea3; border-color: #115ea3; }
QPushButton#primary:pressed { background: #0f548c; }
QPushButton#primary:disabled { background: #ebebeb; border-color: #ebebeb; color: #a5a5a5; }
QLineEdit, QSpinBox, QComboBox { background: white; border: 1px solid #d1d1d1; border-radius: 4px; padding: 7px 9px; min-height: 20px; selection-background-color: #cfe4fa; selection-color: #242424; }
QLineEdit:hover, QSpinBox:hover, QComboBox:hover { border-color: #9e9e9e; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid #0f6cbd; }
QSpinBox { font-weight: 600; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox::down-arrow { image: url("@CHEVRON@"); width: 10px; height: 7px; }
QComboBox QAbstractItemView { background: white; color: #242424; selection-background-color: #e5f1fb; selection-color: #242424; border: 1px solid #d1d1d1; padding: 4px; outline: 0; }
QListWidget { background: transparent; color: #424242; border: 0; outline: 0; }
QListWidget::item { padding: 9px 10px; margin: 1px 0; border-radius: 4px; }
QListWidget::item:hover { background: #ebebeb; }
QListWidget::item:selected { background: #e5f1fb; color: #0f6cbd; font-weight: 600; }
QTableWidget { background: white; color: #242424; alternate-background-color: white; border: 0; gridline-color: transparent; selection-background-color: #e5f1fb; selection-color: #242424; outline: 0; }
QTableWidget::item { padding: 6px 8px; border: 0; }
QTableWidget::item:hover { background: #f5f5f5; }
QTableWidget::item:selected { background: #e5f1fb; color: #242424; }
QHeaderView::section { background: white; border: 0; border-bottom: 1px solid #e5e5e5; color: #707070; padding: 9px 8px; font-size: 11px; font-weight: 500; }
QSplitter::handle { background: #e5e5e5; width: 1px; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 4px 0; }
QScrollBar::handle:vertical { background: #d1d1d1; border-radius: 4px; min-height: 34px; margin: 0 1px; }
QScrollBar::handle:vertical:hover { background: #adadad; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QMenu { background: white; color: #242424; border: 1px solid #d1d1d1; padding: 4px; }
QMenu::item { padding: 8px 16px; border-radius: 4px; }
QMenu::item:selected { background: #e5f1fb; }
QPlainTextEdit { background: white; border: 1px solid #d1d1d1; border-radius: 4px; padding: 8px; }
QProgressBar { border: 0; background: #ebebeb; max-height: 3px; min-height: 3px; }
QProgressBar::chunk { background: #0f6cbd; }
QCheckBox { spacing: 6px; }
QToolTip { background: white; color: #242424; padding: 6px; border: 1px solid #d1d1d1; }
""".replace('@CHEVRON@', (Path(__file__).parent/'assets/chevron-down.svg').as_posix())
