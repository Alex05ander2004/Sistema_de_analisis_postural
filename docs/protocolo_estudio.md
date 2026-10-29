# Protocolo del estudio con participantes

> Cómo se recogen los datos para el Capítulo IV: cuántas personas, cuánto dura
> cada sesión, qué hace el evaluador y cómo salen los resultados. Pensado para
> el calendario de la **Fase 8** (16 oct – 15 nov de 2026).

---

## 1. Tamaño de muestra recomendado

**20 participantes como mínimo; 25 si el tiempo lo permite.** Con 30 no ganas
casi nada en precisión estadística y sí pierdes días de calendario.

La razón no es "cuántas personas" sino **cuántos tramos puntuables** salen. La
precisión se mide por tramo (una condición mantenida ~40 s que el sistema
acierta o falla), y la incertidumbre depende sobre todo de ese número:

| Tramos puntuados | Margen del IC 95 % si la precisión real es 90 % |
|---|---|
| 100 | ± 5.9 puntos |
| 140 | ± 5.0 |
| 200 | ± 4.2 |
| **340** (20 personas × 17 tramos) | **± 3.2** |

(Aproximación normal de una proporción, `1.96·√(p(1−p)/n)`; el exportador usa
el intervalo de Wilson, algo más prudente.)

Con 20 personas y el guion de abajo salen ~340 tramos: suficientes para
afirmar con razonable seguridad si se alcanza el ≥ 90 % del Capítulo I, y unos
**60–80 tramos por condición**, que da un margen de ± 6.6–7.6 puntos por condición.

**Una advertencia que debes declarar:** los tramos de una misma persona no son
independientes (cada persona tiene su postura, su cara, su luz). El intervalo
real es algo más ancho que el calculado. Por eso el exportador también da el
resultado **por participante**: es la forma honesta de mostrar la dispersión
entre personas. Con 20 personas el estudio es sólido como validación técnica,
pero **no es una muestra para generalizar a la población** y así debe
redactarse.

**Inclusión y exclusión:** adultos sin las patologías que tu alcance excluye
(escoliosis, hipercifosis, collarines, lesiones cervicales temporales).

---

## 2. Duración: una sesión de 45 minutos por persona

Tu calendario deja unos 17 días para toda la Fase 8, que además incluye al
especialista. Un estudio longitudinal de varias semanas **no cabe**. Lo viable,
y lo que recomiendo, es **una sola sesión de unos 45 minutos** por participante:

| Min | Qué se hace |
|---|---|
| 0–5 | Explicación, consentimiento y asignación del **código** (P01…) |
| 5–8 | Colocación (cámara a la altura de los ojos, 0.50–0.70 m) y **calibración guiada** |
| 8–23 | **Bloque guiado**: 17 tramos etiquetados (ver §4). Es lo que da la precisión |
| 23–38 | **Uso libre**: trabajo propio (leer, escribir) con el sistema vigilando, sin guion |
| 38–45 | Cierre: valoración subjetiva de los avisos (1–5) y exportar |

El **bloque guiado** responde a "¿detecta bien?" (precisión, latencia). El **uso
libre** responde a "¿cómo se comporta en trabajo real?": alertas por hora,
porcentaje del tiempo con la cabeza desviada, tiempo que tarda la persona en
corregir tras un aviso, y la evolución entre la primera y la segunda mitad.

15 minutos de uso libre no son una jornada laboral, y no sirven para hablar de
adherencia a largo plazo. Si en tu tesis quieres medir "progreso", declara que
es **progreso dentro de la sesión** y deja el seguimiento de varios días como
trabajo futuro.

**Opcional, solo si hay tiempo:** pedir a 5–8 participantes que usen la app dos
o tres días en su propio equipo (1–2 h al día, con la condición "libre").
Aporta datos ecológicos, pero no pongas en riesgo la fecha del 15 de noviembre
por ello. Las bases de cada equipo se unen con `export_study.py --db a.db b.db`.

**Cálculo del calendario:** 20 personas × 45 min = 15 horas. Con 2 sesiones al
día son 10 días hábiles; con 3, siete. Deja 3 días de colchón para repetir
sesiones fallidas.

---

## 3. Antes de la primera sesión

