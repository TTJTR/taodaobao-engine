import fs from "node:fs/promises";
import process from "node:process";
import pptxgen from "pptxgenjs";

const WIDTH = 13.333;
const HEIGHT = 7.5;
const MAX_INPUT_BYTES = 8 * 1024 * 1024;

function fail(message) {
  throw new Error(`PPTX_RENDER_INVALID_INPUT: ${message}`);
}

function color(value) {
  if (!/^#[0-9a-fA-F]{6}$/.test(value)) fail("invalid color token");
  return value.slice(1).toUpperCase();
}

function box(geometry) {
  const values = [geometry.x, geometry.y, geometry.width, geometry.height];
  if (!values.every(Number.isInteger)) fail("geometry must use integer logical units");
  if (geometry.x < 0 || geometry.y < 0 || geometry.width <= 0 || geometry.height <= 0 ||
      geometry.x + geometry.width > 10000 || geometry.y + geometry.height > 10000) {
    fail("geometry is outside the logical canvas");
  }
  return {
    x: geometry.x / 10000 * WIDTH,
    y: geometry.y / 10000 * HEIGHT,
    w: geometry.width / 10000 * WIDTH,
    h: geometry.height / 10000 * HEIGHT,
  };
}

function textOptions(positioned, style, overrides = {}) {
  const token = positioned.text_style_token;
  const base = style.typography.base_size_px;
  const sizes = {
    display: Math.max(24, Math.round(base * style.typography.scale_ratio * 1.45)),
    heading: Math.max(18, Math.round(base * style.typography.scale_ratio)),
    body: Math.max(12, Math.round(base * 0.82)),
    evidence: Math.max(10, Math.round(base * 0.68)),
  };
  if (!(token in sizes)) fail(`unknown text style token: ${token}`);
  return {
    ...box(positioned.geometry),
    fontFace: token === "display" || token === "heading"
      ? style.typography.heading_font : style.typography.body_font,
    fontSize: sizes[token],
    color: color(style.palette.foreground),
    margin: 0.08,
    breakLine: false,
    fit: "shrink",
    valign: "mid",
    ...overrides,
  };
}

function traceOptions(component) {
  const binding = component.fact_binding;
  return binding ? { objectName: `claim-${binding.claim_id}` } : {};
}

function renderBoundItems(slide, positioned, heading, items, style, formatter) {
  const lines = [heading, ...items.map(formatter)];
  slide.addText(lines.join("\n"), textOptions(positioned, style, {
    bullet: false,
    breakLine: true,
    ...traceOptions(items[0] || {}),
  }));
}

function renderComponent(slide, positioned, style) {
  const component = positioned.component;
  const common = textOptions(positioned, style, traceOptions(component));
  switch (component.component_type) {
    case "title":
      slide.addText(component.text, { ...common, bold: true, color: color(style.palette.primary) });
      break;
    case "key_message":
      slide.addText(component.text, common);
      break;
    case "evidence_card":
      slide.addShape("roundRect", { ...box(positioned.geometry), rectRadius: 0.06,
        fill: { color: color(style.palette.background), transparency: 3 },
        line: { color: color(style.palette.secondary), transparency: 35 } });
      slide.addText(`${component.heading}\n${component.body}`, { ...common, bold: false });
      break;
    case "metric":
      slide.addText(`${component.value}\n${component.label}`, {
        ...common, bold: true, color: color(style.palette.accent), align: "center",
      });
      break;
    case "comparison":
      renderBoundItems(slide, positioned, component.heading, [component.left, component.right],
        style, (item) => `${item.label}: ${item.text}`);
      break;
    case "timeline":
      renderBoundItems(slide, positioned, component.heading, component.items, style,
        (item) => `${item.label}: ${item.text}`);
      break;
    case "process":
      renderBoundItems(slide, positioned, component.heading, component.steps, style,
        (item, index) => `${index + 1}. ${item.label}: ${item.text}`);
      break;
    case "source_list":
      renderBoundItems(slide, positioned, component.heading, component.sources, style,
        (item) => item.label);
      break;
    default:
      fail(`unknown component type: ${component.component_type}`);
  }
}

async function readInput() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_INPUT_BYTES) fail("input exceeds maximum size");
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

async function main() {
  const outputPath = process.argv[2];
  if (!outputPath) fail("output path is required");
  const payload = await readInput();
  if (payload.spec?.schema_version !== "positioned-spec-v1") fail("unsupported spec version");
  if (!Array.isArray(payload.spec.slides) || payload.spec.slides.length === 0) fail("slides required");

  const pptx = new pptxgen();
  pptx.layout = "LAYOUT_WIDE";
  pptx.author = "Xianjintuan Engine";
  pptx.subject = `Validated presentation ${payload.spec.presentation_id}`;
  pptx.title = "AI for Process Presentation";
  pptx.company = "Digital China";
  pptx.lang = "zh-CN";
  pptx.theme = {
    headFontFace: payload.style.typography.heading_font,
    bodyFontFace: payload.style.typography.body_font,
    lang: "zh-CN",
  };

  for (const sourceSlide of payload.spec.slides) {
    const slide = pptx.addSlide();
    slide.background = { color: color(payload.style.palette.background) };
    for (const positioned of sourceSlide.components) renderComponent(slide, positioned, payload.style);
    slide.addNotes(`slide_id=${sourceSlide.slide_id};spec=${payload.spec.schema_version}`);
  }
  await pptx.writeFile({ fileName: outputPath, compression: true });
  const stats = await fs.stat(outputPath);
  process.stdout.write(JSON.stringify({ slides: payload.spec.slides.length, bytes: stats.size }));
}

main().catch((error) => {
  process.stderr.write(`${error?.message || "PPTX render failed"}\n`);
  process.exitCode = 1;
});
