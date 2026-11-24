# Protocolo del estudio con participantes

> Cómo se recogen los datos para el Capítulo IV: quiénes participan, cuánto
> tiempo, qué hace el investigador, qué hace el participante y cómo salen los
> resultados. Versión 2 (2026-10-29): estudio de **campo de 15 días** con
> **validación al instalar**. Versión 3 (2026-11-19): diseño
> **experimental con preprueba, posprueba y grupo de control**.

---

## 0. Resumen en una tabla

| Parte | Cuándo | Dónde | Duración | Qué mide |
|---|---|---|---|---|
| **A. Validación** | Día 0, al instalar | Equipo del participante, con el investigador presente | ~25 min | ¿El sistema **acierta**? Sensibilidad, especificidad y precisión (matriz de confusión) |
| **B1. Línea base (preprueba)** | Días de uso 1 a 5, **los dos grupos** | En casa o en el trabajo, sin investigador | ≥ 2 h/día | Cómo se sienta la persona **sin** avisos (la app mide en silencio) |
| **B2. Posprueba, grupo de intervención** | Desde el día 6 hasta el 15 | Igual | ≥ 2 h/día | Cómo se sienta **con** avisos |
| **B2. Posprueba, grupo de control** | Desde el día 6 hasta el 15, sin avisos nunca | Igual | ≥ 2 h/día | Qué cambia solo con el tiempo y por saberse observado |
| **C. Cierre** | Al terminar | — | 10 min | Exportar los datos, cuestionarios de salida |

Las dos partes responden preguntas distintas. En casa nadie anota qué postura
tenía la persona en cada momento, así que **la precisión solo puede salir de la
parte A**. El **efecto del sistema** solo puede salir de la parte B.

### Diseño: experimental con preprueba, posprueba y grupo de control (2026-11-19)

Antes (2026-11-12) era una línea base múltiple con 2 controles. Se cambió
para comparar **quien usa el sistema con quien no lo usa**, como pidió el
ingeniero, sin perder la línea base (Hernández-Sampieri y Mendoza, 2018).
Como el grupo se asigna **por sorteo** (dentro de parejas parecidas), es el
diseño **experimental** con preprueba, posprueba y grupo de control. Si no se
pudiera sortear (p. ej. grupos ya formados), pasaría a ser
**cuasiexperimental** («con grupos intactos») y habría que decirlo así.

- **Todos** reciben el día 0 la misma hoja de recomendaciones ergonómicas
  (altura de la pantalla, distancia, pausas): es el «tratamiento
  tradicional».
- **Todos** miden primero **5 días de línea base** (preprueba): la app mide
  pero no avisa. Cuentan los días con al menos 2 h de uso, así que los
  avisos empiezan el día de uso 6 o algo después si hubo días cortos.
- **Grupo de intervención:** recomendaciones + software. Desde el día 6
  recibe avisos (posprueba con avisos).
- **Grupo de control:** recomendaciones + la app en silencio los 15 días
  (fase «control»): mide igual, nunca avisa y «Mi historial» queda
  bloqueado. **No usar la app no es posible**: sin ella no hay medición.
  Para el análisis, su «después» empieza con la misma regla que el otro
  grupo (tras 5 días de al menos 2 h).
- **Asignación: sorteo por parejas parecidas.** Al reclutar, formar parejas
  de la misma ocupación y horario (dos de oficina, dos estudiantes, dos en
  teletrabajo…) y, en cada pareja, sortear con una moneda quién va a cada
  grupo. Anotar el resultado antes de instalar. Así ningún grupo queda solo
  con un tipo de persona. Se configura en **⚙ Estudio → Grupo** y **Días de
  línea base: 5**.
- **Por qué:** las personas se sientan muy distinto entre sí (en la
  simulación, del 14 % al 35 % del tiempo con la cabeza adelantada), más que
  el efecto que se busca. Comparar solo el «después» de un grupo con el del
  otro no lo detectaría (potencia ~1 de cada 3). Con la línea base, cada
  persona se compara consigo misma y se compara **cuánto cambió** cada
  grupo: el paso del tiempo, la novedad y saberse observado afectan a los
  dos grupos y se descuentan.
