# TRUEC@ — Validación final de Fase 2 (Prompt 5)

Fecha: 06/11/2026. Dictamen: **B — Listo para staging**, con proveedor mock por defecto.
**No se recomienda producción todavía.** No se han iniciado llamadas reales a OpenAI,
despliegues, limpiezas ni migraciones sobre bases reales. El piloto presupuestado queda
desactivado y requiere una autorización posterior específica.

## 1. Resumen ejecutivo y alcance de las evidencias

Se revisaron los entregables reales de Prompts 1–4, se corrigieron defectos de cobertura,
privacidad, contraste y procedencia normativa, y se añadió un ejecutor independiente con
presupuesto persistente. Las pruebas distinguen inspección de código, bases reales pero
sintéticas, SDK simulado, mediciones locales y verificaciones humanas pendientes.

Los 90 casos se ejecutan en ambos modos con el mismo corpus sintético. Sus oráculos no se
construyen a partir de las respuestas del código: las condiciones de registro/activación,
aceptación física, no premio por publicar y decisión humana proceden de decisiones aprobadas.
No se desbloquean plazo de recogida, cargo de reserva ni pequeños electrodomésticos.

Evidencia versionada: [dataset](../../chatbot/evaluation/data/phase2-v1.json),
[corpus sintético](../../chatbot/evaluation/data/corpus-v1.json),
[piloto de diez preguntas](../../chatbot/evaluation/data/pilot-v1.json) y
[resultados por caso y métricas](phase2_offline_results.json).
Los textos de este último archivo son salidas de mocks, no de OpenAI.

## 2. Arquitectura final

API → validaciones/propiedad → reserva corta de cuota → resolver local → KnowledgeService
público → respuesta local o proveedor único → grounding/revalidación → persistencia corta
con referencias. Se conservan GET session, POST messages y POST reset, CSRF, cuotas y contratos.
El evaluador no utiliza conversaciones ciudadanas ni cambia la API para facilitar pruebas.

El nuevo camino de evaluación es: dataset permitido → gates de entorno/modelo/tarifas →
bloqueo exclusivo PostgreSQL → reserva monetaria atómica → intención de envío duradera →
SDK existente sin retries → contabilidad de uso fiable o reserva máxima retenida → resultado.
No incorpora agentes, tools, búsqueda privada, servicios, dependencias o paneles nuevos.

Referencias: [services](../../chatbot/services.py), [answering](../../chatbot/answering.py),
[providers](../../chatbot/providers.py), [evaluation](../../chatbot/evaluation/runner.py),
[ledger](../../chatbot/evaluation/ledger.py).

## 3. Estado verificado de Prompts 1–4

| Componente | Estado | Evidencia / límite |
| --- | --- | --- |
| Conversaciones, sesiones y 10 respuestas anónimas | Verificado | Tests API y concurrencia PostgreSQL; no contadores en cookies |
| Auth, CSRF, separación de propietarios, login/logout/BFCache | Verificado en tests | Pruebas Django y JS; sin pentest real ni sesiones de producción |
| KnowledgeService, FTS y fallback SQLite | Verificado en fixtures | Campos públicos, jerarquía/ancestros, fuentes ocultas, autoridad y límites |
| Contexto conversacional | Parcialmente verificado | Resolver léxico acotado; no NLP/semántica general |
| Reglas locales y bloqueos | Verificado en escenarios | Registro y activación +100; entrega físicamente aceptada +100; publicar no premia por sí solo; IA no decide |
| Extractive | Verificado estructuralmente | Solo unidades íntegras; calidad de selección real pendiente |
| Grounded | Parcialmente verificado | Esquema/citas/controles/fallback probados; calidad real pendiente |
| Referencias persistentes | Verificado | JSONField, hashes, versión, retirada y consultas acotadas |
| Widget responsive y fuentes | Verificado en Chromium | 28 combinaciones; no Safari/iPhone físico |
| Aplicación de migraciones en entornos reales | Pendiente operativo | No se ejecutó como parte de esta tarea |
| Evaluación OpenAI real y facturación | Pendiente / bloqueada por configuración | ALLOW_LIVE=False; tarifas e identidad sin aprobación |

La auditoría histórica [Prompt 1](phase2_knowledge_audit.md) conserva expectativas antiguas
sobre plazo/cargo. No autoriza esos claims: prevalecen las restricciones expresas actuales.
La [documentación de Prompt 4](../../chatbot/README.md) queda complementada por este informe.

## 4. Dataset reproducible y rúbrica

`phase2-v1` contiene 90 casos E01–E90 en 15 categorías: funcionamiento, registro, publicación,
puntos, reservas, entrega, recogida, admisión, centros, contexto, ambigüedad, adversarios,
privacidad, ES/EN y fuera de alcance (algunas categorías se agrupan en los informes).
Cada caso conserva pregunta, idioma, historial sintético, fixtures, fuentes esperadas, claims,
condiciones/negaciones, bloqueos, comportamiento, modalidad, necesidad de OpenAI y criterios.
El loader solo admite nombres versionados; no recibe archivos arbitrarios de CLI.

Hay respuestas directas, síntesis, procedimientos, referencias, aclaraciones, abstenciones y
ataques. Los datos normativos de FAQ son fixtures, no aprobación editorial de nuevas reglas.
Los casos críticos adicionales de FAQ contradictoria y apertura en festivos tienen tests
específicos. Los mensajes históricos y su carga también se prueban por separado.

