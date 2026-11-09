# TRUEC@ — candidata AMD64 definitiva con corrección E34

Fecha: 2026-11-09. **Dictamen: técnicamente validada; sin autorización de producción.**

## 1. Resumen ejecutivo

Se construyó una única candidata nueva, incorporando la corrección offline de E34.
La regresión completa, el staging aislado y las comprobaciones HTTP utilizaron el código
incluido en la imagen por digest, sin montar el repositorio. No hubo llamadas reales
a OpenAI ni cambios en producción. Se conservan las imágenes anteriores y los pilotos.

Evidencias: [manifiesto](candidate_image_manifest_20261109_e34.json) y
[resultados estructurados](staging_validation_results_20261109_e34.json).

## 2. Objetivo y alcance

Empaquetado y validación del estado aprobado, sin nuevas funcionalidades, dependencias,
migraciones ni cambios ejecutables. Se revisaron únicamente la corrección E34 y los
procedimientos de construcción/staging. Respecto a la candidata anterior hay dos archivos
nuevos y cuatro modificados, todos documentados en la corrección; los otros 550 archivos
anteriores mantienen sus hashes. No se incorporaron cambios ejecutables ajenos.

## 3. Imagen anterior

```text
trueca-candidate:chatbot-amd64-20261109-scope
trueca-candidate@sha256:8ca2925a0e9b7a5216891e811bd5d380ee6e83649d2a9e90fea7bb2246325477
```

La imagen y sus manifiestos históricos se conservaron. No se sobrescribieron los
manifiestos genéricos, que siguen describiendo su validación anterior.

## 4. Imagen nueva e identidad inmutable

```text
Tag: trueca-candidate:chatbot-amd64-20261109-e34
Referencia: trueca-candidate@sha256:dd28d44c918e3359c1d5b83ce64ec5be2447929641f798b957d60dd7f51814a4
ID local: sha256:dd28d44c918e3359c1d5b83ce64ec5be2447929641f798b957d60dd7f51814a4
Plataforma: linux/amd64
Usuario: 10001:10001
Python: 3.12.15
Django: 5.2.18
OpenAI SDK: 2.54.0
Archivos empaquetados: 556
```

El digest es el índice OCI registrado por Docker; el manifiesto incluye además el digest
de configuración y los hashes de todos los archivos. Arquitectura y usuario se verificaron
con `docker image inspect`, y el contenedor HTTP ejecutó el mismo ID. La candidata queda
disponible localmente; no se publicó en un registro remoto.

## 5. Trazabilidad Git y reproducibilidad

Commit base: `2b3e1ac0bff6be219f06027ddd0b0511a764fd34`. Árbol sucio, sin commit nuevo.
Se conservaron status, diff de archivos rastreados, lista de archivos sin rastrear,
snapshot limpio y hashes completos. El manifiesto registra el estado utilizado al congelar.

Hay **34 archivos de migraciones sin rastrear, 30 de ellos ignorados por Git**; los cuatro
restantes pertenecen al nuevo chatbot. Todos existen y coinciden con el manifiesto revisado.
No se regeneraron ni se corrigió automáticamente el historial Git.

La imagen congelada es identificable y reutilizable por digest. **Un checkout Git limpio
del commit base no reproduce este árbol**: faltan cambios y artefactos sin rastrear/ignorados,
además de los nuevos archivos de aplicación y empaquetado. Resolver esa entrega en control
de versiones queda pendiente, sin impedir el uso futuro del artefacto ya congelado.

## 6. Corrección E34 incorporada

Nuevos: `chatbot/sentence_planning.py` y `chatbot/tests/test_sentence_planning.py`.
Modificados: `chatbot/answering.py`, `chatbot/tests/test_response_style.py`,
`chatbot/tests/test_evaluation_selection.py` y `chatbot/README.md`.

El formateador conserva las palabras de la única FAQ aprobada y separa tres imperativos
independientes. No traduce, rellena, inventa ni transforma reglas críticas. Los validadores
de estilo, grounding y citas conservan sus hashes anteriores. La fuente vuelve a consultarse
antes de mostrar/persistir el resultado. Ver [análisis offline](e34_offline_fix_20261109.md).

Resultado exacto en ambos modos, con saltos entre frases:

> Explora el catálogo.
>
> Presenta propuestas de objetos para revisión.
>
> Consulta la ayuda para conocer el proceso de intercambio.

Se verificaron ruta `deterministic`, cero construcciones del proveedor, referencias,
revalidación, cuota, duplicados e historial. Los negativos incluyen múltiples fuentes,
condiciones, negaciones, importes/plazos, fuentes no aprobadas o retiradas/modificadas,
gramática no reconocida y ausencia de petición explícita. No se amplió la gramática.

## 7. Tests focalizados antes de construir

