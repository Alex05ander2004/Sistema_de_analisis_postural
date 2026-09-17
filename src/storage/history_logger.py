"""
history_logger.py
-----------------
Bitácora local de eventos para el Capítulo IV de la tesis.

Registra en SQLite (sin dependencias externas) cada alerta confirmada
por el FSM, incluyendo: timestamp, tipo de evento, métricas y duración.

La base de datos se crea automáticamente si no existe.

Tablas:
  - sessions  → una fila por sesión de uso
  - events    → un registro por alerta (FK → sessions)
  - metrics   → snapshots de métricas por frame (opcional, para análisis)

Autor: Generado según PLAN.md — Tesis Huisa Perez, UNSA 2026
"""

import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# Schema SQL
_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    started_at  REAL NOT NULL,
    ended_at    REAL,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES sessions(id),
    timestamp       REAL NOT NULL,
    alert_type      TEXT NOT NULL,
    metric_value    REAL,
    threshold       REAL,
    duration_sec    REAL,
    message         TEXT,
    created_at      REAL NOT NULL DEFAULT (strftime('%s','now'))
);

CREATE TABLE IF NOT EXISTS metrics_log (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES sessions(id),
    timestamp           REAL NOT NULL,
    cervical_angle      REAL,
    shoulder_asymmetry  REAL,
    ear_avg             REAL,
    mouth_opening       REAL,
    fps                 REAL,
    face_detected       INTEGER,
    pose_detected       INTEGER,
    cervical_sagittal   REAL,
    cervical_lateral    REAL,
    distance_m          REAL,
    perclos             REAL,
    blink_rate_per_min  REAL,
    pose_valid          INTEGER
);

