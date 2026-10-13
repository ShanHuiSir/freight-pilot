"""Build current platform only; Windows builds run on Windows in GitHub Actions."""
import subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
subprocess.run([sys.executable,'-m','PyInstaller','--clean','--noconfirm','--onefile','--name','FreightPilot','--collect-all','openpyxl','--add-data',str(root/'static')+':static','--add-data',str(root/'samples')+':samples',str(root/'app.py')],cwd=root,check=True)
