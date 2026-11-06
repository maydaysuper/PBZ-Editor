from .widgets import *

from PySide6.QtWidgets import QMenu
from PySide6.QtGui import QShortcut, QKeySequence, QPixmap, QPainter
from PySide6.QtCore import QSize
import threading
from .draft import InventoryDraft, item_key, _snap_soul
from .paths import resolve_game
from . import __version__


class EditorWindow(QMainWindow):
    session_loaded = Signal()
    task_finished = Signal()
    preparation_progress = Signal(str)

    def __init__(self, game_dir=None, save_path=None, catalog=None, oodle_path=None, language_code=None, auto_language=False):
        super().__init__()
        self.resize(1360, 900)
        self.setMinimumSize(1050, 720)
        self.settings = load_settings()
        set_language(language_code or self.settings.get('language') or (system_language() if auto_language else 'zh-CN'))
        self.setWindowTitle(tr('PBZ Editor {0} · 影之刃零', __version__))
        if game_dir:
            self.settings['game_dir'] = str(game_dir)
        if oodle_path:
            self.settings['oodle_path'] = str(oodle_path)
        if not self.settings.get('game_dir'):
            detected = find_game()
            if detected:
                self.settings['game_dir'] = str(detected)
        self.session = self.draft = None
        self.busy = False
        self._typing = False
        self.icon_index = None
        self._icon_images = {}
        self.icons_loading = False
        self._icon_cancel = threading.Event()
        self.catalog = None
        self._custom_catalog = catalog is not None
        self.preparing = False
        self._prepare_cancel = threading.Event()
        self._pending_save = None
        self.items, self.item_lookup = [], {}
        self.visible_items, self.bag_rows = [], []
        self.selected_item = self.bag_selected = None
        self.pool, self._workers = QThreadPool(self), []
        self._build()
        self.preparation_progress.connect(lambda value: self.status.setText(tr(value)))
        self.load_catalog(catalog or catalog_path())
        candidate = save_path
        if not candidate and self.settings.get('last_save') and Path(self.settings['last_save']).is_file():
            candidate = self.settings['last_save']
        if not candidate and self.settings.get('game_dir'):
            saves = find_saves(self.settings['game_dir'])
            if len(saves) == 1:
                candidate = saves[0]
            elif len(saves) > 1:
                QTimer.singleShot(100, lambda: self.choose_detected_save(saves))
        if candidate:
            QTimer.singleShot(80, lambda: self.load_save(str(candidate)))

    @property
    def pending(self):
        if not self.draft:
            return []
        return [*self.draft.operations(), *self.draft.quest_ops]

    def _unsaved(self):
        return bool(self.draft and (self.draft.changed_keys or self.draft.quest_ops))

    @property
    def owned(self):
        return self.draft.items if self.draft else {}

    def _build(self):
        shell = QWidget()
        outer = QVBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QFrame()
        header.setObjectName('header')
        h = QHBoxLayout(header)
        h.setContentsMargins(16, 8, 16, 8)
        h.setSpacing(8)
        self.open_button = button(tr('打开存档'), self.choose_save)
        self.open_button.setIcon(QIcon(str(Path(__file__).parent/'assets/open.svg')))
        h.addWidget(self.open_button)
        self.changes_label = label(tr('尚未打开存档'), 'saveState')
        self.undo_button = button(tr('撤销'), self.undo)
        self.undo_button.setIcon(QIcon(str(Path(__file__).parent/'assets/undo.svg')))
        self.undo_button.setToolTip(tr('撤销最近一次背包修改 · Ctrl+Z'))
        h.addWidget(self.undo_button)
        self.apply_button = button(tr('保存存档'), self.apply_save, True)
        self.apply_button.setIcon(QIcon(str(Path(__file__).parent/'assets/save.svg')))
        self.apply_button.setToolTip(tr('统一保存当前背包，写入前自动备份 · Ctrl+S'))
        self.backups_button = button(tr('备份'), self.show_backups)
        self.backups_button.setObjectName('quiet')
        h.addWidget(self.backups_button)
        self.more_button = QPushButton(tr('更多'))
        self.more_button.setObjectName('quiet')
        self.more_menu = QMenu(self)
        self.more_menu.addAction(tr('另存为副本…'), self.export_save)
        self.more_menu.addAction(tr('放弃全部修改'), self.discard)
        self.more_menu.addSeparator()
        self.village_quick_hand = self.more_menu.addAction(tr('陆家村：让小张可以交付'), lambda: self.complete_quick_hand('village'))
        self.estate_quick_hand = self.more_menu.addAction(tr('戏梦山庄：让小张可以交付'), lambda: self.complete_quick_hand('estate'))
        for action in (self.village_quick_hand, self.estate_quick_hand):
            action.setToolTip(tr('不在编辑器里结算。保存后进游戏，到该区域小张藏身处和妙手小张对话，由游戏完成交付、奖励和成就。请先退出游戏。'))
        self.more_menu.addSeparator()
        self.advanced_action = self.more_menu.addAction(tr('通过内部 ID 添加…'), self.advanced_add)
        self.internal_action = self.more_menu.addAction(tr('显示内部条目与编号'))
        self.internal_action.setCheckable(True)
        self.internal_action.toggled.connect(self.refresh_all)
        self.more_menu.addAction(tr('设置…'), self.show_settings)
        self.more_button.setMenu(self.more_menu)
        h.addWidget(self.more_button)
        h.addStretch()
        self.language_combo = QComboBox()
        self.language_combo.setAccessibleName(tr('语言 / Language'))
        self.language_combo.addItem(tr('简体中文'), 'zh-CN')
        self.language_combo.addItem('English', 'en-US')
        self.language_combo.setCurrentIndex(self.language_combo.findData(language()))
        self.language_combo.setFixedWidth(118)
        self.language_combo.currentIndexChanged.connect(self._change_language)
        h.addWidget(self.language_combo)
        h.addWidget(self.changes_label)
        h.addWidget(self.apply_button)
        outer.addWidget(header)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        outer.addWidget(self.progress)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.save_card = QFrame()
        self.save_card.setObjectName('saveCard')
        saved = QHBoxLayout(self.save_card)
        saved.setContentsMargins(16, 8, 16, 8)
        saved.setSpacing(20)
        self.slot_combo = QComboBox()
        self.slot_combo.setMinimumWidth(330)
        self.slot_combo.setPlaceholderText(tr('选择存档进度'))
        self.slot_combo.currentIndexChanged.connect(self._slot_changed)
        saved.addWidget(self.slot_combo)
        self.slot_info = label(tr('尚未载入进度'), 'progressInfo')
        saved.addWidget(self.slot_info)
        saved.addStretch()
        file_info = QVBoxLayout()
        file_info.setSpacing(5)
        self.save_file_label = label(tr('尚未打开存档'), 'muted')
        self.save_file_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.save_file_label.setMaximumWidth(250)
        self.save_file_label.setMinimumWidth(145)
        self.save_file_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.save_file_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        file_info.addWidget(self.save_file_label, alignment=Qt.AlignmentFlag.AlignRight)
        file_info.addWidget(label(tr('保存前自动备份'), 'caption'), alignment=Qt.AlignmentFlag.AlignRight)
        saved.addLayout(file_info)
        layout.addWidget(self.save_card)
        self.compatibility_label = label('', 'compatibility', True)
        self.compatibility_label.setContentsMargins(16, 6, 16, 6)
        self.compatibility_label.hide()
        layout.addWidget(self.compatibility_label)
        self.welcome = QFrame()
        self.welcome.setObjectName('inspector')
        intro = QHBoxLayout(self.welcome)
        messages = QVBoxLayout()
        messages.addWidget(label(tr('开始使用'), 'section'))
        self.welcome_text = label(tr('自动识别本机游戏与进度，首次使用会在后台准备物品数据。'), 'muted', True)
        messages.addWidget(self.welcome_text)
        intro.addLayout(messages, 1)
        self.choose_game_button = button(tr('选择游戏位置'), self.choose_game)
        intro.addWidget(self.choose_game_button)
        self.retry_button = button(tr('重新准备'), self.retry_preparation)
        self.retry_button.hide()
        intro.addWidget(self.retry_button)
        self.cancel_prepare_button = button(tr('取消准备'), self.cancel_preparation)
        self.cancel_prepare_button.hide()
        intro.addWidget(self.cancel_prepare_button)
        layout.addWidget(self.welcome)
        workspace = QWidget()
        columns = QHBoxLayout(workspace)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(0)
        self.sidebar = QFrame()
        self.sidebar.setObjectName('sidebar')
        self.sidebar.setFixedWidth(200 if language() == 'en-US' else 172)
        navigation = QVBoxLayout(self.sidebar)
        navigation.setContentsMargins(12, 20, 12, 16)
        navigation.setSpacing(8)
        navigation.addWidget(label(tr('物品库'), 'sidebarTitle'))
        navigation.addSpacing(12)
        navigation.addWidget(label(tr('分类'), 'caption'))
        self.category_filter = QComboBox(self.sidebar)
        self.category_filter.hide()
        self.category_list = QListWidget()
        self.category_list.setAccessibleName(tr('物品分类'))
        for key, name in CATEGORIES.items():
            self.category_filter.addItem(tr(name), key)
            entry = QListWidgetItem(name)
            entry.setData(Qt.ItemDataRole.UserRole, key)
            self.category_list.addItem(entry)
        self.category_filter.currentIndexChanged.connect(self._category_changed)
        self.category_list.currentRowChanged.connect(self._category_selected)
        self.category_list.setCurrentRow(0)
        navigation.addWidget(self.category_list, 1)
        self.region_filter = QComboBox()
        self.region_filter.setAccessibleName(tr('地图区域'))
        self.region_filter.setFixedWidth(130 if language() == 'en-US' else 148)
        self.region_filter.view().setMinimumWidth(260 if language() == 'en-US' else 180)
        self.region_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.region_filter.setMinimumContentsLength(6)
        self.region_filter.addItem(tr('全部区域'), 'all')
        self.region_filter.setToolTip(tr('按游戏地图的关联区域筛选，不代表唯一获取地点。'))
        self.region_filter.currentIndexChanged.connect(self.refresh_items)
        navigation.addSpacing(8)
        navigation.addWidget(label(tr('本地存档 · 离线编辑'), 'caption'))
        columns.addWidget(self.sidebar)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.workspace_splitter = split
        split.setHandleWidth(1)
        self.library_panel = QFrame()
        self.library_panel.setObjectName('inspector')
        left = QVBoxLayout(self.library_panel)
        left.setContentsMargins(20, 20, 20, 16)
        left.setSpacing(14)
        heading = QHBoxLayout()
        self.catalog_heading = label(tr('全部物品'), 'section')
        heading.addWidget(self.catalog_heading)
        heading.addSpacing(8)
        heading.addWidget(self.region_filter)
        heading.addStretch()
        self.catalog_summary = label('', 'count')
        self.icon_status = label('', 'caption')
        heading.addWidget(self.icon_status)
        heading.addWidget(self.catalog_summary)
        left.addLayout(heading)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('搜索名称、说明或编号'))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_items)
        left.addWidget(self.search)
        self.item_table = QTableWidget(0, 3)
        self.item_table.setHorizontalHeaderLabels([tr('物品名称'), tr('类别'), tr('背包')])
        self._table_style(self.item_table)
        self.item_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.item_table.setColumnWidth(1, 128 if language() == 'en-US' else 92)
        self.item_table.setColumnWidth(2, 75)
        self.item_table.setIconSize(QSize(36, 36))
        self.item_table.verticalHeader().setDefaultSectionSize(50)
        self.item_table.itemSelectionChanged.connect(self._item_selected)
        self.item_table.itemDoubleClicked.connect(lambda _: self.add_selected())
        left.addWidget(self.item_table, 1)
        self.library_detail = QFrame()
        self.library_detail.setObjectName('selectionCard')
        self.library_detail.setMinimumHeight(136)
        detail = QVBoxLayout(self.library_detail)
        detail.setContentsMargins(12, 12, 12, 12)
        detail.setSpacing(8)
        self.detail_name = label(tr('选择物品，添加到右侧背包'), 'itemName', True)
        detail_heading = QHBoxLayout()
        self.library_preview = self._preview_label()
        detail_heading.addWidget(self.library_preview)
        detail_heading.addWidget(self.detail_name, 1)
        detail.addLayout(detail_heading)
        self.detail_description = label(tr('可按名称搜索，也可按地图区域查找。'), 'muted', True)
        self.detail_description.setMaximumHeight(68)
        detail.addWidget(self.detail_description)
        self.detail_note = label('', 'muted', True)
        self.detail_note.setMaximumHeight(55)
        detail.addWidget(self.detail_note)
        self.add_button = button(tr('添加到背包'), self.add_selected, True)
        detail_actions = QHBoxLayout()
        detail_actions.setContentsMargins(0, 2, 0, 0)
        detail_actions.addStretch()
        self.add_button.setMinimumWidth(150)
        self.add_button.setObjectName('secondary')
        detail_actions.addWidget(self.add_button)
        detail.addLayout(detail_actions)
        left.addWidget(self.library_detail)
        split.addWidget(self.library_panel)
        self.backpack_panel = QFrame()
        self.backpack_panel.setObjectName('inspector')
        right = QVBoxLayout(self.backpack_panel)
        right.setContentsMargins(20, 20, 20, 16)
        right.setSpacing(14)
        heading = QHBoxLayout()
        heading.addWidget(label(tr('我的背包'), 'section'))
        heading.addStretch()
        self.bag_summary = label('', 'count')
        heading.addWidget(self.bag_summary)
        right.addLayout(heading)
        self.bag_search = QLineEdit()
        self.bag_search.setPlaceholderText(tr('在背包中查找'))
        self.bag_search.setClearButtonEnabled(True)
        self.bag_search.textChanged.connect(self.refresh_bag)
        right.addWidget(self.bag_search)
        self.bag_table = QTableWidget(0, 4)
        self.bag_table.setHorizontalHeaderLabels([tr('物品名称'), tr('数量 / 强化'), tr('状态'), tr('编号')])
        self._table_style(self.bag_table)
        self.bag_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.bag_table.setColumnWidth(1, 144 if language() == 'en-US' else 110)
        self.bag_table.setColumnWidth(2, 84)
        self.bag_table.setColumnHidden(3, True)
        self.bag_table.setIconSize(QSize(36, 36))
        self.bag_table.verticalHeader().setDefaultSectionSize(50)
        self.bag_table.itemSelectionChanged.connect(self._bag_selection_changed)
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._reveal_bag_selection)
        right.addWidget(self.bag_table, 1)
        self.backpack_detail = QFrame()
        self.backpack_detail.setObjectName('selectionCard')
        self.backpack_detail.setMinimumHeight(136)
        bag_detail = QVBoxLayout(self.backpack_detail)
        bag_detail.setContentsMargins(12, 12, 12, 12)
        bag_detail.setSpacing(8)
        self.bag_name = label(tr('选择背包中的物品即可修改'), 'itemName', True)
        bag_heading = QHBoxLayout()
        self.backpack_preview = self._preview_label()
        bag_heading.addWidget(self.backpack_preview)
        bag_heading.addWidget(self.bag_name, 1)
        self.clear_soul_button = button(tr('清除铸魂'), self.clear_soul)
        self.clear_soul_button.setObjectName('quiet')
        self.clear_soul_button.setToolTip(tr('只清除这件武器的铸魂。请先完全退出游戏，点此按钮，再点右上角保存。不改等级和其他数据。类别仅可查看时，移出仍然不可用。'))
        bag_heading.addWidget(self.clear_soul_button)
        self.remove_button = button(tr('移出背包'), self.remove_selected)
        self.remove_button.setObjectName('quiet')
        bag_heading.addWidget(self.remove_button)
        bag_detail.addLayout(bag_heading)
        self.editor = QWidget()
        form = QFormLayout(self.editor)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.quantity_spin, self.level_spin = QSpinBox(), QSpinBox()
        self.quantity_spin.setRange(0, 999999999)
        self.level_spin.setRange(0, 999)
        self.level_spin.setPrefix('+')
        for spin in (self.quantity_spin, self.level_spin):
            spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(self._edit_selected)
            spin.lineEdit().textEdited.connect(self._numeric_input_edited)
        self.quantity_label, self.level_label = QLabel(tr('数量')), QLabel(tr('强化'))
        self.quantity_field, self.level_field = value_field(self.quantity_spin), value_field(self.level_spin)
        self.quantity_field.setMaximumWidth(280)
        self.level_field.setMaximumWidth(280)
        form.addRow(self.quantity_label, self.quantity_field)
        form.addRow(self.level_label, self.level_field)
        self.medicine_slot_label = QLabel(tr('装备位置'))
        self.medicine_slot = QComboBox()
        self.medicine_slot.setMaximumWidth(280)
        for text, value in [(tr('不装备'), 'none'), (tr('上栏'), 'top'), (tr('下栏'), 'bottom'), (tr('保持双栏装备'), 'keep')]:
            self.medicine_slot.addItem(text, value)
        self.medicine_slot.currentIndexChanged.connect(self._edit_selected)
        form.addRow(self.medicine_slot_label, self.medicine_slot)
        self.editor.hide()
        bag_detail.addWidget(self.editor)
        self.bag_note = label(tr('新增物品会立即出现在这里。'), 'muted', True)
        bag_detail.addWidget(self.bag_note)
        right.addWidget(self.backpack_detail)
        split.addWidget(self.backpack_panel)
        split.setChildrenCollapsible(False)
        split.setSizes([610, 570])
        columns.addWidget(split, 1)
        layout.addWidget(workspace, 1)
        self.status = label(tr('打开存档后，即可添加或修改物品。'), 'status', True)
        layout.addWidget(self.status)
        outer.addWidget(content, 1)
        self.setCentralWidget(shell)
        self.save_shortcut = QShortcut(QKeySequence('Ctrl+S'), self)
        self.save_shortcut.activated.connect(self.apply_save)
        self.undo_shortcut = QShortcut(QKeySequence('Ctrl+Z'), self)
        self.undo_shortcut.activated.connect(self.undo)
        self._update_actions()

    def _change_language(self, *_):
        selected = self.language_combo.currentData()
        if self.busy or self.icons_loading or selected == language():
            return
        self._finish_edit()
        category, region = self.category_filter.currentData(), self.region_filter.currentData()
        query, bag_query = self.search.text(), self.bag_search.text()
        selected_key = item_key(self.selected_item) if self.selected_item else None
        bag_key, icons, slot = self.bag_selected, self.icon_index, self.slot_name()
        sizes = self.workspace_splitter.sizes()
        self.settings['language'] = selected
        save_settings(self.settings)
        set_language(selected)
        self.setWindowTitle(tr('PBZ Editor {0} · 影之刃零', __version__))
        self._reveal_timer.stop()
        internal = self.internal_action.isChecked()
        for child in (self.save_shortcut, self.undo_shortcut, self._reveal_timer, self.more_menu):
            child.deleteLater()
        old = self.takeCentralWidget()
        self._build()
        self.internal_action.setChecked(internal)
        self.load_catalog(self._catalog_file)
        if self.draft:
            self.draft.definitions = {item_key(i): i for i in self.items}
        if self.session:
            self.welcome.hide()
            self.save_file_label.setText(self.session.save_path.name)
            self.save_file_label.setToolTip(str(self.session.save_path))
            self._fill_slots(slot)
            self._set_slot_info(slot)
        self.selected_item = next((i for i in self.items if item_key(i) == selected_key), None)
        self.bag_selected = bag_key
        self.category_filter.setCurrentIndex(max(0, self.category_filter.findData(category)))
        self.region_filter.setCurrentIndex(max(0, self.region_filter.findData(region)))
        self.search.setText(query)
        self.bag_search.setText(bag_query)
        self.workspace_splitter.setSizes(sizes)
        self.refresh_all()
        if icons:
            self._apply_icons(icons)
        old.deleteLater()
        self.status.setText(tr('语言已切换，未保存的修改已保留。'))

    @staticmethod
    def _table_style(table):
        table.verticalHeader().hide()
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(False)
        table.setWordWrap(False)
        table.setShowGrid(False)
        table.setFrameShape(QFrame.Shape.NoFrame)
        table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.verticalHeader().setDefaultSectionSize(40)

    @staticmethod
    def _preview_label():
        preview = QLabel()
        preview.setFixedSize(52, 52)
        preview.setAccessibleName(tr('所选物品图标'))
        preview.hide()
        return preview

    def _icon_pixmap(self, item):
        if not item or not self.icon_index:
            return None
        ident = item[0] + ':' + item[1].casefold()
        if ident not in self._icon_images:
            entry = self.icon_index['items'].get(ident)
            if not entry:
                return None
            original = QPixmap(str(local_dir() / 'icons' / entry['file']))
            if original.isNull():
                return None
            tile = QPixmap(128, 128)
            tile.fill(Qt.GlobalColor.transparent)
            painter = QPainter(tile)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#363636'))
            painter.drawRoundedRect(0, 0, 128, 128, 13, 13)
            painter.drawPixmap(4, 4, original.scaled(120, 120, Qt.AspectRatioMode.KeepAspectRatio,
                                                  Qt.TransformationMode.SmoothTransformation))
            painter.end()
            self._icon_images[ident] = tile
        return self._icon_images[ident]

    def _item_icon(self, item):
        pixmap = self._icon_pixmap(item)
        return QIcon(pixmap) if pixmap is not None else QIcon()

    def _show_preview(self, label, item):
        pixmap = self._icon_pixmap(item)
        label.setVisible(pixmap is not None)
        if pixmap is not None:
            label.setPixmap(pixmap.scaled(52, 52, Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
        else:
            label.clear()

    def _apply_icons(self, index):
        # Decoration-only updates preserve selection, typed values and drafts.
        self.icon_index = index
        self._icon_images.clear()
        for row, item in enumerate(self.visible_items):
            self.item_table.item(row, 0).setIcon(self._item_icon(item_key(item)))
        for row, item in enumerate(self.bag_rows):
            self.bag_table.item(row, 0).setIcon(self._item_icon(item))
        self._show_preview(self.library_preview, item_key(self.selected_item) if self.selected_item else None)
        self._show_preview(self.backpack_preview, self.bag_selected)
        self.icon_status.setText(tr('图标 {0}/{1}', len(index['items']), index['total']))
        self.icon_status.setToolTip(tr('来自本机游戏；没有可用图标的物品保留名称显示。'))
        self.icon_status.hide()
        self._reveal_timer.start(0)

    def _reveal_bag_selection(self):
        row = self.bag_table.currentRow()
        if row >= 0:
            self.bag_table.scrollToItem(self.bag_table.item(row, 0), QAbstractItemView.ScrollHint.PositionAtCenter)

    def _ensure_icons(self):
        from .icons import UTOC, build_icons, load_index
        game = self.settings.get('game_dir', '')
        if self.icons_loading or not self.catalog or not game or not (Path(game) / UTOC).is_file():
            return
        root = local_dir() / 'icons'
        cached = load_index(root, game, self.catalog)
        if cached:
            self._apply_icons(cached)
            return
        self.icons_loading = True
        self._update_actions()
        self._icon_cancel = threading.Event()
        self.icon_status.setText(tr('正在准备图标…'))
        self.icon_status.show()
        catalog, oodle = self.catalog, self.settings.get('oodle_path') or None
        worker = Worker(lambda: build_icons(game, catalog, root, oodle,
                        progress=worker.signals.progress.emit, cancel=self._icon_cancel))
        self._workers.append(worker)
        worker.signals.progress.connect(lambda current, total: self.icon_status.setText(tr('准备图标 {0}/{1}', current, total)))
        def finished(value, failed=False):
            self.icons_loading = False
            self._update_actions()
            self._workers.remove(worker)
            if failed:
                if not self._icon_cancel.is_set():
                    logging.warning('Icon preparation unavailable: %s', value)
                self.icon_status.setText(tr('图标未就绪'))
            elif game == self.settings.get('game_dir') and catalog is self.catalog:
                self._apply_icons(value)
            else:
                self._ensure_icons()
        worker.signals.done.connect(lambda value: finished(value))
        worker.signals.error.connect(lambda value: finished(value, True))
        self.pool.start(worker)

    def load_catalog(self, path):
        self._catalog_file = Path(path)
        try:
            catalog = Catalog.load(path)
            self.catalog = catalog
            self.icon_index = None
            self._icon_images.clear()
            self.items = [localized_item(item) for item in catalog.items]
            self.item_lookup = {item_key(i): i for i in self.items}
            self.region_filter.blockSignals(True)
            self.region_filter.clear()
            self.region_filter.addItem(tr('全部区域'), 'all')
            for region in catalog.metadata.get('region_definitions', []):
                if any(region['id'] in i.get('region_ids', []) and i.get('supported', True) for i in self.items):
                    self.region_filter.addItem((region.get('name_en') or region['name']) if language() == 'en-US' else region['name'], region['id'])
            if any(len(i.get('region_ids', [])) > 1 for i in self.items):
                self.region_filter.addItem(tr('跨区域物品'), 'multi')
            if any(i.get('region_status') == 'unknown' for i in self.items):
                self.region_filter.addItem(tr('区域待确认'), 'unknown')
            if any(i.get('region_status') == 'common' for i in self.items):
                self.region_filter.addItem(tr('通用物品'), 'common')
            self.region_filter.blockSignals(False)
        except (OSError, ValueError, KeyError) as error:
            logging.warning('Catalog unavailable: %s', error)
            self.items, self.item_lookup = [], {}
        self.refresh_all()

    def run_task(self, title, function, done):
        if self.busy:
            return
        self.busy = True
        self.progress.show()
        self.status.setText(title)
        self._update_actions()
        worker = Worker(function)
        self._workers.append(worker)
        def finish(value, error=False):
            was_preparing = self.preparing
            self.preparing = False
            self.busy = False
            self.progress.hide()
            self._workers.remove(worker)
            if error:
                if was_preparing and self._prepare_cancel.is_set():
                    self.status.setText(tr('已取消准备，可点击重新准备。'))
                else:
                    self.status.setText(tr('操作未完成；当前背包修改仍保留。'))
                    QMessageBox.warning(self, tr('操作未完成'), value)
                if was_preparing:
                    self.welcome.show()
                    self.retry_button.show()
            else:
                done(value)
            self._update_actions()
            self.task_finished.emit()
        worker.signals.done.connect(lambda value: finish(value))
        worker.signals.error.connect(lambda value: finish(value, True))
        self.pool.start(worker)

    def _resolve_changes(self, continuation):
        self._finish_edit()
        if self._unsaved():
            answer = QMessageBox.question(self, tr('背包尚未保存'), tr('保存当前背包的修改吗？'),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Save:
                self.apply_save(after=continuation)
            elif answer == QMessageBox.StandardButton.Discard:
                self.discard()
                continuation()
        else:
            continuation()

    def load_save(self, path):
        if self.busy:
            return
        def start():
            game = self.settings.get('game_dir')
            if not game:
                self.welcome.show()
                self.welcome_text.setText(tr('尚未找到游戏。选择 PBZ 安装位置后，会自动查找存档。'))
                self.status.setText(tr('请先选择游戏位置。'))
                return
            from .core import SaveSession
            self.preparing = True
            self.welcome.show()
            self._prepare_cancel = threading.Event()
            self._pending_save = str(path)
            self.retry_button.hide()
            def prepare_and_load():
                generated = None
                if not self._custom_catalog:
                    from .bootstrap import prepare_data
                    generated = prepare_data(game, local_dir(), self.preparation_progress.emit, self._prepare_cancel)
                if self._prepare_cancel.is_set():
                    raise InterruptedError(tr('已取消准备'))
                self.preparation_progress.emit(tr('正在读取所选进度…'))
                return generated, SaveSession.open(path, game, self.settings.get('oodle_path') or None)
            def loaded(result):
                generated, session = result
                if generated:
                    self.load_catalog(generated)
                self.accept_session(session)
            self.run_task(tr('正在准备本机游戏数据…'), prepare_and_load, loaded)
        self._resolve_changes(start)

    def accept_session(self, session):
        self.session = session
        self.welcome.hide()
        self.save_file_label.setText(session.save_path.name)
        self.save_file_label.setToolTip(str(session.save_path))
        self.settings['last_save'] = str(session.save_path)
        save_settings(self.settings)
        self._fill_slots(session.current_slot)
        self._new_draft(self.slot_combo.currentData())
        self._ensure_icons()
        self.status.setText(tr('左侧添加物品，右侧调整背包；完成后点击右上角保存。'))
        self.session_loaded.emit()

    def _fill_slots(self, selected):
        session = self.session
        self.slot_combo.blockSignals(True)
        self.slot_combo.clear()
        for slot in session.slots:
            name = slot['name']
            prefix = tr('自动存档') if name.startswith('Auto') else tr('手动存档') if name.startswith('Manual') else name
            suffix = name.removeprefix('AutoSave').removeprefix('ManualSave')
            if suffix.isdigit():
                prefix += ' ' + suffix
            if name == session.current_slot:
                prefix += tr('（继续游戏）')
            self.slot_combo.addItem(f"{prefix} · {slot.get('date', '')}", name)
        self.slot_combo.setCurrentIndex(max(0, self.slot_combo.findData(selected)))
        self.slot_combo.blockSignals(False)

    def _new_draft(self, slot):
        self.draft = InventoryDraft(self.session, slot, self.items)
        self.bag_selected = None
        self._set_slot_info(slot)
        self.refresh_all()

    def _set_slot_info(self, slot):
        data = next((s for s in self.session.slots if s['name'] == slot), {})
        level = data.get('level', '')
        map_id = level.removeprefix('PBZ_').removesuffix('_WP').casefold()
        region = next((r for r in (self.catalog.metadata.get('region_definitions', []) if self.catalog else [])
                       if map_id in [m.casefold() for m in r.get('map_ids', [])]), {})
        place = (region.get('name_en') or level) if language() == 'en-US' else region.get('name', level)
        seconds = int(data.get('playtime', 0))
        self.slot_info.setText(tr('{0} · {1} 小时 {2} 分钟', place, seconds // 3600, seconds % 3600 // 60).strip(' ·'))

    def slot_name(self):
        return self.draft.slot if self.draft else self.slot_combo.currentData() or ''

    def _slot_changed(self, *_):
        if not self.session:
            return
        target = self.slot_combo.currentData()
        if not target or self.draft and target == self.draft.slot:
            return
        if self.draft:
            self.slot_combo.blockSignals(True)
            self.slot_combo.setCurrentIndex(self.slot_combo.findData(self.draft.slot))
            self.slot_combo.blockSignals(False)
        def change():
            self.slot_combo.blockSignals(True)
            self.slot_combo.setCurrentIndex(self.slot_combo.findData(target))
            self.slot_combo.blockSignals(False)
            self._new_draft(target)
        self._resolve_changes(change)

    def choose_save(self):
        path, _ = QFileDialog.getOpenFileName(self, tr('打开 PBZ 存档'), self.settings.get('last_save', ''), 'PBZ save (*.sav)')
        if path:
            self.load_save(path)

    def choose_game(self):
        selected = QFileDialog.getExistingDirectory(self, tr('选择 PBZ 游戏安装目录'), self.settings.get('game_dir', ''))
        if not selected:
            return
        game = resolve_game(selected)
        if not game:
            self.status.setText(tr('这个文件夹中未找到 PBZ，请选择 Steam“浏览本地文件”打开的目录。'))
            return
        def accept():
            self.settings['game_dir'] = str(game)
            save_settings(self.settings)
            saves = find_saves(game)
            if len(saves) == 1:
                self.load_save(str(saves[0]))
            elif saves:
                self.choose_detected_save(saves)
            else:
                self.status.setText(tr('已找到游戏，尚未找到进度。可点击“打开存档”手动选择。'))
        self._resolve_changes(accept)

    def choose_detected_save(self, saves):
        if self.busy:
            return
        game = self.settings.get('game_dir')
        def inspect():
            from .core import SaveSession
            result = []
            for path in saves:
                try:
                    session = SaveSession.open(path, game)
                    current = next((s for s in session.slots if s['name'] == session.current_slot), {})
                    result.append((str(path), current, None))
                except (ValueError, OSError) as error:
                    result.append((str(path), {}, str(error)))
            return result
        def choose(result):
            dialog = QDialog(self)
            dialog.setWindowTitle(tr('选择要编辑的进度'))
            dialog.resize(680, 400)
            layout = QVBoxLayout(dialog)
            layout.addWidget(label(tr('找到多个账号存档，请确认要编辑的进度。'), 'section'))
            rows = QListWidget()
            for path, slot, error in result:
                seconds = int(slot.get('playtime', 0))
                place = {'PBZ_Pangzhen_WP': tr('庞镇'), 'PBZ_Zhengdaodaying_WP': tr('正道大营')}.get(slot.get('level'), slot.get('level', ''))
                row = QListWidgetItem(tr('账号 {0} · {1} · {2} 小时 {3} 分钟\n{4}', Path(path).parent.name, place, seconds // 3600, seconds % 3600 // 60, slot.get('date', '')) if not error else tr('无法读取：{0}\n{1}', Path(path).parent.name, error))
                if not error:
                    row.setData(Qt.ItemDataRole.UserRole, path)
                rows.addItem(row)
            layout.addWidget(rows)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            rows.itemDoubleClicked.connect(lambda _: dialog.accept())
            layout.addWidget(buttons)
            if dialog.exec() == QDialog.DialogCode.Accepted and rows.currentItem():
                path = rows.currentItem().data(Qt.ItemDataRole.UserRole)
                if path:
                    self.load_save(path)
        self.run_task(tr('正在读取各账号的进度信息…'), inspect, choose)

    def retry_preparation(self):
        if self._pending_save:
            self.load_save(self._pending_save)
        else:
            self.choose_game()

    def cancel_preparation(self):
        self._prepare_cancel.set()
        self.status.setText(tr('正在取消准备…'))

    def show_settings(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.settings.update(game_dir=dialog.game.text().strip(), oodle_path=dialog.oodle.text().strip())
            save_settings(self.settings)

    @staticmethod
    def _matches(item, query):
        return query in ' '.join(str(item.get(k, '')) for k in ('id', 'name', 'name_zh', 'name_en', 'description', 'description_zh', 'description_en', 'region_names', 'region_names_en')).casefold()

    def _category_selected(self, row):
        if row >= 0:
            category = self.category_list.item(row).data(Qt.ItemDataRole.UserRole)
            self.category_filter.setCurrentIndex(self.category_filter.findData(category))

    def _category_changed(self, *_):
        category = self.category_filter.currentData()
        self.category_list.blockSignals(True)
        for row in range(self.category_list.count()):
            if self.category_list.item(row).data(Qt.ItemDataRole.UserRole) == category:
                self.category_list.setCurrentRow(row)
                break
        self.category_list.blockSignals(False)
        if hasattr(self, 'catalog_heading'):
            self.catalog_heading.setText(tr(CATEGORIES.get(category, tr('全部物品'))))
        self.refresh_items()

    def _refresh_category_counts(self):
        items = [i for i in self.items if i.get('supported', True) or self.internal_action.isChecked()]
        for row in range(self.category_list.count()):
            entry = self.category_list.item(row)
            category = entry.data(Qt.ItemDataRole.UserRole)
            count = len(items) if category == 'all' else sum(i['category'] == category for i in items)
            entry.setText(f'{tr(CATEGORIES[category])}   {count}')

    def filtered_items(self):
        query, category = self.search.text().strip().casefold(), self.category_filter.currentData()
        return sorted([i for i in self.items if (category == 'all' or i['category'] == category)
            and (i.get('supported', True) or self.internal_action.isChecked())
            and matches_region(i, self.region_filter.currentData()) and self._matches(i, query)],
            key=lambda i: (bool(i.get('experimental')),
                           not bool(i.get('name_en')) if language() == 'en-US' else
                           not any('\u4e00' <= c <= '\u9fff' for c in i.get('name', '')),
                           i.get('name', i['id']).casefold(), i['id']))

    def refresh_all(self, *_):
        self.refresh_items()
        self.refresh_bag()
        self._update_actions()

    def refresh_items(self, *_):
        if not hasattr(self, 'item_table'):
            return
        self._refresh_category_counts()
        selected = item_key(self.selected_item) if self.selected_item else None
        self.visible_items = self.filtered_items()
        self.item_table.blockSignals(True)
        self.item_table.setRowCount(len(self.visible_items))
        selected_row = -1
        for row, item in enumerate(self.visible_items):
            key = item_key(item)
            record = self.owned.get(key)
            state = str(record['quantity']) if record and key[0] == 'goods' else tr('已拥有') if record else '—'
            name = item.get('name', item['id'])
            if self.internal_action.isChecked():
                name += '\n' + item['id']
            table_category = tr('道具') if language() == 'en-US' and item['category'] == 'goods' else tr(CATEGORIES[item['category']])
            for col, text in enumerate([name, table_category, state]):
                cell = QTableWidgetItem(text)
                cell.setToolTip(item['id'])
                if col == 0:
                    cell.setIcon(self._item_icon(key))
                else:
                    cell.setForeground(QColor('#707070'))
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.item_table.setItem(row, col, cell)
            if key == selected:
                selected_row = row
        self.item_table.clearSelection()
        self.item_table.setCurrentCell(-1, -1)
        if selected_row >= 0:
            self.item_table.selectRow(selected_row)
        self.item_table.blockSignals(False)
        self.catalog_summary.setText(tr('{0} 项', len(self.visible_items)))
        self._item_selected()

    def _item_selected(self):
        row = self.item_table.currentRow()
        self.selected_item = self.visible_items[row] if 0 <= row < len(self.visible_items) else None
        if self.selected_item:
            item = self.selected_item
            self.detail_name.setText(item.get('name', item['id']))
            description = item.get('description') or tr('游戏物品表中未提供说明。')
            self.detail_description.setText(description)
            self.detail_description.setToolTip(description)
            note = tr('区域：') + (' / '.join(item.get('region_names', [])) or tr('待确认'))
            if item.get('region_association_only'):
                note += tr(' · 具体获取地点待核实')
            if item.get('supported') is False:
                note += '\n' + tr(item.get('unsupported_reason', '此条目仅供浏览'))
            elif item['category'] == 'appearance':
                note += tr('\n完整服装 · 保存时解锁全局衣柜')
            elif item.get('experimental'):
                note += tr('\n内部条目 · 游戏内效果未验证')
            self.detail_note.setText(note)
        else:
            self.detail_name.setText(tr('选择物品，添加到右侧背包'))
            self.detail_description.setText(tr('可按名称搜索，也可按地图区域查找。'))
            self.detail_note.clear()
        self._update_actions()
        self._show_preview(self.library_preview, item_key(self.selected_item) if self.selected_item else None)

    def _definition(self, key):
        if key in self.item_lookup:
            return self.item_lookup[key]
        item = self.owned.get(key) or self.draft.original.get(key)
        return {'category': key[0], 'id': item['id'], 'name': item['id']}

    def refresh_bag(self, *_):
        if not hasattr(self, 'bag_table'):
            return
        query = self.bag_search.text().strip().casefold()
        keys = self.owned.keys() | (self.draft.changed_keys if self.draft else set())
        keys = [k for k in keys if (k[0] != 'goods' or self.owned.get(k, {}).get('quantity', 0) > 0 or k in self.draft.changed_keys)
                and self._matches(self._definition(k), query)]
        self.bag_rows = sorted(keys, key=lambda k: (tr(CATEGORIES[k[0]]), self._definition(k).get('name', k[1])))
        self.bag_table.blockSignals(True)
        self.bag_table.setRowCount(len(keys))
        selected_row = -1
        changed = self.draft.changed_keys if self.draft else set()
        for row, key in enumerate(self.bag_rows):
            item, record = self._definition(key), self.owned.get(key)
            state = tr('已移出') if not record else tr('新增') if key not in self.draft.original else tr('已修改') if key in changed else ''
            value = '—' if not record else f"+{record['level']}" if key[0] in ('weapon', 'phantom', 'accessory') else tr('{0} 次', record['quantity']) if key[0] == 'medicine' else tr('已解锁') if key[0] == 'appearance' else str(record['quantity'])
            for col, text in enumerate([item.get('name', item['id']), value, state, item['id']]):
                cell = QTableWidgetItem(text)
                cell.setToolTip(item['id'])
                if col == 0:
                    cell.setIcon(self._item_icon(key))
                elif col in (1, 2):
                    cell.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    cell.setForeground(QColor('#707070'))
                if key in changed:
                    cell.setBackground(QColor('#e5f1fb'))
                    cell.setForeground(QColor('#115ea3'))
                self.bag_table.setItem(row, col, cell)
            if key == self.bag_selected:
                selected_row = row
        self.bag_table.setColumnHidden(3, not self.internal_action.isChecked())
        self.bag_table.clearSelection()
        self.bag_table.setCurrentCell(-1, -1)
        if selected_row >= 0:
            self.bag_table.selectRow(selected_row)
            self.bag_table.scrollToItem(self.bag_table.item(selected_row, 0))
        self.bag_table.blockSignals(False)
        self.bag_summary.setText(tr('{0} 项', len(keys)))
        self._bag_selection_changed()

    def _bag_selection_changed(self):
        row = self.bag_table.currentRow()
        self.bag_selected = self.bag_rows[row] if 0 <= row < len(self.bag_rows) else None
        self.show_bag_item()

    def show_bag_item(self):
        self._typing = False
        key = self.bag_selected
        record = self.owned.get(key)
        self._show_preview(self.backpack_preview, key)
        self.editor.setVisible(bool(record) and key[0] != 'appearance')
        if not record:
            self.bag_name.setText(tr('已移出背包 · 可撤销') if key else tr('选择背包中的物品即可修改'))
            self.bag_note.setText(tr('保存前，可随时撤销修改。'))
            self._update_actions()
            return
        item = self._definition(key)
        self.bag_name.setText(item.get('name', item['id']))
        for widget in (self.quantity_spin, self.level_spin, self.medicine_slot):
            widget.blockSignals(True)
        medicine = key[0] == 'medicine'
        self.quantity_label.setText(tr('剩余次数') if medicine else tr('目标数量'))
        for w in (self.quantity_label, self.quantity_field):
            w.setVisible(key[0] in ('goods', 'medicine'))
        for w in (self.level_label, self.level_field):
            w.setVisible(key[0] in LEVELED)
        for w in (self.medicine_slot_label, self.medicine_slot):
            w.setVisible(medicine)
        self.quantity_spin.setValue(record['quantity'])
        maximum = item.get('max_level')
        self.level_spin.setMaximum(max(record['level'], int(maximum) if maximum is not None else 999))
        self.level_spin.setValue(record['level'])
        slots = record.get('equipped_slots', [])
        mode = slots[0] if len(slots) == 1 else 'keep' if slots else 'none'
        self.medicine_slot.setCurrentIndex(self.medicine_slot.findData(mode))
        self.quantity_field.setEnabled(not medicine or bool(slots))
        for widget in (self.quantity_spin, self.level_spin, self.medicine_slot):
            widget.blockSignals(False)
        note = tr('游戏载入时可能补满次数；换栏会将被替换药品的次数归零。') if medicine else tr('完整服装解锁属于账号全局衣柜。') if key[0] == 'appearance' else tr('直接改为目标值，完成后点击右上角保存。')
        self.bag_note.setText(note)
        self._update_actions()
        self._reveal_timer.start(0)

    def _stage(self, operation, select=None):
        if not self.draft or self.busy:
            return False
        try:
            changed = self.draft.stage(operation)
        except ValueError as error:
            QMessageBox.warning(self, tr('无法修改'), str(error))
            self.show_bag_item()
            return False
        if select:
            self.bag_selected = select
            if not self._matches(self._definition(select), self.bag_search.text().strip().casefold()):
                self.bag_search.blockSignals(True)
                self.bag_search.clear()
                self.bag_search.blockSignals(False)
        self.refresh_all()
        self.status.setText(tr('背包预览已更新，尚未写入存档。'))
        return changed

    def add_selected(self):
        if not self.selected_item or not self.draft or self.busy or not self.selected_item.get('supported', True):
            return
        item, key = self.selected_item, item_key(self.selected_item)
        if key in self.owned and key[0] in UNIQUE | {'medicine'}:
            self.bag_selected = key
            self.bag_search.clear()
            self.refresh_bag()
            return
        op = {'action': 'add', 'id': item['id'], 'category': key[0], 'name': item.get('name', item['id']),
              'quantity': 0 if key[0] == 'medicine' else 1, 'level': 0}
        if item.get('max_level') is not None:
            op['max_level'] = item['max_level']
        self._stage(op, key)

    def _edit_selected(self, *_):
        key = self.bag_selected
        if not self.draft or not key or key not in self.owned or self.busy or not self.session.can_edit(self.slot_name(), key[0]):
            return
        item = self._definition(key)
        quantity = self.quantity_spin.value() if key[0] in ('goods', 'medicine') else 1
        op = {'action': 'set', 'id': self.owned[key]['id'], 'category': key[0], 'quantity': quantity,
              'level': self.level_spin.value() if key[0] in LEVELED else 0}
        if item.get('max_level') is not None:
            op['max_level'] = item['max_level']
        if key[0] == 'medicine':
            mode = self.medicine_slot.currentData()
            slots = self.owned[key].get('equipped_slots', [])
            op['equip_slot'] = mode
            if mode == 'none':
                op['quantity'] = 0
            elif mode in ('top', 'bottom') and not slots and not quantity:
                op['quantity'] = 1
        self._stage(op, key)

    def complete_quick_hand(self, chapter):
        if not self.draft or self.busy or not self.session:
            return
        if self.session.read_only:
            QMessageBox.warning(self, tr('无法修改'), tr('此存档格式尚未验证写入，当前仅可查看。'))
            return
        labels = {'village': tr('陆家村的妙手小张'), 'estate': tr('戏梦山庄的妙手小张')}
        try:
            changed = self.draft.stage({'action': 'complete_quick_hand', 'chapter': chapter})
        except ValueError as error:
            QMessageBox.warning(self, tr('无法修改'), str(error))
            return
        self.refresh_all()
        if changed:
            self.status.setText(tr('{0}已准备成可以对话交付，尚未写入存档。保存后完全退出再进游戏，到该区域藏身处和他对一次话。游戏会完成这一段；其他区域都交付过时，由游戏解锁妙手拾遗。', labels[chapter]))
        else:
            self.status.setText(tr('{0}已经可以对话交付，没有新的改动。进游戏后到藏身处和他对一次话。', labels[chapter]))

    def clear_soul(self):
        key = self.bag_selected
        if not self.draft or not key or key not in self.owned or self.busy or self.session.read_only:
            return
        item = self.owned[key]
        if _snap_soul(item) in (None, 0, False):
            return
        if self._stage({'action': 'clear_soul', 'id': item['id'], 'category': item['category']}):
            self.status.setText(tr('铸魂已在预览中清除，尚未写入存档。请先完全退出游戏，再点右上角保存。'))

    def remove_selected(self):
        if self.bag_selected and self.bag_selected in self.owned:
            item = self.owned[self.bag_selected]
            self._stage({'action': 'remove', 'id': item['id'], 'category': item['category']})

    def undo(self):
        if self.draft and not self.busy:
            if self._typing:
                before = self.draft.view
                self._finish_edit()
                if self.draft.view is before:
                    self.show_bag_item()
                    return
            self.draft.undo()
            self.refresh_all()
            self.status.setText(tr('已撤销最近一次修改，存档未写入。'))

    def discard(self):
        if self.draft and not self.busy:
            self._typing = False
            self.draft.discard()
            self.refresh_all()
            self.status.setText(tr('已恢复为上次保存的背包。'))

    def _update_actions(self):
        if not hasattr(self, 'add_button'):
            return
        ready = bool(self.draft and not self.busy)
        changed = set(self.draft.changed_keys) if self.draft else set()
        if self._typing and self.bag_selected:
            changed.add(self.bag_selected)
        count = len(changed) + (len(self.draft.quest_ops) if self.draft else 0)
        self.changes_label.setText(tr('有 {0} 处修改', count) if count else tr('已保存') if self.draft else tr('尚未打开存档'))
        dirty = bool(count)
        if self.changes_label.property('dirty') != dirty:
            self.changes_label.setProperty('dirty', dirty)
            self.changes_label.style().unpolish(self.changes_label)
            self.changes_label.style().polish(self.changes_label)
        self.apply_button.setEnabled(ready and bool(count))
        quick_hand_ready = ready and bool(self.session) and not self.session.read_only
        if hasattr(self, 'village_quick_hand'):
            self.village_quick_hand.setEnabled(quick_hand_ready)
            self.estate_quick_hand.setEnabled(quick_hand_ready)
        self.undo_button.setEnabled(ready and bool(self.draft.history or self._typing))
        item = self.selected_item
        present = bool(item and item_key(item) in self.owned and item['category'] in UNIQUE | {'medicine'})
        self.add_button.setText(tr('在背包中查看') if present else tr('添加到背包'))
        selected_editable = bool(ready and item and self.session.can_edit(self.slot_name(), item['category']))
        self.add_button.setEnabled(ready and bool(item and item.get('supported', True)) and (present or selected_editable))
        bag_editable = bool(ready and self.bag_selected and self.session.can_edit(self.slot_name(), self.bag_selected[0]))
        selected = self.owned.get(self.bag_selected) if ready and self.bag_selected else None
        self.clear_soul_button.setEnabled(bool(
            selected and not self.session.read_only and _snap_soul(selected) not in (None, 0, False)))
        self.remove_button.setEnabled(bag_editable and self.bag_selected in self.owned)
        self.editor.setEnabled(bag_editable)
        self.advanced_action.setEnabled(ready and any(self.session.can_edit(self.slot_name(), c) for c in CATEGORIES if c != 'all'))
        if ready and self.bag_selected and not bag_editable:
            issue = self.session.category_issue(self.slot_name(), self.bag_selected[0])
            note = tr(issue) if issue else ''
            if selected and self.bag_selected[0] in ('weapon', 'phantom') and not self.session.read_only:
                original = self.draft.original.get(self.bag_selected)
                if _snap_soul(selected) not in (None, 0, False):
                    note += '\n' + tr('移出仍然不可用。当前物品已铸魂：请先完全退出游戏，点「清除铸魂」，再点右上角保存。保存只改铸魂状态。')
                elif original and _snap_soul(original) not in (None, 0, False) and self.bag_selected in self.draft.changed_keys:
                    note += '\n' + tr('铸魂已在预览中清除，尚未写入存档。请先完全退出游戏，再点右上角保存。')
            self.bag_note.setText(note)
        self._show_compatibility()
        for widget in (self.sidebar, self.library_panel, self.backpack_panel, self.slot_combo,
                       self.open_button, self.more_button, self.backups_button):
            widget.setEnabled(not self.busy)
        self.choose_game_button.setEnabled(not self.busy)
        self.cancel_prepare_button.setVisible(self.preparing and self.busy)
        self.language_combo.setEnabled(not self.busy and not self.icons_loading)

    def _show_compatibility(self):
        if not self.session or not self.draft:
            self.compatibility_label.hide()
            return
        report = self.session.compatibility
        notes = []
        if self.session.read_only:
            notes.append(tr('只读模式：存档格式已变化，已暂停写入。'))
        elif report:
            blocked = [tr(CATEGORIES[c]) for c in CATEGORIES if c != 'all'
                       and not self.session.can_edit(self.slot_name(), c)]
            if blocked:
                notes.append(tr('部分功能仅可查看：{0}。', ' / '.join(blocked)))
            if report['executable'] != 'reference' or report.get('structural_categories'):
                notes.append(tr('存档格式检查通过 · 此游戏版本尚未经游戏回读验证。'))
            notes.extend(tr(value) for value in report.get('warnings', []))
        self.compatibility_label.setText('\n'.join(notes))
        self.compatibility_label.setVisible(bool(notes))

    def apply_save(self, _=False, after=None):
        if self.busy or not self.draft:
            return
        self._finish_edit()
        if not self._unsaved():
            if after:
                after()
            return
        try:
            operations = self.draft.validated_operations()
        except ValueError as error:
            QMessageBox.warning(self, tr('无法保存'), str(error))
            return
        session, slot = self.session, self.slot_name()
        selected = self.bag_selected
        def saved(result):
            self._new_draft(slot)
            self.bag_selected = selected
            self.refresh_bag()
            self.status.setText(tr('背包已保存，已自动备份。'))
            if after:
                after()
        self.run_task(tr('正在自动备份并保存背包…'), lambda: session.apply(slot, operations), saved)

    def export_save(self):
        if not self.draft or self.busy:
            return
        self._finish_edit()
        try:
            operations = self.draft.validated_operations()
        except ValueError as error:
            QMessageBox.warning(self, tr('无法导出'), str(error))
            return
        path, _ = QFileDialog.getSaveFileName(self, tr('另存为存档副本'), str(local_dir() / 'exports/PBZSaveGame.edited.sav'), 'PBZ save (*.sav)')
        if path:
            session, slot = self.session, self.slot_name()
            self.run_task(tr('正在导出背包副本…'), lambda: session.export(slot, operations, path),
                          lambda result: self.status.setText(tr('副本已导出，当前源文件未写入。')))

    def advanced_add(self):
        if not self.draft:
            return
        dialog = AdvancedDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        ident, category = dialog.item_id.text().strip(), dialog.category.currentData()
        key = category, ident.casefold()
        definition = self.item_lookup.get(key, {})
        if not ident or definition.get('supported') is False:
            self.status.setText(tr('该条目不支持添加。'))
            return
        op = {'id': definition.get('id', ident), 'category': category, 'action': 'add',
              'quantity': dialog.quantity.value(), 'level': dialog.level.value()}
        if definition.get('max_level') is not None:
            op['max_level'] = definition['max_level']
        if category == 'medicine':
            op['equip_slot'] = dialog.medicine_slot.currentData()
        self._stage(op, key)

    def show_backups(self):
        root = local_dir() / 'backups'
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('备份与恢复'))
        dialog.resize(720, 430)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(tr('每次保存前自动备份。恢复前会核对目标文件并保留当前版本。'), '', True))
        layout.addWidget(label(tr('恢复目标：{0}', self.session.save_path) if self.session else tr('请先打开要恢复的存档。'), 'muted', True))
        items = QListWidget()
        backups = []
        if self.session and root.exists():
            for candidate in root.rglob('*.sav'):
                try:
                    metadata = json.loads(candidate.with_suffix('.json').read_text(encoding='utf8'))
                    if Path(metadata['source_path']).resolve() == self.session.save_path:
                        backups.append(candidate)
                except (OSError, ValueError, KeyError):
                    continue
        backups.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for path in backups:
            try:
                metadata = json.loads(path.with_suffix('.json').read_text(encoding='utf8'))
                title = metadata.get('created_at', path.stem)
            except (OSError, ValueError):
                title = path.stem
            item = QListWidgetItem(tr('保存前备份 · {0}', title))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            items.addItem(item)
        if backups:
            items.setCurrentRow(0)
        layout.addWidget(items)
        row = QHBoxLayout()
        row.addWidget(button(tr('关闭'), dialog.reject))
        def open_folder():
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            root.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(root.resolve())))
        row.addWidget(button(tr('打开备份目录'), open_folder))
        row.addStretch()
        def restore():
            selected = items.currentItem()
            if not self.session or not selected:
                return
            path = selected.data(Qt.ItemDataRole.UserRole)
            dialog.accept()
            def start():
                self.run_task(tr('正在恢复备份…'), lambda: self.session.restore(path),
                    lambda result: (self._new_draft(self.slot_name()), self.status.setText(tr('备份已恢复。'))))
            self._resolve_changes(start)
        restore_button = button(tr('恢复所选备份'), restore)
        restore_button.setEnabled(bool(backups and self.session and not self.session.read_only))
        row.addWidget(restore_button)
        layout.addLayout(row)
        dialog.exec()

    def _numeric_input_edited(self, *_):
        self._typing = True
        self._update_actions()

    def _finish_edit(self):
        if not self.busy:
            self.quantity_spin.interpretText()
            self.level_spin.interpretText()
            self._typing = False
            self._update_actions()

    def closeEvent(self, event):
        self._finish_edit()
        if self.icons_loading and not self.busy:
            self._icon_cancel.set()
            if not self.pool.waitForDone(5000):
                self.status.setText(tr('正在结束图标准备，请稍后再关闭。'))
                event.ignore()
                return
        if self.busy:
            event.ignore()
            self.status.setText(tr('正在处理存档，请稍候再关闭。'))
        elif self._unsaved():
            answer = QMessageBox.question(self, tr('背包尚未保存'), tr('保存当前背包的修改吗？'),
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel)
            if answer == QMessageBox.StandardButton.Discard:
                self.draft.discard()
                event.accept()
            elif answer == QMessageBox.StandardButton.Save:
                event.ignore()
                self.apply_save(after=self.close)
            else:
                event.ignore()
        else:
            event.accept()