La [rúbrica 0–4](../../chatbot/evaluation/RUBRIC.md) separa exactitud, fidelidad, completitud,
claridad, concisión, idioma y referencias. Un fallo crítico invalida la respuesta aunque la
media sea alta. La [plantilla humana](phase2_review_template.md) queda sin puntuar; no se utiliza
otro modelo como juez. Los campos semantic_scores son null deliberadamente.

## 5. Tests ejecutados en esta tarea

Los resultados finales se registran en la tabla de cierre al final del documento. No se
atribuyen las cifras históricas del README a esta ejecución. Las suites usan bases nuevas,
CI, clave vacía y contenedores sin red exterior. Los tests de SDK tienen clientes simulados;
el guard global del SDK y la falta de red constituyen protecciones adicionales.

Comandos reproducibles, desde app (ejecutados dentro de la imagen existente):

```bash
python manage.py test chatbot --settings=tenetrueca.test_settings --noinput
python manage.py test --settings=tenetrueca.test_settings --noinput
python manage.py test --settings=tenetrueca.test_postgres_settings --noinput
python manage.py test chatbot.tests.test_evaluation_budget --settings=tenetrueca.test_postgres_settings --noinput
node --test chatbot/tests/js/test_chatbot.cjs
```

Las omisiones SQLite corresponden a row/advisory locks, migraciones reales y otros tests que
requieren PostgreSQL; la suite PostgreSQL no las omite. Los mensajes de errores esperados,
Turnstile y creación de datos demo provienen de tests, nunca de datos reales modificados.

## 6. Evaluación de extractive

Mantiene integridad literal, condiciones y negaciones de las unidades seleccionadas. Las
reglas suficientes y los centros inequívocos se responden localmente. Una FAQ no puede
modificar los importes aprobados ni omitir sus condiciones mediante una copia literal:
las unidades numéricas de puntos deben ser una verbalización aprobada íntegra, o se excluyen.
También se rechazan formulaciones directas que atribuyen puntos a publicar.

La integridad textual no prueba relevancia: una fuente puede ser pública y literal pero
solo parecida al tema. La precisión de candidatos se informa por separado y todavía requiere
revisión editorial/semántica. Extractive se recomienda como modalidad inicial de staging.

## 7. Evaluación de grounded

Permite párrafos nuevos únicamente sobre FAQ explicativas elegibles. Se comprueban estructura,
pertenencia de fuentes, citas literales, atribución declarada, señales de riesgo y versiones.
Cantidades, condiciones, prohibiciones y centros exactos permanecen en rutas estrictas.
Validación fallida → fallback extractivo/abstención, sin otra llamada. Fallo técnico → sin cuota.

No existe verificador semántico general: coincidencia léxica y pertenencia literal no prueban
que una reformulación esté implicada por la fuente. Los ejemplos fieles/maliciosos simulados
prueban controles y escenarios, no naturalidad real ni ausencia general de alucinaciones.
La configuración de producción sigue `CHATBOT_GENERATION_MODE=extractive`.

## 8. Comparativa verificable de modos

| Métrica offline | Extractive | Grounded |
| --- | --- | --- |
| Casos por backend | 90 | 90 |
| Rutas/comportamientos previstos | 90 | 90 |
| Invocaciones del proveedor simulado | 26 | 26 |
| Violaciones detectadas por oráculos acotados | 0 | 0 |
| Puntuación semántica OpenAI real | No medida | No medida |
| Tokens facturados / latencia OpenAI | No medidos | No medidos |

En configuración grounded, 12 preguntas generativas usaron esquema grounded y 14 permanecieron
extractive por sus fuentes/señales críticas; 64 casos fueron locales. No se forzaron llamadas
para igualar cantidades. El mock selecciona la primera unidad y no es una prueba de síntesis
semántica multifuente: el recall de referencias finales refleja esa limitación.

## 9. KnowledgeService y recuperación

Métricas macro sobre 43 casos con fuentes esperadas y 47 casos negativos. Cada cláusula
se resuelve por el mismo splitter/resolver; se unen los candidatos, deduplicados por ID,
con hasta cinco unidades por consulta acotada. No se interpreta SearchRank como veracidad.

| Backend | Precisión candidatos | Recall candidatos | Precisión tras restricción | Recall tras restricción | Recall refs finales mock |
| --- | ---: | ---: | ---: | ---: | ---: |
| sqlite | 0.403 | 1.000 | 0.788 | 1.000 | 0.895 |
| postgres | 0.384 | 1.000 | 0.769 | 1.000 | 0.895 |

Las medias positivas excluyen los negativos para no inflar precisión con vacíos. La tasa
negativa de exclusión tras restricción es 0,957 en ambos backends; una fuente candidata no
implica que el orquestador la muestre. Se conserva el detalle por caso, no solo el promedio.
Las métricas sintéticas no son una evaluación de todo el corpus municipal real.

FAQ publicadas/despublicadas/editadas, categorías con ancestros inactivos, centros ocultos,
idiomas, límites y FAQ excesivas tienen pruebas dedicadas. Una unidad demasiado larga se
omite íntegramente; no se recortan excepciones para encajar. Los conflictos permanecen cerrados.

## 10. Referencias y persistencia

