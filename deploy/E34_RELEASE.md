# Promoción de la candidata E34 — herramientas preparadas, no ejecutadas

Este procedimiento afecta exclusivamente a `app`. No incluye `compose.openai.yml`, no
reconstruye imágenes, no actualiza Git y no arranca, detiene ni recrea el worker de moderación.
Tampoco reinicia PostgreSQL. Requiere Linux AMD64, Python 3.8+, Docker y Compose >=2.24.4,
Git y tar existentes; no instala dependencias. El usuario necesita acceso a Docker y
permisos de lectura de los archivos del entorno y de escritura en backups/estáticos.

## Artefacto y disposición

```text
trueca-candidate@sha256:dd28d44c918e3359c1d5b83ce64ec5be2447929641f798b957d60dd7f51814a4
```

Antes de ejecutar en una tarea autorizada, distribuir la imagen conservando el digest y
verificarla en la VPS. El script nunca hace pull, build ni recurre a un tag mutable.
`--image REGISTRO/REPOSITORIO@sha256:…` permite cambiar el repositorio, pero exige ese digest.

Copiar las herramientas fuera del checkout operativo, conservando esta disposición:

```text
/srv/docker/tenetrueca/releases/e34-tooling/
  compose.release-e34.yml
  deploy/e34_release.py
```

Así no se actualiza `app/`, que puede estar montado también por el worker. El commit
esperado del checkout operativo es `2b3e1ac0bff6be219f06027ddd0b0511a764fd34` por defecto.
Si el servidor ya tiene otro commit, revisarlo y pasarlo expresamente mediante
`--expected-current-commit SHA_COMPLETO`. No cambiarlo únicamente para saltarse un rechazo.
El script se entrega desde Git, pero ejecuta el código del artefacto, no el checkout.
Por ello el commit del host puede seguir siendo `2b3e1ac…` tras la promoción: la versión
web activa la identifica el digest/ID y `state.json`, no ese checkout reservado al entorno
anterior. Se rechaza volver a desplegar la misma imagen como si fuera una versión nueva.

El Compose base debe seguir en `RAIZ/docker-compose.yml`, con `.env` en esa raíz y servicios
`app` y `db`. La identidad del proyecto se obtiene del contenedor actual, sin inventar un
proyecto nuevo. Por defecto se esperan `tenetrueca_app`, `tenetrueca_db` y, si existe,
`tenetrueca_evaluation_worker`; hay opciones explícitas para nombres distintos.

## Configuración y datos conservados

El override elimina `build` y reemplaza el bind completo de `/app` por cuatro binds:

- `app/tenetrueca/local_settings.py` → configuración operativa, solo lectura.
- `app/media` y `app/private_media` → archivos existentes.
- `app/staticfiles` → estáticos recogidos desde la candidata.

No se crean estas rutas automáticamente ni se cambian sus propietarios/permisos. Deben
existir, corresponder a los datos actualmente montados y ser accesibles al UID/GID 10001.
El preflight comprueba accesibilidad de las raíces; revisar además permisos de subdirectorios
y archivos existentes antes de la ventana. Rutas personalizadas, volúmenes de datos con
nombre, entrypoints personalizados o symlinks de medios/estáticos requieren adaptar y revisar
el procedimiento, no migrar ni copiar datos automáticamente.
Tampoco se admiten symlinks para el checkout `app/` o el directorio local `backups/`,
para que el archivo de respaldo y la ruta de recuperación correspondan al mismo árbol.

Se conservan los puertos, redes y configuración base. Se rechazan diferencias respecto al
contenedor actual. Se comprueba que las variables compartidas `OPENAI_*`/`AI_*` del proceso
web no cambien; no se altera la configuración del worker. Las variables propias de
moderación nunca se fuerzan en la aplicación persistente.

**La promoción inicial deja `CHATBOT_ENABLED=False`, proveedor mock y evaluación real
deshabilitada**, también verificados en los settings efectivos. No muestra el widget ni
habilita tráfico facturable. Su activación posterior requiere revisar configuración y
autorizarla expresamente; no debe hacerse editando estas herramientas durante un despliegue.

