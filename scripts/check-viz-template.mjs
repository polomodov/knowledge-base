import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

const root = process.cwd();
const templatePath = path.resolve(
  root,
  process.argv[2] ?? "src/knowledge_base/templates/viz_template.html",
);
const template = readFileSync(templatePath, "utf8");
const errors = [];

if (!template.slice(0, 1024).toLowerCase().includes('<meta charset="utf-8">')) {
  errors.push("UTF-8 meta tag must appear in the first 1024 characters");
}
if (!template.includes('type="application/json" id="kb-data">__KB_DATA__</script>')) {
  errors.push("kb-data application/json placeholder is missing");
}
if (
  !template.includes(
    '<meta http-equiv="Content-Security-Policy" content="default-src \'none\';',
  )
) {
  errors.push("restrictive Content-Security-Policy meta tag is missing");
}

const forbidden = [
  [/\bfetch\s*\(/, "fetch() is not allowed in the offline template"],
  [/\blocalStorage\b/, "localStorage is not allowed in the offline template"],
  [/<script\b[^>]*\btype\s*=\s*["']module["']/i, "module scripts are not allowed"],
  [/<script\b[^>]*\bsrc\s*=/i, "external scripts are not allowed"],
  [/<link\b[^>]*\brel\s*=\s*["']stylesheet["']/i, "external stylesheets are not allowed"],
  [/\binnerHTML\b/, "dynamic HTML insertion is not allowed"],
  [/\bouterHTML\b/, "dynamic HTML replacement is not allowed"],
  [/\binsertAdjacentHTML\b/, "dynamic HTML insertion is not allowed"],
  [/\bdocument\s*\.\s*write(?:ln)?\s*\(/, "document.write() is not allowed"],
  [/@import\b/i, "CSS imports are not allowed"],
  [/\burl\s*\(/, "CSS URL resources are not allowed"],
  [/<(?:img|iframe|object|embed|audio|video|source|track|base|form)\b/i, "network-capable markup is not allowed"],
];
for (const [pattern, message] of forbidden) {
  if (pattern.test(template)) {
    errors.push(message);
  }
}

const scriptPattern = /<script\b([^>]*)>([\s\S]*?)<\/script>/gi;
const classicScripts = [];
let match;
while ((match = scriptPattern.exec(template)) !== null) {
  const attributes = match[1];
  const typeMatch = attributes.match(/\btype\s*=\s*["']([^"']+)["']/i);
  const type = typeMatch ? typeMatch[1].toLowerCase() : "";
  if (type === "" || type === "text/javascript" || type === "application/javascript") {
    classicScripts.push(match[2]);
  }
}

if (classicScripts.length === 0) {
  errors.push("no classic inline scripts found");
}

classicScripts.forEach((source, index) => {
  const check = spawnSync(process.execPath, ["--check", "-"], {
    encoding: "utf8",
    input: source,
  });
  if (check.status !== 0) {
    const detail = (check.stderr || check.stdout || "unknown syntax error").trim();
    errors.push("classic script " + (index + 1) + " failed node --check:\n" + detail);
  }
});

if (errors.length > 0) {
  console.error(
    "Visualization template check failed (" + path.relative(root, templatePath) + "):",
  );
  for (const error of errors) {
    console.error("  - " + error);
  }
  process.exit(1);
}

console.log(
  "Visualization template check passed (" +
    classicScripts.length +
    " classic script" +
    (classicScripts.length === 1 ? "" : "s") +
    ")",
);