[references.py](../../chatbot/references.py) verifica huellas, versión, aprobación y URL al
mostrar historial. Cambios/despublicación/eliminación → solo unavailable, sin título anterior
ni URL. Se mantiene el texto histórico sin reescritura ni llamadas de reconstrucción.
Hasta tres referencias por respuesta y 100 IDs históricos distintos por carga; el exceso
no se revela como vigente sin verificar. FAQ usa lotes por PK; no hay consulta individual
por enlace. Fuentes estructuradas sin URL no inventan enlaces. Mapas solo desde el servicio
público existente, con HTTPS/dominio/coordenadas permitidos.

## 11. Seguridad

| Control | Estado y evidencia |
| --- | --- |
| CSRF y Origin | Verificado: middleware nativo, sin exempt; ambos tipos de usuario y origen externo |
| XSS | Verificado en tests: textContent, nodos DOM, IDs/URLs del servidor, protocolos rechazados |
| Propiedad y sesión | Verificado: UUID ajenos 404, aislamiento, rotación login/logout y caché/BFCache |
| JSON/Unicode/longitud | Verificado: límites servidor, NUL/surrogates, cuerpos grandes/profundos |
| Cuota/idempotencia | Verificado: reserva atómica, último slot, varias pestañas, errores y duplicados |
| Prompt injection | Parcial: esquema/privilegios/fuentes/controles léxicos; adversarios simulados, no modelo real |
| Herramientas OpenAI | Verificado en payloads: sin SQL, HTTP tools, ejecución ni acciones sobre objetos |
| Reglas críticas | Verificado en escenarios independientes; registro/entrega y condiciones no se delegan a texto libre |
| Centro abierto ahora/festivos | Verificado: horario regular no basta; se responde falta de evidencia |
| Secretos/logs | Verificado en tests: sin claves en frontend, textos del usuario ni cuerpos SDK en logs |

No hay acceso a saldos, reservas privadas, cuentas o expedientes para recuperar conocimiento.
Los textos públicos son datos no confiables, no instrucciones con permisos. Ninguna garantía
léxica se presenta como defensa semántica absoluta frente a futuras variantes.

## 12. Privacidad y mantenimiento

Se envían solo pregunta actual con redacción de patrones comunes, evidencia pública y esquema.
No se envían mensajes privados históricos, perfiles, saldos o identificadores de sesión/usuario.
La redacción de PII no es universal; el usuario puede introducir datos sensibles en su mensaje
original, que se conserva en su historial propio conforme a la política existente.

Retención configurable de 30 días y cleanup en modo dry-run por defecto: inspeccionado y
probado en bases sintéticas, pero no ejecutado aquí sobre datos reales ni programado un job.
Las reservas/cuotas no se reinician limpiando una sesión Django todavía existente.
Los administradores con acceso directo a la base siguen siendo usuarios privilegiados;
no se añadió exposición administrativa o dashboard del chatbot/evaluador.

El ledger de evaluación contiene preguntas sintéticas, salidas basadas en fuentes públicas,
metadatos de presupuesto y recibos de uso. No contiene conversaciones reales ni API keys.
Sus contadores no se eliminan con la retención del chat: borrarlos permitiría reusar presupuesto.
Los exports usan escritura atómica, fsync y permisos 0600; PostgreSQL es la autoridad.

## 13. Rendimiento medido

Docker local, Python 3.12.15, Django 5.2.18, SDK 2.54.0, PostgreSQL 17 y SQLite efímeros.
20 muestras por perfil; ejecución secuencial, sin red exterior y con query capture. No se
reservó CPU exclusiva frente a otras actividades. Percentil empírico de rango más próximo;
no es garantía de SLO ni latencia real de OpenAI. El endpoint se mide con Django Client,
sin red HTTP/TLS y con la excepción CSRF habitual del test client; CSRF se prueba aparte.

| Perfil PostgreSQL | n | Mediana ms | p95 ms | Máximo ms | Consultas constantes |
| --- | ---: | ---: | ---: | ---: | ---: |
| deterministic | 20 | 25.127 | 29.367 | 31.635 | 31 |
| clarification | 20 | 9.902 | 10.275 | 10.921 | 25 |
| no_evidence | 20 | 16.276 | 26.175 | 31.802 | 28 |
| conflict | 20 | 10.06 | 11.286 | 13.48 | 25 |
| extractive_mock | 20 | 27.127 | 47.279 | 59.367 | 32 |
| grounded_mock | 20 | 29.896 | 65.697 | 182.058 | 32 |

Las fases completas, medianas/p95/máximos por backend están en el JSON: validación,
resolución, recuperación con ranking, componente Python del ranking, respuesta local,
revalidación, presentación de referencias, reserva, persistencia y endpoint total.
Son sondas independientes; no se suman para reconstruir un endpoint. La persistencia incluye
su snapshot. El ranking SQL permanece integrado en FTS/EXPLAIN; no se inventa una medición
pura del SearchRank separada de la consulta. Tokens reales y latencia de inferencia: pendientes.

| Historial propio sintético PostgreSQL | Mediana ms | p95 ms | Máximo ms | Consultas |
| --- | ---: | ---: | ---: | ---: |
| 20 mensajes | 2.751 | 2.908 | 2.951 | 7 |
| 200 mensajes | 5.078 | 5.386 | 6.245 | 7 |
| 1000 mensajes | 17.157 | 51.894 | 54.35 | 7 |

