# Checklist de Pruebas Manuales

> Lo que **no** puede automatizarse: todo lo que necesita una cámara real, una
> persona delante o tus propios ojos. Los 201 tests de `pytest` no abren la
> cámara ni ejecutan MediaPipe por diseño, así que "todo en verde" no dice nada
> sobre el FPS real, la precisión ni sobre si el icono de bandeja se ve.
>
> Orden recomendado: A → B → C → D. No pases a D (las mediciones del Capítulo
> IV) sin haber cerrado A y C, porque un fallo ahí invalida las mediciones.

**Equipo de prueba:** _______________  **Fecha:** _______________

---

## A. Monitoreo en segundo plano y alertas (~15 min)

Arranca con:

```bash
python -m src.ui.app_ui
```

### A.1 Bandeja del sistema

| # | Prueba | Resultado esperado | ✓ |
|---|---|---|---|
| 1 | Mira junto al reloj de Windows | Aparece el icono de la app (puede estar en el desplegable "mostrar iconos ocultos" — arrástralo a la barra) | ☐ |
| 2 | Click derecho sobre el icono | Menú con **Abrir panel**, **Pausar monitoreo**, **Salir** | ☐ |
| 3 | Cierra la ventana con la **X** | La ventana desaparece **pero el proceso sigue vivo**: el icono continúa ahí y sale un globo avisando | ☐ |
| 4 | Con la ventana cerrada, comprueba el LED de la webcam | **Sigue encendido**: se está midiendo en segundo plano | ☐ |
| 5 | Click en el icono (o *Abrir panel*) | Vuelve el panel, con el feed de video otra vez en marcha | ☐ |

### A.2 Pausa — privacidad

| # | Prueba | Resultado esperado | ✓ |
|---|---|---|---|
| 6 | Menú de bandeja → **Pausar monitoreo** | El **LED de la webcam se apaga** (la cámara se libera de verdad) | ☐ |
| 7 | Vuelve a abrir el menú | La opción ahora dice **Reanudar monitoreo** | ☐ |
| 8 | Estando pausado, abre el panel | El feed está congelado; no se acumulan métricas nuevas | ☐ |
| 9 | **Reanudar monitoreo** | El LED vuelve, el feed se reanuda y los temporizadores del FSM arrancan **en 0.0 s** | ☐ |

> El punto 6 es el que hace creíble la pausa: si el LED siguiera encendido, el
> usuario no tendría forma de verificar que no se le está grabando.

### A.3 Alertas no intrusivas

> **Antes de empezar: desactiva "No molestar"** (Configuración → Sistema →
> Notificaciones), o añade *Monitor Postural y Fatiga* como app prioritaria.
> Con "No molestar" activo, Windows guarda el aviso en el centro de
> notificaciones (`Win + N`) pero **no lo muestra**. Revisa también las reglas
> automáticas por horario: las sesiones del 2026-09-24 fueron a las 4 AM.

| # | Prueba | Resultado esperado | ✓ |
|---|---|---|---|
| 10 | Con la ventana **cerrada** (en bandeja), ponte a escribir en Word o el navegador y **adelanta la cabeza 6+ segundos** | Aparece un **toast** en la esquina inferior derecha: "Cabeza adelantada" | ☐ |
| 11 | Mientras el toast está visible, sigue escribiendo | **No roba el foco**: las teclas siguen yendo a tu aplicación | ☐ |
| 12 | Espera ~5 s sin tocar nada | El toast **se cierra solo** | ☐ |
| 13 | Abre el Centro de Actividades (`Win + N`) | El aviso quedó archivado ahí | ☐ |
| 14 | Inclina un hombro ~9 s | Toast "Hombros desnivelados" (ventana de 8 s, distinta de la cervical) | ☐ |
| 15 | Abre el panel tras las alertas | El bloque **Alertas recientes** lista las últimas 5, con hora | ☐ |
| 16 | Deja pasar una sesión con varias alertas seguidas | Nunca aparece un modal bloqueante por postura | ☐ |

### A.4 Calibración del EAR y alerta aguda

> Desde el 2026-09-24 el umbral de ojo cerrado se calibra para cada usuario
> durante los **primeros 20 s con la cara detectada**. Mira a la pantalla con
> normalidad durante ese tiempo: si entornas los ojos, el umbral saldrá bajo.

