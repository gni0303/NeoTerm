# Third-party notices

`NeoTerm.exe` is a PyInstaller one-file bundle. Besides the NeoTerm code (MIT, see `LICENSE`) it
contains the components listed below, each under its own license. The full license texts are in the
[`licenses/`](licenses) folder of this repository, inside the executable (`licenses\` next to the
program files it unpacks at start-up), and attached to every release as `NeoTerm-licenses.zip`.

## Components in NeoTerm.exe 1.0

| Component | Version | License | License text | Source |
|---|---|---|---|---|
| CPython runtime (incl. its bundled bzip2, libffi, OpenSSL 3, zlib, expat, xz, mpdecimal, Microsoft C runtime) | 3.11.9 | PSF License 2.0; bundled libraries under their own licenses (OpenSSL: Apache 2.0, expat, zlib, libmpdec, bzip2, libffi, ...) | `Python-3.11-PSF.txt`, `Python-3.11-third-party-licenses.rst` | https://www.python.org/downloads/release/python-3119/ |
| pygame | 2.6.1 | LGPL 2.1 | `pygame-LGPL-2.1.txt` | https://github.com/pygame/pygame/tree/2.6.1 |
| SDL2, SDL2_image, SDL2_mixer, SDL2_ttf (inside pygame) | as shipped with pygame 2.6.1 | zlib | `pygame-dep-sdl2*.txt` | https://github.com/libsdl-org |
| FreeType (inside pygame) | as shipped with pygame 2.6.1 | FreeType License | `pygame-dep-freetype.txt` | https://freetype.org |
| libpng, libjpeg, libtiff, libwebp, zlib (inside pygame) | as shipped with pygame 2.6.1 | libpng / IJG / libtiff / BSD / zlib | `pygame-dep-png.txt`, `-jpeg`, `-tiff`, `-webp`, `-zlib` | see the license files |
| libogg, Opus, opusfile, libmodplug, PortMidi (inside pygame) | as shipped with pygame 2.6.1 | BSD / BSD / BSD / public domain / MIT-style | `pygame-dep-ogg-vorbis.txt`, `-opus`, `-opusfile`, `-modplug`, `-portmidi` | see the license files |
| pyte (copyright: see `pyte-AUTHORS.txt`) | 0.8.2 | LGPL 3.0 (+ GPL 3.0 it refers to) | `pyte-LGPL-3.0.txt`, `GPL-3.0.txt`, `pyte-AUTHORS.txt` | https://github.com/selectel/pyte/tree/0.8.2 |
| pyserial | 3.5 | BSD 3-Clause | `pyserial-BSD-3-Clause.txt` | https://github.com/pyserial/pyserial/tree/v3.5 |
| pywinpty | 3.1.2 | MIT | `pywinpty-MIT.txt` | https://github.com/andfoy/pywinpty |
| ConPTY / OpenConsole (inside pywinpty) | as shipped with pywinpty 3.1.2 | MIT | `Microsoft-Terminal-ConPTY-MIT.txt` | https://github.com/microsoft/terminal |
| winpty (inside pywinpty) | as shipped with pywinpty 3.1.2 | MIT | `winpty-MIT.txt` | https://github.com/rprichard/winpty |
| wcwidth | 0.9.5 | MIT | `wcwidth-MIT.txt` | https://github.com/jquast/wcwidth |
| PyInstaller bootloader | 6.22.3 | GPL 2.0 with the bootloader exception (allows any license for the bundled program) | `PyInstaller-COPYING.txt` | https://github.com/pyinstaller/pyinstaller |

All third-party components are used unmodified.

## LGPL components (pygame, pyte): using a modified version

pygame and pyte are LGPL libraries. NeoTerm does not change them, and you may use NeoTerm with a
modified version of either:

- **Run from source:** install your modified pygame / pyte, then run `python neoterm.py`
  (see *Run from source / build* in `README.md`).
- **Rebuild the executable:** with your modified library installed, run `.\build.ps1`. It produces a
  new `dist\NeoTerm.exe` from exactly the same sources (`neoterm.py`, `requirements.txt` with pinned
  versions, `version_info.txt`) that were used for the release.

Inside `NeoTerm.exe` the pygame native libraries are unpacked as separate files at start-up, while
pyte is stored in PyInstaller's compressed module archive; rebuilding as described above is the way to
replace either of them. The source code of the exact library versions is available at the links above
(tags `2.6.1` and `0.8.2`) and on PyPI.

Fonts are not bundled; NeoTerm uses fonts already installed with Windows (Consolas, Malgun Gothic and
optionally Cascadia Mono, Courier New, Lucida Console).
