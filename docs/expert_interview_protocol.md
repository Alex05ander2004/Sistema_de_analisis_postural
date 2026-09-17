# Protocolo de Entrevista con Experto — Fisioterapeuta
## Sistema de Monitoreo Postural y Detección de Fatiga

> ## Estado de este documento: ACTA DE LA RONDA 1 (aplicada el 2026-07-12)
>
> Este archivo se conserva **tal como se aplicó**, con las respuestas del
> experto, porque es la evidencia primaria que sostiene los umbrales del
> Capítulo IV. No debe reescribirse.
>
> Para la siguiente entrevista usa **`docs/expert_interview_protocol_v2.md`**,
> que corrige los defectos de este instrumento y va a por los parámetros que
> quedaron abiertos.
>
> **Resultado de esta ronda:** 9 de 14 parámetros resueltos. Quedaron sin valor
> utilizable la ventana temporal cervical (A2), la ventana de hombros (B2), el
> cooldown entre alertas (D1), los umbrales de aviso (nunca preguntados) y todo
> el bloque ocular (fuera de la especialidad del informante). El análisis de por
> qué se perdieron está en la sección 8 del protocolo v2 — es material para el
> apartado de limitaciones metodológicas de la tesis.
>
> **Hallazgo principal:** la corrección de la distancia cámara-usuario
> (1.60–2.20 m → 0.50–0.70 m). Ver Bloque D2.

**Tesis:** Willy Alexander Huisa Perez — UNSA, 2026  
**Objetivo de la entrevista:** Calibrar los umbrales ergonómicos del sistema con criterio clínico experto  
**Duración estimada:** 30–45 minutos  
**Experto entrevistado:** _______________  
**Especialidad:** _______________  
**Fecha:** _______________

---

> **Contexto para el experto:**  
> El sistema mide posturas de trabajadores de oficina frente a una pantalla usando solo una webcam RGB.  
> No es un dispositivo diagnóstico clínico — es una herramienta preventiva para trastornos musculoesqueléticos.  
> El usuario está sentado frente a la pantalla a una distancia de **0.50 a 1.00 metros** de la cámara.

---

## BLOQUE A — Postura Cervical (Cabeza Adelantada)

El sistema calcula el **ángulo cervical θc**: ángulo entre el vector punto-medio-de-hombros→oreja y el eje **vertical** de la columna.

> **Nota técnica para el experto (si conoce el CVA):** θc es conceptualmente equivalente al **Ángulo Craneovertebral (CVA)** que se usa clínicamente para medir cabeza adelantada, pero con dos diferencias: (1) en vez de C7 (no rastreable por cámara), se usa el punto medio entre ambos hombros como proxy — aproximación común en literatura de visión artificial (Makhmudov et al. 2024, Xing et al. 2023); (2) θc se mide respecto a la **vertical**, mientras que el CVA clínico se mide respecto a la **horizontal**. Son aproximadamente complementarios: **θc ≈ 90° − CVA**. Por eso no se le pide directamente un valor de corte de CVA (ej. "48°"), sino su criterio clínico ya traducido a esta escala (0°=alineado, aumenta con la inclinación).

- **θc = 0°** → cabeza perfectamente alineada con la columna
- **θc = 15°** → leve inclinación hacia adelante
- **θc = 25-30°** → inclinación moderada (cabeza adelantada clara)
- **θc > 45°** → inclinación severa

### Preguntas

**A1.** ¿A partir de qué ángulo cervical considera usted que existe riesgo de lesión o sobrecarga musculoesquelética en un trabajador de oficina?


**Valor umbral recomendado por el experto:** de 30° para abajo °  
**Observaciones:** _______________

---

**A2.** ¿Cuánto tiempo continuo en postura de riesgo cervical genera sobrecarga músculo-tendinosa significativa?

| | < 2 min | 2–5 min | 5–10 min | > 10 min | >20min es demasiado
||---|---|---|---|
| Tiempo de riesgo | ☐ | ☐ | ☐ | ☐ |

**Ventana temporal recomendada para la alerta:** _____ segundos  
**Observaciones:** _______________

