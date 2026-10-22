# PyInstaller build recipe:  pyinstaller LocalConvert.spec
# Produces dist/LocalConvert.app on macOS and dist/LocalConvert/LocalConvert.exe on Windows.
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

datas = [("localconvert/web", "localconvert/web")]
binaries, hiddenimports = [], ["localconvert.converters"]
for pkg in ("pypdfium2", "pypdfium2_raw", "imageio_ffmpeg", "pillow_heif", "reportlab", "webview"):
    try:
        d, b, h = collect_all(pkg)
        datas += d; binaries += b; hiddenimports += h
    except Exception:
        pass
for pkg in ("docx", "pptx", "openpyxl"):
    datas += collect_data_files(pkg)

a = Analysis(["run.py"], datas=datas, binaries=binaries, hiddenimports=hiddenimports,
             excludes=["tkinter", "matplotlib", "numpy.tests", "PyQt5", "PyQt6", "PySide6"])
pyz = PYZ(a.pure)
icon = "assets/icon.icns" if sys.platform == "darwin" else "assets/icon.ico"
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LocalConvert", console=False,
          icon=icon, argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.datas, name="LocalConvert")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="LocalConvert.app", icon=icon, bundle_identifier="app.localconvert",
                 info_plist={"NSHighResolutionCapable": True, "CFBundleShortVersionString": "0.1.0",
                             "LSApplicationCategoryType": "public.app-category.productivity"})
