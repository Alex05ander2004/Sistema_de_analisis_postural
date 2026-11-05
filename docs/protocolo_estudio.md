# Protocolo del estudio con participantes

> Cómo se recogen los datos para el Capítulo IV: quiénes participan, cuánto
> tiempo, qué hace el investigador, qué hace el participante y cómo salen los
> resultados. Versión 2 (2026-10-29): estudio de **campo de 15 días** con
> **validación al instalar**.

---

## 0. Resumen en una tabla

| Parte | Cuándo | Dónde | Duración | Qué mide |
|---|---|---|---|---|
| **A. Validación** | Día 0, al instalar | Equipo del participante, con el investigador presente | ~25 min | ¿El sistema **acierta**? Sensibilidad, especificidad y precisión (matriz de confusión) |
| **B1. Línea base** | Días de uso 1–5 | En casa o en el trabajo, sin investigador | ≥ 2 h/día | Cómo se sienta la persona **sin** avisos (la app mide en silencio) |
| **B2. Intervención** | Días de uso 6–15 | Igual | ≥ 2 h/día | Cómo se sienta **con** avisos |
| **C. Cierre** | Al terminar | — | 10 min | Exportar los datos, cuestionarios de salida |

Las dos partes responden preguntas distintas. En casa nadie anota qué postura
tenía la persona en cada momento, así que **la precisión solo puede salir de la
parte A**. El **efecto del sistema** solo puede salir de la parte B, comparando a
cada persona consigo misma (diseño A-B intra-sujeto).

---

## 1. Muestra

**25 participantes que terminen el estudio. Recluta 28–30**, porque en un
seguimiento de 15 días siempre hay abandonos (equipo averiado, viaje, pérdida
de interés).

- **Inclusión:** adultos que trabajen o estudien frente a una computadora con
  Windows al menos 2 h diarias, con cámara web (integrada o externa).
- **Exclusión:** patologías que el alcance excluye (escoliosis, hipercifosis,
  collarín, lesión cervical reciente).
- **Equipo:** Windows 10/11. Comprobar en la instalación que la app alcanza al
  menos **10 FPS** (se ve en la ventana). Por debajo, la medición se degrada y
  ese equipo no sirve.

Para la **precisión** (parte A), 25 personas × 17 tramos = ~425 tramos, con un
margen de ±3 puntos en la precisión global y de ±6–7 puntos por condición. Para
el **efecto** (parte B), 25 pares base/intervención bastan para una prueba
pareada (Wilcoxon) con efectos medianos. **No es una muestra para generalizar a
la población** y así debe redactarse.

---

## 2. Cuánto tiempo deben usar la app

**Instrucción al participante:** *"Ábrela al empezar a trabajar en la
computadora y déjala en segundo plano hasta que termines. Si te levantas, no
hace falta pausarla. Úsala al menos 2 horas al día."*

Lo que importa es el **bloque continuo**: la fatiga y el deterioro postural
aparecen con el tiempo sostenido frente a la pantalla. Dos horas sueltas en
ratos de 20 minutos solo registran a la persona "fresca". La meta es la
jornada completa; el mínimo, 2 h con un bloque de al menos 1 h.

**Criterios de validez** (fijados ANTES de empezar; están en
`config/thresholds.json → study.field` y el exportador los aplica):

| Criterio | Valor | Clave en la configuración |
|---|---|---|
| Día válido | ≥ 120 min de seguimiento **y** un bloque continuo ≥ 60 min | `valid_day_min_minutes`, `valid_day_min_block_minutes` |
| Día que cuenta para la línea base | ≥ 60 min de seguimiento | `baseline_min_minutes` |
| Duración de la línea base | 5 días que cuenten | `baseline_days` |
| Participante válido para la comparación | ≥ 3 días válidos de base y ≥ 6 de intervención | `analysis_min_valid_days_baseline`, `analysis_min_valid_days_intervention` |
| Días previstos | 15 días de uso (~3 semanas laborables) | `planned_days` |

**Por qué "días de uso" y no días de calendario.** La app pasa de línea base a
intervención cuando se completan 5 días con ≥ 60 min de uso, no a los 5 días
de calendario: un fin de semana o un día con 10 minutos de uso dejarían la línea
base vacía. La fase se decide al iniciar cada sesión con los días **anteriores**,
así que un mismo día nunca mezcla fases.

**Tiempo de seguimiento** = tiempo real con la cámara midiendo. Las pausas
(cámara apagada) no cuentan. Que la persona se levante sí cuenta como
seguimiento: eso es justamente una pausa activa, y se mide.

---

## 3. Antes de empezar con los 25