- **Al terminar**, se ofrece al grupo de control activar los avisos (⚙
  Estudio → Grupo: Intervención, **después** del envío final): nadie se
  queda sin el beneficio y facilita el reclutamiento.

## 1. Muestra

**Objetivo: 16 participantes que terminen el estudio (8 por grupo).
Recluta 18–19**, porque en un seguimiento de 15 días de uso (~3 semanas)
siempre hay abandonos (equipo averiado, viaje, pérdida de interés). **Mínimo
aceptable: 7 por grupo (14).** Con menos de unas 12 personas en total
conviene volver al diseño anterior (cada persona contra sí misma), que rinde
más con muy pocas personas.

Potencia estimada por simulación (4000 estudios, variabilidad entre
personas y efecto de la simulación del 2026-11-19, α = 0.05 de una cola):

| Personas que terminan | Mann-Whitney del cambio entre grupos | Wilcoxon en el grupo con avisos |
|---|---|---|
| 6 + 6 | 71 % | 78 % |
| 7 + 7 | 79 % | 81 % |
| **8 + 8** | **82 %** | **89 %** |

Con dos colas, 8 + 8 da 74 % y 77 %. Los porcentajes dependen de supuestos;
el orden entre tamaños, no.

- **Inclusión:** adultos que trabajen o estudien frente a una computadora con
  Windows al menos 2 h diarias, con cámara web (integrada o externa).
- **Exclusión:** patologías que el alcance excluye (escoliosis, hipercifosis,
  collarín, lesión cervical reciente).
- **Exclusión (criterio del especialista, 2026-11-19):** personas con
  **10 años o más** de trabajo diario frente a una computadora. Con esa
  antigüedad la postura «natural» ya está adaptada (suele ser una cabeza
  adelantada estable), y el sistema la tomaría como postura neutra al
  calibrar: la desviación medida no reflejaría el riesgo real. Preguntarlo
  al reclutar: *«¿Cuántos años llevas trabajando o estudiando a diario
  frente a una computadora?»*
- **Equipo:** Windows 10/11. Comprobar en la instalación que la app alcanza al
  menos **10 FPS** (se ve en la ventana). Por debajo, la medición se degrada y
  ese equipo no sirve.

Para la **precisión** (parte A), 16 personas × 17 tramos = ~272 tramos, con un
margen de unos ±3–4 puntos en la precisión global y de ±7–8 puntos por condición
(con 25 personas: ±3 y ±6–7). Para el **efecto** (parte B), la prueba
principal es la U de Mann-Whitney sobre el cambio de cada persona (con
avisos frente a control), y la de Wilcoxon dentro de cada grupo. **No es
una muestra para generalizar a la población** y así debe redactarse.

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
| Día que cuenta para la línea base | ≥ 120 min de seguimiento (igual que un día válido) | `baseline_min_minutes` |
| Duración de la línea base | 5 días que cuenten, en los dos grupos | `baseline_days` (y 5 en ⚙ Estudio) |
| Participante válido para la comparación | ≥ 3 días válidos antes y ≥ 6 después (en el control, después del corte) | `analysis_min_valid_days_baseline`, `analysis_min_valid_days_intervention` |
| Días previstos | 15 días de uso (~3 semanas laborables) | `planned_days` |

**Por qué "días de uso" y no días de calendario.** La app pasa de línea base a
intervención cuando se completan sus días de línea base con ≥ 120 min de uso, no a esos días
de calendario: un fin de semana o un día con 10 minutos de uso dejarían la línea
base vacía. La fase se decide al iniciar cada sesión con los días **anteriores**,
así que un mismo día nunca mezcla fases.

**Tiempo de seguimiento** = tiempo real con la cámara midiendo. Las pausas
(cámara apagada) no cuentan. Que la persona se levante sí cuenta como
seguimiento: eso es justamente una pausa activa, y se mide.

---

## 3. Antes de empezar con los participantes

