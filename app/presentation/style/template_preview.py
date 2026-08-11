import hashlib
import re
from html import escape
from string import Template

from app.presentation.style.template_candidates import TemplateCandidate
from app.schemas.style_profile import VisualStyleProfileV2

SAFE_FONT = re.compile(r"^[\w\s,.'\-]+$", re.UNICODE)
PREVIEW_COPY = {
    "title": "企业方案风格预览",
    "body": "经核验的业务内容将在此区域展示",
    "left": "客户现状与关键挑战",
    "right": "可信能力与实施路径",
    "card_1": "关键洞察",
    "card_2": "解决方案",
    "card_3": "预期价值",
}


def build_sanitized_preview(
    candidate: TemplateCandidate, profile: VisualStyleProfileV2
) -> dict[str, str]:
    """Build a script-free preview containing only versioned system copy."""
    colors = profile.design_tokens.colors
    typography = profile.design_tokens.typography
    font = (
        typography.body.font_family
        if SAFE_FONT.fullmatch(typography.body.font_family)
        else "sans-serif"
    )
    title_font = (
        typography.title.font_family
        if SAFE_FONT.fullmatch(typography.title.font_family)
        else font
    )
    nodes = []
    for slot in candidate.layout_template.slots:
        box = slot.geometry
        content = escape(PREVIEW_COPY.get(slot.name, "安全示例内容"))
        is_title = slot.name == "title"
        nodes.append(
            '<section class="slot{title}" style="left:{x}%;top:{y}%;width:{width}%;'
            'height:{height}%"><span>{content}</span></section>'.format(
                title=" title" if is_title else "",
                x=box.x / 100,
                y=box.y / 100,
                width=box.width / 100,
                height=box.height / 100,
                content=content,
            )
        )
    html = Template("""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden}
body{display:grid;place-items:center;background:#111827}
.canvas{position:relative;width:min(100vw,calc(100vh * 16 / 9));aspect-ratio:16/9;
overflow:hidden;background:$canvas;color:$text;font-family:"$font",sans-serif}
.canvas:before{content:"";position:absolute;inset:0 0 auto;height:1.2%;background:$primary}
.slot{position:absolute;display:flex;align-items:flex-start;padding:2.2%;overflow:hidden;
border:1px solid $border;border-radius:8px;background:$surface;
font-size:clamp(12px,2cqw,22px);
line-height:1.35}.slot.title{padding:0;border:0;border-radius:0;background:transparent;
font-family:"$title_font",sans-serif;font-size:clamp(20px,3.2cqw,42px);font-weight:700}
</style></head><body><main class="canvas">$nodes</main></body></html>""").substitute(
        canvas=colors.canvas,
        text=colors.text_primary,
        font=escape(font, quote=True),
        primary=colors.primary,
        border=colors.border,
        surface=colors.surface,
        title_font=escape(title_font, quote=True),
        nodes="".join(nodes),
    )
    return {
        "media_type": "text/html",
        "content_set": "sanitized-capacity-v1",
        "html": html,
        "sha256": hashlib.sha256(html.encode()).hexdigest(),
    }
