from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('chatbot', '0003_evaluation_budget')]

    operations = [
        migrations.RemoveConstraint(model_name='chatevaluationattempt', name='chat_eval_mode'),
        migrations.AddConstraint(
            model_name='chatevaluationattempt',
            constraint=models.CheckConstraint(
                condition=models.Q(mode__in=['extractive', 'grounded', 'natural']),
                name='chat_eval_mode',
            ),
        ),
    ]
