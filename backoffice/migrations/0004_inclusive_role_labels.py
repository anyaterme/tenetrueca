from pathlib import Path

from django.db import migrations, models


def baseline_migration():
    # Production has a single initial migration; development has two later artifacts.
    for name in (
        '0003_alter_staffauditevent_action',
        '0002_assign_existing_staff_roles',
        '0001_initial',
    ):
        if Path(__file__).with_name(name + '.py').is_file():
            return name
    raise RuntimeError(
        'Falta el historial base de backoffice. Recupera las migraciones originales '
        'del entorno; no las regeneres.'
    )


class Migration(migrations.Migration):
    dependencies = [
        ('backoffice', baseline_migration()),
    ]

    operations = [
        migrations.AlterField(
            model_name='staffinvitation',
            name='role',
            field=models.CharField(
                max_length=24,
                choices=[('administrator', 'Administración'), ('manager', 'Gestión')],
            ),
        ),
        # The production initial migration can predate this display label. Keep the
        # same final state on either history without replaying development artifacts.
        migrations.AlterField(
            model_name='staffauditevent',
            name='action',
            field=models.CharField(
                choices=[
                    ('staff.invited', 'Invitación enviada'),
                    ('staff.access_granted', 'Acceso staff concedido'),
                    ('staff.role_changed', 'Rol modificado'),
                    ('staff.activated', 'Acceso activado'),
                    ('staff.deactivated', 'Acceso desactivado'),
                    ('staff.access_removed', 'Acceso staff retirado'),
                    ('staff.temporary_password_created', 'Contraseña temporal generada'),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
    ]
