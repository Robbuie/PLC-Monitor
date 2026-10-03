"""Install and control the service when running from source.

    python windows_service.py --startup auto install
    python windows_service.py start | stop | remove | debug

Needs pywin32 (and, once, `python -m pywin32_postinstall -install` as
administrator). The installed build does not use this file: it registers
`PLCMonitor.exe service` instead, from the Service page. Both run the same
class, `app.service.PLCMonitorService`.
"""

import sys

from app.service import HAS_WIN32

if __name__ == "__main__":
    if not HAS_WIN32:
        print("pywin32 is not installed:  pip install pywin32")
        print("then, as administrator:    python -m pywin32_postinstall -install")
        sys.exit(1)
    import win32serviceutil

    from app.service import PLCMonitorService
    win32serviceutil.HandleCommandLine(PLCMonitorService)
