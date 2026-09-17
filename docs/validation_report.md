# Reporte de Validación — Capítulo IV
## Sistema Inteligente de Monitoreo Postural y Detección de Fatiga

**Autor:** Willy Alexander Huisa Perez  
**Institución:** Universidad Nacional de San Agustín (UNSA)  
**Fecha:** 2026-09-07 (auditoría geométrica y de pipeline) — pendiente completar secciones 3.1c, 4 y 6 con sesión real

---

## 1. Resumen Ejecutivo

> **Actualización 2026-07-12:** hasta esta fecha, la aplicación (`app_ui.py`) no podía ejecutarse en absoluto por incompatibilidades de entorno (mediapipe sin API `solutions`, Flet con API renombrada). Los valores de FPS/latencia con MediaPipe reportados en versiones anteriores de este documento estaban marcados como "estimado" porque nunca se pudieron medir. Tras corregir el entorno, se realizó la primera medición real end-to-end con `src/tools/benchmark_mediapipe.py`. **El resultado real no cumple la meta de FPS≥30** — ver detalle en 3.1b. Esto es información nueva y honesta, no un ajuste cosmético.

> **Actualización 2026-07-12 (2) — Entrevista con experto realizada:** se completó `docs/expert_interview_protocol.md` con un fisioterapeuta. Cambios de umbrales resultantes ya aplicados en `config/thresholds.json`:
> - **θc máximo: 15° → 30°** (criterio clínico del experto).
> - **ΔE máximo: 10°** (confirmado sin cambios).
> - **Distancia cámara-usuario: 1.60–2.20 m → 0.50–0.70 m.** Este es el hallazgo más importante de la entrevista: la distancia original (heredada del Capítulo III de la tesis) es de sala de reuniones, no de escritorio. El experto señaló que a esa distancia el usuario tiende a encorvarse para poder ver/leer la pantalla — es decir, **la propia distancia de captura estaba induciendo la mala postura que el sistema intenta medir**, contaminando cualquier lectura de θc tomada bajo ese supuesto. También recomienda que la cámara quede a la altura de los ojos.
> - Pendiente de revisión bibliográfica propia (no cubierto por el experto): ventana temporal de riesgo cervical (`cervical_alert_window_sec`). Pendiente de valor numérico específico: ventana temporal de hombros (el experto pidió que sea distinta de la cervical, sin dar un número).
> - El experto mencionó el **Índice de Discapacidad Cervical (NDI)** como instrumento complementario recomendado — ver sección 4.5.

> **Actualización 2026-09-07 (3) — Auditoría del modelo geométrico y del pipeline.**
> Al revisar por qué la postura "correcta" del sujeto de prueba medía θc ≈ 39-41°
> (sección 4.4) se encontraron **tres errores sistemáticos en `geometry.py`** y
> cuatro fallos de cableado en la aplicación. Todos están corregidos y cubiertos
> por tests de regresión. El detalle está en la nueva sección 3.3; el resumen:
>
> | Hallazgo | Efecto sobre las mediciones previas |
> |---|---|
> | θc se calculaba desde **una sola oreja** (P7), desplazada ~7 cm del plano medio sagital | Sesgo constante de ~13° en postura perfecta. Explica la mediana de 41° medida en postura normal — por encima del umbral clínico de 30°, es decir, **alerta permanente** |
> | Todas las métricas se calculaban sobre coordenadas **normalizadas** (espacio anisótropo 640x480) | ΔE inflado por W/H = 1.333 (un 10° real se leía 13.2°); EAR y MAR inflados por el mismo factor. El umbral EAR de 0.21 equivalía en realidad a exigir un EAR real de 0.157 |
> | El **filtro de parpadeo** del FSM era código muerto (nunca se cumplía su condición) | El temporizador de fatiga se reiniciaba en el primer frame de microapertura: la alerta de 3 s no podía confirmarse |
> | `HistoryLogger.log_event()` **no se llamaba desde ningún sitio** | La tabla `events` quedó vacía en las 3 sesiones registradas. La bitácora de eventos es un entregable del Capítulo IV |
> | `metrics_log.timestamp` guardaba `perf_counter()` en vez de hora de pared | Base de tiempo distinta a la de `events`: imposible correlacionar alertas con métricas |
> | Las sesiones nunca se cerraban (`sessions.ended_at` = NULL en las 3) | El resumen de sesión no podía calcular la duración |
>
> **Consecuencia para el Capítulo IV:** las mediciones de θc, ΔE, EAR y MAR
> anteriores al 2026-09-07 **no son comparables** con los umbrales actuales y
> deben repetirse. Los umbrales acordados con el experto siguen siendo válidos
> (los emitió sobre una descripción verbal de la escala, no sobre lecturas del
> sistema). Las tablas 3.1b y 4.4 están marcadas en consecuencia.

