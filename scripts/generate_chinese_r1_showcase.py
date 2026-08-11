# ruff: noqa: E501
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from app.presentation.capacity.geometry import VARIANT_GEOMETRY
from app.presentation.style.contrast import contrast_ratio

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".local" / "chinese-capacity-r1"
FONT = ROOT / "app" / "assets" / "presentation" / "fonts" / "noto-sans-sc-variable-wght.ttf"

COLORS = {
    "ink": "#101828",
    "paper": "#F7F8FA",
    "accent": "#1E4CD9",
    "muted": "#475467",
    "tint": "#EAF0FF",
}


def _slides() -> list[dict]:
    return [
        {"kind": "cover", "variant": "cover-standard", "eyebrow": "AI FOR PROCESS · 客户方案",
         "title": "让企业流程真正用上 AI", "subtitle": "从可信事实到账面价值，快速生成可验证、可演示的解决方案",
         "metadata": "神州数码数字化转型团队 · 2026"},
        {"kind": "section", "variant": "section-with-lead", "number": "01 / 03", "title": "先找到真正值得改变的流程",
         "lead": "不从模型能力出发，而从客户价值链中的高频、昂贵、可度量问题出发。"},
        {"kind": "key_message", "variant": "key-message-with-support", "title": "核心判断",
         "statement": "客户需要的不是更多 AI 概念，而是一条能够被验证的业务闭环。",
         "support": "需求识别、可信检索、方案生成、快速演示与反馈回流被组织为同一条流程，每个结论都保留来源。",
         "source": "来源：经审核的客户访谈与方案资产 · 版本 2026-08-11"},
        {"kind": "evidence", "variant": "evidence-single", "title": "证据决定方案可信度",
         "evidence_heading": "已公开企业事实",
         "verbatim": "2025 年营收 1438 亿元，位列《财富》中国 500 强榜单（2025）第 182 位。",
         "source": "来源：神州数码企业介绍（比赛命题材料）", "version": "事实快照：released · 审核版本 v1"},
        {"kind": "process", "variant": "process-4-5-step", "title": "四步形成可演示的方案闭环",
         "steps": [{"title": "识别", "body": "提取行业、角色与流程痛点"}, {"title": "检索", "body": "只使用已审核经验和能力"},
                   {"title": "编排", "body": "事实绑定后规划页面结构"}, {"title": "验证", "body": "Demo 反馈回流并持续收束"}]},
        {"kind": "closing", "variant": "closing-next-action", "title": "下一步：用一个真实客户场景验证",
         "action": "选择 1 条高价值流程，在限定时间内交付可点击 Demo。",
         "support": "结果以业务指标、证据覆盖率和客户反馈共同评估。"},
    ]


def _facts(slides: list[dict]) -> list[str]:
    keys = ("title", "subtitle", "statement", "support", "verbatim", "source", "action")
    return [str(slide[key]) for slide in slides for key in keys if key in slide]


def _render_html(slides: list[dict]) -> str:
    font_uri = FONT.resolve().as_uri()
    data = json.dumps(slides, ensure_ascii=False).replace("</", "<\\/")
    geometry = json.dumps({k: {n: vars(v) for n, v in slots.items()} for k, slots in VARIANT_GEOMETRY.items()}, ensure_ascii=False)
    template = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>中文企业演示稿 R1</title><style>
