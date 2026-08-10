from app.presentation.layouts.models import LayoutTemplate, Slot
from app.presentation.layouts.registry import LayoutRegistry, default_layout_registry
from app.schemas.presentation import (
    PositionedComponent,
    PositionedPresentationSpec,
    PositionedSlide,
    PresentationSpecData,
    SemanticComponent,
)


class LayoutEngine:
    """Assign immutable semantic components to deterministic slots without rewriting content."""

    def __init__(self, registry: LayoutRegistry = default_layout_registry) -> None:
        self.registry = registry

    def position(self, spec: PresentationSpecData) -> PositionedPresentationSpec:
        slides = tuple(self._position_slide(slide) for slide in spec.slides)
        return PositionedPresentationSpec(presentation_id=spec.presentation_id, slides=slides)

    def _position_slide(self, slide) -> PositionedSlide:
        template = self.registry.get(slide.layout_token)
        available = list(template.slots)
        positioned: list[PositionedComponent] = []
        for component in slide.components:
            slot = self._take_slot(component, available, template)
            self._validate_capacity(component, slot)
            positioned.append(
                PositionedComponent(
                    slot_name=slot.name,
                    geometry=slot.geometry,
                    z_index=slot.z_index,
                    text_style_token=slot.text_style_token,
                    component=component,
                )
            )
            available.remove(slot)
        return PositionedSlide(
            slide_id=slide.slide_id,
            layout_token=slide.layout_token,
            components=tuple(positioned),
        )

    @staticmethod
    def _take_slot(
        component: SemanticComponent, available: list[Slot], template: LayoutTemplate
    ) -> Slot:
        for slot in available:
            if component.component_type in slot.allowed_component_types:
                return slot
        raise ValueError(
            f"layout {template.token} has no slot for component {component.component_type}"
        )

    @staticmethod
    def _validate_capacity(component: SemanticComponent, slot: Slot) -> None:
        text = " ".join(
            value
            for field in ("text", "heading", "body")
            if isinstance((value := getattr(component, field, None)), str)
        )
        if len(text) > slot.capacity.max_characters:
            raise ValueError(
                f"component {component.component_id} exceeds slot {slot.name} character capacity"
            )