| Indicador | Meta (Cap. III) | Resultado obtenido | ¿Cumple? |
|---|---|---|---|
| FPS sostenido (geometry+FSM puro, sin MediaPipe) | ≥ 30 FPS | ~9,300–16,000 FPS equiv. | ☑ Sí |
| **FPS sostenido real (pipeline completo con MediaPipe)** | **≥ 30 FPS** | **~13.9 FPS antes de optimizar; pendiente de re-medir con el pipeline paralelo (ver 3.1c)** | **⬜ Re-medición pendiente** |
| Latencia geometry+FSM | < 50 ms | 0.11 ms promedio (P95 0.21 ms) | ☑ Sí |
| Latencia P95 geometry+FSM | < 150 ms | 0.21 ms | ☑ Sí |
| **Latencia total del pipeline (con MediaPipe)** | **< 50 ms** | **72.2 ms antes de optimizar; pendiente de re-medir (ver 3.1c)** | **⬜ Re-medición pendiente** |
| Precisión de clasificación | ≥ 90% | Pendiente (validación con usuario/experto real) | ☐ Pendiente |
| Tasa de falsos positivos | < 5% | 0% en pruebas automatizadas (`test_stress.py`, `test_integration_pipeline.py`) | ☑ Sí (solo en condiciones sintéticas) |
| Cobertura de tests | — | 159 tests, 100% en verde (~16 s) | ☑ Sí |
| Uso de CPU | Estable | Sin deriva de latencia (-0.002 a -0.030 ms en 180 frames, geometry puro) | ☑ Sí |
| Uso de RAM | Estable | 0.0 MB de crecimiento en 500 frames (geometry puro) | ☑ Sí |

---

## 2. Configuración del Entorno de Prueba

### Hardware
- **Procesador:** Intel64 Family 6 Model 141 (Tiger Lake/equivalente), 12 núcleos lógicos
- **RAM:** _______________ (no medido en esta ronda — pendiente con `psutil` en sesión real)
- **Cámara:** detectada automáticamente en índice 1 (índice 0 del equipo de prueba entrega frames negros — ver nota en `config/thresholds.json`)
- **Resolución:** 640×480 solicitada @ 30 FPS
- **Distancia sujeto-cámara:** 0.50–0.70 m (corregido — ver actualización del experto arriba; las pruebas de calibración de esta sesión se hicieron ANTES de esta corrección, a una distancia no controlada, lo que probablemente infló los valores de θc medidos en 4.4)

### Software
- **OS:** Windows 11 Home Single Language (build 10.0.26200)
- **Python:** 3.11.15 (⚠️ requerido — ver nota abajo)
- **OpenCV:** 4.10.0
- **MediaPipe:** 0.10.14
- **Flet:** 0.24.1

> **Nota crítica de entorno:** `mediapipe` a partir de ~0.10.30 elimina la API legacy `mp.solutions.*` en los builds para Python 3.13, que es la API que usan `pose_estimator.py` y `face_estimator.py`. El proyecto **requiere Python 3.11** con `mediapipe==0.10.14` (fijado en `requirements.txt`) para funcionar. Ver `README.md`.

### Condiciones de iluminación
- [ ] Iluminación frontal uniforme (condición óptima)
- [ ] Iluminación lateral (condición moderada)
- [ ] Contraluz / iluminación variable (condición adversa)

---

## 3. Resultados de Rendimiento

### 3.1 FPS y Latencia — geometry + FSM puro (test_performance.py)

```
Ejecutar: python -m pytest tests/test_performance.py -v -s
Medido: 2026-07-12, Python 3.11.15, sin MediaPipe (landmarks sintéticos)
```

| Métrica | Valor medido |
|---|---|
| Latencia promedio (geometry, 4 funciones) | 0.062 ms |
| Latencia P50 (geometry) | 0.052 ms |
| Latencia P95 (geometry) | 0.114 ms |
| Latencia P99 (geometry) | 0.197 ms |
| FPS equivalente (geometry sola) | ~16,000 FPS |
| Latencia promedio FSM.update() | 0.001 ms |
| Latencia promedio pipeline geometry+FSM combinado | 0.106 ms |
| Latencia P95 pipeline geometry+FSM combinado | 0.206 ms |
| FPS equivalente pipeline geometry+FSM | ~9,466 FPS |
| Crecimiento de memoria (500 frames) | 0.0 MB |

Estos números confirman que la lógica pura en Python (sin inferencia de MediaPipe) es órdenes de magnitud más rápida que el objetivo — **el cuello de botella real está en la inferencia de MediaPipe**, medida por separado en 3.1b.

### 3.1b FPS y Latencia REAL con MediaPipe (benchmark_mediapipe.py) — ⚠️ Meta no cumplida

