"""PO Dashboard: a local-first purchasing dashboard."""

import os

__version__ = "0.0.1"

# openpyxl uses lxml when it is installed (python-docx brings it), and lxml's parser quietly ignores a hostile DOCTYPE
# instead of refusing the file. Pin openpyxl to the defusedxml path, which refuses it. This must run before openpyxl is
# first imported, so it lives here; a test checks it (DEC-51, PD-3.6).
os.environ["OPENPYXL_LXML"] = "False"
