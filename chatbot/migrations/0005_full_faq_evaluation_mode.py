from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chatbot', '0004_natural_evaluation_mode')]

    operations = [
        migrations.RemoveConstraint(model_name='chatevaluationattempt', name='chat_eval_mode'),
        migrations.AddConstraint(
            model_name='chatevaluationattempt',
            constraint=models.CheckConstraint(
                condition=models.Q(mode__in=['extractive', 'grounded', 'natural', 'full_faq']),
                name='chat_eval_mode',
            ),
        ),
    ]