CREATE INDEX IF NOT EXISTS idx_events_session    ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp  ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_session   ON metrics_log(session_id);
"""


class HistoryLogger:
    """
    Bitácora de eventos y métricas persistida en SQLite.

    Uso típico::

        logger_db = HistoryLogger("data/history.db")
        logger_db.start_session()

        # En cada alerta del FSM:
        logger_db.log_event(alert_event)

        # Periódicamente (ej. cada 30 frames):
        logger_db.log_metrics(sensor_metrics, fps=29.8)

        logger_db.end_session()
        logger_db.close()
    """

    def __init__(self, db_path: str = "data/history.db"):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn: Optional[sqlite3.Connection] = None
        self._session_id: Optional[str] = None

        self._open()
        logger.info("HistoryLogger inicializado → %s", self._db_path.resolve())

    def _open(self) -> None:
        """Abre la conexión y crea las tablas si no existen."""
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,   # accedemos desde el hilo de inferencia
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")  # escrituras más rápidas
        self._conn.executescript(_DDL)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """
        Añade a `metrics_log` las columnas que falten.

        `CREATE TABLE IF NOT EXISTS` no toca una tabla que ya existe, así que
        una base creada por una versión anterior se quedaría sin las columnas
        nuevas y todos los INSERT fallarían. Esto la actualiza en sitio y
        conserva las sesiones ya registradas.
        """
        existing = {row["name"] for row in
                    self._conn.execute("PRAGMA table_info(metrics_log)")}
        nuevas = {
            "cervical_sagittal":  "REAL",
            "cervical_lateral":   "REAL",
            "distance_m":         "REAL",
            "perclos":            "REAL",
            "blink_rate_per_min": "REAL",
            "pose_valid":         "INTEGER",
        }
        for col, tipo in nuevas.items():
            if col not in existing:
                self._conn.execute(
                    f"ALTER TABLE metrics_log ADD COLUMN {col} {tipo}")
                logger.info("metrics_log: columna '%s' añadida por migración.", col)

    @contextmanager
    def _cursor(self):
        """Context manager que hace commit automático."""
        cursor = self._conn.cursor()
        try:
            yield cursor
            self._conn.commit()
        except Exception as exc:
            self._conn.rollback()
            logger.error("HistoryLogger DB error: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Gestión de sesiones
    # ------------------------------------------------------------------

    def start_session(self, notes: str = "") -> str:
        """
        Inicia una nueva sesión de monitoreo.

        Returns
        -------
        str
            ID único de la sesión (UUID4).
        """
        session_id = str(uuid.uuid4())
        self._session_id = session_id

        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (id, started_at, notes) VALUES (?, ?, ?)",
                (session_id, time.time(), notes),
            )

        logger.info("Sesión iniciada: %s", session_id)
        return session_id

    def end_session(self) -> None:
        """Marca la sesión actual como terminada."""
        if not self._session_id:
            return

        with self._cursor() as cur:
            cur.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                (time.time(), self._session_id),
            )
        logger.info("Sesión terminada: %s", self._session_id)
        self._session_id = None

    # ------------------------------------------------------------------
    # Registro de alertas
    # ------------------------------------------------------------------

    def log_event(self, alert_event) -> None:
        """
        Registra un AlertEvent del FSM en la tabla events.

        Parameters
        ----------
        alert_event:
            Instancia de fusion_fsm.AlertEvent.
        """
        if not self._session_id:
            logger.warning("log_event: no hay sesión activa, iniciando una automáticamente.")
            self.start_session(notes="auto-iniciada")

        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO events
                   (session_id, timestamp, alert_type, metric_value,
                    threshold, duration_sec, message)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._session_id,
                    alert_event.timestamp,
                    alert_event.alert_type.value,
                    alert_event.metric_value,
                    alert_event.threshold,
                    alert_event.duration_sec,
                    alert_event.message,
                ),
            )

    # ------------------------------------------------------------------
    # Registro de métricas por frame
    # ------------------------------------------------------------------

    def log_metrics(self, metrics, fps: float = 0.0) -> None:
        """
        Registra un snapshot de métricas en la tabla metrics_log.

        Parameters
        ----------
        metrics:
            Instancia de fusion_fsm.SensorMetrics.
        fps:
            FPS actual del sistema en el momento del registro.
        """
        if not self._session_id:
            return

        distancia = getattr(metrics, "distance_m", None)
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO metrics_log
                   (session_id, timestamp, cervical_angle, shoulder_asymmetry,
                    ear_avg, mouth_opening, fps, face_detected, pose_detected,
                    cervical_sagittal, cervical_lateral, distance_m,
                    perclos, blink_rate_per_min, pose_valid)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._session_id,
                    metrics.timestamp,
                    round(metrics.cervical_angle, 3),
                    round(metrics.shoulder_asymmetry, 3),
                    round(metrics.ear_avg, 4),
                    round(metrics.mouth_opening, 4),
                    round(fps, 2),
                    int(metrics.face_detected),
                    int(metrics.pose_detected),
                    round(getattr(metrics, "cervical_sagittal", 0.0), 3),
                    round(getattr(metrics, "cervical_lateral", 0.0), 3),
                    round(distancia, 3) if distancia is not None else None,
                    round(getattr(metrics, "perclos", 0.0), 4),
                    round(getattr(metrics, "blink_rate_per_min", 0.0), 2),
                    int(getattr(metrics, "pose_valid", True)),
                ),
            )

    # ------------------------------------------------------------------
    # Consultas para reportes (Capítulo IV)
    # ------------------------------------------------------------------

    def get_session_summary(self, session_id: Optional[str] = None) -> dict:
        """
        Devuelve un resumen estadístico de la sesión para el Capítulo IV.

        Returns
        -------
        dict
            Contiene: total_events, events_by_type, avg_cervical_angle,
            avg_ear, session_duration_min, etc.
        """
        sid = session_id or self._session_id
        if not sid:
            return {}

        cur = self._conn.cursor()

        # Información de sesión
        cur.execute("SELECT * FROM sessions WHERE id = ?", (sid,))
        session = cur.fetchone()

        # Conteo de eventos por tipo
        cur.execute(
            "SELECT alert_type, COUNT(*) as cnt FROM events "
            "WHERE session_id = ? GROUP BY alert_type",
            (sid,),
        )
        events_by_type = {row["alert_type"]: row["cnt"] for row in cur.fetchall()}

        # Estadísticas de métricas
        cur.execute(
            """SELECT
               AVG(cervical_angle)    as avg_theta_c,
               MAX(cervical_angle)    as max_theta_c,
               AVG(cervical_sagittal) as avg_theta_sag,
               MAX(cervical_sagittal) as max_theta_sag,
               AVG(ABS(shoulder_asymmetry)) as avg_delta_e,
               MAX(ABS(shoulder_asymmetry)) as max_delta_e,
               AVG(ear_avg)        as avg_ear,
               MIN(ear_avg)        as min_ear,
               AVG(distance_m)     as avg_distance_m,
               MAX(perclos)        as max_perclos,
               AVG(blink_rate_per_min) as avg_blink_rate,
               AVG(fps)            as avg_fps,
               MIN(fps)            as min_fps,
               MIN(timestamp)      as first_ts,
               MAX(timestamp)      as last_ts,
               COUNT(*)            as total_frames
               FROM metrics_log WHERE session_id = ?""",
            (sid,),
        )
        stats = cur.fetchone()

        # Duración: si la sesión no llegó a cerrarse (crash, kill del proceso),
        # se estima con el rango de timestamps de las métricas registradas en
        # vez de devolver None, que dejaba el resumen del Capítulo IV vacío.
        duration = None
        duration_source = None
        if session and session["ended_at"] and session["started_at"]:
            duration = (session["ended_at"] - session["started_at"]) / 60.0
            duration_source = "session"
        elif stats and stats["first_ts"] and stats["last_ts"]:
            duration = (stats["last_ts"] - stats["first_ts"]) / 60.0
            duration_source = "metrics_log (sesión sin cerrar)"

        def _r(key, nd=2):
            v = stats[key] if stats else None
            return round(v, nd) if v is not None else None

        return {
            "session_id":       sid,
            "duration_min":     round(duration, 2) if duration is not None else None,
            "duration_source":  duration_source,
            "events_by_type":   events_by_type,
            "total_events":     sum(events_by_type.values()),
            "avg_theta_c_deg":  _r("avg_theta_c"),
            "max_theta_c_deg":  _r("max_theta_c"),
            "avg_theta_sagital_deg": _r("avg_theta_sag"),
            "max_theta_sagital_deg": _r("max_theta_sag"),
            "avg_delta_e_deg":  _r("avg_delta_e"),
            "max_delta_e_deg":  _r("max_delta_e"),
            "avg_ear":          _r("avg_ear", 4),
            "min_ear":          _r("min_ear", 4),
            "avg_distance_m":   _r("avg_distance_m", 3),
            "max_perclos":      _r("max_perclos", 4),
            "avg_blink_rate_per_min": _r("avg_blink_rate", 1),
            "avg_fps":          _r("avg_fps"),
            "min_fps":          _r("min_fps"),
            "total_frames":     (stats["total_frames"] if stats else 0) or 0,
        }

    def export_session_json(self, session_id: Optional[str] = None,
                             output_path: Optional[str] = None) -> str:
        """
        Exporta los datos de una sesión a JSON para el Capítulo IV.

        Returns
        -------
        str
            Ruta del archivo JSON generado.
        """
        summary = self.get_session_summary(session_id)
        sid = session_id or self._session_id

        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp",
            (sid,),
        )
        events = [dict(row) for row in cur.fetchall()]

        report = {"summary": summary, "events": events}

        if output_path is None:
            output_path = f"data/session_{sid[:8]}_report.json"

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info("Reporte exportado: %s", output_path)
        return output_path

    def close(self) -> None:
        """Cierra la conexión a la base de datos."""
        if self._session_id:
            self.end_session()
        if self._conn:
            self._conn.close()
            self._conn = None
        logger.info("HistoryLogger cerrado.")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
