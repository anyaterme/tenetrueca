from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from catalog.models import Category
from moderation.models import ModerationDecision
from publications.models import Publication


class ModerationDecisionModelTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.submitter = User.objects.create_user(
            email='submitter@example.com',
            first_name='Submitter',
            password='test-password',
        )
        self.reviewer = User.objects.create_user(
            email='reviewer@example.com',
            first_name='Reviewer',
            password='test-password',
        )
        category = Category.objects.create(name='Deporte', slug='deporte')
        self.publication = Publication.objects.create(
            submitter=self.submitter,
            title='Bicicleta',
            description='Bicicleta infantil.',
            category=category,
            status=Publication.Status.PENDING_REVIEW,
        )

    def test_decision_must_match_resulting_publication_status(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            ModerationDecision.objects.create(
                publication=self.publication,
                reviewer=self.reviewer,
                decision=ModerationDecision.Decision.APPROVE,
                previous_status=Publication.Status.PENDING_REVIEW,
                resulting_status=Publication.Status.REJECTED,
            )
