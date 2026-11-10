# PyInstaller spec. Built by `packaging/build.py`, not by hand.
#
# One directory rather than one file, for File Manager's reason: a onefile build
# unpacks itself on every launch, and the service starts this exe at every boot.
# The installer packs the folder into one setup exe.

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

ROOT = os.path.dirname(SPECPATH)
sys.path.insert(0, ROOT)

from app import __version__  # noqa: E402

PARTS = tuple(int(part) for part in (__version__.split("-")[0].split(".") + ["0", "0", "0"])[:4])
VERSION_RESOURCE = os.path.join(SPECPATH, "version_info.txt")
with open(VERSION_RESOURCE, "w", encoding="utf-8") as handle:
    handle.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={PARTS}, prodvers={PARTS}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Robbuie'),
      StringStruct('FileDescription', 'PLC Monitor'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'PLCMonitor'),
      StringStruct('LegalCopyright', 'Copyright (c) 2026 Robbuie. All rights reserved.'),
      StringStruct('OriginalFilename', 'PLCMonitor.exe'),
      StringStruct('ProductName', 'PLC Monitor'),
      StringStruct('ProductVersion', '{__version__}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])]),
  ],
)
""")

# scapy loads its layers by name, so PyInstaller cannot see them.
scapy_datas, scapy_binaries, scapy_hidden = collect_all("scapy")

binaries = list(scapy_binaries) + collect_dynamic_libs("snap7")
# A plctag.dll beside the repository is shipped if present; otherwise the Tags
# page's Download fetches one into the data folder, where updates leave it alone.
for name in ("plctag.dll",):
    candidate = os.path.join(ROOT, name)
    if os.path.isfile(candidate):
        binaries.append((candidate, "."))

datas = list(scapy_datas) + collect_data_files("pycomm3") + [
    (os.path.join(ROOT, "README.md"), "."),
    (os.path.join(ROOT, "assets", "icon.png"), "assets"),
]

hidden = scapy_hidden + [
    "app.service", "app.engine.headless",
    # pywin32: the service host, and the timezone module servicemanager wants.
    "win32serviceutil", "win32service", "win32event", "servicemanager",
    "pywintypes", "win32timezone",
    # Drivers, imported inside functions so a missing one only disables itself.
    "pycomm3", "snap7", "snap7.client", "snap7.util", "pymcprotocol",
    # Found by name at run time (python-snap7 moved its area enum between
    # versions), so the import graph cannot see it.
    "snap7.type",
    "app.engine.profiles", "app.engine.tagimport", "app.engine.clock", "app.ui.tagpicker",
    "psutil", "matplotlib.backends.backend_qtagg",
]

EXCLUDE = [
    # Not "unittest": pyparsing imports it when it loads, and matplotlib loads
    # pyparsing, so excluding it took the Trend and Latency charts out of the
    # build ("No module named 'unittest'"). tests/test_packaging.py checks
    # every runtime import still works with this list taken away.
    "tkinter", "customtkinter", "pytest", "_pytest", "pydoc",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DRender", "PySide6.QtBluetooth", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtDesigner", "PySide6.QtHelp",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtNfc",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtPositioning", "PySide6.QtQml",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors", "PySide6.QtSerialBus",
    "PySide6.QtSerialPort", "PySide6.QtSpatialAudio", "PySide6.QtSql", "PySide6.QtStateMachine",
    "PySide6.QtTest", "PySide6.QtTextToSpeech", "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
]

analysis = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=EXCLUDE,
    noarchive=False,
)

pyz = PYZ(analysis.pure)

exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="PLCMonitor",
    icon=os.path.join(SPECPATH, "icon.ico"),
    debug=False,
    strip=False,
    upx=False,
    console=False,
    version=VERSION_RESOURCE,
)

collect = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="PLCMonitor")
