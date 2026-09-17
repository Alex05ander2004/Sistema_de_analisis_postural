# Sistema Inteligente de Monitoreo Postural y Detección de Fatiga (Edge AI)

> **Tesis:** "Sistema inteligente de monitoreo postural y detección de fatiga basado en visión artificial para la prevención de trastornos musculoesqueléticos en entornos de trabajo digital"  
> **Autor:** Willy Alexander Huisa Perez — UNSA, 2026

---

## ¿Qué hace este sistema?

Detecta en **tiempo real** y **100% local** (sin nube):
- 🔴 **Cabeza adelantada** — ángulo cervical θc > 30° durante > 5 s
- 🟠 **Asimetría de hombros** — |ΔE| > 10° durante > 8 s
- 👁️ **Fatiga ocular** — EAR promedio ≤ 0.21 durante > 3 s
- 😴 **Bostezo** — apertura bucal (MAR) ≥ 0.45 durante > 3 s
- 💤 **Somnolencia** — PERCLOS ≥ 15% en una ventana móvil de 60 s

Además **mide y registra** (sin disparar alertas): componente sagital y lateral
de θc, tasa de parpadeo por minuto y distancia estimada cámara-usuario.

Al confirmar un riesgo, avisa al usuario y registra el evento en una bitácora
SQLite local.

## Monitoreo en segundo plano

El sistema está pensado para dejarse corriendo toda la jornada mientras el
usuario trabaja en otras aplicaciones:

- **Cerrar la ventana la oculta en la bandeja del sistema**; la captura y la
  inferencia siguen activas. Salir de verdad es la opción *Salir* del menú de
  bandeja (terminar el proceso cierra la sesión en SQLite y partiría la
  bitácora de la jornada en dos).
- **Con la ventana oculta se omite el render** —dibujar landmarks y codificar
  JPEG/Base64— porque nadie lo va a mirar. La inferencia, la geometría, el FSM
  y la bitácora siguen a plena velocidad: **ninguna medición se ve afectada**.
- **Pausar desde la bandeja libera la cámara** (`cv2.VideoCapture`), con lo que
  se apaga el piloto de la webcam. Para quien pausa porque entra en una
  videollamada, ese LED apagado es la única confirmación creíble de que no se
  le está grabando. Al reanudar, los temporizadores arrancan de cero.

Menú de bandeja: **Abrir panel** · **Pausar/Reanudar monitoreo** · **Salir**.

### Régimen de alertas

No todas las alertas tienen la misma urgencia, y tratarlas igual falla en las
dos direcciones. Una cabeza adelantada es un riesgo **acumulativo**:
interrumpir con un modal cada 30 s produce fatiga de alertas y acaba con la
herramienta desinstalada. La somnolencia por PERCLOS es un riesgo **agudo**:
ahí un aviso que se desvanece en cinco segundos es insuficiente.

Por eso el canal se elige por tipo de alerta, configurable en
`config/thresholds.json` → `alerts` sin tocar código:

| `alerts.mode` | Comportamiento |
|---|---|
| `auto` *(por defecto)* | Notificación discreta del sistema; modal bloqueante solo para los tipos de `blocking_alert_types` (por defecto, `drowsiness`) |
| `toast` | Todo por notificación discreta. Nunca bloquea |
| `modal` | Todo por modal bloqueante — **el comportamiento original del Capítulo III**, conservado para poder contrastar ambos regímenes en la validación |

En Windows las notificaciones son *toast* nativos (`winotify`), visibles por
encima de cualquier aplicación y archivados en el Centro de Actividades. En
Linux y macOS el sistema degrada a un aviso dentro de la propia ventana, que
sigue siendo no intrusivo. Toda alerta, salga por el canal que salga, queda
además en **Alertas recientes** del panel de telemetría y en la tabla `events`
de SQLite.

> **Escala de θc.** 0° = cabeza alineada sobre los hombros. El umbral de 30°
> corresponde a unos **12 cm** de desplazamiento anterior de la cabeza en un
> adulto de talla media. La componente sagital es aproximadamente el
> complemento del Ángulo Craneovertebral clínico: `CVA ≈ 90° − θc_sagital`.

---

