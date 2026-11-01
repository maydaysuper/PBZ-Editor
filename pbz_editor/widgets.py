from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QObject, Signal, QRunnable, QThreadPool, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox as QtMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .paths import catalog_path, find_game, find_saves, load_settings, local_dir, save_settings
from .core import inventory_level
from .catalog import Catalog
from .regions import matches_region
from .i18n import tr, set_language, language, system_language, localized_item, translate_message


class QMessageBox(QtMessageBox):
    @staticmethod
    def warning(parent, title, text, *args):
        return QtMessageBox.warning(parent, tr(title), translate_message(text), *args)

    @staticmethod
    def information(parent, title, text, *args):
        return QtMessageBox.information(parent, tr(title), translate_message(text), *args)

    @staticmethod
    def question(parent, title, text, *args):
        return QtMessageBox.question(parent, tr(title), translate_message(text), *args)

CATEGORIES = {
    "all": "全部物品", "weapon": "主武器", "phantom": "影之武装",
    "accessory": "饰品", "goods": "道具与材料", "medicine": "药品", "appearance": "外观",
}
UNIQUE = {"weapon", "phantom", "accessory", "appearance"}
LEVELED = {"weapon", "phantom", "accessory", "medicine"}

from .theme import STYLE


def label(text: str, kind: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if kind:
        widget.setObjectName(kind)
    widget.setWordWrap(wrap)
    return widget


def configure_application(app):
    # The offscreen Qt platform does not enumerate Windows fonts. Reuse OS fonts
    # for readable native test captures; no font files are bundled or copied.
    if not QFontDatabase.families():
        for name in ("msyh.ttc", "msyhbd.ttc", "consola.ttf"):
            font = Path("C:/Windows/Fonts") / name
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)


def button(text: str, callback: Callable, primary: bool = False) -> QPushButton:
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    if primary:
        widget.setObjectName("primary")
    return widget


def value_field(spin):
    """A typed value with visible minus/plus buttons, without tiny arrows."""
    # Qt's stylesheet engine collapses the editor to one pixel with NoButtons.
    # Keep native geometry and hide only the small arrow subcontrols.
    spin.setStyleSheet('QSpinBox::up-button, QSpinBox::down-button { width: 0px; }'
                      'QSpinBox QLineEdit { border: 0; padding: 0px 8px; }')
    field = QWidget()
    row = QHBoxLayout(field)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(5)
    row.addWidget(spin, 1)
    for text, callback in [('−', spin.stepDown), ('+', spin.stepUp)]:
        action = button(text, callback)
        action.setFixedWidth(34)
        action.setToolTip(tr('减少 1') if text == '−' else tr('增加 1'))
        row.addWidget(action)
    return field


class WorkerSignals(QObject):
    done = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)


class Worker(QRunnable):
    def __init__(self, function: Callable):
        super().__init__()
        self.function = function
        self.signals = WorkerSignals()

    def run(self):
        try:
            result = self.function()
        except Exception as error:
            logging.exception("Editor operation failed")
            self.signals.error.emit(str(error))
        else:
            self.signals.done.emit(result)


class SettingsDialog(QDialog):
    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('路径设置'))
        self.resize(650, 230)
        form = QFormLayout(self)
        self.game = QLineEdit(settings.get("game_dir", ""))
        self.oodle = QLineEdit(settings.get("oodle_path", ""))
        for title, field, is_folder in [(tr('游戏安装目录'), self.game, True), (tr('兼容库（高级，可选）'), self.oodle, False)]:
            row = QHBoxLayout()
            row.addWidget(field)
            browse = button(tr('浏览'), lambda _=False, f=field, folder=is_folder: self.browse(f, folder))
            row.addWidget(browse)
            form.addRow(title, row)
        form.addRow(label(tr('程序已内置解码组件，通常无需填写兼容库。'), 'muted', True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def browse(self, field, folder):
        value = QFileDialog.getExistingDirectory(self, tr('选择游戏目录'), field.text()) if folder else QFileDialog.getOpenFileName(self, tr('选择 Oodle DLL'), field.text(), "Oodle library (oo2*.dll);;DLL (*.dll)")[0]
        if value:
            field.setText(value)


class AdvancedDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr('通过内部 ID 添加'))
        self.resize(480, 300)
        layout = QFormLayout(self)
        self.item_id = QLineEdit()
        self.item_id.setPlaceholderText(tr('例如 AC_ResistRanged'))
        self.category = QComboBox()
        for key, name in CATEGORIES.items():
            if key != "all":
                self.category.addItem(tr(name), key)
        self.quantity = QSpinBox()
        self.quantity.setRange(1, 999999999)
        self.quantity.setValue(1)
        self.level = QSpinBox()
        self.level.setRange(0, 999)
        self.medicine_slot = QComboBox()
        for text, value in [(tr('保持当前装备'), 'keep'), (tr('装备到上栏'), 'top'), (tr('装备到下栏'), 'bottom'), (tr('不装备'), 'none')]:
            self.medicine_slot.addItem(text, value)
        self.category.currentIndexChanged.connect(self._category_changed)
        layout.addRow(tr('内部 ID'), self.item_id)
        layout.addRow(tr('物品类别'), self.category)
        layout.addRow(tr('数量 / 使用次数'), self.quantity)
        layout.addRow(tr('等级'), self.level)
        layout.addRow(tr('药品快捷栏'), self.medicine_slot)
        self._category_changed()
        layout.addRow(label(tr('按所选类别创建记录。未列入物品库的 ID，其游戏内效果需要自行验证。'), "muted", True))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _category_changed(self):
        medicine = self.category.currentData() == 'medicine'
        self.quantity.setMinimum(0 if medicine else 1)
        self.medicine_slot.setEnabled(medicine)
        if medicine:
            self.quantity.setValue(0)