| # | Qué | Cómo |
|---|---|---|
| 1 | Calibrar el FOV de la cámara (una vez por cámara) | `python src/tools/calibrar_fov.py --estimada <lectura de la app> --real <cinta>` y copiar el valor a `camera.horizontal_fov_deg` |
| 2 | **Piloto con 3 personas** (no cuentan en el estudio) | Sirve para comprobar el guion y fijar el umbral de Δθc (paso 3) |
| 3 | Fijar el umbral de desviación cervical | Con el piloto, mirar `delta_theta_media` de `cabeza_adelantada` en `segmentos.csv`. Si queda **por debajo de 15°**, el sistema no la detectaría: bajar `postural.cervical_deviation_max_deg` |
| 4 | **Congelar la versión** | Commit y etiqueta de git antes de empezar (`git tag estudio-v1`). No cambies umbrales a mitad: invalida la comparación entre participantes |
| 5 | Base limpia | Archivar `data/history.db` del piloto y empezar con una nueva |
| 6 | Revisar la configuración | `study.enabled: true`. Mantén `alert_cooldown_sec` en su valor y respeta el orden del guion (§4) |

El paso 3 es el más importante: el umbral de 15° es **provisional** y nunca se
ha validado con adelantamientos reales. Si el piloto muestra que se queda
corto, el estudio entero mediría un umbral equivocado.

---

## 4. Guion del bloque guiado (15 min)

Cada tramo dura **~40 s**. El evaluador cambia el selector «Condición del
protocolo» de la app **al empezar el tramo** (con un ratón externo, para que el
participante no tenga que moverse). Los primeros ~3 s de cada tramo son de
transición y no cuentan de forma especial: por eso los tramos son largos.

| Condición | Qué hace el participante durante el tramo | Tramos |
|---|---|---|
| `correcta` | Sentado recto y relajado, trabajando con normalidad | 4 |
| `cabeza_adelantada` | Adelanta claramente la cabeza (≈ 10–12 cm) y la mantiene | 4 |
| `hombros_desnivelados` | Inclina la línea de hombros (≥ 10°) y la mantiene | 3 |
| `ojos_cerrados` | Cierra los ojos 4–5 s, abre 2 s, y repite | 3 |
| `bostezo` | Abre la boca como en un bostezo y la mantiene ~4 s; repite | 3 |

**Reglas del orden** (para que el enfriamiento de 30 s entre alertas del mismo
tipo no falsee el resultado):

1. Nunca dos tramos **del mismo tipo seguidos**.
2. Alterna `correcta` entre los tramos con alerta esperada.
3. Aleatoriza el orden entre participantes (p. ej. con una lista preparada),
   para que el cansancio o el aprendizaje no se confundan con una condición.

Orden de ejemplo (rota la lista entre participantes):
`correcta, adelantada, ojos, correcta, hombros, bostezo, adelantada, correcta,
ojos, bostezo, hombros, adelantada, correcta, ojos, bostezo, hombros,
adelantada`.

Un bostezo simulado **no es un bostezo real**. Decláralo como limitación: el
umbral de apertura bucal (0.45) sigue sin base clínica y esta prueba solo
mide si el sistema detecta una boca abierta sostenida.

Tras el bloque guiado, el evaluador cambia el selector a **`libre`** y el
participante trabaja 15 minutos sin guion.

---

## 5. Qué hace el evaluador en la app

1. Abrir la app. Aparece el campo **Código de participante**.
2. Escribir el código (`P01`). Nunca el nombre.
3. **Calibrar** (la persona recta, quieta, mirando la pantalla).
4. **Iniciar seguimiento.** Sin código, el botón no se habilita.
5. Durante el seguimiento, el selector de **Condición** estampa cada fila y
   cada alerta con lo que la persona está haciendo.
6. Al terminar: **Recalibrar** (cierra la sesión) y pasar al siguiente
   participante, o cerrar la app con **Salir** en la bandeja.

**Una sesión = un participante.** No reutilices una sesión para dos personas.

---

## 6. Obtener los resultados

```bash
python src/tools/export_study.py
```

Sin argumentos lee `data/history.db` y crea `data/estudio_<fecha>/` con:

