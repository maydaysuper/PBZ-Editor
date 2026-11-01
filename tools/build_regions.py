"""Build both region and catalog caches directly from the installed game."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pbz_editor.region_builder import u32, fname, header, debug_map
from tools.build_catalog import main

if __name__ == '__main__': main()
