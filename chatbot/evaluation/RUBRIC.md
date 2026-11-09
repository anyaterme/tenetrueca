# Rúbrica de revisión humana de Fase 2

Cada respuesta se revisa contra las versiones de fuentes conservadas en el resultado.
La salida de un mock no puntúa la calidad de un modelo real. No usar otro modelo como juez.
Revisar por separado los modos configurado/efectivo y las respuestas locales. Una abstención
justificada puede ser correcta; no penalizarla por no inventar una respuesta.

| Criterio | 0 | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- | --- |
| Exactitud factual | Falsa/dañina | Errores importantes | Mezcla correcta/incorrecta | Correcta con detalle menor | Todos los hechos correctos |
| Fidelidad | Contradice la fuente | Cambia sentido | Apoyo parcial | Apoyo correcto con ambigüedad menor | Cada afirmación respaldada y condiciones conservadas |
| Completitud | No aborda la pregunta | Omite lo esencial | Cubre parcialmente | Cubre lo esencial | Completa dentro del alcance; aclara lo desconocido |
| Claridad | Incomprensible | Confusa | Entendible con esfuerzo | Clara | Directa, clara y accesible |
| Concisión | Ruido dominante | Exceso severo | Recortable | Breve y suficiente | Longitud adecuada sin omisiones |
| Idioma | Incorrecto/ininteligible | Errores graves | Mezcla/errores frecuentes | Natural con detalle menor | Idioma activo, correcto y profesional |
| Referencias | Inventadas/privadas | No respaldan | Cobertura parcial | Correctas y pertinentes | Todas pertinentes, verificables y versión correcta |

Violaciones críticas (invalidan la respuesta independientemente de la media): inventar
importes/plazos; convertir exclusión en permiso; omitir condición esencial; prometer decisión
administrativa; mostrar datos privados; inventar una fuente; usar un claim bloqueado; atribuir
un hecho a una cita que no lo respalda. No compensar un fallo crítico con buenas puntuaciones.

Para recomendar grounded en producción: ninguna violación crítica, revisión de ambos idiomas
y de cada categoría, puntuaciones individuales justificadas, fuentes vigentes, y revisión de
los casos de abstención/fallback. Un promedio por sí solo no es criterio de aprobación.
Evidencia estructural/lexical automática y métricas de recuperación se informan aparte.
