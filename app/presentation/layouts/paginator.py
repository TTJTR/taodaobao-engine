import uuid

from app.schemas.presentation import PresentationSpecData, SlideSchema


class SlidePaginator:
    """Split overloaded semantic slides before evidence fingerprints are frozen."""

    SLOT_LIMITS = {
        "cover": 1,
        "title_body": 1,
        "two_column": 2,
        "three_cards": 3,
        "evidence_grid": 4,
    }
    NESTED_LIMITS = {
        "timeline": ("items", 4, 2),
        "process": ("steps", 5, 2),
        "source_list": ("sources", 6, 1),
    }

    def paginate(self, spec: PresentationSpecData) -> PresentationSpecData:
        slides = [page for slide in spec.slides for page in self._paginate_slide(slide)]
        return PresentationSpecData(
            schema_version=spec.schema_version,
            presentation_id=spec.presentation_id,
            slides=slides,
        )

    def _paginate_slide(self, slide: SlideSchema) -> list[SlideSchema]:
        title = [component for component in slide.components if component.component_type == "title"]
        business = [
            component for component in slide.components if component.component_type != "title"
        ]
        if len(title) > 1:
            raise ValueError("a slide may contain at most one title component")

        nested = [
            component for component in business if component.component_type in self.NESTED_LIMITS
        ]
        if nested:
            if len(business) != 1:
                raise ValueError("nested list layouts accept exactly one business component")
            component = nested[0]
            field, limit, minimum = self.NESTED_LIMITS[component.component_type]
            values = list(getattr(component, field))
            chunks = self._balanced_chunks(values, limit, minimum)
            return [
                self._page(
                    slide, title, [self._clone_component(component, field, chunk, index)], index
                )
                for index, chunk in enumerate(chunks)
            ]

        limit = self.SLOT_LIMITS.get(slide.layout_token, 1)
        if len(business) <= limit:
            return [slide]
        chunks = [business[index : index + limit] for index in range(0, len(business), limit)]
        return [self._page(slide, title, chunk, index) for index, chunk in enumerate(chunks)]

    @staticmethod
    def _page(slide, title, business, index) -> SlideSchema:
        page_title = title
        if title and index:
            page_title = [
                title[0].model_copy(
                    update={"component_id": uuid.uuid5(title[0].component_id, f"page:{index}")}
                )
            ]
        return SlideSchema(
            slide_id=slide.slide_id if index == 0 else uuid.uuid5(slide.slide_id, f"page:{index}"),
            layout_token=slide.layout_token,
            components=[*page_title, *business],
        )

    @staticmethod
    def _clone_component(component, field, values, index):
        if index == 0 and len(values) == len(getattr(component, field)):
            return component
        return component.model_copy(
            update={
                "component_id": uuid.uuid5(component.component_id, f"page:{index}"),
                field: values,
            }
        )

    @staticmethod
    def _balanced_chunks(values: list, limit: int, minimum: int) -> list[list]:
        chunks = [values[index : index + limit] for index in range(0, len(values), limit)]
        if len(chunks) > 1 and len(chunks[-1]) < minimum:
            missing = minimum - len(chunks[-1])
            chunks[-1] = [*chunks[-2][-missing:], *chunks[-1]]
            chunks[-2] = chunks[-2][:-missing]
        return chunks
