# Candidata AMD64 con correcciones — 2026-11-08

**Resultado: validación técnica satisfactoria en staging temporal aislado.**
La imagen incorpora las correcciones de inglés y tres frases y se probó por su digest,
sin montar código del repositorio. No hubo nuevas llamadas reales a OpenAI, despliegues
en producción ni modificaciones de los contenedores existentes.

## Artefacto retenido

```text
Tag:      trueca-candidate:chatbot-amd64-20261108-style
Digest:   trueca-candidate@sha256:83e3c55f5134975753be36cac19335ae382d6c639e73cfd15ff5db5e4fd3b13a
Platform: linux/amd64
User:     10001:10001
```

La misma identidad se comprobó en el servidor HTTP. La candidata anterior
(`0c9c1ceeeef1…`) y su tag permanecen intactos. La imagen nueva queda disponible
localmente para una futura promoción por digest, sin reconstrucción. No se publicó en
un registro remoto. Cualquier despliegue real requiere una tarea autorizada posterior.

Se reutilizó el runtime AMD64 existente, verificado por ID
`sha256:2a2af441329a94ca4d5ec928acaddbb1817a20d081bc81b5aa1c9b9f1c08be9a`.
Python 3.12.15, Django 5.2.18 y OpenAI SDK 2.54.0. El inventario de dependencias coincide
con la candidata anterior; no se instalaron paquetes nuevos. La compilación no habilitó
red para sus instrucciones. La imagen no incluye `.env`, `local_settings.py`, secretos
ni datos operativos.

El manifiesto registra los hashes de los **552 archivos empaquetados**: dos añadidos y
trece modificados respecto a la candidata anterior, incluyendo documentación/configuración
de ejemplo ya presente en el repositorio. Los 214 archivos protegidos de negocio conservan
sus hashes. El árbol de trabajo sigue sin commit; digest e inventario identifican el
artefacto exacto validado.

## Aislamiento

- Proyecto nuevo: `trueca-chatbot-validation-style202611089ffa19`.
- PostgreSQL propio en `tmpfs`, sin puertos publicados ni volúmenes operativos.
- SECRET_KEY y contraseña nuevos mediante secretos de archivo.
- Aplicación en red interna, filesystem raíz de solo lectura y UID 10001.
- Proxy independiente con puerto temporal `127.0.0.1:8084`.
- Provider `mock`, evaluación real deshabilitada, clave API vacía y `CI=true`.
- Tests SQLite sin red; PostgreSQL en el namespace de red de su servidor temporal,
  conectado solo a una red interna. Ningún test recibió credenciales OpenAI.

El preflight comprobó arquitectura, digests, redes, montajes y recursos separados.
`check_chatbot_staging` verificó identidad, durabilidad y hashes de migraciones. Tras
revisar el plan se aplicaron las **58 migraciones existentes**, incluidas las tres de
chatbot, únicamente a la base nueva. No se generaron migraciones ni se usó `--fake`.
La comprobación final no encontró pendientes.

## Resultados

| Comprobación | Resultado |
| --- | --- |
| Suite completa PostgreSQL | **858/858**, sin omisiones; 64,676 s |
| Suite completa SQLite | **817 superadas, 41 omitidas**, de 858; 36,232 s |
| JavaScript del widget | **32/32**, sin omisiones |
| Smoke de staging | **40/40**, fixtures revertidas |
| HTTP real mediante Nginx | **19/19**, usuarios exclusivamente sintéticos |
| Django system checks | Sin incidencias |
| `makemigrations chatbot --check --dry-run` | Sin cambios |
| `collectstatic` | **194 archivos** |
| Hashes HTTP de CSS/JS de chatbot y FAQ | Coinciden con la imagen |
| Backup y restauración sintéticos | 58 migraciones, 3 de chatbot; ledger vacío |
| Ledger de evaluación temporal | Cero runs y cero intentos |
| Llamadas reales a OpenAI | **0** |

