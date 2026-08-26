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
    return mark_safe(json.dumps(items, ensure_ascii=False))