**143/143 correctos**, sin fallos ni omisiones, sobre snapshot de solo lectura con SQLite
en memoria, proveedor mock, clave vacía y red deshabilitada. Módulos:

```text
chatbot.tests.test_sentence_planning
chatbot.tests.test_response_style
chatbot.tests.test_natural_answers
chatbot.tests.test_grounded_conversations
chatbot.tests.test_api
chatbot.tests.test_rate_limits
```

No se ejecutó la suite completa antes de construir ni se modificó código para estabilizarla.

## 8. Construcción Docker y contenido

Se reutilizó el runtime `trueca-runtime:chatbot-amd64-20261107`, verificado por ID
`sha256:2a2af441329a94ca4d5ec928acaddbb1817a20d081bc81b5aa1c9b9f1c08be9a`.
Sus 31 dependencias coinciden con el inventario previo. No se instalaron paquetes.

Se usaron `Dockerfile.candidate` y `.dockerignore` existentes, desde un contexto congelado
limpio fuera del checkout operativo:

```sh
docker build --platform linux/amd64 --network none --pull=false \
  --build-arg RUNTIME_IMAGE=trueca-runtime:chatbot-amd64-20261107 \
  -f Dockerfile.candidate -t trueca-candidate:chatbot-amd64-20261109-e34 .
```

**Un build**, sin reconstruir el runtime ni candidatas anteriores. Docker emitió la
advertencia estática `InvalidDefaultArgInFrom`: el argumento obligatorio no tiene default.
Se suministró explícitamente el runtime verificado; no impidió construir ni ejecutar.

La inspección dentro de la imagen comprobó los 556 hashes, planner, integración, tests,
KnowledgeService, instrucciones/contratos, CSS/JS, migraciones y catálogo compilado inglés
con 49 entradas. Los `.po`/`.mo` conservan sus hashes y las regresiones ES/EN pasan.
Sin `.env`, `local_settings.py`, claves, datos operativos ni configuración de entorno
incorporada. El perfil de staging es un módulo optativo, no el settings por defecto.
Después del build no se cambió código ejecutable ni se reconstruyó por documentación.

## 9. Suite PostgreSQL de la imagen

**877/877 correctos**, cero fallos, cero omisiones. Una única ejecución completa.
Base `test_` independiente en el PostgreSQL nuevo, clave vacía, mock y `CI=true`.
El contenedor comparte únicamente la red privada del servidor efímero; su único bind
es la contraseña sintética, no código. Incluye concurrencia, cuotas y contabilidad.

## 10. Suite SQLite de la imagen

De **877 pruebas: 833 correctas y 44 omitidas**, sin fallos. Las omisiones son pruebas
específicas de PostgreSQL, ejecutadas satisfactoriamente en la suite anterior. Una única
ejecución completa con memoria temporal y `--network none`, sin bind del repositorio.

## 11. JavaScript y accesibilidad

**32/32 correctos**, sin omisiones: `node --test chatbot/tests/js/test_chatbot.cjs`,
Node 22.10.0 existente. Se ejecutaron los tests y estáticos exportados de la candidata,
con hashes previamente contrastados con la imagen, sin instalar dependencias.
Cubren foco, apertura/cierre, modalidad, visual viewport, composición, errores, cuota,
duplicados, referencias y renderizado seguro. Son pruebas DOM simuladas; no una nueva
sesión visual en Safari/iPhone ni una prueba de teclado físico.

## 12. Migraciones y checks

Se verificaron los **63 archivos** de `chatbot/staging_migrations.json`, incluidos
`__init__.py`, sin hashes alterados. `check_chatbot_staging` comprobó identidad y
durabilidad (`fsync=on`, `synchronous_commit=on`). Tras revisar `migrate --plan`, se
aplicaron **58 migraciones**, tres del chatbot, solo a la base nueva. Post-plan sin pendientes.

`check_chatbot_staging --ready`, `manage.py check`, `makemigrations chatbot --check
--dry-run` y `pip check` terminaron correctamente. No se generó ninguna migración,
no se usó `--fake` ni se aplicó nada a bases existentes.

## 13. Smoke de staging

`validate_chatbot_staging`: **40/40 comprobaciones**, fixtures revertidas, sin SDK real.
CSRF/origen, diez preguntas y bloqueo de la undécima, frecuencia, reset, historial,
autenticación, propiedad, fuentes vigentes/retiradas, ambos modos y dry-run. El dry-run
no creó ledger ni envió llamadas y su JSON se conservó antes del cierre.

## 14. HTTP real y estáticos

Proxy nuevo Nginx en **127.0.0.1:8084**, sin exposición pública. **165 comprobaciones**:
128 bajo `extractive` y 37 bajo `grounded`. Son aserciones HTTP, incluidas aserciones
repetidas por pregunta; no 165 pruebas independientes de calidad del modelo.