| # | Prueba | Resultado esperado | ✓ |
|---|---|---|---|
| 17a | Arranca la app, **siéntate recto** y mira a la pantalla con normalidad 20 s | La banda superior dice *"Calibrando… siéntate recto"*. Al terminar, la fila del EAR muestra **"umbral 0.1xx"** y la cervical **"Desviación cervical (Δθc) · neutro NN°"**. Anota ambos: _______ / _______ | ☐ |
| 17a2 | Sentado recto 1 min tras calibrar | Δθc se queda cerca de **0°** (verde) y **no** salen alertas de cabeza adelantada. *Antes marcaba 35–45° y avisaba cada 30 s* | ☐ |
| 17a3 | Adelanta la cabeza a propósito 6 s | Δθc sube. **Anota hasta cuánto: _____°**. Si pasa de 15°, sale el aviso. *Este número es clave: ver D.3* | ☐ |
| 17a4 | Bandeja → *Recalibrar postura y ojos* | Vuelve la banda de calibración y se repite | ☐ |
| 17b | Tras calibrar, trabaja 2 min con normalidad (parpadeando) | PERCLOS se queda **bajo** (< 0.10) y **no** sale somnolencia. *Antes de la corrección daba ~0.65 y una alerta cada 30 s* | ☐ |
| 17c | Cierra los ojos 4 s seguidos | Toast de **fatiga ocular** | ☐ |
| 17 | Cierra los ojos de forma intermitente durante ~1 min (PERCLOS ≥ 15% de la ventana de 60 s) | Aparece el **modal bloqueante** de somnolencia, no un toast | ☐ |
| 18 | Pulsa "Entendido, voy a pausar" | El modal **desaparece por completo**. *Antes quedaba un recuadro vacío que no se podía cerrar* | ☐ |
| 18b | Provoca una segunda alerta de somnolencia tras la primera | Vuelve a aparecer con su texto y se cierra igual de bien | ☐ |

> Si el 17 te resulta difícil de provocar, baja temporalmente
> `fatigue.perclos_threshold` a `0.05` en `config/thresholds.json`.
> **Devuélvelo a 0.15 antes de cualquier medición del Capítulo IV.**

### A.5 Cierre limpio — esto afecta a tu bitácora

| # | Prueba | Resultado esperado | ✓ |
|---|---|---|---|
| 19 | Menú de bandeja → **Salir** | El proceso termina de verdad (compruébalo en el Administrador de tareas: no queda `python.exe`) | ☐ |
| 20 | Tras salir, ejecuta la consulta de abajo | `ended_at` **NO** es NULL | ☐ |

```bash
python src/tools/stats_report.py --list-sessions
```

> En las tres sesiones de julio `ended_at` quedó NULL y por eso el resumen no
> podía calcular la duración. Si vuelve a salir NULL, **párate aquí**: el
> Capítulo IV necesita ese dato.

### A.6 La bitácora de eventos

| # | Prueba | Resultado esperado | ✓ |
|---|---|---|---|
| 21 | Tras una sesión con alertas, consulta la tabla `events` | **Tiene filas.** En las tres sesiones previas estaba vacía por un bug ya corregido | ☐ |

```bash
python src/tools/stats_report.py --session <id>
```

### A.7 Ahorro de CPU

| # | Prueba | Resultado esperado | ✓ |
|---|---|---|---|
| 22 | Con el panel abierto, mira el uso de CPU de `python.exe` en el Administrador de tareas. Anótalo | _____ % | ☐ |
| 23 | Cierra la ventana (queda en bandeja) y espera 30 s. Vuelve a mirar | **Menor** que el anterior: ya no se codifica JPEG/Base64 | ☐ |

> Anota ambas cifras: van a la sección §3.2 del reporte, que sigue vacía.

---

## B. Regímenes de alerta configurables (~10 min)

Edita `config/thresholds.json` → `alerts` y **reinicia la app** en cada caso.

| # | Config | Resultado esperado | ✓ |
|---|---|---|---|
| 24 | `"mode": "modal"` | **Todas** las alertas bloquean, incluida la postural. Es el comportamiento original del Capítulo III | ☐ |
| 25 | `"mode": "toast"` | **Ninguna** alerta bloquea, ni siquiera la somnolencia | ☐ |
| 26 | `"mode": "auto"` (por defecto) | Postura → toast; somnolencia → modal | ☐ |
| 27 | `"toast_enabled": false` | Sin notificaciones del sistema; solo queda el rastro en el panel | ☐ |
| 28 | `"background_monitoring": false` | Cerrar la ventana **termina** la aplicación (comportamiento anterior) | ☐ |
| 29 | `"blocking_alert_types": ["cervical_angle"]` | Ahora la postura bloquea y la somnolencia no | ☐ |

