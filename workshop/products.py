"""Catalog of team product presentations shown on the CRM shelf and TV."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings


@dataclass(frozen=True)
class ProductPage:
    slug: str
    file_name: str
    title: str
    blurb: str
    kind: str  # hub | slides | demo


@dataclass(frozen=True)
class Product:
    slug: str
    name: str
    tagline: str
    description: str
    rel_dir: str
    accent: str
    pages: tuple[ProductPage, ...]

    def page(self, slug: str | None) -> ProductPage | None:
        if not slug:
            return self.pages[0] if self.pages else None
        for page in self.pages:
            if page.slug == slug:
                return page
        return None

    def file_path(self, page: ProductPage) -> Path:
        return Path(settings.BASE_DIR) / "workshop" / "static" / self.rel_dir / page.file_name

    def static_prefix(self) -> str:
        prefix = f"/static/{self.rel_dir.strip('/')}/"
        if not prefix.endswith("/"):
            prefix += "/"
        return prefix

    def page_url(self, page: ProductPage | None = None) -> str:
        target = page or (self.pages[0] if self.pages else None)
        if target is None:
            return f"/products/{self.slug}"
        if target.slug == self.pages[0].slug:
            return f"/products/{self.slug}"
        return f"/products/{self.slug}/{target.slug}"

    @property
    def open_url(self) -> str:
        return self.page_url()

    def page_links(self) -> tuple[tuple[str, ProductPage], ...]:
        return tuple((self.page_url(page), page) for page in self.pages)


PRODUCTS: tuple[Product, ...] = (
    Product(
        slug="voitos",
        name="Voitos",
        tagline="Сборы двора и работа тракториста",
        description=(
            "Приложение для соседей: общие сборы, точки оплаты и мастера. "
            "Есть слайды для трактористов и полноэкранное демо «для нашего двора»."
        ),
        rel_dir="workshop/promo/products/voitos",
        accent="#1f9e8f",
        pages=(
            ProductPage(
                slug="hub",
                file_name="index.html",
                title="Стартовая Voitos",
                blurb="Выбор презентации",
                kind="hub",
            ),
            ProductPage(
                slug="traktoristy",
                file_name="presentation-traktoristy.html",
                title="Презентация для трактористов",
                blurb="Слайды: проект, сборы, рабочий день. Стрелки или пробел — листать.",
                kind="slides",
            ),
            ProductPage(
                slug="sosedi",
                file_name="demo-sosedi.html",
                title="Демо для соседей",
                blurb="Полноэкранная история двора. Листайте вниз колесом или пультом.",
                kind="demo",
            ),
        ),
    ),
    Product(
        slug="qms",
        name="QA Manager",
        tagline="Система управления качеством тестирования",
        description=(
            "Веб-приложение QMS: каталог тестов, требования, тест-раны и матрица "
            "трассируемости. ИИ-ревью кейсов, роли Admin / Analyst / Tester."
        ),
        rel_dir="workshop/promo/products/qms",
        accent="#667eea",
        pages=(
            ProductPage(
                slug="presentation",
                file_name="presentation.html",
                title="Презентация QA Manager",
                blurb="Слайды по продукту. Стрелки или пробел — листать.",
                kind="slides",
            ),
        ),
    ),
)


def get_product(slug: str) -> Product | None:
    for product in PRODUCTS:
        if product.slug == slug:
            return product
    return None


def html_link_map(product: Product) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for page in product.pages:
        mapping[page.file_name] = product.page_url(page)
    return mapping


def rewrite_internal_html_links(html: str, product: Product) -> str:
    """Point local .html hrefs at CRM URLs so the back bar stays on every hop."""
    updated = html
    for file_name, url in html_link_map(product).items():
        for original in (
            f'href="{file_name}"',
            f"href='{file_name}'",
            f'href="./{file_name}"',
            f"href='./{file_name}'",
        ):
            quote = '"' if original.startswith('href="') else "'"
            updated = updated.replace(original, f"href={quote}{url}{quote}")
    return updated


def inject_base_href(html: str, href: str) -> str:
    if re.search(r"<base\b", html, flags=re.I):
        return html
    prefix = href if href.endswith("/") else href + "/"
    return re.sub(
        r"(<head\b[^>]*>)",
        rf'\1<base href="{prefix}">',
        html,
        count=1,
        flags=re.I,
    )


def inject_before_body_end(html: str, snippet: str) -> str:
    if re.search(r"</body>", html, flags=re.I):
        return re.sub(r"</body>", snippet + "</body>", html, count=1, flags=re.I)
    return html + snippet


def prepare_deck_html(html: str, product: Product, chrome: str | None = None) -> str:
    prepared = rewrite_internal_html_links(html, product)
    prepared = inject_base_href(prepared, product.static_prefix())
    if chrome:
        prepared = inject_before_body_end(prepared, chrome)
    return prepared