## Arquitectura

```
[Webcam RGB 640×480 @ 30 FPS]
       │
       ▼  VideoThread (Queue(maxsize=1), drop-oldest)
[InferenceThread]
  ├── 1 conversión BGR→RGB compartida
  ├── ThreadPoolExecutor(2) ── BlazePose ─┐  ← EN PARALELO
  │                        └─ Face Mesh ──┤
  ├── geometry ─────────────────────────── θc (3D/sagital/lateral), ΔE,
  │                                        EAR, MAR, distancia
  ├── MetricSmoother ───────────────────── mediana móvil + EMA
  ├── FusionFSM ────────────────────────── temporizadores + PERCLOS + parpadeo
  ├── HistoryLogger ────────────────────── SQLite (métricas + eventos)
  └── VideoFeed.encode() ───────────────── JPEG/Base64
       │
       ▼  AppState (Queue(maxsize=1))
[UI Thread — Flet]
  ├── VideoFeed       → asigna el frame ya codificado
  ├── TelemetryPanel  → semáforo, distancia, PERCLOS, temporizadores, alertas
  └── AlertDispatcher → elige canal según el tipo de alerta
         ├── ToastNotifier  → notificación del SO (no intrusiva)  ← por defecto
         └── AlertNotifier  → modal bloqueante (solo somnolencia)

[TrayIcon — hilo propio]
  └── Abrir panel · Pausar/Reanudar · Salir
```

Con la ventana oculta, `VideoFeed.encode()` y los `draw()` se omiten: todo lo
que está por encima en el diagrama sigue ejecutándose igual.

**Regla de oro**: los tres hilos (captura, inferencia, UI) **nunca se bloquean
entre sí**. Dentro del hilo de inferencia, los dos modelos de MediaPipe corren
en paralelo sobre el mismo buffer RGB en modo solo-lectura: la latencia es
`max(pose, face)` y no `pose + face`. MediaPipe libera el GIL durante la
inferencia, así que el solapamiento es real (verificado: 1.29× en
`benchmark_mediapipe.py --synthetic`).

---

## Instalación

> ⚠️ **Requiere Python 3.11 específicamente.** `mediapipe` en builds recientes (≥0.10.30) para Python 3.13 eliminó por completo la API legacy `mp.solutions.*` que usan `pose_estimator.py` y `face_estimator.py` (solo dejaron el nuevo API `mediapipe.tasks`). Con Python 3.13 la app falla con `AttributeError: module 'mediapipe' has no attribute 'solutions'` **antes de abrir cualquier ventana**, sin mensaje visible. `requirements.txt` fija `mediapipe==0.10.14` (última versión que sí trae `solutions`), pero esa versión solo tiene wheel publicado para Python ≤3.12. Usa `py -0p` (Windows) para ver qué intérpretes 3.11 tienes disponibles si no tienes uno.

### 1. Crear entorno virtual (con Python 3.11)

```bash
py -3.11 -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate
```

### 2. Instalar dependencias

```bash
pip install -r requirements.txt
```

> ⚠️ **No instales `opencv-contrib-python` junto a `opencv-python`.** Los dos
> paquetes instalan el **mismo** directorio `cv2/` en `site-packages`: conviven
> sin error aparente, pero el último en instalarse sobrescribe el binario del
> otro y `import cv2` puede cambiar de versión en silencio tras cualquier
> reinstalación — justo en mitad de una ronda de mediciones. Ninguna función de
> este proyecto usa los módulos *contrib*. Para comprobar que solo hay uno:
>
> ```bash
> pip list | grep -i opencv
> ```

### 3. Ejecutar la aplicación

```bash
python -m src.ui.app_ui
```

La cámara se autodetecta al arrancar (prueba los índices 0–3 y usa el primero que entregue imagen real, no negra — ver `camera.source_index` en `config/thresholds.json` para forzar un índice específico).

---

## Estructura del proyecto