`TRUECA_E34_HEALTH_HOST` se suministra mediante `--health-host`; debe estar incluido
explícitamente en `ALLOWED_HOSTS`. La comprobación consulta `/health/` por loopback con ese
Host y `X-Forwarded-Proto: https`, sin seguir redirecciones. Revisar la configuración real
del proxy HTTPS y `SECURE_PROXY_SSL_HEADER` antes de desplegar.

## Comandos para una ejecución futura autorizada

**No ejecutados durante la preparación de estas herramientas.** Sustituir hostname y rutas.

Preflight:

```sh
python3 /srv/docker/tenetrueca/releases/e34-tooling/deploy/e34_release.py preflight \
  --root /srv/docker/tenetrueca --health-host trueca.ejemplo.org
```

El preflight crea un contenedor diagnóstico efímero `--no-deps` y realiza lecturas de la
DB. No migra, detiene servicios ni inicia workers. Usa CI y clave vacía; no es una simulación
puramente local del CLI. Revisa arquitectura/digest, settings, identidad de DB, sesiones,
manifiesto completo de migraciones, archivos históricos presentes, puertos, redes, espacio
libre, permisos y receta de rollback. Solo admite pendientes estas tres migraciones:
`chatbot.0001_initial`, `chatbot.0002_chatmessage_sources`, `chatbot.0003_evaluation_budget`.
Cualquier otra pendiente requiere revisión separada. No se usa `--fake` ni `makemigrations`.
Debe ejecutarse directamente en la VPS Linux con un endpoint Docker Unix local; se
rechazan contextos remotos para no respaldar archivos de otra máquina por error.

Deploy:

```sh
python3 /srv/docker/tenetrueca/releases/e34-tooling/deploy/e34_release.py deploy \
  --root /srv/docker/tenetrueca --health-host trueca.ejemplo.org \
  --maintenance-confirmed
```

La opción confirma que el operador ya ha restringido tráfico y pausado otros escritores.
No configura el proxy ni pone por sí sola el sitio en mantenimiento. **Si algún worker de
moderación del proyecto está activo, se rechaza la operación sin detenerlo.** Se localizan
tanto el nombre indicado como los workers etiquetados del proyecto y se comparan durante
la operación. La ventana debe organizarse externamente respetando esa restricción.

Secuencia:

1. Bloqueo exclusivo `.update.lock`, compatible con el script anterior, y preflight.
2. Metadatos de recuperación privados antes de detener la app.
3. Detener únicamente el ID inspeccionado de `app`.
4. Backup de imagen anterior, checkout/configuración/archivos, estáticos y PostgreSQL.
5. Verificar catálogo del dump y restaurarlo con `--exit-on-error` en otro PostgreSQL
   efímero, usando la imagen local del DB actual. Está sin red/puertos ni montajes operativos,
   con disco efímero, RAM limitada a 512 MiB y una CPU. Se eliminan solo ese contenedor y
   sus volúmenes anónimos propios. Si requiere más recursos o roles especiales, aborta
   antes de migrar; revisar la verificación, sin omitirla.
6. Persistir hashes y estado. Ejecutar checks y migraciones forward explícitas con IA
   deshabilitada, después `collectstatic`.
7. Recrear exclusivamente `app` con `--no-deps --no-build --pull never`, esperar salud,
   verificar ID ejecutado y conservación del DB/worker.

No se ejecutan `down`, limpiezas globales, restauraciones sobre la DB real ni pruebas OpenAI.
La estimación previa de espacio es conservadora, no garantiza que no pueda agotarse el
disco. Se conserva el fallo y la ruta de recuperación; nunca se continúa tras un error.

## Backups y fallos

Directorio `RAIZ/backups/e34-FECHA-IDENTIFICADOR/`, modo 0700 y archivos privados. Incluye:

- `state.json`, hashes, paso alcanzado y estado de migraciones/estáticos.
- `rollback.compose.json`: solo app, imagen anterior por ID, entorno resuelto, puertos,
  montajes y redes anteriores; sin build ni migración automática al arrancar.
