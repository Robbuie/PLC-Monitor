"""The design system, shared with File Manager, NetControl, DWG Viewer and Redline PDF.

Three independent axes -- theme, accent, density -- kept independent on purpose.
`tokens.py` holds the values (copied from File Manager), `qss.py` derives every
tint in Python, and `sheet.py` renders PLC Monitor's stylesheet by
substitution. Changing any axis re-renders and re-applies the whole sheet.

**No literal colour belongs anywhere outside this package.**
"""
