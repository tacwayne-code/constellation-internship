from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create administration, supervisor, and system-administrator groups."

    def handle(self, *args, **options):
        permissions = Permission.objects.filter(content_type__app_label="reports")
        roles = {
            "行政": ["view_workreport", "view_reportmaterialsnapshot", "view_reportsyncevent", "export_workreport"],
            "主管": ["view_workreport", "view_reportmaterialsnapshot", "view_reportsyncevent", "export_workreport", "review_workreport"],
            "系统管理员": [permission.codename for permission in permissions],
        }
        for name, codenames in roles.items():
            group, _ = Group.objects.get_or_create(name=name)
            group.permissions.set(permissions.filter(codename__in=codenames))
            self.stdout.write(self.style.SUCCESS(f"已配置角色: {name}"))
