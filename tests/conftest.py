"""
conftest.py
-----------
La consola de Windows usa por defecto cp1252, que no puede codificar los
símbolos Unicode (θ, σ, ≈, °) que los tests imprimen con `-s`. Forzar UTF-8
en stdout/stderr evita un UnicodeEncodeError al ejecutar
`pytest -v -s` (como recomienda el README) en una terminal Windows nueva.
"""

import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