Las siete consultas constantes descartan N+1 en este sondeo de fuentes repetidas. Tiempo,
serialización y tamaño de respuesta crecen con mensajes: la API histórica todavía no pagina.
Debe operarse la retención y revisarse paginación si el uso real crece; no se añade esa función
sin alcance específico. No se ejecutaron benchmarks de producción.

El EXPLAIN ANALYZE del corpus pequeño se conserva en el JSON. El escaneo secuencial y orden
FTS sobre pocas filas no justifican añadir un GIN ni una migración de índices. No extrapolar
su coste al corpus real completo. Se reutilizan índices de historial y restricciones únicas.
Los bytes de contexto/esquema/payload son diagnósticos de representación, no tokens facturados
ni la cota utilizada para admitir gasto.

## 14. UX y accesibilidad

28 combinaciones en Chrome headless: 1440×900, 1024×768, 768×1024, 768×360, 390×844,
320×568 y 390×320, con anónimo/autenticado y ES/EN. Se revisaron cuatro estados, avatar
contenido, safe-area CSS, panel/compositor/cierre, scroll, fuentes largas/retiradas, foco,
Escape, restauración, anclas FAQ, texto ampliado y ausencia de desplazamiento del hero.
Se calculó y comprobó contraste de texto pequeño ≥4,5:1 en los controles revisados.

Los colores secundarios sobre fondos de burbuja/placeholder estaban entre 4,16:1 y 4,45:1;
se sustituyen por el azul marino existente dentro del widget. Se conserva el diseño del portal.
La simulación visualViewport no es teclado físico. Safari iPhone, teclados iOS/Android,
zoom de navegador/OS y lectura con tecnologías de asistencia reales quedan pendientes.
Las pruebas DOM/ARIA no equivalen a probar un lector de pantalla.

## 15. Defectos corregidos

1. Faltaba equivalencia inglesa publication → publicar: la consulta equivalente se abstenía.
2. El filtro numérico trataba «en 3 frases» como una condición del negocio; se separa solo
   esa petición de estilo, sin eliminar números de límites reales.
3. Una dirección privada de entrega pedía centro público: ahora se abstiene sin consultar fuentes.
4. Una FAQ podía contradecir cantidades/condiciones de puntos y pasar como copia literal:
   se exige unidad normativa aprobada íntegra, con exclusión conservadora y revalidación histórica.
5. Se evita responder apertura actual/festivos a partir de horario regular publicado.
6. Contraste insuficiente de textos secundarios dentro del widget: corrección localizada.
7. El smoke manual antiguo permitía evaluar sin ledger: `check_chatbot_openai` se retira y
   rechaza llamadas; el procedimiento vigente usa exclusivamente el nuevo ejecutor presupuestado.
8. El SDK actual incluye cache_write_tokens: se contemplan su coste aprobado y el esquema;
   SDK/uso con componentes nuevos desconocidos bloquea la admisión o mantiene reserva completa.
9. Se preserva contabilidad y se señalan intentos sin resultado si falla el guardado/export.

No se cambian reglas de negocio ni importes operativos. El control normativo nuevo puede
omitir FAQ válidas que no usen la verbalización canónica; esa cautela se documenta.

## 16. Pendientes y bloqueantes por ámbito

| Hallazgo | Estado | Efecto |
| --- | --- | --- |
| Calidad semántica/idioma de OpenAI real | Pendiente | Bloquea recomendar grounded en producción |
| Corpus real y relevancia de candidatos | Parcialmente verificado | Revisión editorial/recuperación necesaria antes de producción |
| Claims D1/D3/D6 | Pendiente de decisión autorizada | Siguen bloqueados; no bloquea staging prudente |
| Tarifas/UUID/proyecto/entorno aprobados | Bloqueante para ejecución real | Default seguro; no es fallo del piloto desactivado |
| Aplicar 0003 en staging con autorización | Pendiente operativo | Sin tablas no se admite el evaluador real |
| HTTPS, proxy, hosts, secreto, HSTS y estáticos reales | Pendiente | Bloqueante para producción; no se cambian globalmente otras apps |
| Safari/teclado/lector de pantalla reales | Pendiente | Emulación no sustituye validación física |
| Historial muy grande sin paginación | Riesgo operativo | Retención y medición posterior; consultas sin N+1 no eliminan coste lineal |

No quedan fallos críticos conocidos reproducibles en los escenarios automatizados finales.
Eso no demuestra ausencia de fallos futuros ni valida operativamente un entorno de producción.

## 17. Presupuesto implementado y persistencia

[Modelos](../../chatbot/evaluation/models.py): ChatEvaluationRun y ChatEvaluationAttempt,
independientes de conversación/cuota/moderación. `0003_evaluation_budget` solo crea sus tablas,
PK/FK, unicidad y checks de seguridad. No hay data migration ni borrado de historial.
La inspección local de migrate --plan muestra solo 0003 pendiente; 0002 ya no estaba pendiente
en ese momento. No se atribuye su aplicación a esta tarea.

