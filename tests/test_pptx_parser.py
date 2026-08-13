import io
import zipfile

import pytest

from app.services.pptx_parser import PPTXParseError, SafePPTXParser

PRESENTATION_RELS = b"""<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster"
    Target="slideMasters/slideMaster1.xml"/>
</Relationships>"""

PRESENTATION = b"""<?xml version="1.0"?>
<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
 <p:sldSz cx="12192000" cy="6858000"/>
</p:presentation>"""

MASTER_RELS = b"""<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
    Target="../theme/theme1.xml"/>
  <Relationship Id="rId2"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"
    Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>"""

SLIDE_MASTER = b"""<?xml version="1.0"?>
<p:sldMaster xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree><p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
  </p:sp></p:spTree></p:cSld>
  <p:clrMap accent1="accent2" accent2="accent1" bg1="lt1" tx1="dk1"/>
  <p:txStyles>
   <p:titleStyle><a:lvl1pPr><a:defRPr sz="3600"/></a:lvl1pPr></p:titleStyle>
   <p:bodyStyle><a:lvl1pPr><a:defRPr sz="1800"/></a:lvl1pPr></p:bodyStyle>
  </p:txStyles>
</p:sldMaster>"""

SLIDE_LAYOUT = b"""<?xml version="1.0"?>
<p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <p:cSld name="Title and Content"><p:spTree><p:sp>
  <p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>
  <p:spPr><a:xfrm><a:off x="952500" y="1905000"/><a:ext cx="7620000" cy="3810000"/>
  </a:xfrm></p:spPr>
 </p:sp></p:spTree></p:cSld>
</p:sldLayout>"""

THEME = b"""<?xml version="1.0"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
 <a:themeElements>
  <a:clrScheme name="Corporate">
   <a:dk1><a:sysClr val="windowText" lastClr="102030"/></a:dk1>
   <a:lt1><a:sysClr val="window" lastClr="F8FAFC"/></a:lt1>
   <a:accent1><a:srgbClr val="E11D48"/></a:accent1>
   <a:accent2><a:srgbClr val="075985"/></a:accent2>
   <a:accent3><a:srgbClr val="808080"><a:lumMod val="50000"/></a:srgbClr></a:accent3>
  </a:clrScheme>
  <a:fontScheme name="Corporate Fonts">
   <a:majorFont><a:latin typeface="Arial"/><a:ea typeface=""/>
    <a:font script="Hans" typeface="Microsoft YaHei"/></a:majorFont>
   <a:minorFont><a:latin typeface="Georgia"/><a:ea typeface="Microsoft YaHei"/></a:minorFont>
  </a:fontScheme>
 </a:themeElements>
</a:theme>"""

SENSITIVE_SLIDE = """<?xml version="1.0"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart">
 <p:cSld><p:spTree>
  <p:sp><p:nvSpPr><p:cNvPr id="1" name="safe-shape"/></p:nvSpPr><p:spPr>
   <a:xfrm><a:off x="609600" y="342900"/><a:ext cx="10972800" cy="914400"/></a:xfrm>
   <a:solidFill><a:srgbClr val="123456"/></a:solidFill>
  </p:spPr><p:txBody><a:p><a:r><a:rPr sz="3200"/><a:t>绝密客户名称</a:t></a:r></a:p>
  </p:txBody></p:sp>
  <p:sp><p:spPr><a:xfrm><a:off x="1219200" y="2057400"/>
   <a:ext cx="4267200" cy="2743200"/></a:xfrm><a:solidFill><a:srgbClr val="123456"/>
   </a:solidFill></p:spPr><p:txBody><a:p><a:r><a:rPr sz="1800"/>
   <a:t>机密财务数字 987654321</a:t></a:r></a:p></p:txBody></p:sp>
  <p:sp><p:spPr><a:xfrm><a:off x="6705600" y="2057400"/>
   <a:ext cx="4267200" cy="2743200"/></a:xfrm><a:solidFill><a:srgbClr val="123456"/>
   </a:solidFill></p:spPr></p:sp>
  <p:graphicFrame><p:xfrm><a:off x="0" y="0"/><a:ext cx="100" cy="100"/></p:xfrm>
   <a:graphic><c:chartSpace><c:pt idx="0"><c:v>88888888</c:v></c:pt></c:chartSpace></a:graphic>
  </p:graphicFrame>
  <p:oleObj name="hidden-secret"><a:t>OLE_SECRET</a:t></p:oleObj>
 </p:spTree></p:cSld>
</p:sld>""".encode()