Se verificaron salud, portal con avatar/widget, los tres endpoints, cookies independientes,
`HttpOnly`/`SameSite`, no-cache, CSRF y origen, aislamiento entre sesiones y entre usuarios,
métodos/rutas/JSON/longitudes inválidos, errores HTTP esperados y destinos de referencias.
Login real por HTTP de dos usuarios sintéticos; E34 e idempotencia en ambos modos.
Un anónimo completó diez preguntas, recibió bloqueo en la undécima y conservó la cuota
agotada tras reset. Un autenticado superó diez, manteniendo la protección de frecuencia.

Para no esperar un minuto se envejecieron únicamente las filas sintéticas de solicitudes
de esas conversaciones, como en el smoke existente; no se alteraron los límites efectivos.
`collectstatic` copió 194 archivos. Los hashes HTTP de CSS/JS del chatbot y FAQ coinciden
con la imagen. No hubo acceso a código Python mediante la ruta de estáticos ni rutas extra.

Para el segundo modo solo se recrearon app y proxy de este proyecto nuevo mediante un
override privado de una variable de entorno. Se verificó el modo y el ID ejecutado; no
se cambió Compose del repositorio ni se reconstruyó la imagen.

## 15. Backup/restauración e integridad

Dump sintético conservado y listado con `pg_restore --list`; restaurado en otra base nueva
del mismo PostgreSQL temporal. **43 tablas** con mismos recuentos y hashes de contenido,
**192 restricciones** válidas, **278 índices** y **37 secuencias** verificados. Las 58
migraciones y las tres del chatbot coinciden; ledger vacío y cero reservas pendientes.

SHA-256 del backup: `597127a58df9ab84498d7797c3d4609411285c862bacce0f35cfd0347d559027`.
Tamaño: 223.481 bytes. La base de restauración se eliminó después de verificarla.

La comparación textual inicial señaló cinco CHECK y luego dos índices parciales: PostgreSQL
representa de otra forma casts de arrays al releer un dump. Se verificaron las expresiones
exactas, nombres, columnas, conjuntos de valores y comportamiento SQL para valores válidos,
inválidos, vacíos y NULL en ambas bases. Las otras 187 definiciones de restricciones y 276
de índices coinciden literalmente. No se descartaron predicados ni se modificó la aplicación.
Se preservaron los fallos del comprobador auxiliar y su resolución; se reutilizó el mismo
dump, sin repetir build ni regresiones.

## 16. Aislamiento y cierre

Proyecto: `trueca-chatbot-validation-e34202611099bb740`, exclusivamente los Compose
existentes `compose.staging.yml` y `compose.staging.ephemeral.yml`.
PostgreSQL en RAM, secretos nuevos, volúmenes separados, red privada interna, UID no
privilegiado, filesystem de aplicación de solo lectura y proxy sin secretos. App y DB
sin puertos ni red exterior; solo el proxy publicó loopback. Proveedor mock, clave vacía,
OpenAI y evaluación real deshabilitados.

El preflight existente y la inspección de recursos reales comprobaron ausencia de
recursos compartidos. Tras conservar resultados, logs, preview y backup se retiró solo
este proyecto y sus volúmenes/redes. Quedan cero recursos temporales; 8084 cerrado;
dos secretos sintéticos eliminados y env-file deshabilitado. La candidata permanece.

## 17. Contenedores operativos y pilotos

Se compararon antes/después **30 contenedores preexistentes**: mismos IDs, imágenes,
montajes y pertenencia a redes. TRUEC@, su DB, worker detenido y los dos PostgreSQL de
pilotos cerrados conservaron estado, arranque y reinicios. **137 archivos privados de
pilotos** mantienen hashes. Los 556 archivos empaquetados del workspace tampoco cambiaron.

El servicio ajeno `plans24_nginx` ya estaba reiniciándose automáticamente: su contador
pasó de 229 a 244 y cambió su fecha de inicio por ese bucle. No se actuó sobre él.
No se usaron Compose productivo, `deploy/update.sh`, bases ni backups operativos.

## 18. Duraciones medidas

Duración de ejecución de comandos, sin sumar tiempos de análisis/redacción. No representan
rendimiento nativo de la VPS: AMD64 se ejecutó bajo emulación en Docker ARM64.

| Operación | Segundos medidos |
|---|---:|
| Inspección inicial completa | No cronometrada |
| Focalizados | 7,417 (Django: 3,441) |
| Build único | 2,385 |
| PostgreSQL completo | 66,910 (Django: 60,845) |
| SQLite completo | 34,793 (Django: 30,801) |
| JavaScript | 0,343 |
| Preflight Compose | 0,444 |
| Aplicación de migraciones | 4,672 |
| Smoke existente | 3,700 |
| HTTP extractive / grounded | 3,895 / 2,207 |
| Arranque HTTP / recreación para grounded | 7,581 / 8,028 |
| Backup y verificaciones, incluidos diagnósticos acotados | 12,049 acumulados de comandos |
| Retirada del proyecto temporal | 1,242 |

