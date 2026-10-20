from pathlib import Path

from django.db import migrations, models


def baseline_migration():
    # Legacy migrations are deployment artifacts, with two known equivalent histories.
    # Depend on the installed history without creating or rewriting its old nodes.
    names = (
        '0006_registrationverificationtoken_and_nif_constraint',
        '0005_registrationverificationtoken_alter_user_nif_nie_and_more',
    )
    installed = [name for name in names if Path(__file__).with_name(name + '.py').is_file()]
    if len(installed) != 1:
        raise RuntimeError(
            'No se puede identificar un único historial base de accounts. '
            'Conserva las migraciones originales del entorno; no las regeneres.'
        )
    return installed[0]


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', baseline_migration()),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='username',
            field=models.CharField(
                'nombre de usuari@', max_length=150, null=True, blank=True, unique=True,
            ),
        ),
        migrations.AlterField(
            model_name='usercenteraccess',
            name='role',
            field=models.CharField(
                max_length=20,
                choices=[
                    ('operator', 'Operaciones de punto limpio'),
                    ('supervisor', 'Supervisión de punto limpio'),
                ],
            ),
        ),
    ]
