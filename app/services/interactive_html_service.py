from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

import httpx

MAX_HTML_BYTES = 100 * 1024
SYSTEM_LABELS = {
    "互动演示",
    "概览",
    "可信事实",
    "依据与边界",
    "风险与待确认",
    "来源依据",
    "展开 / 收起",
    "上一节",
    "下一节",
    "historical_fact",
    "enterprise_capability",
    "ai_inference",
    "pending_confirmation",
    "历史事实",
    "企业能力",
    "AI 推断",
    "待确认信息",
}

SYSTEM_PROMPT = """你是企业售前互动演示的前端导演。只输出完整的单文件 HTML。

视觉要求：中文、暖白或纯白画布、深蓝灰正文、单一品牌强调色；依靠网格、留白、字号和几何图形，
禁止 emoji、暗色科技大屏、彩虹渐变、玻璃卡片堆砌和外部素材。

事实契约（违反即废弃）：
1. 业务文字只能逐字复制输入 claims 或 evidence，禁止改写、概括、补充效果和数字。
2. 每条 claim 必须恰好出现一次，并放在带 data-claim-id="原 claim_id" 的元素内；该元素内部只能
   放该 claim 的原文，不得嵌套标题、标签或来源。
3. evidence 如展示，必须放在带 data-evidence-id="原 evidence_id" 的元素内并逐字复制 quote。
4. 文档标题必须放在 data-document-title 元素内并逐字复制 title。
5. 导航、章节名、按钮和边界标签必须放在 data-system-label 元素内，且只能使用
   supplied_system_labels。
6. 除 style、script、空白外，禁止出现没有上述四种绑定属性覆盖的可见文字。

安全与交互契约：
1. 只输出原生 HTML/CSS，禁止输出任何 script；交互由平台注入固定运行时。不得使用任何外链、
   字体、图片、网络请求、表单或跳转。
2. nav 必须带 data-role="section-nav" 并保持 sticky；可放置 data-role="scroll-progress" 进度条。
3. 至少放置一个 data-action="toggle" 且 data-target="#目标ID" 的按钮，目标元素由平台切换 hidden；
   CSS 需要提供 hover 和展开后的视觉状态，并尊重 prefers-reduced-motion。
4. 采用 3-5 个章节，桌面和移动端均不能横向溢出。HTML 小于 60KB。
"""


class InteractiveHTMLValidationError(ValueError):
    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        super().__init__("；".join(violations))


@dataclass
class _Frame:
    tag: str
    binding: tuple[str, str] | None = None
    text: list[str] = field(default_factory=list)


class _BoundHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.frames: list[_Frame] = []
        self.bindings: list[tuple[str, str, str]] = []
        self.unbound_text: list[str] = []
        self.violations: list[str] = []
        self.has_nav = False
        self.style_parts: list[str] = []
        self.script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        denied_tags = {
            "iframe",
            "object",
            "embed",
            "form",
            "input",
            "textarea",
            "select",
            "base",
            "link",
        }
        if tag in denied_tags:
            self.violations.append(f"禁止标签 <{tag}>")
        if tag == "nav" and attr.get("data-role") == "section-nav":
            self.has_nav = True
        for key, value in attr.items():
            if key.startswith("on"):
                self.violations.append(f"禁止事件属性 {key}")
            if key in {"src", "action", "formaction", "poster", "data", "xlink:href"}:
                self.violations.append(f"禁止资源或提交属性 {key}")
            if key == "href" and value and not value.startswith("#"):
                self.violations.append("href 只能指向页内章节")
            if key == "style" and _unsafe_css(value):
                self.violations.append("内联 style 包含外部资源或动态内容")
        binding = None
        for kind, key in (
            ("claim", "data-claim-id"),
            ("evidence", "data-evidence-id"),
            ("system", "data-system-label"),
            ("title", "data-document-title"),
        ):
            if key in attr:
                binding = (kind, attr[key])
                break
        self.frames.append(_Frame(tag=tag, binding=binding))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self.frames:
            return
        frame = self.frames.pop()
        if frame.binding:
            self.bindings.append((frame.binding[0], frame.binding[1], "".join(frame.text)))

    def handle_data(self, data: str) -> None:
        if not data.strip():
            return
        if any(frame.tag == "style" for frame in self.frames):
            self.style_parts.append(data)
            return
        if any(frame.tag == "script" for frame in self.frames):
            self.script_parts.append(data)
            return
        for frame in reversed(self.frames):
            if frame.binding:
                frame.text.append(data)
                return
        if any(frame.tag in {"head", "svg"} for frame in self.frames):
            return
        self.unbound_text.append(data.strip())


