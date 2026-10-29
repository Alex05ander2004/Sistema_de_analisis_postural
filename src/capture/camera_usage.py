"""
camera_usage.py
---------------
¿Qué otras aplicaciones están usando la cámara ahora mismo?

Por qué existe
--------------
Prueba del 2026-10-29: con la app midiendo, otro programa que abría la misma
cámara obtenía 0 de 100 lecturas con imagen. En el equipo de un participante,
la app en segundo plano dejaría NEGRA su cámara en Teams/Zoom/Meet: rompería
sus reuniones y la desinstalaría. La app debe CEDER la cámara.

Cómo se sabe
------------
Windows registra el uso de la cámara por aplicación (es lo que muestra
Configuración → Privacidad → Cámara, "en uso"):

  HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager\\
      ConsentStore\\webcam\\
          NonPackaged\\C:#ruta#a#app.exe     (programas clásicos: Zoom, Chrome)
          <PackageFamilyName>               (apps de la Tienda: Teams nuevo)

Cada clave tiene LastUsedTimeStart / LastUsedTimeStop (FILETIME). Mientras la
app usa la cámara, LastUsedTimeStop vale 0.

LIMITACIÓN: no se pudo verificar en el entorno de desarrollo (los procesos
lanzados desde él tienen el registro virtualizado). Verificarlo en el equipo
real con una videollamada:

    python -m src.capture.camera_usage

debe listar Teams/Zoom mientras la llamada tiene la cámara encendida.

Autor: Tesis Huisa Perez, UNSA 2026
"""

import logging
import sys
import time
from typing import Callable, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)

_BASE = (r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
         r"\ConsentStore\webcam")

# Un uso "en curso" más antiguo que esto se ignora: una app que se cerró de
# golpe puede dejar LastUsedTimeStop=0 para siempre, y la app se quedaría
# pausada indefinidamente.
MAX_IN_USE_HOURS = 12.0

_FILETIME_EPOCH = 116444736000000000   # 1601-01-01 → 1970-01-01, en 100 ns

Entry = Tuple[str, int, int]   # (clave, start FILETIME, stop FILETIME)


def _filetime_to_unix(ft: int) -> float:
    return (ft - _FILETIME_EPOCH) / 1e7


def read_registry_entries() -> List[Entry]:
    """Todas las entradas de uso de cámara del usuario (solo Windows)."""
    if sys.platform != "win32":
        return []
    import winreg

    out: List[Entry] = []

    def _values(key) -> Tuple[Optional[int], Optional[int]]:
        vals = {}
        for name in ("LastUsedTimeStart", "LastUsedTimeStop"):
            try:
                vals[name] = winreg.QueryValueEx(key, name)[0]
            except OSError:
                vals[name] = None
        return vals["LastUsedTimeStart"], vals["LastUsedTimeStop"]

    def _walk(path: str, label_prefix: str = "") -> None:
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        with key:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                if sub == "NonPackaged" and not label_prefix:
                    _walk(path + "\\" + sub, "NonPackaged\\")
                    continue
                try:
                    with winreg.OpenKey(key, sub) as sk:
                        start, stop = _values(sk)
                except OSError:
                    continue
                if start is not None and stop is not None:
                    out.append((label_prefix + sub, int(start), int(stop)))

    _walk(_BASE)
    return out


def friendly_name(key: str) -> str:
    """'NonPackaged\\C:#…#Zoom.exe' → 'Zoom'; 'MSTeams_8wek…' → 'MSTeams'."""
    name = key.split("\\")[-1].split("#")[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    if "_" in name and "#" not in key:
        name = name.split("_")[0]
    return name


def is_own(key: str, own_markers: Iterable[str]) -> bool:
    low = key.lower().replace("#", "\\")
    return any(m and m.lower().replace("#", "\\") in low for m in own_markers)


def others_using_camera(own_markers: Iterable[str],
                        reader: Callable[[], List[Entry]] = read_registry_entries,
                        now: Optional[float] = None) -> List[str]:
    """Nombres de las OTRAS aplicaciones que usan la cámara ahora."""
    now = time.time() if now is None else now
    markers = list(own_markers)
    try:
        entries = reader()
    except Exception:
        logger.debug("No se pudo leer el uso de la cámara.", exc_info=True)
        return []
    names = []
    for key, start, stop in entries:
        if stop != 0 or start <= 0:
            continue
        if now - _filetime_to_unix(start) > MAX_IN_USE_HOURS * 3600:
            continue
        if is_own(key, markers):
            continue
        names.append(friendly_name(key))
    return sorted(set(names))


def own_markers() -> List[str]:
    """Cómo aparece ESTA app en el registro (ejecutable o intérprete)."""
    exe = sys.executable or ""
    return [exe, "MonitorPostural.exe"]


if __name__ == "__main__":
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    entries = read_registry_entries()
    print(f"Entradas de uso de cámara: {len(entries)}")
    now = time.time()
    for key, start, stop in sorted(entries, key=lambda e: -e[1])[:15]:
        when = time.strftime("%Y-%m-%d %H:%M",
                             time.localtime(_filetime_to_unix(start)))
        estado = "EN USO AHORA" if stop == 0 else "libre"
        print(f"  {estado:12s} {when}  {friendly_name(key)}")
    otros = others_using_camera(own_markers(), now=now)
    print("\nOtras apps usando la cámara:", ", ".join(otros) or "ninguna")
