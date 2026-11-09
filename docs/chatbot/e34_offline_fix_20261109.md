# E34 — corrección offline del formato (2026-11-09)

## Diagnóstico y alcance

El micropiloto cerrado `10d8c14e-9adb-4749-9f39-9027999814b5` registró
`invalid_response_style` en E34/extractive y `invalid_grounding` en E34/grounded.
Las salidas brutas rechazadas no se conservaron: estos códigos no permiten identificar
con certeza la subcondición concreta fallida en cada salida. No se repitieron llamadas
para investigarlo ni se modificaron esos resultados históricos.

La evidencia de E34 contiene dos oraciones, pero tres acciones independientes:
«Explora el catálogo y presenta propuestas de objetos para revisión. Consulta la ayuda
para conocer el proceso de intercambio.» La dependencia del modelo para convertir
esa coordinación en tres frases era evitable. Las regresiones simulan además errores
de formato, citas y grounding para demostrar que siguen rechazándose.

## Corrección

`chatbot/sentence_planning.py` aplica una gramática local deliberadamente estrecha:
petición explícita de tres frases, una sola FAQ aprobada, dos oraciones completas,
una coordinación y tres cláusulas que empiecen con imperativos reconocidos del idioma
activo. No utiliza el identificador E34 ni una respuesta constante. Conserva las palabras,
su orden y las citas originales; elimina únicamente la conjunción de coordinación,
capitaliza cada inicio y añade el punto final correspondiente.

Resultado en ambos modos para la evidencia congelada de E34:

> Explora el catálogo.
>
> Presenta propuestas de objetos para revisión.
>
> Consulta la ayuda para conocer el proceso de intercambio.

El resultado pasa por `validate_display_text`, `validate_style` y, en grounded,
`validate_grounded`, además de la validación común y la consulta final de fuentes
vigentes de `ConversationAnswerer`. La ruta se informa como `deterministic`, con
historial, referencias y cuota habituales. No se construye el proveedor.

No se traduce evidencia, se añaden hechos ni se rellena una respuesta insuficiente.
Fuentes críticas, condiciones, cifras, alternativas, varias fuentes o construcciones
fuera de esta gramática conservan la ruta previa del proveedor y sus rechazos.
No se reparan respuestas mediante nuevas llamadas. No cambia ningún validador:
`generation.py`, `response_style.py` y `grounding.py` conservan exactamente el SHA-256
de la candidata del micropiloto. El corpus y `pilot-v1` también permanecen intactos.

## Archivos

- Nuevos: `chatbot/sentence_planning.py`, `chatbot/tests/test_sentence_planning.py`, este informe.
- Modificados: `chatbot/answering.py`, `chatbot/tests/test_response_style.py`,
  `chatbot/tests/test_evaluation_selection.py`, `chatbot/README.md`, `.gitignore`.
- Sin migraciones, dependencias, cambios de frontend, modelos, reservas o puntos.

La vista previa E34/E36 en ambos modos mantiene los cuatro pares ordenados, pero
ahora calcula dos posibles llamadas: E34 se resuelve localmente. Las pruebas financieras
que necesitan recorrer cuatro llamadas simuladas utilizan exclusivamente una variante
editorial sintética fuera de la gramática local; no alteran el dataset versionado.

## Verificación y aislamiento

Se ejecutó el código actualizado mediante un snapshot limpio montado de solo lectura
en contenedores nuevos con el runtime AMD64 existente
`trueca-candidate@sha256:8ca2925a0e9b7a5216891e811bd5d380ee6e83649d2a9e90fea7bb2246325477`.
Esto verifica el código corregido usando ese runtime; no convierte la imagen congelada
en una nueva candidata ni cambia su contenido.

Sin claves API, con `CHATBOT_PROVIDER=mock`, `CI=true` y red externa deshabilitada.
PostgreSQL usa un contenedor nuevo sin puertos, volúmenes persistentes ni datos compartidos;
el contenedor de tests comparte únicamente su espacio de red aislado. Las pruebas de SDK
utilizan mocks. Los datos se crean y destruyen exclusivamente en bases de test.

Comandos Django dentro de esos contenedores:

```sh
DJANGO_SETTINGS_MODULE=tenetrueca.test_settings python manage.py test --noinput --verbosity 1
DJANGO_SETTINGS_MODULE=tenetrueca.test_postgres_settings OPENAI_TEST_DB_PORT=5432 python manage.py test --noinput --verbosity 1
```

Resultados finales:

| Motor | Pruebas descubiertas | Resultado | Duración Django |
|---|---:|---|---:|
| SQLite en memoria | 877 | OK, 44 omitidas por necesitar PostgreSQL | 30,924 s |
| PostgreSQL 17 efímero | 877 | OK, sin omisiones | 59,958 s |

La primera pasada focalizada detectó únicamente un error del nuevo fixture de usuario
(faltaba el email exigido por el manager existente); se corrigió antes de ambas suites
completas. Las advertencias de proveedor en las suites corresponden a errores simulados.

Las nueve regresiones nuevas cubren E34 real en ambos modos, formato y citas, otra FAQ
no codificada, idioma, rechazo de evidencia insegura/ambigua/crítica, protección de los
validadores existentes, cambios/retiro de fuentes, acceso anónimo/autenticado, historial,
reset, cuota e idempotencia sin proveedor. La suite existente comprueba además llamadas
simuladas, ausencia de reintentos, límites, concurrencia y contabilidad.

No se realizaron llamadas reales a OpenAI, reconstrucciones, despliegues ni cambios en
los contenedores actuales, pilotos cerrados o ledgers. La confirmación con un modelo real
queda fuera de esta tarea y necesitaría una autorización nueva; no se afirma éxito de un
nuevo micropiloto ni se cambia la recomendación histórica de despliegue.