```
Ejecutar: python src/tools/benchmark_mediapipe.py --frames 300
Medido: 2026-07-12, cámara física (índice 1), 300 frames, model_complexity=1 (full)
```

| Métrica | Valor medido |
|---|---|
| Latencia BlazePose (pose) — promedio / P95 | 48.0 ms / 57.6 ms |
| Latencia Face Mesh (face) — promedio / P95 | 23.7 ms / 32.2 ms |
| Latencia geometry+FSM (dentro del pipeline real) | 0.41 ms / 0.89 ms (P95) |
| **Latencia total del pipeline — promedio** | **72.2 ms** |
| **Latencia total del pipeline — P95 / P99** | **87.9 ms / 94.2 ms** |
| **FPS equivalente** | **~13.9 FPS** |
| Tasa de detección de pose | 100% |
| Tasa de detección de cara | 91.3% |

**Con `pose_model_complexity=0` (modelo lite)**, medido en la misma máquina: pose 44.4 ms, total 64.3 ms, ~15.6 FPS — mejora marginal, no suficiente para cumplir la meta.

**Causa raíz identificada:** `InferenceThread.run()` en `app_ui.py` ejecuta `pose_est.process()` y `face_est.process()` **secuencialmente en el mismo hilo** (no en paralelo entre sí, aunque sí en paralelo respecto al hilo de UI y de captura). La latencia total es efectivamente la suma de ambos modelos. Un comentario en `test_performance.py` afirmaba que "MediaPipe se ejecuta en paralelo en el hilo de inferencia" — esto es impreciso y debe corregirse en el código fuente.

**Recomendación no implementada aún (requiere decisión):** ejecutar pose y face en dos hilos/futures paralelos reduciría la latencia total a aproximadamente `max(pose, face) ≈ 48 ms` en vez de `pose + face ≈ 72 ms`, acercando el sistema a la meta de 50 ms. Puede no ser suficiente por sí solo en este hardware — combinarlo con `model_complexity=0` y/o reducir la resolución de captura son las siguientes palancas a probar.

### 3.1c Optimizaciones aplicadas y medición del paralelismo

La causa raíz señalada en 3.1b (los dos modelos ejecutándose en secuencia)
**ya está corregida**, junto con otras tres fuentes de coste por frame que la
medición anterior no había aislado porque estaban dentro de `process()`:

| # | Cambio | Por qué costaba |
|---|---|---|
| 1 | **Pose y Face en paralelo** (`ThreadPoolExecutor(2)`) | La latencia total era `pose + face`; ahora es `max(pose, face)` |
| 2 | **Una sola conversión BGR→RGB** compartida por ambos modelos | Se hacía dos veces por frame, una dentro de cada `process()` |
| 3 | **Dos `frame.copy()` eliminados** | Cada `process()` copiaba el frame completo para dibujar sobre él |
| 4 | **`face_refine_landmarks` a `false`** | Añadía una pasada de red por frame (modelo de atención del iris) para producir 10 landmarks que **ninguna métrica de la tesis usa** |
| 5 | **Overlay facial de malla completa → solo los puntos medidos** | `FACEMESH_TESSELATION` dibuja ~2600 segmentos por frame con OpenCV en CPU: costaba más que la propia inferencia de Face Mesh |
| 6 | **Codificación JPEG/Base64 movida al hilo de inferencia** | Se hacía en el event loop de Flet, bloqueando la UI ~2-4 ms por repintado |
| 7 | **Overlay del FPS solo sobre su ROI** | Copiaba los 640×480 y hacía un `addWeighted` completo para oscurecer un rectángulo de 160×30 |

**Verificación del paralelismo** (`benchmark_mediapipe.py --synthetic`,
60 frames, mismo equipo):

| Configuración | Latencia total media | FPS equivalente |
|---|---|---|
| Secuencial | 21.57 ms | 46.4 |
| Paralelo | 16.77 ms | 59.6 |
| **Reducción** | **−22.3% (factor 1.29×)** | |

El bloque paralelo midió 16.51 ms frente a un `pose` de 16.91 ms y un `face` de
3.98 ms en secuencial: el conjunto cuesta lo que el modelo más caro, lo que
confirma que **MediaPipe libera el GIL durante la inferencia** y los dos grafos
se solapan de verdad.