| # | Qué | Cómo |
|---|---|---|
| 1 | **Medir el umbral de cabeza adelantada (D.3).** Es lo más importante | Con 2–3 personas: Calibrar, Iniciar en modo laboratorio, y alternar `correcta` con `cabeza_adelantada` leve (≈ 5 cm) y marcada (≈ 10–12 cm). Mirar `delta_theta_media` en `segmentos.csv`. Si un adelantamiento claro da menos de 15°, el sistema no lo detecta: bajar `postural.cervical_deviation_max_deg` |
| 2 | **Confirmar la política de avisos con el especialista** | Hecho en el software (`alert_policy`, 2026-11-12): en campo solo se avisa una postura mantenida **30 s**, con **5 min** entre avisos del mismo tipo; ojos y somnolencia 2 min; bostezo con **3 en 15 min**. En modo laboratorio (validación) se usa la detección inmediata (ventanas de 3–8 s, 30 s de enfriamiento), para puntuar tramo a tramo. Confirmar los valores con el Lic. Collazos. El resultado principal del estudio es la **exposición** (% del tiempo desviado), no el número de alertas |
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
| 28–33 | Botón **⚙ Estudio** → modo **Campo**, **Grupo** según el sorteo por parejas y **Días de línea base: 5** → Guardar. Desde aquí la app está en línea base (o en control) |
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
avisa"*. En el grupo de intervención, los avisos aparecen tras 5 días de al
menos 2 h de uso (normalmente desde el día de uso 6). El grupo de control
nunca los recibe.

**Desde la intervención, además:**

- **Aviso de pausa activa** (OE 1.3.2.3): tras 30 min seguidos frente a la
  cámara sin levantarse, la app avisa y repite cada 10 min mientras siga
  sentado. Levantarse ≥ 60 s reinicia la cuenta. En la línea base se detecta
  igual pero en silencio. Su efecto se mide con la evolución de las
  **pausas por hora** y del **tramo más largo sentado sin pausa** (línea base
  frente a intervención). Valores en `config/thresholds.json → sedentary`
  (30 min: dentro del rango de 30–60 min de la bibliografía; el especialista
  aprobó la política de avisos).
- **Detecciones en enfriamiento:** si una condición se confirma mientras su
  tipo está en el tiempo de espera, no se avisa pero se guarda en `events`
  con `suppressed = 1` (una por ventana). El exportador las cuenta aparte
  (`detecciones_en_enfriamiento`) y no las mezcla con las alertas.
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
| `resumen_participantes.csv` | Una fila por participante: grupo, días válidos, adherencia, día de inicio del «después» y **media de cada indicador antes y después con su diferencia** | Parte B: la tabla de datos |
| `prueba_grupo.csv` | Por indicador, tres pruebas: Wilcoxon antes/después en cada grupo y **Mann-Whitney del cambio con avisos frente al control**, con p de una y dos colas y r | Parte B: **las pruebas de hipótesis** |
| `reportes/comparacion_grupos.html` | Antes y después de cada persona por grupo, el cambio de cada persona y la media diaria de cada grupo, con la tabla de pruebas | Parte B: **figura principal** del Capítulo IV |
| `reportes/evolucion_participantes.html` | Un panel por participante con sus días, la línea donde empieza su «después» y el NAP | Parte B: figura complementaria |
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
| Deterioro | Δθc, % desviado y PERCLOS en la primera frente a la última hora (días ≥ 2 h) |
| Alertas | detectadas, mostradas, por hora y por tipo |
| Autorreporte | dolor de cuello, cansancio visual, somnolencia (0–10) |
| Validez | minutos de seguimiento, bloque continuo más largo, día válido sí/no |

---

## 8. Análisis sugerido

1. **Precisión (parte A):** global y por condición con IC 95 %, latencia
   mediana, y el rango por participante.
2. **Efecto (parte B), resultado principal** (`prueba_grupo.csv`,
   `reportes/comparacion_grupos.html`), con `pct_desviado`:
   - **Prueba principal: U de Mann-Whitney** sobre el cambio (después −
     antes) de cada persona, grupo con avisos frente a control. Responde
     a si mejora más quien recibe avisos que quien no.
   - **Wilcoxon de rangos con signo** dentro de cada grupo: el grupo con
     avisos debería bajar; el control, cambiar poco.
   - **Una cola**, en la dirección de la hipótesis (que el indicador baje),
     declarada aquí, antes de ver los datos; se informa también la de dos
     colas. Nivel α = 0.05.
   - **Tamaño del efecto:** r de rangos biserial (|r| ≥ 0.1 pequeño, ≥ 0.3
     medio, ≥ 0.5 grande). Por participante, además, el **NAP**.
   - Pruebas exactas (muestras pequeñas, sin supuesto de normalidad).