def _normalized(value: str) -> str:
    return " ".join(value.split())


def _unsafe_css(value: str) -> bool:
    return bool(
        re.search(
            r"url\s*\(|@import|expression\s*\(|-moz-binding|content\s*:\s*['\"]\s*\S",
            value,
            re.IGNORECASE,
        )
    )


def extract_html(raw: str) -> str:
    value = raw.strip()
    value = re.sub(r"^```(?:html)?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*```$", "", value)
    start = value.lower().find("<!doctype html")
    if start < 0:
        start = value.lower().find("<html")
    if start < 0 or "</html>" not in value.lower():
        raise InteractiveHTMLValidationError(["模型没有返回完整 HTML"])
    result = value[start:].strip()
    if len(result.encode("utf-8")) > MAX_HTML_BYTES:
        raise InteractiveHTMLValidationError(["HTML 超过 100KB 上限"])
    return result


def audit_interactive_html(document: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    parser = _BoundHTMLParser()
    parser.feed(document)
    violations = list(parser.violations)
    if parser.unbound_text:
        violations.append("存在未绑定可见文字：" + "、".join(parser.unbound_text[:4]))
    if not parser.has_nav:
        violations.append('缺少 nav[data-role="section-nav"]')
    css = "\n".join(parser.style_parts)
    if _unsafe_css(css):
        violations.append("CSS 包含外部资源或动态可见内容")
    script = "\n".join(parser.script_parts)
    unsafe_script = re.compile(
        r"\b(fetch|XMLHttpRequest|WebSocket|EventSource|eval|Function|indexedDB)\b"
        r"|sendBeacon|dynamic\s+import|\bimport\s*\(|document\.cookie"
        r"|localStorage|sessionStorage|window\.(parent|top|opener)"
        r"|\b(parent|top|opener)\. |(?:window\.)?location\b|history\."
        r"|document\.write|\.innerHTML\b|\.outerHTML\b|insertAdjacentHTML"
        r"|createTextNode|\.textContent\s*=|\.innerText\s*=",
        re.IGNORECASE,
    )
    if unsafe_script.search(script):
        violations.append("JavaScript 包含网络、跳转、存储或动态文字能力")

    expected_claims = {
        str(item["claim_id"]): _normalized(str(item["text"]))
        for item in snapshot.get("released_claims", [])
    }
    expected_evidence = {
        str(item["evidence_id"]): _normalized(str(item["quote"]))
        for item in snapshot.get("evidence", [])
    }
    seen_claims: list[str] = []
    seen_titles = 0
    for kind, identifier, text in parser.bindings:
        normalized = _normalized(text)
        if kind == "claim":
            seen_claims.append(identifier)
            if identifier not in expected_claims:
                violations.append(f"未知 claim_id：{identifier}")
            elif normalized != expected_claims[identifier]:
                violations.append(f"claim {identifier} 未逐字复制")
        elif kind == "evidence":
            if identifier not in expected_evidence:
                violations.append(f"未知 evidence_id：{identifier}")
            elif normalized != expected_evidence[identifier]:
                violations.append(f"evidence {identifier} 未逐字复制")
        elif kind == "title":
            seen_titles += 1
            if normalized != _normalized(str(snapshot.get("title", ""))):
                violations.append("演示标题与输入快照不一致")
        elif kind == "system" and normalized not in SYSTEM_LABELS:
            violations.append(f"未授权系统标签：{normalized}")
    for claim_id in expected_claims:
        count = seen_claims.count(claim_id)
        if count != 1:
            violations.append(f"claim {claim_id} 必须恰好出现一次，当前 {count} 次")
    if seen_titles == 0:
        violations.append("缺少 data-document-title 标题绑定")
    if violations:
        raise InteractiveHTMLValidationError(list(dict.fromkeys(violations)))
    return {
        "fact_binding_passed": True,
        "security_passed": True,
        "claim_count": len(expected_claims),
        "evidence_count": len(expected_evidence),
        "html_bytes": len(document.encode("utf-8")),
    }


def _inject_csp(document: str) -> str:
    if "content-security-policy" in document.lower():
        return document
    csp = (
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; "
        "connect-src 'none'; form-action 'none'; base-uri 'none'; object-src 'none'\">"
    )
    return re.sub(r"</head>", csp + "</head>", document, count=1, flags=re.IGNORECASE)


def _enforce_document_title(document: str, title: str) -> str:
    trusted_title = f"<title data-document-title>{html.escape(title)}</title>"
    if re.search(r"<title\b[^>]*>.*?</title>", document, re.IGNORECASE | re.DOTALL):
        return re.sub(
            r"<title\b[^>]*>.*?</title>",
            lambda _: trusted_title,
            document,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
    return re.sub(
        r"</head>",
        lambda _: trusted_title + "</head>",
        document,
        count=1,
        flags=re.IGNORECASE,
    )


def _inject_interaction_runtime(document: str) -> str:
    runtime = """<script id="taodaobao-interaction-runtime">
document.addEventListener('DOMContentLoaded',()=>{
const nav=document.querySelector('[data-role="section-nav"]');
const links=nav?[...nav.querySelectorAll('a[href^="#"]')]:[];
const activate=link=>links.forEach(item=>item.toggleAttribute('aria-current',item===link));
links.forEach(link=>link.addEventListener('click',event=>{
event.preventDefault();const target=document.querySelector(link.getAttribute('href'));
if(target){target.scrollIntoView({behavior:'smooth',block:'start'});activate(link)}}));
document.querySelectorAll('[data-action="toggle"][data-target]').forEach(button=>{
const target=document.querySelector(button.dataset.target);if(!target)return;
button.addEventListener('click',()=>{target.hidden=!target.hidden;
button.setAttribute('aria-expanded',String(!target.hidden))})});
const progress=document.querySelector('[data-role="scroll-progress"]');
if(progress)document.addEventListener('scroll',()=>{
const height=document.documentElement.scrollHeight-window.innerHeight;
progress.style.width=`${height>0?Math.min(100,window.scrollY/height*100):100}%`});
if('IntersectionObserver' in window){const sections=links.map(link=>
document.querySelector(link.getAttribute('href'))).filter(Boolean);
const observer=new IntersectionObserver(entries=>{const visible=entries.filter(entry=>
entry.isIntersecting).sort((a,b)=>b.intersectionRatio-a.intersectionRatio)[0];
if(visible){const link=links[sections.indexOf(visible.target)];if(link)activate(link)}},
{rootMargin:'-20% 0px -65% 0px',threshold:[0,.25,.5]});
sections.forEach(section=>observer.observe(section))}
});</script>"""
    return re.sub(r"</body>", runtime + "</body>", document, count=1, flags=re.IGNORECASE)


def _spec(snapshot: dict[str, Any], provider_mode: str) -> dict[str, Any]:
    claims = snapshot.get("released_claims", [])
    return {
        "schema_version": "interactive-presentation-spec-v1",
        "pages": [
            {
                "page_id": "interactive-story",
                "purpose": "互动方案叙事",
                "blocks": [
                    {
                        "block_id": f"block-{index}",
                        "claim_id": item["claim_id"],
                        "text": item["text"],
                        "boundary": item["boundary"],
                        "fact_block": item["boundary"]
                        in {"historical_fact", "enterprise_capability"},
                        "locked": True,
                    }
                    for index, item in enumerate(claims, start=1)
                ],
            }
        ],
        "source_claim_ids": [item["claim_id"] for item in claims],
        "provider_mode": provider_mode,
    }


class MockInteractiveHTMLProvider:
    mode = "interactive-mock"

    async def render(
        self, snapshot: dict[str, Any], style_profile: dict[str, Any]
    ) -> dict[str, Any]:
        del style_profile
        claims = snapshot.get("released_claims", [])
        cards = "".join(
            '<article class="fact"><p data-claim-id="{}">{}</p>'
            '<small data-system-label>{}</small></article>'.format(
                html.escape(str(item["claim_id"])),
                html.escape(str(item["text"])),
                html.escape(str(item["boundary"])),
            )
            for item in claims
        )
        title = html.escape(str(snapshot.get("title", "")))
        document = "".join(
            [
                '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">',
                '<meta name="viewport" content="width=device-width,initial-scale=1">',
                f"<title data-document-title>{title}</title>",
                "<style>*{box-sizing:border-box}html{scroll-behavior:smooth}",
                "body{margin:0;font-family:Arial,sans-serif;color:#172033;background:#faf9f6}",
                "nav{position:sticky;top:0;display:flex;gap:12px;padding:16px 6vw;",
                "background:#fff;border-bottom:1px solid #dde3ee}",
                "main{max-width:1120px;margin:auto;padding:8vh 6vw}",
                "h1{font-size:clamp(36px,7vw,78px)}",
                ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));",
                "gap:18px}.fact{padding:24px;background:#fff;border-left:4px solid #3370ff;",
                "transition:transform .2s}.fact:hover{transform:translateY(-4px)}",
                "@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;",
                "transition:none!important}}</style></head><body>",
                '<nav data-role="section-nav"><a href="#overview" data-system-label>',
                "概览</a><a href=\"#facts\" data-system-label>可信事实</a>",
                '<button type="button" id="toggle" data-action="toggle" ',
                'data-target="#fact-grid" data-system-label>展开 / 收起</button></nav>',
                '<main><section id="overview"><span data-system-label>互动演示</span>',
                f"<h1 data-document-title>{title}</h1></section>",
                '<section id="facts"><h2 data-system-label>可信事实</h2>',
                f'<div class="grid" id="fact-grid">{cards}</div></section></main>',
                "</body></html>",
            ]
        )
        document = _inject_interaction_runtime(
            _inject_csp(_enforce_document_title(document, str(snapshot.get("title", ""))))
        )
        audit = audit_interactive_html(document, snapshot)
        return {
            "provider_mode": self.mode,
            "spec": _spec(snapshot, self.mode),
            "html": document,
            "css": "",
            "assets": [],
            "render_report": {
                **audit,
                "render_mode": "interactive",
                "browser_rendered": False,
                "visual_check": "not_measured",
                "passed": False,
                "reason": "mock mode validates contracts but does not call the visual model",
            },
            "status": "needs_review",
        }


