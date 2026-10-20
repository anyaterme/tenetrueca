from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('backoffice', '0003_alter_staffauditevent_action'),
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
    ]
