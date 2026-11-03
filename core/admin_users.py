"""Simplified user management: role, two-factor status, reset of a lost device."""

from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.translation import gettext_lazy as _
from django_otp import devices_for_user
from django_otp.plugins.otp_static.models import StaticDevice
from django_otp.plugins.otp_totp.models import TOTPDevice

from .roles import role_of

User = get_user_model()


class UserAdmin(BaseUserAdmin):
    list_display = ["username", "full_name", "email", "role", "two_factor", "is_active", "last_login"]
    list_filter = ["is_active", "is_superuser", "groups"]
    fieldsets = [
        (None, {"fields": ["username", "password", "first_name", "last_name", "email"]}),
        (_("Access"), {"fields": ["is_active", "groups", "is_staff", "is_superuser"],
                       "description": _("Role: group 'viewer' = read only, 'editor' = can change data, "
                                        "superuser = admin (also manages users). Staff status is "
                                        "needed to use the data-entry interface.")}),
        (_("More details"), {"fields": ["user_permissions", "last_login", "date_joined"],
                             "classes": ["collapse"]}),
    ]
    add_fieldsets = [
        (None, {"fields": ["username", "email", "password1", "password2"]}),
    ]
    actions = ["reset_two_factor"]

    @admin.display(description=_("name"))
    def full_name(self, obj):
        return obj.get_full_name()

    @admin.display(description=_("role"))
    def role(self, obj):
        return role_of(obj) or "–"

    @admin.display(description=_("2FA"), boolean=True)
    def two_factor(self, obj):
        return any(True for _device in devices_for_user(obj, confirmed=True))

    @admin.action(description=_("Reset two-factor authentication (lost phone)"))
    def reset_two_factor(self, request, queryset):
        for model in (TOTPDevice, StaticDevice):
            model.objects.filter(user__in=queryset).delete()
        messages.success(request, _("The selected users will set up 2FA again at their next login."))

    def save_model(self, request, obj, form, change):
        if not change:
            obj.is_staff = True  # new users can use the interface once given a role
        super().save_model(request, obj, form, change)


admin.site.unregister(User)
admin.site.register(User, UserAdmin)

# Two-factor devices are managed by each user (Security page) and reset from the
# user list, so the raw device tables are kept out of the menu.
for _model in (TOTPDevice, StaticDevice):
    if admin.site.is_registered(_model):
        admin.site.unregister(_model)
