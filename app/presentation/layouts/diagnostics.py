from pydantic import BaseModel, ConfigDict, Field

from app.schemas.presentation import PositionedPresentationSpec


class LayoutDiagnosticReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    passed: bool
    checked_slides: int = Field(ge=0)
    errors: tuple[str, ...]


def diagnose_layout(spec: PositionedPresentationSpec) -> LayoutDiagnosticReport:
    errors: list[str] = []
    for slide in spec.slides:
        boxes = []
        for item in slide.components:
            box = item.geometry
            if box.x + box.width > 10_000 or box.y + box.height > 10_000:
                errors.append(f"{slide.slide_id}:{item.slot_name}:out_of_bounds")
            for other_name, other in boxes:
                if _overlaps(box, other):
                    errors.append(
                        f"{slide.slide_id}:{other_name}:{item.slot_name}:unexpected_overlap"
                    )
            boxes.append((item.slot_name, box))
    return LayoutDiagnosticReport(
        passed=not errors,
        checked_slides=len(spec.slides),
        errors=tuple(errors),
    )


def _overlaps(first, second) -> bool:
    return not (
        first.x + first.width <= second.x
        or second.x + second.width <= first.x
        or first.y + first.height <= second.y
        or second.y + second.height <= first.y
    )