Las suites incluyen los 17 tests nuevos de idioma/formato con proveedor/SDK simulado:
ES/EN, ambos modos, tres frases, cita original, salida incorrecta, revalidación, historial,
cuota e idempotencia. PostgreSQL ejecutó también los controles de bloqueos, presupuesto y
concurrencia omitidos por SQLite. No se suman subconjuntos como casos adicionales.

El smoke comprobó CSRF, origen, pregunta 11, frecuencia, historial, reset sin restaurar
cuota, autenticación, propiedad, fuentes, ambos modos y dry-run. Revirtió sus fixtures.
HTTP comprobó salud, portal, estáticos y los tres endpoints con visitantes y un usuario
sintético, incluyendo duplicados y acceso denegado entre historiales anónimos/autenticados.
Las respuestas HTTP fueron locales/deterministas; la presentación generativa se comprobó
con mocks dentro de las suites.

El backup se restauró en otra base nueva del mismo PostgreSQL temporal. Se verificaron
las migraciones y el ledger vacío y se eliminó esa base de restauración. No se restauró
ni migró sobre datos existentes.

## Comandos ejecutados

Desde `app/`, compilación única de esta candidata:

```bash
docker build --platform linux/amd64 --network none --pull=false \
  --build-arg RUNTIME_IMAGE=trueca-runtime:chatbot-amd64-20261107 \
  -f Dockerfile.candidate -t trueca-candidate:chatbot-amd64-20261108-style .
```

La validación usó solo `compose.staging.yml` más `compose.staging.ephemeral.yml`,
un env-file privado nuevo y el proyecto indicado. Comandos de la imagen por digest:

```bash
python3 deploy/staging/check_compose.py --env-file RUTA_PRIVADA \
  --project trueca-chatbot-validation-style202611089ffa19 --ephemeral
python manage.py check_chatbot_staging
python manage.py migrate --plan
python manage.py migrate --noinput
python manage.py check_chatbot_staging --ready
python manage.py test --settings=tenetrueca.test_postgres_settings --noinput --verbosity 1
python manage.py test --settings=tenetrueca.test_settings --noinput --verbosity 1
python manage.py validate_chatbot_staging
python manage.py check
python manage.py makemigrations chatbot --check --dry-run
python manage.py collectstatic --noinput
```

Los tests PostgreSQL utilizaron una base `test_` separada del staging. El único archivo
montado en ese contenedor de tests fue la contraseña sintética. JavaScript se ejecutó
con `node --test chatbot/tests/js/test_chatbot.cjs`; se verificó el hash del script frente
a la imagen. Un cliente HTTP de pruebas comprobó el puerto temporal y los endpoints.

## Cierre y preservación

Se retiró solo el proyecto nuevo, con sus contenedores, redes y volúmenes temporales.
El puerto 8084 ya no queda publicado por esta validación. Se conservan la imagen y las
evidencias; el PostgreSQL del piloto anterior continúa detenido.
También se eliminaron los dos archivos de secretos generados exclusivamente para esta
validación. Los secretos y recursos del piloto anterior no se tocaron.

Los **29 contenedores preexistentes** conservaron IDs, imágenes, creación, montajes y
redes. La aplicación TRUEC@, su PostgreSQL, el worker de moderación y el PostgreSQL del
piloto conservaron además estado, inicio y contador de reinicios. Un servicio ajeno que
ya estaba en un bucle automático de reinicios cambió de estado por ese bucle previo;
no se actuó sobre él. Los archivos privados, backups y ledger del piloto no cambiaron.

## Evidencias y límites

- [Manifiesto de esta candidata](candidate_image_manifest_20261108.json).
- [Resultados estructurados](staging_validation_results_20261108.json).
- [Manifiesto anterior archivado](candidate_image_manifest_20261107.json).
- `candidate_image_manifest.json`, `staging_validation_results.json` y
  `staging_preview.json` apuntan ahora a esta validación; sus versiones anteriores se
  archivaron con sufijo `_20261107`.

La aplicación AMD64 se ejecutó bajo emulación en Docker ARM64: los tiempos no describen
rendimiento nativo de la VPS. Las correcciones se validaron con mocks y no se repitió el
piloto real. No se comprobaron secretos, HTTPS, base ni configuración productiva y esta
validación no autoriza su despliegue.
