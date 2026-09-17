# Protocolo de Entrevista con Expertos — Ronda 2
## Sistema de Monitoreo Postural y Detección de Fatiga

**Tesis:** Willy Alexander Huisa Perez — UNSA, 2026
**Versión:** 2.0 — sustituye a `expert_interview_protocol.md` (v1, aplicado el 2026-07-12)
**Estado:** instrumento preparado, **pendiente de aplicar**

---

## 0. Por qué existe esta ronda 2

La ronda 1 resolvió tres parámetros (θc máximo, ΔE máximo y, sobre todo, la
distancia cámara-usuario) y produjo el hallazgo más importante del proyecto
hasta la fecha. Pero **dejó 5 de 14 parámetros sin valor utilizable**, y el
análisis del instrumento (sección 8 de este documento) muestra que eso no fue
casualidad: fue consecuencia de cómo estaban formuladas las preguntas.

Esta ronda 2 corrige el instrumento y va a por lo que quedó abierto. También
cubre parámetros que **no existían** cuando se hizo la ronda 1 y que hoy sí
disparan alertas (PERCLOS) o condicionan la validez de la medición (umbrales
de aviso, control de distancia).

### Lo que sigue abierto tras la ronda 1

| # | Parámetro | Estado tras ronda 1 | Bloque de esta ronda |
|---|---|---|---|
| 1 | `cervical_alert_window_sec` | Sin valor. El experto marcó ">20 min es demasiado", que acota el techo pero no fija la ventana | A2 |
| 2 | `shoulder_alert_window_sec` | "Que sean diferentes", sin número. Fijado provisionalmente en 8.0 s por criterio de ingeniería | B2 |
| 3 | `alert_cooldown_sec` | No respondido | D1 |
| 4 | Umbrales de **aviso** (semáforo amarillo) | Nunca preguntados. Hoy están cableados al 70% del umbral rojo, sin base clínica | A3, B3 |
| 5 | `mouth_opening_threshold` (MAR) | Sin objeción, pero sin fuente | C2-3 (+ calibración empírica) |
| 6 | Frecuencia de parpadeo (C1 v1) | Sin responder — no es especialidad del fisioterapeuta | Módulo II |
| 7 | `perclos_threshold` | No existía en la ronda 1 | Módulo II |
| 8 | Validación visual de landmarks (Bloque E v1) | No se llegó a hacer | Bloque E |
| 9 | Criterios de exclusión de participantes | Nunca preguntados | Bloque F |

---

## 1. Identificación del experto y consentimiento

> **Obligatorio antes de empezar.** En la ronda 1 estos campos quedaron en
> blanco. Un umbral justificado como "criterio clínico experto" no es citable
> en el Capítulo IV si no consta quién lo emitió y con qué credenciales.

| Campo | Respuesta |
|---|---|
| Nombre completo | |
| Profesión / especialidad | |
| Nº de colegiatura (CMP / CTFP u otro) | |
| Años de experiencia en el área | |
| Institución / centro de trabajo | |
| Experiencia previa en ergonomía ocupacional (sí/no, describir) | |
| Fecha y duración de la entrevista | |
| Modalidad (presencial / videollamada) | |

**Consentimiento** *(leer en voz alta y marcar)*

- [ ] Acepto participar como informante experto en esta investigación de tesis.
- [ ] Acepto que mi nombre y especialidad se citen en el documento de tesis
      como fuente de los umbrales calibrados.
