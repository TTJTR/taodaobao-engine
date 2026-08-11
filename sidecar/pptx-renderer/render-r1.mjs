import process from "node:process";
import fs from "node:fs/promises";
import pptxgen from "pptxgenjs";

const W=13.333,H=7.5, sx=x=>x/1600*W, sy=y=>y/900*H, hex=x=>x.replace("#","");
let raw;
if (process.argv[3]) raw = await fs.readFile(process.argv[3], "utf8");
else { const chunks=[]; for await (const chunk of process.stdin) chunks.push(chunk); raw=Buffer.concat(chunks).toString("utf8"); }
const data=JSON.parse(raw.replace(/^\uFEFF/, ""));
const pptx=new pptxgen(); pptx.layout="LAYOUT_WIDE"; pptx.author="神州数码 AI for Process"; pptx.lang="zh-CN";
const c=data.colors, font="Noto Sans SC";
function box(b){return{x:sx(b.x),y:sy(b.y),w:sx(b.width),h:sy(b.height)}}
function text(slide,value,b,size,opt={}){slide.addText(String(value??""),{...box(b),fontFace:font,fontSize:size,color:hex(opt.color||c.ink),bold:opt.bold||false,margin:0,breakLine:false,fit:"shrink",valign:opt.valign||"mid",...opt})}
for(const [idx,s] of data.slides.entries()){
 const slide=pptx.addSlide(); const dark=s.kind==="closing"; slide.background={color:hex(dark?c.ink:c.paper)};
 slide.addShape(pptx.ShapeType.rect,{x:0,y:0,w:sx(dark?360:18),h:H,line:{transparency:100},fill:{color:hex(c.accent)}});
 const g=data.geometry[s.variant];
 if(s.kind==="cover"){
  slide.addShape(pptx.ShapeType.rect,{x:sx(930),y:sy(64),w:sx(600),h:sy(772),line:{transparency:100},fill:{color:hex(c.ink)}});
  slide.addShape(pptx.ShapeType.ellipse,{x:sx(1190),y:sy(-80),w:sx(520),h:sy(520),line:{color:hex(c.accent),width:36},fill:{transparency:100}});
  text(slide,"需求\n识别真实流程",{x:994,y:160,width:260,height:100},18,{color:"#FFFFFF",bold:true});
  text(slide,"事实\n绑定可信证据",{x:1180,y:370,width:260,height:100},18,{color:"#FFFFFF",bold:true});
  text(slide,"演示\n交付可见价值",{x:1020,y:650,width:260,height:100},18,{color:"#FFFFFF",bold:true});
 }
 if(s.kind==="section") text(slide,"01",{x:1130,y:460,width:400,height:350},210,{color:c.tint});
 if(s.kind==="key_message"){
  slide.addShape(pptx.ShapeType.rect,{x:sx(1180),y:0,w:sx(420),h:H,line:{transparency:100},fill:{color:hex(c.tint)}});
  slide.addShape(pptx.ShapeType.ellipse,{x:sx(1260),y:sy(250),w:sx(260),h:sy(260),line:{color:hex(c.accent),width:25},fill:{transparency:100}});
  text(slide,"可信",{x:1300,y:335,width:180,height:80},28,{bold:true,align:"center"});
 }
 if(s.kind==="closing") slide.addShape(pptx.ShapeType.ellipse,{x:sx(1210),y:sy(120),w:sx(760),h:sy(760),line:{color:hex(c.accent),width:45,transparency:12},fill:{transparency:100}});
 if(s.kind==="process"){
  text(slide,s.title,g.title,31,{bold:true}); const gap=26,cw=(g.steps.width-gap*3)/4;
  s.steps.forEach((step,j)=>{const y=g.steps.y+(j%2?54:0),b={x:g.steps.x+j*(cw+gap),y,width:cw,height:g.steps.height-54};slide.addShape(pptx.ShapeType.rect,{...box(b),line:{color:"D0D5DD",width:1},fill:{color:"FFFFFF"}});if(j<3)slide.addShape(pptx.ShapeType.line,{x:sx(b.x+b.width),y:sy(b.y+b.height/2),w:sx(gap),h:0,line:{color:hex(c.accent),width:2}});text(slide,`0${j+1}`,{x:b.x+b.width-75,y:b.y+14,width:52,height:28},12,{color:c.accent,align:"right"});text(slide,step.title,{x:b.x+28,y:b.y+52,width:b.width-56,height:62},22,{bold:true});text(slide,step.body,{x:b.x+28,y:b.y+132,width:b.width-56,height:160},16,{color:c.muted,valign:"top"})});
 } else {
  for(const [name,b] of Object.entries(g)){if(s[name]==null)continue;let bb=b,size=14,opt={color:dark?"#FFFFFF":c.ink};if(name==="title"){size=s.kind==="cover"?42:s.kind==="closing"?43:31;opt.bold=true;if(s.kind==="cover")bb={...b,width:760}}else if(["subtitle","lead"].includes(name)){size=21;opt.color=dark?"#D0D5DD":c.muted;if(s.kind==="cover")bb={...b,width:700}}else if(name==="statement"){size=29;opt.bold=true;bb={...b,width:900}}else if(name==="support"||name==="action"){size=name==="action"?24:17;opt.color=dark?"#D0D5DD":c.muted}else if(name==="verbatim"){size=27;opt.bold=true;slide.addShape(pptx.ShapeType.rect,{x:sx(b.x+20),y:sy(b.y+20),w:sx(b.width),h:sy(b.height),line:{transparency:100},fill:{color:hex(c.tint)}});slide.addShape(pptx.ShapeType.rect,{...box(b),line:{transparency:100},fill:{color:"FFFFFF"}});slide.addShape(pptx.ShapeType.rect,{x:sx(b.x),y:sy(b.y),w:0.09,h:sy(b.height),line:{transparency:100},fill:{color:hex(c.accent)}});bb={x:b.x+48,y:b.y+25,width:b.width-96,height:b.height-50}}else if(name==="evidence_heading"){size=20;opt.color=c.accent;opt.bold=true}else{opt.color=dark?"#D0D5DD":c.muted}text(slide,s[name],bb,size,opt)}
  if(s.kind==="evidence"){slide.addShape(pptx.ShapeType.rect,{x:sx(1184),y:sy(690),w:sx(330),h:sy(130),line:{transparency:100},fill:{color:hex(c.ink)}});text(slide,"可追溯\n来源 · 版本 · 审核状态",{x:1212,y:710,width:274,height:86},16,{color:"#FFFFFF",bold:true})}
 }
 text(slide,`${String(idx+1).padStart(2,"0")} / ${String(data.slides.length).padStart(2,"0")}`,{x:1390,y:790,width:110,height:26},10,{color:"#98A2B3",align:"right"});
}
await pptx.writeFile({fileName:process.argv[2],compression:true});
process.stdout.write(JSON.stringify({slides:data.slides.length,output:process.argv[2]}));
