from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import SetPasswordForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from accounts.models import UserCenterAccess
from audit.models import AuditEvent
from backoffice.forms import StaffInviteForm, StaffMemberForm
from backoffice.models import StaffAuditEvent, StaffInvitation
from backoffice.permissions import staff_management_required
from backoffice.services import (
    StaffInvitationService,
    assign_staff_role,
    ensure_admin_safeguards,
    generate_temporary_password,
    invalidate_user_sessions,
    record_staff_event,
    staff_role_label,
    staff_role_value,
)
from core.permissions import (
    allowed_reception_centers,
    operational_scope,
    scope_publications,
    scope_reservations,
    user_can_access_backoffice,
    user_can_manage_staff,
    user_can_manage_users,
    user_can_moderate,
)
from publications.models import Publication
from reservations.models import Reservation


AUDIT_ACTION_LABELS = {
    'publication.moderation.approve': 'aprobó una publicación',
    'publication.moderation.reject': 'rechazó una publicación',
    'publication.moderation.request_changes': 'solicitó cambios en una publicación',
    'inventory.reception.accepted': 'completó una recepción',
    'inventory.reception.rejected': 'rechazó una recepción',
    'reservations.pickup.completed': 'completó una recogida',
}


def _require_backoffice(user):
    if not user_can_access_backoffice(user):
        raise PermissionDenied


