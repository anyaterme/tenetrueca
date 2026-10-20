from dataclasses import dataclass


@dataclass(frozen=True)
class RoleDefinition:
    name: str
    description: str


ROLE_VISITOR = 'Visitante'
ROLE_CITIZEN = 'Usuari@ registrad@'
ROLE_STAFF_ADMIN = 'Administración'
ROLE_STAFF_MANAGER = 'Gestión'
ROLE_OPERATOR = 'Operaciones de punto limpio'
ROLE_SUPERVISOR = 'Supervisión de punto limpio'
ROLE_MODERATOR = 'Moderación de catálogo'
ROLE_AUCTION_MANAGER = 'Gestión de subastas'
ROLE_ANALYST = 'Analista / consulta'
ROLE_FUNCTIONAL_ADMIN = 'Administración funcional'
ROLE_TECH_ADMIN = 'Administración técnica'

ROLE_DEFINITIONS = [
    RoleDefinition(ROLE_CITIZEN, 'Ciudadan@ autenticad@.'),
    RoleDefinition(ROLE_STAFF_ADMIN, 'Administra el equipo y su acceso al backoffice.'),
    RoleDefinition(ROLE_STAFF_MANAGER, 'Accede a las funciones operativas asignadas.'),
    RoleDefinition(ROLE_OPERATOR, 'Opera recepciones, inspecciones y entregas en centros autorizados.'),
    RoleDefinition(ROLE_SUPERVISOR, 'Supervisa la operativa de uno o varios puntos limpios.'),
    RoleDefinition(ROLE_MODERATOR, 'Gestiona revision de catalogo y publicaciones.'),
    RoleDefinition(ROLE_AUCTION_MANAGER, 'Gestiona subastas con puntos.'),
    RoleDefinition(ROLE_ANALYST, 'Consulta analitica e informes sin operar datos sensibles.'),
    RoleDefinition(ROLE_FUNCTIONAL_ADMIN, 'Administra reglas y configuracion funcional.'),
    RoleDefinition(ROLE_TECH_ADMIN, 'Administra configuracion tecnica y soporte.'),
]
