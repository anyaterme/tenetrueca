# Micropiloto real OpenAI de idioma y formato — 2026-11-09

**Resultado global: no satisfactorio.** Se ejecutaron las cuatro llamadas autorizadas,
sin reintentos. E36 produjo dos respuestas válidas en inglés. E34 no produjo una respuesta
aceptada en tres frases en ninguno de los dos modos. No se aprueban conjuntamente ambas
correcciones para producción a partir de este micropiloto.

El ciclo de ejecución terminó y el ledger quedó conciliado: cuatro intentos liquidados,
ninguna reserva desconocida. Cota local contabilizada: **0,002758635 USD**; presupuesto
restante: **0,997241365 USD**. No se usó el remanente para nuevas llamadas.

## Imagen, autorización y alcance

```text
Imagen: trueca-candidate:chatbot-amd64-20261109-scope
Digest: trueca-candidate@sha256:8ca2925a0e9b7a5216891e811bd5d380ee6e83649d2a9e90fea7bb2246325477
Arquitectura: linux/amd64
UUID: 10d8c14e-9adb-4749-9f39-9027999814b5
Modelo: gpt-6-luna
Dataset: pilot-v1, exclusivamente E34 y E36
```

El operador autorizó expresamente esta candidata y UUID, ambos modos, máximo cuatro
llamadas y 1 USD, las tarifas y el proyecto previamente aprobados, sin reintentos ni
producción. Salida máxima: 400 tokens; timeout: 30 segundos. Se efectuó **una sola
invocación real** del evaluador presupuestado, sin `--resume` ni SDK directo.

La imagen del día 8 (`83e3c55f5134…`) no permitía seleccionar casos. La alternativa del
apartado 5 de la solicitud permitió añadir `--case-ids`, crear y validar esta imagen
nueva y solicitar su aprobación antes de usarla. La imagen anterior se conserva intacta.
La candidata nueva no se reconstruyó y no se modificó código durante la ejecución.
Python 3.12.15, Django 5.2.18 y OpenAI SDK 2.54.0; mismas dependencias del runtime validado.

Orden real: **E34/extractive → E36/extractive → E34/grounded → E36/grounded**.
No se ejecutaron otros casos ni llamadas de diagnóstico, traducción, reparación o
validación mediante otro modelo.

## Staging, fuentes y preflight repetido

Proyecto independiente: `trueca-chatbot-validation-micro20261109686e94`. PostgreSQL propio
con volumen persistente, sin tmpfs, secretos independientes y red interna. No se levantaron
servidor web ni proxy; el evaluador CLI fue el único proceso con clave y salida a Internet.
No se copiaron datos operativos ni se crearon usuarios, mensajes de chat, reservas,
publicaciones o movimientos de puntos.

Antes de gastar se comprobaron digest/AMD64, hashes de código, modelo/SDK, identidad de
base/usuario, migraciones completas, `fsync=on` y `synchronous_commit=on`. Se contrastaron
las doce FAQ sintéticas autorizadas con el manifiesto; no se resembra ni cambia el corpus.
Las fuentes negativas no publicadas/adversariales siguieron sujetas a los filtros existentes.
Los hashes de entrada de cada caso coincidieron entre modos.

Dataset/corpus sin cambios:
`179b77ff68dd44081ffaaedea651c54271815a736d37f25d81647bb36baf09a8`.

Se repitió el dry-run primero en preparación y después en el evaluador con la configuración
exacta autorizada. Ambos coincidieron: dos casos, cuatro pares, tarifas aprobadas, cota
válida y ningún bloqueo. El ledger estaba vacío, sin intentos previos del UUID. Los dry-runs
no crearon cliente API ni presupuesto. La clave se suministró exclusivamente por secreto
al evaluador; el identificador privado del proyecto se omite en informes públicos.

La candidata había pasado **868/868 tests PostgreSQL**, **824 correctos y 44 omitidos de
868 en SQLite**, y **40/40 checks de staging**, todos con proveedor/SDK simulado. Esas
pruebas no se presentan como validación de calidad del modelo real.

## Respuestas completas y validación por caso

