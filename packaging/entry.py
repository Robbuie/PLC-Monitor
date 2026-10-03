"""The frozen entry point. Kept tiny, as File Manager's is: anything that must
happen before Qt is imported happens here, and the application proper stays in
app/__main__.py."""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from app.__main__ import main
    sys.exit(main())
