# Chatbot TRUEC@ — backend, interfaz y OpenAI

Estado actual — 07/11/2026: KnowledgeService está integrado en la conversación, con
respuestas locales y generación fundamentada mediante Responses API o proveedor simulado.
Consulta solo fuentes públicas autorizadas, sin tools, saldos ni reservas particulares.
Los detalles vigentes están en «Fase 2, Prompt 5 — Evaluación final y presupuesto»
y en la corrección de funcionamiento general al final de este documento. Las auditorías
fechadas anteriores se conservan como historial; no aprueban los conflictos pendientes.
La integración no modifica la IA de moderación ni sus presupuestos/flags.

## Archivos y arquitectura

- `models.py`, `migrations/0001_initial.py`: conversaciones, mensajes e historial de uso.
  `0002_chatmessage_sources.py` añade metadatos públicos de referencias con valor vacío.
- `services.py`: propiedad, historial PostgreSQL, reservas de cuota, idempotencia y finalización.
- `answering.py`, `conversation_context.py`, `grounding.py`: continuidad temática, selección
  de ruta, respuesta fundamentada y referencias verificadas.
- `providers.py`: contrato `AIProvider.respond(history, language=..., knowledge=...)`, SimulatedProvider y
  OpenAIProvider. El SDK se importa sin crear clientes ni enviar solicitudes al arrancar.
- `instructions.py`: identidad, idioma y conducta, sin hechos normativos duplicados.
- `generation.py`, `references.py`: selección de modalidad, controles de reformulación y
  presentación acotada de referencias persistentes, sin consultas privadas.
- `config.py`, `checks.py`, `safety.py`: límites, validación local y bloqueo de red en tests/CI,
  migraciones y comandos de despliegue. Reutiliza el detector de tests de assistant.safety;
  no llama a require_live_access ni cambia su política de moderación.
- `rate_limits.py`: frecuencia sobre intentos persistidos, usando el bloqueo del propietario.
- `management/commands/evaluate_chatbot_live.py`: piloto presupuestado, dry-run por defecto.
  `check_chatbot_openai` está retirado: no permite evaluar sin ledger.
- `templatetags/chatbot_tags.py`, `templates/chatbot/_widget.html`, `static/js/chatbot.js`,
  `static/css/chatbot.css` y catálogo `locale/en`: interfaz responsive y errores traducidos.
- `tests/test_openai_provider.py`, `test_openai_api.py`, `test_rate_limits.py`,
  `test_concurrency.py`, `test_widget.py` y `tests/js/test_chatbot.cjs`: validación sin red real.

## Variables de entorno

Se siguen las convenciones de os.getenv en tenetrueca/settings.py y el ejemplo app/.env.example.
En Docker Compose existente, las variables efectivas provienen del `.env` de la carpeta raíz;
app/.env.example es documentación, no se carga por sí mismo.

Configuración de producción:

```dotenv
# Reutilizar la clave ya configurada en el servidor; nunca incluirla en JS o Git.
OPENAI_API_KEY=<clave existente en el entorno del servidor>
CHATBOT_ENABLED=True
CHATBOT_PROVIDER=openai
CHATBOT_GENERATION_MODE=extractive
CHATBOT_OPENAI_MODEL=gpt-6-luna
CHATBOT_MAX_OUTPUT_TOKENS=400
CHATBOT_HISTORY_MESSAGES=10
CHATBOT_CONTEXT_TURNS=4
CHATBOT_KNOWLEDGE_MAX_FRAGMENTS=5
CHATBOT_KNOWLEDGE_FRAGMENT_CHARS=1200
CHATBOT_KNOWLEDGE_CONTEXT_CHARS=6000
CHATBOT_REQUEST_TIMEOUT=30
CHATBOT_REASONING_EFFORT=none
CHATBOT_ANONYMOUS_LIMIT=10
CHATBOT_MAX_INPUT_CHARS=2000
CHATBOT_RATE_LIMIT_REQUESTS=6
CHATBOT_RATE_LIMIT_WINDOW_SECONDS=60
CHATBOT_RETENTION_DAYS=30
```

| Variable | Valor predeterminado | Efecto |
| --- | --- | --- |
| CHATBOT_ENABLED | True | Desactiva endpoints y widget al poner False |
| CHATBOT_PROVIDER | mock | mock sin red; openai activa el proveedor real explícitamente |
| CHATBOT_GENERATION_MODE | extractive | grounded permite reformulación conservadora; extractive es rollback sin pérdida de fuentes |
| CHATBOT_OPENAI_MODEL | gpt-6-luna | Modelo utilizado exclusivamente por el chatbot |
| CHATBOT_MAX_OUTPUT_TOKENS | 400 | Máximo de salida, incluyendo razonamiento si se habilita |
| CHATBOT_HISTORY_MESSAGES | 10 | Cota de contexto reciente; las respuestas previas no se usan como evidencia |
| CHATBOT_CONTEXT_TURNS | 4 | Máximo de preguntas completadas recientes para orientación local, sujeto a HISTORY_MESSAGES−1 |
| CHATBOT_REQUEST_TIMEOUT | 30 | Plazo total de la llamada en segundos; debe ser menor de 120 |
| CHATBOT_REASONING_EFFORT | none | Compatible con gpt-6-luna; vacío omite el parámetro |
| CHATBOT_ANONYMOUS_LIMIT | 10 | Respuestas correctas por sesión anónima Django |
| CHATBOT_MAX_INPUT_CHARS | 2000 | Longitud máxima de la pregunta y recorte por mensaje enviado |
| CHATBOT_RATE_LIMIT_REQUESTS | 6 | Nuevos turnos por propietario dentro de la ventana móvil |
| CHATBOT_RATE_LIMIT_WINDOW_SECONDS | 60 | Ventana de frecuencia compartida entre workers |
| CHATBOT_RETENTION_DAYS | 30 | Antigüedad del historial elegible para limpieza explícita |

Todos los límites son positivos. `manage.py check` valida configuración sin acceder a OpenAI.
Si se selecciona openai sin clave, informa chatbot.W001; los turnos que necesiten ese
proveedor fallan de forma segura sin consumir cuota. Las rutas locales no necesitan clave.
Otros parámetros inválidos producen errores de configuración.
El valor predeterminado mock conserva el desarrollo local sin costes. No se ha activado
OpenAI en el entorno local como parte de esta entrega ni se ha ejecutado una llamada real.

OPENAI_MODEL, OPENAI_LIVE_ENABLED, OPENAI_EVALUATION_BACKEND, presupuestos de moderación y
sus workers permanecen independientes. No es necesario cambiarlos para el chatbot.
Se reutiliza la dependencia oficial existente `openai>=2,<3`, instalada en la imagen
`app/Dockerfile.openai` mediante app/requirements.txt. Usar ese Dockerfile/overlay existente
para el despliegue con SDK, sin introducir nuevos paquetes o servicios.

## Compatibilidad comprobada sin llamadas reales

El entorno local tiene openai 2.54.0 y Python 3.12. Se inspeccionaron las firmas y tipos
de Responses.create y se validaron fixtures con el modelo Response del SDK real.
Se usan model, instructions, input, text.format, max_output_tokens, store y reasoning.effort.
No se envían temperature, top_p, tools, metadata, IDs de usuario/sesión, previous_response_id
ni una conversación remota. No se solicita streaming ni background.

[La documentación oficial de GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)
confirma Responses y reasoning.effort=none. Se evita el razonamiento medium predeterminado
para favorecer respuestas cortas dentro de 400 tokens. Si se cambia de modelo, verificar
sus esfuerzos admitidos; CHATBOT_REASONING_EFFORT vacío delega la selección al modelo.
No se cambia de modelo ni se reintenta automáticamente ante incompatibilidad.

[Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
documenta instrucciones, entrada y límites de salida. store=False evita guardar la respuesta
para recuperación por API: no equivale a una garantía de retención cero de todos los datos
por parte del proveedor. El acceso efectivo de la cuenta al modelo solo puede confirmarse
mediante la prueba manual autorizada; no se ha inferido a partir de tener una clave.

## Instrucciones y contexto

Las instrucciones permanentes fijan identidad, tono, idioma, límites informativos y trato
de datos no confiables; no contienen importes ni plazos. Los hechos proceden exclusivamente
de KnowledgeService. Siguen bloqueados reservation.pickup_window, points.reservation_charge
y admission.small_appliance; no se declara oficial ninguna interpretación enfrentada.

El servicio lee preguntas completadas recientes del historial propio para resolver el tema
en Django. No lee las respuestas anteriores como normas. Al proveedor solo salen la pregunta
actual con redacción de campos personales comunes y bundle.context como datos user separados
de instructions. No se envían historial privado previo, perfiles, saldos, secretos ni IDs de
usuario/sesión. Las fuentes públicas sin columna de idioma conservan language=und; no se
inventan traducciones oficiales. El idioma sigue la traducción activa ES/EN de Django.

W002 se retiró: dependía de un hecho estático que dejó de existir en el prompt. E005 comprueba
que ningún claim bloqueado esté simultáneamente aprobado; los límites de recuperación y de
contexto se validan junto con los existentes. Ningún check modifica reglas ni consulta OpenAI.
Las pruebas locales no demuestran calidad o relevancia semántica de un modelo real.

## Errores, cuota y frecuencia

El cliente AsyncOpenAI se crea perezosamente dentro de `asyncio.timeout`, con endpoint
oficial explícito, timeout de transporte configurable y max_retries=0. El servicio sync
espera esa corrutina; no deja un worker de fondo tras el timeout.
[El SDK reintenta varios errores por defecto](https://developers.openai.com/api/reference/python),
por lo que se deshabilitan expresamente sus reintentos.

Se clasifican internamente timeout, autenticación, permisos, rate limit de OpenAI, conexión,
5xx, peticiones rechazadas, configuración incompleta, texto vacío y respuesta incompleta.
Una salida parcial con status=incomplete no se guarda como respuesta correcta. Los logs
propios contienen solo una clasificación segura y un UUID, sin contenido, clave ni cuerpo
de error del proveedor. No habilitar logs DEBUG del SDK/HTTP con datos de producción.

La API conserva su contrato: fallo de turno 502/response_failed, fallo de persistencia
503/service_unavailable, sin consumir cuota anónima. La interfaz ya muestra un error
recuperable y traducido. Si falla la escritura tras una respuesta válida, se revierte la
transacción y se intenta liberar la reserva; si la base está caída, el vencimiento la recupera.
Una llamada que falla o cuyo resultado se pierde puede generar coste en OpenAI aunque no
consuma cuota de TRUEC@: no se supone que el proveedor reembolse esas solicitudes.

La cuota anónima se reserva atómicamente bajo select_for_update, sin transacción durante
la llamada externa. Solo guardar pregunta+respuesta correctamente convierte la reserva en
consumo. Los duplicados por conversación+request_id recuperan el resultado guardado sin
otra llamada, incluso después de cerrar la conversación. Los UUID fallidos son terminales;
un nuevo intento deliberado necesita un UUID nuevo. Una respuesta tardía, posterior a
los 120 segundos de reserva, se descarta sin consumir cuota.

La frecuencia usa filas ChatMessage de rol user creadas dentro de la ventana, sin importar
si acabaron completed/failed/pending. Se comprueba dentro del mismo bloqueo del propietario,
antes de crear el mensaje/reserva. No usa LocMemCache, que no sería compartida entre
Gunicorn workers; no requiere Redis ni otra tabla. Los índices existentes por conversación
y fecha se reutilizan. El propietario es user_id para cuentas o la clave validada de la
sesión Django db/cached_db para anónimos; la cookie no contiene contadores.

Crear conversaciones también tiene un bucket de frecuencia independiente, con los mismos
límites/ventana, para evitar crecimiento de conversaciones vacías. Reset no restaura cuota ni frecuencia. Varias pestañas/sesiones del mismo usuario
comparten frecuencia. Los autenticados no tienen un máximo fijo de diez preguntas, pero
sí la frecuencia general. El rechazo devuelve 429/error=rate_limited y Retry-After;
no reserva ni consume cuota ni invoca el proveedor. La interfaz conserva el borrador y
muestra un aviso de espera, distinto del aviso de cuota anónima agotada.

Este control es por propietario, no es un presupuesto monetario global ni protección
por IP/dispositivo. Una nueva sesión anónima puede obtener otra cuota/frecuencia; no se
identifican personas que borren cookies. Los presupuestos de la IA de moderación no
contabilizan el chatbot. La retención configurable se describe en la auditoría siguiente. Límites globales
de gasto y RAG no forman parte de este cambio.

## API y mantenimiento

- GET /chatbot/api/session/: crea/recupera conversación activa e historial; establece CSRF.
- GET con conversation_id: recupera una conversación anterior del mismo propietario.
- POST /chatbot/api/messages/: JSON conversation_id, request_id UUID y content; CSRF obligatorio.
- POST /chatbot/api/reset/: nueva conversación, conserva consumo/frecuencia y reservas en curso.

La sesión debe inicializarse antes de enviar mensajes. Se mantienen los middlewares de
cuentas, CSRF y propiedad estricta. No se fusiona historial anónimo al iniciar sesión.
Las reservas interrumpidas se recuperan al consultar/enviar/reset o mediante:

```sh
python manage.py expire_chatbot_reservations
```

El timeout de fetch se ajusta al plazo del proveedor más 5 segundos, para no abortar
a los 20 segundos una respuesta del backend configurada para 30. El widget reutiliza
el layout base, logo, sprite y variables CSS. Es no modal de 380×520 px
en escritorio y modal a pantalla completa hasta 768 px, con visualViewport, safe areas,
16px en el campo móvil, foco restaurado y movimiento reducido. No guarda mensajes ni cuota
en Web Storage; sessionStorage guarda solo preferencias de apertura/etiqueta. El historial
se recupera desde el servidor y las respuestas se insertan como textContent. La etiqueta
cambia de "Asistente de prueba" a "Asistente virtual con IA" según el proveedor configurado.

## Pruebas automatizadas: siempre sin OpenAI real

```sh
python manage.py test chatbot --settings=tenetrueca.test_settings --noinput
python manage.py test --settings=tenetrueca.test_settings --noinput
python manage.py test chatbot --settings=tenetrueca.test_postgres_settings --noinput
node --test chatbot/tests/js/test_chatbot.cjs
```

Test settings seleccionan mock y separan la frecuencia de las pruebas funcionales de cuota.
Las pruebas específicas simulan AsyncOpenAI/responses.create con AsyncMock y fixtures
Response del SDK, y prueban errores, deadline real de cancelación local, idiomas, parámetros,
historial, persistencia e idempotencia. La guardia global del SDK de assistant.safety sigue
activa incluso cuando el preflight local se sustituye en tests específicos de proveedor.
Las pruebas de PostgreSQL usan una base aislada para verificar concurrencia, nunca la base
local de desarrollo. No se invoca OpenAI desde migraciones, startup, collectstatic o checks.

Para recompilar traducciones (el .mo compilado se versiona, sin exigir gettext en Docker):

```sh
msgfmt --check --check-format -o chatbot/locale/en/LC_MESSAGES/django.mo chatbot/locale/en/LC_MESSAGES/django.po
```

## Evaluación manual real — procedimiento sustituido en Prompt 5

El comando antiguo `check_chatbot_openai` está retirado y rechaza llamadas. El procedimiento
vigente es `evaluate_chatbot_live`, con presupuesto persistente y dry-run por defecto.
Véanse [validación final, sección 21](../docs/chatbot/phase2_final_validation.md#21-procedimiento-futuro-diez-preguntas-hasta-veinte-llamadas)
y la configuración de Prompt 5 al final de este README. Hace falta una autorización futura
explícita, tarifas aprobadas, UUID fijo y staging; no se ejecutó ninguna llamada real.

Para habilitar el widget real en producción, configurar explícitamente CHATBOT_PROVIDER=openai
en su entorno y recrear el servicio web con el despliegue habitual. No copiar claves a
comandos, navegador o archivos versionados. Conservar mock en entornos donde no se desean
llamadas facturables. CHATBOT_ENABLED=False deshabilita la función completa.


## Auditoría previa al despliegue — 29/10/2026 (hora de México)

Actualización de reglas — 30/10/2026: instrucciones y W002 alineados a 72 horas y cargo
de 50 puntos al completar la recogida, sin modificar reservas ni puntos. Verificación
posterior: `python manage.py test --settings=tenetrueca.test_settings --noinput`, en
contenedor efímero sin red, código de solo lectura y SQLite en memoria: 648 pruebas,
617 correctas y 31 omitidas por las condiciones del entorno SQLite; sin errores de
system checks. Incluye instrucciones enviadas al SDK simulado en español/inglés y W002
ausente con 72 horas/presente con 168 horas, sin cambiar el setting. Sin llamadas reales
a OpenAI, migraciones nuevas, despliegue ni modificaciones de datos existentes. Los
resultados PostgreSQL y de navegador detallados abajo corresponden a la auditoría original.

### 1. Dictamen y alcance

Código y pruebas aprobados para validación en staging con proveedor mock. La aceptación de
producción queda condicionada al proxy/HTTPS/estáticos, comprobación física de Safari y
revisión del aviso de privacidad del chat. La discrepancia de reglas se resolvió el
30/10/2026 ajustando solo el chatbot a las reglas actuales confirmadas. No se ha desplegado, recargado ni recreado la
aplicación local existente durante esta auditoría. No se alteraron sus datos ni se ejecutó
OpenAI real. Todas las escrituras y borrados de prueba fueron en bases efímeras/aisladas.

Se revisaron backend, interfaz, autenticación, sesiones, SDK, modelos, migraciones, Docker,
configuración, validaciones, conservación y documentación. La separación es adecuada:
vistas JSON, servicios de propiedad/cuota, proveedor, instrucciones estáticas, configuración,
frecuencia y retención. Se reutilizan auth/sessions, transacciones, iconos, variables CSS,
idioma y mecanismo de comandos de gestión. No se añadieron frameworks, tablas o servicios
permanentes. Los tests de regresión del resto de aplicaciones pasan; assistant/moderation
no tienen cambios. No hay consultas a FAQ, RAG ni nuevas acciones de negocio.

### 2. Hallazgos corregidos

| Prioridad | Hallazgo | Corrección y evidencia |
| --- | --- | --- |
| P1 resuelto 30/10/2026 | El contexto estático del chatbot difería del plazo y del momento de cargo vigentes. | Instrucciones alineadas a 72 horas y cargo de 50 puntos al completar la recogida, por confirmación del responsable. W002 conserva la detección de futuras discrepancias de plazo. Sin cambios en reservas ni puntos. |
| P1 | El DOM del chat podía permanecer en BFCache al salir de una página y reaparecer tras logout/cambio de sesión. | pagehide limpia mensajes, borrador y estado privado; aborta fetch y descarta respuestas de un ciclo de página anterior. Un fallo al refrescar sesión también descarta el historial. Tests JS y login/logout/back en navegador aislado. |
| P2 | NUL y Unicode no codificable podían llegar a PostgreSQL y producir 500. JSON profundamente anidado también podía provocar RecursionError. | Validación UTF-8/NUL centralizada antes de persistir/llamar al proveedor; límite de cuerpo antes de leerlo y errores JSON 400 controlados. |
| P2 | El rechazo de un POST podía revertir la recuperación de una reserva vencida dentro de la misma transacción. | Los rechazos esperados de reserva se propagan después de confirmar la liberación; errores de BD siguen revirtiendo la operación. |
| P2 | Reset podía crear conversaciones vacías sin límite de frecuencia. | Bucket de creación independiente por propietario bajo el mismo bloqueo; 429 con Retry-After; no se cierra la conversación vigente si se rechaza. |
| P2 | El Dockerfile raíz instala requirements.txt raíz, que carecía de OpenAI y Pydantic ya requeridos por las apps. | Se añadieron las mismas dos dependencias existentes; paridad exacta entre manifiestos, pip check correcto en la imagen instalada. No se hizo un build fresco con descarga de paquetes. |
| Mantenimiento | Historial indefinido y ausencia de limpieza programable. | CHATBOT_RETENTION_DAYS=30, limpieza por lotes con vista previa y --execute explícito; no se engancha a startup ni migraciones. |
| Distribución | JS corregido podía permanecer cacheado con la misma versión de URL. | Versión de chatbot.js actualizada a 20261029-audit, sin cambios visuales amplios. |

El requirements.txt raíz está fuera del repositorio Git situado en app/: incluir ese archivo
al empaquetar el contexto Docker raíz, o usar el overlay existente que instala app/requirements.txt.
No confundir su ausencia en git diff de app/ con ausencia de cambio en el workspace.

### 3. Seguridad y funcionalidad comprobadas

- CSRF sigue obligatorio en POST para anónimos y autenticados; sin csrf_exempt. Pruebas con
  enforce_csrf_checks y rechazo de Origin ajeno. La cookie actual/token oculto se reutilizan.
- XSS: contenido insertado con textContent; JSON de configuración usa json_script. En navegador,
  `<b>QA texto literal</b>` aparece como texto y no crea elementos. No se interpreta Markdown/HTML.
- Historial accesible solo por propietario, UUID no basta para autorizar. Se rechazan intentos
  de elegir role, usuario, sesión, cuota o instrucciones mediante el JSON del cliente.
- Sesiones db/cached_db validadas por Django; cookies desconocidas/expiradas no seleccionan
  historial ajeno. Login rota la clave y logout elimina la sesión; no se adopta historial anónimo.
- Quota: exactamente diez respuestas correctas en total por sesión anónima, 11ª bloqueada;
  fallos no consumen, reset/navegación/pestañas conservan consumo. Los autenticados no tienen
  ese máximo fijo, pero sí frecuencia. Son dos límites independientes: seis intentos/minuto
  no equivale a seis preguntas totales. Tests de cuota usan una frecuencia mayor para aislarlos.
- Propietario bloqueado con select_for_update, claves únicas y restricciones de BD; proveedor
  fuera de transacciones incluso con ATOMIC_REQUESTS. UUID duplicado no vuelve a llamar al SDK.
- Timeouts, autenticación, rate limit upstream, respuestas vacías/incompletas y fallo de escritura
  dejan un estado recuperable, sin consumir cuota. Los logs propios solo incluyen motivo seguro
  y UUID; pruebas con marcadores secretos verifican ausencia de clave/pregunta en logs y HTML.
- Prompt injection: instrucciones privilegiadas solo en backend y roles de entrada restringidos;
  el proveedor no recibe clave, perfiles o IDs de sesión ni dispone de tools. Un usuario no
  puede concederse acceso/acciones. Esto no demuestra que un modelo real nunca inventará texto
  o revelará parte de un prompt no secreto: esa conducta no se ha evaluado con llamadas reales.
- Español/inglés comprobados con traducción activa de Django. Apertura, Escape, foco, modalidad,
  movimiento reducido, errores y composición IME están cubiertos en pruebas JS.
- Navegador aislado: escritorio y móvil, 320px sin overflow dentro del chat, altura 420px con
  header y composer visibles, restauración al navegar, contador oculto al autenticar y ausencia
  de historial privado al volver atrás tras logout. El teclado físico de iPhone no fue probado.

### 4. Pruebas y resultados

| Comando/verificación | Resultado final |
| --- | --- |
| python manage.py test --settings=tenetrueca.test_settings --noinput | 647 detectadas: 616 correctas, 31 omitidas por las condiciones del entorno SQLite |
| python manage.py test chatbot --settings=tenetrueca.test_postgres_settings --noinput | 113 correctas, sin omisiones |
| node --test chatbot/tests/js/test_chatbot.cjs | 23 correctas |
| makemigrations chatbot --check --dry-run; migrate --check | Sin migraciones nuevas; esquema aplicado en BD efímera |
| manage.py check con CHATBOT_ENABLED=False, proveedor openai, clave/modelo vacíos, settings reales y --network none | Arranque correcto, sin llamadas |
| docker compose config --quiet, base y overlay OpenAI | Correctos; configuración/secrets no volcados |
| pip check; paridad de requirements raíz/app | Correctos; sin dependencias nuevas respecto a las ya usadas por el proyecto |
| collectstatic con STATIC_ROOT temporal | CSS, JS, logo y sprite recogidos correctamente |
| GET estáticos en servidor efímero DEBUG=True | Las cuatro rutas devuelven 200 |
| GET /static/js/chatbot.js con DEBUG=False, Django sin proxy | 404 esperado: el proxy debe servir STATIC_ROOT |
| msgfmt --check --check-format; node --check; git diff --check | Correctos |

Carga/concurrencia en PostgreSQL: 24 duplicados simultáneos producen una sola llamada al
proveedor; 24 propietarios independientes mantienen historias/cuotas separadas; 24 peticiones
por el último slot solo consumen uno; 12 duplicados durante una respuesta lenta comparten
una reserva; 12 proveedores fallidos independientes liberan todas las reservas; tres limpiezas
concurrentes no recrean ni duplican registros. El proveedor es Mock/AsyncMock o simulador local.
Son pruebas funcionales de concurrencia, no un benchmark de capacidad/SLA de producción.

Las primeras pruebas PostgreSQL corrieron en una red interna Docker. La revisión visual
usó otra red efímera con puerto IPv4 127.0.0.1:18080; en todos los contenedores de auditoría
el proveedor fue mock, la clave estuvo vacía y CI=true bloqueó el SDK. El código fue montado
read-only, PostgreSQL usó tmpfs, y collectstatic escribió solo en /tmp del contenedor.
El entorno temporal y la cuenta sintética se retiraron al finalizar; la BD local no se usó.

### 5. Política de conservación y privacidad

CHATBOT_RETENTION_DAYS, inicialmente 30, controla la antigüedad elegible del historial.
El comando NO se ejecuta al desplegar ni desde migraciones. Vista previa predeterminada:

```sh
python manage.py cleanup_chatbot_history
```

La eliminación requiere una decisión explícita del operador:

```sh
python manage.py cleanup_chatbot_history --execute --batch-size 500
```

Se eliminan turnos antiguos por pares user/assistant; si un par cruza la fecha de corte,
se conserva hasta que ambos mensajes sean antiguos. Las conversaciones con solicitud
pendiente viva se aplazan. La ejecución recupera reservas vencidas bajo el bloqueo del
propietario y luego limpia en lotes de hasta 500 turnos. Las conversaciones vacías antiguas
pueden eliminarse, incluidas las activas sin actividad; se creará otra cuando se vuelva al chat.
La vista previa es solo lectura y puede ser conservadora respecto a reservas que la ejecución
expire o datos que cambien entre ambas invocaciones. skipped_owners requiere revisión si
persiste: evita recrear contadores cuando el propietario desapareció o hay datos huérfanos.

ChatSessionUsage se conserva mientras exista su fila Django Session, incluso si está
pendiente de clearsessions: eliminar contenido NO restaura cuota ni permite reproducir una
cookie para reiniciarla. Los contadores sin sesión ni conversación, antiguos y sin pendientes,
pueden eliminarse. Los usuarios, sesiones de Django y otras apps nunca son borrados por este
comando. Los IDs de sesión en BD son confidenciales; restringir acceso y proteger backups.
No se almacenan IP, user-agent, perfiles completos, tokens OpenAI ni respuestas SDK completas.

La deduplicación de un UUID se conserva mientras se retenga su turno. No hay garantía de
idempotencia indefinida después de eliminar el historial. La conservación efectiva depende
de ejecutar el comando: con ejecución diaria, habrá margen hasta la siguiente pasada y
aplazamiento de solicitudes pendientes. Los backups tienen su política independiente y
no quedan purgados por este comando. No se puede deshacer la limpieza con un rollback de código.

Programación preparada en `chatbot/cron.example`, siguiendo el mecanismo de comandos/cron
ya documentado para reservas. Revisar preview y backup antes de instalarlo en el cron del
host/PaaS. No se instaló ningún job ni automatización. El ejemplo ejecuta limpieza diaria
y recuperación de reservas cada cinco minutos, sin servicios o dependencias adicionales.

### 6. Riesgos y condiciones pendientes

| Riesgo/condición | Tratamiento antes de producción |
| --- | --- |
| Mantenimiento del conocimiento estático | Alineado el 30/10/2026 a 72 horas y cargo de 50 puntos al completar la recogida. Revisar instrucciones cuando cambie la política: W002 detecta discrepancias del plazo configurado, pero no detecta cambios del importe o del momento del cargo. No cambia ni bloquea por sí solo las operaciones. |
| Servir estáticos con DEBUG=False | Verificar el proxy real y volumen/ruta STATIC_ROOT: Django/Gunicorn solos no los sirven. Comprobar 200/MIME de CSS, JS, logo y sprite en el dominio público. |
| TLS y configuración real del proxy | DEBUG=False, SECRET_KEY fuerte, ALLOWED_HOSTS concreto, cookies Secure/HttpOnly/SameSite y CSRF correctos bajo HTTPS. Ejecutar check --deploy y revisar cada advertencia; no confiar cabeceras de cualquier origen. No se auditó un servidor de producción real. |
| Safari iPhone | Verificar teclado, orientación y safe areas en dispositivo físico; visualViewport/redimensionamiento no lo sustituyen. |
| Aviso de privacidad | La página actual es genérica y no describe específicamente el chat/OpenAI/30 días. Someter esos textos y condiciones del proveedor al responsable antes de activar tráfico real; no se modificó contenido legal. |
| Abuso distribuido y presupuesto global | Control por propietario; borrar cookies crea otra sesión. No existe límite global monetario ni por IP en chatbot. Revisar controles del proxy/proyecto OpenAI según tráfico y coste aceptables. |
| Volumen de historial | El contexto del modelo está acotado; la API aún devuelve el historial completo retenido. Evaluar coste de GET/serialización y paginación si sube el volumen, fuera de esta corrección mínima. |
| Modelo real y prompt injection semántica | Cuenta/modelo y calidad requieren prueba explícita con coste autorizado. Las instrucciones reducen riesgo, no sustituyen autorización ni garantizan veracidad. No hay tools ni consultas privadas. |
| Build reproducible | Se validó la imagen instalada y manifiestos; falta build fresco de la imagen de release en CI/staging y fijar su digest. No se descargaron paquetes para esta auditoría. |

### 7. Procedimiento de despliegue y rollback — preparado, NO ejecutado

1. Conservar release/imagen anterior, configuración privada, backup PostgreSQL verificable y
   archivos de migración locales históricos. El proyecto ignora varios baselines: no borrarlos,
   regenerarlos ni usar --fake automáticamente. No ejecutar limpieza durante el despliegue.
2. Preparar imagen con app/Dockerfile.openai y el overlay existente, o incluir el requirements.txt
   raíz corregido si se usa el Dockerfile base. Registrar digest/revisión y validar en staging.
3. Mantener CHATBOT_ENABLED=False o proveedor mock hasta cerrar aceptación. Conservar las
   variables de moderación/worker. Validar Compose sin imprimir secretos y ejecutar checks:

   ```sh
   docker compose -f docker-compose.yml -f app/compose.openai.yml config --quiet
   docker compose -f docker-compose.yml -f app/compose.openai.yml run --rm --no-deps \
     -e CI=true -e CHATBOT_PROVIDER=mock -e OPENAI_API_KEY= app python manage.py check
   ```

4. Revisar migrate --plan. Esta auditoría no añade migraciones; chatbot.0001_initial solo crea
   sus tablas/índices/restricciones. Aplicar pendientes autorizadas y comprobar migrate --check.
   No hay borrados de historial ni datos de negocio en la migración del chatbot.
5. Ejecutar collectstatic en el despliegue habitual (deploy/update.sh ya lo contempla), revisar
   permisos y configurar/verificar el proxy para STATIC_ROOT con DEBUG=False. Desplegar solo
   el servicio web previsto; no recrear el worker de moderación por este cambio.
6. Verificar salud, GET de sesión/estáticos y CSRF usando mock en staging; no enviar preguntas a
   OpenAI en los smoke tests automáticos. Confirmar cache bust de chatbot.js y comportamiento
   responsive. Activar openai únicamente tras autorización y cierre de las condiciones pendientes.
7. Instalar la programación de retención en una actuación posterior, después de revisar el dry run
   y la política de backups. No ejecutar --execute ni instalar cron como efecto de desplegar código.

Rollback: deshabilitar CHATBOT_ENABLED, detener el job de retención si se hubiera instalado,
restaurar imagen/revisión y estáticos anteriores y recrear solo web. Mantener las tablas del
chatbot: no ejecutar migrate chatbot zero, drop, flush ni restaurar toda la BD automáticamente.
El esquema de este cambio es compatible con el rollback de aplicación. Si ya se ejecutó limpieza,
recuperar datos solo mediante un backup revisado/restaurado primero en un entorno aislado;
revertir código no recupera registros borrados y restaurar una BD completa puede perder actividad
posterior de otras apps.

### 8. Checklist de aceptación

- [x] CSRF, XSS, propietario e input validados en servidor.
- [x] Diez respuestas anónimas, fallo sin consumo, reset/pestañas/navegación sin restauración.
- [x] Autenticados sin límite fijo y aislados de otros propietarios.
- [x] Reservas, frecuencia, duplicados, latencia y fallos probados en PostgreSQL.
- [x] Login/logout/BFCache y limpieza de estado privado comprobados.
- [x] Español/inglés, navegación por teclado, foco, modalidad y errores cubiertos.
- [x] Retención 30 días, dry run predeterminado y --execute con pruebas aisladas.
- [x] Sin llamadas reales a OpenAI, despliegue real, cron instalado o cambios de datos reales.
- [x] Migraciones actuales seguras, Compose válido, dependencias consistentes y collectstatic correcto.
- [x] Plazo de 72 horas y cargo de 50 puntos al completar la recogida reconciliados entre política aprobada, backend y texto del asistente (30/10/2026).
- [ ] Proxy de producción/TLS/CSRF/estáticos con DEBUG=False verificados en el dominio real.
- [ ] Safari físico con teclado y safe areas verificado.
- [ ] Aviso de privacidad del chat y condiciones de tratamiento revisados por el responsable.
- [ ] Imagen de release construida en CI/staging y backup/rollback verificados operativamente.
- [ ] Cualquier prueba real de OpenAI o despliegue cuenta con autorización separada.

## Fase 2, Prompt 2 — KnowledgeService local (30/10/2026)

Registro histórico de esa entrega. La integración y los cambios vigentes se describen en
la sección del Prompt 3, al final; el estado «sin conexión» de este apartado ya fue superado.

Motor implementado y probado, **todavía sin conexión con endpoints, conversaciones o
OpenAI**. La auditoría de fuentes está en `docs/chatbot/phase2_knowledge_audit.md`; sus
propuestas no constituyen aprobación de todas las reglas. La autorización del Prompt 2
prevalece para este corpus: plazo y cargo de reservas y excepción de pequeños
electrodomésticos siguen bloqueados. No se declara oficial ninguna de sus interpretaciones.
El chat de Fase 1 conserva sus instrucciones anteriores porque este paso prohíbe modificarlas;
no confundir su comportamiento actual con el corpus autorizado de KnowledgeService.

### Archivos y responsabilidades

Se crearon los siguientes archivos; no se modificaron modelos ni se generaron migraciones:

```text
chatbot/knowledge/__init__.py             exportación del servicio
chatbot/knowledge/contracts.py            contratos inmutables tipados
chatbot/knowledge/registry.py             fuentes/campos/claims autorizados y conflictos
chatbot/knowledge/normalization.py        consultas literales y equivalencias ES/EN
chatbot/knowledge/conflicts.py            exclusión conservadora de conflictos/instrucciones
chatbot/knowledge/context.py              selección, vigencia y contexto JSON acotado
chatbot/knowledge/service.py              orquestación de solo lectura
chatbot/knowledge/sources/__init__.py
chatbot/knowledge/sources/base.py         fragmento, procedencia y huella de contenido
chatbot/knowledge/sources/faq.py          FAQ publicadas, PostgreSQL FTS/fallback
chatbot/knowledge/sources/categories.py   nombres, jerarquía y slugs públicos
chatbot/knowledge/sources/centers.py      proyección pública existente del mapa
chatbot/knowledge/sources/rules.py        cuatro hechos expresamente autorizados
chatbot/tests/test_knowledge.py           37 pruebas nuevas
```

Modificados en esta entrega: `tenetrueca/settings.py`, `.env.example` (tres límites nuevos)
y este README. No cambian frontend, endpoints, proveedores, instrucciones, modelos, Docker,
dependencias, reglas de moderación, reservas ni puntos. El paquete no importa proveedores IA.

### Contrato y uso desde Python

```python
from chatbot.knowledge import KnowledgeService

bundle = KnowledgeService().retrieve("¿Publicar un objeto me da puntos?", language="es")
bundle.outcome       # grounded / no_evidence / conflict
bundle.fragments     # tuple[KnowledgeFragment, ...], solo unidades completas seleccionadas
bundle.rules         # tuple[RuleFact, ...], solo hechos con fragmentos incluidos
bundle.conflict_ids  # IDs canónicos; no versiones enfrentadas ni datos privados
bundle.context       # JSON de evidencia pública, sin instructions ni historial
bundle.exclusions    # códigos seguros: unsafe_content, fragment_too_large, etc.
```

`retrieve(question, *, language=None)` no acepta usuario, request, sesión, modelos, SQL,
URLs ni una lista de fuentes seleccionadas por el cliente. El idioma por defecto procede
de Django; ES/EN regional se normaliza. Una consulta vacía/inválida, NUL, Unicode no
codificable o superior a `CHATBOT_MAX_INPUT_CHARS` produce ValueError antes de consultar.
Se usan hasta 24 términos normalizados y hasta 24 términos literales con tildes conservadas.

Contratos frozen: SourceRef (tipo/ID/procedencia/URL/versión), KnowledgeFragment (texto,
idioma, aprobación, autoridad, relevancia y método, fechas, claims/huella), RuleFact
(valor/unidad/evento/ámbito/condiciones) y KnowledgeBundle. `internal_locator` es metadato
backend; **no serializar el bundle entero con asdict** para un modelo o una API.
`bundle.context` usa una proyección explícita que lo excluye. Los IDs de FAQ y centro
referencian registros ya identificados públicamente por las vistas; no se incluyen IDs
de usuarios, expedientes, reservas ni ubicaciones internas. Las reglas no tienen URL
pública inventada: public_url=None.

Las fuentes editoriales no tienen columna de idioma: language=und. Cambiar el idioma
de consulta no traduce el texto ni acredita una traducción oficial. Las cuatro reglas
autorizadas se redactan en español (language=es), con hechos estructurados independientes
de su futura verbalización. No se implementa selección de idioma por contenido del mensaje.

### Fuentes y reglas autorizadas

| Fuente | Consulta/proyección |
| --- | --- |
| FAQ | `FAQ.objects.filter(is_published=True)` con id/question/answer/position/timestamps; sin AuditEvent |
| Categorías | `public_category_ids()` más id/name/slug/parent_id/sort_order/updated_at; sin description, tags u objetos |
| Centros | `public_centers()` + `center_map_data`, cargando solo columnas permitidas; sin email ni services |
| Reglas | Registro explícito en sources/rules.py; consulta de constantes, nunca movimientos o estados |

Los centros cerrados pero publicados siguen visibles con su estado, tal como en el mapa;
estado operativo no significa abierto ahora. Horario vacío permanece vacío. URLs de
FAQ/catálogo/portada se generan con reverse y parámetros codificados; el enlace a Google
Maps usa el constructor existente. No se aceptan URLs del usuario como fuente de búsqueda
ni se siguen enlaces encontrados en textos. Nombres/jerarquía de categorías no garantizan
admisión o disponibilidad de artículos.

| Claim autorizado | Hecho y condición |
| --- | --- |
| points.registration | +100 al completar el registro público correspondiente y activar cuenta |
| points.accepted_delivery | +100 por entrega físicamente aceptada por personal autorizado |
| points.publication | Publicar no proporciona por sí mismo los puntos de entrega |
| moderation.human_decision | Evaluación IA informativa, sin sustituir decisión humana |

Las dos cantidades se contrastan con REGISTRATION_POINTS y ACCEPTED_OBJECT_POINTS del
servicio real. Si cambian, se omite la regla y se señala `approved_rule_drift:<claim>`;
el nuevo importe no se aprueba automáticamente. No se consulta saldo ni PointMovement,
ni se invoca award_*, charge_*, recepción, reserva o decisión administrativa.

### Conflictos y contenido sospechoso

| Claim bloqueado | Referencia de la auditoría |
| --- | --- |
| reservation.pickup_window | D1: plazo de recogida |
| points.reservation_charge | D3: cargo de reserva y su momento |
| admission.small_appliance | D6: excepción de pequeños electrodomésticos |

Una pregunta sobre temas bloqueados devuelve outcome=conflict y contexto vacío sin
consultar BD. Una FAQ/categoría/nota de centro candidata que trate esos temas se omite
**entera**, registrando el conflicto; el resto de evidencia segura puede conservarse,
pero el bundle sigue marcado conflict. No interpretar ese estado como permiso de generar
una respuesta normativa automáticamente. Los identificadores de conflicto están en el
bundle, sin exponer al contexto las afirmaciones enfrentadas.

La exclusión es conservadora: menciones de reserva/recogida/plazos/cargos, expresiones
conocidas de las dos versiones y aparatos contemplados bloquean unidades completas.
Puede descartar texto útil no contradictorio dentro de ese mismo tema. No se importan
templates, FunctionalRule genérica, seeds, instrucciones de moderación ni datos de prueba.

Se rechazan patrones evidentes de cambio de instrucciones, roles suplantados, acceso a
tablas/secretos y marcado HTML activo. Es defensa adicional, no un detector semántico
exhaustivo de prompt injection ni un saneador de datos personales escritos en prosa por
un editor. La publicación editorial debe seguir revisándose. El servicio no ejecuta
texto recuperado, no tiene operaciones de escritura ni herramientas del modelo. En el
Prompt 3 toda evidencia deberá permanecer como datos de menor privilegio.

### Búsqueda, orden y frescura

PostgreSQL utiliza SearchVector con peso A en question y B en answer, SearchQuery plain
con términos literales/equivalencias controladas y SearchRank. Se consultan diccionarios
spanish/english sin asumir idioma editorial por fila. No se acepta tsquery raw, SQL ni
configuración de búsqueda arbitraria. Las tildes originales se conservan además de las
formas normalizadas para evitar perder coincidencias nativas. Referencia técnica:
[FTS de Django 5.2](https://docs.djangoproject.com/en/5.2/ref/contrib/postgres/search/).

PostgreSQL selecciona hasta 50 candidatos FAQ en BD. En SQLite, la búsqueda recorre
FAQ publicadas por chunks y mantiene 50 ganadoras en memoria, ponderando coincidencias
de question tres veces más que answer. También se buscan nombres/jerarquía de categorías
y campos públicos de centros; estos adaptadores mantienen como máximo 50 candidatos.
La relevancia lexical es una ponderación de términos coincidentes, no una probabilidad.
FTS añade rank/(1+rank) para conservar coincidencias por stemming; las puntuaciones de
los dos motores no tienen por qué ser idénticas. Hay un test nativo de stemming PostgreSQL.

Selección final determinista por relevancia, autoridad (1 mayor), última edición y source_id;
se excluyen unidades no aprobadas o fuera de vigencia conocida. No se inventan fechas
de vigencia si el modelo no las proporciona. updated_at significa edición, no aprobación.
Sin cache documental, tabla de corpus ni copia persistente: la siguiente recuperación
refleja edición/despublicación/borrado. Si falla una consulta se propaga DatabaseError,
sin devolver cache ni un bundle parcial como información oficial.

### Configuración y límites

```dotenv
CHATBOT_KNOWLEDGE_MAX_FRAGMENTS=5
CHATBOT_KNOWLEDGE_FRAGMENT_CHARS=1200
CHATBOT_KNOWLEDGE_CONTEXT_CHARS=6000
```

Enteros positivos; límites inválidos fallan antes de recuperar. Se comprueba el tamaño
del fragmento JSON serializado (incluidos título/metadatos/URLs), y del contexto total
incluida su envoltura. No se corta una FAQ o condición: se omite la unidad que no cabe.
Esto es más estricto que limitar solo el cuerpo a 1200. Un límite demasiado pequeño
puede devolver no_evidence. No se usa un contador aproximado como garantía de tokens.

### Pruebas locales y límites conocidos

Todas las pruebas usan fixtures sintéticos, clave vacía, CI=true y proveedor mock; no hay
llamadas reales. Código montado read-only y almacenamiento de pruebas temporal.

| Comando dentro del contenedor de pruebas | Resultado |
| --- | --- |
| `python manage.py test --settings=tenetrueca.test_settings --noinput` | 685 detectadas: 653 correctas, 32 omitidas; sin fallos |
| `python manage.py test chatbot --settings=tenetrueca.test_postgres_settings --noinput` | 151 correctas, sin omisiones |

La primera ejecución usa SQLite en memoria y contenedor `--network none`. La segunda
usa PostgreSQL 17 efímero, tmpfs, `OPENAI_TEST_DB_PORT=5432` y namespace de red compartido
solo con ese contenedor PostgreSQL, sin red exterior ni volúmenes existentes. No apuntar
test_postgres_settings a una base real. Las migraciones del test runner se aplican solo
a su BD de prueba; esta entrega no crea nuevas migraciones.

Las 37 pruebas nuevas cubren publicación/edición/despublicación/borrado, ancestros inactivos,
centros ocultos/cerrados, columnas privadas excluidas incluso del SELECT, reglas autorizadas
y bloqueadas, ES/EN/tildes, FTS real/fallback, relevancia, límites serializados, vigencia,
determinismo, contenido malicioso, consultas literales, enlaces y fallos de BD sin cache.
Los tests anteriores mantienen cuota, errores, idempotencia, seguridad y concurrencia.

Limitaciones: búsqueda lexical/FTS sin interpretación semántica general; equivalencias
curadas; recall acotado por 50 candidatos/fuente; unidades largas se omiten; sin traducción
oficial automática ni enriquecimiento de horarios/festivos; sin índice FTS nuevo (evaluar
rendimiento con corpus mayor antes de proponer GIN). Los detectores conservadores pueden
tener falsos positivos y no prueban comprensión de toda contradicción o ataque paraphraseado.
La conexión Django mantiene sus permisos existentes: el servicio aplica solo lectura y
allowlists en código; no se ha creado un rol SQL separado ni una nueva conexión de BD.

### Puntos de integración para el Prompt 3, no implementados

Recuperar después de reclamar el turno y antes de generar, fuera del bloqueo/transacción;
conservar deduplicación/cuota/frecuencia y liberar la reserva ante fallo de recuperación.
Pasar bundle/contexto separado del historial, sin introducir roles nuevos en ChatTurn ni
concatenar evidencia a instructions. Revisar las instrucciones estáticas y las decisiones
de conflictos solo mediante autorización posterior. Validar source_ids/URLs en backend;
no serializar metadatos internos ni aceptar referencias inventadas por el modelo.
Nada de esta recuperación se activa al cargar el widget, migrar o desplegar.

## Fase 2, Prompt 3 — Conversación fundamentada (06/11/2026)

KnowledgeService está conectado al flujo de conversación. No se modifican endpoints,
modelos/migraciones, frontend, reglas de negocio, moderación, Docker ni dependencias.
Los importes y plazos en conflicto siguen sin aprobación para el corpus, con independencia
de la reconciliación descrita en la auditoría histórica. No hubo despliegue ni llamadas reales.

### Arquitectura y archivos de esta entrega

Nuevos: `answering.py` (ConversationAnswerer), `conversation_context.py`
(ConversationContextResolver/ResolvedQuestion), `grounding.py` (contratos, formato y validación),
`tests/fixtures.py` y `tests/test_grounded_conversations.py`.

Modificados:

- `services.py`, `providers.py`, `instructions.py`, `config.py`, `checks.py`.
- `knowledge/contracts.py`, `registry` permanece con las mismas aprobaciones; cambios en
  `knowledge/conflicts.py`, `context.py`, `normalization.py`, `service.py`,
  `sources/faq.py` y `sources/rules.py`.
- `management/commands/check_chatbot_openai.py`: en esta fase histórica exigía evidencia
  aprobada; retirado en Prompt 5 y sustituido por el evaluador con ledger.
- Tests existentes de API, auditoría, concurrencia, conocimiento, proveedor/SDK y frecuencia,
  adaptados al contrato fundamentado, manteniendo sus comprobaciones de seguridad/cuota.
- `tenetrueca/settings.py`, `.env.example` y este README.

Las únicas fuentes del conocimiento siguen siendo FAQ publicadas, taxonomía pública,
centros publicados mediante el servicio existente y los cuatro hechos aprobados. No se
consultan instancias de reservas, saldos, usuarios ni expedientes para recuperar información.

### Flujo real de una petición

1. La API valida JSON, longitud, UUID y propietario; conserva CSRF y el contrato HTTP.
2. `reserve_message` verifica estado, frecuencia, duplicados y cuota bajo el bloqueo del
   propietario. Solo el proceso que reclama el turno entra en la integración nueva.
3. Fuera de esa transacción, se leen únicamente preguntas **completadas** recientes de la
   misma conversación. No se toman respuestas previas del asistente como hechos o entidades.
4. El resolver interpreta tema/referente público y conserva la pregunta original en BD.
   Una referencia no inequívoca produce aclaración concreta, sin llamada al proveedor.
5. KnowledgeService recupera fuentes permitidas con consulta orientada por ese contexto.
   El orquestador restringe campos/tema/entidad y evita usar una regla de registro para
   responder condiciones o preguntas distintas que solo compartan la palabra «puntos».
6. Se elige regla local, dato estructurado de centro, aclaración, respuesta prudente,
   conflicto o una única generación fundamentada para las partes que la necesitan.
7. Antes de guardar una respuesta con referencias se vuelve a recuperar la evidencia
   seleccionada: misma fuente autorizada, contenido/huella y URL pública. No se usa cache.
8. `finish_message` persiste pregunta/respuesta y convierte la reserva en consumo en una
   transacción corta. Una petición repetida reproduce ese resultado, sin recuperación nueva.

La revalidación requiere otra lectura de fuentes; no es una llamada generativa adicional.
Una fuente editada/despublicada durante la generación invalida la respuesta antigua y da
una respuesta local prudente. Un error técnico de consulta/proveedor/formato/persistencia
mantiene el circuito de fallo/liberación y no consume cuota. Una respuesta válida de
aclaración, conflicto o falta de información **sí cuenta** como pregunta respondida.

Las pruebas TransactionTestCase comprueban que tanto recuperación como revalidación y
proveedor corren fuera de transacciones, también con ATOMIC_REQUESTS. Los duplicados
simultáneos solo provocan una recuperación inicial, una generación y una revalidación
por turno reclamado; los demás recuperan pending o el resultado persistido.

### Contexto conversacional y límite nuevo

```dotenv
CHATBOT_CONTEXT_TURNS=4
```

Limita las preguntas completadas usadas para continuidad, junto con la cota
`CHATBOT_HISTORY_MESSAGES−1`. No amplía fuentes ni permisos. Se reconocen temas acotados,
equivalencias ES/EN, preguntas de puntos, cancelación y campos públicos de centros.
Los referentes de centros se comparan con nombres **actualmente públicos**; dos centros,
centro oculto, referente desconocido o cambio de tema exigen nueva aclaración si hace falta.
Se puede atravesar una cadena «centro → su horario → su teléfono» dentro del límite.

La pregunta original se conserva sin reescribir. Solo se añade a la búsqueda el tema o
nombre público necesario; ni el historial ni una respuesta antigua conceden autoridad.
El resolver descarta contexto con instrucciones sospechosas o longitud histórica excesiva.
Un historial truncado no autoriza rescatar datos de otra conversación o de otra cuenta.
No hay reformulación con LLM, estado global de última entidad ni NLP externo.

### Conflictos por afirmación y preguntas mixtas

Se mantienen exactamente estos claims bloqueados:

- `reservation.pickup_window`.
- `points.reservation_charge`.
- `admission.small_appliance`.

La palabra «reserva» ya no bloquea todo el tema. FAQ públicas pueden explicar registro,
objeto propio o cancelación sin autorizar plazo/cargo. No se añaden esos requisitos al
registro de reglas estructuradas: hace falta evidencia publicada que los respalde.
Los siete días de un horario semanal público no se confunden con plazo de recogida.

El planificador separa hasta dos preguntas interrogativas explícitas, p.ej. «¿Necesito
registrarme para reservar y cuánto tiempo tengo para recoger?». Responde la parte publicada
y añade que no dispone de regla confirmada para el plazo, en el orden de las preguntas.
No manda versiones normativas enfrentadas al proveedor. Consultas complejas inseparables
no se concilian mediante heurísticas: se responde prudentemente o se pide aclaración.

Las FAQ con párrafos independientes pueden aportar una unidad completa segura y excluir
la unidad conflictiva. No se separan frases enlazadas ni unidades con excepciones o
condiciones dependientes reconocibles. No se cortan respuestas para cumplir el presupuesto.
Esta clasificación es conservadora y lexical, no un verificador semántico general.

### Respuestas deterministas y estados

`RuleFact.localized_content` contiene verbalizaciones ES/EN aprobadas generadas en la
fuente a partir de su valor y condición; el orquestador solo elige idioma y hecho vigente.
No hay importes ni reglas duplicados en instructions. Se resuelven localmente:

- Registro público y activación correctamente completados: valor del hecho aprobado.
- Entrega físicamente aceptada por personal autorizado: valor del hecho aprobado.
- Publicar por sí mismo no genera los puntos de entrega.
- La IA de moderación no sustituye decisión humana.

El reconocedor local usa formas de pregunta y vocabulario restringidos; condiciones
adicionales desconocidas impiden presentar el hecho como respuesta completa. Datos de
dirección/horario/teléfono de un centro identificado también se expresan localmente, con
sus valores públicos exactos. Horario vacío no se rellena ni se interpreta como abierto ahora.

| Estado | Tratamiento |
| --- | --- |
| grounded | Regla/dato local si suficiente; en los demás casos selección generativa de evidencia |
| no_evidence | Respuesta local que reconoce falta de información; nunca conocimiento general del modelo |
| conflict | Respuesta local sobre la afirmación sin regla confirmada; las partes seguras pueden conservarse |
| clarification | Pregunta local concreta sobre tema o centro sin referente inequívoco |

### Contrato del proveedor, evidencia y trazabilidad

`AIProvider.respond(history, *, language, knowledge=None)` recibe KnowledgeBundle aparte
del historial. El proveedor real exige evidencia; sin ella no crea cliente. Devuelve
GeneratedAnswer con EvidenceSelection(source_id, content). SimulatedProvider admite ese
contrato y mantiene su eco antiguo si se usa directamente sin knowledge. Adaptadores
locales/tests que devuelven str solo se aceptan si coincide exactamente con una unidad
completa inequívoca de la evidencia, sin permitir texto arbitrario no respaldado.

El SDK conserva modelo configurable, timeout total, max_retries=0, max_output_tokens y
store=False, sin tools, conversaciones remotas ni estado remoto. Se añade `text.format`
json_schema estricto. Solo se envían dos mensajes user: pregunta actual y bundle.context
con rótulos de datos no confiables. No se incluyen respuestas históricas, internal_locator,
diagnósticos, IDs de cuenta/sesión ni modelos Django completos. Se redactan email, DNI/NIE
y teléfonos reconocibles de la pregunta actual; ello no sustituye revisión de prosa libre.

Decisión de esta entrega: la generación es **extractiva controlada**, con hasta dos unidades
completas. El modelo elige evidencia para responder y devuelve su texto íntegro; el backend
rechaza IDs fuera del conjunto recuperado, texto distinto, condiciones recortadas o referencias
inventadas. Una selección vacía es falta de evidencia válida; un formato inválido es error
de proveedor, sin cuota. No se afirma validar semánticamente una paráfrasis libre.
Una FAQ sin traducción acreditada se muestra en su idioma original, con introducción ES/EN;
las reglas estructuradas sí tienen plantillas ES/EN revisadas. Esta limitación es deliberada.

AssistantReply prepara para el Prompt 4: text, references, route y outcome. Cada
ValidatedReference incluye source_id, URL pública del backend o None, huella y texto
efectivamente respaldado. Para FAQ se comprueba unidad literal; para reglas/centros se
comprueba la proyección estructurada autorizada. Se vuelve a leer la publicación antes
de aceptar referencias. Los cambios posteriores de una fuente no reescriben mensajes ya
entregados; la siguiente pregunta vuelve a recuperar datos actuales.

Por restricción de esta fase se persiste **solo text** en ChatMessage; las referencias son
contrato interno, no un campo nuevo del JSON público ni metadatos persistidos ocultamente
en el texto. Su almacenamiento y presentación histórica necesitan diseño autorizado en el
Prompt 4. No se implementan tarjetas, enlaces del widget ni una migración anticipada.

[Responses API](https://developers.openai.com/api/reference/python/resources/responses/methods/create)
documenta instructions, input y text.format; la
[ficha de GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) declara soporte
de Structured Outputs. Las firmas/tipos del SDK instalado se verifican en tests sin red;
el acceso efectivo de una cuenta y el comportamiento real siguen pendientes de prueba autorizada.

### Coste, seguridad y retención

Cero llamadas al proveedor para reglas suficientes, campos de centros, aclaraciones,
conflictos o ausencia de evidencia. Máximo una llamada por turno que necesita generación,
incluso si contiene dos preguntas. Nada de calls para buscar, reformular o verificar con IA.
Se conservan 5 fragmentos / 1200 caracteres por fragmento / 6000 totales, historial 10 y
salida 400 tokens; los fragmentos idénticos no se repiten aunque tengan distintos IDs.
El texto de evidencia aparece una sola vez, sin repetirlo en instructions o en el schema.

CSRF, propietario, límites de input, cuota anónima 10, frecuencia, idempotencia, lease y
finalización atómica permanecen en sus servicios existentes. Un administrador recibe el
mismo corpus público; el contexto no acepta privilegios. Las excepciones no exponen
preguntas, secretos o cuerpos SDK en logs. La retención configurable de 30 días no cambia.
No se creó cache documental, tabla, dependencia, servicio, herramienta ejecutable o job.

### Conversaciones cubiertas por pruebas locales

Estos intercambios se ejecutan con fixtures sintéticos y proveedores simulados; no son
capturas de producción ni pruebas de un modelo real. Las respuestas locales son exactas;
los ejemplos generativos muestran el contenido seleccionado por el mock.

**Puntos encadenados, sin proveedor:**

```text
Usuario: ¿Cuántos puntos gano al registrarme?
Asistente: Recibes 100 puntos cuando completas el registro público y activas tu cuenta.
Usuario: ¿Y por entregar un objeto?
Asistente: Recibes 100 puntos cuando el personal autorizado acepta físicamente el objeto entregado.
Usuario: ¿Los recibo al publicarlo?
Asistente: No. Publicar un objeto no genera puntos por sí mismo; se conceden cuando el personal autorizado acepta físicamente la entrega.
```

**Centro, horario y teléfono, sin proveedor; datos de prueba, no información real de Adeje:**

```text
Usuario: ¿Dónde está el punto limpio de Adeje?
Asistente: Punto Limpio de Adeje: Dirección pública Adeje
Usuario: ¿Cuál es su horario?
Asistente: Punto Limpio de Adeje: Lunes 09:00–14:00
Usuario: ¿Y tiene teléfono?
Asistente: Punto Limpio de Adeje: 900123456
```

**Cambio de tema, sin arrastrar el referente anterior:**

```text
Usuario: ¿Dónde está el punto limpio de Adeje?
Asistente: Punto Limpio de Adeje: Dirección pública Adeje
Usuario: ¿Cuántos puntos gano al registrarme?
Asistente: Recibes 100 puntos cuando completas el registro público y activas tu cuenta.
Usuario: ¿Cuál es su horario?
Asistente: ¿De qué punto limpio quieres consultar la información?
```

**Ambigua / sin contexto, sin proveedor:**

```text
Usuario: ¿Cuál es su horario?
Asistente: ¿De qué punto limpio quieres consultar la información?
```

**Mixta, mock de generación para la FAQ de autenticación y bloqueo local del plazo:**

```text
Usuario: ¿Necesito registrarme para reservar y cuánto tiempo tengo para recoger?
Asistente: Según la información pública, en su idioma original:
Para reservar necesitas registrarte e iniciar sesión.

No dispongo de una regla confirmada para esa afirmación. Consulta al personal de TRUEC@.
```

**Sin información, sin proveedor:**

```text
Usuario: ¿Hay astronautas en Marte?
Asistente: No dispongo de información pública suficiente para responder a esa pregunta.
```

También se ejecutan variantes EN para reglas, referencias elípticas y aclaraciones.
No se ejecutaron conversaciones manuales con coste ni pruebas contra datos reales.

### Evaluación E01–E29 y riesgos pendientes

La auditoría sigue siendo inventario de referencia, no aprobación de los plazos/importes
que aparecían en sus oráculos originales. La evaluación vigente separa estas dimensiones:

| Casos de referencia | Evidencia comprobada y alcance |
| --- | --- |
| E01 | FAQ de funcionamiento con reservas sin plazo/cargo: selección y referencia literal |
| E02–E04, E16 | Reglas/localización ES/EN y secuencia de puntos sin llamadas; recepción física y humano como condición |
| E05–E06, E09, E13, E22–E23 | Conflictos mantienen bloqueo; ninguna versión de plazo/cargo pasa como evidencia |
| E07–E08 | FAQ de registro/objeto propio/cancelación; no inferir devolución o penalización sin evidencia específica |
| E10 | No asumir tres reservas desde seed ni responder el número usando FAQ de autenticación |
| E11–E15 | Solo fuentes publicadas autorizadas/taxonomía; no incorporar reglas internas de admisión aún no aprobadas ni prometer aceptación |
| E17–E19, E24, E28 | Centro público, horario/teléfono, ocultación, ambigüedad, cambio de tema y dato vacío; sin disponibilidad en tiempo real |
| E20 | Sin consulta de datos particulares; historial anterior excluido del request remoto y pregunta actual redactada |
| E21 | Referente inequívoco o aclaración; truncado, vacío y cambios de tema no autorizan adivinar |
| E25–E26 | Instrucciones de usuario/corpus no amplían fuentes; patrones maliciosos excluidos; sin SQL/tools |
| E27 | Edición/despublicación entre turnos y durante generación; revalidación antes de responder |
| E29 | Ninguna regla de límite diario estructurada se añade por esta integración; solo FAQ pertinente autorizada podría respaldarla |

Esto no equivale a que se hayan probado todas las paráfrasis de esos casos. Los tests
evalúan por separado recuperación/filtros, continuidad, reglas locales, payload SDK,
validación de salida/referencias y consumo de cuota. El mock selecciona contenido conocido;
no demuestra que el modelo real seleccione siempre el documento correcto o suficiente.

Pendiente con autorización posterior: calidad/pertinencia del modelo, capacidad de abstenerse,
latencia/tokenización de unidades largas con 400 tokens, variaciones lingüísticas reales,
revisión editorial de FAQ y resolución funcional de conflictos. La detección lexical de
intenciones/inyección no es exhaustiva; se prefiere aclaración o pérdida de recall a inventar
un referente. La redacción de campos personales no detecta toda información sensible posible.
La proyección pública depende de la corrección editorial de los contenidos publicados.
No hay garantía de una instantánea que bloquee futuras ediciones de fuentes; se revalida
antes de entregar y se consulta de nuevo en cada pregunta, sin locks durante generación.

### Pruebas ejecutadas y preparación del Prompt 4

Verificación final completada el 06/11/2026 tras retomar la sesión interrumpida. Se repitieron
ambas suites completas porque los logs temporales anteriores no sobrevivieron al reinicio.
Las pruebas usan SQLite en memoria y PostgreSQL 17 tmpfs efímero, sin red exterior, código
montado de solo lectura, CI=true y clave vacía; SDK/mock y AsyncMock, sin llamadas reales.
No se modificaron datos locales existentes. El contenedor PostgreSQL temporal se retiró
al acabar; no se ejecutó un despliegue ni un reinicio explícito de los servicios de TRUEC@.

| Comando en el entorno aislado | Resultado final |
| --- | --- |
| `python manage.py test --settings=tenetrueca.test_settings --noinput` | 735 detectadas: 702 correctas, 33 omitidas por requisitos del entorno; 21,202 s, sin fallos |
| `python manage.py test --settings=tenetrueca.test_postgres_settings --noinput` | 735 correctas, sin omisiones; 33,004 s |
| `node --test chatbot/tests/js/test_chatbot.cjs` | 23 correctas, sin omisiones |
| AST de los 50 archivos Python del chatbot, enlaces documentales locales y `git diff --check` | Correctos |

Se añadieron 50 tests: 44 de conversaciones fundamentadas, cuatro del proveedor y dos de
transacciones/concurrencia. Los tests de Fase 1 se adaptaron con evidencia pública sintética
para seguir comprobando transporte, errores y cuota en la ruta que realmente llama al proveedor.
Las omisiones SQLite corresponden a pruebas que necesitan otro motor o condiciones específicas;
la suite completa PostgreSQL ejecutó todas las pruebas, incluidas las de concurrencia y FTS.

El contenedor SQLite se ejecutó con `--network none`; el contenedor de tests PostgreSQL
compartió únicamente el namespace del servidor temporal, también sin red exterior, usando
`OPENAI_TEST_DB_PORT=5432`. El código estuvo montado read-only y no se cargó el `.env` real.
No se ejecutó el comando manual con coste, limpieza sobre datos reales ni nuevas migraciones.

Para Prompt 4 queda preparado el contrato AssistantReply/ValidatedReference. Antes de
mostrar referencias históricas deberá aprobarse su almacenamiento, política de cambios,
serialización pública y presentación accesible. No se continúa con esa fase aquí.

## Fase 2, Prompt 4 — Respuestas naturales, fuentes oficiales y UX

Entrega del 06/11/2026. Esta sección sustituye los contratos y resultados del Prompt 3
que limitaban toda generación a copia literal y no persistían referencias. Las secciones
anteriores fechadas describen su entrega histórica. No hay despliegue, evaluación real con
OpenAI ni cambios en `reservations`, `points`, `moderation` o `assistant`.

### Arquitectura y contratos comprobados

Se conserva `services` para propiedad, reservas de cuota, frecuencia e idempotencia;
`ConversationAnswerer` elige la ruta y recupera/revalida fuentes fuera de la transacción de
reserva. `generation.py` añade selección conservadora de modalidad y controles de salida;
`references.py` guarda procedencia pública y la revalida por lotes al recuperar mensajes.
El único proveedor OpenAI sigue usando Responses API, sin tools, historial remoto ni retries.

Contratos internos actuales, compatibles con los constructores anteriores por defecto:

| Contrato | Campos y responsabilidad |
| --- | --- |
| `AssistantReply` | `text`, `references`, `route`, `outcome`; solo el texto y metadatos públicos se persisten |
| `ValidatedReference` | `source_id`, `public_url`, `content_hash`, `supported_text`; añade `source_type`, `title`, `source_version`, `evidence_hashes` |
| `EvidenceSelection` | `source_id`, `content`; unidad íntegra en extractive, cita literal en grounded; `display_text` opcional para presentación localizada sin cambiar la evidencia |
| `GroundedClaim` | `text`, `evidence`; un párrafo visible y sus citas |
| `GeneratedAnswer` | `selections`; añade `text`, `claims`, `sufficient` para la reformulación |
| `KnowledgeBundle` | Idioma, fragmentos, reglas, conflictos, resultado, contexto y exclusiones existentes; `generation_mode` por defecto extractive y `response_sentences=0` salvo petición explícita de tres frases |

`source_id`, hashes, versiones, claims, rutas de decisión e identificadores de evidencia
son internos. No se mandan al navegador. Las fuentes no amplían los permisos del usuario.
El historial anterior del asistente sigue sin ser evidencia. El actual mecanismo ES/EN,
los límites de recuperación y la redacción de campos personales comunes se conservan.

### Archivos de esta entrega

Nuevos:

- `generation.py`, `references.py`, `migrations/0002_chatmessage_sources.py`.
- `tests/test_natural_answers.py`, `test_phase2_matrix.py`, `test_reference_migration.py`.
- `tests/browser/render_fixtures.py` y `check_widget.cjs`, harness opcional sin dependencia
  nueva en la aplicación; reutiliza un Playwright y Chrome ya instalados como herramientas.

Modificados dentro de `chatbot/`:

- `answering.py`, `grounding.py`, `providers.py`, `instructions.py`, `models.py`, `services.py`, `config.py`.
- `knowledge/contracts.py`, `normalization.py`, `sources/faq.py`, `sources/categories.py`, `sources/centers.py`.
- `templatetags/chatbot_tags.py`, `locale/en/LC_MESSAGES/django.po` y `.mo`.
- `management/commands/check_chatbot_openai.py`; autorización histórica; retirado en Prompt 5.
- Tests de API, conversaciones, concurrencia, SDK y JavaScript, además de este README.

Integración mínima fuera de la app: `tenetrueca/settings.py`, `.env.example`,
`static/css/chatbot.css`, `static/js/chatbot.js`, versiones de assets en `templates/base.html`;
`static/js/faq.js`, `static/css/faq.css` y versión del script en `templates/faq/public_list.html`.
El parcial `templates/chatbot/_widget.html` y su inclusión existente se reutilizan.
No se cambia la cabecera, hero, navegación, catálogo, Docker, dependencias ni otra lógica de negocio.

### Modalidades y selección local

```dotenv
# Default conservador y rollback inmediato; no exige migrar para cambiar de modalidad.
CHATBOT_GENERATION_MODE=extractive
# Opt-in en staging, tras aplicar 0002 y revisar el corpus público:
# CHATBOT_GENERATION_MODE=grounded
```

| Ruta | Elección | Llamadas máximas |
| --- | --- | --- |
| Determinista | Regla estructurada verificada suficiente o campo público inequívoco de centro | 0 |
| Aclaración, bloqueo o sin evidencia | Referente ambiguo, claim bloqueado o falta de fuente | 0 |
| Extractive | Configuración extractive, taxonomía/centros, cantidades, condiciones, negaciones, prohibiciones y otras señales críticas | 1 |
| Grounded | Configuración grounded y exclusivamente FAQ explicativas sin señales críticas en pregunta/evidencia | 1 |
| Fallback | Reformulación o selección inválida: unidades recuperadas completas, revalidadas, o abstención | 0 adicionales |

La selección considera la evidencia completa, incluyendo el título: un procedimiento que
contenga requisitos, excepciones o cantidades permanece extractivo. Una FAQ sencilla con
pasos descriptivos puede resumirse. Varias FAQ compatibles se pueden sintetizar en una sola
llamada. Una petición de «tres frases» se interpreta como estilo, no como un límite de negocio;
el backend exige exactamente tres frases en las respuestas generadas suficientes. Se reconocen
«en tres/3 frases» e «in three/3 sentences». No se añaden hechos ni frases de relleno para
alcanzar el número solicitado; ante evidencia insuficiente se mantiene la abstención.
No se mezclan hechos críticos con texto libre para saltarse el control estricto. Los datos
exactos de centros conservan su tratamiento local; no se deduce apertura en tiempo real.

Los claims `reservation.pickup_window`, `points.reservation_charge` y
`admission.small_appliance` permanecen bloqueados, sin reinterpretar decisiones históricas.
El reconocimiento de registro incluye el error habitual «registrame» y vocabulario acotado
de verificación. No hay corrección difusa general de reglas. Una pregunta de puntos sin
intención resuelta no hereda automáticamente un conflicto de cargo de una FAQ descartada.

### Structured Outputs y controles de reformulación

El modo extractive conserva `{selections: [{source_id, content}]}` con contenido íntegro.
Con idioma activo inglés o petición explícita de tres frases, exige además `display_text`
por selección. Es la presentación en el idioma activo, generada en la misma llamada;
`content` permanece literal y se utiliza para procedencia, hashes y revalidación. Se muestran
las presentaciones sin el antiguo preámbulo que obligaba a leer la respuesta en el idioma
de la fuente. Una unidad crítica/normativa solo admite `display_text == content`: esta
corrección no permite traducir ni reformular reglas críticas sin traducción autorizada.
Si no hay una presentación válida, el backend devuelve un error recuperable o la abstención
localizada, sin mostrar el texto extranjero como alternativa ni enviar otra llamada.
Grounded utiliza un esquema estricto con:

```json
{
  "answer": "Añade fotografías del objeto y envía la propuesta para su revisión.",
  "sufficient": true,
  "claims": [{
    "text": "Añade fotografías del objeto y envía la propuesta para su revisión.",
    "evidence": [{"source_id": "faq:123", "quote": "Cita literal presente en la FAQ autorizada."}]
  }]
}
```

El ejemplo ilustra el formato interno: el texto y la cita deben estar respaldados por la
misma fixture real para ser válidos. No es una respuesta evaluada con un modelo real.
El esquema no admite URLs; usa el enum de fuentes recuperadas, todos los campos requeridos
y `additionalProperties: false`. `answer` debe coincidir exactamente con los párrafos
`claims.text` unidos por dos saltos de línea. Máximo tres párrafos, tres fuentes, 1.800
caracteres de respuesta y 900 por párrafo. Cada afirmación requiere citas literales del
bundle aprobado, con longitud mínima y coincidencia léxica básica por cita.

Al solicitar tres frases, el esquema admite tres claims (una frase cada uno) o una
abstención vacía. Django valida el número de frases, incluyendo decimales, iniciales y
abreviaturas comunes. El texto visible debe estar en el idioma activo; las citas conservan
el idioma original. Se rechazan errores claros de idioma por frase y coincidencias de
relevancia usan equivalencias ES/EN acotadas, sin cambiar la recuperación de fuentes.
La detección de idioma y la coincidencia de términos son controles conservadores, no
pruebas de traducción fiel o implicación semántica. Se conservan los límites y revisión
de calidad descritos a continuación.

Django comprueba pertenencia de citas, fuentes permitidas, contenido/versión, vigencia pública
y rutas. Rechaza HTML, enlaces generados, datos personales con patrones conocidos y señales
críticas añadidas. Revalida otra vez las fuentes antes de finalizar, agrupando por pregunta
resuelta. No se utiliza otra llamada para verificar ni para traducir o construir enlaces.

**Estos controles no demuestran implicación semántica.** Compartir palabras y citar una
frase auténtica no demuestra que una paráfrasis sea fiel. Puede haber cambios de significado
sin indicadores léxicos; se mantiene el valor predeterminado extractive hasta evaluación
humana autorizada. Los bloqueos y las rutas deterministas son independientes del texto
producido por el modelo, pero la detección de preguntas normativas no es un clasificador
semántico exhaustivo.

La documentación oficial consultada confirma [Structured Outputs con Responses](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses)
y [su soporte en GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna).
Structured Outputs asegura la forma, no la corrección factual. Se comprobaron los tipos y
fixtures del SDK instalado: OpenAI 2.54.0, Django 5.2.18 y Python 3.12. El acceso real de la
cuenta y la calidad de respuesta no se han probado.

Una validación rechazada registra solo `reason=invalid_grounding`, sin pregunta ni salida.
Devuelve evidencia extractiva segura sin regenerar y **consume un turno respondido**.
Timeouts, autenticación, rate limits del proveedor, respuestas vacías/incompletas y errores
de lectura/persistencia siguen siendo fallos técnicos: liberación/expiración de reserva,
sin consumo. Un duplicado reproduce el resultado persistido y no genera otra llamada.

Se conserva `CHATBOT_MAX_OUTPUT_TOKENS=400`. Los mocks no permiten certificar que alcance
para todas las respuestas con citas. Se favorecen citas breves y respuestas concisas; una
salida truncada falla de forma técnica sin cuota. No se aumenta el límite por una estimación
no medida: en una futura evaluación autorizada se medirán tokens/truncamientos y se decidirá
si ajustar este parámetro. Se mantienen modelo configurable, timeout 30s, historia 10,
contexto 5 unidades/1.200 caracteres por unidad/6.000 totales y retries=0.

### Persistencia, migración y referencias históricas

`ChatMessage.sources` es `JSONField(default=list, db_default=[], blank=True)`.
La migración `0002_chatmessage_sources` es aditiva y solo añade esa columna JSONB. No tiene
RunPython ni limpieza de mensajes. Tanto Django como PostgreSQL proporcionan el valor vacío;
el default de BD permite que un escritor anterior siga insertando mensajes durante un rollback.
El test de migración conserva un mensaje preexistente y prueba también ese escritor antiguo.

Se guardan source_id, tipo, título público, URL validada o null, hash de contenido, versión
y hashes que identifican las evidencias usadas. No se copian FAQ completas ni se guarda
`internal_locator`, contenido administrativo o instrucciones. Texto y referencias se guardan
en la misma transacción corta que completa el turno y consume la reserva. Si falla, no queda
una respuesta o referencias huérfanas ni se consume cuota.

La migración solo se ha aplicado sobre bases efímeras de prueba. **No se ha aplicado a la
base local persistente ni a producción.** Se ha revisado `migrate --plan`, SQL PostgreSQL y
`makemigrations chatbot --check --dry-run`. Antes de utilizar esta versión con la base local
existente será necesario aplicar 0002; estos cambios no incluyen ese despliegue.

Política al recuperar historial:

- Fuente pública, misma huella, versión y URL: título original y enlace actual verificados.
- Contenido/título cambiado: referencia no disponible; no se atribuye el texto antiguo a la nueva versión.
- Fuente eliminada, despublicada o fuera del ámbito público: solo `{available: false}`,
  sin título anterior, URL, tipo ni razón interna.
- URL inválida: sin enlace ni reconstrucción alternativa.
- Mensajes anteriores sin fuentes: `sources: []`; no se reconstruyen mediante OpenAI.
- El texto histórico del mensaje se conserva, incluso si posteriormente se retira su fuente.

La revalidación lee hasta 100 IDs distintos, priorizando mensajes recientes, en consultas
por lote de cada tipo. FAQ utiliza una consulta con `pk__in`; se reutilizan la proyección
pública de categorías/ancestros y `public_centers/center_map_data`. Un test con 105 fuentes
prueba el tope y otro con mensajes repetidos prueba una sola consulta FAQ. El exceso muestra
«Referencia no disponible» sin exponer el motivo. No se hace una consulta por cada enlace.
La retención existente de 30 días también elimina los metadatos al limpiar cada mensaje;
no se ejecutó ninguna limpieza ni se instaló una tarea nueva.

### Contrato JSON y enlaces oficiales

Mismos tres endpoints, métodos, CSRF y propiedad. Cada mensaje assistant añade:

```json
{
  "role": "assistant",
  "content": "Explicación breve…",
  "sources": [{"title": "¿Cómo publicar un objeto?", "url": "/faq/#faq-question-123", "type": "faq", "available": true}]
}
```

El servidor ignora `sources` enviados por el cliente. Una regla estructurada puede tener
`url: null`: se muestra un título informativo, nunca una ruta de código. No se exponen hashes,
source_id ni el esquema de generación. IDs de mensajes/conversación siguen su contrato anterior.

FAQ usa el reverse existente y el summary `faq-question-N`: el script de FAQ abre details,
limpia el filtro si hace falta y enfoca/resalta el summary al cargar el fragmento o cambiarlo.
Categorías usa el catálogo real y filtros `category/subcategory` por slug, sin garantizar
admisión. Centros enlazan `/#puntos-limpios`; cuando existe el enlace de indicaciones ya
construido por `locations`, se añade `directions_url` con Google Maps HTTPS autorizado y
coordenadas públicas válidas. No se crean páginas de detalle. El frontend vuelve a comprobar
rutas/protocolo/dominio; mapas externos abren con `noopener noreferrer`.

### UX, formato seguro e internacionalización

Los cuatro estados conservan la implementación del widget: avatar cerrado fijo; panel de
380×520 adaptable en escritorio; avatar móvil dentro de safe areas; diálogo móvil casi a
pantalla completa. No hay desplazamiento del portal. Se reutilizan `visualViewport`, scroll
independiente, cabecera/composer visibles, foco de apertura/cierre y movimiento reducido.
Se amplían a 44px los objetivos móviles de envío, nueva conversación y fuentes.

«Fuentes» aparece bajo el texto, con títulos breves enlazados, como máximo tres fuentes y
un indicador genérico para las retiradas. Las reglas sin URL se muestran sin enlace. Los
párrafos y listas en texto plano conservan saltos mediante `white-space: pre-wrap`; textos
largos rompen palabras sin overflow. Toda salida/título se inserta con `textContent` y DOM,
nunca HTML o Markdown ejecutable. Los enlaces son enfocables y tienen nombre accesible.
Seguir una referencia interna cierra el widget para no tapar la página de destino.

Se conservan bienvenida, sugerencias solo en vacío, carga, errores recuperables, cuota
anónima, frecuencia, historial y estados de falta de información/aclaración. Las etiquetas
nuevas usan gettext ES/EN: Fuentes, Consultar información oficial, Referencia no disponible,
Información no confirmada y Cómo llegar. El título de una FAQ conserva su idioma original;
una explicación inglesa no se presenta como traducción oficial del documento.

### Evaluación offline E01–E29 y síntesis

`tests/test_phase2_matrix.py` ejecuta los 29 casos con sesiones independientes y evidencia
sintética. No mide el modelo real ni certifica el corpus publicado en producción. Para cada
caso se comprueban texto esperado, número de llamadas, cuota, referencias autorizadas y
ausencia de marcadores privados. Las pruebas dedicadas cubren más versiones/adversarios.

| Casos | Evidencia y modalidad verificadas en fixtures | Llamadas / cuota |
| --- | --- | --- |
| E01 | FAQ de funcionamiento, grounded con una explicación respaldada | 1 / 1 |
| E02–E04 | Reglas locales de registro/activación, publicación y entrega aceptada | 0 / 1 |
| E05–E06 | Cargo/plazo bloqueados, sin cantidades afirmadas | 0 / 1 |
| E07 | FAQ con autenticación y negación de objeto propio; extracción íntegra | 1 / 1 |
| E08 | Sin evidencia autorizada de devolución: abstención | 0 / 1 |
| E09 | Plazo/penalización sin regla aprobada: respuesta prudente | 0 / 1 |
| E10 | Sin evidencia de límite de reservas: abstención | 0 / 1 |
| E11 | Prohibición de una FAQ de fixture: extracción conservando negación | 1 / 1 |
| E12 | Sin evidencia de admisión en el fixture: abstención, no garantía | 0 / 1 |
| E13 | Claim de pequeño electrodoméstico bloqueado | 0 / 1 |
| E14–E15 | Caso eléctrico/dimensiones sin fuente aprobada: abstención | 0 / 1 |
| E16 | IA informativa y decisión humana, regla local | 0 / 1 |
| E17 | Horario de centro público, valor exacto y referencia pública | 0 / 1 |
| E18 | Festivo sin evidencia específica: no deducir apertura | 0 / 1 |
| E19 | Centro oculto: aclaración sin datos de su fila | 0 / 1 |
| E20 | Saldo/QR privado: no se consulta, abstención | 0 / 1 |
| E21 | Pregunta de coste sin tema: aclaración | 0 / 1 |
| E22–E23 | Cargo/aparato bloqueados, respuesta inglesa | 0 / 1 |
| E24 | Sin referente público: aclaraciones inglesas | 0 / 1 |
| E25 | Inyección de usuario no cambia permisos: aclaración | 0 / 1 |
| E26 | FAQ con instrucciones maliciosas excluida | 0 / 1 |
| E27 | FAQ no publicada excluida; tests dedicados cubren edición entre turnos | 0 / 1 |
| E28 | Horario vacío: no se inventa | 0 / 1 |
| E29 | Sin regla aprobada de límite diario: abstención | 0 / 1 |

Síntesis añadidas: resumen breve de FAQ larga; procedimiento para publicar; combinación de
dos FAQ compatibles; explicación en tres frases; ES/EN; diferencias normativas de publicar
y entregar; conservación íntegra de condiciones, excepciones, negaciones y cantidades;
fallos de cita, fuente inventada, falta de evidencia y respuesta normativa alterada.

Evaluación de las nueve dimensiones, separadas de una evaluación real:

| Dimensión | Evidencia automatizada | Pendiente con modelo/corpus real |
| --- | --- | --- |
| Recuperación | FAQ públicas, reglas y centros sintéticos; exclusiones y frescura | Relevancia y cobertura de consultas reales |
| Modalidad | Señales críticas fuerzan extractive; reglas/centros locales | Falsos negativos de clasificación lingüística |
| Fidelidad factual | Ejemplos fieles e invenciones conocidas rechazadas | Implicación semántica de paráfrasis nuevas |
| Condiciones/negaciones | Conservación literal y rutas estrictas | Corpus con condiciones implícitas no detectadas |
| Concisión/calidad | Fixture resumida más corta; párrafos/cotas comprobados | Naturalidad, utilidad, idioma y completitud |
| Referencias | Bundle, citas literales, hashes, versión, URL y publicación | Relevancia semántica entre cada afirmación y cita |
| Ausencia de evidencia | Abstención local/estructurada y sin fuente inventada | Calidad de aclaraciones para consultas ambiguas nuevas |
| Llamadas | Cero locales, máximo una generativa, sin retry de validación | Latencia, tokens y rechazos reales |
| Cuota | Un turno válido/fallback, cero en error técnico; duplicados/concurrencia | Revisión operativa sin modificar estas reglas |

Ejemplos **simulados**, no salidas reales de OpenAI:

- «Resume cómo publicar un objeto» → «Para publicar, inicia sesión, completa los datos y
  añade fotografías. Envía la propuesta para su revisión.» Fuente: FAQ pública del fixture.
- «Explain how to publish an object» → «To publish an object, sign in, add photographs and
  submit the proposal for review.» Fuente con título original «¿Cómo publicar un objeto?».
- «Me dan puntos por registrame?» → regla local de 100 puntos con registro público y
  activación completados. Fuente informativa «Puntos por registro público», sin URL inventada.
- Pregunta sobre cargo/plazo bloqueado → respuesta de información no confirmada, sin
  fuente que atribuya arbitrariamente una de las versiones históricas.

### Pruebas y reproducción

Todos los contenedores de prueba usan montaje de código de solo lectura, CI, clave vacía
y red `none`; PostgreSQL comparte únicamente el namespace de un contenedor efímero también
sin red exterior. El SDK se simula incluso en los tests que establecen una clave ficticia.
No se usan credenciales ni datos de la base de desarrollo/producción.

```bash
# Desde la raíz Docker, APP_PATH debe apuntar al checkout app.
docker run --rm --network none --read-only --tmpfs /tmp \
  -v "$APP_PATH:/app:ro" -w /app -e CI=true -e PYTHONDONTWRITEBYTECODE=1 -e OPENAI_API_KEY= \
  --entrypoint python tenetrueca-app:latest manage.py test --settings=tenetrueca.test_settings --noinput

# PostgreSQL efímero, sin publicar puertos; contraseña exclusivamente sintética.
docker run --rm -d --name trueca-p4-test-pg --network none --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_PASSWORD=temporary-test-password -e POSTGRES_DB=trueca_ai_test postgres:17-alpine
# Esperar pg_isready antes del siguiente comando.
docker run --rm --network container:trueca-p4-test-pg --read-only --tmpfs /tmp \
  -v "$APP_PATH:/app:ro" -w /app -e CI=true -e PYTHONDONTWRITEBYTECODE=1 -e OPENAI_API_KEY= \
  -e OPENAI_TEST_DB_PORT=5432 --entrypoint python tenetrueca-app:latest \
  manage.py test --settings=tenetrueca.test_postgres_settings --noinput
# Al terminar exclusivamente este contenedor efímero:
docker stop trueca-p4-test-pg

# Desde app:
node --test chatbot/tests/js/test_chatbot.cjs
msgfmt --check --check-format -o chatbot/locale/en/LC_MESSAGES/django.mo chatbot/locale/en/LC_MESSAGES/django.po
```

El harness browser renderiza los templates reales con `render_fixtures.py` en SQLite de
memoria y recibe un directorio temporal como argumento. `check_widget.cjs` recibe ese mismo
directorio, usa un Playwright existente mediante NODE_PATH y Chrome existente mediante
CHATBOT_TEST_BROWSER. Todas sus solicitudes se interceptan: assets del checkout y snapshots
sintéticos, sin servidor vivo ni acceso exterior. No exige instalar Playwright en producción.
Comprueba 1440×900, 1024×768, 390×844, 320×568 y 390×320; anónimo/autenticado × ES/EN.
Revisa avatar, panel, composer/cierre visibles, overflow, texto ampliado, resize de viewport,
foco/teclado, restauración, enlaces FAQ y ausencia de desplazamiento del hero.

Resultados finales de esta entrega: ver tabla de verificación al final de esta sección.

### Staging, rollback y preparación para Prompt 5

Este procedimiento se documenta; no se ha ejecutado un despliegue:

1. Revisar corpus público y permisos, guardar respaldo según la operación habitual y
   comprobar que se conserva el mismo motor de sesiones servidor y configuración PostgreSQL.
2. En staging aislado, `python manage.py migrate --plan` y
   `python manage.py migrate chatbot`, después `python manage.py check` y
   `python manage.py collectstatic --noinput`. No hay data migration ni limpieza automática.
3. Empezar con `CHATBOT_PROVIDER=mock` y modo extractive; comprobar tres endpoints, usuario
   anónimo/autenticado, fuentes tras recarga/edición/despublicación, cuotas y cuatro estados.
4. Evaluar grounded con mocks y fixtures. La antigua prueba manual sin ledger quedó
   retirada en Prompt 5. Para cualquier prueba real seguir exclusivamente el procedimiento
   presupuestado de [validación final](../docs/chatbot/phase2_final_validation.md#21-procedimiento-futuro-diez-preguntas-hasta-veinte-llamadas),
   tras una nueva autorización explícita. CI/tests siguen bloqueados. No se ejecutó aquí.
5. Para Prompt 5, preparar revisión humana ES/EN de recuperación, modalidad, fidelidad,
   negaciones, fuentes, latencia, tokens y abstenciones. El corpus y preguntas adversariales
   deben mantenerse junto con una rúbrica y resultado por caso; no activar grounded como
   default de producción basándose solo en mocks.

Rollback preferente: `CHATBOT_GENERATION_MODE=extractive` y reinicio según el mecanismo
habitual; conserva referencias y cuota. Desactivar todo con CHATBOT_ENABLED=False si hace
falta. Se puede restaurar código anterior conservando la columna y su default de BD; no
bajar 0002 como primer recurso porque eliminaría metadatos de fuentes. Nunca se borra historial
ni se ejecuta cleanup para un rollback. No se han cambiado flags/presupuestos de moderación.

Riesgos pendientes: fidelidad semántica y cobertura léxica limitadas; corpus incompleto o
obsoleto; límite de salida todavía no medido con modelo real; FAQ que exceda el presupuesto
se omite íntegramente; ventana de cambio entre la última lectura y la persistencia (en una
lectura posterior el enlace se oculta); historial textual no se censura retroactivamente.
La redacción de PII reconoce patrones comunes y no es un detector universal. No se prometen
traducciones oficiales ni ausencia total de alucinaciones. Safari real en iPhone, teclado
iOS/Android físico y validación manual con lectores de pantalla quedan pendientes; Chromium
con visualViewport simulado no los sustituye. No se continúa con Prompt 5.

### Verificación final del Prompt 4

| Comprobación ejecutada | Resultado |
| --- | --- |
| `manage.py test chatbot --settings=tenetrueca.test_settings --noinput` | 240 detectados; 222 pasan y 18 omitidos por necesitar PostgreSQL/migraciones reales; 2,660s |
| Suite Django SQLite completa | 774 detectados; 739 pasan y 35 omitidos por backend/row locks; 29,531s |
| Suite Django PostgreSQL 17 completa | 774 pasan, sin omitidos; 52,743s |
| Matriz E01–E29 | 29 subcasos del test matricial pasan en ambas suites |
| `node --test chatbot/tests/js/test_chatbot.cjs` | 32 pasan, sin omitidos |
| Harness de layout en Chrome headless | 20 casos pasan; cinco tamaños × anónimo/autenticado × ES/EN |
| `makemigrations chatbot --check --dry-run` sobre PostgreSQL aislado | Sin cambios pendientes en el modelo |
| `migrate --plan` y `sqlmigrate chatbot 0002` | Columna JSONB aditiva con default []; sin RunPython ni modificación de otros modelos |
| Plan local, solo lectura | Únicamente `chatbot.0002_chatmessage_sources` pendiente; no se ejecutó migrate |
| System checks / chatbot desactivado sin clave | Sin incidencias |
| `collectstatic` con STATIC_ROOT temporal | CSS/JS de chatbot y FAQ presentes |
| GNU `msgfmt --check --check-format` | Catálogo válido; .mo entregado coincide con el compilado |
| AST Python y `git diff --check` | Correctos |
| Comparación de 209 archivos de aplicaciones protegidas | Ningún cambio en accounts, assistant, catalog, core, faq, locations, moderation, points, publications o reservations |

Los mensajes esperados de error y creación de datos demo en las suites proceden de tests
sobre sus bases sintéticas. Los contenedores de prueba se eliminaron, incluido PostgreSQL
efímero. No se arrancó el worker de evaluación de moderación ni se modificó el entorno Docker.
El contenedor local conserva la migración pendiente: aplicar `python manage.py migrate chatbot`
en el entorno elegido es un paso operativo posterior, previo al uso de esta versión.

Aceptación técnica:

- [x] Reformulación explicativa configurable, con alternativa extractive.
- [x] Rutas locales y claims bloqueados conservados; sin una segunda llamada generativa.
- [x] Referencias persistentes, revalidadas y ocultas tras pérdida de publicación/versión.
- [x] Migración aditiva con compatibilidad de historial y escritor anterior.
- [x] UI segura y compatible con el contrato anterior, ES/EN y anclas FAQ accesibles.
- [x] Concurrencia, duplicados, CSRF, cuota y regresiones comprobadas en PostgreSQL aislado.
- [x] Cuatro estados responsive comprobados en Chromium con fuentes y texto ampliado.
- [ ] Aplicación de 0002 en el entorno donde se vaya a utilizar la versión; sin despliegue aquí.
- [ ] Evaluación humana y consumo/truncamiento con OpenAI real, solo tras autorización.
- [ ] Safari/iPhone y teclados físicos; lectores de pantalla reales.

## Fase 2, Prompt 5 — Evaluación final y presupuesto

Entrega del 06/11/2026. Estado **B — Listo para staging**; producción todavía no recomendada.
Informe vigente: [phase2_final_validation.md](../docs/chatbot/phase2_final_validation.md).
Las cifras de Prompts anteriores son históricas. El informe final documenta las pruebas de
esta ejecución, resultados por caso, mediciones locales, correcciones y verificaciones pendientes.
No se realizaron llamadas reales, despliegues ni modificaciones de datos reales.

### Dataset, controles y resultados

90 casos versionados E01–E90, ES/EN, historial sintético, fuentes/claims/condiciones/bloqueos,
comportamiento y criterios independientes de las salidas del código. Dos modos sobre el mismo
corpus público sintético; 20 muestras por perfil de rendimiento y sondeo de 20/200/1000 mensajes.
[Resultados completos](../docs/chatbot/phase2_offline_results.json) identifican explícitamente
mocks, bytes diagnósticos, consultas, latencias locales y puntuaciones semánticas pendientes.
[Rúbrica 0–4](evaluation/RUBRIC.md) y [revisión humana](../docs/chatbot/phase2_review_template.md)
separan fallos críticos de promedios. No se usa otro modelo para juzgar respuestas.

Se corrigen equivalencia inglesa publication, solicitudes de estilo «3 frases», privacidad
de direcciones/consultas personales, horario regular frente a apertura actual/festivos,
contradicciones normativas de puntos en FAQ y contraste de textos del widget. Las unidades
numéricas sobre puntos deben ser una verbalización aprobada completa, o se excluyen. Esto
puede omitir FAQ legítimas; no es un verificador semántico general. No se desbloquean claims.

### Evaluación real preparada, desactivada

```dotenv
CHATBOT_EVAL_ALLOW_LIVE=False
CHATBOT_EVAL_CASE_IDS=
CHATBOT_EVAL_ENVIRONMENT=disabled
CHATBOT_EVAL_AUTHORIZATION_ID=
CHATBOT_EVAL_PROJECT_REF=
CHATBOT_EVAL_PRICING_FILE=
CHATBOT_EVAL_MAX_BUDGET_USD=1.00
CHATBOT_EVAL_MAX_REQUESTS=20
CHATBOT_EVAL_MAX_INPUT_TOKENS=1050000
CHATBOT_EVAL_MAX_OUTPUT_TOKENS=400
CHATBOT_EVAL_PRICE_MAX_AGE_DAYS=7
```

Los límites se pueden reducir; este piloto no admite más de 1 USD/20 intentos/400 tokens de
salida, tampoco mediante CLI. No cambian límites públicos ni presupuestos/flags de moderación.
No es un límite de la factura global de cuenta: otras apps/usuarios/procesos quedan fuera.

`ChatEvaluationRun` y `ChatEvaluationAttempt` conservan reservas, pico comprometido, uso,
resultados y pendientes en PostgreSQL. Migración aditiva `0003_evaluation_budget`, sin data
migration. No se ha aplicado a bases reales. Un UUID configurado fijo y un lock advisory
exclusivo evitan ejecuciones simultáneas/reinicios accidentales. Las reservas se admiten con
row locks y se comprometen antes del cliente SDK. No hay transacción durante la llamada.

La cota disponible es toda la ventana documentada del modelo (1.050.000 para el perfil de
Luna), no caracteres/4. Incluye instrucciones/esquema/evidencia/framing por su límite global.
Con un límite de entrada menor no se puede justificar la cota y se rechaza la llamada.
Tarifas exactas y máximas para todos los contextos/componentes deben aprobarse manualmente;
[pricing.example.json](evaluation/pricing.example.json) está sin aprobar y sin valores operativos.
Se contemplan cache writes, recargos y fijo mediante tasas conservadoras; no se descuenta caché.
SDK con esquema de uso desconocido se rechaza. Precios se comprueban antes de cada intento.

Uso fiable → techo calculado desde tokens reportados y liberación solo de la diferencia
justificada. Timeout/error sin uso → reserva completa retenida. Unknown nunca significa coste
cero. Es Decimal, con redondeo superior de costes y sin ampliar presupuestos por redondeo.
`--resume` exige misma autorización/configuración y no reenvía intentos existentes. Perder la
exportación no reinicia fondos. El ledger no se elimina con la retención de conversaciones.

Comando por defecto, **sin cliente OpenAI ni admisión de gasto**:

```bash
python manage.py evaluate_chatbot_live --dry-run --dataset pilot-v1 --mode both --output /tmp/chatbot-preview.json
```

En producción/staging usar un directorio protegido y persistente. El preview no crea ni
reinicia el presupuesto de una ejecución existente: es una simulación marcada como tal.
El piloto contiene diez preguntas, dos locales: hasta 16 generaciones si hay fuentes, sin
forzar llegar al máximo 20. No se utiliza el anterior `check_chatbot_openai`: se retiró porque
permitía una evaluación fuera de la contabilidad.

**Solo después de una autorización futura explícita**: configurar proceso de staging/proyecto/
clave existente, UUID fijo, tarifas aprobadas y durabilidad PostgreSQL; revisar preview; usar
`--allow-live --run-id <UUID-aprobado> --max-budget-usd 1.00 --max-requests 20` y demás límites.
El informe contiene el procedimiento exacto. CI/tests/migraciones/startup siguen bloqueados.
Nunca habilitar esa ruta en CI ni generar un UUID nuevo para eludir un presupuesto agotado.

### Reproducción offline

En bases **nuevas y sintéticas**, con CI=true y contenedor sin red:

```bash
python manage.py evaluate_chatbot_offline --dataset phase2-v1 --samples 20 \
  --output /tmp/chatbot-sqlite.json --settings=tenetrueca.test_settings
python manage.py evaluate_chatbot_offline --dataset phase2-v1 --samples 20 \
  --output /tmp/chatbot-postgres.json --settings=tenetrueca.test_postgres_settings
```

El comando crea/destruye una base de test y rechaza settings normales o ausencia de guards.
Nunca instala fixtures sobre el corpus real. El SQL EXPLAIN y el detalle de fases permanecen
en el informe JSON; el tiempo del mock no representa latencia de OpenAI.

### Archivos y límites de esta entrega

Nuevos: `evaluation/` (dataset/corpus/piloto, rúbrica, límites, tarifas, ledger, provider,
runner, mediciones y export), comandos `evaluate_chatbot_live`/`evaluate_chatbot_offline`,
`migrations/0003_evaluation_budget.py`, `knowledge/point_policy.py`, tests de presupuesto y
dataset, informe final, evidencia JSON y plantilla de revisión.

Modificados: `models.py` (descubrimiento del ledger independiente), `providers.py` (reutiliza
construcción de argumentos/cliente), `answering.py`, `generation.py`, `references.py`,
`knowledge/context.py` y `normalization.py`, comando smoke retirado, tests afectados,
`tenetrueca/settings.py`, `.env.example`, `.gitignore`, CSS del widget y su versión en base,
harness browser y este README. El test de migraciones restaura todas las hojas de chatbot.
No se modifican servicios de puntos/reservas/moderación ni otras aplicaciones de negocio.

Pendientes: corpus editorial y precisión de recuperación, fidelidad/naturalidad/tokens reales,
Safari/teclados/lectores físicos, configuración productiva de HTTPS/proxy/staticfiles/backups,
migración y autorización operativa del piloto. Mantener extractive como default hasta revisión.
Rollback: desactivar evaluación y detener proceso; conservar tablas/counters/unknown; no bajar
0003 ni restaurar un saldo anterior al gasto. No se despliega ni se continúa con Fase 3.

### Verificación final de Prompt 5 — 06/11/2026

PostgreSQL: 821/821 correctos. SQLite: 780 correctos y 41 omitidos de 821.
Suite chatbot SQLite: 263 correctos y 24 omitidos de 287. Presupuesto PostgreSQL:
37/37 correctos, incluidos en la regresión. JavaScript: 32/32. Responsive Chromium:
28/28. Cero fallos. Proveedores simulados y bases aisladas, sin llamadas a OpenAI.
Comandos, mediciones y límites en el [informe final](../docs/chatbot/phase2_final_validation.md).

### Corrección de funcionamiento general — 07/11/2026

La pregunta «Cómo funciona trueca?» se abstenía en la base local porque solo había una FAQ
publicada y ninguna evidencia recuperable explicaba el funcionamiento. La grafía sin @ no
es un error de usuario. Se incorpora `platform.overview` a los hechos descriptivos verificados:
intercambio sostenible en Tenerife, catálogo/reservas y publicaciones sujetas a revisión.
Procede de las capacidades ya verificadas en Fase 1 y la descripción pública del portal;
no añade importes, plazos ni condiciones de admisión, ni sustituye los cuatro hechos normativos.

Una FAQ pública elegible sigue usando la ruta fundamentada existente. Si no hay una FAQ
apta, las preguntas generales explícitas ES/EN reciben la descripción local con procedencia
persistente y revalidación. El reconocimiento es acotado: condiciones adicionales, datos
privados y claims bloqueados no se convierten en una descripción general. No se usan FAQ
ocultas o maliciosas. No hay llamadas al proveedor para la respuesta local. La cuota normal
se conserva, también para duplicados y reset.

Archivos: `knowledge/normalization.py`, `registry.py`, `service.py`, `sources/rules.py`,
`conversation_context.py`, `answering.py`, `references.py`; nuevo
`tests/test_platform_overview.py` y ajustes de expectativas en `test_knowledge.py` y
`test_phase2_matrix.py`. Sin cambios de modelos, migraciones, frontend o reglas de negocio.

Verificación de esta corrección: siete tests nuevos, incluidos en 294 tests de chatbot
PostgreSQL correctos y 294 SQLite (270 correctos, 24 omitidos). La regresión completa SQLite
final: 828 tests, 787 correctos y 41 omitidos. La primera ejecución completa tuvo un fallo
intermitente ajeno al cambio en `backoffice.tests.test_views.BackofficeViewsTests.
test_pickup_queue_links_to_existing_qr_flow`: dos firmas timestamp se generaron en segundos
consecutivos. Pasó aisladamente y en la repetición completa; no se modificó backoffice/reservas.
Todos los tests usan bases efímeras, CI, SDK protegido y contenedores sin red exterior.

Comandos: `python manage.py test chatbot --settings=tenetrueca.test_postgres_settings --noinput`,
`python manage.py test chatbot --settings=tenetrueca.test_settings --noinput` y
`python manage.py test --settings=tenetrueca.test_settings --noinput` en esos aislamientos.
Además se verificó ES/EN contra las fuentes públicas locales en una transacción de solo
lectura, con proveedor prohibido; la pregunta exacta respondió por ruta deterministic.
Se recargaron gradualmente los workers Gunicorn locales, sin ejecutar migrate ni reiniciar
contenedores. No se crearon conversaciones ni se modificaron FAQ/datos reales para probarlo.
Las métricas y salidas versionadas del 06/11 siguen siendo históricas, no nuevas mediciones.

### Staging temporal y candidata promocionable — 07/11/2026

Informe vigente: [staging_and_budget_review.md](../docs/chatbot/staging_and_budget_review.md).
La imagen se construye una vez con `Dockerfile.candidate`/`compose.candidate.yml` y staging
la consume por digest, sin build. No incorpora configuración de staging por defecto ni
secretos/datos operativos. La futura promoción usa la misma imagen con configuración y DB
propias de producción, en otra tarea con autorización explícita. No se promocionó aquí.

Staging temporal independiente: `compose.staging.yml` y, para validación sintética,
`compose.staging.ephemeral.yml`; proyecto, volúmenes, base PostgreSQL, cookies y secretos
propios. Nginx de ingreso sin secretos en 127.0.0.1:8083, Django/PG en red interna sin egress.
La instancia actual en 8082 y los servicios de moderación no se reinician ni reconfiguran.
`deploy/staging/check_compose.py` exige digest/ausencia de build y rechaza recursos compartidos.
`check_chatbot_staging` revisa identidad/durabilidad y manifiesto de migraciones; no las aplica.
`validate_chatbot_staging` admite solo una base efímera nueva y revierte sus fixtures.

Presupuesto: `CHATBOT_EVAL_TOKEN_BOUND_METHOD=conservative` sigue usando la ventana completa.
`auto` vuelve al mismo método; `verified` rechaza la ejecución porque no hay cota local
ajustada garantizada para framing/esquemas Responses. No se instaló tokenizador ni se llamó
al endpoint remoto de conteo. Bytes JSON son diagnóstico, no tokens. SDK de evaluación
revisado 2.54.0; otro SDK/perfil falla antes de crear cliente. El dry-run informa cota, método,
fuentes, tamaños, llamadas máximas posibles, tarifas ausentes y blockers, sin ledger ni SDK.

La plantilla de precios sigue sin aprobar. Requiere además
`rate_scope=maximum_across_entire_context_window`, contemplando contexto largo/escrituras/
recargos y margen. No se reducen reservas para forzar el piloto. Los caps siguen 1 USD,
20 intentos y 400 salida, sin retries; uso desconocido mantiene su reserva. No hay migración
nueva ni cambios de reglas/API/frontend. Los presupuestos existentes nunca se reinician.

El perfil futuro `compose.staging.pilot.yml` es un CLI independiente, no ejecutado, con
UUID fijo/proyecto/tarifas/clave y autorización expresa. Una base temporal no permite borrar
su ledger con gasto incierto; conservar/archivar/reconciliar antes de cerrar el entorno.
El informe documenta comandos, pruebas, limitaciones y promoción/rollback sin rebuild.

Destino confirmado por el operador: VPS Ubuntu **linux/amd64**. La validación ARM64
preliminar no autoriza promoción; la candidata final y su digest deben ser AMD64. Los
Compose de construcción/staging fijan esa plataforma y el preflight comprueba la imagen.
`Dockerfile.runtime` y `runtime-requirements.lock` permiten preparar el runtime AMD64 con
las mismas versiones revisadas, sin añadir dependencias. La promoción posterior usa el
artefacto AMD64 validado, sin reconstruirlo en la VPS.


### Selección acotada para micropilotos (2026-11-09)

La candidata del 8 de noviembre no admite selección individual. Una candidata posterior
añade `--case-ids E34 E36`, sin cambiar `pilot-v1` ni sus fuentes versionadas. La ejecución
real de un subconjunto exige `CHATBOT_EVAL_CASE_IDS=E34,E36` en el servidor, exactamente
en ese orden y sin duplicados. Si existe ese alcance, omitir el selector se rechaza;
no se ejecutan automáticamente las diez preguntas. El máximo de solicitudes del subconjunto
no puede exceder sus pares caso/modo: cuatro para estos dos casos en ambos modos.

Los micropilotos se ejecutan por modo: E34/extractive, E36/extractive, E34/grounded,
E36/grounded. La selección, alcance del servidor, orden y hash del dataset forman parte
de la configuración persistente; modificarlos al reanudar invalida el run. El ledger,
bloqueos, reservas conservadoras y validación de fuentes se conservan. No hay migraciones.
Los informes de CLI omiten el identificador privado del proyecto; permanece únicamente
en la configuración privada necesaria para ligar la autorización.

El Compose del evaluador admite `TRUECA_PILOT_CASE_IDS`, `TRUECA_PILOT_MAX_REQUESTS` y
`TRUECA_PILOT_MAX_BUDGET_USD`. Para el micropiloto: `E34,E36`, `4`, `1.00`. No son
autorización de gasto: se siguen exigiendo nueva aprobación de imagen/UUID/proyecto/precios
y `--allow-live`, con PostgreSQL duradero y sin reintentos. No reutilizar el UUID del
piloto cerrado del 7 de noviembre.

### Formato local conservador de tres frases (2026-11-09)

Una petición explícita de tres frases puede resolverse sin proveedor cuando la única
FAQ pública recuperada contiene dos oraciones con tres acciones imperativas independientes,
unidas por una única «y» (o «and», para evidencia inglesa). Se conservan todas las palabras,
salvo esa conjunción, y solo se añaden mayúscula inicial y puntuación. No se traduce, resume
ni rellena contenido para alcanzar el número solicitado. Este caso incluye E34 de `pilot-v1`.

El formateador rechaza fuentes críticas, condiciones, cifras, alternativas, fuentes múltiples
y construcciones fuera de esa gramática acotada. Los validadores de estilo, presentación,
citas y grounding siguen intactos; las fuentes se vuelven a consultar antes de guardar la
respuesta. Cuando no resulta aplicable continúa la ruta existente del proveedor, sin
reintentos de reparación. El dry-run refleja las llamadas evitadas: E34/E36 en ambos modos
planifica dos llamadas máximas, frente a cuatro antes de esta corrección. El corpus, los
pilotos históricos, sus presupuestos y sus candidatas congeladas permanecen intactos.
