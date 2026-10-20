from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('accounts', '0006_registrationverificationtoken_and_nif_constraint'),
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