> **Deja `mode` en `auto` al terminar**, salvo que decidas validar el régimen
> `modal` con usuarios — en cuyo caso declara cuál usaste en cada sesión.

---

## C. Robustez con cámara real (~20 min)

Estas son las filas marcadas "⬜ Pendiente validación con usuario" en
`docs/validation_report.md` §5.1.

| # | Prueba | Resultado esperado | ✓ |
|---|---|---|---|
| 30 | Tápate la cara con la mano 10 s | Sin crash. Los temporizadores se congelan o reinician, no se dispara una alerta falsa | ☐ |
| 31 | Sal completamente del encuadre y vuelve | Banner "No se detecta al usuario"; al volver, retoma sin alertas acumuladas | ☐ |
| 31b | Siéntate quieto dentro del rango 1 min | El banner de encuadre **no aparece ni parpadea**. *Antes alternaba constantemente con el usuario quieto* | ☐ |
| 32 | Acércate a ~30 cm | Banner "Demasiado cerca" tras ~1.5 s; los temporizadores posturales se congelan | ☐ |
| 33 | Aléjate a ~1.2 m | Banner "Demasiado lejos" tras ~1.5 s | ☐ |
| 34 | Gira la cabeza 90° | `pose_valid` pasa a falso; no se mide θc sobre landmarks inventados | ☐ |
| 35 | Apaga la luz de la habitación | La detección degrada sin crashear | ☐ |
| 36 | Contraluz (ventana detrás de ti) | Anota si la detección aguanta — es una condición declarada en §2 del reporte | ☐ |
| 37 | Que se siente **otra persona** en tu lugar | El sistema mide a quien esté delante, sin arrastrar el estado anterior | ☐ |
| 38 | Dos condiciones a la vez (mala postura + ojos cerrados) | Ambas alertas se registran; no se pierde ninguna en la bitácora | ☐ |
| 39 | **Sesión larga: 30-60 min continuos** | Sin fuga de memoria (mira `python.exe` en el Administrador de tareas), FPS estable, sin crash | ☐ |

> El 39 es el que más te conviene hacer temprano: un fallo de estabilidad
> descubierto durante la sesión con participantes te cuesta la sesión entera.

---

## D. Mediciones para el Capítulo IV

Estas no son "pruebas": son **los datos que te faltan** para cerrar la tesis.
Hazlas en este orden.

### D.1 Calibrar el FOV de la cámara *(una sola vez, antes de todo lo demás)*

| # | Paso | ✓ |
|---|---|---|
| 40 | Siéntate a **0.60 m** medidos con cinta métrica, cámara a la altura de los ojos | ☐ |
| 41 | Lee la distancia estimada en el panel de telemetría | ☐ |
| 42 | Ajusta `camera.horizontal_fov_deg` hasta que coincida con la cinta | ☐ |
| 43 | Anota el valor final: _______ ° | ☐ |

```bash
python src/tools/calibration_mode.py --duration 60
```

> Sin esto, todas las distancias que reportes en el Capítulo IV salen de un FOV
> **asumido** (60°), no medido.

### D.2 FPS real con MediaPipe *(la cifra que falta en §3.1c)*

| # | Paso | ✓ |
|---|---|---|
| 44 | Ejecuta el benchmark con un **sujeto real** delante (no la habitación vacía) | ☐ |
| 45 | Anota el resultado en `docs/validation_report.md` §3.1c | ☐ |
| 46 | Si no llegas a 30 FPS: prueba `pose_model_complexity: 0`, luego `pose_every_n_frames: 2`, y **declara cuál usaste** | ☐ |

```bash
python src/tools/benchmark_mediapipe.py --frames 300
```

> Sobre una habitación vacía MediaPipe no llega a ejecutar la red de landmarks
> y el resultado sale artificialmente rápido. Tiene que haber alguien delante.

### D.3 Medir la desviación de θc — ⚠️ LA MÁS IMPORTANTE

> **Actualizado el 2026-09-24.** Con cámara frontal el θc **absoluto** no es
> medible (§3.7 del reporte: la profundidad `z` de MediaPipe se mide desde la
> cadera, fuera del encuadre, y un usuario recto marca ~48°). Lo que hay que
> medir es la **desviación Δθc** respecto a la postura neutra.
>
> Esta medición decide si el umbral provisional de **15°** sirve. Si una
> cabeza adelantada real da menos de 15° de Δθc, **el sistema estaría ciego a
> ella** — en julio la posición más adelantada posible dio +13°.