def _elapsed_label(value):
    if value is None:
        return 'Sin fecha'
    elapsed = timezone.now() - value
    if elapsed.days:
        return f'{elapsed.days} d'
    hours = max(int(elapsed.total_seconds() // 3600), 0)
    return f'{hours} h'


def _dashboard_tasks(user, centers):
    tasks = []

    if user_can_moderate(user):
        publications = scope_publications(
            user,
            Publication.objects.filter(
                status=Publication.Status.PENDING_REVIEW,
                submitted_at__isnull=False,
            ),
        )
        publications = (
            publications
            .select_related('submitter', 'category', 'submitter__habitual_recycling_center')
            .order_by('submitted_at', 'pk')[:6]
        )
        for publication in publications:
            tasks.append(
                {
                    'kind': 'moderation',
                    'kind_label': 'Moderación',
                    'title': publication.title,
                    'person': publication.submitter.get_full_name() or publication.submitter.email,
                    'center': publication.submitter.habitual_recycling_center,
                    'status_label': 'Por moderar',
                    'status_tone': 'warning',
                    'waiting_label': _elapsed_label(publication.submitted_at),
                    'sort_at': publication.submitted_at,
                    'url': reverse('moderation:detail', args=[publication.pk]),
                    'action_label': 'Revisar',
                }
            )

    if centers.exists():
        publications = scope_publications(
            user,
            Publication.objects.filter(
                status=Publication.Status.APPROVED,
                inventory_object__isnull=True,
            ),
        )
        publications = (
            publications
            .select_related('submitter', 'category')
            .order_by('approved_at', 'pk')[:6]
        )
        for publication in publications:
            tasks.append(
                {
                    'kind': 'reception',
                    'kind_label': 'Recepción',
                    'title': publication.title,
                    'person': publication.submitter.get_full_name() or publication.submitter.email,
                    'center': None,
                    'status_label': 'Aprobada',
                    'status_tone': 'success',
                    'waiting_label': _elapsed_label(publication.approved_at),
                    'sort_at': publication.approved_at or publication.updated_at,
                    'url': reverse('inventory:reception_detail', args=[publication.pk]),
                    'action_label': 'Recibir',
                }
            )

        reservations = scope_reservations(
            user,
            Reservation.objects.filter(
                status=Reservation.Status.ACTIVE,
                expires_at__gt=timezone.now(),
                inventory_item__center__in=centers,
            ),
        )
        reservations = (
            reservations
            .select_related('user', 'inventory_item', 'inventory_item__center')
            .order_by('expires_at', 'pk')[:6]
        )
        for reservation in reservations:
            tasks.append(
                {
                    'kind': 'pickup',
                    'kind_label': 'Recogida',
                    'title': reservation.inventory_item.title,
                    'person': reservation.user.get_full_name() or reservation.user.email,
                    'center': reservation.inventory_item.center,
                    'status_label': 'Lista para recoger',
                    'status_tone': 'info',
                    'waiting_label': reservation.expires_at.strftime('%d/%m %H:%M'),
                    'sort_at': reservation.expires_at,
                    'url': reverse('reservations:pickup_scan', args=[reservation.qr_token()]),
                    'action_label': 'Abrir',
                }
            )

    return sorted(tasks, key=lambda task: task['sort_at'])[:8]


@login_required
def dashboard(request):
    _require_backoffice(request.user)
    User = get_user_model()
    centers = allowed_reception_centers(request.user)
    can_moderate = user_can_moderate(request.user)
    can_manage_users = user_can_manage_users(request.user)
    can_manage_staff = user_can_manage_staff(request.user)

    metrics = []
    if can_moderate:
        pending_publications = scope_publications(
            request.user,
            Publication.objects.filter(status=Publication.Status.PENDING_REVIEW),
        )
        metrics.append(
            {
                'label': 'Por moderar',
                'value': pending_publications.count(),
                'note': 'Publicaciones pendientes',
                'tone': 'warning',
                'icon': 'file-check',
                'url': reverse('moderation:queue'),
            }
        )
    if centers.exists():
        reception_publications = scope_publications(
            request.user,
            Publication.objects.filter(
                status=Publication.Status.APPROVED,
                inventory_object__isnull=True,
            ),
        )
        active_pickups = scope_reservations(
            request.user,
            Reservation.objects.filter(
                status=Reservation.Status.ACTIVE,
                expires_at__gt=timezone.now(),
                inventory_item__center__in=centers,
            ),
        )
        metrics.extend(
            [
                {
                    'label': 'Pendientes de recepción',
                    'value': reception_publications.count(),
                    'note': 'Objetos aprobados',
                    'tone': 'success',
                    'icon': 'package',
                    'url': reverse('inventory:reception_queue'),
                },
                {
                    'label': 'Recogidas activas',
                    'value': active_pickups.count(),
                    'note': 'En tus centros',
                    'tone': 'info',
                    'icon': 'qr',
                    'url': reverse('backoffice:pickup_queue'),
                },
            ]
        )
    if can_manage_users:
        metrics.append(
            {
                'label': 'Usuarios activos',
                'value': User.objects.filter(
                    is_staff=False,
                    is_active=True,
                    account_status=User.AccountStatus.ACTIVE,
                    center_accesses__isnull=True,
                ).count(),
                'note': 'Cuentas ciudadanas',
                'tone': 'neutral',
                'icon': 'users',
                'url': reverse('backoffice:user_list'),
            }
        )
    if can_manage_staff:
        metrics.append(
            {
                'label': 'Equipo activo',
                'value': User.objects.filter(
                    is_staff=True,
                    is_active=True,
                ).distinct().count(),
                'note': 'Miembros con acceso',
                'tone': 'neutral',
                'icon': 'team',
                'url': reverse('backoffice:team_list'),
            }
        )

    activity = AuditEvent.objects.select_related('actor').filter(source='backoffice')
    if not request.user.is_superuser and not can_manage_staff:
        activity = activity.filter(actor=request.user)
    activity = list(activity[:5])
    for event in activity:
        event.display_action = AUDIT_ACTION_LABELS.get(
            event.action,
            event.action.replace('.', ' '),
        )

    return render(
        request,
        'backoffice/dashboard.html',
        {
            'staff_section': 'dashboard',
            'dashboard_name': request.user.first_name or request.user.email,
            'metrics': metrics,
            'tasks': _dashboard_tasks(request.user, centers),
            'recent_activity': activity,
            'assigned_centers': centers,
        },
    )


@login_required
def no_assignment(request):
    scope = operational_scope(request.user)
    if not scope.is_manager:
        return redirect('backoffice:dashboard')
    if scope.center is not None:
        return redirect('backoffice:dashboard')
    return render(request, 'backoffice/no_assignment.html')


@login_required
def pickup_queue(request):
    _require_backoffice(request.user)
    centers = allowed_reception_centers(request.user)
    if not centers.exists():
        raise PermissionDenied
    reservations = scope_reservations(
        request.user,
        Reservation.objects.filter(
            status=Reservation.Status.ACTIVE,
            expires_at__gt=timezone.now(),
            inventory_item__center__in=centers,
        ),
    )
    reservations = (
        reservations
        .select_related('user', 'inventory_item', 'inventory_item__center')
        .order_by('expires_at', 'pk')
    )
    query = request.GET.get('q', '').strip()
    if query:
        reservations = reservations.filter(
            Q(inventory_item__title__icontains=query)
            | Q(inventory_item__reference__icontains=query)
            | Q(user__email__icontains=query)
            | Q(user__first_name__icontains=query)
            | Q(user__last_name__icontains=query)
        )
    page_obj = Paginator(reservations, 20).get_page(request.GET.get('page'))
    for reservation in page_obj.object_list:
        reservation.staff_pickup_url = reverse(
            'reservations:pickup_scan', args=[reservation.qr_token()]
        )
    return render(
        request,
        'backoffice/pickup_queue.html',
        {
            'staff_section': 'pickup',
            'page_obj': page_obj,
            'query': query,
        },
    )


@login_required
def user_list(request):
    _require_backoffice(request.user)
    if not user_can_manage_users(request.user):
        raise PermissionDenied
    User = get_user_model()
    users = (
        User.objects.filter(is_staff=False, center_accesses__isnull=True)
        .select_related('habitual_recycling_center')
        .order_by('-date_joined', '-pk')
    )
    query = request.GET.get('q', '').strip()
    status = request.GET.get('status', '').strip()
    if query:
        users = users.filter(
            Q(email__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
            | Q(nif_nie__icontains=query)
        )
    if status in User.AccountStatus.values:
        users = users.filter(account_status=status)
    page_obj = Paginator(users, 20).get_page(request.GET.get('page'))
    return render(
        request,
        'backoffice/user_list.html',
        {
            'staff_section': 'users',
            'page_obj': page_obj,
            'query': query,
            'selected_status': status,
            'status_choices': User.AccountStatus.choices,
        },
    )


@login_required
@staff_management_required
def team_list(request):
    User = get_user_model()
    members = (
        User.objects.filter(is_staff=True)
        .prefetch_related(
            Prefetch(
                'center_accesses',
                queryset=UserCenterAccess.objects.filter(is_active=True).select_related(
                    'center'
                ),
                to_attr='active_center_accesses',
            ),
            'groups',
        )
        .distinct()
        .order_by('first_name', 'last_name', 'email')
    )
    query = request.GET.get('q', '').strip()
    role = request.GET.get('role', '').strip()
    status = request.GET.get('status', '').strip()
    if query:
        members = members.filter(
            Q(email__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        ).distinct()
    if role == StaffInvitation.Role.ADMINISTRATOR:
        members = members.filter(
            Q(is_superuser=True)
            | Q(groups__name='Administrador')
            | Q(
                user_permissions__content_type__app_label='accounts',
                user_permissions__codename='manage_staff',
            )
        ).distinct()
    elif role == StaffInvitation.Role.MANAGER:
        members = members.filter(groups__name='Gestor').distinct()
    if status == 'active':
        members = members.filter(is_active=True)
    elif status == 'inactive':
        members = members.filter(is_active=False)

    paginator = Paginator(members, 20)
    page_obj = paginator.get_page(request.GET.get('page'))
    for member in page_obj.object_list:
        member.staff_role_label = staff_role_label(member)
        member.staff_role_value = staff_role_value(member)
    preserved_query = request.GET.copy()
    preserved_query.pop('page', None)
    return render(
        request,
        'backoffice/team_list.html',
        {
            'staff_section': 'team',
            'page_obj': page_obj,
            'paginator': paginator,
            'page_range': paginator.get_elided_page_range(page_obj.number),
            'preserved_query': preserved_query.urlencode(),
            'query': query,
            'selected_role': role,
            'selected_status': status,
            'role_choices': StaffInvitation.Role.choices,
        },
    )


def _temporary_credentials_response(request, user, temporary_password):
    return render(
        request,
        'backoffice/team_temporary_credentials.html',
        {
            'staff_section': 'team',
            'member': user,
            'temporary_password': temporary_password,
            'role_label': staff_role_label(user),
        },
    )


@never_cache
@login_required
@staff_management_required
def team_add(request):
    User = get_user_model()
    existing_user = None
    local_auth_enabled = settings.AUTH_PROVIDER == 'local'
    form = StaffInviteForm(
        request.POST or None,
        local_auth_enabled=local_auth_enabled,
    )
    if request.method == 'POST' and form.is_valid():
        email = form.cleaned_data['email']
        provisioning_method = form.cleaned_data['provisioning_method']
        existing_user = User.objects.filter(email__iexact=email).first()
        if existing_user is None:
            temporary_password = None
            try:
                with transaction.atomic():
                    user = User(
                        email=email,
                        first_name=form.cleaned_data['first_name'].strip(),
                        last_name=form.cleaned_data['last_name'].strip(),
                        is_staff=True,
                        is_active=True,
                        account_status=User.AccountStatus.ACTIVE,
                        auth_source=(
                            User.AuthSource.LOCAL
                            if settings.AUTH_PROVIDER == 'local'
                            else User.AuthSource.EXTERNAL
                        ),
                    )
                    if (
                        provisioning_method
                        == StaffInviteForm.ProvisioningMethod.TEMPORARY_PASSWORD
                    ):
                        temporary_password = generate_temporary_password()
                        user.set_password(temporary_password)
                        user.must_change_password = True
                    else:
                        user.set_unusable_password()
                    user.save()
                    assign_staff_role(
                        user,
                        form.cleaned_data['role'],
                        center=form.cleaned_data['center'],
                    )
                    record_staff_event(
                        actor=request.user,
                        target=user,
                        action=StaffAuditEvent.Action.ACCESS_GRANTED,
                        metadata={
                            'role': form.cleaned_data['role'],
                            'new_account': True,
                            'center_id': getattr(form.cleaned_data['center'], 'pk', None),
                        },
                    )
                    if temporary_password is None:
                        invitation = StaffInvitationService.issue(
                            request=request,
                            user=user,
                            invited_by=request.user,
                            role=form.cleaned_data['role'],
                        )
                        if invitation is None:
                            raise ValidationError(
                                'No se pudo enviar la invitación. Inténtalo de nuevo.'
                            )
                        record_staff_event(
                            actor=request.user,
                            target=user,
                            action=StaffAuditEvent.Action.INVITED,
                            metadata={'role': form.cleaned_data['role']},
                        )
                    else:
                        record_staff_event(
                            actor=request.user,
                            target=user,
                            action=StaffAuditEvent.Action.TEMPORARY_PASSWORD_CREATED,
                            metadata={'role': form.cleaned_data['role']},
                        )
            except ValidationError as error:
                form.add_error(None, error.messages[0])
            else:
                if temporary_password is not None:
                    return _temporary_credentials_response(
                        request,
                        user,
                        temporary_password,
                    )
                messages.success(request, 'La invitación se ha enviado correctamente.')
                return redirect('backoffice:team_detail', pk=user.pk)

    return render(
        request,
        'backoffice/team_add.html',
        {
            'staff_section': 'team',
            'form': form,
            'existing_user': existing_user,
            'local_auth_enabled': local_auth_enabled,
        },
    )


@never_cache
@login_required
@staff_management_required
def team_grant_existing(request):
    if request.method != 'POST':
        raise PermissionDenied
    User = get_user_model()
    target = get_object_or_404(User, pk=request.POST.get('user_id'))
    role = request.POST.get('role', '')
    center_id = request.POST.get('center', '')
    provisioning_method = request.POST.get(
        'provisioning_method',
        StaffInviteForm.ProvisioningMethod.INVITATION,
    )
    if role not in StaffInvitation.Role.values:
        messages.error(request, 'El rol seleccionado no es válido.')
        return redirect('backoffice:team_add')
    if provisioning_method not in StaffInviteForm.ProvisioningMethod.values:
        messages.error(request, 'El método de acceso seleccionado no es válido.')
        return redirect('backoffice:team_add')
    from locations.models import RecyclingCenter

    center = None
    if role == StaffInvitation.Role.MANAGER and center_id:
        center = get_object_or_404(RecyclingCenter, pk=center_id, is_active=True)

    temporary_password = None
    try:
        with transaction.atomic():
            target = User.objects.select_for_update().get(pk=target.pk)
            previous_role = staff_role_value(target)
            was_staff = target.is_staff
            was_active = target.is_active
            if was_staff and previous_role != role:
                ensure_admin_safeguards(
                    actor=request.user,
                    target=target,
                    operation='change_role',
                    new_role=role,
                )
            target.is_staff = True
            target.is_active = True
            target.account_status = User.AccountStatus.ACTIVE
            target.deactivated_at = None
            target.save(
                update_fields=[
                    'is_staff',
                    'is_active',
                    'account_status',
                    'deactivated_at',
                ]
            )
            assign_staff_role(target, role, center=center)
            if not was_staff:
                record_staff_event(
                    actor=request.user,
                    target=target,
                    action=StaffAuditEvent.Action.ACCESS_GRANTED,
                    metadata={
                        'role': role,
                        'existing_account': True,
                        'center_id': getattr(center, 'pk', None),
                    },
                )
            if not was_active:
                record_staff_event(
                    actor=request.user,
                    target=target,
                    action=StaffAuditEvent.Action.ACTIVATED,
                )
            if previous_role != role:
                record_staff_event(
                    actor=request.user,
                    target=target,
                    action=StaffAuditEvent.Action.ROLE_CHANGED,
                    metadata={'from': previous_role, 'to': role},
                )
                if was_staff:
                    invalidate_user_sessions(target)
            if not target.has_usable_password():
                can_use_temporary_password = (
                    provisioning_method
                    == StaffInviteForm.ProvisioningMethod.TEMPORARY_PASSWORD
                    and settings.AUTH_PROVIDER == 'local'
                    and target.auth_source == User.AuthSource.LOCAL
                )
                if can_use_temporary_password:
                    temporary_password = generate_temporary_password()
                    target.set_password(temporary_password)
                    target.must_change_password = True
                    target.save(update_fields=['password', 'must_change_password'])
                    record_staff_event(
                        actor=request.user,
                        target=target,
                        action=StaffAuditEvent.Action.TEMPORARY_PASSWORD_CREATED,
                        metadata={'role': role},
                    )
                else:
                    invitation = StaffInvitationService.issue(
                        request=request,
                        user=target,
                        invited_by=request.user,
                        role=role,
                    )
                    if invitation is None:
                        raise ValidationError(
                            'No se pudo enviar la invitación. Inténtalo de nuevo.'
                        )
                    record_staff_event(
                        actor=request.user,
                        target=target,
                        action=StaffAuditEvent.Action.INVITED,
                        metadata={'role': role},
                    )
    except ValidationError as error:
        messages.error(request, error.messages[0])
        return redirect('backoffice:team_add')

    if temporary_password is not None:
        return _temporary_credentials_response(
            request,
            target,
            temporary_password,
        )
    messages.success(request, 'Se ha concedido acceso al equipo.')
    return redirect('backoffice:team_detail', pk=target.pk)


def _staff_member_or_404(pk):
    User = get_user_model()
    return get_object_or_404(
        User.objects.prefetch_related('groups', 'center_accesses__center'),
        pk=pk,
        is_staff=True,
    )


@login_required
@staff_management_required
def team_detail(request, pk):
    member = _staff_member_or_404(pk)
    current_role = staff_role_value(member)
    assigned_center = (
        member.center_accesses.filter(is_active=True)
        .select_related('center')
        .first()
    )
    form = StaffMemberForm(
        request.POST or None,
        user=member,
        initial_role=current_role,
        allow_role_change=(member.pk != request.user.pk and not member.is_superuser),
    )
    if request.method == 'POST' and form.is_valid():
        new_role = form.cleaned_data['role']
        try:
            ensure_admin_safeguards(
                actor=request.user,
                target=member,
                operation='change_role',
                new_role=new_role,
            )
        except ValidationError as error:
            form.add_error('role', error.messages[0])
        else:
            with transaction.atomic():
                form.save()
                assign_staff_role(
                    member,
                    new_role,
                    center=form.cleaned_data['center'],
                )
                if current_role != new_role:
                    record_staff_event(
                        actor=request.user,
                        target=member,
                        action=StaffAuditEvent.Action.ROLE_CHANGED,
                        metadata={'from': current_role, 'to': new_role},
                    )
                    invalidate_user_sessions(member)
            messages.success(request, 'Los datos del miembro se han actualizado.')
            return redirect('backoffice:team_detail', pk=member.pk)

    activity = member.staff_audit_events.select_related('actor')[:20]
    staff_since = (
        member.staff_audit_events.order_by('created_at')
        .values_list('created_at', flat=True)
        .first()
        or member.date_joined
    )
    return render(
        request,
        'backoffice/team_detail.html',
        {
            'staff_section': 'team',
            'member': member,
            'form': form,
            'activity': activity,
            'staff_since': staff_since,
            'current_role_label': staff_role_label(member),
            'assigned_center': assigned_center.center if assigned_center else None,
            'pending_invitation': member.staff_invitations.filter(
                accepted_at__isnull=True,
                invalidated_at__isnull=True,
                expires_at__gt=timezone.now(),
            ).first(),
        },
    )


def _member_state_change(request, pk, action):
    if request.method != 'POST':
        raise PermissionDenied
    User = get_user_model()
    with transaction.atomic():
        member = get_object_or_404(
            User.objects.select_for_update(), pk=pk, is_staff=True
        )
        if action == 'deactivate':
            ensure_admin_safeguards(
                actor=request.user,
                target=member,
                operation='deactivate',
            )
            member.is_active = False
            member.account_status = User.AccountStatus.SUSPENDED
            member.deactivated_at = timezone.now()
            member.save(
                update_fields=['is_active', 'account_status', 'deactivated_at']
            )
            member.staff_invitations.filter(
                accepted_at__isnull=True,
                invalidated_at__isnull=True,
            ).update(invalidated_at=timezone.now())
            invalidate_user_sessions(member)
            audit_action = StaffAuditEvent.Action.DEACTIVATED
            message = 'El acceso del miembro se ha desactivado.'
        elif action == 'reactivate':
            member.is_active = True
            member.account_status = User.AccountStatus.ACTIVE
            member.deactivated_at = None
            member.save(
                update_fields=['is_active', 'account_status', 'deactivated_at']
            )
            audit_action = StaffAuditEvent.Action.ACTIVATED
            message = 'El acceso del miembro se ha reactivado.'
        else:
            ensure_admin_safeguards(
                actor=request.user,
                target=member,
                operation='remove',
            )
            previous_role = staff_role_value(member)
            member.is_staff = False
            member.save(update_fields=['is_staff'])
            member.groups.remove(
                *member.groups.filter(name__in=('Administrador', 'Gestor'))
            )
            member.user_permissions.remove(
                *member.user_permissions.filter(
                    content_type__app_label='accounts',
                    codename='manage_staff',
                )
            )
            member.staff_invitations.filter(
                accepted_at__isnull=True,
                invalidated_at__isnull=True,
            ).update(invalidated_at=timezone.now())
            invalidate_user_sessions(member)
            audit_action = StaffAuditEvent.Action.ACCESS_REMOVED
            message = 'Se ha retirado el acceso al backoffice.'
            record_staff_event(
                actor=request.user,
                target=member,
                action=audit_action,
                metadata={'previous_role': previous_role},
            )
            messages.success(request, message)
            return redirect('backoffice:team_list')

        record_staff_event(
            actor=request.user,
            target=member,
            action=audit_action,
        )
    messages.success(request, message)
    return redirect('backoffice:team_detail', pk=member.pk)


@login_required
@staff_management_required
def team_deactivate(request, pk):
    try:
        return _member_state_change(request, pk, 'deactivate')
    except ValidationError as error:
        messages.error(request, error.messages[0])
        return redirect('backoffice:team_detail', pk=pk)


@login_required
@staff_management_required
def team_reactivate(request, pk):
    return _member_state_change(request, pk, 'reactivate')


@login_required
@staff_management_required
def team_remove(request, pk):
    try:
        return _member_state_change(request, pk, 'remove')
    except ValidationError as error:
        messages.error(request, error.messages[0])
        return redirect('backoffice:team_detail', pk=pk)


def staff_invitation_accept(request, token):
    pending = StaffInvitationService.pending(token)
    if pending is None:
        return render(
            request,
            'backoffice/staff_invitation_invalid.html',
            status=400,
        )

    requires_password = (
        settings.AUTH_PROVIDER == 'local'
        and pending.user.auth_source == pending.user.AuthSource.LOCAL
    )
    form = SetPasswordForm(pending.user, request.POST or None) if requires_password else None
    if request.method == 'POST' and (form is None or form.is_valid()):
        with transaction.atomic():
            if form is not None:
                form.save()
                pending.user.must_change_password = False
                pending.user.save(update_fields=['must_change_password'])
            StaffInvitationService.accept(pending.record)
        if requires_password:
            from accounts.views import authenticated_home_url

            login(
                request,
                pending.user,
                backend=settings.AUTHENTICATION_BACKENDS[0],
            )
            messages.success(request, 'Tu acceso al equipo ya está preparado.')
            return redirect(authenticated_home_url(pending.user))
        messages.success(request, 'Invitación aceptada. Ya puedes iniciar sesión.')
        return redirect('login')

    return render(
        request,
        'backoffice/staff_invitation_accept.html',
        {
            'invitation': pending.record,
            'invited_user': pending.user,
            'requires_password': requires_password,
            'form': form,
        },
    )