class LiveInteractiveHTMLProvider:
    mode = "interactive-live"

    def __init__(self, base_url: str, api_key: str, model: str, timeout_seconds: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def render(
        self, snapshot: dict[str, Any], style_profile: dict[str, Any]
    ) -> dict[str, Any]:
        prompt_data = {
            "title": snapshot.get("title"),
            "audience": snapshot.get("audience"),
            "visual_direction": snapshot.get("visual_direction"),
            "style_profile": style_profile.get("visual_json", {}),
            "claims": snapshot.get("released_claims", []),
            "evidence": snapshot.get("evidence", []),
            "supplied_system_labels": sorted(SYSTEM_LABELS),
        }
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(prompt_data, ensure_ascii=False)},
        ]
        last_error: InteractiveHTMLValidationError | None = None
        usage: dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            for attempt in range(3):
                request_messages = list(messages)
                chunks: list[str] = []
                for _ in range(3):
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        json={
                            "model": self.model,
                            "messages": request_messages,
                            "temperature": 0.45 if attempt == 0 else 0.0,
                            "max_tokens": 8192,
                            "stream": False,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
                    choice = payload["choices"][0]
                    chunk = str(choice["message"]["content"])
                    chunks.append(chunk)
                    raw_usage = payload.get("usage") or {}
                    usage = dict(raw_usage) if isinstance(raw_usage, dict) else {}
                    if "</html>" in "".join(chunks).lower():
                        break
                    if choice.get("finish_reason") != "length":
                        break
                    request_messages.extend(
                        [
                            {"role": "assistant", "content": chunk},
                            {
                                "role": "user",
                                "content": (
                                    "输出因长度截断。请从上一个字符后原样继续，"
                                    "只输出剩余 HTML，不要重复。"
                                ),
                            },
                        ]
                    )
                raw = "".join(chunks)
                try:
                    generated = extract_html(raw)
                    if re.search(r"<script\b", generated, re.IGNORECASE):
                        raise InteractiveHTMLValidationError(
                            ["模型输出包含 script；交互必须使用平台固定运行时"]
                        )
                    document = _inject_interaction_runtime(
                        _inject_csp(
                            _enforce_document_title(
                                generated, str(snapshot.get("title", ""))
                            )
                        )
                    )
                    audit = audit_interactive_html(document, snapshot)
                    break
                except InteractiveHTMLValidationError as exc:
                    last_error = exc
                    messages.extend(
                        [
                            {"role": "assistant", "content": raw},
                            {
                                "role": "user",
                                "content": (
                                    "上一版未通过契约："
                                    + "；".join(exc.violations)
                                    + "。请从零重新输出完整 HTML。CSS 中禁止 url(、@import、"
                                    "expression( 和 content:；不要使用伪元素生成文字或图标。"
                                    "每个 data-system-label 元素必须包含 supplied_system_labels "
                                    "中的一条非空可见文字；装饰图形不得带任何文字绑定属性。"
                                    "不要输出 script、外链、表单或未绑定可见文字。"
                                ),
                            },
                        ]
                    )
            else:
                raise last_error or InteractiveHTMLValidationError(["互动 HTML 生成失败"])
        return {
            "provider_mode": self.mode,
            "spec": _spec(snapshot, self.mode),
            "html": document,
            "css": "",
            "assets": [],
            "render_report": {
                **audit,
                "render_mode": "interactive",
                "browser_rendered": False,
                "visual_check": "static_contract_only",
                "model": self.model,
                "usage": usage,
                "passed": True,
            },
            "status": "ready",
        }