---

**A3.** Para la rehabilitación / pausa activa de cabeza adelantada, ¿cuál de los siguientes ejercicios recomienda? *(puede marcar varios)*

- [ ] Retracción cervical (chin tuck)
- [ ] Rotación cervical suave (3 repeticiones por lado)
- [ ] Estiramiento trapecio superior
- [ ] Corrección de postura de silla (ajuste ergonómico)
- [ ] Mirar lejos 20 segundos (descanso visual)
- [ ] Otro: ejercicios de estiramiento caeza cuello, apusa activa

---

## BLOQUE B — Asimetría Escapular (Inclinación de Hombros)

El sistema calcula **ΔE**: ángulo de inclinación del segmento de hombros respecto a la horizontal.

- **ΔE = 0°** → hombros perfectamente nivelados
- **ΔE = 5°** → asimetría leve
- **ΔE = 10°** → asimetría moderada
- **ΔE > 15°** → asimetría severa

### Preguntas

**B1.** ¿A partir de qué ángulo de asimetría escapular considera que existe riesgo de lesión en un trabajador de oficina?

| | < 5° | 5°–10° | 10°–15° | > 15° | Depende |
|---|---|---|---|---|---|
| Riesgo leve | ☐ | ☐ | ☐ | ☐ | ☐ |
| Riesgo moderado | ☐ | ☐ | ☐ | ☐ | ☐ |
| Riesgo severo | ☐ | ☐ | ☐ | ☐ | ☐ |

**Valor umbral recomendado:** _____ °  
**Observaciones:** _______________

---

**B2.** ¿El sistema debe alertar con el mismo tiempo de espera que el cervical (5 s), o considera que la asimetría crónica requiere una ventana diferente?

**Ventana temporal recomendada:** que sean diferentes 
**Observaciones:** _______________

---

**B3.** Pausa activa recomendada para asimetría de hombros: *(puede marcar varios)*

- [ ] Elevación bilateral de hombros + relajación
- [ ] Retracción escapular
- [ ] Estiramiento del pectoral mayor
- [ ] Corrección de altura de monitor/teclado
- [ ] Otro: pausa activa y visitar un fisioterapeuta

---

## BLOQUE C — Fatiga Ocular (EAR)

El sistema calcula el **EAR (Eye Aspect Ratio)**: relación entre la apertura vertical y horizontal del ojo.

- **EAR ≈ 0.35–0.40** → ojos abiertos normalmente
- **EAR ≈ 0.21** → umbral de fatiga (literatura: Soukupová & Čech, 2016)
- **EAR < 0.10** → ojos cerrados

> **Nota técnica:** El parpadeo fisiológico normal dura 100–400 ms y el sistema lo ignora automáticamente. Solo se alerta si EAR ≤ umbral durante **> 3 segundos continuos** (excluyendo parpadeos).

### Preguntas

**C1.** Desde la perspectiva clínica, ¿a qué frecuencia de parpadeo o apertura ocular asocia usted fatiga visual significativa?

**Frecuencia normal de parpadeo:** _____ veces/minuto  
**Frecuencia que indica fatiga:** _____ veces/minuto  
**Observaciones:** _______________

---

**C2.** ¿Le parece adecuado el umbral EAR = 0.21 para definir fatiga ocular en contexto de oficina? *(Si no, indique el valor que recomendaría)*

- [ ] Sí, es adecuado (EAR = 0.21)
- [ ] Demasiado estricto → ajustar a EAR = _____
- [ ] Demasiado permisivo → ajustar a EAR = _____
- [ ] No manejo este parámetro, mantener el de la literatura

**Valor EAR recomendado:** _____  
**Observaciones:** _______________

---

**C3.** ¿Cuánto tiempo de ojos semiabiertos / pesados considera clínicamente relevante como señal de fatiga que requiera pausa?

| | < 1 min | 1–3 min | 3–5 min | > 5 min |
|---|---|---|---|---|
| Fatiga leve | ☐ | ☐ | ☐ | ☐ |
| Fatiga moderada | ☐ | ☐ | ☐ | ☐ |

