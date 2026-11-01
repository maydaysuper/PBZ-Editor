"""Prepare catalog and region data from a local PBZ installation."""
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Keep parser helpers importable for optional local integration tests.
from pbz_editor.catalog_builder import TABLES, table_rows, item_metadata, plain_text, normalize_name
from pbz_editor.bootstrap import prepare_data
from pbz_editor.paths import local_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-dir', required=True, type=Path)
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    print(prepare_data(args.game_dir, args.data_dir or local_dir(), print))


if __name__ == '__main__': main()
