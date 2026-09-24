# Datos archivados — NO usar para el Capítulo IV

## `history_pre-correccion_2026-07.db`

Bitácora de las tres sesiones exploratorias de julio de 2026, capturadas
**antes** de la auditoría geométrica y de pipeline del 2026-09-07
(`docs/validation_report.md` §3.3).

**Estos datos no son comparables con los umbrales actuales y no deben
aparecer en el Capítulo IV.** Se conservan únicamente como evidencia del
problema que motivó la corrección.

### Por qué es inservible como medición

| Problema | Efecto |
|---|---|
| θc calculado desde una sola oreja (P7) | Sesgo constante de ~13°. Por eso la postura "correcta" marca 38–41° |
| Métricas sobre coordenadas normalizadas (espacio anisótropo) | ΔE, EAR y MAR inflados por W/H = 1.333 |
| `metrics_log.timestamp` guardaba `perf_counter()` | Son segundos desde el arranque del equipo (p. ej. 248082), no hora de pared: imposible correlacionar con `events` |
| `HistoryLogger.log_event()` nunca se llamaba | La tabla `events` quedó **vacía** en las tres sesiones |
| Las sesiones nunca se cerraban | `sessions.ended_at` = NULL: el resumen no puede calcular la duración |
| Columnas añadidas después (sagital, lateral, distancia, PERCLOS, parpadeo, `pose_valid`) | **Todas NULL**: no existían cuando se capturaron estas filas |

### Contenido

- 3 sesiones (todas con `ended_at` = NULL)
- 107 filas en `metrics_log`
- **0 filas** en `events`

### Qué hacer en su lugar

La sesión de validación real genera una base nueva y limpia en
`data/history.db`. Los reportes del Capítulo IV se producen con:

```bash
python src/tools/stats_report.py --list-sessions
python src/tools/stats_report.py --session <id>
```

Si alguna vez necesitas consultar este archivo, hazlo explícitamente y
declarando su procedencia:

```bash
python src/tools/stats_report.py --db data/archive/history_pre-correccion_2026-07.db --list-sessions
```

---

## `sesion_prueba_2026-09-24.db`

Primera sesión con cámara real **después** de la auditoría geométrica
(3.3 min, un usuario, configuración no controlada). Es la **evidencia** de los
cuatro fallos documentados en `docs/validation_report.md` §3.6, no una
medición del Capítulo IV.

### Qué sigue siendo válido

- `ear_avg`, `cervical_angle`, `shoulder_asymmetry`, `mouth_opening` y `fps`
  son mediciones reales con la geometría corregida. La mediana de EAR de ojo
  abierto (0.200) y la de FPS (22.4) se citan en §3.6.
- `sessions.ended_at` y la tabla `events` demuestran que esas dos correcciones
  funcionan en hardware real.

### Qué NO es válido

| Dato | Por qué |
|---|---|
| `perclos` y los eventos `drowsiness` | Calculados con el umbral fijo 0.21, que clasificaba el ojo abierto de este usuario como cerrado el 64% del tiempo |
| `distance_m` | Sin filtrar: incluye saltos de hasta 3.51 m en frames sueltos |
| `pose_valid` | Sus 10 ceros se deben a esos saltos de distancia |
| Las alertas `cervical_angle` (θc 36–47°) | Sin constancia de la postura real ni de la altura de la cámara (§3.6.6) |

### Reproducir el análisis de §3.6

```bash
python src/tools/stats_report.py --db data/archive/sesion_prueba_2026-09-24.db --list-sessions
```