> ⚠️ **Estos números son sintéticos** (frames de ruido, sin sujeto). Sobre ruido
> MediaPipe no detecta a nadie y no llega a ejecutar la red de landmarks, así
> que `face` sale artificialmente barato y la ganancia del paralelismo queda
> **subestimada**. Con las cifras de cámara real de 3.1b (pose 48.0 ms, face
> 23.7 ms), pasar de `48.0 + 23.7 = 71.7` a `max(48.0, 23.7) ≈ 48` ms es una
> reducción del orden del 33% solo por el cambio 1, sin contar los cambios 2-7.
>
> **Pendiente: repetir `benchmark_mediapipe.py --frames 300` con cámara y
> sujeto real** para obtener la cifra que va al Capítulo IV. El script mide
> ahora las dos configuraciones en la misma sesión, de modo que la comparación
> secuencial/paralelo queda hecha bajo idénticas condiciones de luz y cámara.
>
> Si aun así no se alcanzan los 30 FPS en el equipo de prueba, quedan dos
> palancas ya implementadas y documentadas en `config/thresholds.json`:
> `pose_model_complexity: 0` (modelo lite) y `pose_every_n_frames: 2`
> (BlazePose 1 de cada 2 frames — θc y ΔE se evalúan en ventanas de 5-8 s, así
> que no afecta a la detección). Cualquiera que se use debe declararse en el
> reporte.

### 3.2 Uso de Recursos (10 minutos de ejecución continua)

| Recurso | Inicio | 5 min | 10 min | Variación |
|---|---|---|---|---|
| CPU (%) | ___ | ___ | ___ | ___ |
| RAM (MB) | ___ | ___ | ___ | ___ |
| VRAM (MB) | N/A | N/A | N/A | N/A |

*(Pendiente: requiere sesión de 10 min con `psutil` corriendo la app real, no automatizado en esta ronda)*

---

## 3.3 Corrección del modelo geométrico (auditoría 2026-09-07)

Esta sección documenta los errores sistemáticos encontrados en `geometry.py` y
el efecto que tenían sobre las métricas. Es material directo para el apartado
de validez de constructo del Capítulo IV: explica por qué las mediciones
previas no coincidían con la escala de referencia, y por qué las nuevas sí.

### 3.3.1 Sesgo por referencia unilateral de la cabeza

La formulación original tomaba **P7 (oreja izquierda)** como punto de
referencia de la cabeza:

```
V_cervical = P7 − midpoint(P11, P12)
```

La oreja izquierda está desplazada unos **7 cm del plano medio sagital**,
mientras que el punto medio escapular sí está sobre ese plano. El vector
cervical nace por tanto inclinado lateralmente aunque la postura sea perfecta,
y ese componente lateral entra en el arcocoseno como si fuera inclinación.

Cuantificación con un modelo antropométrico (cuello de 22 cm, sujeto a 0.6 m):

| Separación lateral de la oreja | θc medido en postura perfecta |
|---|---|
| 0 cm (hipotético) | 0.00° |
| 4 cm | 7.77° |
| 7 cm (adulto medio) | **13.42°** |
| 9 cm | 17.06° |

Peor aún, el sesgo **dominaba la señal**: con la oreja a 7 cm, 12 cm de cabeza
adelantada real solo movían θc de 13.4° a 25.3°, es decir ~1° por centímetro,
con casi la mitad del rango consumido por un offset constante.

**Corrección:** usar el **punto medio inter-auricular** `midpoint(P7, P8)`, que
sí cae sobre el plano medio. Es el mismo cambio que hace clínicamente la
diferencia entre medir en el plano sagital y medir una mezcla de planos.

```
V_cervical = midpoint(P7, P8) − midpoint(P11, P12)
```

### 3.3.2 Espacio de coordenadas anisótropo

MediaPipe normaliza `x` por el ancho del frame y `y` por el alto. En 640×480
ese espacio **no es isótropo**: un mismo desplazamiento físico vale 1/640 en x
y 1/480 en y. Calcular ángulos y razones directamente sobre esas coordenadas
introduce un factor de escala W/H = 1.333.

| Métrica | Valor real | Valor que se medía | Consecuencia |
|---|---|---|---|
| ΔE | 10.0° | 13.2° | El umbral de 10° confirmado por el experto disparaba en realidad a **7.5° reales** |
| EAR | 0.210 | 0.280 | Exigir "medido ≤ 0.21" equivalía a exigir un EAR real de **0.157**: el ojo casi cerrado del todo. La detección de fatiga ocular era prácticamente inalcanzable |
| MAR | 0.450 | 0.600 | Mismo efecto sobre el umbral de bostezo |

**Corrección:** convertir a píxeles isótropos (`x·W`, `y·H`, `z·W`) antes de
operar. Verificado: el factor medido pasa de 1.333 a 1.000 exacto.

### 3.3.3 Escala resultante, ya corregida

| Desplazamiento anterior de la cabeza | θc | Componente sagital | CVA equivalente |
|---|---|---|---|
| 0 cm | 0.0° | 0.0° | 90° |
| 3 cm | 7.8° | 7.8° | 82° |
| 6 cm | 15.3° | 15.3° | 75° |
| 9 cm | 22.3° | 22.3° | 68° |
| 12 cm | **28.6°** | 28.6° | 61° |

