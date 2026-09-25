from dataclasses import dataclass


@dataclass(frozen=True)
class RoleDefinition:
    name: str
    description: str


ROLE_VISITOR = 'Visitante'
ROLE_CITIZEN = 'Usuario registrado'
ROLE_STAFF_ADMIN = 'Administrador'
ROLE_STAFF_MANAGER = 'Gestor'
ROLE_OPERATOR = 'Operador de punto limpio'
ROLE_SUPERVISOR = 'Supervisor de punto limpio'
ROLE_MODERATOR = 'Moderador de catalogo'
ROLE_AUCTION_MANAGER = 'Gestor de subastas'
ROLE_ANALYST = 'Analista / consulta'
ROLE_FUNCTIONAL_ADMIN = 'Administrador funcional'
ROLE_TECH_ADMIN = 'Administrador tecnico'

ROLE_DEFINITIONS = [
    RoleDefinition(ROLE_CITIZEN, 'Ciudadano autenticado.'),
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
