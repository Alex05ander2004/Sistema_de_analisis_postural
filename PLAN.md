# Plan de Implementación
## Sistema Inteligente de Monitoreo Postural y Detección de Fatiga (Edge AI)

> Basado en la tesis "Sistema inteligente de monitoreo postural y detección de fatiga basado en visión artificial para la prevención de trastornos musculoesqueléticos en entornos de trabajo digital" — Willy Alexander Huisa Perez, UNSA, 2026.

Este documento traduce el marco teórico y la arquitectura definida en el Capítulo III de tu tesis en un **plan de desarrollo ejecutable**, pensado para que trabajes asistido por IA (Claude Code u otro asistente) de forma ordenada, por fases, con criterios de aceptación claros en cada paso.

---

## Estado actual (actualizado 2026-09-07)

> **Este documento es el plan original.** Algunas de sus decisiones ya han sido
> superadas por la evidencia y **no deben seguirse tal cual**. Lo que está
> desactualizado aquí, y dónde vive ahora la versión correcta:

| Sección de este plan | Qué cambió | Fuente actual |
|---|---|---|
| §1 — Distancia 1.60–2.20 m | **Corregido a 0.50–0.70 m.** Es distancia de sala de reuniones, no de escritorio: inducía que el usuario se encorve para leer la pantalla, contaminando la postura medida | `docs/expert_interview_protocol.md` D2 |
| §5 — `V_cervical = P7 − P_scapula` | **Corregido a `midpoint(P7, P8) − P_scapula`.** La oreja izquierda sola está ~7 cm fuera del plano medio sagital y metía un sesgo constante de ~13° en θc | `docs/validation_report.md` §3.3.1 |
| §5 — landmarks normalizados | **Todo se calcula ahora en píxeles isótropos.** El espacio normalizado de un frame 640×480 es anisótropo e inflaba ΔE, EAR y MAR por W/H = 1.333 | `docs/validation_report.md` §3.3.2 |
| §6 — θc umbral 15° / EAR 0.21 / 5 s | θc ahora **30°** (criterio del experto). ΔE mantiene 10°. La ventana de hombros pasa a **8 s** (el experto pidió que fuera distinta de la cervical) | `config/thresholds.json` |
| §6 — filtro de parpadeo | La implementación original era código muerto; reescrita como periodo de gracia de 400 ms | `src/fusion/fusion_fsm.py` |
| §2 — pipeline de inferencia | Pose y Face corren **en paralelo**, no en secuencia. Antes la latencia era `pose + face` (72 ms, ~13.9 FPS) | `docs/validation_report.md` §3.1c |
| §8 — "FPS ≥ 30" | Sigue siendo la meta, pero **pendiente de re-medir** con cámara real tras las optimizaciones | `docs/validation_report.md` §3.1c |

**Lo que sigue vigente de este plan:** la arquitectura de tres hilos, la regla
de oro de que no se bloqueen entre sí, la estructura de carpetas y el enfoque
por fases. **Fases 1–3 completadas.** La Fase 4 está en curso: falta la sesión
de validación con usuarios reales y la segunda ronda de entrevista con expertos
(`docs/expert_interview_protocol_v2.md`).

**Antes de la sesión con personas reales, en este orden:**

1. Calibrar `camera.horizontal_fov_deg` con una medición con cinta métrica
   (ver README → *Calibración del FOV*).
2. Ejecutar `python src/tools/benchmark_mediapipe.py --frames 300` con cámara y
   sujeto real, y anotar el resultado en `docs/validation_report.md` §3.1c.
3. Re-medir la tabla de θc con `src/tools/calibration_mode.py` a 0.50–0.70 m y
   cámara a la altura de los ojos (la tabla §4.4 del reporte está obsoleta).
4. Revisar `ui.alert_cooldown_sec`: 30 s son dos modales bloqueantes por minuto
   con postura de riesgo sostenida, y eso arruina una sesión de prueba.