def _pptx(*, theme_target: str = "../theme/theme1.xml") -> io.BytesIO:
    stream = io.BytesIO()
    master_rels = MASTER_RELS.replace(b"../theme/theme1.xml", theme_target.encode())
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("ppt/_rels/presentation.xml.rels", PRESENTATION_RELS)
        archive.writestr("ppt/presentation.xml", PRESENTATION)
        archive.writestr("ppt/slideMasters/slideMaster1.xml", SLIDE_MASTER)
        archive.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", master_rels)
        archive.writestr("ppt/slideLayouts/slideLayout1.xml", SLIDE_LAYOUT)
        archive.writestr("ppt/theme/theme1.xml", THEME)
        archive.writestr(
            "ppt/slides/slide1.xml",
            SENSITIVE_SLIDE,
        )
        archive.writestr("ppt/slides/slide2.xml", SENSITIVE_SLIDE)
    stream.seek(0)
    return stream


def test_parser_follows_relationships_and_maps_theme_without_reading_slides() -> None:
    profile = SafePPTXParser().parse(_pptx())

    assert profile.palette.primary == "#075985"
    assert profile.palette.secondary == "#E11D48"
    assert profile.palette.accent == "#404040"
    assert profile.palette.background == "#F8FAFC"
    assert profile.palette.foreground == "#102030"
    assert profile.typography.heading_font == "Microsoft YaHei"
    assert profile.typography.body_font == "Microsoft YaHei"
    assert profile.typography.base_size_px == 24
    assert profile.typography.scale_ratio == 2
    assert profile.layout_grammar == ["layout:Title and Content|body@100,200,800,400"]
    assert "SECRET" not in profile.model_dump_json()
    assert "99999999" not in profile.model_dump_json()


def test_parser_rejects_relationships_outside_the_theme_directory() -> None:
    with pytest.raises(PPTXParseError, match="outside ppt/theme"):
        SafePPTXParser().parse(_pptx(theme_target="../../ppt/slides/slide1.xml"))


def test_sanitized_visual_extracts_patterns_without_leaking_business_data() -> None:
    profile = SafePPTXParser().parse(_pptx(), mode="sanitized_visual")
    serialized = profile.model_dump_json()

    assert profile.palette.primary == "#123456"
    assert profile.typography.base_size_px == 24
    assert profile.typography.scale_ratio == pytest.approx(1.778)
    assert profile.layout_grammar[0] == (
        "visual-pattern:title-band+two-column+graphic-focus:count=2"
    )
    for secret in ("绝密客户名称", "机密财务数字", "987654321", "88888888", "OLE_SECRET"):
        assert secret not in serialized


def test_parser_builds_strongly_typed_sanitized_feature_set() -> None:
    features = SafePPTXParser().extract_features(_pptx(), mode="sanitized_visual")
    serialized = features.model_dump_json()

    assert features.schema_version == "style-feature-set-v1"
    assert features.canvas.aspect_ratio == pytest.approx(16 / 9)
    assert len(features.page_samples) == 2
    assert features.page_samples[0].column_count == 2
    assert features.master_layouts[0].name == "Title and Content"
    assert features.master_layouts[0].placeholders[0].role == "body"
    for secret in ("缁濆瘑瀹㈡埛鍚嶇О", "987654321", "88888888", "OLE_SECRET"):
        assert secret not in serialized


def test_sanitized_visual_rejects_xml_entity_expansion() -> None:
    stream = _pptx()
    malicious = b'<!DOCTYPE x [<!ENTITY secret "LEAK">]><p:sld xmlns:p="p">&secret;</p:sld>'
    rewritten = io.BytesIO()
    with zipfile.ZipFile(stream) as source, zipfile.ZipFile(rewritten, "w") as target:
        for item in source.infolist():
            content = malicious if item.filename == "ppt/slides/slide1.xml" else source.read(item)
            target.writestr(item, content)
    rewritten.seek(0)

    with pytest.raises(PPTXParseError, match="sanitized visual parsing"):
        SafePPTXParser().parse(rewritten, mode="sanitized_visual")


def test_parser_enforces_zip_bomb_entry_limit() -> None:
    with pytest.raises(PPTXParseError, match="too many parts"):
        SafePPTXParser(max_entries=3).parse(_pptx())