El umbral de 30° fijado por el experto corresponde ahora a unos **12 cm de
desplazamiento anterior de la cabeza**, una magnitud clínicamente interpretable.
Y θc es ahora **invariante a la distancia del sujeto** (verificado: variación
< 0.5° entre 4 y 9 px/cm), como debe ser un ángulo.

### 3.3.4 Métricas nuevas derivadas de la corrección

Al trabajar en un espacio métricamente consistente, se pueden derivar tres
indicadores que antes no tenían sentido:

- **Descomposición de θc** en componente **sagital** (cabeza adelantada, ≈ 90° − CVA)
  y **lateral** (inclinación de la cabeza). Permite reportar por separado en el
  Capítulo IV dos gestos clínicamente distintos que θc mezclaba.
- **Distancia cámara-usuario estimada** a partir del ancho biacromial aparente
  (modelo estenopeico). Sin ella, el rango 0.50–0.70 m validado por el experto
  era una recomendación que el sistema no podía verificar; ahora avisa al
  usuario y **congela los temporizadores** fuera de rango, para no acumular
  tiempo de riesgo sobre mediciones no interpretables.
- **PERCLOS y tasa de parpadeo** (ver 3.4).

### 3.3.5 Filtrado temporal

En la sesión del 2026-07-12, ΔE osciló entre −20.5° y +3.0° dentro de la misma
sesión sin que el sujeto cambiara de postura: jitter de estimación, no señal.
Alimentar el FSM con esa señal cruda provoca *chattering* en el umbral — la
condición se activa y desactiva decenas de veces por segundo, el temporizador
se reinicia sin parar y **la alerta no se confirma aunque el riesgo sea real**.

Se añadió un filtro de dos etapas (mediana móvil de 5 muestras + EMA α=0.35)
sobre θc, ΔE, EAR y MAR. Retardo introducido ≈ 0.3 s, despreciable frente a
ventanas de 3–8 s. Ver `src/vision/smoothing.py`.

## 3.4 Indicadores de fatiga añadidos

La regla original —"EAR ≤ 0.21 durante 3 s continuos"— detecta el **ojo cerrado
tres segundos seguidos**, que es un microsueño, no fatiga visual: un trabajador
con astenopia mantiene los ojos abiertos. Se añadieron los dos indicadores que
la literatura sí asocia al estado del operador:

| Indicador | Definición | Estado |
|---|---|---|
| **PERCLOS** | % de tiempo con el ojo cerrado en ventana móvil de 60 s | **Alerta activa**, umbral 0.15 (Wierwille & Ellsworth 1994; Dinges & Grace 1998). Valor de **literatura de conducción**, no validado por el experto entrevistado — declararlo así |
| **Tasa de parpadeo** | Parpadeos/min, contando solo cierres de 100–400 ms | **Solo se mide y se registra**. No dispara alertas: el experto no fijó valores de corte (Bloque C1 sin responder) y el sistema no debe inventarlos. Pendiente del Módulo II del protocolo v2 |

---

## 4. Resultados de Precisión

### 4.1 Protocolo de validación

- **N participantes:** ___
- **Duración por sesión:** ___ minutos
- **Condiciones evaluadas:** Postura correcta, cabeza adelantada, hombros asimétricos, fatiga ocular

### 4.2 Matriz de confusión — Clasificación Postural

| | Predicho: Normal | Predicho: Riesgo |
|---|---|---|
| **Real: Normal** | VP = ___ | FP = ___ |
| **Real: Riesgo** | FN = ___ | VN = ___ |

**Precisión:** ___ %  
**Recall:** ___ %  
**F1-Score:** ___

### 4.3 Resultados EAR (Fatiga Ocular)

| Condición | EAR promedio medido | ¿Bajo umbral 0.21? |
|---|---|---|
| Ojos abiertos | ___ | No |
| Parpadeo normal (< 400 ms) | ___ | ___ |
| Fatiga real (> 3 s) | ___ | Sí |
| Falsos positivos (parpadeo) | ___ de ___ casos | ___% |

### 4.4 Resultados Ángulo Cervical (θc)

