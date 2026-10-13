#!/bin/bash
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then exec .venv/bin/python app.py; fi
python3 -c 'import openpyxl' 2>/dev/null || python3 -m pip install -r requirements.txt
exec python3 app.py