---

## 1. Resumen de lo que hay que construir

| Elemento | Definición según tu tesis |
|---|---|
| **Objetivo del software** | Detectar en tiempo real posturas de riesgo (cabeza adelantada, asimetría de hombros) y fatiga (ocular y por bostezo) usando solo una webcam RGB, sin nube, sin wearables. |
| **Paradigma** | Edge AI / Edge Computing — 100% procesamiento local, cero envío de video a servidores. |
| **Entradas** | Video RGB 640x480 @ 30 FPS, distancia sujeto-cámara 1.60–2.20 m. |
| **Salidas** | Métricas numéricas (θc, ΔE, EAR), overlay de landmarks, alertas de pausa activa, reportes históricos. |
| **Restricciones de alcance** | No aplica a usuarios con patologías estructurales preexistentes, no es dispositivo diagnóstico clínico. |

---

## 2. Arquitectura objetivo (según tu esquema del Capítulo III)

```
[Cámara Web RGB]
       │  flujo de video continuo
       ▼
┌─────────────────────────────────────────────┐
│  vision_module.py  (Backend - Python)        │
│                                               │
│  1. Hilo de Captura (Threading + Queue(1))    │
│  2. MediaPipe BlazePose  → 33 landmarks 3D    │
│  3. MediaPipe Face Mesh  → 468 landmarks      │
│  4. Procesamiento geométrico:                 │
│       - Ángulo cervical (θc)                  │
│       - Asimetría escapular (ΔE)              │
│       - EAR + apertura bucal                  │
│  5. Módulo de Fusión Multimodal (FSM)         │
│       - temporizadores independientes         │
│       - filtra falsos positivos               │
└─────────────────────────────────────────────┘
       │  JSON de estado + frame anotado
       ▼
┌─────────────────────────────────────────────┐
│  app_ui.py  (Frontend - Flet)                 │
│  - Feed de video con landmarks (Base64/JPG)   │
│  - Panel de telemetría (semaforización)       │
│  - Notificador de alertas (modal bloqueante)  │
└─────────────────────────────────────────────┘
```

**Regla de oro de esta arquitectura**: el hilo de captura de video, el hilo de inferencia y el hilo de UI **nunca se bloquean entre sí**. Esto es lo primero que hay que dejar bien resuelto porque todo lo demás depende de que esto funcione sin fugas de memoria ni caídas de FPS.

---

## 3. Stack tecnológico y por qué

| Componente | Librería | Justificación (de tu tesis) |
|---|---|---|
| Pose corporal | `mediapipe` (BlazePose) | >30 FPS en CPU, 33 landmarks 3D, mejor equilibrio precisión/velocidad que OpenPose/YOLOv8 para este caso |
| Malla facial | `mediapipe` (Face Mesh) | 468 landmarks, permite EAR y apertura bucal sin sensores |
| Captura/render | `opencv-python` | Estándar de facto, integra bien con MediaPipe |
| UI | `flet` | Framework declarativo en Python, permite async sin bloquear el render |
| Concurrencia | `threading`, `queue.Queue(maxsize=1)` | Aísla I/O bloqueante de cv2.VideoCapture |
| Persistencia local | `sqlite3` o `.json` | Reportes históricos sin dependencia de nube |
| Matemática vectorial | `numpy` | Producto escalar, norma L2, arctan2 |

```txt
# requirements.txt sugerido
opencv-python==4.10.*
mediapipe==0.10.*
flet==0.24.*
numpy>=1.26
```

---

## 4. Estructura de carpetas propuesta