> ⚠️ **Estos valores están obsoletos por dos razones acumuladas. No usarlos.**
>
> **(1) Distancia no controlada.** Se midieron antes de corregir la distancia
> cámara-usuario a 0.50–0.70 m (ver actualización del experto en la sección 1).
>
> **(2) Modelo geométrico con sesgo — causa principal, identificada el
> 2026-09-07.** El baseline de θc≈39-41° en postura "correcta" **no era un
> efecto de la distancia**: era el sesgo sistemático de calcular θc desde una
> sola oreja, sumado al factor de escala del espacio anisótropo (sección 3.3).
> El diagnóstico original de esta tabla —"la distancia infló los valores"— era
> incorrecto: θc es un ángulo y, con la geometría corregida, es invariante a la
> distancia del sujeto (verificado en `tests/test_geometry.py::TestCervicalAngle::
> test_invariant_to_subject_distance`).
>
> Lo que sí sigue siendo válido de aquella sesión es la **sensibilidad
> diferencial**: en la prueba de transición continua (normal → adelantada
> gradual) se registró una diferencia de +13° entre baseline y pico, muy por
> encima del ruido (~2-3°). La métrica respondía a cambios reales de postura;
> lo que estaba mal era su cero y su escala.
>
> **Siguiente paso:** repetir la tabla completa con `src/tools/calibration_mode.py`
> a 0.50–0.70 m, cámara a la altura de los ojos y el código actual. Con la
> geometría corregida, los valores esperados de la columna de referencia sí son
> alcanzables (ver la tabla de escala en 3.3.3).

| Postura | θc medido (°) — **OBSOLETO**, geometría con sesgo | θc esperado (°) (escala corregida) | Nueva medición |
|---|---|---|---|
| Postura correcta | ~39-41° | < 5° | ⬜ pendiente |
| Inclinación leve (~6 cm) | — | ~15° | ⬜ pendiente |
| Inclinación moderada (~9-12 cm) | — | ~22-29° | ⬜ pendiente |
| Inclinación severa / extrema | ~52-57° | > 35° | ⬜ pendiente |

**Métricas adicionales a registrar en la re-medición** (ya las produce
`calibration_mode.py`): componente sagital, componente lateral y distancia
estimada, columna a columna en el CSV. La componente sagital es la que se puede
contrastar directamente con el CVA clínico (`CVA ≈ 90° − θc_sagital`), y es por
tanto la que da la comparación más fuerte con la literatura de fisioterapia.

### 4.5 Instrumento Complementario Recomendado — Índice de Discapacidad Cervical (NDI)

El experto (fisioterapeuta) recomendó el **Índice de Discapacidad Cervical** (*Neck Disability Index*, Vernon & Mior, 1991) como instrumento complementario de validación. A diferencia de θc (medición geométrica objetiva en tiempo real), el NDI es un **cuestionario auto-reportado** de 10 ítems (intensidad de dolor, cuidado personal, levantar objetos, lectura, cefalea, concentración, trabajo, manejo, sueño, recreación), cada uno puntuado 0-5, normalizado a porcentaje de discapacidad percibida.

**Uso propuesto:** no reemplaza la medición de θc — sirve como variable de resultado (outcome) para una validación pre/post: aplicar el NDI antes de que el usuario empiece a usar el sistema, y de nuevo tras un periodo de uso (p. ej. 2-4 semanas), para ver si el uso del sistema se asocia con una reducción del NDI auto-reportado. Esto es un instrumento clínico estandarizado y validado — **la versión oficial del cuestionario debe obtenerse de la publicación original** (Vernon & Mior, 1991) o una traducción/adaptación validada al español, no reproducirse de memoria aquí.

**Estado:** no implementado. Pendiente decidir si entra en el alcance de esta tesis (medición de un sistema de detección en tiempo real) o queda como trabajo futuro (validación de impacto clínico longitudinal) — ver sección 7.3.

---

## 5. Análisis de Robustez

### 5.1 Pruebas de Estrés (Automatizadas — test_stress.py)

| Escenario | Resultado | Estado |
|---|---|---|
| Oclusión parcial (pose_detected=False) | Sin alertas ni crashes | ✅ PASÓ |
| Recuperación tras oclusión | Timer reinicia desde 0 | ✅ PASÓ |
| Oclusión facial parcial (visibility baja) | EAR sigue siendo válido | ✅ PASÓ |
| Ruido gaussiano σ=0.005 en landmarks | σ(θc)=0.558°, max_dev=2.635° | ✅ PASÓ |
| Ruido en EAR (σ=0.002) | σ(EAR)=0.00768 (< 0.05) | ✅ PASÓ |
| Oscilación en umbral (chattering) | 0 alertas espurias | ✅ PASÓ |
| Landmarks fuera del frame (dist. < 0.50m) | Sin excepción, float válido | ✅ PASÓ |
| Landmarks comprimidos (dist. > 0.70m) | Ángulo en [0°, 180°] | ✅ PASÓ |
| Métricas extremas (180°, -45°, NaN) | Sin crash | ✅ PASÓ |
| Transición riesgo → normal → riesgo | Timer reinicia correctamente | ✅ PASÓ |
| Condiciones simultáneas (postura+fatiga) | Ambas alertas generadas: {cervical_angle, eye_fatigue} | ✅ PASÓ |
| Carga sostenida 180 frames | Deriva de latencia: -0.002 ms | ✅ PASÓ |
| FSM sin alertas espurias (postura normal) | 0 alertas en 180 frames | ✅ PASÓ |
| **Distancia < 0.50 m (hardware real)** | Pendiente validación con usuario | ⬜ Pendiente |
| **Distancia > 0.70 m (hardware real)** | Pendiente validación con usuario | ⬜ Pendiente |
| **Variabilidad lumínica real** | Pendiente validación con usuario | ⬜ Pendiente |