| # | Qué | Cómo |
|---|---|---|
| 1 | **Medir el umbral de cabeza adelantada (D.3).** Es lo más importante | Con 2–3 personas: Calibrar, Iniciar en modo laboratorio, y alternar `correcta` con `cabeza_adelantada` leve (≈ 5 cm) y marcada (≈ 10–12 cm). Mirar `delta_theta_media` en `segmentos.csv`. Si un adelantamiento claro da menos de 15°, el sistema no lo detecta: bajar `postural.cervical_deviation_max_deg` |
| 2 | **Decidir el enfriamiento entre alertas** | Hoy son 30 s: con una postura mala sostenida, 2 avisos por minuto durante 10 días es la receta del abandono. Recomendado para campo: **120–300 s**. Afecta al número de alertas, por eso el resultado principal del estudio es la **exposición** (% del tiempo desviado), no el número de alertas |
| 3 | **Piloto de campo: 3 personas, 3–5 días** (no cuentan en el estudio) | Pedir el `.zip` **al tercer día**. Detecta los problemas reales (cámara ocupada por Teams o Zoom, días sin datos, avisos molestos) cuando aún hay tiempo de corregirlos |
| 4 | **Congelar la versión** | Subir `APP_VERSION` en `src/version.py`, commit y `git tag estudio-v1`. A partir de aquí **no se cambia nada**. Cada sesión guarda la versión y la huella de `thresholds.json` (`config_hash`): el exportador avisa si algún equipo usó otra |
| 5 | **Instalador** | Los participantes no tienen Python. Hay que empaquetar la app (`.exe`) y probarla en una PC limpia (ver §10) |
| 6 | Instrumentos | Cuestionario Nórdico (versión española de Mateos-González et al., 2024) al inicio y al final, SUS en español (Sevilla-Gonzalez et al., 2020) al final, y el consentimiento |

---

## 4. Día 0: instalación y validación (parte A, ~40 min)

| Min | Qué se hace |
|---|---|
| 0–5 | Consentimiento, asignación del **código** (P01…), cuestionario de entrada (molestias). Si hay especialista: **foto lateral para el ángulo craneovertebral (CVA)** |
| 5–10 | Instalar la app. Botón **⚙ Estudio**: código, modo **Laboratorio** y **FOV** (sentar a la persona a una distancia medida con cinta, p. ej. 0.60 m, escribirla y pulsar *Calcular*; luego *Guardar*) |
| 10–13 | Colocación (cámara a la altura de los ojos, 0.50–0.70 m) y **Calibrar** → **Iniciar** |
| 13–28 | **Bloque guiado**: 17 tramos etiquetados con el selector de condición (ver §5) |
| 28–33 | Botón **⚙ Estudio** → modo **Campo** → Guardar. Desde aquí la app está en línea base |
| 33–40 | Explicar al participante qué hacer (§6) y entregarle la hoja de instrucciones |

---

## 5. Guion del bloque guiado

Cada tramo dura **~40 s**. El investigador cambia el selector «Condición del
protocolo» **al empezar el tramo**.

| Condición | Qué hace el participante | Tramos |
|---|---|---|
| `correcta` | Sentado recto y relajado, trabajando con normalidad | 4 |
| `cabeza_adelantada` | Adelanta claramente la cabeza (≈ 10–12 cm) y la mantiene | 4 |
| `hombros_desnivelados` | Inclina la línea de hombros (≥ 10°) y la mantiene | 3 |
| `ojos_cerrados` | Cierra los ojos 4–5 s, abre 2 s, y repite | 3 |
| `bostezo` | Abre la boca como en un bostezo ~4 s y repite | 3 |

**Reglas del orden** (por el enfriamiento entre alertas del mismo tipo):
nunca dos tramos del mismo tipo seguidos, alternar `correcta` entre los tramos
con alerta y rotar el orden entre participantes. Ejemplo:
`correcta, adelantada, ojos, correcta, hombros, bostezo, adelantada, correcta,
ojos, bostezo, hombros, adelantada, correcta, ojos, bostezo, hombros,
adelantada`.

> Si en el paso 2 de §3 se sube el enfriamiento a 120–300 s, **en la
> validación** deja separados por al menos ese tiempo los tramos con la misma
> alerta, o haz la validación con el valor de 30 s y decláralo.

Bostezos y cierres de ojos son **simulados**: decláralo como limitación.

---

## 6. Qué hace el participante (días 1–15)

**Cada día:**

1. Abrir la app al empezar a trabajar.
2. **Calibrar** (sentado recto, mirando la pantalla, ~20 s) e **Iniciar
   seguimiento**. Se calibra cada día porque la silla, la luz y la posición de la
   cámara cambian.
