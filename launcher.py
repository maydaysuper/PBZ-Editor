"""Windowed entry point; startup failures remain available in a local log."""
from __future__ import annotations

import logging
from pathlib import Path
import sys


def application_root() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent


def run() -> int:
    log_file = None
    try:
        from pbz_editor.paths import local_dir
        log_dir = local_dir() / 'logs'
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir/'editor.log'
        logging.basicConfig(filename=log_file, level=logging.INFO, force=True,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s", encoding="utf-8")
        from pbz_editor.app import main
        result = main()
        return int(result) if result is not None else 0
    except Exception:
        logging.exception("Application startup or execution failed")
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication(sys.argv[:1])
            message = f'程序未能启动。详细信息已保存到：\n{log_file}' if log_file else '无法建立用户数据目录。请检查权限，或将完整运行包解压到可写位置后使用 portable.flag。'
            QMessageBox.critical(None, "PBZ Editor 启动失败", message)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(run())
