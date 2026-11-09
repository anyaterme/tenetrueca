# TRUEC@ — Staging temporal, imagen candidata y presupuesto

Fecha: **07/11/2026**. Dictamen: **B — Listo para staging; producción pendiente de autorización y verificaciones operativas**.
No se han iniciado llamadas reales a OpenAI ni desplegado en producción. No se han sustituido,
reiniciado o recreado los contenedores actuales de TRUEC@. Las migraciones y fixtures se probaron
exclusivamente en un PostgreSQL nuevo, aislado y efímero.

## 1. Resumen ejecutivo

Se conserva el ledger y el mecanismo B de reserva previa por ventana completa. **A — cota
ajustada verificada: No disponible**. La documentación consultada no proporciona una cota
local garantizada de la sobrecarga de Responses y Structured Outputs para este perfil.
No se instala un tokenizador ni se habilita un cálculo heurístico para reducir reservas.

Staging es un entorno temporal independiente para validar **una imagen candidata neutral**.
La misma imagen podrá promocionarse posteriormente, sin reconstrucción, con configuración,
secretos y base de datos propios de producción. Esa promoción no se ejecuta en esta tarea.
La construcción y la ejecución están separadas en Compose diferentes.

Evidencias: [resultados](staging_validation_results.json), [manifiesto de candidata](candidate_image_manifest.json),
[preview sintético](staging_preview.json), [comparativa de cotas](budget_bound_comparison.json).
Los resultados anteriores del [informe de Fase 2](phase2_final_validation.md) siguen siendo históricos.

## 2. Estado inicial inspeccionado

Se revisaron README, informe final, runner, ledger, modelos, tarifas, dataset pilot-v1,
proveedor, configuración, safety, tests y Docker. No se encontró staging en los Compose
ni en los contenedores inspeccionados. La instancia local existente escucha en 8082;
se conserva. El worker de moderación estaba detenido y no se arrancó.

El Compose operativo usa nombres y montajes fijos y ejecuta migrate antes de Gunicorn.
El [script de producción existente](../../deploy/update.sh) también detiene servicios y
requiere su configuración habitual: **no se ejecutó ni reutilizó para staging**.
Las dependencias existentes se reutilizan; no se añadieron paquetes Python.

## 3. Candidata y promoción sin reconstrucción

Imagen validada:

```text
trueca-candidate@sha256:0c9c1ceeeef14f9f7006b769c3c0d27bdae7b3468837f99ffc3244c9a0fb36cf
```

El ID del contenedor de staging coincide con el ID registrado de esa candidata. Se volvió
a comprobar tras las pruebas. El manifiesto recoge 550 archivos y las versiones de paquetes. El destino Ubuntu AMD64
fue confirmado por el operador; la candidata ARM64 preliminar no es promocionable a ese destino.
La candidata no fija DJANGO_SETTINGS_MODULE, no contiene local_settings.py, dotenv, SQLite,
SECRET_KEY operativa ni OPENAI_API_KEY. Usa UID 10001 y un runtime **linux/amd64**, construido desde Python 3.12.15 fijado
por digest y las mismas 31 versiones revisadas en runtime-requirements.lock. La aplicación
hereda ese runtime; no instala paquetes al arrancar. No se añadieron dependencias.

[Dockerfile.candidate](../../Dockerfile.candidate) y [Compose de construcción](../../compose.candidate.yml)
construyen el artefacto una vez. [Compose de staging](../../compose.staging.yml) **no tiene build**:
consume la imagen ya construida. El preflight exige una referencia inmutable por digest.
No se debe usar un tag mutable para decidir qué artefacto se promociona.

La imagen se construyó desde el árbol de trabajo actual, con cambios pendientes de commit;
se registra el commit base y no se presenta como un checkout limpio. Antes de autorizar
producción, cerrar trazabilidad/revisión del código y conservar el artefacto y sus hashes.
Los cambios de documentación y scripts/Compose posteriores no reconstruyen la candidata.

La producción futura seleccionará su configuración propia mediante DJANGO_SETTINGS_MODULE
y secretos/variables autorizados. Si mantiene la convención actual de local_settings.py,
ese archivo se montará como configuración de solo lectura, sin incluirlo en la imagen.
No se copia a staging el archivo productivo ni se conecta su base de datos. La definición
concreta del despliegue productivo pertenece a otra tarea autorizada.