3. Cerrar la ventana: la app sigue en segundo plano (icono junto al reloj).
   **Pausar** si no quiere ser medido en ese momento.
   **Videollamadas:** si Meet, Teams o Zoom dicen *"la cámara está en uso"*,
   pulsar **Ceder cámara** (en la ventana, o clic derecho en el icono junto al
   reloj) y, en los 20 s siguientes, volver a encender la cámara en la
   llamada. La app no mide durante la llamada y **vuelve a medir sola** al
   terminarla. No hace falta hacer nada más.
4. Al terminar la jornada: **Salir** desde el icono de la bandeja. La app
   pregunta **tres cosas en 10 segundos**: dolor de cuello u hombros, cansancio
   visual y somnolencia (0–10). Si se apagó el equipo sin salir, la pregunta
   aparece al abrir la app al día siguiente, marcada como retrospectiva.

**Durante los primeros días (línea base) la app no avisa.** Es intencionado y
el participante lo sabe: la barra muestra *"Línea base: la app mide pero no
avisa"*. A partir del día 6 de uso aparecen los avisos.

**Desde la intervención, además:**

- **Aviso de pausa activa** (OE 1.3.2.3): tras 45 min seguidos frente a la
  cámara sin levantarse, la app avisa y repite cada 10 min mientras siga
  sentado. Levantarse ≥ 60 s reinicia la cuenta. En la línea base se detecta
  igual pero en silencio, así que el exportador compara la respuesta:
  `pct_pausa_tras_aviso_inactividad` = % de avisos seguidos de una pausa en
  los 10 min siguientes. Valores en `config/thresholds.json → sedentary`
  (45 min es provisional: **confirmar con el especialista**).
- **Mi historial** (OE 1.3.2.5): botón en la cabecera con los últimos 14 días
  (% del tiempo en buena postura, horas medidas, pausas por hora, tramo más
  largo sin levantarse y avisos). **En la línea base está bloqueado**: ver su
  propia postura ya sería retroalimentación.

**Envío de datos:** botón **Exportar mis datos**. Guarda en el Escritorio un
`Estudio_P07_<fecha>.zip` y lo selecciona en el Explorador. Se envía:

- **el día 7** (envío intermedio, para detectar problemas a tiempo), y
- **al terminar** (envío final).

Por correo, si pesa menos de 25 MB, o a una carpeta compartida de Drive por
participante. El `.zip` no contiene imágenes ni el nombre.

---

## 7. Obtener los resultados

Guarda todos los `.zip` recibidos en una carpeta (por ejemplo `recibidos/`) y
ejecuta:

```bash
python src/tools/export_study.py --db recibidos/ --output-dir resultados/campo
```

Lee todos los `.zip` y `.db` de la carpeta. Si un participante envió el
intermedio y el final, **las sesiones repetidas se cuentan una sola vez**: cada
sesión tiene un identificador único.

| Archivo | Contenido | Para qué |
|---|---|---|
| `matriz_confusion.csv` | VP/FN/FP/VN, sensibilidad, especificidad y precisión con IC 95 % | Parte A: **precisión ≥ 90 %** del Capítulo I |
| `segmentos.csv` | Cada tramo del bloque guiado con su resultado y latencia | Parte A: tabla de resultados |
| `resumen_participantes.csv` | Una fila por participante: días válidos, adherencia, y **media de cada indicador en línea base e intervención con su diferencia** | Parte B: **la tabla para la prueba pareada** |
| `resumen_diario.csv` | Una fila por participante y día, con todos los indicadores (ver abajo) | Evolución, gráficos, análisis de días |
| `perfil_horario.csv` | Indicadores por hora del día y fase | A qué hora empieza el deterioro |
| `reportes/reporte_P07.html` | **Reporte por participante para el especialista**: comparación, gráficas por día, exposición por bandas, perfil horario, tabla diaria y cómo leerlo. Se abre en el navegador y se imprime a PDF | Revisión clínica |
| `eventos.csv` | Cada alerta: fase, si se **mostró** o se registró en silencio, y segundos hasta corregir | Detalle |
| `detalle/metricas_P07.csv` | Todas las mediciones de campo, una por segundo, por participante | Análisis propio en R, Python o SPSS |
| `resumen_sesiones.csv`, `metricas_detalle.csv` | Por sesión, y el detalle de las sesiones de laboratorio | Parte A |

**Indicadores por día** (`resumen_diario.csv`):

