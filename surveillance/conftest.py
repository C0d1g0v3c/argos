import sys
from pathlib import Path

# Permite importar detector.py directamente en los tests
sys.path.insert(0, str(Path(__file__).resolve().parent))