## 4. Arquitectura y aislamiento de staging

| Elemento | Configuración preparada | Estado |
| --- | --- | --- |
| Proyecto | trueca-chatbot-staging; validación AMD64 usó trueca-chatbot-validation-amd6420261107 | Verificado |
| Aplicación | Candidata por digest, UID 10001, raíz read-only, sin capacidades | Verificado |
| PostgreSQL | Servicio staging_db, DB trueca_chatbot_staging, usuario trueca_staging | Verificado |
| Volúmenes | Nombres exclusivos del proyecto; sin pg_data/media operativos | Verificado |
| Sesión | BD propia, trueca_staging_sessionid y trueca_staging_csrftoken | Verificado |
| Secretos | Archivos nuevos fuera del checkout; no dotenv operativo | Verificado en validación |
| Red privada | Solo Django/PostgreSQL y lado interno del proxy; sin gateway exterior | Verificado |
| Entrada | Nginx propio, 127.0.0.1:8083, sin secretos; solo estáticos RO | Verificado por HTTP |
| IA web | mock/extractive, clave vacía, evaluación real deshabilitada | Verificado |
| Limpieza | Sin cron, worker ni limpieza automática destructiva | Verificado |

Se reutilizó una imagen Nginx disponible. Fue necesario un proxy de ingreso porque Docker
no publicaba hacia el host el puerto de una aplicación conectada solo a una red interna.
Solo el proxy se conecta además a la red de ingreso. Django y PostgreSQL siguen sin ruta
exterior. El proxy no monta claves, datos privados ni el volumen PostgreSQL, y su upstream
es fijo: app:8000. También sirve directamente el STATIC_ROOT recopilado.

La validación utilizó [el override efímero](../../compose.staging.ephemeral.yml): PostgreSQL
en tmpfs, CI activado y volúmenes scratch nuevos para assets/reportes. El entorno de pruebas
se retira al terminar; la candidata queda disponible. La temporalidad de un futuro staging
no permite borrar un ledger con gasto o reservas inciertas: conservarlo/archivarlo y reconciliar
antes de cerrar el entorno. Destruir una base no autoriza otro presupuesto de 1 USD.

## 5. Configuración, imagen y secretos

Plantillas: [staging.env.example](../../deploy/staging/staging.env.example) y
[pilot.env.example](../../deploy/staging/pilot.env.example). Están sin secretos ni autorización.
No copiar .env de la instancia operativa. Proveer:

- TRUECA_CANDIDATE_RUNTIME_IMAGE: runtime revisado, solo para construir. Un alias local
  requiere registrar/verificar su ID; un runtime distribuido debe fijarse por digest.
- TRUECA_CANDIDATE_IMAGE: tag durante construcción; **digest al validar/ejecutar**.
- TRUECA_STAGING_POSTGRES_IMAGE y TRUECA_STAGING_PROXY_IMAGE: imágenes revisadas por digest.
- TRUECA_STAGING_HTTP_PORT=8083, o puerto independiente aprobado.
- TRUECA_STAGING_SECRET_KEY_FILE y TRUECA_STAGING_DB_PASSWORD_FILE: secretos nuevos e independientes.

Guardar los archivos en un directorio privado 0700, fuera del checkout y del contexto Docker.
Con los secrets basados en bind de Compose, asegurar lectura por UID 10001: por ejemplo,
archivos 0444 dentro de ese directorio privado, o una solución equivalente de permisos/grupos.
No se imprimen sus valores. Los secretos aleatorios de esta validación fueron solo de test.

El perfil [staging_settings.py](../../tenetrueca/staging_settings.py) evita importar
local_settings y rechaza flags de IA real en el proceso web. Mantiene límites menores fijados
por el operador y respeta CHATBOT_ENABLED=False. No modifica la política de autenticación.
Correo local en memoria, Maps sin clave y ningún worker de moderación: no se prueban servicios
externos reales de correo/CAPTCHA/autenticación en este perfil.

## 6. Migraciones e integridad

Nombres reales: `0001_initial`, `0002_chatmessage_sources`, `0003_evaluation_budget`.
0002 añade sources con default de BD; 0003 crea únicamente las dos tablas del ledger y sus
restricciones. No se modificaron migraciones ni se creó otra nueva para esta revisión.
No se usó --fake ni se borraron tablas existentes.