### E34 — extractive

Pregunta: «Explícame cómo funciona TRUEC@ en tres frases».

**Sin respuesta visible aceptada.** Error: `invalid_response_style`. La salida generada
fue rechazada; no se repitió la llamada. El ledger conserva uso, coste y diagnóstico,
pero no el texto bruto invalidado. No puede determinarse su número exacto de frases ni
qué subcondición de estilo falló. No se afirma que el modelo devolviera dos frases.

Resultado de la corrección de tres frases: **no superado**.

### E36 — extractive

Pregunta: «Explain how to publish an object».

> Log in and open the publishing option. Fill in the object's details and add photographs. Submit the proposal to the team for review.

Respuesta visible íntegramente en inglés; no hay párrafo explicativo español, afirmación
de traducción oficial ni regla adicional observada. Conserva los tres pasos de la fuente:
iniciar sesión/abrir publicación, completar datos/añadir fotos y enviar para revisión.
No promete aceptación. La referencia mantiene título y evidencia originales en español.

Resultado de idioma: **superado en este caso**.

### E34 — grounded

Pregunta: «Explícame cómo funciona TRUEC@ en tres frases».

Texto visible devuelto por Django:

> No dispongo de información pública suficiente para responder a esa pregunta.

Es una **abstención local tras un rechazo `invalid_grounding`**, no una explicación del
modelo aceptada ni una prueba de que la evidencia fuera realmente insuficiente. El campo
`error` del resultado está vacío porque la aplicación aplicó su fallback; el diagnóstico
persistente del intento identifica el rechazo. No hubo otro intento para repararlo.

La abstención contiene una frase y no satisface la petición. El texto bruto rechazado no
se conserva; no se puede reconstruir su estructura original ni la subcondición de
validación que falló. Resultado de tres frases: **no superado**.

### E36 — grounded configurado, extractive efectivo

Pregunta: «Explain how to publish an object».

> Log in and open the publishing option. Fill in the object details and add photographs. Submit the proposal to the team for review.

Idioma correcto y traducción fiel observada en la comparación técnica con la misma fuente.
La configuración grounded mantuvo su filtro conservador y usó **extractive efectivamente**.
Esto demuestra la presentación inglesa en esa ruta, no una reformulación inglesa en el
modo grounded efectivo.

Resultado de idioma: **superado en este caso**, con esa limitación de cobertura.

## Fuente de las respuestas inglesas y fidelidad

Ambas respuestas citan la FAQ sintética «¿Cómo publicar un objeto y añadir fotografías?»,
`faq:10`, URL relativa `/faq/#faq-question-10`. Su evidencia original íntegra es:

> Inicia sesión y abre la opción de publicación. Completa los datos del objeto y añade fotografías. Envía la propuesta al equipo para su revisión.

Hash: `ced3728180b1d7b6204317f30e8606212a97f4f1fec039e8bc7299535aa47f74`.
Se conserva la versión `knowledge-1` correspondiente y la revalidación pública. No se
observaron condiciones omitidas, fuentes inventadas, datos privados, claims bloqueados,
acciones administrativas ni instrucciones de FAQ ejecutadas en los textos aceptados.

La revisión anterior es **comparación técnica asistida** del texto con su evidencia. No
es una revisión humana independiente ni prueba semántica general. La revisión humana de
fidelidad queda pendiente. Los títulos/citas de fuentes no se contaron como frases de la
explicación. Las tres frases observadas en E36 no acreditan la instrucción específica de E34.

## Comparación y mediciones reales

| Caso | Configurado / efectivo | Resultado | Entrada / salida | Tiempo de ruta | Cota USD | Restante USD |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| E34 | extractive / extractive | Rechazo de estilo | 692 / 74 | 3.702 s | 0.000593890 | 0.999406110 |
| E36 | extractive / extractive | Inglés válido | 804 / 86 | 4.513 s | 0.000690030 | 0.998716080 |
| E34 | grounded / grounded | Abstención tras rechazo | 853 / 155 | 2.312 s | 0.000784685 | 0.997931395 |
| E36 | grounded / extractive | Inglés válido | 804 / 86 | 3.899 s | 0.000690030 | 0.997241365 |

