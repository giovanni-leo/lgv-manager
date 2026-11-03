"""User roles.

viewer  read-only access to every record (e.g. co-investors)
editor  can add, change and delete records
admin   editor + manages users (Django superuser)
"""

from django.contrib.auth.models import Group

VIEWER = "viewer"
EDITOR = "editor"
ADMIN = "admin"
ROLES = [VIEWER, EDITOR, ADMIN]


def role_of(user):
    if user.is_superuser:
        return ADMIN
    names = set(user.groups.values_list("name", flat=True))
    if EDITOR in names:
        return EDITOR
    if VIEWER in names:
        return VIEWER
    return None


def assign_role(user, role):
    if role not in ROLES:
        raise ValueError(f"Unknown role {role!r}; choose one of {', '.join(ROLES)}")
    user.is_staff = True  # every role uses the data-entry interface
    user.is_superuser = role == ADMIN
    user.save()
    user.groups.remove(*Group.objects.filter(name__in=[VIEWER, EDITOR]))
    if role in (VIEWER, EDITOR):
        user.groups.add(Group.objects.get(name=role))