- [ ] Prefiero que mi aporte se cite de forma anónima (p. ej. "Fisioterapeuta,
      N años de experiencia").
- [ ] Acepto que la sesión se grabe en audio, únicamente para transcripción.

*Firma / confirmación verbal registrada:* ______________________

---

## 2. Cómo aplicar este instrumento (notas para el entrevistador)

Cuatro reglas que salen directamente de lo que falló en la ronda 1:

1. **Toda pregunta numérica lleva un valor propuesto.** Preguntar "¿cuántos
   segundos?" en abstracto produce respuestas como "que sean diferentes".
   Preguntar "proponemos 5 s, ¿lo aceptaría o lo cambiaría?" fuerza una
   respuesta accionable. Si el experto lo acepta, eso **también** es un dato
   validado y así se registra.

2. **No pedir números sobre escalas que el experto no usa a diario.** θc es una
   escala construida para este sistema. En vez de pedir un ángulo, se muestran
   posturas y se pide clasificarlas; el número lo deriva el sistema. Ver
   Bloque A.

3. **Una pregunta, un dato.** En la v1, B1 mezclaba una tabla de tres niveles
   de riesgo con un único "valor umbral recomendado", y el resultado fue que
   la tabla quedó vacía. Aquí, cada nivel se pregunta por separado.

4. **Cerrar cada bloque leyendo en voz alta el valor registrado** y pidiendo
   confirmación explícita. Es lo que evita las ambigüedades del acta de la
   ronda 1.

**Duración estimada:** Módulo I, 40–50 min. Módulos II y III, 20–25 min cada uno.

---

## 3. A quién entrevistar

El instrumento está dividido en tres módulos porque la ronda 1 mostró el
límite del enfoque de un solo informante: al fisioterapeuta se le preguntó por
el umbral EAR y por el bostezo, y respondió — correctamente — que no era su
especialidad y que se consultara a un oftalmólogo y a un especialista en
somnolencia. Preguntar fuera de especialidad no produce validación: produce un
"sin objeción" que luego no sostiene nada en el Capítulo IV.

| Módulo | Perfil del experto | Qué calibra |
|---|---|---|
| **I — Postural** | Fisioterapeuta / terapeuta ocupacional / ergónomo | θc, ΔE, ventanas posturales, pausas activas, exclusiones |
| **II — Ocular y somnolencia** | Optometrista u oftalmólogo; si es posible, además médico ocupacional | EAR, tasa de parpadeo, PERCLOS, MAR |
| **III — Alertas y aceptación** | Cualquiera de los anteriores, o especialista en salud ocupacional | Cooldown, formato de alerta, fatiga de alertas |

> **Sobre el número de informantes.** Con un solo experto por módulo, el
> Capítulo IV debe declarar explícitamente que los umbrales provienen de un
> **juicio de experto individual**, no de un consenso. Si se puede llegar a
> **tres informantes en el Módulo I**, se puede reportar la mediana de sus
> respuestas y un índice de acuerdo, que es un respaldo metodológico
> sustancialmente más fuerte por muy poco coste adicional. Recomendado.

---

# MÓDULO I — Postura (fisioterapeuta / ergónomo)

## BLOQUE A — Postura Cervical

### A0. Calibración de la escala *(hacer antes que A1 — 5 min)*

> **Por qué.** En la ronda 1 se le pidió al experto un número en la escala θc,
> advirtiéndole que θc ≈ 90° − CVA. Respondió "de 30° para abajo". Ese valor se
> aplicó como θc ≤ 30°, pero **no consta si estaba pensando en la escala θc o
> en la del CVA**, y la diferencia es enorme: un CVA de 30° es una cabeza
> adelantada severa, mientras que θc = 30° es el límite de lo moderado. Es
> exactamente el tipo de ambigüedad que una escala visual elimina.

**Material:** cuatro fotografías de perfil del mismo sujeto (o el propio
sistema en vivo), etiquetadas P1–P4, correspondientes a θc ≈ 0°, 15°, 30° y 45°.

Pedir al experto que las ordene y clasifique **sin ver los números**:

| Foto | Alineada | Leve | Moderada | Severa |
|---|---|---|---|---|
| P1 | ☐ | ☐ | ☐ | ☐ |
| P2 | ☐ | ☐ | ☐ | ☐ |
| P3 | ☐ | ☐ | ☐ | ☐ |
| P4 | ☐ | ☐ | ☐ | ☐ |

**A0.1.** ¿Cuál es la **primera** foto en la que usted ya recomendaría una
corrección postural? → **P____**

*Esta respuesta, y no un número dicho en abstracto, es la que fija el umbral.*

---

### A1. Umbral de alerta (rojo)

> Valor actual en el sistema: **θc > 30°**. Corresponde a unos **12 cm** de
> desplazamiento anterior de la cabeza en un adulto de talla media.

- [ ] Confirmo 30°
- [ ] Lo bajaría a: _____ ° (más sensible, alerta antes)
- [ ] Lo subiría a: _____ ° (más permisivo)

**Observaciones:** _______________

### A2. Ventana temporal de alerta cervical ⚠️ *(parámetro abierto nº 1)*

> El sistema no alerta al instante: exige que la postura de riesgo se mantenga
> de forma continua durante una ventana, para no reaccionar a un gesto puntual
> (mirar el teclado, agacharse a recoger algo).
>
> En la ronda 1 usted indicó que **más de 20 minutos** en postura de riesgo ya
> es claramente excesivo. Eso fija el techo. Lo que falta es el disparo.

**Propuesta del sistema: 5 segundos continuos.**

**A2.1.** ¿Le parece razonable avisar tras **5 segundos** continuos de postura
cervical de riesgo?

- [ ] Sí, 5 s es adecuado
- [ ] Demasiado corto → recomiendo _____ segundos
- [ ] Demasiado largo → recomiendo _____ segundos

**A2.2.** *(Aclaración importante — no saltarla)* Hay dos cosas distintas y el
sistema puede hacer las dos:

- **(a)** *Detectar* la postura de riesgo: cuánto tiempo debe mantenerse para
  considerar que no es un gesto pasajero → **_____ segundos**
- **(b)** *Interrumpir* al usuario con la recomendación de pausa activa: cuánto
  tiempo acumulado de mala postura justifica cortarle el trabajo →
  **_____ minutos**

**Observaciones:** _______________

### A3. Umbral de aviso (amarillo) ⚠️ *(parámetro abierto nº 4)*

> El panel tiene un nivel intermedio, **amarillo**, antes del rojo: no
> interrumpe, solo avisa visualmente de que la postura se está degradando.
> Hoy está fijado automáticamente en el 70% del umbral rojo (21°) **sin
> ninguna base clínica** — nunca se preguntó en la ronda 1.

**¿A partir de qué ángulo mostraría el aviso amarillo?** _____ °
*(debe ser menor que el umbral rojo de A1)*

- [ ] O bien: no le ve utilidad al nivel intermedio, con dos estados basta

### A4. Asimetría de la alerta *(pregunta nueva)*

> Todo umbral se equivoca en las dos direcciones. Saber cuál de los dos errores
> le preocupa más a un clínico es lo que permite mover el umbral con criterio
> en vez de a ojo.

¿Qué error prefiere que cometa el sistema?

- [ ] Que **avise de más** (alguna alerta innecesaria, pero no se le escapa
      ninguna postura de riesgo)
- [ ] Que **avise de menos** (solo alerta cuando es indudable, aunque se le
      escape alguna postura de riesgo)
- [ ] Equilibrado

**Por qué:** _______________

### A5. Pausa activa cervical

> Ya aplicado en el sistema desde la ronda 1: retracción cervical (*chin tuck*),
> 3 rotaciones suaves por lado, estiramiento de trapecio superior, y la
> recomendación de acudir a un fisioterapeuta si el malestar persiste.

- [ ] Correcto tal como está
- [ ] Modificaría: _______________
- [ ] Añadiría: _______________

**A5.1.** ¿Cuánto debería durar la pausa activa recomendada? _____ segundos

---

## BLOQUE B — Asimetría Escapular (ΔE)

> **Nota técnica de esta ronda.** La medición de ΔE tenía un error de escala
> del 33% que se corrigió después de la ronda 1: una inclinación real de 10° se
> reportaba como 13.2°, de modo que el umbral de 10° que usted confirmó estaba
> disparando en realidad a partir de ~7.5° reales. Ya está corregido: hoy 10°
> medidos son 10° reales. Conviene reconfirmarlo sobre esa base.

### B1. Umbral de alerta (rojo)

**¿Confirma 10° como umbral de asimetría escapular, sabiendo que ahora la
medición es exacta?**

- [ ] Sí, confirmo 10°
- [ ] Lo ajustaría a: _____ °

### B2. Ventana temporal de hombros ⚠️ *(parámetro abierto nº 2)*

> En la ronda 1 usted pidió que esta ventana fuera **distinta** de la cervical,
> pero no dio un valor. El sistema la fijó provisionalmente en **8 segundos**
> (frente a los 5 s de la cervical), con el razonamiento de que la asimetría
> escapular es un patrón más crónico y sostenido que la flexión cervical, y de
> que una ventana más larga evita disparos por gestos transitorios como
> alcanzar el ratón o girarse a hablar con alguien.

**¿Le parece correcto ese razonamiento y el valor de 8 segundos?**

- [ ] Sí, confirmo 8 s
- [ ] El razonamiento es correcto pero el valor debería ser _____ s
- [ ] La ventana debería ser **más corta** que la cervical, porque: _______________

### B3. Umbral de aviso (amarillo) ⚠️

**¿A partir de qué inclinación mostraría el aviso amarillo?** _____ °
*(hoy: 7°, sin base clínica)*

### B4. Pausa activa de hombros

- [ ] Correcta tal como está (retracción escapular + estiramiento de pectoral
      y trapecio + recomendación de acudir a fisioterapeuta)
- [ ] Modificaría: _______________

---

## BLOQUE F — Criterios de exclusión de participantes *(bloque nuevo)*

> La tesis declara que el sistema **no aplica a usuarios con patologías
> estructurales preexistentes**, pero nunca se definió cómo identificarlos.
> Sin esto, la sesión de validación puede incluir a alguien cuya postura basal
> esté alterada por una condición estructural, y esas lecturas contaminarían la
> matriz de confusión del Capítulo IV.

**F1.** ¿Qué condiciones deberían excluir a una persona de participar en la
prueba de validación? *(marcar las que apliquen)*

- [ ] Escoliosis diagnosticada
- [ ] Hipercifosis dorsal estructurada
- [ ] Cirugía cervical o de hombro en los últimos ___ meses
- [ ] Hernia discal cervical
- [ ] Tortícolis congénita o adquirida
- [ ] Dismetría de miembros inferiores > ___ cm
- [ ] Dolor cervical o de hombro agudo en el momento de la prueba
- [ ] Otra: _______________

**F2.** ¿Bastaría un cuestionario auto-reportado de tamizaje o haría falta una
evaluación presencial?

- [ ] Cuestionario auto-reportado es suficiente
- [ ] Hace falta evaluación presencial
- [ ] Cuestionario + observación breve

**F3.** ¿Qué preguntas mínimas debería incluir ese tamizaje?

_______________________________________________

**F4.** Sobre el **Índice de Discapacidad Cervical (NDI)** que usted recomendó
en la ronda 1: ¿lo aplicaría como cribado **antes** de la prueba, como medida
de resultado **antes/después**, o ambas?

- [ ] Solo cribado previo
- [ ] Solo medida de resultado pre/post
- [ ] Ambas
- [ ] ¿Qué puntuación NDI recomendaría como criterio de exclusión? _____ %

---

# MÓDULO II — Fatiga ocular y somnolencia
### *(optometrista / oftalmólogo; PERCLOS y bostezo: médico ocupacional o especialista en sueño)*

> **Contexto para el experto.** El sistema no mide agudeza visual ni hace
> diagnóstico. Estima, desde una webcam, cuánto tiempo tiene abiertos los ojos
> el usuario y con qué frecuencia parpadea, para recomendar pausas visuales.

## BLOQUE C — Indicadores oculares

### C1. Umbral EAR

> **EAR** (*Eye Aspect Ratio*) es la razón entre la apertura vertical del
> párpado y el ancho del ojo. Ojo bien abierto ≈ 0.30–0.35; ojo cerrado < 0.10.
> El sistema usa **0.21** como frontera "ojo cerrado", tomado de la literatura
> de visión artificial (Soukupová & Čech, 2016), no de la clínica.

**C1.1.** ¿Conoce o utiliza alguna medida equivalente en su práctica?

- [ ] Sí: _______________
- [ ] No, pero el criterio de "ojo cerrado" me parece razonable
- [ ] No manejo este parámetro → mantener el valor de la literatura

**C1.2.** ¿Debería el umbral ajustarse por persona (calibración individual al
inicio de la sesión) o es aceptable un valor único para todos?

- [ ] Valor único es aceptable
- [ ] Debería calibrarse por persona, porque: _______________

### C2. Tasa de parpadeo ⚠️ *(parámetro abierto nº 6)*

> El sistema **ya mide** parpadeos por minuto y lo muestra en el panel, pero
> **no dispara ninguna alerta con ese dato**, porque nadie ha fijado todavía un
> valor de corte. Esta pregunta es la que desbloquea ese indicador.

| | Valor |
|---|---|
| Frecuencia de parpadeo normal en trabajo de oficina | _____ /min |
| Frecuencia que ya sugiere fatiga visual | _____ /min |
| ¿Fatiga se asocia a parpadeo **reducido**, **aumentado**, o ambos? | _____ |

**C2.1.** ¿Sobre qué ventana de tiempo tiene sentido promediar esa frecuencia?
_____ minutos

### C3. PERCLOS *(parámetro nuevo — no existía en la ronda 1)*

> **PERCLOS** es el porcentaje de tiempo con los ojos cerrados dentro de una
> ventana móvil. Es la medida de somnolencia mejor validada en la literatura de
> conducción y teleoperación. El sistema la calcula sobre **60 segundos** y
> alerta si supera el **15%**, según los valores clásicos de esa literatura
> (Wierwille & Ellsworth, 1994; Dinges & Grace, 1998).
>
> Esos estudios se hicieron en conducción, no en trabajo de oficina.

**C3.1.** ¿Le parece trasladable ese umbral del 15% al trabajo frente a pantalla?

- [ ] Sí, es razonable
- [ ] Es demasiado sensible → recomendaría _____ %
- [ ] Es demasiado permisivo → recomendaría _____ %
- [ ] No es trasladable; en oficina usaría más bien: _______________

**C3.2.** ¿Y la ventana de 60 segundos?

- [ ] Adecuada
- [ ] Recomendaría _____ segundos

**C3.3.** ¿Debería este indicador **alertar** al usuario, o solo **registrarse**
para el informe de la sesión?

- [ ] Alertar
- [ ] Solo registrar
- [ ] Alertar solo si coincide con otro indicador (bostezo, postura)

### C4. Bostezo (MAR) ⚠️ *(parámetro abierto nº 5)*

> El sistema detecta apertura bucal sostenida ≥ 0.45 (razón entre apertura
> vertical y ancho de la boca) durante más de 3 segundos. El valor 0.45 es una
> **estimación de ingeniería sin ninguna fuente**.

**C4.1.** ¿Considera el bostezo un indicador utilizable de somnolencia en
oficina, o es demasiado ruidoso de forma aislada?

- [ ] Indicador válido por sí solo
- [ ] Solo combinado con: _______________
- [ ] No es relevante en este contexto → *(desactivar la alerta)*

**C4.2.** ¿Qué duración de apertura bucal sostenida distingue un bostezo de
hablar, reír o toser? _____ segundos

**C4.3.** ¿Cuántos bostezos en qué periodo justificarían una alerta?
_____ bostezos en _____ minutos

### C5. Pausa activa visual

- [ ] Correcta tal como está (regla 20-20-20, parpadeo consciente ×10, cierre
      de ojos 30 s)
- [ ] Modificaría: _______________

**C5.1.** ¿Con qué periodicidad debería el sistema recordar la regla 20-20-20
de forma preventiva, aunque no detecte fatiga? Cada _____ minutos

---

# MÓDULO III — Alertas, aceptación y validación de la app

## BLOQUE D — Régimen de alertas

### D1. Cooldown entre alertas ⚠️ *(parámetro abierto nº 3)*

> **No se llegó a responder en la ronda 1.** Es el parámetro con más impacto
> sobre si la gente sigue usando el sistema o lo cierra a la media hora.
>
> Valor actual: **30 segundos**. Con una postura de riesgo sostenida, eso son
> **dos ventanas modales bloqueantes por minuto** — casi con seguridad
> demasiado.

**¿Cuál es el intervalo mínimo entre dos alertas del mismo tipo?**

| Cada 30 s | Cada 2 min | Cada 5 min | Cada 10 min | Cada 15 min | Cada 30 min |
|---|---|---|---|---|---|
| ☐ | ☐ | ☐ | ☐ | ☐ | ☐ |

**Valor recomendado: _____ segundos**

**D1.1.** ¿Debería el intervalo **crecer** si el usuario ignora alertas
sucesivas, para no volverse invasivo?

- [ ] Sí
- [ ] No, mantener constante

**D1.2.** ¿Cuántas alertas por hora son aceptables antes de que la herramienta
se vuelva contraproducente? _____ /hora

### D2. Formato de la alerta

> Hoy es una **ventana modal bloqueante**: el usuario no puede seguir hasta
> pulsar "Entendido".

- [ ] El modal bloqueante es adecuado
- [ ] Debería ser un aviso no bloqueante (esquina, sonido suave)
- [ ] Bloqueante solo para postura sostenida; no bloqueante para el resto
- [ ] Otra: _______________

### D3. Indicadores adicionales

¿Qué otro indicador postural o de fatiga debería medir el sistema y hoy no mide?

- [ ] Postura lumbar / respaldo
- [ ] Posición de muñecas
- [ ] Rotación de tronco
- [ ] Frecuencia de micro-movimientos (estatismo postural)
- [ ] Tiempo continuo sentado sin levantarse
- [ ] Otro: _______________

**D3.1.** De los que marcó, ¿cuál priorizaría si solo se pudiera añadir uno?
_______________

---

## BLOQUE E — Validación de la aplicación en funcionamiento
### ⚠️ Bloque obligatorio en esta ronda

> En la ronda 1 este bloque figuraba como "si hay tiempo" y **no se llegó a
> hacer**. Es la única parte del protocolo donde el experto valida lo que el
> sistema realmente mide y no una descripción de lo que se supone que mide.
>
> **Reservar 15 minutos con la aplicación en marcha.** El overlay facial ahora
> dibuja exactamente los puntos que entran en cada fórmula, de modo que el
> experto puede ver sobre qué se calcula cada métrica.

### E1. Landmarks posturales

Mostrar el feed con el esqueleto superpuesto.

**¿Los puntos que usa el sistema (orejas y acromion de cada hombro) son los que
usted usaría para una evaluación postural manual?**

- [ ] Sí, son los puntos correctos
- [ ] Usaría en su lugar: _______________
- [ ] Faltaría añadir: _______________

**E1.1.** El sistema toma el **punto medio entre ambas orejas** como referencia
de la cabeza, en lugar de C7 (que una cámara no puede ver). ¿Le parece una
aproximación aceptable?

- [ ] Sí
- [ ] No, porque: _______________

### E2. Concordancia con la evaluación manual

> Prueba de validez de criterio: la comparación más fuerte que puede aportar
> esta tesis.

Pedir al sujeto de prueba tres posturas. Para cada una, el experto la clasifica
**sin ver la pantalla**, y luego se anota lo que marcó el sistema.

| Postura | Clasificación del experto (alineada / leve / moderada / severa) | θc medido | ΔE medido | ¿Concuerda? |
|---|---|---|---|---|
| Neutra | | | | ☐ Sí ☐ No |
| Cabeza adelantada moderada | | | | ☐ Sí ☐ No |
| Cabeza adelantada marcada | | | | ☐ Sí ☐ No |
| Hombro derecho elevado | | | | ☐ Sí ☐ No |
| Hombro izquierdo elevado | | | | ☐ Sí ☐ No |

*Repetir con 3 sujetos si es posible. Esta tabla alimenta directamente la
matriz de confusión de la sección 4.2 del reporte de validación.*

### E3. Semaforización

- [ ] Verde / amarillo / rojo comunica claramente el nivel de riesgo
- [ ] Sugerencia: _______________

### E4. Indicador de distancia

> Nuevo en esta versión: el panel estima la distancia al usuario y avisa si se
> sale del rango 0.50–0.70 m que usted definió en la ronda 1.

- [ ] Útil tal como está
- [ ] Sugerencia: _______________

### E5. Valoración global

**¿Recomendaría esta herramienta como apoyo preventivo en un puesto de trabajo
de oficina?**

- [ ] Sí, tal como está
- [ ] Sí, con las modificaciones indicadas
- [ ] No todavía; antes haría falta: _______________

**Comentario libre:**

_______________________________________________
_______________________________________________

---

## 4. Verificación de consistencia *(para el entrevistador, al cerrar)*

Leer en voz alta y confirmar uno por uno. Si alguna respuesta contradice otra
anterior, anotarlo y preguntar cuál prevalece — no resolverlo por cuenta propia
después.

| Parámetro | Valor registrado | Confirmado |
|---|---|---|
| θc alerta (rojo) | _____ ° | ☐ |
| θc aviso (amarillo) | _____ ° | ☐ |
| Ventana cervical (detección) | _____ s | ☐ |
| Tiempo acumulado para interrumpir | _____ min | ☐ |
| ΔE alerta (rojo) | _____ ° | ☐ |
| ΔE aviso (amarillo) | _____ ° | ☐ |
| Ventana de hombros | _____ s | ☐ |
| Cooldown entre alertas | _____ s | ☐ |
| EAR umbral | _____ | ☐ |
| Parpadeo normal / fatiga | _____ / _____ /min | ☐ |
| PERCLOS umbral / ventana | _____ % / _____ s | ☐ |
| MAR umbral / ventana | _____ / _____ s | ☐ |

**Comprobación cruzada:** ¿el umbral amarillo es menor que el rojo en ambas
métricas posturales? ¿La ventana de hombros es distinta de la cervical, como
pidió en la ronda 1? ☐

---

## 5. Después de la entrevista

1. Volcar los valores en `config/thresholds.json`, sustituyendo el comentario
   `_..._comment` de cada parámetro por la fuente real (nombre o código del
   experto y fecha). Los valores marcados hoy como "PROVISIONAL" deben dejar
   de estarlo o quedar justificados como tales.
2. Actualizar la tabla de la sección 8 de `docs/validation_report.md`
   (referencias de umbrales) con el origen de cada valor.
3. Registrar en este documento la fecha de aplicación y adjuntar la
   transcripción o las notas.
4. Reiniciar la aplicación: los umbrales se leen del JSON al arrancar, no hace
   falta recompilar nada.

---

## 6. Registro de aplicación

| Módulo | Experto | Fecha | Duración | Estado |
|---|---|---|---|---|
| I — Postural | | | | ⬜ Pendiente |
| II — Ocular / somnolencia | | | | ⬜ Pendiente |
| III — Alertas y app | | | | ⬜ Pendiente |

---

## 7. Resumen de valores obtenidos *(rellenar tras aplicar)*

| Parámetro | Valor ronda 1 | Valor ronda 2 | Fuente | Aplicado |
|---|---|---|---|---|
| `cervical_angle_max_deg` | 30.0 | | | ⬜ |
| `cervical_warn_deg` *(nuevo)* | — (70% cableado) | | | ⬜ |
| `cervical_alert_window_sec` | 5.0 (provisional) | | | ⬜ |
| `shoulder_asymmetry_max_deg` | 10.0 | | | ⬜ |
| `shoulder_warn_deg` *(nuevo)* | — (70% cableado) | | | ⬜ |
| `shoulder_alert_window_sec` | 8.0 (provisional) | | | ⬜ |
| `ear_threshold` | 0.21 (literatura) | | | ⬜ |
| `ear_alert_window_sec` | 3.0 | | | ⬜ |
| `blink_rate` umbral *(nuevo)* | — (solo se mide) | | | ⬜ |
| `perclos_threshold` *(nuevo)* | 0.15 (literatura) | | | ⬜ |
| `perclos_window_sec` *(nuevo)* | 60.0 (literatura) | | | ⬜ |
| `mouth_opening_threshold` | 0.45 (ingeniería) | | | ⬜ |
| `yawn_alert_window_sec` | 3.0 | | | ⬜ |
| `alert_cooldown_sec` | 30 (sin validar) | | | ⬜ |
| Criterios de exclusión | — | | | ⬜ |

---

## 8. Análisis crítico del instrumento de la ronda 1

> Esta sección documenta **por qué** la ronda 1 dejó abiertos 5 de 14
> parámetros. Es material directamente utilizable en el apartado de
> limitaciones metodológicas del Capítulo IV.

### 8.1 Lo que funcionó

El hallazgo de la distancia (1.60–2.20 m → 0.50–0.70 m) justifica la entrevista
por sí solo. Salió del Bloque D2, que era la única pregunta del instrumento que
**no** pedía un valor abstracto, sino que sometía a juicio un supuesto concreto
del diseño ("¿es adecuada esta distancia?"). El experto pudo reconocer de
inmediato que era una distancia de sala de reuniones y no de escritorio, y
señalar el efecto de segundo orden que nadie había visto: a esa distancia el
usuario se encorva para leer la pantalla, de modo que **el propio montaje de
captura inducía la mala postura que el sistema pretendía medir**.

La lección es transferible: las preguntas que someten a crítica una decisión
concreta del diseño producen mucho más que las que piden un número en el aire.
La ronda 2 se ha reescrito entera con ese principio.

### 8.2 Fallos del instrumento y su efecto

| # | Fallo | Efecto observado |
|---|---|---|
| 1 | **Preguntas numéricas sin ancla.** A2, B2, C3 y D1 pedían un valor en abstracto | Respuestas no accionables: "que sean diferentes" (B2), ">20 min es demasiado" (A2). Corregido: toda pregunta numérica propone ahora un valor a aceptar o corregir |
| 2 | **Tablas mal formadas.** La tabla de A2 tenía la fila de encabezado rota, y el experto acabó escribiendo su respuesta dentro del propio encabezado | La opción marcada se perdió. Corregido: tablas simples y validadas |
| 3 | **Casillas nunca marcadas.** En A3, B1, C2, C3 y D1 el experto respondió en texto libre junto a las casillas, sin marcarlas | Se perdió la granularidad que la casilla pretendía capturar. Corregido: se pide confirmación verbal explícita al cerrar cada bloque (sección 4) |
| 4 | **Una pregunta con dos objetivos.** B1 mezclaba una matriz de tres niveles de riesgo con un único "valor umbral recomendado" | La matriz quedó vacía y solo sobrevivió el número. Corregido: un dato por pregunta |
| 5 | **Preguntar fuera de especialidad.** Se preguntó a un fisioterapeuta por el umbral EAR y por el bostezo | Respondió, con buen criterio, "sin objeción, consulten a un oftalmólogo". Un "sin objeción" de quien no es especialista **no valida nada**, y así debe declararse en el Capítulo IV. Corregido: instrumento dividido en tres módulos por perfil |
| 6 | **Escala no nativa del experto.** Se le pidió un número en la escala θc, advirtiéndole que θc ≈ 90° − CVA | No consta si "30° para abajo" se refería a θc o a CVA, y el significado clínico es opuesto. Corregido: escala visual (A0) en lugar de numérica |
| 7 | **Bloque E opcional.** La validación con la app en marcha figuraba como "si hay tiempo" | No se hizo. Es la única evidencia de validez de criterio que la tesis puede aportar. Corregido: bloque obligatorio con tiempo reservado |
| 8 | **Sin identificación ni consentimiento.** Los campos de nombre, especialidad y fecha quedaron en blanco | El umbral no es citable como juicio experto sin constancia de quién lo emitió. Corregido: sección 1 obligatoria |
| 9 | **Parámetros del sistema nunca preguntados.** Umbrales de aviso (amarillo), criterios de exclusión de participantes, duración de la pausa activa | Siguen sin base clínica: el amarillo está cableado al 70% del rojo. Corregido: A3, B3, A5.1, Bloque F |
| 10 | **N = 1.** Un solo informante para todos los umbrales | Amenaza de validez que debe declararse. Mitigación propuesta: tres informantes en el Módulo I y reporte de la mediana |

### 8.3 Riesgo abierto que no resuelve el instrumento

La ronda 1 se aplicó cuando el cálculo de θc tenía un sesgo constante de ~13°
por usar una sola oreja como referencia, y el de ΔE un error de escala del 33%.
El experto emitió sus umbrales **sobre una descripción verbal de la escala**,
no sobre lecturas del sistema, así que sus valores siguen siendo válidos — pero
cualquier medición tomada antes de la corrección (incluida la tabla 4.4 del
reporte de validación) no es comparable con los umbrales actuales y debe
repetirse. Ver `docs/validation_report.md` §3.3.