Los tiempos de comandos y sus códigos de salida se conservan en las evidencias privadas.

## 19. Defectos detectados

Sin defecto de aplicación que exigiera cambios o invalidación de la candidata. Dos fallos
del comparador auxiliar de restauración, resueltos con verificación estricta de equivalencia
de casts, según §15. Advertencia no bloqueante del argumento de Dockerfile, según §8.
Las suites y los smoke finales no presentan fallos. No se ocultaron los diagnósticos.

## 20. Riesgos y estado de verificación

| Estado | Alcance |
|---|---|
| Verificado | Digest/AMD64, código E34, dependencias, migraciones, cuotas, referencias, idempotencia, regresión, HTTP/estáticos, restore y aislamiento |
| Parcialmente verificado | Responsive/accesibilidad por tests DOM; fidelidad generativa por mocks e historial previo, sin nueva ejecución real |
| Pendiente | Revisión de configuración, HTTPS, secretos, historial de migraciones y datos propios de producción; distribución remota con digest preservado |
| No ejecutado | Nuevas llamadas OpenAI, micropilotos, despliegue productivo, teclado físico iPhone, medición nativa AMD64 |
| Bloqueante para reconstruir desde Git limpio | Fuentes y migraciones ignoradas/sin rastrear y cambios sin commit; snapshot e imagen sí conservados |

E34 determinista no demuestra calidad generativa general ni sustituye la revisión humana
de fidelidad. No se reabren UUID, presupuestos ni autorizaciones de pilotos anteriores.

## 21. Procedimiento de promoción posterior y rollback

Solo en una tarea futura con autorización expresa:

1. Identificar esta candidata por el digest completo del §4 y contrastar manifiesto,
   plataforma e inventario. Distribuir el artefacto ya construido mediante un registro
   revisado; comprobar que conserva el mismo digest. No reconstruir en la VPS ni usar
   un tag mutable como única identidad. Si la transferencia altera el digest, detenerse.
2. Revisar la configuración productiva para consumir esa imagen sin `build` ni bind del
   código. Utilizar exclusivamente DB, secretos, dominios, HTTPS y configuración propios
   de producción. No copiar fixtures, secretos, cookies o flags CI/staging. Mantener el
   mecanismo de desactivación del chatbot y sus límites. Ver variables en `chatbot/README.md`
   y `.env.example`; no copiar sus valores sintéticos como secretos.
3. Registrar el digest de la imagen realmente productiva anterior y su configuración.
   No asumir que la candidata `8ca…` es la imagen actualmente desplegada. Respaldar la
   DB productiva con un procedimiento de restauración revisado para ese entorno.
4. Contrastar archivos e historial de migraciones con la DB productiva y revisar su plan
   antes de cualquier aplicación. La corrección E34 no introduce migraciones. No utilizar
   `--fake`, regenerar históricos ni restaurar el dump sintético en producción.
5. Ejecutar, ya autorizado, la promoción del mismo artefacto con la configuración revisada,
   collectstatic y comprobaciones de salud/HTTP/CSRF/propiedad. No añadir llamadas reales
   de prueba con coste sin autorización separada. Revisar fuentes públicas reales sin
   sembrar el corpus sintético. La validación presente no prueba compatibilidad de los
   datos ni de la configuración productiva aún no inspeccionados.
6. Para rollback, restaurar imagen/configuración productivas anteriores o desactivar
   `CHATBOT_ENABLED` según el incidente. No borrar historial ni revertir esquema con
   operaciones destructivas automáticas. Cualquier restauración de DB requiere un plan
   autorizado compatible con los datos escritos desde la promoción.

Estos pasos son documentación: no se publicó ni desplegó nada durante esta tarea.

## 22. Dictamen y entrega

**Candidata AMD64 técnicamente validada y congelada, preparada para una futura promoción
autorizada del mismo artefacto.** E34 responde en tres frases localmente en ambos modos;
no se amplían permisos, gramática o validadores. Cero llamadas reales a OpenAI.

Se añadieron este informe y los JSON específicos de candidata/resultados, más la excepción
documental de `.gitignore`. No se modificó código ejecutable, frontend, dependencias ni
Compose. No hubo commits automáticos. Los manifiestos históricos siguen intactos.

Las evidencias detalladas, snapshot y backup sintético se conservan fuera del build context
en `.chatbot-releases/20261109-e34/`. El éxito técnico no equivale a autorización de
producción ni a aprobación de una modalidad generativa general.
