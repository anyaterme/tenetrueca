from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chatbot', '0001_initial')]
    operations = [migrations.AddField(
        model_name='chatmessage', name='sources',
        field=models.JSONField(blank=True, db_default=[], default=list),
    )]