**Ventana temporal recomendada para alerta:** _____ segundos  
**Observaciones:** _______________

---

**C4.** Pausa activa recomendada para fatiga ocular: *(puede marcar varios)*

- [ ] Regla 20-20-20 (mirar 6 m por 20 seg cada 20 min)
- [ ] Parpadeo consciente (10 repeticiones)
- [ ] Cierre de ojos y descanso (30–60 s)
- [ ] Ejercicios de acomodación visual
- [ ] Otro: _______________

---

## BLOQUE C2 — Bostezo / Somnolencia (Apertura Bucal)

El sistema calcula la **apertura bucal normalizada**: distancia vertical entre labios dividida por el ancho de la boca.

- **Apertura ≈ 0.0** → boca cerrada
- **Apertura ≈ 0.45** → umbral de bostezo (valor actual en `thresholds.json`, sin fuente clínica específica — es una estimación de ingeniería)
- Se alerta si la apertura se mantiene ≥ umbral durante **> 3 segundos continuos**

### Preguntas

**C2.1.** ¿Le parece adecuado usar el bostezo como señal de somnolencia en un trabajador de oficina, o es poco fiable como indicador aislado?

- [ ] Sí, es un indicador válido
- [ ] Es poco fiable solo — debería combinarse con: _______________
- [ ] No es relevante para este contexto

**C2.2.** ¿Qué duración de apertura bucal sostenida considera clínicamente indicativa de somnolencia (vs. un bostezo normal aislado, hablar, o toser)?

**Ventana temporal recomendada:** _____ segundos  
**Observaciones:** _______________

---

## BLOQUE D — Frecuencia y Configuración de Alertas

**D1.** ¿Con qué frecuencia máxima deberían repetirse las alertas del mismo tipo para no generar fatiga de alertas (alert fatigue) en el usuario?

| | Cada 1 min | Cada 5 min | Cada 10 min | Cada 15 min | Cada 30 min |
|---|---|---|---|---|---|
| Recomendación | ☐ | ☐ | ☐ | ☐ | ☐ |

**Cooldown recomendado:** _____ segundos  
**Observaciones:** _______________

---

**D2.** ¿Considera que la distancia de trabajo recomendada de **1.60–2.20 metros** entre usuario y cámara es adecuada para la evaluación postural? *(Esta distancia equivale aproximadamente a la distancia usuario–monitor en una estación de trabajo estándar)*

- [ ] Sí, es adecuada
- [ ] Demasiado cerca → mínimo recomendado: _____ m
- [x] Demasiado lejos → máximo recomendado: 0.60 o 0.50 m
- [ ] La distancia óptima sería: _____ m – _____ m

---

**D3.** ¿Hay algún indicador postural o de fatiga adicional que el sistema debería medir y que no esté contemplado? *(Ej: postura lumbar, posición de muñecas, frecuencia de movimiento)*

_______________________________________________  
_______________________________________________

---

## BLOQUE E — Validación de la App (si hay tiempo)

*Si es posible, mostrar la aplicación al experto y solicitar su feedback.*

**E1.** Observando el feed de video con los landmarks de MediaPipe, ¿los puntos de referencia (hombros, orejas) coinciden con los que usted utilizaría para la evaluación postural manual?

- [ ] Sí, son los puntos correctos
- [ ] No, debería usarse: _______________

**E2.** ¿El sistema de semaforización (verde/amarillo/rojo) comunica claramente el riesgo al usuario?

- [ ] Sí
- [ ] Sugerencia: _______________

**E3.** ¿La pausa activa recomendada en las alertas es clínicamente apropiada para prevenir trastornos musculoesqueléticos?

- [ ] Sí
- [ ] Modificación sugerida: _______________

---

## Resumen de Valores a Ingresar al Sistema

*Completado tras la entrevista del 2026-07-12. Ya aplicado en `config/thresholds.json`.*