Totales: **3.153 tokens de entrada y 401 de salida**; cero tokens cacheados o de escritura
de caché reportados. Cada petición quedó dentro de los 400 tokens de salida y 30 segundos.
Los tiempos incluyen la ruta del evaluador, no solo latencia de red. La aplicación AMD64
se ejecutó bajo emulación en Docker ARM64; no son mediciones nativas de la VPS.

La API completó las cuatro respuestas y no hubo errores de transporte/autenticación/rate
limit. **Solo dos respuestas generadas fueron aceptadas**. Hubo dos incidentes de
validación, uno de ellos convertido en abstención. `stop_reason=completed` describe el
cierre de la secuencia, no una aprobación de calidad.

## Contabilidad y límites

Se mantuvo la reserva por ventana completa de **1.050.000 tokens** de entrada. Perfil
aprobado, USD/millón: entrada 0,22; salida 0,825; componente adicional máximo de caché
0,55; coste fijo cero y margen del 10 %. Tarifas verificadas el 9 de noviembre en
[precios oficiales](https://developers.openai.com/api/docs/pricing),
[modelo](https://developers.openai.com/api/docs/models/gpt-6-luna) y
[caché](https://developers.openai.com/api/docs/guides/prompt-caching).

| Medida | Resultado |
| --- | ---: |
| Máximo autorizado | 1,00 USD / 4 llamadas |
| Admitidos / enviados / respuestas API completas | 4 / 4 / 4 |
| Respuestas generadas aceptadas | 2 |
| Reserva máxima por solicitud | 0,889713000 USD |
| Pico comprometido con reservas en curso | 0,891781605 USD |
| Cota final calculada desde usage | **0,002758635 USD** |
| Presupuesto restante | **0,997241365 USD** |
| Intentos liquidados | 4 |
| Reservas desconocidas / pendientes / duplicados | 0 / 0 / 0 |
| Reintentos / llamadas adicionales | 0 / 0 |

La suma de los cuatro cargos coincide exactamente con el ledger. Las respuestas rechazadas
también consumieron uso del proveedor y se contabilizaron. Esta es una **cota conservadora
local, no la factura definitiva de OpenAI**. No se consultó la facturación mediante otra API
ni se redujeron reservas para completar el piloto. El presupuesto público del chatbot y
la moderación no participaron en este ledger.

## Cierre seguro y preservación

- Resultados exportados y comparados exactamente con el snapshot PostgreSQL.
- Cuatro pares únicos, todos `settled`, en el orden autorizado; sin uso desconocido.
- Backup final verificado con `pg_restore --list`, conservando ambas tablas del ledger.
- Copia temporal de la clave eliminada, `live.env` deshabilitado y red de salida del
  evaluador retirada. No quedan procesos evaluadores ni puertos publicados.
- Se detuvo solo PostgreSQL de este micropiloto, con salida 0. Su volumen, fuentes,
  resultados y backups se conservan; no se ejecutó `down -v` sobre esta base.
- Los 29 contenedores previos al staging nuevo conservaron su configuración. TRUEC@,
  su PostgreSQL, moderación y el PostgreSQL del primer piloto conservaron además sus
  estados e inicios esperados. Los 228 archivos de negocio contrastados y los archivos
  privados del piloto del 7 de noviembre permanecen sin cambios.
- No se desplegó, reinició producción ni reconstruyó la candidata. No se modificaron
  modelo, código, tarifas o fuentes entre los modos. No se crearon nuevas autorizaciones.

Los archivos privados de autorización, recibos, reconciliación y backups están en
`.chatbot-micropilots/20261109-style/`, fuera del checkout/build context, con acceso privado.
El informe de preflight anterior se conserva allí para auditoría.

## Conclusión y recomendación

**Inglés:** evidencia positiva para E36 bajo extracción efectiva en ambas configuraciones,
con referencia original conservada. No valida toda consulta inglesa ni grounded efectivo.

**Tres frases:** no demostrado. E34/extractive se rechazó por estilo; E34/grounded se
rechazó por grounding y terminó en abstención. La abstención segura no se considera éxito.
La salida bruta descartada limita el diagnóstico: antes de atribuir el fallo al modelo o
al validador hay que revisar el contrato y la observabilidad en un trabajo offline separado.
No se alteraron condiciones ni se hicieron nuevas llamadas para forzar una aprobación.

**No recomendar la aprobación conjunta de las dos correcciones para producción todavía.**
Hace falta resolver E34, comprobarlo con pruebas simuladas y, si se desea otro ensayo real,
autorizarlo expresamente. La revisión humana de fidelidad sigue pendiente; cualquier
despliegue será otra tarea con autorización independiente.

Evidencias: [resultados completos JSON](openai_style_micropilot_20261109.json) y
[manifiesto de la candidata utilizada](candidate_image_manifest_20261109_scope.json).

## Solicitud operativa posterior — parada segura del preflight (2026-11-09)

La solicitud posterior volvió a autorizar expresamente el mismo digest y UUID anteriores,
condicionando el envío a un ledger sin intentos y a 1 USD disponible. Se contrastaron los
archivos duraderos de autorización, ejecución única, resumen y reconciliación existentes.
**Esas condiciones ya no se cumplen**:

- UUID `10d8c14e-9adb-4749-9f39-9027999814b5`: cuatro intentos previos, cuatro llamadas
  reales y cuatro respuestas de proveedor, todos liquidados; máximo autorizado: cuatro.
- Cota contabilizada: **0,002758635 USD**. Saldo: **0,997241365 USD**, no 1 USD.
- Autorización conservada: `completed_with_validation_failures_no_further_calls`.
- Ledger conciliado, sin reservas desconocidas. El margen monetario no restituye llamadas.

Se detuvo el preflight al detectar el bloqueo. No se reabrió PostgreSQL ni se invocó el
evaluador, tampoco con `--resume`. No se borraron intentos ni se reinició presupuesto.
Los demás controles operativos (imagen presente/arquitectura actual, vigencia de tarifas,
configuración activa y consulta directa de PostgreSQL) no se repitieron: no se presenta
un preflight completo como satisfactorio. La evidencia utilizada es la reconciliación
persistida del piloto cerrado, sin nuevas operaciones contra su base.

Además, el manifiesto del digest autorizado no contiene `chatbot/sentence_planning.py`,
y el hash de su `chatbot/answering.py` difiere del código corregido posteriormente. Esa
imagen congelada **no incorpora la corrección offline de E34**, documentada en
[el informe de corrección](e34_offline_fix_20261109.md). No se reconstruyó ni se montó
código nuevo para cambiar el significado de la imagen autorizada.

**Resultado de esta solicitud: cero llamadas adicionales, coste adicional cero y parada
segura por condiciones de autorización incumplidas.** Las cuatro respuestas, tokens,
latencias, referencias y costes del cuerpo de este informe siguen siendo los resultados
históricos; no representan una nueva ejecución ni una validación real de la corrección.

Duraciones de esta solicitud:

| Operación | Resultado / duración |
|---|---|
| Lecturas del estado persistido para el preflight | Aproximadamente 2 s de ejecución de comandos; bloqueo detectado |
| Arranque del evaluador | No ejecutado |
| Llamadas OpenAI | No ejecutadas |
| Nueva evaluación de respuestas | No ejecutada; se conservaron los resultados anteriores |
| Cierre de recursos | Sin recursos nuevos que cerrar; staging no reactivado |

Las duraciones anteriores no incluyen lectura de la solicitud ni redacción del informe.
Se conservó el JSON anterior y se añadió únicamente un registro de esta parada. No se
modificaron código, dependencias, Compose, fixtures, migraciones, ledgers, backups,
secretos, redes ni contenedores de staging o producción. No se ejecutaron tests ni builds.
La recomendación histórica de producción sigue sin aprobación conjunta. Cualquier futura
validación de la corrección requeriría una candidata que la contenga y un UUID nuevos,
con autorización específica; esta solicitud prohíbe prepararlos y no se prepararon.