La consulta de solo lectura a la instancia local mostró las tres aplicadas. No se atribuye
su aplicación a esta tarea. El estado de una producción remota sigue sin comprobarse.

En la base efímera nueva: identidad/rol/durabilidad verificados → migrate --plan → migrate
--noinput → check de historial consistente y pendientes vacíos. Makemigrations chatbot
--check --dry-run no detecta cambios. Se aplicó toda la cadena de dependencias desde cero.

Existe una limitación previa del repositorio: muchas migraciones base están ignoradas por
Git. Se generó un [manifiesto de 63 archivos](../../chatbot/staging_migrations.json); la
candidata contiene los artefactos locales revisados y el comando compara hashes antes de
validar. Un checkout que carezca de esos archivos debe detenerse, recuperar los artefactos
correctos y revisar su trazabilidad. No generar migraciones alternativas ni falsear su estado.
Esto no impide promocionar la imagen exacta validada, pero bloquea afirmar que un build nuevo
desde un checkout incompleto es reproducible.

## 7. Reserva y contabilidad actuales

Se conservan ChatEvaluationRun/Attempt, UUID estable aprobado, fingerprint de configuración,
row locks y advisory lock exclusivo PostgreSQL. La reserva Decimal se persiste antes de
crear el cliente; ninguna transacción queda abierta durante la espera al proveedor.

Para I tokens de entrada acotados, O salida máxima, precios máximos Ri/Ro/Rs, fijo F y margen M:

`U = ceil_1e-9_USD(M × ((I × (Ri + Rs) + O × Ro) / 1.000.000 + F))`

Admisión: committed+U≤budget y admitted<max_requests. Cap local: 1 USD y 20 intentos.
Uso fiable permite liberar solo la diferencia justificable. Error/timeout sin uso fiable
retiene U y detiene la ejecución. Los intentos enviados cuentan aunque fallen; no hay retries.
Se conserva el presupuesto en reinicios, duplicados, export fallido y resume. No se reinició
ni eliminó un presupuesto existente. Cambiar código/configuración invalida resume, no crea saldo.

## 8. Investigación del conteo y fuentes