| Grupo | Indicadores |
|---|---|
| Exposición cervical | minutos y % del tiempo en cada banda de Δθc (< 10°, 10–20°, 20–30°, ≥ 30°), % del tiempo por encima del umbral, Δθc medio y p95, episodio más largo, episodios sostenidos por hora |
| Hombros y cabeza | % del tiempo con asimetría > 10°, hombro habitualmente más bajo, inclinación lateral mediana |
| Pantalla | distancia mediana, % del tiempo demasiado cerca |
| Fatiga | PERCLOS medio y p95, minutos con PERCLOS alto, bostezos |
| Pausas activas | número de pausas (≥ 60 s fuera de cámara), minutos en pausa, **tramo más largo sentado sin pausa** |
| Respuesta | segundos hasta corregir tras un episodio cervical (con aviso en la intervención, espontáneo en la base), % corregidos en 30 s |
| Deterioro | Δθc, % desviado y PERCLOS en la primera frente a la última hora (días ≥ 2 h) |
| Alertas | detectadas, mostradas, por hora y por tipo |
| Autorreporte | dolor de cuello, cansancio visual, somnolencia (0–10) |
| Validez | minutos de seguimiento, bloque continuo más largo, día válido sí/no |

---

## 8. Análisis sugerido

1. **Precisión (parte A):** global y por condición con IC 95 %, latencia
   mediana, y el rango por participante.
2. **Efecto (parte B), resultado principal:** `pct_desviado` en línea base
   frente a intervención, con **Wilcoxon de rangos con signo** sobre los
   participantes válidos (`resumen_participantes.csv`, columnas `_base` e
   `_interv`). Reportar la mediana del cambio y el tamaño del efecto.
3. **Secundarios:** episodio más largo, tramo sin pausa, pausas por hora,
   PERCLOS, segundos hasta corregir y autorreporte, con la misma prueba.
   Varias pruebas a la vez: corregir por comparaciones múltiples (Holm) o
   declararlas exploratorias.
4. **Antes / después con instrumentos externos:** cuestionario de molestias
   (Nórdico o NDI) y, si el especialista lo hace, el CVA con foto lateral.
5. **Usabilidad:** SUS al cierre, y la adherencia (`adherencia_pct`).

---

## 9. Ética y datos personales

- **Consentimiento informado** por escrito, que explique que hay una fase sin
  avisos, que se puede pausar en cualquier momento y que se puede retirar.
- **Códigos seudónimos** (P01…). La tabla código → persona va en un archivo
  aparte, fuera del repositorio y fuera de los equipos de los participantes.
- **No se guarda ninguna imagen ni video.** El `.zip` lo explica en `LEEME.txt`.
- Los datos recibidos no se suben a GitHub (`data/estudio_*/`, `resultados/` y
  `data/*.db` están en `.gitignore`).
- El sistema **no diagnostica**: aporta datos de exposición y conducta que
  complementan la evaluación del especialista. Así debe constar en el
  consentimiento y en la tesis.

---

## 10. Qué falta probar antes de entregar la app

### Ya resuelto en el código (2026-10-29)

| Problema | Qué se hizo | Cómo se comprobó |
|---|---|---|
| La cámara no se recuperaba nunca tras una videollamada o una suspensión (y el log crecía ~100 líneas/s) | Reconexión con espera creciente, aviso en la ventana y en la bandeja tras 5 min | 9 pruebas con cámara simulada |
| Si la app arrancaba con la cámara real ocupada, se quedaba para siempre en la cámara IR (negra) | Al perder la cámara se vuelve a autodetectar | Reproducido con el `.exe` real y corregido |
| **La app deja la cámara "en uso" para Meet/Teams/Zoom** | Detectarlo solo no es posible (Windows únicamente registra a la app que LOGRA abrir la cámara) y compartirla tampoco funcionó con Meet real. Solución: botón **Ceder cámara** (ventana y bandeja) | Probado con Google Meet el 2026-10-29: Meet toma la cámara tras ceder |
| Con Meet usando la cámara, la app recibía ~1 frame/s sin imagen útil y **lo registraba como persona ausente** (pausas activas falsas) | La app lo detecta (< 3 FPS), no registra nada durante la llamada y reintenta cada 15 s; el exportador también descarta filas < 3 FPS | Visto en la base real; 2 pruebas automáticas |
| No había ejecutable | `packaging/` (PyInstaller), datos en `%LOCALAPPDATA%\MonitorPostural`, log a archivo incluido en el `.zip` | El `.exe` arranca, abre la cámara, calibra y registra |
| ~1.6 núcleos de CPU durante toda la jornada | Tope de 12 FPS de inferencia (`mediapipe.max_inference_fps`) | Medido con el `.exe`: ~1.1 núcleos (−33 %) |

### Lo que tienes que probar tú (en este orden)

