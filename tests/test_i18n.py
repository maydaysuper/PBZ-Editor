import ast
import json
from pathlib import Path
import string
import pytest
from pbz_editor import i18n


def test_translations_cover_ui_and_keep_format_fields():
    root = Path(__file__).resolve().parents[1]/'pbz_editor'
    translations = json.loads((root/'locales/en-US.json').read_text(encoding='utf8'))
    keys = []
    for filename in ('ui.py', 'widgets.py', 'app.py'):
        for node in ast.walk(ast.parse((root/filename).read_text(encoding='utf8'))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'tr' and node.args:
                source = node.args[0]
                if isinstance(source, ast.Constant) and isinstance(source.value, str):
                    keys.append(source.value)
    assert set(keys) <= translations.keys()
    formatter = string.Formatter()
    fields = lambda value: sorted(f for _, f, _, _ in formatter.parse(value) if f is not None)
    for source, target in translations.items():
        assert fields(source) == fields(target), source


@pytest.mark.parametrize(('source', 'target'), [
    ('游戏仍在运行。请退出游戏后写入，避免自动保存覆盖修改。', 'The game is running. Exit it before saving changes.'),
    ('未知压缩算法：9。', 'Unknown compression algorithm: 9.'),
    ('存档槽 ManualSave1 中的 JSON 无效。', 'Invalid JSON in save slot ManualSave1.'),
    ('C:/test/file.sav', 'C:/test/file.sav'),
])
def test_backend_messages_translate_without_changing_details(source, target):
    i18n.set_language('en-US')
    try:
        assert i18n.translate_message(source) == target
    finally:
        i18n.set_language('zh-CN')


def test_localized_metadata_preserves_internal_ids_and_chinese_search_text():
    item = {'id': 'Synthetic', 'category': 'goods', 'name': '测试物品', 'description': '测试描述',
            'name_en': 'Test item', 'description_en': 'Test description',
            'region_names': ['测试区域'], 'region_names_en': ['Test area']}
    i18n.set_language('en-US')
    try:
        actual = i18n.localized_item(item)
        assert actual['id'] == item['id'] and actual['category'] == item['category']
        assert actual['name'] == 'Test item' and actual['description'] == 'Test description'
        assert actual['name_zh'] == '测试物品' and actual['region_names'] == ['Test area']
        assert item['name'] == '测试物品'
    finally:
        i18n.set_language('zh-CN')