Con el FOV ya calibrado, a 0.50–0.70 m y cámara a la altura de los ojos. Mide
el adelantamiento de la cabeza con una regla (distancia del trago de la oreja
a la vertical del hombro, o con ayuda de otra persona mirando de lado):

| # | Postura | θc medido | Δθc (θc − neutro) | Relación cuello/hombros | ✓ |
|---|---|---|---|---|---|
| 47 | **Neutra**, sentado recto (referencia) | _____ | 0 | _____ | ☐ |
| 48 | Cabeza adelantada ~6 cm | _____ | _____ | _____ | ☐ |
| 49 | Cabeza adelantada ~9 cm | _____ | _____ | _____ | ☐ |
| 50 | Cabeza adelantada ~12 cm | _____ | _____ | _____ | ☐ |
| 50b | Encorvado (como al leer de cerca) | _____ | _____ | _____ | ☐ |

```bash
python src/tools/calibration_mode.py --duration 300
```

Mantén cada postura ~30 s y anota en qué segundo empieza cada una: el CSV
(`data/calibration_*.csv`) registra θc, sus componentes y `neck_ratio`, y
después se calcula la mediana de cada tramo.

**Qué decidir con estos datos:**

1. **El umbral de desviación.** Toma el Δθc de la fila 12 cm, que es la
   cabeza adelantada que el experto consideró de riesgo con 30° absolutos.
   Si queda **por encima de 15°**, el umbral sirve; si queda **por debajo**,
   hay que bajarlo (sin acercarse al ruido de la postura neutra).
2. **Qué indicador usar.** Si la relación cuello/hombros cambia de forma más
   clara y estable que Δθc entre neutra y 12 cm, es mejor candidato:
   **avísame con los números** y lo cambiamos.

> **Prueba rápida sin CSV:** con la app abierta, la fila del panel muestra
> **"Desviación cervical (Δθc)"** en vivo. Adelanta la cabeza a propósito y
> mira hasta cuánto sube.

### D.4 Calibrar el umbral de bostezo (MAR)

| # | Paso | ✓ |
|---|---|---|
| 51 | Graba 2 min **hablando** normalmente → anota el percentil alto de MAR | ☐ |
| 52 | Graba 2 min **bostezando** a propósito → anota el MAR típico | ☐ |
| 53 | Ajusta `mouth_opening_threshold` si el 0.45 actual no separa ambos casos | ☐ |

> El 0.45 es una estimación de ingeniería **sin fuente clínica** (declarado así
> en §7.2 del reporte). Estos dos CSV le dan por fin base empírica.

### D.5 Ajustes antes de sentar a nadie

| # | Paso | ✓ |
|---|---|---|
| 54 | Sube `ui.alert_cooldown_sec` de 30 a **300–600 s** | ☐ |
| 55 | Verifica que `fatigue.perclos_threshold` volvió a **0.15** si lo bajaste en A.4 | ☐ |
| 56 | Borra `data/history.db` para arrancar la sesión con una base limpia | ☐ |

> Con 30 s y riesgo sostenido, el participante recibe **dos avisos por minuto**.
> Eso arruina la sesión y contamina cualquier valoración subjetiva que le pidas.

### D.6 Sesión de validación con usuarios

| # | Paso | ✓ |
|---|---|---|
| 57 | N participantes, sesión por persona → llena la **matriz de confusión** (§4.2) | ☐ |
| 58 | Registra EAR por condición (§4.3) | ☐ |
| 59 | Prueba del filtro de parpadeo: cierres de 50 / 200 / 400 / 1000 / 3500 ms (§5.2) | ☐ |
| 60 | Exporta la bitácora de al menos una sesión completa (§6) | ☐ |

```bash
python src/tools/stats_report.py --session <id> --export
```

### D.7 Segunda ronda de entrevista

| # | Paso | ✓ |
|---|---|---|
| 61 | Aplica `docs/expert_interview_protocol_v2.md` (cierra 9 parámetros sin base clínica) | ☐ |
| 62 | Añade al Bloque E la pregunta de **régimen de alertas**: ¿toast o modal? Ahora son intercambiables por config | ☐ |

---

## Registro de incidencias

| # de prueba | Qué falló | Notas |
|---|---|---|
| | | |
| | | |
| | | |
