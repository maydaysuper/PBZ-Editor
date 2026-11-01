from __future__ import annotations
import argparse
import logging
import sys
import json
import time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from .paths import local_dir
from .i18n import set_language, system_language, tr, language
from .ui import EditorWindow, configure_application
from . import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(description=tr('PBZ Editor · 影之刃零全物品编辑器'))
    parser.add_argument("--game-dir")
    parser.add_argument("--save")
    parser.add_argument("--catalog")
    parser.add_argument("--oodle-dll")
    parser.add_argument('--language', choices=['zh-CN', 'en-US'])
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-empty", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("PBZ Editor")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("PBZ Editor")
    configure_application(app)
    set_language(args.language or system_language())
    try:
        log = local_dir() / 'logs'
        log.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=log/'editor.log', encoding='utf8', level=logging.INFO, force=True,
                            format='%(asctime)s %(levelname)s %(message)s')
    except OSError:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, tr('无法保存程序数据'), tr('当前用户数据目录不可写。请检查权限，或将完整运行包解压到可写文件夹并放入 portable.flag 以使用便携模式。'))
        return 1
    errors = []
    if args.smoke_test:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning = lambda _parent, _title, text, *_rest: errors.append(str(text))
    if args.smoke_empty:
        from . import ui
        ui.find_game = lambda: None
    window = EditorWindow(args.game_dir, args.save, args.catalog, args.oodle_dll, language_code=args.language, auto_language=True)
    window.show()
    if args.smoke_empty:
        def empty_ready():
            valid = window.session is None and window.welcome.isVisible() and window.choose_game_button.isEnabled()
            output = local_dir()/'verification/empty-startup.json'
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps({'ok': valid, 'frozen': bool(getattr(sys, 'frozen', False))}), encoding='utf8')
            window.close()
            app.exit(0 if valid else 1)
        QTimer.singleShot(300, empty_ready)
    if args.smoke_test:
        deadline = time.monotonic() + 35
        ready_at = None
        timer = QTimer(window)
        timer.setInterval(100)
        def check():
            nonlocal ready_at
            if window.busy and time.monotonic() < deadline:
                return
            if window.icons_loading and time.monotonic() < deadline:
                return
            if not window.session and not errors and time.monotonic() < deadline:
                return
            # Native font/layout changes are posted after session/icon signals.
            # Capture a settled frame rather than the first worker completion.
            if ready_at is None:
                ready_at = time.monotonic() + 0.35
                return
            if time.monotonic() < ready_at:
                return
            output = local_dir() / "verification/packaged-ui"
            output.mkdir(parents=True, exist_ok=True)
            valid = window.session is not None and not errors and len(window.items) >= 700
            report = {"ok": valid, "frozen": bool(getattr(sys, "frozen", False)),
                      "language": language(),
                      "catalog_count": len(window.items), "slot": window.slot_name(),
                      "workspace": "backpack-preview", "library_rows": window.item_table.rowCount(),
                      "navigation": "category-sidebar", "category_count": window.category_list.count(),
                      "backpack_rows": window.bag_table.rowCount(),
                      "icon_count": len(window.icon_index['items']) if window.icon_index else 0,
                      "codec_backend": getattr(window.session.container.codec, 'backend', 'explicit-external') if window.session else None,
                      "data_directory": str(local_dir()),
                      "save_path": str(window.session.save_path) if window.session else None,
                      "slot_count": len(window.session.slots) if window.session else 0,
                      "errors": errors, "game_save_modified": False,
                      "game_runtime_verified": False}
            window.grab().save(str(output / "editor.png"))
            (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
            timer.stop()
            window.pool.waitForDone(5000)
            window.close()
            app.exit(0 if valid else 1)
        timer.timeout.connect(check)
        timer.start()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