```
postural-fatigue-system/
├── requirements.txt
├── README.md
├── config/
│   └── thresholds.json          # umbrales ergonómicos calibrables (1.3.2.1)
├── src/
│   ├── capture/
│   │   └── video_thread.py      # Hilo de Captura + Queue(1)
│   ├── vision/
│   │   ├── pose_estimator.py    # wrapper BlazePose
│   │   ├── face_estimator.py    # wrapper Face Mesh
│   │   └── geometry.py          # θc, ΔE, EAR, apertura bucal
│   ├── fusion/
│   │   └── fusion_fsm.py        # Máquina de estados de fusión multimodal
│   ├── storage/
│   │   └── history_logger.py    # bitácora de eventos / matriz de resultados
│   └── ui/
│       ├── app_ui.py            # entry point Flet
│       ├── video_feed.py
│       ├── telemetry_panel.py
│       └── alert_notifier.py
├── tests/
│   ├── test_geometry.py
│   ├── test_fusion_fsm.py
│   └── test_performance.py      # FPS y latencia (1.3.2.6)
└── docs/
    └── validation_report.md     # resultados Cap. IV de tu tesis
```

---

## 5. Modelos matemáticos a implementar (Capítulo III.3.3.2.C)

Estos son los que debes codificar en `geometry.py`, tal como los formalizaste en tu tesis:

**Ángulo cervical (θc)** — desplazamiento de cabeza adelantada:
1. Punto medio escapular: `P_scapula = midpoint(P11, P12)` (landmarks de hombros de BlazePose)
2. Vector cervical: `V_cervical = P7 - P_scapula` (P7 = trago oreja izquierda)
3. Vector vertical de referencia: `V_vertical = (0, -1, 0)`
4. `θc = arccos( (V_cervical·V_vertical) / (‖V_cervical‖·‖V_vertical‖) ) * 180/π`

**Asimetría escapular (ΔE)**:
- `V_hombros = P11 - P12`
- Ángulo respecto a la horizontal de cámara vía `atan2`

**EAR (Eye Aspect Ratio)** — usar los 6 puntos estándar por ojo de Face Mesh y la fórmula clásica de Soukupová & Čech (relación distancias verticales/horizontales del párpado).

**Apertura bucal** — distancia vertical entre landmarks labio superior/inferior de Face Mesh, normalizada por el ancho facial.

> Sugerencia de prompt para tu IA: *"Implementa la función `calculate_cervical_angle(landmarks)` en Python usando numpy, siguiendo exactamente esta fórmula: [pegar fórmula]. Los landmarks vienen como objetos MediaPipe con atributos x,y,z normalizados. Incluye docstring y un test unitario con coordenadas de ejemplo."*

---

## 6. Módulo de fusión multimodal (FSM) — reglas exactas de tu tesis

No dispares alertas por eventos instantáneos. Implementa temporizadores independientes:

| Condición | Umbral | Ventana temporal |
|---|---|---|
| Postura fuera de rango | θc fuera de rango seguro | > 5.0 s continuos |
| Fatiga ocular | EAR_promedio ≤ 0.21 | > 3.0 s continuos |
| Parpadeo fisiológico normal | 100–400 ms | **ignorar**, no alertar |

Al confirmarse riesgo → serializar JSON atómico → disparar hacia `alert_notifier.py`.

> Nota de calibración: estos umbrales (5s, 3s, EAR 0.21) son los que definiste en el objetivo específico 1.3.2.1 como derivados de literatura. Antes de la Fase 4 (validación), revisa si tus fuentes (Makhmudov et al. 2024; Xing et al. 2023) sustentan estos valores exactos o si necesitas una prueba piloto con usuarios reales para ajustarlos — esto es exactamente lo que tu objetivo 1.3.2.6 pide validar.

---

## 7. Plan de desarrollo por fases (alineado a tu cronograma de 8 semanas)

### Fase 1 — Core Postural (Semana 1-2)
- [ ] Setup de entorno virtual + `requirements.txt`
- [ ] `video_thread.py`: hilo de captura con `Queue(maxsize=1)`, descartando frames antiguos
- [ ] Integración de `MediaPipe BlazePose` en modo streaming
- [ ] `geometry.py`: función de ángulo cervical (θc) y prueba con landmarks de ejemplo
- [ ] Criterio de aceptación: FPS ≥ 30 sostenido, sin memory leak en 10 min de ejecución continua