| Parámetro | Valor actual (literatura) | Valor recomendado por experto | Estado |
|---|---|---|---|
| Ángulo cervical máximo (θc) | 15.0° | **30.0°** (≤30° aceptable) | ✅ Aplicado |
| Ventana temporal postura | 5.0 s | *No definido clínicamente — requiere revisión bibliográfica propia* | ⬜ Pendiente (queda en 5.0 s provisional) |
| Asimetría escapular máxima (ΔE) | 10.0° | **10.0°** (confirmado sin cambios) | ✅ Aplicado (sin cambio) |
| Ventana temporal asimetría | 5.0 s | *Pidió que sea distinta a la cervical, sin dar valor numérico* | ⬜ Pendiente (queda en 5.0 s provisional) |
| Umbral EAR de fatiga | 0.21 | Sin objeción (no es su especialidad; sugiere consultar oftalmólogo para mayor precisión) | ✅ Sin cambio |
| Ventana temporal fatiga ocular | 3.0 s | Sin objeción | ✅ Sin cambio |
| Umbral apertura bucal (bostezo) | 0.45 | Sin objeción (sugiere consultar especialista en somnolencia para mayor precisión) | ✅ Sin cambio |
| Ventana temporal bostezo | 3.0 s | Sin objeción | ✅ Sin cambio |
| Cooldown entre alertas | 30 s | No respondido en esta sesión | ⬜ Pendiente |
| Distancia mínima cámara-usuario | 1.60 m | **0.50 m** | ✅ Aplicado |
| Distancia máxima cámara-usuario | 2.20 m | **0.70 m** | ✅ Aplicado |
| Altura de cámara | No especificada | **A la altura de los ojos del usuario** | ✅ Aplicado (texto UI + README) |
| Pausa activa cervical/hombros | Genérica | Estiramientos cabeza-cuello/hombros + recomendar visitar fisioterapeuta si persiste | ✅ Aplicado en `alert_notifier.py` |
| Instrumento complementario | — | **Índice de Discapacidad Cervical (NDI)** — cuestionario auto-reportado, uso pre/post | 📋 Documentado como trabajo futuro (sección 4.5 de `validation_report.md`) |

**Hallazgo clave de esta entrevista:** la distancia original (1.60–2.20 m) inducía que el usuario se encorve para ver la pantalla, contaminando la propia postura medida. Ver detalle en `docs/validation_report.md`.

> **Advertencia sobre la escala de A1.** Al experto se le pidió un valor en la
> escala θc advirtiéndole que θc ≈ 90° − CVA. Respondió "de 30° para abajo" y
> ese valor se aplicó como θc ≤ 30°. No consta si estaba razonando en la escala
> θc o en la del CVA, y el significado clínico es opuesto (CVA = 30° sería una
> cabeza adelantada severa). El bloque A0 del protocolo v2 resuelve esta
> ambigüedad con una escala visual en lugar de numérica; hasta entonces, el
> umbral de 30° debe declararse en el Capítulo IV con esta salvedad.

> **Nota sobre la validez temporal de estos umbrales.** Esta entrevista se
> aplicó cuando el cálculo de θc arrastraba un sesgo constante de ~13° y el de
> ΔE un error de escala del 33% (ambos corregidos después — ver
> `docs/validation_report.md` §3.3). Los umbrales **siguen siendo válidos**
> porque el experto los emitió sobre una descripción verbal de la escala, no
> sobre lecturas del sistema. Lo que no es válido es cualquier **medición**
> tomada antes de la corrección.

---

## Instrucciones post-entrevista

`src/tools/expert_calibration.py` (script interactivo) **no existe** — no se construyó, se optó por edición manual dado el volumen de cambios. Los valores de arriba ya están aplicados directamente en `config/thresholds.json` (con comentarios `_..._comment` explicando cada cambio y qué sigue pendiente). Si en una futura sesión el experto define las ventanas temporales o el cooldown, edita esos campos directamente y actualiza esta tabla.

Guarda el archivo y reinicia la app (`.venv\Scripts\python.exe -m src.ui.app_ui`) para que tome los nuevos valores — no requiere recompilar nada. Registra también la fuente (nombre/especialidad del experto, fecha) en la sección 8 de `docs/validation_report.md` para justificar los umbrales en el Capítulo IV.