```
postural-fatigue-system/
├── requirements.txt          # Versiones EXACTAS — ver aviso sobre opencv-contrib
├── .gitignore
├── config/
│   └── thresholds.json       # Umbrales calibrables (θc, EAR, ventanas temporales)
├── assets/                   # Icono de la app (bandeja y notificaciones)
├── data/                     # Datos de sesión (fuera de git)
│   ├── history.db            # Bitácora de la sesión actual
│   └── archive/              # Datos pre-corrección — NO usar (ver LEEME.md)
├── src/
│   ├── capture/
│   │   ├── video_thread.py   # Hilo de captura + Queue(1)
│   │   └── camera_source.py  # Autodetección de cámara (compartida app/tools)
│   ├── vision/
│   │   ├── pose_estimator.py # BlazePose wrapper (process / draw separados)
│   │   ├── face_estimator.py # Face Mesh wrapper (process / draw separados)
│   │   ├── geometry.py       # θc, ΔE, EAR, MAR, distancia, visibilidad
│   │   └── smoothing.py      # filtrado temporal (mediana + EMA)
│   ├── fusion/
│   │   └── fusion_fsm.py     # Máquina de estados multimodal
│   ├── storage/
│   │   └── history_logger.py # Bitácora SQLite
│   ├── tools/                      # Todas aceptan --source -1 (autodetectar)
│   │   ├── _config.py              # Carga compartida de thresholds.json
│   │   ├── calibration_mode.py     # Captura θc/EAR/etc a CSV sin disparar alertas
│   │   ├── stats_report.py         # Reportes de sesión desde SQLite (Cap. IV)
│   │   ├── ab_test_cervical.py     # Prueba A/B normal vs. cabeza adelantada
│   │   ├── transition_test.py      # Transición continua de postura
│   │   └── benchmark_mediapipe.py  # Latencia REAL, secuencial vs. paralelo
│   └── ui/
│       ├── app_ui.py         # Entry point Flet + bandeja + segundo plano
│       ├── video_feed.py     # Feed Base64/JPEG
│       ├── telemetry_panel.py# Dashboard semáforo + alertas recientes
│       ├── alert_dispatcher.py # Elige el canal de cada alerta
│       ├── toast_notifier.py # Notificación del SO (no intrusiva)
│       ├── tray_icon.py      # Icono de bandeja (segundo plano)
│       └── alert_notifier.py # Modal de pausa activa (solo alertas agudas)
├── tests/                    # 201 tests, ~24 s, sin cámara ni MediaPipe
│   ├── test_geometry.py             # Geometría + regresiones de sesgo y escala
│   ├── test_fusion_fsm.py           # FSM con reloj falso (ventanas reales de 3-8 s)
│   ├── test_smoothing.py            # Filtrado temporal
│   ├── test_ui_components.py        # Humo de widgets Flet (panel, feed, alertas)
│   ├── test_integration_pipeline.py # Pipeline completo con estimadores falsos
│   ├── test_performance.py          # FPS, latencia, memoria — geometry+FSM sintético
│   ├── test_stress.py               # Oclusión, ruido, distancia, transiciones
│   ├── test_background_alerts.py    # Enrutado de alertas, toast y bandeja
│   └── test_background_pipeline.py  # Segundo plano y pausa, con pipeline real
└── docs/
    ├── validation_report.md             # Reporte Capítulo IV (resultados medidos)
    ├── expert_interview_protocol.md     # Acta de la ronda 1 (2026-07-12)
    └── expert_interview_protocol_v2.md  # Instrumento de la ronda 2 (pendiente)
```

---

## Calibración de umbrales

Edita `config/thresholds.json` para ajustar sin recompilar:

