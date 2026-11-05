"""
history_logger.py
-----------------
Bitácora local de eventos para el Capítulo IV de la tesis.

Registra en SQLite (sin dependencias externas) cada alerta confirmada
por el FSM, incluyendo: timestamp, tipo de evento, métricas y duración.

La base de datos se crea automáticamente si no existe.

Tablas:
  - sessions      → una fila por sesión de uso
  - events        → un registro por alerta (FK → sessions)
  - metrics_log   → snapshots de métricas (uno por segundo, para análisis)
  - self_reports  → autorreporte diario del participante (dolor de cuello,
                    cansancio visual, somnolencia; escala 0–10)

Concurrencia
------------
La conexión la usan el hilo de inferencia (métricas y alertas) y el de la
interfaz (autorreporte, copia de seguridad para exportar). Todas las
operaciones pasan por un RLock: sqlite3 con check_same_thread=False permite
compartir la conexión, pero NO la serializa.

Autor: Generado según PLAN.md — Tesis Huisa Perez, UNSA 2026
"""

import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List, Optional

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

CREATE TABLE IF NOT EXISTS self_reports (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_code TEXT,
    report_date      TEXT NOT NULL,
    created_at       REAL NOT NULL,
    neck_pain        INTEGER,
    eye_strain       INTEGER,
    sleepiness       INTEGER,
    retrospective    INTEGER NOT NULL DEFAULT 0,
    skipped          INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_events_session    ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp  ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_session   ON metrics_log(session_id);
"""

# Fases de campo (sessions.study_phase); ver src/study/field_study.py.
FIELD_PHASES = ("linea_base", "intervencion")


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
        self._lock = threading.RLock()

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
            # Umbral de ojo cerrado vigente en cada muestra. Con el umbral
            # adaptativo cada sesión (y cada recalibración) tiene el suyo, y
            # sin registrarlo un EAR o un PERCLOS guardado no se puede
            # interpretar después. NULL = calibración en curso.
            "ear_threshold":      "REAL",
            # θc relativo a la postura neutra del usuario (modo "reference").
            # Se guardan la referencia y la desviación: el θc absoluto sigue
            # en cervical_angle, así que cualquier fila permite reconstruir
            # los tres. NULL = calibración en curso o modo absoluto.
            "cervical_baseline":  "REAL",
            "cervical_deviation": "REAL",
            # Indicador 2D sin profundidad (altura del cuello / ancho de
            # hombros). Solo se mide, para compararlo con θc en la validación.
            "neck_ratio":         "REAL",
        }
        # Condición del protocolo de estudio vigente en cada fila (p. ej.
        # "cabeza_adelantada"). Permite cruzar lo que el sistema midió y avisó
        # con lo que el participante estaba haciendo realmente, que es lo que
        # hace falta para calcular la precisión. "" = sin condición.
        nuevas["condition_label"] = "TEXT"
        for col, tipo in nuevas.items():
            if col not in existing:
                self._conn.execute(
                    f"ALTER TABLE metrics_log ADD COLUMN {col} {tipo}")
                logger.info("metrics_log: columna '%s' añadida por migración.", col)

        # sessions.participant_code: código seudonimizado del participante
        # (P01, P02…). Nunca un nombre. Sin él no se puede saber de quién es
        # cada sesión ni analizar la evolución de una misma persona.
        ses = {row["name"] for row in
               self._conn.execute("PRAGMA table_info(sessions)")}
        # app_version y config_hash: trazabilidad (todos los participantes
        # deben haber usado la misma versión y los mismos umbrales).
        # study_phase: validacion / linea_base / intervencion.
        for col in ("participant_code", "app_version", "config_hash",
                    "study_phase"):
            if col not in ses:
                self._conn.execute(
                    f"ALTER TABLE sessions ADD COLUMN {col} TEXT")
                logger.info("sessions: columna '%s' añadida.", col)

        ev = {row["name"] for row in
              self._conn.execute("PRAGMA table_info(events)")}
        if "condition_label" not in ev:
            self._conn.execute(
                "ALTER TABLE events ADD COLUMN condition_label TEXT")
            logger.info("events: columna 'condition_label' añadida.")
        # notified: 1 si la alerta se mostró al usuario, 0 si se registró en
        # silencio (línea base del estudio de campo). NULL en bases antiguas,
        # donde todas se mostraban.
        if "notified" not in ev:
            self._conn.execute(
                "ALTER TABLE events ADD COLUMN notified INTEGER")
            logger.info("events: columna 'notified' añadida.")

    @contextmanager
    def _cursor(self):
        """Context manager que hace commit automático (bajo el cerrojo)."""
        with self._lock:
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

    def start_session(self, notes: str = "",
                      participant_code: str = "",
                      study_phase: str = "",
                      app_version: str = "",
                      config_hash: str = "") -> str:
        """
        Inicia una nueva sesión de monitoreo.

        Parameters
        ----------
        notes:
            Texto libre; la app guarda aquí un resumen de la calibración.
        participant_code:
            Código seudonimizado del participante ("" si no es un estudio).
        study_phase:
            validacion / linea_base / intervencion ("" fuera de un estudio).
        app_version, config_hash:
            Versión de la app y huella de config/thresholds.json.

        Returns
        -------
        str
            ID único de la sesión (UUID4).
        """
        session_id = str(uuid.uuid4())
        self._session_id = session_id

        with self._cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (id, started_at, notes, participant_code,"
                " study_phase, app_version, config_hash)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, time.time(), notes, participant_code or None,
                 study_phase or None, app_version or None,
                 config_hash or None),
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

    def log_event(self, alert_event, condition: str = "",
                  notified: bool = True) -> None:
        """
        Registra un AlertEvent del FSM en la tabla events.

        Parameters
        ----------
        alert_event:
            Instancia de fusion_fsm.AlertEvent.
        condition:
            Condición del protocolo de estudio en el momento de la alerta.
        notified:
            False si la alerta se detectó pero NO se mostró (línea base).
        """
        if not self._session_id:
            logger.warning("log_event: no hay sesión activa, iniciando una automáticamente.")
            self.start_session(notes="auto-iniciada")

        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO events
                   (session_id, timestamp, alert_type, metric_value,
                    threshold, duration_sec, message, condition_label,
                    notified)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._session_id,
                    alert_event.timestamp,
                    alert_event.alert_type.value,
                    alert_event.metric_value,
                    alert_event.threshold,
                    alert_event.duration_sec,
                    alert_event.message,
                    condition or None,
                    int(bool(notified)),
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
        ear_thr = getattr(metrics, "ear_threshold", None)

        def _opt(name, nd):
            v = getattr(metrics, name, None)
            return round(v, nd) if v is not None else None

        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO metrics_log
                   (session_id, timestamp, cervical_angle, shoulder_asymmetry,
                    ear_avg, mouth_opening, fps, face_detected, pose_detected,
                    cervical_sagittal, cervical_lateral, distance_m,
                    perclos, blink_rate_per_min, pose_valid, ear_threshold,
                    cervical_baseline, cervical_deviation, neck_ratio,
                    condition_label)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           ?, ?, ?, ?)""",
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
                    round(ear_thr, 4) if ear_thr is not None else None,
                    _opt("cervical_baseline", 3),
                    _opt("cervical_deviation", 3),
                    _opt("neck_ratio", 4),
                    getattr(metrics, "condition_label", "") or None,
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

    # ------------------------------------------------------------------
    # Estudio de campo
    # ------------------------------------------------------------------

    def field_minutes_by_date(self, participant_code: str,
                              phases=FIELD_PHASES,
                              max_gap_sec: float = 5.0) -> Dict[str, float]:
        """
        Minutos de seguimiento REAL por día (fecha local) de un participante.

        Se suman los intervalos entre filas consecutivas de metrics_log de
        hasta `max_gap_sec`: un hueco mayor es una pausa (cámara apagada) y no
        cuenta como uso. Por eso no se usa started_at/ended_at, que incluye
        las pausas y, en sesiones sin cerrar, ni siquiera tiene fin.
        """
        if not participant_code:
            return {}
        marks = ",".join("?" for _ in phases)
        sql = f"""
            SELECT d, SUM(dt) FROM (
                SELECT date(m.timestamp, 'unixepoch', 'localtime') AS d,
                       m.timestamp - LAG(m.timestamp) OVER (
                           PARTITION BY m.session_id ORDER BY m.timestamp) AS dt
                FROM metrics_log m JOIN sessions s ON s.id = m.session_id
                WHERE s.participant_code = ? AND s.study_phase IN ({marks})
            ) WHERE dt > 0 AND dt <= ? GROUP BY d ORDER BY d"""
        with self._lock:
            rows = self._conn.execute(
                sql, (participant_code, *phases, max_gap_sec)).fetchall()
        return {r[0]: (r[1] or 0.0) / 60.0 for r in rows}

    _HISTORY_COLS = ("session_id", "timestamp", "fps", "face_detected",
                     "pose_detected", "pose_valid", "cervical_deviation",
                     "shoulder_asymmetry", "cervical_lateral", "distance_m",
                     "perclos")

    def rows_since(self, since_ts: float,
                   participant_code: Optional[str] = None) -> List[dict]:
        """
        Filas de metrics_log desde `since_ts` (solo las columnas que usa el
        historial personal), ordenadas por sesión y hora. Con
        `participant_code`, solo las sesiones de ese participante.
        """
        cols = ", ".join(f"m.{c}" for c in self._HISTORY_COLS)
        sql = (f"SELECT {cols} FROM metrics_log m "
               "JOIN sessions s ON s.id = m.session_id WHERE m.timestamp >= ?")
        args: list = [since_ts]
        if participant_code:
            sql += " AND s.participant_code = ?"
            args.append(participant_code)
        sql += " ORDER BY m.session_id, m.timestamp"
        with self._lock:
            cur = self._conn.execute(sql, args)
            return [dict(zip(self._HISTORY_COLS, r)) for r in cur.fetchall()]

    def events_since(self, since_ts: float,
                     participant_code: Optional[str] = None) -> List[dict]:
        """Eventos desde `since_ts` (tipo, hora y si se mostraron)."""
        sql = ("SELECT e.timestamp, e.alert_type, e.notified FROM events e "
               "JOIN sessions s ON s.id = e.session_id WHERE e.timestamp >= ?")
        args: list = [since_ts]
        if participant_code:
            sql += " AND s.participant_code = ?"
            args.append(participant_code)
        sql += " ORDER BY e.timestamp"
        with self._lock:
            cur = self._conn.execute(sql, args)
            return [{"timestamp": r[0], "alert_type": r[1], "notified": r[2]}
                    for r in cur.fetchall()]

    def log_self_report(self, participant_code: str, report_date: str,
                        neck_pain: Optional[int], eye_strain: Optional[int],
                        sleepiness: Optional[int], retrospective: bool = False,
                        skipped: bool = False) -> None:
        """Guarda el autorreporte de un día (o que el participante lo omitió)."""
        with self._cursor() as cur:
            cur.execute(
                """INSERT INTO self_reports
                   (participant_code, report_date, created_at, neck_pain,
                    eye_strain, sleepiness, retrospective, skipped)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (participant_code or None, report_date, time.time(),
                 neck_pain, eye_strain, sleepiness,
                 int(bool(retrospective)), int(bool(skipped))),
            )

    def self_report_dates(self, participant_code: str) -> set:
        """Fechas con autorreporte (respondido u omitido) del participante."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT report_date FROM self_reports "
                "WHERE participant_code IS ?", (participant_code or None,)
            ).fetchall()
        return {r[0] for r in rows}

    def backup_to(self, dest_path) -> Path:
        """
        Copia consistente de la base aunque la app esté escribiendo.

        Copiar el archivo .db a mano con WAL activo puede dejar fuera las
        últimas escrituras (siguen en el -wal); la API de backup de SQLite
        produce una base completa y autónoma.
        """
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest.unlink()
        with self._lock:
            dst = sqlite3.connect(str(dest))
            try:
                self._conn.backup(dst)
            finally:
                dst.close()
        return dest

    def close(self) -> None:
        """Cierra la conexión a la base de datos."""
        with self._lock:
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