| Archivo | Contenido | Para qué |
|---|---|---|
| `resumen_sesiones.csv` | Una fila por participante | Estadística descriptiva y comparación entre personas |
| `segmentos.csv` | Una fila por tramo: condición, alerta esperada y obtenida, latencia | Matriz de confusión; es la «tabla de resultados» del Capítulo IV |
| `matriz_confusion.csv` | VP/FN/FP/VN y precisión por condición y global, con IC 95 % | §4.2 del reporte |
| `eventos.csv` | Cada alerta, su condición y los segundos hasta corregir | Indicadores de prevención del Capítulo I |
| `metricas_detalle.csv` | Todas las métricas fila a fila | Gráficos de evolución |

Los archivos usan UTF-8 con BOM: se abren en Excel sin romper tildes ni la θ, y
se leen en SPSS, R o Python sin más.

Si los participantes usaron equipos distintos, une las bases:

```bash
python src/tools/export_study.py --db equipo1/history.db equipo2/history.db
```

**Cómo se puntúa un tramo.** Con alerta esperada (p. ej. `cabeza_adelantada` →
`cervical_angle`): **VP** si el sistema la emitió dentro del tramo, **FN** si no.
Sin alerta esperada (`correcta`): **VN** si no hubo ninguna, **FP** si hubo
alguna. Una alerta cuenta hasta 5 s después del fin del tramo (por el filtro y
el temporizador). Los tramos de menos de 15 s no se puntúan.

**Indicadores que salen del estudio y se corresponden con el Capítulo I:**

| Indicador de la tesis | Dónde está |
|---|---|
| Precisión de clasificación (≥ 90 %) | `matriz_confusion.csv`, fila `GLOBAL` |
| Tiempo de exposición tras la notificación | `segundos_hasta_corregir` (`eventos.csv`) y `correccion_mediana_s` |
| Restablecimiento de la postura tras la alerta | `alertas_cervicales_corregidas / alertas_cervicales_total` |
| FPS, latencia, consumo | `benchmark_mediapipe.py` (no sale de este exportador) |
| **Adherencia a las pausas** | **No se puede medir todavía**: el sistema registra alertas emitidas, no si el usuario las atendió. El tiempo hasta corregir es el mejor sustituto disponible |

---

## 7. Análisis sugerido

1. **Principal:** precisión global y por condición con su IC 95 %, más la
   latencia mediana. Reporta también el rango de la precisión **por
   participante**.
2. **Uso libre:** `alertas_por_hora`, `pct_desviado_uso_libre` y
   `correccion_mediana_s`. Compara `alertas_1a_mitad` con `alertas_2a_mitad`
   con una prueba pareada (Wilcoxon) para ver si hay evolución dentro de la
   sesión.
3. **Percepción:** la valoración 1–5 de los avisos (¿molestos?, ¿oportunos?).
   Es lo que justifica el régimen de notificaciones frente al modal del
   Capítulo III.
4. **Opcional:** el Índice de Discapacidad Cervical (NDI) que recomendó el
   experto solo tiene sentido en un seguimiento de semanas. Con una sola
   sesión no lo apliques.

---

## 8. Ética y datos personales

- **Consentimiento informado** por escrito, con derecho a retirarse.
- **Códigos seudónimos** (P01…). La tabla código→persona, si existe, va en un
  archivo **aparte** y fuera del repositorio.
- **No se guarda imagen ni video**: la bitácora solo contiene números.
- Los datos del estudio (`data/estudio_*/`, `data/*.db`) están excluidos de git.
  No los subas a GitHub: son datos de personas.
- Informa a cada participante de que el sistema no es un dispositivo de
  diagnóstico y de que los avisos son orientativos.

---

## 9. Limitaciones a declarar en la tesis

- Muestra de 20–25 personas: validación técnica, no generalizable.
- Los tramos de una persona no son independientes; el IC es optimista.
- Bostezos y cierres de ojos son **simulados** por el participante.
- La distancia es una estimación (±8 % por el ancho de hombros) y el FOV se
  calibró con una persona.
- Umbrales provisionales (15° de desviación, ratio 0.65 del EAR, MAR 0.45) sin
  validación clínica; el umbral de desviación se ajustó con un piloto de 3
  personas.
- Un solo experto (N = 1) para los criterios clínicos.
- La adherencia a las pausas no se mide: solo el tiempo hasta corregir.