- [ ] **1. Umbral de cabeza adelantada (D.3)**, §3 paso 1. Sigue siendo lo más
  importante.

- [x] ~~2a. Meet obtiene la cámara tras **Ceder cámara**~~ (2026-10-29: sí).
- [x] **2b. La app vuelve a medir sola al terminar la llamada** (2026-10-29,
  Meet, llamada de 2.5 min: detectó la cámara compartida en 4 s, reintentó cada
  ~18 s sin cortar la llamada, no registró filas durante la llamada y volvió a
  medir 2 s después de salir de Meet). Procedimiento usado: con la app
  monitoreando: Ceder cámara → encenderla en Meet → durante la llamada la app
  debe decir *"Otra aplicación está usando la cámara…"* → salir de la
  llamada → en ≤ 20 s la app debe volver a mostrar imagen y medir (en el log:
  `Cámara: shared → ok`).
- [ ] **2c. Teams y Zoom de escritorio:** repetir 2a/2b (pueden comportarse
  distinto que Meet en el navegador).

- [ ] **3. Suspender y volver.** Con la app monitoreando, cierra la tapa del
  portátil 2 minutos y ábrela. La app debe volver a medir sola (en el log:
  `Cámara: reconnecting → ok`).

- [ ] **4. Jornada de 8 h.** Abre la app (mejor el `.exe`), calibra, inicia, y
  en otra terminal:
  ```bash
  python src/tools/soak_monitor.py --horas 8
  ```
  Trabaja normal. Al final imprime el veredicto: memoria estable (sin fuga),
  CPU media, crecimiento de la base y si la app se cerró sola.

- [ ] **5. El `.exe` en una PC sin Python** (la de un amigo o familiar):
  descomprimir, abrir, calibrar, 10 min de seguimiento, Exportar mis datos, y
  leer ese `.zip` con `export_study.py`. Ver `packaging/LEEME.md`.

- [ ] **6. Línea base silenciosa y paso a intervención**: en modo Campo con un
  código de prueba (`baseline_days: 1` para no esperar 5 días). Primer día sin
  avisos (`mostrada = no` en `eventos.csv`), al día siguiente con avisos.

- [ ] **7. Encuesta diaria**: sale al pulsar Salir tras ≥ 30 min de uso, no se
  repite el mismo día, y si se apaga el equipo sin salir aparece al abrir al
  día siguiente.

- [ ] **8. Luz baja y lentes**: 10 min de noche y con lentes; el PERCLOS no
  debe dispararse con los ojos abiertos.

- [ ] **9. Notificaciones en otro equipo**: con "No molestar" no se ven; pedir
  al participante que permita las de la app.

### Decisiones pendientes

- **Enfriamiento entre alertas** (30 s ahora; recomendado 120–300 s en campo).
- **Más ahorro de CPU** si un equipo de participante va lento:
  `pose_every_n_frames: 2` o `pose_model_complexity: 0`. Medir antes con
  `benchmark_mediapipe.py` y declararlo.
- **Calendario** (§11).

## 11. Calendario: aviso importante

La Fase 8 del cronograma termina el **15 de noviembre de 2026**. Con este
diseño: piloto (1 semana) + instalaciones escalonadas (1–2 semanas) + 15 días de
uso (3 semanas laborables) + análisis (1 semana) ≈ **6–7 semanas**. **No cabe
antes del 15 de noviembre**: hay que mover la Fase 8 en el cronograma o
reducir la parte B (p. ej. 10 días de uso: 3 de base y 7 de intervención,
ajustando `baseline_days` y los mínimos de §2). Decidirlo antes del piloto.

---

## 12. Limitaciones a declarar en la tesis

- Muestra de 25 personas: no generalizable a la población.
- Precisión medida con condiciones **simuladas** (parte A); en casa no hay
  verdad de terreno.
- Δθc es una desviación **relativa** a la postura neutra de cada día, no el
  ángulo craneovertebral clínico.
- Diseño A-B sin grupo control: un cambio entre fases también puede deberse a
  la novedad o a saberse observado (efecto Hawthorne). La línea base lo
  atenúa, no lo elimina.
- En la línea base la ventana de la app muestra las métricas en vivo: quien la
  mira recibe algo de retroalimentación. Por eso se pide trabajar con la
  ventana cerrada.
- El número de alertas depende del enfriamiento configurado; por eso el
  resultado principal es la exposición.
- La distancia es una estimación (±8 % por el ancho de hombros).
- Umbrales provisionales (Δθc, ratio 0.65 del EAR, MAR 0.45) sin validación
  clínica; un solo experto (N = 1) para los criterios clínicos.
