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

MASTER_RELS = b"""<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
    Target="../theme/theme1.xml"/>
</Relationships>"""

SLIDE_MASTER = b"""<?xml version="1.0"?>
<p:sldMaster xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree><p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
  </p:sp></p:spTree></p:cSld>
  <p:clrMap accent1="accent2" accent2="accent1" bg1="lt1" tx1="dk1"/>
</p:sldMaster>"""

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
   <a:majorFont><a:latin typeface="Arial"/></a:majorFont>
   <a:minorFont><a:latin typeface="Georgia"/></a:minorFont>
  </a:fontScheme>
 </a:themeElements>
</a:theme>"""


def _pptx(*, theme_target: str = "../theme/theme1.xml") -> io.BytesIO:
    stream = io.BytesIO()
    master_rels = MASTER_RELS.replace(b"../theme/theme1.xml", theme_target.encode())
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("ppt/_rels/presentation.xml.rels", PRESENTATION_RELS)
        archive.writestr("ppt/slideMasters/slideMaster1.xml", SLIDE_MASTER)
        archive.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", master_rels)
        archive.writestr("ppt/theme/theme1.xml", THEME)
        archive.writestr(
            "ppt/slides/slide1.xml",
            b"<not-even-valid-xml>SECRET CUSTOMER 99999999 & leaked fact",
        )
    stream.seek(0)
    return stream


def test_parser_follows_relationships_and_maps_theme_without_reading_slides() -> None:
    profile = SafePPTXParser().parse(_pptx())

    assert profile.palette.primary == "#075985"
    assert profile.palette.secondary == "#E11D48"
    assert profile.palette.accent == "#404040"
    assert profile.palette.background == "#F8FAFC"
    assert profile.palette.foreground == "#102030"
    assert profile.typography.heading_font == "Inter"
    assert profile.typography.body_font == "Source Serif Pro"
    assert profile.layout_grammar == ["master-placeholder:title"]
    assert "SECRET" not in profile.model_dump_json()
    assert "99999999" not in profile.model_dump_json()


def test_parser_rejects_relationships_outside_the_theme_directory() -> None:
    with pytest.raises(PPTXParseError, match="outside ppt/theme"):
        SafePPTXParser().parse(_pptx(theme_target="../../ppt/slides/slide1.xml"))