UUID configurado debe coincidir con --run-id. No se genera una nueva autorización al invocar
el comando. Registro con huella de dataset/corpus, código, runtime, modelo, precios/proyecto,
modos, límites, intentos, uso, pico comprometido, resultados, pendientes y motivo de parada.
La misma autorización/configuración no se puede reiniciar; --resume recupera su saldo real.
Cambiar output o repetir CLI no crea otra bolsa. Cambiar límites, fuentes comparadas, código
u opciones relevantes se rechaza, sin poner el contador a cero.

Los checks de BD fuerzan presupuesto ≤1 USD, admitted≤20 y sent≤admitted, importe comprometido
y pico≤presupuesto. Solo se permiten límites menores por configuración/CLI. Un bloqueo
advisory exclusivo en una sesión dedicada impide dos ejecuciones de la misma autorización;
row locks protegen admisiones. El piloto real exige PostgreSQL con fsync y synchronous_commit
on. Ninguna transacción permanece abierta mientras espera al proveedor.

## 18. Cota conservadora previa y tarifas

No se usan caracteres/4, ratios bytes/tokens ni costes medidos después para decidir admisión.
La estrategia disponible reserva **toda la ventana máxima documentada del modelo** como techo
de entrada, cubriendo instrucciones, esquema, pregunta, evidencia y framing. Para el perfil
actual gpt-6-luna son 1.050.000 tokens. El límite de salida inicial es 400, incluyendo tokens
no visibles/razonamiento. Es una cota deliberadamente holgada: si --max-input-tokens es menor,
se rechaza incluso una pregunta corta. No se instaló un tokenizador ni se consulta el endpoint
de conteo. Un modelo sin perfil verificable se rechaza.

