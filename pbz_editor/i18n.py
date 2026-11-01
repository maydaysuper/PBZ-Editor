"""UI language selection. Save identifiers and stored game values never change."""
import json
import re
import string
from pathlib import Path
from PySide6.QtCore import QLocale, QTranslator, QLibraryInfo
from PySide6.QtWidgets import QApplication

_language = 'zh-CN'
_english = None
_qt_translator = None
_patterns = None


def language():
    return _language


def system_language():
    return 'zh-CN' if QLocale.system().language() == QLocale.Language.Chinese else 'en-US'


def set_language(value):
    global _language, _qt_translator
    if value not in ('zh-CN', 'en-US'):
        raise ValueError('Unsupported UI language')
    _language = value
    app = QApplication.instance()
    if app is None:
        return
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator = None
    if value == 'zh-CN':
        translator = QTranslator(app)
        if translator.load('qtbase_zh_CN', QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(translator)
            _qt_translator = translator


def tr(text, *values):
    global _english
    if _language == 'en-US':
        if _english is None:
            _english = json.loads((Path(__file__).parent/'locales/en-US.json').read_text(encoding='utf8'))
        text = _english.get(text, text)
    return text.format(*values) if values else text


def translate_message(text):
    """Translate backend messages without coupling save code to Qt or UI state."""
    global _patterns
    if language() != 'en-US':
        return text
    direct = tr(text)
    if direct != text:
        return direct
    if _patterns is None:
        _patterns = []
        for source, target in _english.items():
            if '{0}' not in source:
                continue
            expression = ''
            for literal, field, spec, conversion in string.Formatter().parse(source):
                expression += re.escape(literal)
                if field is not None:
                    expression += '(.*?)'
            _patterns.append((re.compile(expression, re.DOTALL), target))
    for expression, target in _patterns:
        match = expression.fullmatch(text)
        if match:
            return target.format(*(translate_message(v) for v in match.groups()))
    # File names and technical exception details are retained verbatim.
    return text


def localized_item(item):
    result = dict(item)
    result['name_zh'] = item.get('name', item['id'])
    result['description_zh'] = item.get('description', '')
    if language() == 'en-US':
        result['name'] = item.get('name_en') or result['name_zh']
        result['description'] = item.get('description_en') or result['description_zh']
        result['region_names'] = item.get('region_names_en') or item.get('region_names', [])
    return result
