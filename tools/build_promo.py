"""Render a small, editable sharing card from a public DEMO UI screenshot."""
import base64
import argparse
import os
from pathlib import Path
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication
from pbz_editor.widgets import configure_application


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--language', choices=['zh-CN', 'en-US'], default='zh-CN')
    args = parser.parse_args()
    english = args.language == 'en-US'
    copy = {
        'headline': 'Get your missing gear back.' if english else '错过的装备，补回来。',
        'steps': 'Find → Adjust → Save' if english else '找物品 → 调数值 → 保存',
        'languages': 'Chinese / English' if english else '中文 / English',
        'backups': 'Auto backups' if english else '自动备份',
        'subtitle': 'Offline inventory editor' if english else '影之刃零 · 离线物品编辑器',
        'demo': 'DEMO inventory' if english else 'DEMO · 演示数据',
    }
    # Deliberately no arbitrary screenshot argument: only the reviewed public
    # demo screenshot may be embedded, never a current desktop or player save.
    source = ROOT/f'docs/screenshots/02-edit.{args.language}.png'
    picture = base64.b64encode(source.read_bytes()).decode('ascii')
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="1280" height="640" viewBox="0 0 1280 640">
<rect width="1280" height="640" fill="#f7f9fc"/>
<rect x="0" y="0" width="1280" height="8" fill="#0f6cbd"/>
<g font-family="Microsoft YaHei">
<text x="60" y="78" fill="#0f6cbd" font-size="16" font-weight="600" letter-spacing="1">OPEN SOURCE · WINDOWS x64</text>
<text x="56" y="166" fill="#17212f" font-size="66" font-weight="700">PBZ Editor</text>
<text x="60" y="234" fill="#17212f" font-size="30" font-weight="600">{copy['headline']}</text>
<text x="60" y="285" fill="#526274" font-size="22">Phantom Blade Zero save editor</text>
<text x="60" y="362" fill="#25384c" font-size="25">{copy['steps']}</text>
<rect x="60" y="401" width="160" height="40" rx="6" fill="#e5f1fb"/>
<text x="73" y="428" fill="#0f6cbd" font-size="17">{copy['languages']}</text>
<rect x="233" y="401" width="132" height="40" rx="6" fill="#edf0f4"/>
<text x="245" y="428" fill="#41556a" font-size="17">{copy['backups']}</text>
<text x="60" y="505" fill="#526274" font-size="18">{copy['subtitle']}</text>
<text x="60" y="566" fill="#526274" font-size="16">github.com/qry6868/PBZ-Editor</text>
<rect x="579" y="95" width="648" height="432" rx="9" fill="#dce3ec"/>
<rect x="575" y="89" width="648" height="432" rx="9" fill="#ffffff" stroke="#d6dfe9"/>
<image x="583" y="97" width="632" height="416" preserveAspectRatio="xMidYMid meet" xlink:href="data:image/png;base64,{picture}"/>
<text x="1222" y="551" text-anchor="end" fill="#65758a" font-size="14">{copy['demo']}</text>
</g></svg>'''
    output = ROOT/'docs/promotion'
    output.mkdir(parents=True, exist_ok=True)
    stem = 'social-preview.en-US' if english else 'social-preview'
    (output/(stem+'.svg')).write_bytes(svg.encode('utf8'))
    app = QApplication.instance() or QApplication([])
    configure_application(app)
    renderer = QSvgRenderer(svg.encode('utf8'))
    assert renderer.isValid()
    image = QImage(1280, 640, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    path = output/(stem+'.png')
    assert image.save(str(path), 'PNG')
    assert path.stat().st_size < 1024*1024
    print('Sharing card ready: 1280 x 640, below 1 MB, public DEMO data only.')


if __name__ == '__main__':
    main()
