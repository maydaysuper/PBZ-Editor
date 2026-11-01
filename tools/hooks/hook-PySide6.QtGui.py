"""Keep Qt's standard dependency discovery, excluding unused optional GUI plugins."""
from pathlib import Path
from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)

# This editor is a Widgets application: it does not render PDFs, accept TUIO
# devices or display Qt Virtual Keyboard. Exclude their plugins before
# PyInstaller's binary dependency analysis would pull in Pdf, Quick and Qml.
unused_plugins = {"qpdf.dll", "qtvirtualkeyboardplugin.dll", "qtuiotouchplugin.dll"}
binaries = [(source, destination) for source, destination in binaries
            if Path(source).name.lower() not in unused_plugins]