*Valores re-medidos el 2026-07-12 tras corregir un bug real en `calculate_shoulder_asymmetry` (devolvía 180° en vez de ≈0° para hombros nivelados). Ese bug no afectaba estas filas de estrés (son sobre θc/EAR), pero sí invalidaba cualquier medición previa de ΔE — no había ninguna registrada en este documento, así que no hay valores previos que corregir en esta tabla.*

### 5.2 Filtro de Parpadeo Fisiológico

| Duración del cierre de ojos | ¿Generó alerta? | Comportamiento esperado |
|---|---|---|
| 50 ms | ☐ Sí / ☐ No | No |
| 200 ms | ☐ Sí / ☐ No | No |
| 400 ms | ☐ Sí / ☐ No | Límite |
| 1000 ms | ☐ Sí / ☐ No | No (< 3 s ventana) |
| 3500 ms | ☐ Sí / ☐ No | Sí |

---

## 6. Bitácora de Sesión de Prueba con Usuario Real

> Generada automáticamente por `HistoryLogger` → `data/session_XXXXXXXX_report.json`

```json
{
  "summary": {
    "session_id": "...",
    "duration_min": 0.0,
    "total_events": 0,
    "events_by_type": {},
    "avg_theta_c_deg": 0.0,
    "max_theta_c_deg": 0.0,
    "avg_ear": 0.0,
    "avg_fps": 0.0
  }
}
```

*(Pegar aquí el JSON exportado de la sesión de prueba real)*

---

## 7. Conclusiones

### 7.1 Cumplimiento de Objetivos

| Objetivo específico | Estado | Observaciones |
|---|---|---|
| 1.3.2.1 Calibración de umbrales ergonómicos | ◐ Parcial | Entrevista con experto realizada (θc, ΔE, distancia). Pendiente: ventanas temporales (revisión bibliográfica propia) y re-medición a la distancia corregida |
| 1.3.2.2 Implementación BlazePose + Face Mesh | ☐ | |
| 1.3.2.3 Modelo geométrico θc y ΔE | ☐ | |
| 1.3.2.4 Cálculo EAR y detección de bostezo | ☐ | |
| 1.3.2.5 Integración con UI Flet | ☐ | |
| 1.3.2.6 Validación de FPS, latencia, precisión | ☐ | |

### 7.2 Limitaciones Identificadas

1. **La meta de FPS≥30 / latencia<50ms está pendiente de re-medición.** La medición de 72.2 ms / ~13.9 FPS corresponde al pipeline secuencial anterior. La causa raíz (los dos modelos en secuencia) y otras seis fuentes de coste por frame ya están corregidas (sección 3.1c), con un paralelismo verificado de 1.29× sobre frames sintéticos. **Falta ejecutar `benchmark_mediapipe.py --frames 300` con cámara y sujeto real** para la cifra definitiva del Capítulo IV. Si no se alcanzan los 30 FPS, quedan dos palancas ya implementadas (`pose_model_complexity: 0`, `pose_every_n_frames: 2`) que deberán declararse si se usan.
2. El proyecto requiere específicamente **Python 3.11** — `mediapipe` en builds recientes para Python 3.13 eliminó la API `mp.solutions.*` que usa este código. Esto debe documentarse claramente para quien reproduzca el entorno (ya corregido en `README.md`).
3. La validación de precisión (≥90%, matriz de confusión) y las pruebas de distancia/iluminación real siguen pendientes de una sesión con usuario/experto real — no son automatizables. El Bloque E del protocolo v2 está diseñado para producir directamente las filas de la matriz de confusión de la sección 4.2.
4. **Todas las mediciones de métricas anteriores al 2026-09-07 son inválidas** por los errores del modelo geométrico documentados en la sección 3.3. La sección 4.4 está marcada en consecuencia.
5. **Los umbrales provienen de un único informante experto** (N=1), y varios de ellos quedaron sin valor clínico: las dos ventanas temporales posturales, el cooldown entre alertas y los umbrales de aviso (semáforo amarillo, hoy cableado al 70% del rojo sin base clínica). El protocolo v2 (`docs/expert_interview_protocol_v2.md`) los aborda y propone ampliar a tres informantes en el módulo postural.
6. **Umbrales que siguen siendo de literatura o de ingeniería, no validados clínicamente por el experto entrevistado:** EAR (0.21, literatura de visión artificial), PERCLOS (0.15/60 s, literatura de conducción), MAR de bostezo (0.45, estimación de ingeniería sin fuente). Deben declararse como tales en el Capítulo IV.
7. **Ambigüedad de escala en el umbral cervical.** Al experto se le pidió un valor en la escala θc advirtiéndole que θc ≈ 90° − CVA, y respondió "de 30° para abajo". No consta si razonaba en θc o en CVA, y el significado clínico es opuesto. El bloque A0 del protocolo v2 lo resuelve con una escala visual.
8. **La estimación de distancia usa una media poblacional** de ancho biacromial (0.38 m) y un FOV de cámara asumido (60°). Es suficiente para avisar del encuadre, pero debe calibrarse con una medición con cinta métrica antes de reportar distancias en el Capítulo IV (`camera.horizontal_fov_deg`).