### Fase 2 — Backend Multimodal (Semana 3-4)
- [ ] Integración de `Face Mesh` en paralelo (mismo frame, dos pipelines)
- [ ] Cálculo de EAR y apertura bucal
- [ ] `fusion_fsm.py`: máquina de estados con temporizadores independientes
- [ ] Calibración por consola (logging de θc, ΔE, EAR en vivo para ajustar umbrales)
- [ ] Criterio de aceptación: FSM no dispara falsos positivos ante parpadeo normal en prueba de 5 min

### Fase 3 — Frontend e Integración Reactiva (Semana 5-6)
- [ ] `app_ui.py` base en Flet
- [ ] `video_feed.py`: render Base64/JPG del frame con landmarks superpuestos, async, sin bloquear la UI
- [ ] `telemetry_panel.py`: semaforización verde/amarillo/rojo
- [ ] `alert_notifier.py`: modal bloqueante de pausa activa
- [ ] Criterio de aceptación: integración asíncrona sin caída de FPS por debajo de 30 durante render simultáneo

### Fase 4 — Cierre, Validación y Mitigación (Semana 7-8)
- [ ] Pruebas de estrés: oclusiones parciales, variabilidad lumínica, distancia fuera de rango (1.60–2.20 m)
- [ ] Ajuste fino de umbrales según resultados reales de usuario
- [ ] `history_logger.py`: bitácora local de eventos (para tu Capítulo IV de resultados)
- [ ] Redacción de resultados estadísticos (precisión, latencia, FPS) para el Capítulo IV de tu tesis
- [ ] Documentación técnica final

---

## 8. Pruebas técnicas mínimas (objetivo 1.3.2.6 de tu tesis)

| Prueba | Métrica | Meta definida en tu tesis |
|---|---|---|
| Rendimiento | FPS | ≥ 30 |
| Latencia de inferencia | ms | < 50 ms |
| Precisión de clasificación | Accuracy | ≥ 90% |
| Recursos | CPU/RAM | Estable en hardware x86/ARM 4 núcleos, 8 GB RAM |

Escribe estos como tests automatizados en `tests/test_performance.py` usando `time.perf_counter()` alrededor del pipeline de inferencia, y corre sobre al menos 500 frames para tener una medición estable.

---

## 9. Cómo trabajar esto con tu asistente de IA de forma eficiente

1. **Un módulo por sesión, no todo de una vez.** Pide primero `video_thread.py` aislado, con su test, antes de pedir la integración con MediaPipe.
2. **Pega las fórmulas exactas** de la sección 5 de este plan en tus prompts — evita que la IA "invente" una variante distinta de tu ángulo cervical.
3. **Pide siempre tests junto con el código**, especialmente para `geometry.py` y `fusion_fsm.py`, porque son la lógica que sustenta tu validación en el Capítulo IV.
4. **Congela el código en la Fase 4** (como ya definiste en tu cronograma) y dedica ese tiempo solo a pruebas, ajuste de umbrales y redacción de resultados — no seguir agregando features.
5. Si usas Claude Code para esto, conviene tenerlo como repo local con esta estructura de carpetas ya creada, para que cada sesión de la IA tenga contexto de dónde va cada archivo.

---

## 10. Checklist de entregables para tu sustentación

- [ ] Prototipo funcional ejecutable (Versión 1.0)
- [ ] Código fuente organizado según la estructura de carpetas propuesta
- [ ] Resultados de pruebas de FPS/latencia/precisión (Capítulo IV)
- [ ] Bitácora de eventos de al menos una sesión de prueba de usuario real
- [ ] Documentación de umbrales finales usados y su justificación (ligada a tus antecedentes bibliográficos)
