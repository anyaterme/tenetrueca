# Revisión asistida de publicaciones

Implementación preparada en la rama `openai`. Solo existe el modo ASSISTED: el servicio de
evaluación no importa ni ejecuta el motor de decisiones administrativas. Se conservan los
estados de publicaciones, los cuatro pasos del formulario y las reglas de puntos, reservas,
recepción y retirada. Las decisiones siguen pasando por `moderation.services` y sus permisos.

## Arquitectura y flujo

1. Al enviar o reenviar una publicación, `transaction.on_commit` programa su evaluación.
   Un error de programación no revierte el envío ni impide la revisión manual.
2. `PublicationEvaluation` contiene un trabajo persistente en la misma base de datos y una
   instantánea sin datos de cuenta. La huella SHA-256 incluye título, descripción, categoría,
   catálogo real, fotografías y fecha de envío. Una nueva entrega genera otra versión incluso
   cuando el contenido no cambia.
3. `evaluation_worker` reclama el trabajo con bloqueo de la publicación y de la evaluación.
   Una restricción parcial impide dos trabajos activos para la misma publicación. El proceso
   HTTP nunca espera la respuesta de OpenAI. No había infraestructura asíncrona: para este
   único flujo se utiliza PostgreSQL y un worker dedicado, sin incorporar Celery ni Redis.
