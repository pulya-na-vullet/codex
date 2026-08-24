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
        tagline="Сборы двора и работа технической команды",
        description=(
            "Приложение для соседей: общие сборы, точки оплаты и мастера. "
            "Есть слайды для технической команды и полноэкранное демо «для нашего двора»."
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
                title="Презентация для технической команды",
                blurb="Слайды: проект, сборы, рабочий день. Стрелки или пробел — листать.",
                kind="slides",
            ),
            ProductPage(
                slug="sosedi",
                file_name="demo-sosedi.html",
                title="Демо для соседей",
                blurb="Слайды: двор, сборы, мастера, QR. Стрелки или пробел — листать.",
                kind="slides",
            ),
        ),
    ),
    Product(
        slug="qms",
        name="QMS",
        tagline="Quality management system",
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
                title="Презентация QMS",
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


_RELATIVE_ASSET = re.compile(
    r"""(?P<attr>src|href)=(?P<q>['"])(?P<path>(?![/#]|https?:|data:|mailto:)[^'"]+)(?P=q)""",
    flags=re.I,
)


def rewrite_relative_assets(html: str, product: Product) -> str:
    """Point local images at /static so decks work without a <base> tag.

    A <base href> makes html2canvas's empty iframe resolve to /products/ and
    the TV/Mac then flicker between the shelf and the deck about once a second.
    """
    prefix = product.static_prefix()
    html_files = set(html_link_map(product))

    def repl(match: re.Match[str]) -> str:
        path = match.group("path").replace("\\", "/").lstrip("./")
        name = path.split("/")[-1]
        if name in html_files or not name:
            return match.group(0)
        return f"{match.group('attr')}={match.group('q')}{prefix}{name}{match.group('q')}"

    return _RELATIVE_ASSET.sub(repl, html)


def inject_before_body_end(html: str, snippet: str) -> str:
    if re.search(r"</body>", html, flags=re.I):
        return re.sub(r"</body>", snippet + "</body>", html, count=1, flags=re.I)
    return html + snippet


def prepare_deck_html(html: str, product: Product, chrome: str | None = None) -> str:
    prepared = rewrite_internal_html_links(html, product)
    prepared = rewrite_relative_assets(prepared, product)
    if chrome:
        prepared = inject_before_body_end(prepared, chrome)
    return prepared


_STYLE_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.I | re.S)
_LINK_RE = re.compile(r"<link\b[^>]*>", re.I)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.I | re.S)
_BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.I | re.S)


def _prefix_selector(sel: str, scope: str) -> str:
    sel = sel.strip()
    if not sel:
        return sel
    if sel.startswith("@") or sel in {"from", "to"} or re.match(r"^[\d.]+%", sel):
        return sel
    sel = re.sub(r":root\b", scope, sel)
    sel = re.sub(r"\bhtml\b", scope, sel)
    sel = re.sub(r"\bbody\b", scope, sel)
    if sel.startswith(scope):
        return sel
    if sel == "*":
        return f"{scope}, {scope} *"
    return f"{scope} {sel}"


def scope_css(css: str, scope: str = "#product-deck") -> str:
    """Keep presentation rules inside the CRM embed, not on html/body."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    css = re.sub(r"position\s*:\s*fixed\b", "position: absolute", css, flags=re.I)
    css = re.sub(r"min-height\s*:\s*100(?:vh|dvh)\b", "min-height: 100%", css, flags=re.I)

    def transform(chunk: str) -> str:
        out: list[str] = []
        i = 0
        n = len(chunk)
        while i < n:
            if chunk[i].isspace():
                j = i
                while j < n and chunk[j].isspace():
                    j += 1
                out.append(chunk[i:j])
                i = j
                continue
            if chunk.startswith("@keyframes", i) or chunk.startswith("@-webkit-keyframes", i):
                brace = chunk.find("{", i)
                if brace < 0:
                    out.append(chunk[i:])
                    break
                j = brace
                depth = 0
                while j < n:
                    if chunk[j] == "{":
                        depth += 1
                    elif chunk[j] == "}":
                        depth -= 1
                        if depth == 0:
                            j += 1
                            break
                    j += 1
                out.append(chunk[i:j])
                i = j
                continue
            if chunk[i] == "@":
                brace = chunk.find("{", i)
                if brace < 0:
                    out.append(chunk[i:])
                    break
                header = chunk[i:brace]
                j = brace
                depth = 0
                while j < n:
                    if chunk[j] == "{":
                        depth += 1
                    elif chunk[j] == "}":
                        depth -= 1
                        if depth == 0:
                            inner = chunk[brace + 1 : j]
                            if header.strip().lower().startswith("@font-face"):
                                out.append(header + "{" + inner + "}")
                            else:
                                out.append(header + "{" + transform(inner) + "}")
                            j += 1
                            break
                    j += 1
                i = j
                continue
            brace = chunk.find("{", i)
            if brace < 0:
                out.append(chunk[i:])
                break
            selectors = chunk[i:brace]
            j = brace
            depth = 0
            while j < n:
                if chunk[j] == "{":
                    depth += 1
                elif chunk[j] == "}":
                    depth -= 1
                    if depth == 0:
                        body = chunk[brace : j + 1]
                        prefixed: list[str] = []
                        seen: set[str] = set()
                        for part in selectors.split(","):
                            item = _prefix_selector(part, scope)
                            if item and item not in seen:
                                seen.add(item)
                                prefixed.append(item)
                        out.append(", ".join(prefixed) + " " + body.lstrip())
                        j += 1
                        break
                j += 1
            i = j
        return "".join(out)

    return transform(css)


def extract_embed(html: str) -> dict[str, str]:
    """Split a full presentation document into CRM-embeddable pieces."""
    links = [
        tag
        for tag in _LINK_RE.findall(html)
        if "stylesheet" in tag.lower() or "preconnect" in tag.lower() or "font" in tag.lower()
    ]
    styles = _STYLE_RE.findall(html)
    body_match = _BODY_RE.search(html)
    body = body_match.group(1) if body_match else html
    scripts = _SCRIPT_RE.findall(body)
    body = _SCRIPT_RE.sub("", body)
    return {
        "links": "\n".join(links),
        "css": scope_css("\n".join(styles)),
        "body": body.strip(),
        "scripts": "\n".join(scripts),
    }
