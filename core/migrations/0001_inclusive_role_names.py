from django.db import migrations


# Keep historical names here: migrations must not depend on live role constants.
ROLE_RENAMES = (
    ('Usuario registrado', 'Usuari@ registrad@'),
    ('Administrador', 'Administración'),
    ('Gestor', 'Gestión'),
    ('Operador de punto limpio', 'Operaciones de punto limpio'),
    ('Supervisor de punto limpio', 'Supervisión de punto limpio'),
    ('Moderador de catalogo', 'Moderación de catálogo'),
    ('Gestor de subastas', 'Gestión de subastas'),
    ('Administrador funcional', 'Administración funcional'),
    ('Administrador tecnico', 'Administración técnica'),
)


def rename_roles(apps, schema_editor, renames):
    Group = apps.get_model('auth', 'Group')
    groups = Group.objects.using(schema_editor.connection.alias)
    for old_name, new_name in renames:
        # Update the existing group in place, preserving its ID, users and permissions.
        # A conflicting name fails atomically rather than combining distinct grants.
        groups.filter(name=old_name).update(name=new_name)


def forwards(apps, schema_editor):
    rename_roles(apps, schema_editor, ROLE_RENAMES)


def backwards(apps, schema_editor):
    rename_roles(apps, schema_editor, ((new, old) for old, new in ROLE_RENAMES))


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('accounts', '0007_inclusive_labels'),
        ('backoffice', '0004_inclusive_role_labels'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