### 7.3 Trabajo Futuro

1. Soporte para múltiples usuarios simultáneos
2. Exportación de reportes en PDF
3. Integración con sistemas de RR.HH.
4. Validación de impacto clínico longitudinal con el Índice de Discapacidad Cervical (NDI) pre/post uso — ver sección 4.5
5. Revisión bibliográfica propia para las ventanas temporales de alerta (postural y de hombros), no cubiertas por el experto
6. Aplicar el protocolo de entrevista v2 (`docs/expert_interview_protocol_v2.md`) en sus tres módulos, para cerrar los 9 parámetros que siguen sin base clínica
7. Calibración individual del EAR al inicio de cada sesión (el umbral único de 0.21 ignora la variabilidad anatómica del ojo entre personas)
8. Habilitar la alerta por tasa de parpadeo cuando un especialista fije los valores de corte

---

## 8. Referencias de Umbrales Utilizados

| Parámetro | Valor Final | Fuente |
|---|---|---|
| θc máximo (cabeza adelantada) | 30° | Entrevista con experto (fisioterapeuta), 2026-07-12 — antes 15° (literatura) |
| ΔE máximo (asimetría de hombros) | 10° | Entrevista con experto — confirmado sin cambios |
| Distancia cámara-usuario | 0.50–0.70 m | Entrevista con experto — antes 1.60–2.20 m (Cap. III, corregido por ser excesivo) |
| EAR umbral fatiga | 0.21 | Soukupová & Čech (2016) — sin objeción del experto |
| Apertura bucal (bostezo) | 0.45 | Estimación de ingeniería — sin objeción del experto |
| Ventana temporal postural (cervical) | 5.0 s (provisional) | Makhmudov et al. (2024) — **pendiente** revisión bibliográfica propia (el experto no lo definió clínicamente; solo acotó que >20 min es excesivo) |
| Ventana temporal asimetría de hombros | 8.0 s (provisional) | **Ingeniería** — el experto pidió una ventana distinta a la cervical (Bloque B2) sin dar valor. Se fija en 8.0 s con el criterio de que la asimetría escapular es un patrón más crónico y una ventana mayor evita disparos por gestos transitorios. Pendiente de confirmación en la ronda 2 |
| Ventana temporal fatiga | 3.0 s | Xing et al. (2023) |
| Filtro parpadeo / periodo de gracia | 100–400 ms | Literatura fisiológica. `blink_max_ms` actúa además como periodo de gracia del temporizador de fatiga |
| PERCLOS umbral / ventana | 0.15 / 60 s | Wierwille & Ellsworth (1994); Dinges & Grace (1998), FHWA-MCRT-98-006 — **literatura de conducción**, no validada para trabajo de oficina ni por el experto entrevistado |
| Tasa de parpadeo | *(solo se mide, no alerta)* | Sin valor de corte: el Bloque C1 quedó sin responder por estar fuera de la especialidad del informante |
| Umbrales de aviso (semáforo amarillo) | 70% del umbral rojo | **Sin base clínica** — valor cableado, nunca preguntado al experto. Bloques A3/B3 del protocolo v2 |
| Cooldown entre alertas | 30 s | **Sin validar** — Bloque D1 no respondido en la ronda 1. Probablemente demasiado corto (2 modales por minuto en riesgo sostenido) |
| Ancho biacromial (estimación de distancia) | 0.38 m | Media poblacional adulta (Pheasant & Haslegrave, *Bodyspace*, 3ª ed.) — valor de referencia, no medición individual |
| Filtrado temporal (mediana 5 + EMA α=0.35) | — | Decisión de ingeniería ante el jitter medido de MediaPipe (ΔE oscilando −20.5° a +3.0° sin cambio de postura). Ver 3.3.5 |

---

*Documento generado automáticamente por el sistema. Última actualización: _______________*
