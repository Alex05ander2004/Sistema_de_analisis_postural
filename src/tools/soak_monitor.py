"""
soak_monitor.py
---------------
Prueba de jornada larga: vigila la app mientras corre (por ejemplo 8 h) y
registra cada minuto memoria, CPU, hilos, handles y tamaño de la base.

Responde a las preguntas que hay que contestar ANTES de entregar la app a
25 personas durante 15 días:

  - ¿La memoria crece sin parar? (fuga: al día 10 el equipo iría lento)
  - ¿Cuánto CPU consume de media? (¿le estorba al participante?)
  - ¿Cuánto crece la base por hora? (¿cabe en 15 días? ¿pesa el .zip?)
  - ¿Se cerró la app sola en algún momento?

Uso
---
  1. Abre la app normalmente, calibra e inicia el seguimiento.
  2. En otra terminal:
         python src/tools/soak_monitor.py --horas 8
  3. Trabaja con normalidad (puedes ocultar la ventana). Al terminar, o con
     Ctrl+C, imprime el veredicto. El CSV queda en data/soak_<fecha>.csv.

Busca el proceso de la app por su línea de comandos ("src.ui.app_ui") o por
el nombre del ejecutable (MonitorPostural.exe).

Autor: Tesis Huisa Perez, UNSA 2026
"""

import argparse
import csv
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXE_NAMES = ("monitorpostural.exe",)
CMD_MARK = "src.ui.app_ui"

# Por encima de esta pendiente de memoria (tras el calentamiento) se
# considera que hay una fuga a investigar.
LEAK_MB_PER_HOUR = 20.0
WARMUP_MIN = 30.0


def find_app_process(psutil):
    """Proceso principal de la app, o None."""
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name in EXE_NAMES or CMD_MARK in cmd:
            # El lanzador de PyInstaller crea un hijo con el mismo nombre:
            # interesa el que tiene los hilos de la app (el de más memoria).
            return p
    return None


def slope_per_hour(t_min: Sequence[float], y: Sequence[float]) -> Optional[float]:
    """Pendiente de mínimos cuadrados, en unidades de y por hora."""
    n = len(t_min)
    if n < 3:
        return None
    mt, my = sum(t_min) / n, sum(y) / n
    den = sum((t - mt) ** 2 for t in t_min)
    if den == 0:
        return None
    return 60.0 * sum((t - mt) * (v - my) for t, v in zip(t_min, y)) / den


def db_size_mb(db: Path) -> float:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(db) + suffix)
        if f.exists():
            total += f.stat().st_size
    return total / 1e6


def verdict(rows: List[dict], died_at: Optional[str]) -> List[str]:
    """Conclusiones legibles a partir de las muestras."""
    out = []
    if not rows:
        return ["Sin muestras: no se encontró la app."]
    dur_h = rows[-1]["min"] / 60.0
    out.append(f"Duración vigilada: {dur_h:.1f} h ({len(rows)} muestras).")
    if died_at:
        out.append(f"[FALLO] La app se cerró a las {died_at}. Revisa "
                   f"data/logs/app.log alrededor de esa hora.")

    rss = [r["rss_mb"] for r in rows]
    out.append(f"Memoria: inicio {rss[0]:.0f} MB, final {rss[-1]:.0f} MB, "
               f"máx {max(rss):.0f} MB.")
    late = [r for r in rows if r["min"] >= WARMUP_MIN]
    s = slope_per_hour([r["min"] for r in late], [r["rss_mb"] for r in late])
    if s is None:
        out.append("  (Hace falta más de 30 min para juzgar la tendencia.)")
    elif s > LEAK_MB_PER_HOUR:
        out.append(f"  [FALLO] Crece {s:.1f} MB/h tras el calentamiento: "
                   f"posible fuga. En 15 días de uso sería un problema.")
    else:
        out.append(f"  [OK] Tendencia tras el calentamiento: {s:+.1f} MB/h.")

    cpu = [r["cpu_pct"] for r in rows if r["cpu_pct"] is not None]
    if cpu:
        out.append(f"CPU de la app: media {sum(cpu) / len(cpu):.0f} % de un "
                   f"núcleo, máx {max(cpu):.0f} %.")
    th = [r["threads"] for r in rows]
    if max(th) - min(th) > 10:
        out.append(f"  [AVISO] Los hilos variaron de {min(th)} a {max(th)}: "
                   f"puede que algo cree hilos sin cerrarlos.")
    dbs = slope_per_hour([r["min"] for r in rows], [r["db_mb"] for r in rows])
    if dbs is not None:
        out.append(f"Base de datos: {rows[-1]['db_mb']:.1f} MB, crece "
                   f"{dbs:.2f} MB/h → ~{dbs * 4 * 15:.0f} MB en 15 días de 4 h.")
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    if sys.platform == "win32" and sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        import psutil
    except ImportError:
        print("[ERROR] Falta psutil: pip install psutil")
        return 1
    from src.paths import data_dir

    ap = argparse.ArgumentParser(description="Prueba de jornada larga.")
    ap.add_argument("--horas", type=float, default=8.0)
    ap.add_argument("--intervalo", type=float, default=60.0,
                    help="Segundos entre muestras (default 60)")
    ap.add_argument("--db", type=Path, default=None,
                    help="Base a vigilar (default: la de la app)")
    ap.add_argument("--salida", type=Path, default=None)
    args = ap.parse_args(argv)

    db = args.db or (data_dir() / "history.db")
    out = args.salida or (data_dir() / f"soak_{datetime.now():%Y%m%d_%H%M}.csv")

    proc = find_app_process(psutil)
    if proc is None:
        print("[ERROR] No encuentro la app. Ábrela primero "
              "(python -m src.ui.app_ui o MonitorPostural.exe).")
        return 1
    print(f"Vigilando PID {proc.pid} durante {args.horas:g} h; muestras cada "
          f"{args.intervalo:g} s en {out}\nCtrl+C para terminar antes.\n")

    rows: List[dict] = []
    died_at = None
    t0 = time.time()
    proc.cpu_percent(None)
    fields = ["hora", "min", "rss_mb", "cpu_pct", "threads", "handles",
              "db_mb"]
    try:
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            while time.time() - t0 < args.horas * 3600:
                time.sleep(args.intervalo)
                try:
                    with proc.oneshot():
                        row = {
                            "hora": datetime.now().strftime("%H:%M:%S"),
                            "min": round((time.time() - t0) / 60.0, 2),
                            "rss_mb": round(proc.memory_info().rss / 1e6, 1),
                            "cpu_pct": round(proc.cpu_percent(None), 1),
                            "threads": proc.num_threads(),
                            "handles": (proc.num_handles()
                                        if hasattr(proc, "num_handles") else ""),
                            "db_mb": round(db_size_mb(db), 2),
                        }
                except psutil.NoSuchProcess:
                    died_at = datetime.now().strftime("%H:%M:%S")
                    break
                rows.append(row)
                w.writerow(row)
                f.flush()
                print(f"{row['hora']}  {row['rss_mb']:7.0f} MB  "
                      f"CPU {row['cpu_pct']:5.0f} %  hilos {row['threads']:3d}"
                      f"  base {row['db_mb']:.1f} MB")
    except KeyboardInterrupt:
        pass

    print("\n" + "\n".join(verdict(rows, died_at)))
    print(f"\nCSV: {out}")
    return 0 if died_at is None else 2


if __name__ == "__main__":
    sys.exit(main())
