from django.apps import AppConfig
from django.db.models.signals import post_migrate
from django.utils.translation import gettext_lazy as _

AUDITED_MODELS = [
    "InvestingEntity", "Company", "Contact", "Deal", "ShareClass", "ShareEvent", "OptionPool",
    "Investment", "ConvertibleTerms", "Realization", "Valuation", "CompanyMetric", "Milestone",
    "Interaction", "Document", "DocumentFolder", "DocumentLink", "FxRate", "CoInvestor", "RoundParticipant", "FundingRound",
]



def create_roles(sender, **kwargs):
    from django.contrib.auth.models import Group, Permission

    from .roles import EDITOR, VIEWER

    perms = Permission.objects.filter(content_type__app_label="core")
    viewer, _created = Group.objects.get_or_create(name=VIEWER)
    viewer.permissions.set(perms.filter(codename__startswith="view_"))
    editor, _created = Group.objects.get_or_create(name=EDITOR)
    editor.permissions.set(perms)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = _("Tables")

    def ready(self):
        from auditlog.registry import auditlog

        for name in AUDITED_MODELS:
            model = self.get_model(name)
            exclude = ["search", "extracted_text"] if name == "Document" else []
            auditlog.register(model, exclude_fields=exclude)
        post_migrate.connect(create_roles, sender=self)