4. El proveedor real usa el SDK oficial, Responses API, imágenes JPEG privadas en base64,
   Structured Outputs y validación local con Pydantic. La clave permanece en el servidor;
   se fuerza `store=False` y no se habilitan herramientas ni operaciones administrativas.
   Referencias: [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs),
   [imágenes en Responses API](https://developers.openai.com/api/docs/guides/images-vision).
5. Se registran resultado, reglas/prompt/modelo, fechas, errores saneados y consumo por intento.
   La respuesta se valida después de registrar el consumo, incluso si está incompleta, rechazada
   o no cumple el esquema. Nunca se almacenan excepciones del SDK con cuerpos o credenciales.
6. Se comprueba la huella antes y después del análisis. Un resultado tardío no sustituye el de
   otra versión. Las evaluaciones históricas se conservan y se identifican como históricas.
7. El panel actual de moderación muestra «Evaluación automática», diez comprobaciones,
   categorías alternativas, dimensiones, incidencias y recomendaciones. El gestor puede decidir
   aunque la evaluación falle, y puede repetir el análisis mediante POST con CSRF y los permisos
   actuales por rol y punto limpio. La decisión guarda la evaluación vigente disponible entonces
   y la huella de la publicación para comparar posteriormente IA y gestor.

Los estados técnicos son `pending`, `processing`, `completed`, `error` y `obsolete`. Son
independientes de los estados administrativos; no se añaden estados administrativos nuevos.

## Configuración y ejecución sin coste

Instalar las dependencias desde **app/**:

```bash
python -m pip install -r requirements-dev.txt
python manage.py test --settings=tenetrueca.test_settings --noinput
python -m pytest
```

No se necesita `OPENAI_API_KEY`. La configuración de pruebas usa una base SQLite temporal,
correo en memoria y proveedor simulado. Los modelos existentes se sincronizan sin depender
de los artefactos de migraciones base que este repositorio ignora.

Para desarrollo visual y funcional, configurar `OPENAI_EVALUATION_BACKEND=mock` y ejecutar
`python manage.py evaluation_worker` como proceso separado, después de aplicar las migraciones.
El panel muestra claramente «Simulación de desarrollo»; el fixture predeterminado no afirma
haber analizado las fotos. Hay fixtures reutilizables para objeto admisible, ropa prohibida,
pequeño electrodoméstico, categoría incorrecta, incoherencia entre fotos/texto, medidas visuales
y medidas no verificables. `--once` permite procesar como máximo un trabajo de la cola.

El valor predeterminado `OPENAI_EVALUATION_BACKEND=disabled` deshabilita el servicio; la revisión
manual continúa disponible. No se debe habilitar el proveedor real para probar cambios menores,
estilos, prompts, errores o reglas de negocio.

## Docker y migraciones

### Arranque local en modo simulado

Desde el directorio padre de `app/`:

```bash
docker compose -f docker-compose.yml -f app/compose.openai.yml -f app/compose.mock.yml up -d --build app evaluation_worker
```

`compose.mock.yml` fuerza `OPENAI_EVALUATION_BACKEND=mock`, deshabilita el acceso real, vacía
la clave y fija en cero el presupuesto y el límite diario de solicitudes reales en ambos servicios,
incluso si `.env` contiene otros valores. El worker espera a que la aplicación responda al healthcheck
y sus migraciones se hayan aplicado. Para consultar el estado o los logs, usar los mismos tres
archivos Compose con `ps` o `logs --tail=50 app evaluation_worker`.

Acceder a `http://localhost:8000/moderacion/` con una cuenta de gestor o administrador existente.
Abrir una publicación pendiente con fotografías, pulsar «Repetir análisis» y actualizar la página
unos segundos después. Debe mostrar «Simulación de desarrollo». El fixture predeterminado deja
las comprobaciones como no verificables; no interpreta las fotografías ni consume OpenAI.

El Git del proyecto empieza en **app/**. Los archivos Docker originales están en su directorio
padre. Por ello se entrega un override versionado, sin modificar los archivos externos a Git:

```bash
# Desde el directorio padre de app/
docker compose -f docker-compose.yml -f app/compose.openai.yml build
# Aplicar migraciones antes de arrancar el worker, durante la puesta en servicio autorizada:
docker compose -f docker-compose.yml -f app/compose.openai.yml run --rm app python manage.py migrate
docker compose -f docker-compose.yml -f app/compose.openai.yml up -d app evaluation_worker
```

`Dockerfile.openai` instala **app/requirements.txt**, que incorpora OpenAI y Pydantic. El worker
reutiliza la base de datos, red, variables y almacenamiento privado existentes. No se ha realizado
ningún despliegue en producción. En el arranque local solicitado se aplicaron las dos migraciones
nuevas después de guardar una copia privada de la base local.

Migraciones nuevas (excepciones explícitas en `.gitignore` para poder revisarlas/versionarlas):

- `assistant/0001_initial`: evaluaciones, intentos, reserva diaria, índice de cola y exclusión
  de trabajos activos duplicados. Depende de la migración local de fotografías de `publications`.
- `moderation/0002_moderationdecision_ai_evaluation_and_more`: referencia opcional a evaluación
  y huella de la publicación; las decisiones previas siguen siendo válidas con esos campos vacíos.

Las migraciones base existen localmente y están ignoradas por la convención previa del proyecto.
Conservar esos artefactos y su historial en los despliegues; las nuevas migraciones no sustituyen
ni regeneran las bases instaladas. Un checkout que carezca de las migraciones anteriores debe
recuperarlas de los artefactos de su entorno antes de usar `migrate`.

Para comprobar bloqueos y migraciones en un PostgreSQL **exclusivo de pruebas**:

```bash
OPENAI_TEST_DB_PORT=55439 OPENAI_TEST_DB_PASSWORD=temporary-test-password \
  python manage.py test --settings=tenetrueca.test_postgres_settings --noinput
```

Ese módulo fija el host a `127.0.0.1` y usa credenciales de pruebas independientes; nunca hereda
las de desarrollo. Requiere permisos para crear/eliminar la base `test_trueca_ai_test`.

## Variables de entorno

Las opciones y sus valores iniciales se incluyen en `.env.example`. No copiar claves a Git.

| Variable | Valor inicial / función |
| --- | --- |
| `OPENAI_EVALUATION_BACKEND` | `disabled`; admite `mock` o `live` |
| `OPENAI_LIVE_ENABLED` | `False`; segunda activación explícita para el proveedor real |
| `OPENAI_API_KEY` | Vacía; solo servidor |
| `OPENAI_MODEL` | Vacío; seleccionar expresamente un modelo con visión y Structured Outputs |
| `OPENAI_TIMEOUT_SECONDS` | 45; rango admitido 5–120 s |
| `OPENAI_MAX_OUTPUT_TOKENS` | 2400; rango admitido 256–4096 |
| `AI_IMAGE_MAX_DIMENSION` | 1024 px en el lado mayor; rango 128–4096; sin ampliar |
| `AI_IMAGE_JPEG_QUALITY` | 80; entero entre 1 y 95 |
| `AI_IMAGE_FORMAT` | JPEG; se rechazan otros formatos de salida |
| `AI_IMAGE_DETAIL` | `high`; admite `low`, `high`, `auto`, `original` según el modelo |
| `AI_IMAGE_REMOVE_EXIF` | True; debe mantenerse activado para no transmitir metadatos privados |
| `AI_IMAGE_MAX_COUNT` | 3; entre 1 y el máximo de fotos permitido por publicación (8 inicialmente) |
| `OPENAI_IMAGE_MAX_SIDE` | Nombre anterior, aceptado desde el entorno si falta `AI_IMAGE_MAX_DIMENSION` |
| `OPENAI_IMAGE_TOKEN_RESERVE` | 8192 por imagen; revisar el margen para el modelo elegido |
| `OPENAI_MAX_ATTEMPTS` | 2; tope absoluto 3 para respuestas 429 reintentables |
| `OPENAI_RETRY_DELAY_SECONDS` | 120; espera creciente entre intentos |
| `OPENAI_DAILY_REQUEST_LIMIT` | 20; incluye intentos reservados, incluso errores |
| `OPENAI_DAILY_BUDGET_USD` | 1; límite de reservas global, compartido entre workers, por día UTC |
| `OPENAI_REQUEST_RESERVE_USD` | 0.10; debe cubrir la estimación conservadora de entrada y salida |
| `OPENAI_INPUT_USD_PER_MILLION` | 0; introducir tarifa vigente del modelo antes de habilitar llamadas |
| `OPENAI_OUTPUT_USD_PER_MILLION` | 0; introducir tarifa vigente del modelo antes de habilitar llamadas |
| `OPENAI_MAX_INPUT_BYTES` | 30000 para texto/datos; una entrada mayor se rechaza |

No se presupone un modelo ni una tarifa que puedan quedar obsoletos. El proveedor real falla
de forma segura si faltan modelo, clave, activación o tarifas positivas. Para una prueba real,
seleccionar el modelo compatible más económico disponible y comprobar sus tarifas e imputación
de imágenes antes de solicitar autorización. Los costes son estimaciones según las tarifas
configuradas, sin descuentos por caché; la facturación del proveedor es la referencia definitiva.

## Control de costes y pruebas reales excepcionales

Las pruebas habituales y CI bloquean llamadas reales aunque exista una clave o estén activadas
las opciones reales. El runner Django, `conftest.py` y la detección de pytest/unittest/CI activan
el bloqueo. También se protege el transporte del SDK síncrono y asíncrono, cubriendo llamadas
directas fuera del proveedor. Las pruebas verifican que el transporte HTTP no llega a ejecutarse.

Cada solicitud real reserva presupuesto mediante un bloqueo transaccional global. Se comprueba
una estimación conservadora de tokens de texto, esquema, imágenes y salida antes del envío.
Si se conoce el consumo, la reserva se sustituye por su coste estimado. Ante errores sin consumo
conocido se conserva la reserva y se indica que el consumo puede ser desconocido. Las reservas
son límites preventivos estimados; configurar también los límites del proyecto en OpenAI.

Los reintentos del SDK están siempre desactivados. Solo un 429 por límite temporal puede volver
a la cola con espera y número limitado de intentos. No se reintentan automáticamente timeouts,
fallos de red, cuota agotada, respuestas inválidas ni workers interrumpidos: podrían haber generado
un cargo. Un trabajo interrumpido se marca como error tras tres timeouts más 60 segundos. El
gestor puede revisarlo manualmente. Las recargas no programan análisis; POST duplicados reutilizan
el trabajo activo y existe una espera mínima de un minuto para repetir un análisis terminado.

La prueba real está separada de pytest/Django TestCase/CI. **No se ejecutó durante la implementación
inicial; el registro de la prueba autorizada posterior figura al final de este documento.** Antes de
ejecutarla se debe informar del coste, propósito, modelo, presupuesto y número de solicitudes,
y obtener autorización explícita del usuario. La opción CLI refleja esa autorización; no la
sustituye. Tras autorizar, en un entorno manual dedicado, configurar temporalmente el proveedor
real y ejecutar:

```bash
# SOLO después de la autorización explícita del usuario:
python manage.py test_openai_integration ID --budget-usd 0.10 --authorize-cost
```

Máximo una solicitud, una foto de 512 px con detalle bajo, 1200 tokens de salida, presupuesto
máximo autorizado de 1 USD y ningún reintento. El worker no puede consumir este trabajo manual.
El comando impide repetir llamadas reales para una publicación que ya registra un intento real.
Imprime el resultado y el consumo estimado; los errores sin respuesta se consideran de consumo
desconocido. Restaurar después `OPENAI_LIVE_ENABLED=False` y el backend `mock` o `disabled`.

## Reglas, privacidad y limitaciones

`assistant/rules.py` centraliza reglas y versiones. Se distinguen ropa, calzado, eléctricos/RAEE,
electrodomésticos grandes, alimentos, bebidas y plantas; el límite por eje es 50 cm. La excepción
de pequeños electrodomésticos permite su entrega para puntos, nunca su publicación en catálogo.

Las dimensiones registran método, intervalos, evidencia, limitaciones y necesidad de verificación
física. Cada eje se compara en el servidor. La falta de medidas no es una infracción. Una estimación
visual o cercana al límite exige comprobación física. No hay búsqueda técnica externa: se rechazan
medidas supuestamente documentadas por el modelo sin una fuente previamente verificada. Para
incorporar documentación técnica en el futuro se necesitará una fuente validada y una nueva versión
de reglas/prompt; no se implementa una interfaz de reglas ni revisión automática.

Se envían únicamente texto del objeto, categorías/reglas e imágenes reducidas y sin EXIF. Se
omiten campos de cuenta, centro y ubicación; correos/teléfonos reconocibles se redactan en el
texto libre. La redacción por patrones no garantiza eliminar cualquier dato personal incrustado
en texto o imagen: la captura debe evitar documentos personales y personas identificables.
Los contenidos ciudadanos son datos no confiables y las instrucciones del sistema prevalecen.
Las respuestas no pueden ejecutar código ni modificar categorías o estados. Las recomendaciones
se muestran escapadas por Django; la calidad semántica y la resistencia real a instrucciones
maliciosas requieren una validación posterior autorizada, además de los controles estructurales.

La cola está diseñada para un volumen moderado y el funcionamiento concurrente se verifica en
PostgreSQL. SQLite sirve para las pruebas rápidas; sus bloqueos no equivalen a los de PostgreSQL.
El resultado se consulta actualizando la página; no se añade un sistema de notificaciones ni
recargas que puedan iniciar nuevas solicitudes.

## Archivos y validación de la entrega

Nuevos módulos de `assistant/`: modelos, esquema, reglas, instantáneas, proveedores, cola/consumo,
protección de pruebas, fixtures, pruebas y comandos `evaluation_worker`/`test_openai_integration`.
Nuevos: migraciones indicadas, include `templates/moderation/_evaluation.html`, configuraciones
de pruebas, `pytest.ini`, `conftest.py`, `requirements-dev.txt`, Dockerfile/override y este documento.
Modificados: envío ciudadano; modelos/servicio/vistas/URLs/admin de moderación; template de detalle;
CSS y carga de estilos del backoffice; `assistant/apps.py`; settings, requisitos, ejemplo de entorno y `.gitignore`.
La prueba preexistente de concurrencia de puntos se limita a motores con `SELECT FOR UPDATE`:
SQLite producía un fallo intermitente de bloqueo de tabla. Se ejecuta íntegramente en PostgreSQL,
sin cambiar las reglas ni el servicio de puntos.

Verificado: suite Django completa (377 pruebas) sobre PostgreSQL temporal con migraciones; pruebas
de concurrencia de programación, workers y presupuesto; pytest sin clave; validación Docker Compose;
esquema sin migraciones pendientes; panel con datos simulados. Se conservan las pruebas existentes
de estados y de envío, aprobación, rechazo, recepción, puntos, reservas, retirada y permisos.
En pytest/SQLite: 372 pruebas correctas, 5 omitidas por necesitar PostgreSQL/migraciones base,
y 103 subcasos correctos. Las cinco comprobaciones se ejecutan en la suite PostgreSQL.

**Consumo real de la implementación inicial: 0 llamadas, 0 tokens, 0 USD.** Sus verificaciones de
OpenAI usan mocks/fixtures, incluidos errores de red, timeouts, límites, cuota, respuestas malformadas
y el flujo completo. Sin commits, pushes ni despliegues en producción.

### Prueba manual autorizada del producto #165

El usuario autorizó expresamente una única solicitud con `gpt-6-luna`, presupuesto máximo de
0,01 USD y sin reintentos. Se ejecutó en un contenedor efímero separado, con una fotografía
reducida a 512 px, máximo 1200 tokens de salida, razonamiento `none` y tarifa estándar. Los
procesos habituales de aplicación y worker mantuvieron backend `mock`, API real desactivada
y clave vacía.

- Evaluación: #3; solicitud real: 1; reintentos: 0.
- Consumo devuelto por la API: 1645 tokens de entrada y 696 de salida.
- Coste estimado registrado: 0,00051250 USD, dentro del presupuesto autorizado.
- Estado: `error`, código `invalid_response`. La API respondió, pero la validación local no
  aceptó el resultado; no se guardó una evaluación utilizable. El detalle de validación no se
  conservó y no se ha supuesto una causa concreta ni realizado otra llamada para investigarla.
- La publicación conserva el estado administrativo `pending_review`.

La autorización de una solicitud está consumida. La investigación posterior con mocks se detalla
a continuación. Comprobar una evaluación real válida requiere una nueva autorización.

### Corrección de validación y diagnóstico (#165)

La respuesta original de la evaluación #3 no se conservó. Su código genérico no permite
reconstruir la causa exacta y no se consulta ni repite aquella solicitud. Se mantiene el
registro histórico y su consumo sin modificaciones.

Las regresiones con mocks reproducen dos defectos del procesamiento anterior:

- Un resumen general contradictorio provocaba el rechazo de toda una respuesta aunque sus
  comprobaciones fueran válidas. Ahora el servidor determina el resumen después de aplicar
  las reglas dimensionales: conserva incidencias y resultados cautelares, exige verificación
  ante incertidumbre y nunca elimina una incidencia dimensional por falta de medidas.
- Los estados incompletos, los rechazos y las salidas vacías se confundían con JSON inválido.
  Ahora tienen códigos diferenciados; una respuesta parcial nunca se publica como resultado
  válido y estos fallos no provocan reintentos. Se registra el consumo antes de validar.

Se mantienen las validaciones estrictas de tipos, campos obligatorios, categorías permitidas,
intervalos dimensionales y fuentes técnicas. La migración `assistant.0002` añade diagnósticos
acotados a evaluación e intento: fase, regla o ruta/tipo del error; nunca valores, respuesta
completa, mensajes de excepción ni texto del rechazo. Las claves desconocidas se ocultan.
El panel explica los nuevos estados y muestra errores de campos sin exponer su contenido.
Los registros antiguos conservan un diagnóstico vacío y se identifican como tales.

Fixtures y pruebas nuevas cubren resúmenes contradictorios, conservación de incidencias,
JSON truncado, respuesta vacía, rechazo, interrupciones por tokens/filtros, conservación del
consumo, ausencia de reintentos y protección de datos en diagnósticos. La documentación oficial
confirma que las [salidas estructuradas pueden quedar incompletas o incluir rechazos](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).

Esta corrección usa exclusivamente mocks: **0 llamadas nuevas a OpenAI, 0 tokens reales nuevos,
0 USD adicionales**. La comprobación real del comportamiento corregido queda pendiente y
requiere una nueva autorización; la autorización anterior ya se consumió.

Verificación de la corrección: suite Django completa sobre PostgreSQL temporal sin salida a
Internet, **387 pruebas correctas** (incluidas migraciones y concurrencia). Pytest completo en
contenedor sin red y sin clave: **382 correctas, 5 omitidas por requerir PostgreSQL y 124 subcasos
correctos**; esas cinco pruebas pasaron en la ejecución PostgreSQL. Los límites del ledger en
`test_settings` son explícitos para que los overrides locales de presupuesto cero no alteren
las pruebas con proveedores simulados; esto no habilita el acceso a la API.

En Docker local se aplicó `assistant.0002`, se reiniciaron aplicación y worker con los tres archivos
Compose (incluido `compose.mock.yml`) y el worker completó la **evaluación simulada #4 del producto
#165**. El panel respondió HTTP 200 y muestra la simulación y el intento histórico #3. La publicación
sigue `pending_review`; el número total de intentos reales sigue siendo uno. Ambos procesos
mantienen backend `mock`, llamadas reales desactivadas, clave vacía y presupuesto/solicitudes
reales permitidas a cero. Se guardó una copia de la base antes de la migración en
`/private/tmp/trueca-invalid-response-tests/before-error-diagnostics.dump` (respaldo temporal local).

### Validación real posterior de la corrección (#165)

El usuario autorizó de nuevo solicitudes reales con `gpt-6-luna` para comprobar la corrección
sobre el producto #165, con presupuesto máximo total de 1 USD. Se hizo **una sola solicitud**
en un contenedor efímero, sin reintentos, con una foto a 512 px/detalle bajo, máximo 1200 tokens
de salida, razonamiento `none`, tarifa estándar y reserva conservadora de 0,01 USD. La nueva
autorización quedó auditada y el lanzador usó un marcador exclusivo para impedir repeticiones.
No se cambió la configuración de los procesos habituales ni se alteró el intento histórico #3.

- Nueva evaluación real: **#5, completada**, sin errores de esquema o validación.
- Resultado general: `issues`; dimensiones: `NO_VERIFICABLE`.
- Consumo real conocido: **1645 tokens de entrada y 685 de salida**.
- Coste estimado: **0,00050700 USD**; muy inferior al máximo autorizado de 1 USD.
- Tarifas comprobadas en [OpenAI Docs](https://developers.openai.com/api/docs/pricing):
  0,10 USD por millón de tokens de entrada y 0,50 USD por millón de salida (contexto corto,
  procesamiento estándar). La estimación usa los tokens devueltos por la API y esas tarifas.
- Solicitudes reales nuevas: **1**; reintentos: **0**. No se hicieron más solicitudes al
  completarse correctamente la comprobación. El total histórico del proyecto pasa a dos
  solicitudes reales, contando el intento anterior fallido.
- Se conserva la batería previa de mocks: Django/PostgreSQL 387 pruebas correctas;
  pytest 382 correctas, 124 subcasos y 5 omitidas que pasaron en PostgreSQL.

Esta prueba confirma que el procesamiento corregido admite y guarda una evaluación real válida
para este producto. No permite reconstruir el fallo original, cuya respuesta no se conservó,
ni sustituye las pruebas con mocks de los escenarios de error. La comprobación real solicitada
queda completada; no se ha ejecutado una batería real adicional de escenarios ni fotografías.

### Verificación explícita de subcategoría

La publicación guarda el nodo final seleccionado en `category_id`: por ejemplo, en el producto
#165 es «Otros», dentro de «Hogar». Ya existía una comprobación `checks.subcategory`, pero la
entrada no distinguía explícitamente la categoría raíz y el nodo seleccionado. Ahora la
instantánea y la entrada incluyen `classification.category`, `classification.subcategory` y
`classification.path`, con nombres, IDs y jerarquía, además de disponibilidad y obligatoriedad.

El prompt `assisted-2-subcategory` y las descripciones del esquema exigen evaluar ambos niveles
por separado: una categoría correcta puede tener una subcategoría incorrecta. La comprobación
de subcategoría sigue siendo obligatoria. Su pertinencia depende del objeto y de la evidencia;
si no puede verificarse, se informa `NO_VERIFICABLE`. El servidor controla además los casos
estructurales: subcategoría obligatoria ausente, selección inactiva y categoría sin subniveles
(`No aplica`). Las sugerencias mantienen la restricción a IDs públicos vigentes y no modifican
la clasificación ni la decisión administrativa automáticamente.

El panel muestra la categoría raíz y la ruta completa de subcategoría junto a sus comprobaciones.
Los cambios de subcategoría, de jerarquía o de versión del prompt invalidan la vigencia de una
evaluación anterior; sus resultados siguen disponibles en el historial. El proveedor simulado
identifica los nombres de ambos niveles y pide su comprobación manual sin afirmar que analiza
las fotografías. La nueva funcionalidad se ha implementado y probado con mocks, sin llamadas
adicionales a OpenAI.

Verificado con **398 pruebas Django correctas** sobre PostgreSQL temporal (incluidas migraciones
y concurrencia) y **393 pruebas pytest correctas, 124 subcasos y 5 omitidas** que pasaron en
PostgreSQL. Las suites se ejecutaron sin clave y sin acceso a Internet. Las regresiones específicas
cubren categoría correcta/subcategoría incorrecta, selección ausente, nodos inactivos, raíces sin
subniveles, rutas profundas, cambio de subcategoría, cambio de padre, campo obligatorio de respuesta
y presentación independiente en el panel.

El entorno local conserva backend `mock`. El worker completó la nueva evaluación simulada **#6**
del producto **#165**, con «Hogar» y «Hogar / Otros» como selecciones evaluadas. La evaluación
real #5 sigue guardada en el historial; no se adapta retroactivamente a las nuevas instrucciones.
**Consumo adicional de este cambio: 0 llamadas reales, 0 tokens reales, 0 USD.** La nueva validación
se comprobó mediante mocks; su comprobación con un modelo real no se ejecutó.

### Solicitud de comprobación real de subcategoría y corrección del esquema

El usuario pidió evaluar de nuevo el #165 con `gpt-6-luna` dentro del presupuesto autorizado
acumulado de 1 USD. Se lanzó un único intento real (#7), con foto a 512 px/detalle bajo,
salida máxima 1200 tokens, tarifa estándar, razonamiento `none` y cero reintentos. El resto del
entorno conservó backend `mock`, clave vacía y llamadas reales deshabilitadas.

El intento terminó en `provider_error`, sin resultado ni datos de uso devueltos. **Los tokens
y el coste de ese intento son desconocidos**, no se consideran cero. Se conserva la reserva de
0,01 USD. El coste conocido anterior es 0,00101950 USD; sumando la reserva desconocida se
contabilizan conservadoramente 0,01101950 USD del presupuesto. Los tres intentos reales
históricos permanecen en la base; no se ha sustituido el fallo por un resultado simulado.

La revisión offline encontró que las nuevas descripciones Pydantic producían propiedades con
`$ref` y `description` simultáneas. El normalizador de esquema estricto del SDK elimina esa
combinación; ahora se utiliza tanto en la petición como en el cálculo de reserva. Se mantienen
`responses.create` y la validación local para conservar el uso incluso ante salidas inválidas.
La causa exacta del intento #7 no puede confirmarse porque el error original no guardó detalle.
También se añaden diagnósticos HTTP seguros (estado, código/parámetro en listas permitidas),
clasificación de errores de autenticación, permisos, modelo y servidor, y regresiones para
esquemas estrictos y cuerpos HTTP malformados. No se guardan mensajes ni valores sensibles.

Validado offline: 401 pruebas Django sobre PostgreSQL, 396 pruebas pytest y 5 omitidas que
pasaron en PostgreSQL. Se verificó además la regresión de cuerpos HTTP malformados con mocks.

Se preparó una segunda solicitud manual para comprobar el esquema corregido, con otra reserva
de 0,01 USD: máximo comprometido acumulado 0,02101950 USD. **No se ejecutó**: la revisión
automática de aprobación la rechazó por considerar que la autorización no cubría claramente
un segundo envío del producto después del fallo. El lanzador está preparado en
`/private/tmp/trueca-real-165-subcategory-fixed-20261020/authorized_once.py`, sin clave en el
archivo, y requiere confirmación explícita del usuario antes de ejecutarse. La comprobación
real de subcategoría sigue pendiente; no se ha realizado otra llamada para depurar el fallo.

### Confirmación explícita y evaluación real de subcategoría completada

Tras el bloqueo previo, el usuario autorizó expresamente nuevas solicitudes reales con
`gpt-6-luna` para el producto #165 y su subcategoría, manteniendo el presupuesto acumulado
de 1 USD. Se ejecutó únicamente la solicitud preparada, con una foto a 512 px/detalle bajo,
máximo 1200 tokens de salida, razonamiento `none`, procesamiento estándar y cero reintentos.
La autorización posterior al fallo #7 quedó registrada en auditoría. El contenedor fue efímero;
se retiraron el lanzador y el override real al finalizar, conservando su marcador de consumo.

- **Evaluación #8: completada**, sin errores de proveedor, esquema ni validación.
- La respuesta distingue la categoría raíz «Hogar» y la subcategoría «Hogar / Otros»;
  marca `INCIDENCIA` en ambas y propone considerar el ID público 28, «Juguetes», dentro de
  «Niños y bebes». Las sugerencias no cambian la categoría ni aprueban la publicación.
- Una solicitud real nueva; cero reintentos automáticos.
- Uso conocido de esta solicitud: **2080 tokens de entrada y 1097 de salida**.
- Coste estimado de esta solicitud: **0,00075650 USD**, calculado con las tarifas estándar
  verificadas en [OpenAI Docs](https://developers.openai.com/api/docs/models/gpt-6-luna):
  0,10 USD/millón de entrada y 0,50 USD/millón de salida.
- Coste conocido histórico acumulado: **0,00177600 USD**. Se conservan **0,01 USD reservados**
  por el intento #7, cuyo consumo sigue siendo desconocido. El importe contabilizado de forma
  conservadora es **0,01177600 USD**, dentro del máximo autorizado de 1 USD.
- Hay cuatro intentos reales históricos en total. Solo uno pertenece a esta nueva ejecución.

La comprobación real solicitada de categoría y subcategoría queda completada para el #165.
Se mantienen las pruebas offline previamente verificadas: 401 pruebas Django/PostgreSQL y
396 pytest, con cinco omitidas que pasaron en PostgreSQL. No se repitieron solicitudes para
otros escenarios; estos permanecen cubiertos con mocks. El entorno habitual conserva backend
`mock`, clave vacía, llamadas reales desactivadas y límites reales a cero. La publicación
continúa `pending_review` con su clasificación original; los intentos anteriores se conservan
en el historial.

### Cambio de clasificación solicitado y nueva evaluación real (#9)

Por petición explícita del usuario, se cambió el producto #165 de la subcategoría 34,
«Hogar / Otros», a la subcategoría pública activa 28, «Niños y bebes / Juguetes» (raíz 27).
El cambio se registró en `publication.category.changed`, con el ID anterior y el nuevo.
La evaluación #8 conserva su instantánea original y pasa a ser histórica para la publicación
modificada; no se reescriben sus comprobaciones.

El usuario autorizó repetir la evaluación con `gpt-6-luna` dentro del presupuesto acumulado
de 1 USD. Se hizo **una solicitud nueva**, en contenedor efímero, con una foto a 512 px/detalle
bajo, salida máxima de 1200 tokens, razonamiento `none`, procesamiento estándar y sin reintentos.
La nueva instantánea identifica por separado la categoría «Niños y bebes» y la subcategoría
«Juguetes». La autorización, los límites y el presupuesto quedaron auditados.

- Evaluación **#9 completada**, sin errores de proveedor, esquema ni validación.
- Categoría raíz: **CORRECTO**. Subcategoría: **CORRECTO**.
- Resultado general: `issues`, por discrepancias del título y descripción con la fotografía;
  quedan pendientes comprobaciones del estado/integridad y de las dimensiones del objeto.
- Uso conocido de la nueva solicitud: **2090 tokens de entrada y 700 de salida**.
- Coste estimado nuevo: **0,00055900 USD**, según las tarifas estándar ya verificadas.
- Coste conocido histórico acumulado: **0,00233500 USD**. Se conservan **0,01 USD reservados**
  por el intento #7, cuyo consumo continúa desconocido. Total contabilizado conservador:
  **0,01233500 USD**, dentro del máximo acumulado autorizado de 1 USD.
- Intentos reales históricos: cinco; solo uno corresponde a esta petición. Reintentos: cero.

La publicación mantiene el estado `pending_review` y la nueva clasificación; la evaluación no
la aprueba automáticamente. El entorno habitual sigue en modo `mock`, con clave vacía, API real
deshabilitada y límites reales a cero. Se retiraron el lanzador temporal y el override real tras
la prueba. La comprobación real solicitada de la nueva clasificación queda completada.
No hubo cambios de código ni nuevas pruebas automatizadas en esta petición: se conserva la
batería offline previamente validada (401 pruebas Django/PostgreSQL y 396 pytest, con cinco
omitidas que pasaron en PostgreSQL).

### Corrección del título y descripción según la fotografía; evaluación real #10

Por petición explícita del usuario, se revisó localmente la fotografía original del producto
#165. Muestra una figura de robot con accesorios sobre fondo negro e incluye un aviso de que
el producto real puede diferir. No permite certificar estado real, contenido entregado ni medidas.
Se corrigieron los textos y se registró `publication.content.changed`, conservando el antes y
después en auditoría:

- Título: «Figura de robot con accesorios».
- Descripción: «Figura de robot en tonos azul y turquesa, con detalles rojos, plateados y
  amarillos. La fotografía de presentación muestra la figura junto a varios accesorios. El
  estado real, las piezas incluidas y las dimensiones están pendientes de comprobar.»

Se mantuvieron la clasificación «Niños y bebes / Juguetes» y el estado `pending_review`.
La evaluación #9 conserva la instantánea anterior en el historial. Con autorización explícita
se realizó una nueva solicitud real con `gpt-6-luna`, una foto a 512 px/detalle bajo, máximo
1200 tokens de salida, razonamiento `none`, tarifa estándar y sin reintentos.

- **Evaluación #10 completada**, sin errores de proveedor, esquema o validación.
- Título, categoría y subcategoría: **CORRECTO**.
- Descripción: **INCIDENCIA**. La respuesta reconoce que es coherente con la imagen, pero
  indica que faltan el estado real y la lista de accesorios efectivamente entregados.
- Calidad de foto: **INCIDENCIA**, por las limitaciones de una imagen de presentación para
  comprobar estado, parte trasera y contenido. Dimensiones: **NO_VERIFICABLE**.
- Una solicitud real nueva; cero reintentos. Uso conocido: **2136 tokens de entrada y 907 de salida**.
- Coste estimado nuevo: **0,00066710 USD**, con las tarifas estándar ya verificadas.
- Acumulado conocido: **0,00300210 USD**; reserva conservada por consumo desconocido del #7:
  **0,01 USD**. Total contabilizado conservador: **0,01300210 USD**, dentro del máximo acumulado
  autorizado de 1 USD. Intentos reales históricos: seis; solo uno pertenece a esta petición.

No se inventaron condición, autenticidad, modelo, piezas incluidas, funcionamiento ni medidas
para obtener un resultado favorable. La comprobación real solicitada queda completada, con
las incidencias y verificaciones físicas pendientes expresamente registradas. No se hicieron
más solicitudes. El contenedor efímero, el lanzador y el override real se retiraron al finalizar.
Aplicación y worker habituales mantienen backend `mock`, clave vacía, llamadas reales
desactivadas y límites reales a cero. No hubo cambios de código ni nuevas pruebas automatizadas;
se conserva la cobertura offline previa (401 pruebas Django/PostgreSQL y 396 pytest, con cinco
omitidas que pasaron en PostgreSQL).

### Decisión de moderación antes de la evaluación y propuesta editable

La tarjeta «Decisión de moderación» se sitúa inmediatamente antes de «Evaluación automática»
en la columna principal, tanto en escritorio como en móvil. Los historiales permanecen en la
columna secundaria. El formulario muestra un grupo de radios con la propuesta marcada y un
borrador editable de «Motivo» e «Indicaciones», calculados localmente a partir de la evaluación
vigente y completada, sin volver a consultar OpenAI ni cambiar su esquema o prompt.

Las reglas de la propuesta son:

- `Rechazar` cuando la comprobación de artículos prohibidos señala una incidencia.
- `Aprobar` solo si todas las comprobaciones son correctas, el resultado es aparentemente
  adecuado y no hay incidencias, aspectos no verificables ni verificación física pendiente.
- `Solicitar cambios` para las demás incidencias o incertidumbres, con los motivos y las
  recomendaciones guardadas. Si no hay recomendaciones textuales, se proponen indicaciones
  concretas para completar descripción, fotos o medidas según las comprobaciones pendientes.

Las sugerencias están identificadas como propuestas basadas en una evaluación concreta; las
procedentes de mocks indican que son simuladas. No hay propuesta ni valores preseleccionados
cuando falta una evaluación vigente/completada, está obsoleta o la publicación ya se resolvió.
Los borradores se limitan al máximo de 2000 caracteres y se muestran escapados. Los datos de
un POST humano, incluso inválido, prevalecen sobre el borrador: no se sustituyen su elección
ni sus notas. La selección puede cambiarse y solo el botón «Registrar decisión» ejecuta el
servicio existente, con sus controles de autorización, ámbito y CSRF.

Para el #165, la evaluación vigente #10 propone **Solicitar cambios**, por la información de
estado y accesorios que falta, las limitaciones de las fotos y la verificación de dimensiones.
No se ha registrado ninguna decisión ni modificado el producto para preparar el formulario.
La evaluación guardada y sus consumos se conservan.

Pruebas con fixtures y sin red: **412 pruebas Django/PostgreSQL correctas** y **407 pytest
correctas, 138 subcasos y cinco omitidas** que pasaron en PostgreSQL. Las once regresiones nuevas
cubren las tres propuestas, ausencia/error/obsolescencia de evaluación, precedencia de tarjetas,
radios marcados, texto propuesto, simulación, límites/escape y conservación de la elección y
notas humanas tras un POST. La versión final del borrador sin indicaciones duplicadas se volvió
a comprobar con las once regresiones sobre PostgreSQL.

**Consumo adicional de este cambio: 0 llamadas reales, 0 tokens reales, 0 USD.** La funcionalidad
se verifica sobre resultados guardados y mocks, sin prueba real pendiente para el cambio de
interfaz. Aplicación y worker permanecen en modo simulado y la rama de trabajo es `openai`.

### Borradores independientes al cambiar de decisión

Cada opción («Solicitar cambios», «Aprobar» y «Rechazar») dispone de su propio motivo e
indicaciones, derivados localmente de la evaluación vigente. La recomendación original
permanece visible. Al elegir una alternativa, el borrador distingue esa elección humana de
la recomendación guardada y solicita justificarla; no inventa hechos verificados ni afirma
que se han resuelto las incidencias.

El navegador conserva por separado el texto de cada opción mientras la página siga abierta.
Al volver a una opción recupera exactamente sus ediciones, incluidos saltos de línea, espacios
y texto vaciado voluntariamente. El texto devuelto por un error de validación prevalece al
inicializar el formulario. Si aún no hay elección, el texto manual se conserva en la primera
opción seleccionada. No se usa almacenamiento persistente: recargar o abandonar la página
descarta los borradores sin registrar. Sin una evaluación válida el formulario sigue manual.
Los borradores se serializan mediante `json_script` y se aplican solo a `textarea.value`.

Verificación: 13 pruebas Django de propuestas, 7 pruebas de eventos del formulario con Node
(`node --test moderation/tests/js/test_decision_drafts.cjs`) y suite completa pytest en un
contenedor sin red: **409 correctas, 141 subcasos y 5 omitidas específicas de PostgreSQL**.
La comprobación en el panel del #165 confirmó los textos adaptados y la conservación de
ediciones y campos vaciados al cambiar varias veces entre las tres opciones. Se retiraron
los textos temporales de la comprobación y se restauró el borrador original, sin enviar el
formulario. El producto permanece pendiente, sin decisiones, con la evaluación #10 guardada.

Aplicación y worker siguen con backend `mock`, llamadas reales deshabilitadas y clave vacía.
El contador histórico continúa en 6 intentos reales: **este cambio añade 0 llamadas, 0 tokens
y 0 USD**. No hay ninguna prueba real pendiente para esta funcionalidad.

### Optimización local de fotografías para IA

La preparación original estaba en `assistant.providers.image_content`, justo antes de
`OpenAIProvider.evaluate` / `client.responses.create`. Se ha extraído a
`assistant.images.optimize_image`: devuelve un `BytesIO` JPEG y solo se invoca al construir
el contenido del proveedor real. El proveedor simulado no lee ni convierte los archivos.
No se han cambiado modelos, almacenamiento, reglas, prompts, dependencias ni interfaz.

La derivación aplica orientación EXIF antes de reducir, conserva las proporciones y no amplía
imágenes pequeñas. Usa LANCZOS, calidad 80 y fondo blanco para transparencia RGBA, LA, paleta y
WebP. Genera píxeles nuevos sin EXIF, GPS, comentarios, perfiles ICC ni texto PNG. No crea archivos
temporales, ni guarda, sustituye o elimina fotografías; las pruebas comparan nombre, hash de
contenido, fecha de actualización y dimensiones/tamaño almacenados antes y después.

La configuración de imágenes se valida al preparar una evaluación. Valores inválidos hacen
fallar la asistencia, sin impedir abrir o resolver manualmente la publicación. Formato de salida
distinto de JPEG o desactivar la eliminación de metadatos se rechazan: son requisitos de las
derivaciones enviadas, no opciones para transmitir originales. Los formatos de entrada admitidos
son los ya usados en publicaciones (JPEG, PNG, WebP); no se admiten animaciones. Se limitan bytes
leídos y dimensiones/píxeles decodificados con los límites existentes de publicaciones; se
convierten en errores las advertencias de descompresión excesiva de Pillow. Los errores usan
`image_preparation_failed` y una razón fija saneada, sin rutas, contenido ni mensajes de excepción.
Si falla cualquiera de las fotos seleccionadas, no se crea el cliente SDK ni se envía una petición
parcial u original. Se conserva la contabilidad preventiva de intentos del flujo existente.

`assistant.images.evaluation_photos` selecciona en SQL la principal primero y después
`sort_order, pk`, con el límite configurable aplicado antes de cargar/preparar los archivos.
El adaptador aplica también el máximo al recibir directamente una lista/iterador. La prueba
manual excepcional sigue limitada a una foto, máximo 512 px (o el límite configurado si es menor),
detalle bajo y sin reintentos. Las evaluaciones ordinarias usan inicialmente detalle alto.

Se reutiliza la huella SHA-256 existente (datos y referencias/fechas de todas las fotografías).
Solo se ajusta su orden para reflejar la prioridad de la principal. Si cambia ese orden respecto
a un resultado antiguo, este puede quedar histórico; no se programa ninguna reevaluación al
consultarlo. No se añade hashing remoto, caché duplicada ni análisis recurrente. Una sustitución
de archivo a través del almacenamiento normal cambia su referencia/fecha; modificaciones externas
que sobreescriban bytes sin actualizar esas referencias no las detecta la huella existente.

Limitaciones: limitar a tres fotos puede dejar detalles relevantes fuera; el gestor puede ajustar
el máximo o revisar todas las originales desde el panel. JPEG es con pérdida y elimina perfiles
de color; no garantiza menor peso en archivos que ya eran pequeños o estaban muy comprimidos.
El peso en bytes no equivale a tokens facturados: el consumo visual depende del modelo,
dimensiones y `detail`. No se promete menor coste por imagen al pasar del antiguo detalle bajo
al alto; deben mantenerse las reservas y tarifas existentes. Los valores de detalle admitidos
dependen del modelo y no se comprueban con solicitudes remotas. Referencia:
[OpenAI Docs: imágenes y niveles de detalle](https://developers.openai.com/api/docs/guides/images-vision).

Validación de esta entrega: **426 pytest correctas, 166 subcasos y 5 omitidas específicas de
PostgreSQL**, con el contenedor de pruebas sin red y sin clave. Las 17 pruebas nuevas cubren
orientación con posición de colores, proporciones, tamaños, transparencia/modos, metadatos,
bytes/píxeles excesivos, animación/corrupción, configuración, selección/prioridad/límite, petición
SDK completamente simulada, originales intactos, errores con revisión manual, deduplicación y
la excepción manual de coste controlado. Consumo nuevo: **0 llamadas reales, 0 tokens, 0 USD**.
No se ha hecho una evaluación real; la calidad semántica con fotografías reales no se ha medido
contra OpenAI. La comprobación real no es necesaria para verificar esta implementación.

### Script de actualización de producción

`deploy/update.sh` se instala como `/srv/docker/tenetrueca/update.sh`, fuera del checkout
`app/`, y se ejecuta como el usuario propietario del repositorio. Requiere el `.env` real ya
configurado (`live`, `gpt-6-luna`, clave de servidor, tarifas y límites positivos, una tentativa).
No escribe variables, claves ni tarifas en el archivo. Conserva el Compose base del servidor,
incluido el puerto ligado a `127.0.0.1`, añadiendo únicamente `app/compose.openai.yml`.
No incluye el override local de simulación.

El script usa bloqueo para impedir dos actualizaciones simultáneas y exige un checkout limpio.
Descarga `origin/openai` y permite únicamente avance directo, sin descartar commits. Detecta
migraciones ignoradas que serían sustituidas por versiones diferentes del remoto. Detiene
worker y aplicación, manteniendo PostgreSQL; guarda el dump y un archivo del código/fotos,
configuración local, estáticos y migraciones antiguas, más `.env`, Compose y revisión previa,
en `backups/FECHA-PID/` con permisos privados. No copia directamente `pg_data` mientras la
base está arrancada. Después actualiza la rama, comprueba dependencias de migraciones,
reconstruye imágenes, valida la configuración, verifica que no faltan migraciones de modelos,
aplica las existentes y recoge estáticos.

Las operaciones administrativas usan contenedores efímeros con OpenAI deshabilitado, clave
vacía y bloqueo de SDK por CI. La validación de la configuración real usa también CI, lee los
ajustes sin mostrar la clave y no instancia el cliente. No ejecuta pruebas, evaluaciones,
`evaluation_worker --once` ni el comando de integración real. Finalmente recrea app y worker
con el `.env` real. **A partir de ese arranque, el procesamiento normal de trabajos pendientes
puede generar consumo**; el arranque no es una solicitud de prueba.

Ante un fallo el script se detiene e informa de la etapa y de la copia disponible. No realiza
rollback ni vuelve a arrancar automáticamente servicios detenidos: tras una migración parcial
o incompatible es necesaria una revisión del error. Las copias se conservan sin purga automática;
hay que disponer de espacio y gestionarlas con la política de backups del servidor. El servidor
de estáticos debe continuar sirviendo `app/staticfiles/`.

Validación del script: sintaxis Bash y del Python incrustado, y ocho escenarios de shell con
Docker/Git simulados (éxito, checkout sucio, bloqueo ocupado, rama divergente, colisión de
migración ignorada, migración base ausente, configuración inválida y fallo al migrar). Se ha
comprobado además que el validador acepta la configuración real esperada y rechaza `mock` en
un contenedor sin red, con clave ficticia y el SDK bloqueado por CI. No se ha ejecutado el
despliegue contra el servidor ni alterado los servicios locales. **0 llamadas reales nuevas.**