La [guía oficial de conteo](https://developers.openai.com/api/docs/guides/token-counting)
explica que roles, fronteras y esquemas pueden añadir tokens que no aparecen al tokenizar
el texto local. El endpoint de conteo recibe el payload completo, pero requiere una petición
remota: **no se llamó**. No se ha encontrado una cota local documentada de esa sobrecarga.
Un conteo local del texto no sería una prueba de la factura ni del total procesado.

| Concepto | Resultado |
| --- | --- |
| Conteo local estimado | No utilizado para admisión |
| Conteo local verificado de todo Responses | No disponible |
| Cota ajustada demostrable A | No disponible |
| Cota conservadora B | Ventana documentada del perfil revisado |
| Conteo informado por OpenAI | No obtenido; pendiente de autorización |
| Uso facturable real | No medido; usage no sustituye la factura |

También se consultaron [modelo Luna](https://developers.openai.com/api/docs/models/gpt-6-luna),
[precios](https://developers.openai.com/api/docs/pricing) y
[referencia de conteo](https://developers.openai.com/api/reference/python/resources/responses/subresources/input_tokens/methods/count).
La consulta documental no aprueba tarifas ni disponibilidad efectiva de la cuenta.

## 9. Método implementado y límites aplicables

[token_budget.py](../../chatbot/evaluation/token_budget.py) centraliza el perfil offline:

- conservative: B; auto: B con motivo de fallback; verified: rechazo seguro porque A no está verificado.
- Modelo revisado gpt-6-luna, SDK 2.54.0; otro SDK/perfil requiere revisión y falla antes del cliente.
- Payload textual completo: instrucciones, dos mensajes user, pregunta/contexto, esquema JSON,
  salida, reasoning, store=False, tier default y truncation=disabled.
- Fingerprint del payload; no tools, archivos, conversaciones remotas o previous_response_id.
- Diagnóstico de JSON UTF-8 canónico y sus componentes; máximo 131.072 bytes de ese diagnóstico.
  No son tokens ni una afirmación de bytes exactos transmitidos por el SDK.
- Hasta cinco fragmentos, 1.200 caracteres/unidad y 6.000 de contexto; input/pregunta y timeout
  existentes. Máximo 400 tokens de salida, incluyendo componentes no visibles.

El perfil exige una cota de entrada de 1.050.000 tokens: un límite CLI menor rechaza la
petición antes de crear el SDK. No se envía un parámetro inventado max_input_tokens a Responses;
el control es previo. No se promete un máximo ajustado menor que esa cota. Un modelo sin
perfil o payload no admitido se rechaza, nunca cae en caracteres/4.

El ledger conserva su diseño y sus checks. Se añade al fingerprint el método/versión del
bound; no hay cambios de esquema ni de la API pública del widget.

## 10. Tarifas y contexto largo

[pricing.example.json](../../chatbot/evaluation/pricing.example.json) permanece approved=false,
coverage_approved=false y sin importes/fecha operativos. Se exige además
`rate_scope=maximum_across_entire_context_window`: tasas máximas para **todo** el rango B.
Un archivo antiguo sin esa cobertura falla cerrado. El operador debe verificar y aprobar
manualmente el perfil y sus posibles recargos; la fecha máxima sigue siendo siete días.

La ficha oficial consultada indica un umbral de 272K y multiplicadores 2× en entrada/caché
y 1,5× en salida por encima. Por tanto, el precio corto no basta para reservar una ventana
completa. Considerar escritura de caché y recargos regionales; no aplicar descuentos al reservar.
Los adicionales Rs se aplican conservadoramente a toda la entrada, aunque resulte holgado.

No se cambian los controles presupuestarios de moderación. Otros procesos/cuentas/proyectos
no quedan limitados por este ledger. No es una garantía absoluta de facturación del proveedor.

## 11. Comparativa reproducible

Comparativa offline generada con la candidata, sin SDK cliente ni red. O=400 en todas las filas.
El ejemplo aritmético usa Ri=0,20; Rs=0,50; Ro=0,75 USD/1M; F=0 y M=1,10. Es una hipótesis
conservadora ilustrativa basada en contexto largo/escrituras; **no una tarifa aprobada** ni
la configuración operativa. La aprobación requiere revisar las condiciones exactas del proyecto.

| Escenario | Contexto real chars | JSON local bytes | Cota B entrada | Reserva B del ejemplo | Reserva A verificada |
| --- | ---: | ---: | ---: | ---: | --- |
| Pregunta breve | 218 | 2.685 | 1.050.000 | 0,808830000 USD | No disponible |
| FAQ corta | 246 | 2.712 | 1.050.000 | 0,808830000 USD | No disponible |
| FAQ extensa | 961 | 3.477 | 1.050.000 | 0,808830000 USD | No disponible |
| Cinco fragmentos al máximo completo admisible | 5.873 | 8.485 | 1.050.000 | 0,808830000 USD | No disponible |

5.873 es el contexto de ese fixture con unidades completas; el límite configurado es 6.000.
Los tamaños cambian, la cota B no: no se afirma ahorro monetario verificado. Un recargo
adicional de 10% sobre ese ejemplo llevaría U a 0,889713000 USD. No se usa como precio operativo.

Capacidad aritmética si se retuviera cada reserva: floor(1/U)=1 en ese ejemplo, hasta el cap20.
La política además se detiene ante uso desconocido. Con uso fiable se libera la diferencia;
para admitir n intentos de coste conocido C, comprobar (n−1)C+U≤1 y n≤20. **C no está medido**;
no se predice que se completarán las diez comparaciones. Con tarifas actuales ausentes:
reserva monetaria no calculable y **cero admisiones reales**.

## 12. Pruebas y resultados de esta tarea

| Suite | Total | Correctos | Fallidos | Omitidos |
| --- | ---: | ---: | ---: | ---: |
| PostgreSQL, candidata AMD64 exacta, sin montar código | 841 | 841 | 0 | 0 |
| SQLite, candidata AMD64 exacta, sin montar código | 841 | 800 | 0 | 41 |
| Presupuesto + bound + aislamiento PostgreSQL (incluidos) | 50 | 50 | 0 | 0 |
| JavaScript Node | 32 | 32 | 0 | 0 |
| Smoke de staging, candidata final | 40 | 40 | 0 | 0 |

No sumar subconjuntos como casos independientes. Las comprobaciones preliminares ARM64
no se atribuyen a la candidata AMD64; ambos motores se ejecutaron de nuevo por su digest final. Las omisiones SQLite requieren
PostgreSQL (locks/migraciones); ninguna omisión en la suite candidata PostgreSQL.

Los ocho tests nuevos de bound cubren longitudes, ES/EN/Unicode, contexto vacío y máximo,
instrucciones/esquema, modelo, SDK, tokenizer/método desconocido, payload excesivo, salida,
precio largo y margen, y preview sin cliente/ledger. Los 37 presupuestarios existentes prueban
reservas, errores, timeouts, reinicio/resume, duplicados, concurrencia y caps. Los cinco de
staging prueban redes/montajes/puertos, rechazo de activación, separación de local_settings,
límites menores y desactivación. Todos los proveedores están simulados, con CI y red aislada.

El smoke usa Django Client con CSRF real, usuarios/FAQ/categorías/centros sintéticos dentro
de una transacción revertida. Comprueba pregunta 11, frecuencia, reset, propiedad, referencias,
ambos modos y dry-run. Solo envejece las solicitudes del fixture para recorrer la ventana
de frecuencia, sin cambiar los límites del backend. No modifica datos reales.

## 13. Comandos ejecutados y entorno

Python 3.12.15, Django 5.2.18, SDK 2.54.0; PostgreSQL 17 efímero y SQLite memoria. Docker
Compose 5.5.1. Destino confirmado: VPS Ubuntu AMD64. La aplicación AMD64 se ejecutó
bajo emulación en el equipo ARM64; PostgreSQL/Nginx de prueba fueron nativos. Esto valida
funcionalidad, no rendimiento nativo de la VPS. El manifiesto contiene el resto de versiones. Comandos equivalentes ejecutados:

```bash
python manage.py test --settings=tenetrueca.test_postgres_settings --noinput
python manage.py test --settings=tenetrueca.test_settings --noinput
python manage.py test chatbot --settings=tenetrueca.test_settings --noinput
python manage.py test chatbot.tests.test_evaluation_budget chatbot.tests.test_token_budget \
  chatbot.tests.test_staging_isolation --settings=tenetrueca.test_postgres_settings --noinput
node --test chatbot/tests/js/test_chatbot.cjs
python manage.py check_chatbot_staging
python manage.py migrate --plan
python manage.py migrate --noinput
python manage.py check_chatbot_staging --ready
python manage.py check
python manage.py collectstatic --noinput
python manage.py validate_chatbot_staging
python manage.py makemigrations chatbot --check --dry-run
```

Los comandos stage usan exclusivamente los Compose nuevos y su env-file independiente;
los tests usan CI=true, clave vacía y bases test_ distintas. La candidata se probó sin
montar el repositorio: solo se montó la contraseña **sintética** del servidor PostgreSQL
nuevo. Ningún contenedor de test obtuvo el socket Docker ni datos operativos.

Collectstatic: 194 archivos disponibles. HTTP real por Nginx: health, CSS, JS e icono devuelven
200; hashes de los tres assets idénticos a las fuentes. No se repiten como actuales las
28 pruebas visuales históricas ni se afirma validación física Safari/lector de pantalla.

## 14. Backup y restauración

Se hizo pg_dump -Fc de la base efímera (219.886 bytes), y se restauró correctamente en otra
base **nueva del mismo servidor efímero**. Se verificaron tres migraciones chatbot aplicadas
y cero filas de evaluación. Nunca se restauró sobre una base existente o productiva.

Para un entorno posteriormente autorizado: backup privado → verificar pg_restore --list →
ensayar restore en otra base nueva aislada → revisar identidad/plan → aplicar → ready.
No usar --fake, no borrar tablas y no restaurar automáticamente un ledger anterior al gasto.
Conservar el respaldo y UUID de cualquier futuro piloto hasta reconciliar cargos inciertos.

## 15. Defectos encontrados y corregidos

- El dry-run carecía de método/cota y motivos explícitos: ahora muestra bound, tamaños,
  preguntas, modalidades, llamadas posibles, ausencia de precios, advertencias y blockers.
- La cobertura de precios no declaraba explícitamente el rango: se exige rate_scope completo.
- Se evita que otro método/tokenizer/SDK o un payload no revisado reduzca silenciosamente U.
- Staging no existía y el script operativo no era apto: configuración y preflight independientes.
- Se evita importar local_settings/dotenv operativos; secretos, sesiones y DB separados.
- Se corrigió un tmpfs mal expresado antes de la primera migración válida del entorno nuevo.
- Assets con permisos heredados demasiado restrictivos: lectura corregida solo dentro de la
  imagen, no en el repositorio/servidor operativo. Collectstatic y HTTP quedaron verificados.
- Una red solo interna no publicaba hacia el host: proxy independiente con estáticos RO,
  sin dar egress a Django/PG ni credenciales al proxy.
- Construcción separada de ejecución: candidata neutral, validada y congelada por digest.

## 16. Dry-run verificado

El [preview](staging_preview.json) se generó sobre corpus sintético y centro público de
prueba: diez preguntas × dos modos, hasta **16 generaciones posibles**, dos preguntas locales,
precios ausentes, A no disponible y cero admisiones/llamadas reales. No se abrió cliente.
No se forzaron llamadas para alcanzar veinte. Sin fuentes suficientes habrá menos generaciones.
El dry-run no crea autorizaciones ni lee/restablece un saldo existente.

Comando seguro con el entorno mock autorizado:

```bash
docker compose --env-file /ruta-privada/staging.env -p trueca-chatbot-staging \
  -f compose.staging.yml run --rm -T --no-deps app \
  python manage.py evaluate_chatbot_live --dry-run --dataset pilot-v1 --mode both \
  --max-budget-usd 1.00 --max-requests 20 --max-input-tokens 1050000 \
  --max-output-tokens 400 --output /app/evaluation_reports/preview.json
```

Este comando no instala fixtures. Revisar las fuentes públicas disponibles; no conectar
ni clonar datos privados de producción para completar el preview.

## 17. Preparación temporal y ejecución por digest

Los comandos siguientes preparan un artefacto nuevo **antes** de validarlo; no son un paso
de promoción ni autorización de producción. No volver a construir la candidata final validada.
Preparar env-file/secretos independientes y una versión cerrada del código/migraciones.

```bash
# Preparar runtime AMD64 con base oficial fijada y versiones congeladas:
docker build --platform linux/amd64 \
  --build-arg PYTHON_IMAGE=python@sha256:57d96f71de145a98e7ceca18ee4ade77bba4103a33a72dfaea026b23625c6bf0 \
  -f Dockerfile.runtime -t trueca-runtime:VERSION-NUEVA .
# Registrar su ID. Usar ese alias local revisado en TRUECA_CANDIDATE_RUNTIME_IMAGE.
# Construcción única de candidata; usar un tag nuevo solo durante este paso.
docker compose --env-file /ruta-privada/staging.env -f compose.candidate.yml build candidate
# Registrar ID/RepoDigest. Cambiar TRUECA_CANDIDATE_IMAGE al digest resultante.
docker image inspect CANDIDATA_CONSTRUIDA
python3 deploy/staging/check_compose.py --env-file /ruta-privada/staging.env

docker compose --env-file /ruta-privada/staging.env -p trueca-chatbot-staging \
  -f compose.staging.yml up -d --wait staging_db
```

Antes de modificar una base persistente ya existente, obtener autorización específica.
En un entorno nuevo autorizado: ejecutar check_chatbot_staging y migrate --plan; revisar
backup/identidad y autorizar migrate --noinput; después ready, check y collectstatic.
Arrancar app/proxy con `up -d --wait --no-build app proxy`. El Compose de staging no contiene
build y el preflight exige digest. No emplear compose operativo ni deploy/update.sh.

No ejecutar validate_chatbot_staging en una base con datos. Ese comando exige el override
efímero y una base nueva vacía, y revierte fixtures. La base de un piloto real deberá retener
el ledger aunque el entorno sea temporal: no aplicar el override tmpfs/CI a ese piloto.

## 18. Piloto real: preparado, no ejecutado

Requisitos: staging aislado validado; migraciones autorizadas; corpus público y pilot-v1
aprobados; modelo/SDK compatibles; tarifas recientes aprobadas para todo el rango; proyecto
OpenAI autorizado; UUID fijo; ledger duradero; máximo 1 USD/20 intentos/400 salida; cota B
fiable; ejecución secuencial/exclusiva; sin retries; nueva confirmación expresa del operador.
El permiso para este piloto no es permiso para desplegar en producción.

Rellenar pilot.env a partir de su plantilla: UUID aprobado fijo, proyecto, modelo, y rutas
a clave autorizada y archivo de precios aprobado. El perfil adicional
[compose.staging.pilot.yml](../../compose.staging.pilot.yml) es solo un CLI: no publica puertos,
monta la clave solo para el evaluador y añade egress únicamente a ese proceso. No se levantó
ese perfil ni su red durante esta tarea. Web y moderación permanecen sin IA real.

Tras autorización futura, primero previsualizar; luego el comando real exacto será:

```bash
docker compose --env-file /ruta-privada/staging.env --env-file /ruta-privada/pilot.env \
  -p trueca-chatbot-staging -f compose.staging.yml -f compose.staging.pilot.yml \
  --profile authorized-pilot run --rm -T --no-deps evaluator \
  python manage.py evaluate_chatbot_live --allow-live --dataset pilot-v1 --mode both \
  --model gpt-6-luna --run-id UUID-FIJO-EXPRESAMENTE-APROBADO \
  --max-budget-usd 1.00 --max-requests 20 --max-input-tokens 1050000 \
  --max-output-tokens 400 --output /app/evaluation_reports/pilot-results.json
```

Para el preview anterior, sustituir --allow-live por --dry-run y usar otro archivo de salida.
Si el modelo aprobado difiere, ajustar env-file y CLI de forma coherente; un perfil no revisado
será rechazado. No generar otro UUID por error, falta de saldo, timeout o exportación fallida.
Para reanudar: misma imagen, UUID, configuración y fuentes; añadir --resume. CI/test/startup/
migraciones siguen bloqueados por safety y no se desactiva su guard.

## 19. Promoción y rollback posteriores

No se promocionó ni desplegó. Una tarea futura deberá autorizar explícitamente el digest,
configuración y destino. Transferir el mismo artefacto por registro autorizado o save/load,
verificar su identidad y usar `image: repositorio@sha256:...`, sin build. No reconstruir para
cambiar settings: seleccionar la configuración propia del destino mediante entorno/secretos.
No introducir en producción datos, cuentas, cookies o volúmenes de staging.

Antes: backup productivo restaurable, compatibilidad de migraciones, permisos para UID 10001,
SECRET_KEY/cookies/hosts/CSRF/TLS/proxy, estáticos con el mismo artefacto y configuración de
OpenAI expresamente aprobada. Esas comprobaciones sobre producción no se han ejecutado.

Rollback: desactivar chatbot/evaluación con flags de su entorno, detener nuevas admisiones
y conservar reservas inciertas; volver a imagen y configuración previas según autorización.
Mantener tablas/columnas aditivas y datos; no bajar 0002/0003 como atajo. Restaurar una base
antigua no puede deshacer cargos y puede volver inseguro el ledger: bloquear hasta reconciliar.

## 20. Riesgos, estado y recomendación final

| Ámbito | Estado |
| --- | --- |
| Candidata por digest y tests empaquetados | Verificado |
| Staging temporal aislado, migraciones, estáticos y HTTP | Verificado en entorno efímero |
| Ledger, caps, errores, duplicados y concurrencia | Verificado con mocks |
| Cota B y rechazo de A no verificado | Verificado en el perfil revisado |
| A local más ajustada | No disponible; bloqueante para activar ese método |
| Precios/proyecto/clave/UUID aprobados | Pendiente de autorización; bloqueante para gasto real |
| Calidad/tokens/facturación de OpenAI real | No verificable aquí sin llamadas; no ejecutado |
| Corpus editorial, Safari/teclados/lectores reales | Parcialmente verificado / pendiente |
| Checkout completo de migraciones y trazabilidad del release | Pendiente de cierre antes de producción |
| Configuración/base/HTTPS/backups productivos | Pendiente de tarea autorizada |
| Promoción/despliegue productivo | Pendiente de autorización explícita |

Se mantiene **B**. El próximo paso es revisar y aceptar el digest/evidencias de esta candidata
y autorizar por separado la validación operativa o el piloto deseado. No se continúa con IA
real ni producción. La falta de A es una limitación demostrada, no se oculta como optimización.

## Archivos de esta entrega

Creados: `.dockerignore`, `Dockerfile.runtime`, `Dockerfile.candidate`, `compose.candidate.yml`,
`compose.staging.yml`, `compose.staging.ephemeral.yml`, `compose.staging.pilot.yml`,
`tenetrueca/staging_settings.py`, `chatbot/staging_isolation.py`, `chatbot/staging_migrations.json`,
`chatbot/evaluation/token_budget.py`, comandos `check_chatbot_staging` y
`validate_chatbot_staging`, tests `test_token_budget.py` y `test_staging_isolation.py`,
`deploy/staging/{check_compose.py,manifest.py,nginx.conf,staging.env.example,pilot.env.example,runtime-requirements.lock}`,
este informe y los cuatro JSON de evidencia enlazados.

Modificados: `.env.example`, `.gitignore`, `tenetrueca/settings.py`,
`chatbot/evaluation/{runner.py,provider.py,pricing.py,pricing.example.json}`,
`chatbot/tests/test_evaluation_budget.py`, README y enlace de actualización del informe final.
Los otros cambios ya presentes en Git pertenecen a entregas anteriores; no se atribuyen aquí.
Sin cambios en modelos/migraciones, frontend o aplicaciones de negocio/moderación.

El código ejecutable de la candidata no se cambia después de congelar su digest. Las
actualizaciones posteriores de documentación y de los Compose/scripts externos de operación
no requieren reconstruirla. El límite de salida 400 puede producir respuestas incompletas
con unidades extractivas extensas: pendiente de evaluación real autorizada, sin retry ni
incremento automático de tokens.

## Cierre del entorno temporal

El proyecto de validación se retiró tras comprobar cero presupuestos, usuarios, FAQ y
conversaciones de prueba persistentes. Solo se eliminaron sus contenedores/redes/volúmenes
scratch; la candidata queda disponible. El worker operativo sigue detenido y los IDs,
fechas de arranque y contadores de reinicio de app/DB actuales coinciden con el inventario
inicial. No se ejecutó el perfil de IA real ni se creó su red de egress.

Se probó también la candidata directamente por digest, con --pull never y sin reconstruir.
Plataforma final: **linux/amd64**, conforme a la VPS Ubuntu confirmada por el operador.
La candidata ARM64 preliminar queda descartada para ese destino. El preflight comprueba
platform e inspecciona la arquitectura real, rechazando ARM64 antes de arrancar staging.
Registro/transporte, validación nativa de la VPS, configuración productiva y permisos quedan
pendientes de la tarea de despliegue autorizada; no se reconstruirá la candidata al promoverla.

Sintaxis Python/JavaScript/JSON, traducción compilada, enlaces locales y git diff --check
correctos. Los 214 archivos protegidos de aplicaciones existentes conservan sus hashes; el
código y recursos ejecutables del repositorio coinciden con el paquete candidato. Las
únicas diferencias posteriores permitidas son documentación/configuración de operación.

La construcción local por digest de un runtime aún no publicado puede intentar consultar
un registro inexistente. Se resolvió usando su alias local verificado, registrando ID y
versiones; no se publicó ni se pidió acceso a ningún registro. La candidata resultante se
congeló por su propio digest antes de los tests y no se volvió a construir después.

La validación final AMD64 sustituyó explícitamente la prueba ARM64 preliminar: PostgreSQL
841/841 correctos (73,324 s), SQLite 800 correctos y 41 omisiones de 841 (43,507 s), smoke
40/40, HTTP/estáticos 200 y pip check sin conflictos. Los ocho tests de bound y cinco de
aislamiento forman parte de esas suites; las pruebas y mediciones ARM previas no se reutilizan
como evidencia de ejecución AMD64. Los tests JavaScript no cambiaron de plataforma ni código.

## Actualización 2026-11-08 — candidata con correcciones de presentación

Se generó y validó una nueva candidata AMD64 por digest, sin llamadas OpenAI ni despliegue
productivo: `trueca-candidate@sha256:83e3c55f5134975753be36cac19335ae382d6c639e73cfd15ff5db5e4fd3b13a`.
PostgreSQL 858/858; SQLite 817 superados y 41 omitidos; JavaScript 32/32; smoke 40/40;
HTTP 19/19; collectstatic 194 archivos y restauración sintética correcta. Se retiró solo
el staging nuevo, preservando los 29 contenedores previos y el piloto archivado.
El [informe de esta candidata](candidate_validation_20261108.md) contiene comandos,
manifiestos y límites. Los manifiestos/resultados anteriores se conservaron con sufijo
`_20261107`; los archivos de referencia sin fecha apuntan a la validación nueva.