| Parámetro | Default | Descripción |
|---|---|---|
| `cervical_angle_max_deg` | 30.0° | Umbral de cabeza adelantada (validado por experto) |
| `cervical_alert_window_sec` | 5.0 s | Ventana temporal para alerta postural |
| `shoulder_asymmetry_max_deg` | 10.0° | Umbral de asimetría escapular (validado por experto) |
| `shoulder_alert_window_sec` | 8.0 s | Ventana de hombros — distinta de la cervical, a petición del experto |
| `ear_threshold` | 0.21 | Umbral EAR de fatiga (Soukupová & Čech) |
| `ear_alert_window_sec` | 3.0 s | Ventana temporal para alerta de fatiga |
| `blink_min_ms` / `blink_max_ms` | 100–400 ms | Parpadeo fisiológico. `blink_max_ms` es además el periodo de gracia del temporizador de fatiga |
| `perclos_threshold` / `perclos_window_sec` | 0.15 / 60 s | Somnolencia por PERCLOS (literatura de conducción) |
| `min_distance_m` / `max_distance_m` | 0.50–0.70 m | Rango de trabajo validado por el experto |
| `horizontal_fov_deg` | 60.0° | FOV de la webcam — **calibrar antes de la sesión de validación** |
| `pose_model_complexity` | 1 | Bajar a 0 si no se alcanzan 30 FPS |
| `pose_every_n_frames` | 1 | Subir a 2 si no se alcanzan 30 FPS |
| `face_draw_mode` | `metrics` | `tesselation` es vistoso pero cuesta más que la propia inferencia |
| `alerts.mode` | `auto` | Canal de las alertas: `auto` / `toast` / `modal` |
| `alerts.blocking_alert_types` | `["drowsiness"]` | Tipos que sí interrumpen con modal en modo `auto` |
| `alerts.background_monitoring` | `true` | Cerrar la ventana la oculta en la bandeja en vez de terminar el proceso |
| `ui.skip_render_when_hidden` | `true` | Omitir el render con la ventana oculta (no afecta a la medición) |

Cada parámetro lleva en el JSON un comentario `_..._comment` con su fuente y su
estado de validación. Los umbrales pendientes de criterio clínico están
marcados como **PENDIENTE** o **PROVISIONAL**.

### Calibración del FOV de la cámara (hacer una vez, antes de la validación)

La estimación de distancia depende del campo de visión de la webcam, que varía
entre 55° y 78° según el modelo:

1. Sitúate a una distancia medida con cinta métrica (p. ej. 0.60 m).
2. Lee la distancia estimada en el panel de telemetría.
3. Ajusta `camera.horizontal_fov_deg` hasta que ambas coincidan.

---

## Ejecutar pruebas

```bash
# Todos los tests (201 tests, ~24 s, sin cámara ni MediaPipe)
python -m pytest tests/ -v

# Solo tests de geometría
python -m pytest tests/test_geometry.py -v

# Tests de rendimiento con reporte detallado (geometry+FSM puro, landmarks sintéticos)
python -m pytest tests/test_performance.py -v -s

# Tests de estrés/robustez (oclusión, ruido, distancia, transiciones)
python -m pytest tests/test_stress.py -v -s

# Tests del FSM (reloj falso: ventanas reales de 3-8 s en milisegundos)
python -m pytest tests/test_fusion_fsm.py -v

# Segundo plano: enrutado de alertas, toast, bandeja, pausa y ahorro de render
python -m pytest tests/test_background_alerts.py tests/test_background_pipeline.py -v

# Pipeline completo con estimadores falsos (sin cámara ni MediaPipe)
python -m pytest tests/test_integration_pipeline.py -v

# Humo de la UI (widgets Flet, sin abrir ventana)
python -m pytest tests/test_ui_components.py -v

# Latencia REAL con cámara + MediaPipe — mide secuencial Y paralelo en la
# misma sesión (requiere webcam; NO cubierto por pytest)
python src/tools/benchmark_mediapipe.py --frames 300

# Lo mismo sin cámara, con frames sintéticos (tiempos NO comparables)
python src/tools/benchmark_mediapipe.py --synthetic --frames 60
```

### Herramientas de medición (requieren cámara)

Todas autodetectan la cámara por defecto (`--source -1`). Fuerza un índice con
`--source N` solo si sabes cuál quieres.

```bash
# Calibración: vuelca θc, sagital, lateral, ΔE, EAR, MAR y distancia a CSV
python src/tools/calibration_mode.py --duration 300
```

```bash
# Prueba A/B: ¿θc separa postura normal de cabeza adelantada?
python src/tools/ab_test_cervical.py --phase-sec 8
```

```bash
# Trayectoria continua de θc al pasar de normal a adelantada
python src/tools/transition_test.py
```

```bash
# Reportes de sesión desde la bitácora SQLite (Capítulo IV)
python src/tools/stats_report.py --list-sessions
```

