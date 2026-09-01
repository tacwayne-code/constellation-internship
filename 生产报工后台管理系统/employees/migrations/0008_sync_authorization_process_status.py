from django.db import migrations


def sync_authorization_statuses(apps, schema_editor):
    Authorization = apps.get_model("employees", "EmployeeProcessAuthorization")
    for authorization in Authorization.objects.select_related("process").iterator():
        if authorization.is_active != authorization.process.is_active:
            Authorization.objects.filter(pk=authorization.pk).update(is_active=authorization.process.is_active)


class Migration(migrations.Migration):
    dependencies = [
        ("employees", "0007_process_sop"),
    ]

    operations = [
        migrations.RunPython(sync_authorization_statuses, migrations.RunPython.noop),
    ]
