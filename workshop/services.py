from __future__ import annotations

from workshop.models import Service, ServiceCategory


def ensure_category_path(path) -> ServiceCategory:
    if isinstance(path, str):
        parts = [p.strip() for p in path.replace("/", " / ").split("/") if p.strip()]
    else:
        parts = [str(p).strip() for p in path if str(p).strip()]
    if not parts:
        parts = ["Основные"]
    parent = None
    category = None
    for part in parts:
        category, _ = ServiceCategory.objects.get_or_create(name=part, parent=parent)
        parent = category
    assert category is not None
    return category


def build_service_catalog_tree(active_only: bool = True) -> list[dict]:
    services_qs = Service.objects.select_related("category")
    if active_only:
        services_qs = services_qs.filter(is_active=True)

    by_category: dict[int, list[dict]] = {}
    for service in services_qs:
        by_category.setdefault(service.category_id, []).append(
            {
                "id": service.id,
                "name": service.name,
                "price": float(service.price),
                "is_active": service.is_active,
            }
        )

    categories = list(ServiceCategory.objects.all().order_by("name"))
    children_map: dict[int | None, list[ServiceCategory]] = {}
    for cat in categories:
        children_map.setdefault(cat.parent_id, []).append(cat)

    def attach(parent_id: int | None) -> list[dict]:
        nodes = []
        for cat in children_map.get(parent_id, []):
            kids = attach(cat.id)
            own = by_category.get(cat.id, [])
            if not kids and not own:
                continue
            nodes.append(
                {
                    "id": cat.id,
                    "name": cat.name,
                    "children": kids,
                    "services": own,
                }
            )
        return nodes

    return attach(None)


def category_choices() -> list[str]:
    return [c.path_label for c in ServiceCategory.objects.all()] or ["Основные"]