> ⚠️ Ningún test de `pytest` ejecuta MediaPipe ni abre la cámara: no validan el
> FPS real de la app. `test_integration_pipeline.py` sí recorre el pipeline
> completo (InferenceThread, geometry, smoothing, FSM y SQLite reales) pero con
> los dos modelos sustituidos por dobles de prueba. Para el rendimiento real usa
> `benchmark_mediapipe.py`. Ver `docs/validation_report.md` §3.1b y §3.1c.

### Metas de rendimiento (Capítulo III.1.3.2.6)

| Métrica | Meta | Medición | Estado actual |
|---|---|---|---|
| FPS (pipeline completo, con MediaPipe) | ≥ 30 | `benchmark_mediapipe.py` | ⬜ Re-medición pendiente. ~13.9 FPS con el pipeline secuencial anterior; ya corregido y optimizado (7 cambios, ver §3.1c) |
| Latencia geometry+FSM puro | < 50 ms | `test_performance.py` | ✅ ~0.1 ms |
| Precisión | ≥ 90% | Validación con usuarios (Cap. IV) | ⬜ Pendiente sesión real |
| Memoria | Estable | `test_performance.py::TestMemoryStability` | ✅ 0.0 MB de crecimiento |
| Tests | 100% verde | `pytest tests/` | ✅ 201/201 |

---

## Requerimientos de hardware

- **CPU**: x86 o ARM, 4 núcleos mínimo
- **RAM**: 8 GB mínimo
- **Cámara**: Webcam RGB USB (640×480 @ 30 FPS), ubicada a la altura de los ojos del usuario
- **Distancia**: 0.50–0.70 m entre cámara y usuario (distancia de escritorio; validado con experto — 1.60–2.20 m era la distancia original de la tesis y resultó ser excesiva, provocaba que el usuario se encorve para ver la pantalla)
- **OS**: Windows 10/11, Ubuntu 20.04+, macOS 12+

---

## Modelos matemáticos implementados

> **Espacio de coordenadas.** MediaPipe normaliza `x` por el ancho del frame y
> `y` por el alto: en 640×480 ese espacio **no es isótropo**. Calcular ángulos o
> razones directamente sobre él introduce un factor W/H = 1.333 (un ΔE real de
> 10° se lee como 13.2°; un EAR real de 0.21 se lee como 0.28). Todas las
> fórmulas operan sobre píxeles: `x·W`, `y·H`, `z·W`.

### Ángulo Cervical (θc)
```
P_scapula  = midpoint(P11, P12)     # punto medio escapular
P_head     = midpoint(P7, P8)       # punto medio inter-auricular
V_cervical = P_head - P_scapula
V_vertical = (0, -1, 0)
θc = arccos(V_cervical · V_vertical / ‖V_cervical‖) × 180/π
```

Se usa el punto medio **entre ambas orejas**, no una sola: la oreja izquierda
está ~7 cm fuera del plano medio sagital y, tomada por separado, inclina el
vector cervical unos 13° aunque la postura sea perfecta.

Descomposición en planos anatómicos:
```
θc_sagital = atan2(-Δz, -Δy)    # cabeza adelantada  ≈ 90° - CVA
θc_lateral = atan2( Δx, -Δy)    # inclinación lateral de la cabeza
```

### Asimetría Escapular (ΔE)
```
V_hombros = P11 - P12
ΔE = atan2(V_hombros.y, |V_hombros.x|) × 180/π
```

### EAR (Eye Aspect Ratio) — Soukupová & Čech, 2016
```
EAR = (‖P2-P6‖ + ‖P3-P5‖) / (2 × ‖P1-P4‖)
Umbral fatiga: EAR ≤ 0.21
```

### PERCLOS
```
PERCLOS = (frames con EAR ≤ umbral) / (frames en la ventana de 60 s)
Umbral somnolencia: PERCLOS ≥ 0.15
```

### Distancia cámara-usuario (modelo estenopeico)
```
f_px = (W/2) / tan(HFOV/2)
d    = ancho_biacromial_m × f_px / ancho_hombros_px
```

---

## Licencia

Uso académico — Tesis de grado UNSA 2026. Todos los derechos reservados.
