from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('assistant', '0001_initial')]

    operations = [
        migrations.AddField(
            model_name='publicationevaluation', name='error_details',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='evaluationattempt', name='error_details',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
