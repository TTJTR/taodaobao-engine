from app.presentation.layouts.models import LayoutTemplate
from app.presentation.layouts.templates import LAYOUT_TEMPLATES
from app.schemas.presentation import LayoutToken


class LayoutRegistry:
    def __init__(self, templates: tuple[LayoutTemplate, ...]) -> None:
        self._templates = {template.token: template for template in templates}
        if len(self._templates) != len(templates):
            raise ValueError("layout tokens must be unique")

    def get(self, token: LayoutToken) -> LayoutTemplate:
        try:
            return self._templates[token]
        except KeyError as exc:
            raise ValueError(f"unknown layout token: {token}") from exc

    @property
    def tokens(self) -> tuple[str, ...]:
        return tuple(self._templates)


default_layout_registry = LayoutRegistry(LAYOUT_TEMPLATES)
