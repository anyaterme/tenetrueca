from django.db import models


class FAQ(models.Model):
    question = models.CharField('Pregunta', max_length=255)
    answer = models.TextField('Respuesta')
    is_published = models.BooleanField('Publicada', default=True)
    position = models.PositiveIntegerField('Posición', default=0)
    created_at = models.DateTimeField('Fecha de creación', auto_now_add=True)
    updated_at = models.DateTimeField('Última actualización', auto_now=True)

    class Meta:
        ordering = ['position', 'id']
        verbose_name = 'pregunta frecuente'
        verbose_name_plural = 'preguntas frecuentes'

    def __str__(self):
        return self.question
