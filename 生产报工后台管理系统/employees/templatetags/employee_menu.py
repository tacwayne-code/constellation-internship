import json
from urllib.parse import urlencode

from django import template
from django.urls import reverse
from django.utils.safestring import mark_safe

from employees.models import Department


register = template.Library()


@register.simple_tag
def department_menu_items():
    employee_list_url = reverse("admin:employees_employee_changelist")
    items = []
    for department in Department.objects.filter(employees__isnull=False).distinct().order_by("name"):
        items.append({
            "name": department.name,
            "icon": "fa fa-building-o",
            "url": f"{employee_list_url}?{urlencode({'department__id__exact': department.pk})}",
            "eid": f"employee-department-{department.pk}",
            "breadcrumbs": [
                {"name": "员工", "icon": "fa fa-user"},
                {"name": "部门", "icon": "fa fa-building"},
                {"name": department.name, "icon": "fa fa-building-o"},
            ],
        })
    payload = json.dumps(items, ensure_ascii=False)
    # The payload is embedded directly inside an inline <script> element
    # (var departmentItems = ...). Escape the characters that can break out of
    # that element or terminate a JS string so a department name can never
    # inject markup or script (stored XSS).
    payload = (
        payload.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return mark_safe(payload)