3. **Secundarios:** episodio más largo, tramo sin pausa, pausas por hora,
   PERCLOS y autorreporte, con las mismas tres pruebas.
   Varias pruebas a la vez: corregir por comparaciones múltiples (Holm) o
   declararlas exploratorias.
4. **Antes / después con instrumentos externos:** cuestionario de molestias
   (Nórdico o NDI) y, si el especialista lo hace, el CVA con foto lateral.
5. **Usabilidad:** SUS al cierre, y la adherencia (`adherencia_pct`).

6. **Qué no se analiza:** la reacción inmediata a cada aviso (segundos hasta
   corregir, pausa en los minutos siguientes). La hipótesis es que la
   persona **mejora con el uso**, y eso lo miden los indicadores diarios
   comparados entre fases. La reacción a cada aviso depende de muchos
   factores del momento y, en la línea base, no hay aviso al que reaccionar.
   El dato sigue en `eventos.csv` (segundos hasta corregir de cada alerta)
   por si el jurado lo pide.

---

## 9. Ética y datos personales

- **Consentimiento informado** por escrito, que explique que hay una fase sin
  avisos, que se sortea quién recibe avisos después y quién no (y que al
  grupo sin avisos se le ofrece activarlos al terminar), que se puede pausar
  en cualquier momento y que se puede retirar.
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
- [x] **2c. Teams y Zoom de escritorio** (2026-11-12): igual que Meet. La
  cámara no se usa a la vez en la app y en la llamada, pero «Ceder cámara»
  funciona y la app vuelve a medir sola al colgar. Limitación en §12.

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
reducir la parte B (p. ej. 10 días de uso: 4 de base y 6 de posprueba,
ajustando `baseline_days` y los mínimos de §2). Decidirlo antes del piloto.

---

## 12. Limitaciones a declarar en la tesis

- Se excluyó a quienes llevan 10 años o más trabajando a diario frente a la
  computadora (criterio del especialista): su postura natural ya está
  adaptada y la calibración la tomaría como neutra. Los resultados no se
  extienden a esa población, que es precisamente la de mayor exposición
  acumulada; para ella haría falta una referencia externa (p. ej. el ángulo
  craneovertebral medido de perfil por un especialista) en lugar de la
  postura neutra de cada persona.
- Muestra pequeña (14–19 personas) y no probabilística (por conveniencia):
  no generalizable a la población. La asignación a los grupos sí es por
  sorteo, pero con grupos tan pequeños el sorteo no garantiza que sean
  equivalentes; por eso se compara el **cambio** de cada persona y no solo
  la posprueba.
- Precisión medida con condiciones **simuladas** (parte A); en casa no hay
  verdad de terreno.
- Δθc es una desviación **relativa** a la postura neutra de cada día, no el
  ángulo craneovertebral clínico.
- Sin cegamiento: cada participante sabe en qué grupo está. El grupo de
  control reduce, pero no elimina, la influencia de la novedad y de saberse
  observado (efecto Hawthorne), porque el grupo con avisos además recibe
  atención activa del sistema.
- En la línea base la ventana de la app muestra las métricas en vivo: quien la
  mira recibe algo de retroalimentación. Por eso se pide trabajar con la
  ventana cerrada.
- El número de alertas depende del enfriamiento configurado; por eso el
  resultado principal es la exposición.
- La distancia es una estimación (±8 % por el ancho de hombros).
- La cámara web no puede compartirse: Windows la entrega a una sola
  aplicación a la vez. Durante las videollamadas el sistema no mide (con
  «Ceder cámara» o «Pausar»), y ese tiempo queda como «sin datos», no como
  ausencia. Probado con Google Meet, Microsoft Teams y Zoom: «Ceder cámara»
  funciona y el sistema vuelve a medir solo al terminar la llamada.
- Umbrales provisionales (Δθc, ratio 0.65 del EAR, MAR 0.45) sin validación
  clínica; un solo experto (N = 1) para los criterios clínicos.