- `database.dump`, su catálogo y `app.tar.gz` con configuración/medios/migraciones ignoradas.
- `staticfiles.tar`, `previous-image.tar` y copias de `.env`, Compose y `local_settings.py`.

Estos archivos contienen secretos y datos reales. Protegerlos, conservarlos fuera del
servidor según la política existente y no añadirlos a Git ni publicarlos. El dump conserva
propietarios/permisos para recuperación; la verificación aislada omite owners/grants.
El backup no incluye globals/roles del clúster PostgreSQL, que se conservan en el servidor
existente; una recuperación completa en otro clúster necesitaría también esos roles.
El ID de la restauración efímera se guarda en el estado mientras existe. SIGTERM/SIGINT
intentan limpiar ese recurso en `finally`; tras una caída de máquina, SIGKILL o fallo de
Docker, revisar ese ID específico antes de retirar recursos. No realizar limpieza global.

Ante fallo o interrupción, el script conserva estado/backup y no hace rollback automático.
Puede quedar la app detenida o una candidata sin salud. Mantener mantenimiento y revisar
el paso guardado antes de invocar rollback. No reiniciar el script de deploy a ciegas.

## Rollback de aplicación y estáticos

```sh
python3 /srv/docker/tenetrueca/releases/e34-tooling/deploy/e34_release.py rollback \
  --root /srv/docker/tenetrueca --health-host trueca.ejemplo.org \
  --backup /srv/docker/tenetrueca/backups/e34-FECHA-IDENTIFICADOR \
  --maintenance-confirmed
```

Verifica integridad del backup, identidad del DB y worker, configuración y commit del host
sin cambios y que no se haya sustituido la app por otro despliegue. Recupera la imagen
anterior desde el archivo si ya no está local. Recrea solo app, con el entorno y montajes
guardados, y arranca Gunicorn **sin ejecutar migrate**. Comprueba `/health/` antes de concluir.

Si se inició `collectstatic`, repone los archivos anteriores desde `staticfiles.tar` sin
borrar archivos adicionales ni medios. No extrae `app.tar.gz` sobre el checkout ni sobrescribe
configuración cambiada externamente. Si el backup quedó incompleto antes de las migraciones,
usa los metadatos preparados previamente; no intenta restaurar estáticos que no se tocaron.

**No restaura PostgreSQL, no revierte migraciones y no borra conversaciones ni uploads.**
Las migraciones permitidas añaden tablas/campos del chatbot y se conservan al volver a la
aplicación anterior. Si hace falta restaurar DB por otro incidente, requiere un procedimiento
manual autorizado que contemple las escrituras posteriores; nunca sobreescribir con el dump
automáticamente. Tampoco se modifica o arranca moderación durante rollback.

Antes de retirar mantenimiento, comprobar mediante el proxy real: salud, portada,
CSS/JS, login y configuración esperada del chatbot deshabilitado. Esta herramienta solo
comprueba salud interna, no certifica TLS, proxy, cachés ni permisos completos de todos
los archivos. La activación del asistente se revisará aparte, sin fixtures sintéticos en
la DB real y sin solicitudes facturables no autorizadas.

## Validación de la preparación

Se permite comprobar sintaxis y probar funciones puras sin ejecutar estas herramientas.
`deploy/tests/test_e34_release.py` prohíbe subprocesses durante import y tests; cubre
rollback sin migraciones, escape de secretos con `$`, conservación de puertos/volúmenes,
rechazo de binds/build/activación inesperados y metadatos privados.
No se invocan `preflight`, `deploy`, `rollback`, Docker ni Compose durante estas pruebas.
La integración real de estas herramientas con el servidor queda pendiente de autorización.

Resultado de esta preparación: **8 tests offline correctos**, sintaxis Python compatible
con la gramática 3.8+ y YAML válido mediante su AST (tags Compose conservados). Los tests
bloquean cualquier subprocess y no ejecutan el entrypoint de despliegue. No se renderizó
ni se ejecutó Compose, no se consultó Docker ni se contactó con el servidor. Los 556 hashes
de archivos de la candidata siguen coincidiendo con su manifiesto; no se reconstruyó.
