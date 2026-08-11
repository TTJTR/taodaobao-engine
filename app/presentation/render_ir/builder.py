import hashlib
import json
import uuid
from dataclasses import dataclass

from app.presentation.render_ir.models import (
    FactTrace,
    RenderIR,
    RenderSlide,
    ShapeNode,
    TextNode,
    TextProvenance,
    TextRun,
)
from app.presentation.render_ir.text_layout import TextLayoutEngine, TextLayoutRequest
from app.schemas.presentation import (
    BoundFactItem,
    ComponentGeometry,
    FactBinding,
    PositionedComponent,
    PositionedPresentationSpec,
)
from app.schemas.style_profile import TypographyToken, VisualStyleProfileV2


@dataclass(frozen=True)
class _TextField:
    path: str
    text: str
    binding: FactBinding | None = None
    typography_role: str | None = None


class RenderIRBuilder:
    """Compile positioned semantics into immutable renderer input without inventing content."""

    def __init__(self, text_layout_engine: TextLayoutEngine | None = None) -> None:
        self.text_layout_engine = text_layout_engine or TextLayoutEngine()

    def build(
        self,
        positioned_spec: PositionedPresentationSpec,
        style: VisualStyleProfileV2,
        *,
        compiled_style_hash: str,
        text_provenance: dict[str, TextProvenance],
    ) -> RenderIR:
        used_provenance: set[str] = set()
        slides = tuple(
            RenderSlide(
                slide_id=slide.slide_id,
                layers=(
                    *self._slide_chrome(slide.slide_id, slide.layout_token, style),
                    *tuple(
                        node
                        for item in slide.components
                        for node in self._component_nodes(
                            item,
                            style,
                            text_provenance,
                            used_provenance,
                            slide.layout_token,
                        )
                    ),
                ),
            )
            for slide in positioned_spec.slides
        )
        unused = set(text_provenance) - used_provenance
        if unused:
            raise ValueError("unused text provenance entries: " + ",".join(sorted(unused)))
        return RenderIR(
            presentation_id=positioned_spec.presentation_id,
            positioned_spec_hash=self._hash(positioned_spec.model_dump(mode="json")),
            compiled_style_hash=compiled_style_hash,
            slides=slides,
        )

    def _component_nodes(
        self,
        item: PositionedComponent,
        style: VisualStyleProfileV2,
        provenance: dict[str, TextProvenance],
        used_provenance: set[str],
        layout_token: str,
    ) -> tuple[ShapeNode | TextNode, ...]:
        component = item.component
        skin = self._skin(style, component.component_type)
        colors = style.design_tokens.colors
        geometry = self._visual_geometry(
            component.component_type, self._to_canvas(item.geometry), layout_token
        )
        shapes: list[ShapeNode] = []
        if component.component_type == "title":
            shapes.append(
                ShapeNode(
                    node_id=uuid.uuid5(component.component_id, "title-accent"),
                    geometry=ComponentGeometry(
                        x=geometry.x,
                        y=geometry.y + geometry.height - 6,
                        width=min(180, geometry.width),
                        height=6,
                    ),
                    z_index=item.z_index,
                    fill=colors.accent,
                )
            )
        else:
            shapes.append(
                ShapeNode(
                    node_id=uuid.uuid5(component.component_id, "shape"),
                    geometry=geometry,
                    z_index=item.z_index,
                    fill=getattr(colors, skin.fill_token),
                    stroke=(
                        getattr(colors, skin.border_token) if skin.border_width else None
                    ),
                    stroke_width=round(skin.border_width / 10_000 * 1600, 2),
                    corner_radius=round(skin.radius / 10_000 * 1600, 2),
                    shadow_color=("#000000" if component.component_type != "source_list" else None),
                    shadow_blur=(12 if component.component_type != "source_list" else 0),
                    shadow_offset_y=(6 if component.component_type != "source_list" else 0),
                    opacity=0.98,
                )
            )
            if skin.accent_placement != "none":
                shapes.append(
                    self._accent_node(
                        component.component_id,
                        geometry,
                        item.z_index + 1,
                        colors.accent,
                        skin.accent_placement,
                    )
                )
        fields = self._text_fields(component)
        content_geometry = self._inset_geometry(
            geometry,
            0 if component.component_type == "title" else skin.padding,
        )
        boxes = self._field_boxes(component.component_type, content_geometry, len(fields))
        shapes.extend(
            self._internal_shapes(
                component.component_id,
                component.component_type,
                boxes,
                item.z_index + 1,
                colors,
            )
        )
        text_nodes = []
        for index, field in enumerate(fields):
            typography = self._typography(
                style, item.text_style_token, index, field.typography_role
            )
            text_nodes.append(
                self._text_node(
                component.component_id,
                field,
                boxes[index],
                    item.z_index + 2,
                    typography,
                provenance,
                used_provenance,
                    self._text_color(component.component_type, index, colors, typography),
                self._text_alignment(component.component_type, skin.alignment),
            )
            )
        return (*tuple(shapes), *tuple(text_nodes))

    def _text_node(
        self,
        component_id: uuid.UUID,
        field: _TextField,
        geometry: ComponentGeometry,
        z_index: int,
        typography: TypographyToken,
        provenance: dict[str, TextProvenance],
        used_provenance: set[str],
        fill: str,
        text_align: str,
    ) -> TextNode:
        key = f"{component_id}:{field.path}"
        if field.binding is not None:
            source = TextProvenance(
                content_origin=(
                    "ledger_verbatim"
                    if field.binding.content_mode == "verbatim"
                    else "ledger_label"
                ),
                fact_trace=FactTrace(
                    claim_id=field.binding.claim_id,
                    claim_key=field.binding.claim_key,
                    evidence_ids=tuple(field.binding.evidence_ids),
                    source_ids=tuple(field.binding.source_ids),
                    content_mode=field.binding.content_mode,
                ),
            )
            if key in provenance:
                raise ValueError(f"fact-bound text must not override provenance: {key}")
        else:
            try:
                source = provenance[key]
            except KeyError as exc:
                raise ValueError(f"missing text provenance: {key}") from exc
            used_provenance.add(key)
        run = TextRun(
            run_id=uuid.uuid5(component_id, f"run:{field.path}"),
            text=field.text,
            **source.model_dump(mode="python"),
        )
        layout = self.text_layout_engine.layout(
            TextLayoutRequest(
                text=field.text,
                requested_font=typography.font_family,
                font_size=round(typography.size_pt * 4 / 3, 2),
                min_font_size=12,
                line_height_ratio=typography.line_spacing,
                box_width=geometry.width,
                box_height=geometry.height,
            )
        )
        if layout.overflowed:
            raise ValueError(f"text layout overflowed after font fallback: {key}")
        return TextNode(
            node_id=uuid.uuid5(component_id, f"text:{field.path}"),
            component_id=component_id,
            geometry=geometry,
            z_index=z_index,
            fill=fill,
            font_weight=typography.weight,
            text_align=text_align,
            runs=(run,),
            layout=layout,
        )

    @staticmethod
    def _text_fields(component) -> tuple[_TextField, ...]:
        component_type = component.component_type
        if component_type == "title":
            return (_TextField("text", component.text),)
        if component_type == "key_message":
            return (_TextField("text", component.text, component.fact_binding),)
        if component_type == "evidence_card":
            return (
                _TextField("heading", component.heading),
                _TextField("body", component.body, component.fact_binding, "evidence"),
            )
        if component_type == "metric":
            return (
                _TextField("label", component.label, typography_role="subtitle"),
                _TextField("value", component.value, component.fact_binding, "display"),
            )
        if component_type == "comparison":
            return (
                _TextField("heading", component.heading),
                *RenderIRBuilder._bound_items("left", (component.left,)),
                *RenderIRBuilder._bound_items("right", (component.right,)),
            )
        if component_type in {"timeline", "process"}:
            collection = component.items if component_type == "timeline" else component.steps
            return (
                _TextField("heading", component.heading),
                *RenderIRBuilder._bound_items("items", collection),
            )
        if component_type == "source_list":
            return (
                _TextField("heading", component.heading),
                *tuple(
                    _TextField(f"sources.{index}.label", source.label)
                    if source.fact_binding is None
                    else _TextField(
                        f"sources.{index}.label",
                        source.label,
                        typography_role="evidence",
                    )
                    for index, source in enumerate(component.sources)
                ),
            )
        raise ValueError(f"unsupported component type: {component_type}")

    @staticmethod
    def _bound_items(prefix: str, items: tuple[BoundFactItem, ...] | list[BoundFactItem]):
        fields = []
        for index, item in enumerate(items):
            fields.extend(
                (
                    _TextField(
                        f"{prefix}.{index}.label", item.label, typography_role="subtitle"
                    ),
                    _TextField(
                        f"{prefix}.{index}.text",
                        item.text,
                        item.fact_binding,
                        "evidence",
                    ),
                )
            )
        return tuple(fields)

    @staticmethod
    def _to_canvas(geometry: ComponentGeometry) -> ComponentGeometry:
        return ComponentGeometry(
            x=round(geometry.x / 10_000 * 1600),
            y=round(geometry.y / 10_000 * 900),
            width=max(1, round(geometry.width / 10_000 * 1600)),
            height=max(1, round(geometry.height / 10_000 * 900)),
        )

    @staticmethod
    def _visual_geometry(
        component_type: str, geometry: ComponentGeometry, layout_token: str
    ) -> ComponentGeometry:
        if component_type == "title" and layout_token == "cover":
            return ComponentGeometry(
                x=geometry.x,
                y=geometry.y + 30,
                width=min(820, geometry.width),
                height=geometry.height,
            )
        if component_type != "key_message" or geometry.height <= 260:
            return geometry
        height = 260
        return ComponentGeometry(
            x=geometry.x,
            y=geometry.y + (geometry.height - height) // 2,
            width=geometry.width,
            height=height,
        )

    @staticmethod
    def _internal_shapes(component_id, component_type, boxes, z_index, colors):
        nodes = []
        if component_type in {"evidence_card", "metric"} and len(boxes) == 2:
            nodes.append(
                ShapeNode(
                    node_id=uuid.uuid5(component_id, "field-separator"),
                    geometry=ComponentGeometry(
                        x=boxes[0].x,
                        y=boxes[0].y + boxes[0].height,
                        width=boxes[0].width,
                        height=2,
                    ),
                    z_index=z_index,
                    fill=colors.border,
                    opacity=0.7,
                )
            )
        if component_type in {"comparison", "timeline", "process"}:
            for index in range(1, len(boxes), 2):
                label = boxes[index]
                body = boxes[index + 1]
                nodes.append(
                    ShapeNode(
                        node_id=uuid.uuid5(component_id, f"item-panel:{index}"),
                        geometry=ComponentGeometry(
                            x=label.x + 8,
                            y=label.y + 8,
                            width=max(1, label.width - 16),
                            height=max(1, label.height + body.height - 16),
                        ),
                        z_index=z_index,
                        fill=colors.canvas,
                        stroke=colors.border,
                        stroke_width=1,
                        corner_radius=14,
                        shadow_color="#000000",
                        shadow_blur=6,
                        shadow_offset_y=3,
                    )
                )
        if component_type == "source_list":
            for index, box in enumerate(boxes[1:], start=1):
                nodes.extend(
                    (
                        ShapeNode(
                            node_id=uuid.uuid5(component_id, f"source-row:{index}"),
                            geometry=ComponentGeometry(
                                x=box.x + 6,
                                y=box.y + 6,
                                width=max(1, box.width - 12),
                                height=max(1, box.height - 12),
                            ),
                            z_index=z_index,
                            fill=colors.canvas,
                            stroke=colors.border,
                            stroke_width=1,
                            corner_radius=10,
                        ),
                        ShapeNode(
                            node_id=uuid.uuid5(component_id, f"source-marker:{index}"),
                            geometry=ComponentGeometry(
                                x=box.x + 6,
                                y=box.y + 6,
                                width=10,
                                height=max(1, box.height - 12),
                            ),
                            z_index=z_index + 1,
                            fill=colors.accent,
                        ),
                    )
                )
        return tuple(nodes)

    @staticmethod
    def _field_boxes(
        component_type: str, geometry: ComponentGeometry, count: int
    ) -> tuple[ComponentGeometry, ...]:
        if count == 1:
            return (geometry,)
        if component_type in {"evidence_card", "metric"}:
            heading_height = max(1, round(geometry.height * 0.24))
            return (
                ComponentGeometry(
                    x=geometry.x,
                    y=geometry.y,
                    width=geometry.width,
                    height=heading_height,
                ),
                ComponentGeometry(
                    x=geometry.x,
                    y=geometry.y + heading_height,
                    width=geometry.width,
                    height=geometry.height - heading_height,
                ),
            )
        heading_height = max(1, round(geometry.height * 0.16))
        body_y = geometry.y + heading_height
        body_height = geometry.height - heading_height
        item_fields = count - 1
        columns = 2 if component_type in {"comparison", "source_list"} else item_fields // 2
        columns = max(1, columns)
        rows = (item_fields + columns - 1) // columns
        cell_width = max(1, geometry.width // columns)
        cell_height = max(1, body_height // rows)
        boxes = [
            ComponentGeometry(
                x=geometry.x,
                y=geometry.y,
                width=geometry.width,
                height=heading_height,
            )
        ]
        for index in range(item_fields):
            item_group = index // 2 if component_type != "source_list" else index
            column = item_group % columns
            row = item_group // columns
            if component_type == "source_list":
                boxes.append(
                    ComponentGeometry(
                        x=geometry.x + column * cell_width,
                        y=body_y + row * cell_height,
                        width=cell_width,
                        height=cell_height,
                    )
                )
                continue
            is_label = index % 2 == 0
            label_height = max(1, round(cell_height * 0.28))
            boxes.append(
                ComponentGeometry(
                    x=geometry.x + column * cell_width,
                    y=body_y + row * cell_height + (0 if is_label else label_height),
                    width=cell_width,
                    height=(label_height if is_label else cell_height - label_height),
                )
            )
        return tuple(boxes)

    @staticmethod
    def _inset_geometry(geometry: ComponentGeometry, normalized_padding: int) -> ComponentGeometry:
        horizontal = round(normalized_padding / 10_000 * 1600)
        vertical = round(normalized_padding / 10_000 * 900)
        horizontal = min(horizontal, max(0, (geometry.width - 1) // 3))
        vertical = min(vertical, max(0, (geometry.height - 1) // 3))
        return ComponentGeometry(
            x=geometry.x + horizontal,
            y=geometry.y + vertical,
            width=max(1, geometry.width - horizontal * 2),
            height=max(1, geometry.height - vertical * 2),
        )

    @staticmethod
    def _skin(style: VisualStyleProfileV2, component_type: str):
        preferred = {
            "title": "plain_text",
            "key_message": "filled_card",
            "evidence_card": "outline_card",
        }.get(component_type, "filled_card")
        for skin in style.component_skins:
            if skin.token == preferred and component_type in skin.applies_to:
                return skin
        for skin in style.component_skins:
            if component_type in skin.applies_to:
                return skin
        raise ValueError(f"style profile has no skin for component type: {component_type}")

    @staticmethod
    def _slide_chrome(slide_id, layout_token, style: VisualStyleProfileV2):
        colors = style.design_tokens.colors
        nodes = [
            ShapeNode(
                node_id=uuid.uuid5(slide_id, "canvas-background"),
                geometry=ComponentGeometry(x=0, y=0, width=1600, height=900),
                z_index=0,
                fill=colors.canvas,
            )
        ]
        if layout_token == "cover":
            nodes.extend(
                (
                    ShapeNode(
                        node_id=uuid.uuid5(slide_id, "cover-panel"),
                        geometry=ComponentGeometry(x=1110, y=0, width=490, height=900),
                        z_index=1,
                        fill=colors.primary,
                    ),
                    ShapeNode(
                        node_id=uuid.uuid5(slide_id, "cover-secondary"),
                        geometry=ComponentGeometry(x=1190, y=100, width=330, height=700),
                        z_index=2,
                        fill=colors.secondary,
                        opacity=0.72,
                    ),
                    ShapeNode(
                        node_id=uuid.uuid5(slide_id, "cover-cutout"),
                        geometry=ComponentGeometry(x=1260, y=170, width=260, height=560),
                        z_index=3,
                        fill=colors.canvas,
                        opacity=0.96,
                    ),
                    ShapeNode(
                        node_id=uuid.uuid5(slide_id, "cover-accent"),
                        geometry=ComponentGeometry(x=1080, y=100, width=18, height=700),
                        z_index=3,
                        fill=colors.accent,
                    ),
                    ShapeNode(
                        node_id=uuid.uuid5(slide_id, "cover-kicker"),
                        geometry=ComponentGeometry(x=144, y=122, width=72, height=12),
                        z_index=3,
                        fill=colors.primary,
                    ),
                    ShapeNode(
                        node_id=uuid.uuid5(slide_id, "cover-footer"),
                        geometry=ComponentGeometry(x=144, y=800, width=720, height=3),
                        z_index=3,
                        fill=colors.border,
                    ),
                )
            )
        else:
            nodes.extend(
                (
                    ShapeNode(
                        node_id=uuid.uuid5(slide_id, "top-rail"),
                        geometry=ComponentGeometry(x=0, y=0, width=1600, height=12),
                        z_index=1,
                        fill=colors.primary,
                    ),
                    ShapeNode(
                        node_id=uuid.uuid5(slide_id, "title-marker"),
                        geometry=ComponentGeometry(x=82, y=48, width=12, height=108),
                        z_index=1,
                        fill=colors.accent,
                    ),
                    ShapeNode(
                        node_id=uuid.uuid5(slide_id, "footer-rule"),
                        geometry=ComponentGeometry(x=104, y=858, width=1392, height=2),
                        z_index=1,
                        fill=colors.border,
                        opacity=0.65,
                    ),
                    ShapeNode(
                        node_id=uuid.uuid5(slide_id, "footer-block"),
                        geometry=ComponentGeometry(x=1420, y=846, width=76, height=14),
                        z_index=1,
                        fill=colors.primary,
                    ),
                )
            )
        return tuple(nodes)

    @staticmethod
    def _accent_node(component_id, geometry, z_index, color, placement) -> ShapeNode:
        thickness = 8
        if placement in {"top", "bottom"}:
            accent = ComponentGeometry(
                x=geometry.x,
                y=geometry.y if placement == "top" else geometry.y + geometry.height - thickness,
                width=geometry.width,
                height=thickness,
            )
        else:
            accent = ComponentGeometry(
                x=geometry.x,
                y=geometry.y,
                width=thickness,
                height=geometry.height,
            )
        return ShapeNode(
            node_id=uuid.uuid5(component_id, f"accent:{placement}"),
            geometry=accent,
            z_index=z_index,
            fill=color,
        )

    @staticmethod
    def _text_alignment(component_type: str, skin_alignment: str) -> str:
        return "center" if component_type == "metric" else skin_alignment

    @staticmethod
    def _typography(
        style: VisualStyleProfileV2,
        token: str,
        field_index: int,
        role_override: str | None = None,
    ) -> TypographyToken:
        typography = style.design_tokens.typography
        if role_override is not None:
            return getattr(typography, role_override)
        if field_index > 0:
            return typography.body
        return {
            "display": typography.display,
            "heading": typography.title,
            "body": typography.body,
            "evidence": typography.evidence,
        }[token]

    @staticmethod
    def _text_color(component_type, field_index, colors, typography):
        if component_type == "metric":
            return colors.primary if field_index else colors.text_muted
        if component_type in {"evidence_card", "comparison", "timeline", "process"}:
            return colors.primary if field_index == 0 else getattr(
                colors, typography.color_token
            )
        return getattr(colors, typography.color_token)

    @staticmethod
    def _hash(value: dict) -> str:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()