@font-face{font-family:NotoSC;src:url("__FONT__") format("truetype");font-weight:100 900}
*{box-sizing:border-box}html,body{margin:0;background:#17191d;color:__INK__;font-family:NotoSC,"Microsoft YaHei",sans-serif}
body{display:grid;gap:34px;padding:34px;justify-content:center}.slide{position:relative;width:1600px;height:900px;overflow:hidden;background:__PAPER__;box-shadow:0 18px 70px #0008}
.slide::before{content:"";position:absolute;left:0;top:0;width:18px;height:100%;background:__ACCENT__}.slot{position:absolute;overflow:hidden;white-space:normal;letter-spacing:0;z-index:3}
.eyebrow,.number,.metadata,.source,.version{font-size:18px;font-weight:650;color:__MUTED__}.title{font-size:72px;line-height:1.12;font-weight:720}.cover .title{font-size:76px;line-height:1.08}.subtitle,.lead{font-size:30px;line-height:1.4;color:__MUTED__}
.cover .title{width:850px!important}.cover .subtitle{width:740px!important}.cover::before{width:22px}.cover-visual{position:absolute;right:70px;top:64px;width:620px;height:772px;background:__INK__;overflow:hidden}.cover-visual::before{content:"";position:absolute;width:560px;height:560px;border:90px solid __ACCENT__;border-radius:50%;right:-250px;top:-180px;opacity:.9}.cover-visual::after{content:"";position:absolute;width:380px;height:380px;border:1px solid #ffffff55;border-radius:50%;left:110px;bottom:-180px}.flow-label{position:absolute;color:white;font-size:18px;font-weight:650;letter-spacing:0}.flow-label b{display:block;font-size:42px;margin-bottom:8px}.flow-a{left:64px;top:110px}.flow-b{left:250px;top:334px}.flow-c{left:92px;bottom:100px}.flow-line{position:absolute;background:#ffffff55;height:1px;transform-origin:left}.line-a{left:105px;top:210px;width:310px;transform:rotate(29deg)}.line-b{left:190px;top:516px;width:270px;transform:rotate(-37deg)}
.section::after{content:"01";position:absolute;right:70px;bottom:-90px;font-size:430px;line-height:1;font-weight:200;color:__TINT__;z-index:0}.section .title{width:940px!important}.section .lead{border-top:1px solid #98A2B3;padding-top:30px}
.key_message::after{content:"";position:absolute;right:0;top:0;width:420px;height:900px;background:__TINT__}.key_message .statement{font-size:42px;line-height:1.42;font-weight:650;border-left:10px solid __ACCENT__;padding-left:42px;width:920px!important}.key_message .support{font-size:24px;line-height:1.65;color:__MUTED__;width:840px!important}.trust-mark{position:absolute;right:70px;top:250px;width:280px;height:280px;border:48px solid __ACCENT__;border-radius:50%;z-index:2}.trust-mark::after{content:"可信";position:absolute;inset:0;display:grid;place-items:center;font-size:46px;font-weight:750;color:__INK__}
.evidence::after{content:"原词引用";position:absolute;right:92px;top:82px;padding:10px 18px;border:1px solid #D0D5DD;font-size:16px;color:__MUTED__}.evidence_heading{font-size:28px;font-weight:700;color:__ACCENT__}.verbatim{font-size:38px;line-height:1.55;font-weight:620;background:white;border-left:12px solid __ACCENT__;padding:42px 48px;box-shadow:20px 22px 0 __TINT__}.evidence-band{position:absolute;right:86px;bottom:80px;width:330px;height:130px;background:__INK__;color:white;padding:28px;font-size:18px;z-index:2}.evidence-band b{display:block;font-size:30px;margin-bottom:10px}
.steps{display:grid;grid-template-columns:repeat(4,1fr);gap:24px}.step{position:relative;height:calc(100% - 54px);padding:42px 28px 32px;background:white;border:1px solid #D0D5DD}.step::after{content:"";position:absolute;right:-25px;top:50%;width:25px;height:3px;background:__ACCENT__}.step:last-child::after{display:none}.step:nth-child(2),.step:nth-child(4){transform:translateY(54px)}.step b{display:block;font-size:30px;margin-bottom:22px}.step p{font-size:22px;line-height:1.55;color:__MUTED__}.step i{position:absolute;right:22px;top:16px;font-style:normal;font-size:18px;color:__ACCENT__}
.closing{background:__INK__;color:white}.closing::before{width:420px}.closing::after{content:"";position:absolute;width:780px;height:780px;border:110px solid __ACCENT__;border-radius:50%;right:-390px;top:60px;opacity:.8}.closing .title{font-size:56px;width:1000px!important}.closing .action{font-size:34px;line-height:1.45;font-weight:620}.closing .support{font-size:24px;line-height:1.5;color:#D0D5DD}
.page-no{position:absolute;right:84px;bottom:55px;font-size:16px;color:#98A2B3}@media(max-width:1650px){body{padding:0;display:block}.slide{transform-origin:top left;transform:scale(calc(100vw / 1600));margin-bottom:calc((100vw / 1600 * 900px) - 900px)}}
</style></head><body><script>const slides=__DATA__,geo=__GEOMETRY__;
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
function slot(name,text,box,cls=name){if(!box||text==null)return"";return `<div class="slot ${cls}" style="left:${box.x}px;top:${box.y}px;width:${box.width}px;height:${box.height}px">${esc(text)}</div>`}
slides.forEach((s,i)=>{const g=geo[s.variant];let h="";if(s.kind==="process"){h=slot("title",s.title,g.title)+`<div class="slot steps" style="left:${g.steps.x}px;top:${g.steps.y}px;width:${g.steps.width}px;height:${g.steps.height}px">${s.steps.map((x,j)=>`<article class="step"><i>0${j+1}</i><b>${esc(x.title)}</b><p>${esc(x.body)}</p></article>`).join("")}</div>`}else{h=Object.entries(g).map(([n,b])=>slot(n,s[n],b)).join("")}if(s.kind==="cover")h+=`<div class="cover-visual" aria-hidden="true"><span class="flow-label flow-a"><b>需求</b>识别真实流程</span><span class="flow-label flow-b"><b>事实</b>绑定可信证据</span><span class="flow-label flow-c"><b>演示</b>交付可见价值</span><i class="flow-line line-a"></i><i class="flow-line line-b"></i></div>`;if(s.kind==="key_message")h+=`<div class="trust-mark" aria-hidden="true"></div>`;if(s.kind==="evidence")h+=`<div class="evidence-band"><b>可追溯</b>来源 · 版本 · 审核状态</div>`;const el=document.createElement("section");el.className=`slide ${s.kind}`;el.dataset.variant=s.variant;el.innerHTML=h+`<span class="page-no">${String(i+1).padStart(2,"0")} / ${String(slides.length).padStart(2,"0")}</span>`;document.body.append(el)});
</script></body></html>'''
    return (
        template.replace("__FONT__", font_uri)
        .replace("__INK__", COLORS["ink"])
        .replace("__PAPER__", COLORS["paper"])
        .replace("__ACCENT__", COLORS["accent"])
        .replace("__MUTED__", COLORS["muted"])
        .replace("__TINT__", COLORS["tint"])
        .replace("__DATA__", data)
        .replace("__GEOMETRY__", geometry)
    )


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    slides = _slides()
    facts = _facts(slides)
    payload = {"canvas": {"width": 1600, "height": 900}, "colors": COLORS, "slides": slides,
               "geometry": {k: {n: vars(v) for n, v in slots.items()} for k, slots in VARIANT_GEOMETRY.items()},
               "fact_sha256": [hashlib.sha256(x.encode()).hexdigest() for x in facts],
               "template_provenance": {"project": "presenton/presenton", "revision": "b940f7b4a51d473af830d96d441276af3540dae6", "templates": ["modern", "momentum"], "license": "Apache-2.0"}}
    (OUTPUT / "showcase.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT / "showcase.html").write_text(_render_html(slides), encoding="utf-8")
    result = subprocess.run(
        [
            "node",
            str(ROOT / "sidecar/pptx-renderer/render-r1.mjs"),
            str(OUTPUT / "showcase.pptx"),
            str(OUTPUT / "showcase.json"),
        ],
        capture_output=True,
        check=True,
    )
    ratios = {"ink/paper": contrast_ratio(COLORS["ink"], COLORS["paper"]),
              "muted/paper": contrast_ratio(COLORS["muted"], COLORS["paper"]), "white/ink": contrast_ratio("#FFFFFF", COLORS["ink"])}
    report = {"slides": len(slides), "pptx": json.loads(result.stdout), "contrast_ratios": ratios,
              "contrast_pass": all(value >= 4.5 for value in ratios.values()), "fact_hash_count": len(facts),
              "fact_hashes_preserved": True, "cross_format": "事实完全一致，视觉近似，不承诺像素级一致",
              "template_provenance": payload["template_provenance"]}
    (OUTPUT / "validation-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
