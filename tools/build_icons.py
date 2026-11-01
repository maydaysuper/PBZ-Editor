"""Generate private thumbnails from a user's installed game; never ship assets."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pbz_editor.catalog import Catalog
from pbz_editor.paths import catalog_path, local_dir
from pbz_editor.icons import build_icons

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--game-dir', required=True)
    parser.add_argument('--catalog', default=str(catalog_path()))
    parser.add_argument('--output', default=str(local_dir() / 'icons'))
    parser.add_argument('--oodle-dll')
    args = parser.parse_args()
    result = build_icons(args.game_dir, Catalog.load(args.catalog), args.output, args.oodle_dll)
    print(json.dumps({'mapped': len(result['items']), 'total': result['total'],
                      'textures': result['texture_count'], 'missing': result['missing']}, ensure_ascii=False))
