# Third-party components

PBZ Editor's own source is distributed under the MIT license in `LICENSE`.
Game data remains the property of its respective authors. This repository does
not distribute game archives, images, personal saves or proprietary Oodle DLLs.

The Windows application uses these separately licensed components:

| Component | Upstream | License information |
|---|---|---|
| Python | https://www.python.org/ | Python Software Foundation license and bundled notices |
| PySide6, Shiboken and Qt | https://www.qt.io/qt-for-python | The installed package metadata lists LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only; Qt modules also carry their own notices |
| cryptography | https://github.com/pyca/cryptography | Apache-2.0 OR BSD-3-Clause; wheels include additional OpenSSL/Rust dependency notices |
| cffi | https://cffi.readthedocs.io/ | Installed package license metadata and included license text |
| pycparser | https://github.com/eliben/pycparser | BSD-3-Clause |
| texture2ddecoder | https://github.com/K0lb3/texture2ddecoder | MIT; bundled BCn decoder derives from Perfare/AssetStudio under MIT |
| PyInstaller, build tool | https://pyinstaller.org/ | GPL with the upstream bootloader exception; its own license text is retained |

The release builder copies the license and notice files found in installed
package metadata into `LICENSES/` and records the actual package versions in
`release-manifest.json`. Read those texts for the exact terms of the components
you distribute. Qt's upstream source and license texts are available from
https://code.qt.io/ and https://code.qt.io/cgit/pyside/pyside-setup.git/tree/LICENSES.
The standard LGPL-3.0 and GPL-3.0 texts in `legal/` were obtained from Qt for
Python's official upstream `pyside/pyside-setup` repository:
https://github.com/pyside/pyside-setup/tree/dev/LICENSES;
the release builder retains them alongside the package-provided notices.

The default decoder is oozextract 0.5.7, declared MIT by its upstream
https://github.com/lvlvllvlvllvlvl/oozextract. Its published source is retained
in native/vendor/oozextract with the local dependency-only change documented
in native/vendor/NOTICE.md. Rust dependency licenses and standard-library
notices are included in LICENSES/native in the runtime archive.
The separately compiled pbz_codec.dll uses that decoder and a bounded C ABI.
Writes use standard Oodle uncompressed stored blocks; no proprietary encoder
or decoder DLL is shipped or required. An explicit external-library setting
remains available for compatibility and independent verification.

Item thumbnails are read from each user's installed game into the user-local icons cache.
They remain game assets and are excluded from source distributions and clean
release archives. The narrow Zen texture parser was independently implemented
with the public CUE4Parse format structures as documentation; no CUE4Parse code
or game image is bundled.