Fuentes oficiales revisadas: [modelo](https://developers.openai.com/api/docs/models/gpt-6-luna)
y [conteo/límites de tokens](https://developers.openai.com/api/docs/guides/token-counting).
La ventana limita el contexto del modelo; suponer una tarifa plana corta para esa ventana
sería incorrecto. Deben aprobarse precios máximos aplicables a todo el intervalo y componentes.

La [plantilla de tarifas](../../chatbot/evaluation/pricing.example.json) tiene approved=false,
coverage_approved=false y valores monetarios/fecha nulos. No autoriza gastos. Exige modelo
exacto, USD, tasas máximas de entrada y salida, tasa adicional por tokens de entrada para
cache writes/sobrecargos (tasa positiva obligatoria), importe fijo si aplica, margen≥1,10, fecha de verificación de hasta
siete días y URLs oficiales. Se validan de nuevo antes de cada envío; cambios bloquean continuar.

La página [oficial de precios](https://developers.openai.com/api/docs/pricing) consultada muestra
para Standard/contexto corto de Luna 0,10 USD/1M entrada, 0,50 salida, 0,01 cacheada y tasas de
escritura 0,125/0,25. **No se trasladan esos datos a una tarifa operativa autorizada**: el
operador debe verificar contexto largo, retenciones, región y cualquier recargo del proyecto.
Si no puede acotar todos los componentes, la petición se rechaza. No se obtienen precios por API.

Para entradas I, salida máxima O, tasas aprobadas Ri, Ro, adicionales Rs, fijo F y margen M:

`U = ceil_1e-9_USD(M × ((I × (Ri + Rs) + O × Ro) / 1.000.000 + F))`

Antes de emitir: committed+U≤budget y admitted<max_requests; se guarda U en una transacción.
Decimal y redondeo superior de costes; presupuestos no representables con nueve decimales se
rechazan, nunca se redondean hacia arriba para ampliar autorización. Se ignoran descuentos
cacheados al reservar; Rs debe cubrir conservadoramente todos los adicionales posibles.

## 19. Uso conocido, desconocido e interrupciones

Uso fiable requiere modelo autorizado, enteros no negativos, total coherente, entrada/salida
dentro de techos y componentes reconocidos del SDK: input/output, cached, cache_write y reasoning.
Se calcula un techo derivado de uso real reportado, con tarifas completas conservadoras.
Solo se libera U menos ese techo; no se etiqueta como factura real. Incomplete o error con
uso fiable se contabiliza aunque no haya respuesta válida.

Timeout, 429/500 sin uso, ausencia de uso, modelo/componente desconocido o interrupción →
reserva máxima retenida. No se supone factura cero. Antes de continuar, --resume conserva
reservas y marca intentos interrumpidos unknown; nunca reenvía la misma pareja caso/modo.
No hay retries automáticos de red, validación o regeneración. El contador enviado representa
intención de envío persistida antes del SDK; puede sobrecontar si se mata el proceso antes
llegar a la red, pero no infracontar para ampliar presupuesto.

Fallo al guardar respuesta/exportar no elimina el intento ni reinicia fondos. El ledger se
recupera primero; los intentos sin resultado se enumeran y no se repiten automáticamente.
La suma histórica de reservas liberadas puede superar 1 USD: la invariante correcta es gasto
local contabilizado + reservas aún comprometidas, cuyo pico nunca supera el tope autorizado.

## 20. Pruebas presupuestarias

`test_evaluation_budget.py` cubre 37 tests, con subcasos para precios ausentes/no aprobados/
caducados, modelo/moneda/metadata erróneos, límites de entrada/salida/dinero, reserva previa,
liberación por uso, redondeo, errores 429/500/timeout/incomplete, esquema de uso futuro,
doble envío, reinicio/resume, configuración/fuentes cambiadas, fallos de persistencia/export,
guards CI/startup/entorno, dry-run sin cliente y flags que intentan ampliar autorización.

Pruebas PostgreSQL: doce reservas concurrentes con coste alto admiten solo la que cabe;
24 concurrentes baratas admiten como máximo 20; la sesión duplicada no obtiene el lock.
Veinte intentos secuenciales con uso conocido permiten comprobar pico≤1 y rechazo del 21.
La comparación completa de diez preguntas y resume se prueba con SDK simulado, sin forzar
llamadas locales. Sus precios son **fabricados para aritmética**, no aprobación de precios reales.
No se infiere una garantía absoluta sobre la facturación del proveedor de esos tests.

## 21. Procedimiento futuro: diez preguntas, hasta veinte llamadas

No ejecutado aquí. Antes, obtener autorización explícita que identifique UUID estable,
proyecto OpenAI, modelo, dataset pilot-v1, ambos modos, máximo 1 USD y 20 intentos. No usar
uuidgen en cada invocación ni automatizar autorizaciones nuevas para eludir un saldo agotado.

1. Preparar staging aislado con respaldo verificado, versión fijada y PostgreSQL duradero;
   aplicar migraciones autorizadas. No usar datos de conversaciones de producción.
2. Verificar manualmente todas las tarifas/condiciones oficiales y rellenar un archivo de
   precios protegido. No copiar los importes ficticios de tests ni aprobar cobertura incierta.
3. Configurar solo en el proceso de evaluación: CHATBOT_ENABLED=True, CHATBOT_PROVIDER=openai,
   clave existente en servidor, CHATBOT_EVAL_ALLOW_LIVE=True,
   CHATBOT_EVAL_ENVIRONMENT=staging, CHATBOT_EVAL_AUTHORIZATION_ID=<UUID aprobado fijo>,
   CHATBOT_EVAL_PROJECT_REF=<proj autorizado> y CHATBOT_EVAL_PRICING_FILE=<archivo>.
   Mantener los máximos 1.00/20/1050000/400 y TTL 7 días de .env.example.
   No activar OpenAI ni alterar flags de moderación.
4. Previsualizar sin SDK real y revisar fuentes públicas disponibles:

```bash
python manage.py evaluate_chatbot_live --dry-run --dataset pilot-v1 --mode both \
  --model gpt-6-luna --run-id UUID-FIJO-APROBADO --max-requests 20 \
  --max-budget-usd 1.00 --max-input-tokens 1050000 --max-output-tokens 400 \
  --output /ruta-protegida/preview.json
```

5. **Solo después de la autorización posterior**, ejecutar el mismo comando sustituyendo
   --dry-run por --allow-live y un archivo de resultado. Los dos modos se recorren secuencialmente.
   Ocho preguntas del piloto son candidatas generativas y dos son locales: hasta 16 llamadas
   si todas tienen evidencia suficiente, nunca llamadas artificiales para llegar a veinte.
6. Revisar respuestas/citas con la rúbrica, uso, importes, pico, errores y pendientes. Si se
   interrumpe, reusar exactamente UUID/configuración y añadir --resume. Los errores desconocidos
   conservan U; el programa se detiene si no puede admitir el siguiente intento.

Sin --allow-live el comportamiento es siempre dry-run; con --dry-run también aunque se pase
--allow-live. CI/tests/startup/migraciones no pueden habilitar red mediante esos flags. El
comando previo `check_chatbot_openai` está retirado para no ofrecer una vía de evaluación
sin presupuesto. No se ejecuta el piloto desde un pipeline CI ni se desactiva su guard.

## 22. Informe económico parametrizado

No hay tokens facturados, gasto medido de cuenta ni promedio real de OpenAI en esta tarea.
Definir L=fracción local, E=errores por llamada generativa, C=techo de coste calculado por
uso de una respuesta generativa válida y U=reserva retenida para errores desconocidos.
Con una llamada máxima por consulta: coste diario conservador teórico
`N × (1−L) × ((1−E) × C + E × U)`; sin retries. Por respuesta válida, dividir por
`N × (L + (1−L) × (1−E))`, indicando cómo se cuentan las abstenciones válidas.

| Consultas/día | Escenario parametrizado, no medición |
| --- | --- |
| 100 | 100 × (1−L) × ((1−E)C + EU) |
| 500 | 500 × (1−L) × ((1−E)C + EU) |
| 1.000 | 1.000 × (1−L) × ((1−E)C + EU) |

C depende de tarifas aprobadas y tokens efectivos; no se inventan valores de L/E/tokens ni
se extrapola la proporción local de un dataset dirigido al tráfico municipal real.

## 23. Riesgos de facturación y alcance del límite

Es un **límite local de admisión de peticiones**, no garantía de factura total. Precios
incorrectos/cambiados, componentes no previstos, diferencia de factura/usage, otros procesos,
API pública del chatbot, moderación u otras apps/cuentas compartidas están fuera de su control.
Si la incertidumbre se conoce antes, se rechaza la admisión; tras respuesta desconocida se
retiene el máximo. No se comparten bolsas/contadores con moderación ni se modifican sus límites.

Revisar controles de plataforma además del ledger. La documentación oficial distingue
[hard spend limits y alertas](https://developers.openai.com/api/docs/guides/spend-limits):
un límite efectivo rechaza peticiones afectadas, una alerta solo notifica. Verificar disponibilidad,
proyecto y alcance; no presentar una alerta como bloqueo. No se configuraron controles externos.
Una cuenta/proyecto compartido exige autorización y su gasto externo no queda limitado por este piloto.

## 24. Checklist de staging

- [ ] Backup restaurable y versión/imagen/dependencias fijadas; comparar manifest y hash.
- [ ] Revisar migrate --plan, autorizar migrate chatbot y comprobar 0002/0003 sin borrar datos.
- [ ] Ejecutar check y collectstatic; validar que el servidor real entrega CSS/JS/íconos.
- [ ] Proveedor mock, ambos modos; sesión anónima/autenticada, historial, fuentes y retirada.
- [ ] CSRF/Origin, UUID ajeno, login/logout/BFCache, reset sin reponer cuota, pregunta 11 y frecuencia.
- [ ] Desktop/móvil/teclado/lector de pantalla; mensajes largos, errores, cuota y fuentes.
- [ ] Privacidad, logs, permisos, política/cron de retención 30 días y backups.
- [ ] Desactivación CHATBOT_ENABLED=False independiente de moderación.
- [ ] Aprobación editorial y tarifas/UUID/proyecto; evaluación real únicamente tras autorización nueva.

## 25. Despliegue preparado, no ejecutado

Verificar DEBUG=False, ALLOWED_HOSTS reales, CSRF_TRUSTED_ORIGINS/Origin, cookies seguras,
HTTPS/proxy confiable, secreto adecuado, HSTS/redirect según arquitectura y permisos del volumen.
El perfil de tests produjo security.W004/W008/W009; no se utiliza como perfil de producción.
Cookies de sesión/CSRF seguras y HttpOnly de sesión están activas en ese perfil; esto no prueba
HTTPS ni hosts reales de producción.

Compose inspeccionado expone Gunicorn/app y PostgreSQL; no demuestra un proxy/servidor de
estáticos productivo. Con DEBUG=False, urls.py no sirve staticfiles de desarrollo y no hay
WhiteNoise añadido. Collectstatic probado solo acredita los archivos: servir STATIC_ROOT por
la infraestructura autorizada y probarlo es un requisito operativo previo a producción.
No se añade un servicio ni se modifica Docker para suplir infraestructura no verificada.

Secuencia posterior autorizada: backup → build reproducible → migrate --plan → migrate chatbot
→ check → collectstatic → despliegue de release/estáticos en staging → checklist → decisión.
No arrancar/reconfigurar workers de moderación como parte de esta validación. Variables nuevas
y plantilla de precios están documentadas sin claves ni activación real en .env.example.

## 26. Rollback y recuperación presupuestaria

Desactivar admisiones con CHATBOT_EVAL_ALLOW_LIVE=False y detener el comando activo; cualquier
petición ya enviada puede seguir facturándose, por eso se conserva su reserva. Desactivar el
widget/API mediante CHATBOT_ENABLED=False sin afectar moderación. Revertir grounded a
extractive y restaurar release/estáticos según operación habitual.

Conservar columnas y tablas aditivas de 0002/0003; no bajar migraciones ni limpiar contadores.
Un release anterior puede seguir escribiendo ChatMessage gracias al default de BD de sources.
No invocar el smoke antiguo ni evaluar desde código sin el guard presupuestario restaurado.
No se creó ningún job de evaluación para desprogramar. Historial/referencias no se reescriben.

Una restauración de BD anterior a gasto real vuelve incierto el ledger: bloquear evaluación,
no reusar automáticamente ese UUID ni su saldo, y reconciliar conservadoramente antes de una
nueva autorización explícita. El rollback no puede deshacer una llamada ya facturada ni
recuperar un resultado perdido sin otra generación, que aquí está prohibida automáticamente.

## 27. Modalidad recomendada y preparación posterior

Staging: extractive como inicio, mock por defecto, grounded solo con fixtures/controles y
revisión. Producción: pendiente de corpus, infraestructura, UX física y evaluación humana real;
no activar grounded por haber pasado mocks. El piloto real queda preparado pero desactivado.
No se implementa Fase 3, dashboard, búsqueda de objetos, ni nuevas reglas de negocio.

## 28. Dictamen final

**B — Listo para staging**, después de las operaciones autorizadas de preparación. Los controles
probados no muestran regresiones críticas conocidas; quedan verificaciones que impiden recomendar
producción. La evaluación real no se ejecutó y no se confunde con los tests de SDK simulado.

## Cierre: resultados finales y comandos auxiliares

Resultados propios de esta tarea, después de congelar el código. Las suites específicas
son subconjuntos de la regresión; no sumar sus cantidades como casos independientes.

| Verificación | Total | Correctos | Fallidos | Omitidos | Duración registrada |
| --- | ---: | ---: | ---: | ---: | ---: |
| Django completo PostgreSQL 17 | 821 | 821 | 0 | 0 | 38,073 s |
| Django completo SQLite | 821 | 780 | 0 | 41 | 16,490 s |
| Chatbot SQLite | 287 | 263 | 0 | 24 | 3,180 s |
| Presupuesto PostgreSQL | 37 | 37 | 0 | 0 | 2,090 s |
| JavaScript nativo / Node | 32 | 32 | 0 | 0 | 149 ms |
| Responsive Chrome | 28 | 28 | 0 | 0 | No se publica como benchmark |

Ejecución Docker: imagen local existente tenetrueca-app:latest, código montado read-only,
CI=true, OPENAI_API_KEY vacía, PYTHONDONTWRITEBYTECODE=1 y /tmp efímero. SQLite utiliza
--network none. PostgreSQL utiliza un contenedor postgres:17-alpine sin red exterior,
sin puertos publicados y con tmpfs para PGDATA; los runners comparten solo su namespace
mediante --network container:trueca-p5-test-pg y OPENAI_TEST_DB_PORT=5432. Cada suite crea y
destruye su propia base de test. El contenedor efímero se retira al cerrar la validación.
No se alteran los contenedores ni las bases del portal.

Comandos auxiliares y equivalentes CLI de las verificaciones ejecutadas (en Docker
con los mismos aislamientos y STATIC_ROOT temporal para collectstatic):

```bash
python manage.py evaluate_chatbot_offline --dataset phase2-v1 --samples 20 \
  --output /out/sqlite-evaluation.json --settings=tenetrueca.test_settings
python manage.py evaluate_chatbot_offline --dataset phase2-v1 --samples 20 \
  --output /out/postgres-evaluation.json --settings=tenetrueca.test_postgres_settings
python manage.py check --settings=tenetrueca.test_settings
python manage.py makemigrations chatbot --check --dry-run --settings=tenetrueca.test_postgres_settings
python manage.py sqlmigrate chatbot 0003 --settings=tenetrueca.test_postgres_settings
python manage.py collectstatic --noinput --settings=tenetrueca.test_settings
python chatbot/tests/browser/render_fixtures.py /out
node chatbot/tests/browser/check_widget.cjs /tmp/trueca-p5-browser
node --check static/js/chatbot.js
node --check static/js/faq.js
msgfmt --check --check-format -o /tmp/trueca-p5-checked.mo chatbot/locale/en/LC_MESSAGES/django.po
git diff --check
```

- System checks: sin incidencias. Con chatbot desactivado y sin clave: check/arranque de
  configuración correctos, sin cliente. `check --deploy` sobre el perfil de test produce
  W004/W008/W009 documentados en §25; no es una validación de HTTPS/secreto productivos.
- Migraciones: sin cambios pendientes de generar; SQL de 0003 solo añade tablas/checks.
  `migrate --plan` local, de solo lectura, muestra 0003 pendiente. No se aplicó allí.
- Collectstatic: ejecutado con STATIC_ROOT temporal; CSS/JS comprobados. Traducción compilada
  idéntica a la versionada. JavaScript/AST Python/JSON válidos; enlaces locales verificados.
- Docker Compose: `config --quiet` del compose base y overrides locales pasa; no se imprimen
  secretos ni se cambian puertos, imágenes, servicios o dependencias de aplicación.
- Se compararon hashes de 209 archivos de aplicaciones protegidas (incluidas moderación,
  reservas y puntos): sin cambios. No hay dependencias nuevas en requirements.
- Las métricas offline por caso, fases e historial están versionadas en el JSON enlazado;
  las verificaciones humanas y de modelo real no se ejecutaron ni se puntúan.

La entrega termina en **B — Listo para staging**, con evaluación real desactivada.

## Adenda — 07/11/2026: explicación básica sin FAQ

Se corrigió la abstención de «Cómo funciona trueca?» cuando falta una FAQ explicativa.
El hecho descriptivo `platform.overview` reutiliza capacidades verificadas de Fase 1; no
altera importes, plazos ni claims bloqueados. Se prefiere FAQ pública elegible y, cuando
falta, se responde localmente en ES/EN con referencia persistente/revalidable. El registro
incluye ahora ese hecho descriptivo además de los cuatro hechos normativos anteriores.

Siete regresiones nuevas en `chatbot/tests/test_platform_overview.py` comprueban ambas
grafías/idiomas/modos, ausencia de proveedor, preferencia de FAQ válida, rechazo de fuentes
privadas/maliciosas, condiciones no soportadas, cuota, duplicados, reset e historial propio.
294 tests chatbot PostgreSQL pasan; SQLite: 270 correctos y 24 omitidos de 294.
Regresión completa SQLite final: 787 correctos y 41 omitidos de 828. Primera ejecución:
un fallo intermitente de la prueba QR de backoffice por firmas generadas en segundos distintos;
pasó aisladamente y al repetir la suite. No se tocaron aplicaciones ajenas al chatbot.

Solo lectura de FAQ públicas locales para diagnóstico y comprobación, sin escribir historial
ni llamar a OpenAI. Recarga gradual de Gunicorn local, sin migraciones ni despliegue productivo.
No se han repetido las mediciones offline del 06/11: sus cifras permanecen históricas.
Se mantiene el dictamen B y el piloto real desactivado.

## Preparación posterior de staging y revisión presupuestaria — 07/11/2026

Véase [staging_and_budget_review.md](staging_and_budget_review.md). Se preparó una candidata
neutral validada por digest en staging temporal independiente, sin modificar contenedores
operativos ni realizar llamadas reales. El Compose de staging no reconstruye la imagen;
la futura promoción y su configuración/base productivas requieren otra autorización.
El método A local no se habilita por falta de overhead verificable; permanece B con nuevas
comprobaciones y preview explícito. Las cifras anteriores siguen fechadas a su ejecución;
los nuevos resultados y su alcance están en el informe enlazado. Se mantiene B.
