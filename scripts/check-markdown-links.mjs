import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, realpathSync } from "node:fs";
import path from "node:path";

const root = process.cwd();
const markdownExtensions = new Set([".md", ".markdown"]);
const externalTarget = /^(?:[a-z][a-z0-9+.-]*:|\/\/)/iu;
const githubPunctuation = /[\0-\x1F!-,./:;<=>?@[\\\]^`{|}~\x7F-\xA0\u2000-\u206F\u2E00-\u2E7F]/gu;

const repositoryMarkdown = () => {
  const output = execFileSync(
    "git",
    ["ls-files", "-z", "--cached", "--others", "--exclude-standard"],
    { cwd: root },
  );
  return output
    .toString("utf8")
    .split("\0")
    .filter(Boolean)
    .filter((file) => markdownExtensions.has(path.extname(file).toLowerCase()))
    .sort((left, right) => left.localeCompare(right));
};

const decodeEntities = (value) =>
  value
    .replace(/&amp;/giu, "&")
    .replace(/&quot;/giu, '"')
    .replace(/&#39;|&apos;/giu, "'")
    .replace(/&lt;/giu, "<")
    .replace(/&gt;/giu, ">")
    .replace(/&#(\d+);/gu, (_match, decimal) => String.fromCodePoint(Number.parseInt(decimal, 10)))
    .replace(/&#x([0-9a-f]+);/giu, (_match, hexadecimal) => String.fromCodePoint(Number.parseInt(hexadecimal, 16)));

const unescapeMarkdown = (value) => decodeEntities(value.replace(/\\([!"#$%&'()*+,\-./:;<=>?@[\\\]^_`{|}~])/gu, "$1"));

const maskInlineCode = (line) => {
  const characters = [...line];
  for (let index = 0; index < characters.length; index += 1) {
    if (characters[index] !== "`") {
      continue;
    }
    let delimiterLength = 1;
    while (characters[index + delimiterLength] === "`") {
      delimiterLength += 1;
    }
    const delimiter = "`".repeat(delimiterLength);
    const remainder = characters.slice(index + delimiterLength).join("");
    const closingOffset = remainder.indexOf(delimiter);
    if (closingOffset < 0) {
      continue;
    }
    const end = index + delimiterLength + closingOffset + delimiterLength;
    for (let cursor = index; cursor < end; cursor += 1) {
      characters[cursor] = " ";
    }
    index = end - 1;
  }
  return characters.join("");
};

const visibleLines = (source) => {
  const lines = source.split("\n");
  const structural = [];
  const links = [];
  let fence = null;
  let inComment = false;

  for (const original of lines) {
    const fenceMatch = original.match(/^ {0,3}(`{3,}|~{3,})/u);
    if (fence === null && fenceMatch !== null) {
      fence = { marker: fenceMatch[1][0], length: fenceMatch[1].length };
      structural.push("");
      links.push("");
      continue;
    }
    if (fence !== null) {
      const closing = original.match(/^ {0,3}(`+|~+)\s*$/u);
      if (closing !== null && closing[1][0] === fence.marker && closing[1].length >= fence.length) {
        fence = null;
      }
      structural.push("");
      links.push("");
      continue;
    }

    let visible = "";
    let cursor = 0;
    while (cursor < original.length) {
      if (inComment) {
        const end = original.indexOf("-->", cursor);
        if (end < 0) {
          cursor = original.length;
          continue;
        }
        inComment = false;
        cursor = end + 3;
        continue;
      }
      const start = original.indexOf("<!--", cursor);
      if (start < 0) {
        visible += original.slice(cursor);
        break;
      }
      visible += original.slice(cursor, start);
      inComment = true;
      cursor = start + 4;
    }
    structural.push(visible);
    links.push(maskInlineCode(visible));
  }
  return { structural, links };
};

const findClosingBracket = (line, start, opening, closing) => {
  let depth = 1;
  for (let index = start + 1; index < line.length; index += 1) {
    if (line[index] === "\\") {
      index += 1;
    } else if (line[index] === opening) {
      depth += 1;
    } else if (line[index] === closing) {
      depth -= 1;
      if (depth === 0) {
        return index;
      }
    }
  }
  return -1;
};

const destinationFrom = (raw) => {
  const value = raw.trimStart();
  if (value.startsWith("<")) {
    let closing = -1;
    for (let index = 1; index < value.length; index += 1) {
      if (value[index] === "\\") {
        index += 1;
      } else if (value[index] === ">") {
        closing = index;
        break;
      }
    }
    return closing < 0 ? "" : unescapeMarkdown(value.slice(1, closing));
  }

  let result = "";
  let depth = 0;
  for (let index = 0; index < value.length; index += 1) {
    const character = value[index];
    if (character === "\\" && index + 1 < value.length) {
      result += value[index + 1];
      index += 1;
    } else if (/\s/u.test(character) && depth === 0) {
      break;
    } else {
      if (character === "(") {
        depth += 1;
      } else if (character === ")" && depth > 0) {
        depth -= 1;
      }
      result += character;
    }
  }
  return unescapeMarkdown(result);
};

const inlineLinks = (line) => {
  const links = [];
  for (let index = 0; index < line.length; index += 1) {
    if (line[index] !== "[" || (index > 0 && line[index - 1] === "\\")) {
      continue;
    }
    const labelEnd = findClosingBracket(line, index, "[", "]");
    if (labelEnd < 0 || line[labelEnd + 1] !== "(") {
      continue;
    }
    const destinationEnd = findClosingBracket(line, labelEnd + 1, "(", ")");
    if (destinationEnd < 0) {
      continue;
    }
    links.push(destinationFrom(line.slice(labelEnd + 2, destinationEnd)));
    index = destinationEnd;
  }
  return links;
};

const parseLinks = (source) => {
  const { links: lines } = visibleLines(source);
  const found = [];
  const definitions = new Map();
  const referenceUsages = [];

  for (const [offset, line] of lines.entries()) {
    const lineNumber = offset + 1;
    const definition = line.match(/^ {0,3}\[([^\]]+)\]:\s*(.*)$/u);
    if (definition !== null) {
      const label = definition[1].trim().replace(/\s+/gu, " ").toLowerCase();
      const target = destinationFrom(definition[2]);
      definitions.set(label, target);
      found.push({ line: lineNumber, target });
      continue;
    }

    for (const target of inlineLinks(line)) {
      found.push({ line: lineNumber, target });
    }
    for (const match of line.matchAll(/!?\[([^\]]+)\]\[([^\]]*)\]/gu)) {
      const label = (match[2] || match[1]).trim().replace(/\s+/gu, " ").toLowerCase();
      referenceUsages.push({ line: lineNumber, label });
    }
    for (const match of line.matchAll(/\b(?:href|src)\s*=\s*["']([^"']+)["']/giu)) {
      found.push({ line: lineNumber, target: unescapeMarkdown(match[1]) });
    }
  }

  return { found, definitions, referenceUsages };
};

const headingText = (value) =>
  decodeEntities(value)
    .replace(/\s+#+\s*$/u, "")
    .replace(/<[^>]+>/gu, "")
    .replace(/!?\[([^\]]+)\]\([^)]*\)/gu, "$1")
    .replace(/!?\[([^\]]+)\]\[[^\]]*\]/gu, "$1")
    .replace(/[`*_~]/gu, "")
    .trim();

const githubSlug = (value) => headingText(value).toLowerCase().replace(githubPunctuation, "").replace(/\s/gu, "-");

const anchorsFor = (source) => {
  const { structural: lines } = visibleLines(source);
  const anchors = new Set();
  const occurrences = new Map();

  const addHeading = (heading) => {
    const base = githubSlug(heading);
    if (!base) {
      return;
    }
    let slug = base;
    let count = occurrences.get(base) ?? 0;
    while (anchors.has(slug)) {
      count += 1;
      slug = `${base}-${count}`;
    }
    occurrences.set(base, count);
    anchors.add(slug);
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    for (const match of line.matchAll(/<a\s+[^>]*(?:id|name)\s*=\s*["']([^"']+)["'][^>]*>/giu)) {
      anchors.add(decodeEntities(match[1]));
    }
    const atx = line.match(/^ {0,3}#{1,6}(?:[ \t]+|$)(.*)$/u);
    if (atx !== null) {
      addHeading(atx[1]);
      continue;
    }
    if (index > 0 && /^ {0,3}(?:=+|-+)\s*$/u.test(line) && lines[index - 1].trim()) {
      addHeading(lines[index - 1]);
    }
  }
  return anchors;
};

const withinDirectory = (directory, absolute) => {
  const relative = path.relative(directory, absolute);
  return relative === "" || (!relative.startsWith(`..${path.sep}`) && relative !== ".." && !path.isAbsolute(relative));
};

const withinRoot = (absolute) => withinDirectory(root, absolute);

const exactPathExists = (absolute) => {
  if (!withinRoot(absolute)) {
    return false;
  }
  const relative = path.relative(root, absolute);
  let current = root;
  for (const component of relative.split(path.sep).filter(Boolean)) {
    const entries = readdirSafe(current);
    if (entries === null || !entries.includes(component)) {
      return false;
    }
    current = path.join(current, component);
  }
  return existsSync(current);
};

const decodedComponent = (value) => {
  try {
    return decodeURIComponent(value);
  } catch {
    return null;
  }
};

const targetParts = (rawTarget) => {
  const target = rawTarget.trim();
  const fragmentAt = target.indexOf("#");
  const pathAndQuery = fragmentAt < 0 ? target : target.slice(0, fragmentAt);
  const rawFragment = fragmentAt < 0 ? null : target.slice(fragmentAt + 1);
  const queryAt = pathAndQuery.indexOf("?");
  const rawPath = queryAt < 0 ? pathAndQuery : pathAndQuery.slice(0, queryAt);
  return {
    path: decodedComponent(rawPath),
    fragment: rawFragment === null ? null : decodedComponent(rawFragment),
  };
};

const main = () => {
  const files = repositoryMarkdown();
  const realRoot = realpathSync(root);
  const sources = new Map(files.map((file) => [file, readFileSync(path.join(root, file), "utf8")]));
  const anchorCache = new Map();
  const errors = [];
  let checked = 0;

  const anchorsAt = (absolute) => {
    if (!anchorCache.has(absolute)) {
      anchorCache.set(absolute, anchorsFor(readFileSync(absolute, "utf8")));
    }
    return anchorCache.get(absolute);
  };

  for (const [file, source] of sources) {
    const parsed = parseLinks(source);
    for (const usage of parsed.referenceUsages) {
      if (!parsed.definitions.has(usage.label)) {
        errors.push({ file, line: usage.line, target: `[${usage.label}]`, reason: "missing reference definition" });
      }
    }

    for (const link of parsed.found) {
      checked += 1;
      if (!link.target) {
        errors.push({ file, line: link.line, target: link.target, reason: "empty link target" });
        continue;
      }
      if (externalTarget.test(link.target)) {
        continue;
      }
      const parts = targetParts(link.target);
      if (parts.path === null || parts.fragment === null && link.target.includes("#") && parts.fragment === null) {
        errors.push({ file, line: link.line, target: link.target, reason: "invalid percent encoding" });
        continue;
      }
      const linked = parts.path
        ? parts.path.startsWith("/")
          ? path.resolve(root, `.${parts.path}`)
          : path.resolve(root, path.dirname(file), parts.path)
        : path.resolve(root, file);
      if (!withinRoot(linked)) {
        errors.push({ file, line: link.line, target: link.target, reason: "local link escapes the repository" });
        continue;
      }
      if (!exactPathExists(linked)) {
        errors.push({ file, line: link.line, target: link.target, reason: "local target does not exist with exact case" });
        continue;
      }
      if (!withinDirectory(realRoot, realpathSync(linked))) {
        errors.push({ file, line: link.line, target: link.target, reason: "local target resolves outside the repository" });
        continue;
      }

      let anchorFile = linked;
      if (parts.fragment !== null && existsSync(linked) && readdirSafe(linked) !== null) {
        const readme = path.join(linked, "README.md");
        if (!exactPathExists(readme)) {
          errors.push({ file, line: link.line, target: link.target, reason: "directory anchor has no README.md" });
          continue;
        }
        anchorFile = readme;
      }
      if (!withinDirectory(realRoot, realpathSync(anchorFile))) {
        errors.push({ file, line: link.line, target: link.target, reason: "anchor target resolves outside the repository" });
        continue;
      }
      if (parts.fragment !== null && parts.fragment !== "" && markdownExtensions.has(path.extname(anchorFile).toLowerCase())) {
        const anchors = anchorsAt(anchorFile);
        const fragment = parts.fragment.replace(/^user-content-/u, "");
        if (!anchors.has(parts.fragment) && !anchors.has(fragment)) {
          errors.push({ file, line: link.line, target: link.target, reason: `anchor #${parts.fragment} does not exist` });
        }
      }
    }
  }

  if (errors.length > 0) {
    console.error("Markdown link check failed:");
    for (const error of errors) {
      console.error(`  - ${error.file}:${error.line}: ${error.reason}: ${error.target || "<empty>"}`);
    }
    process.exit(1);
  }
  console.log(`Markdown link check passed (${checked} links across ${files.length} files)`);
};

const readdirSafe = (target) => {
  try {
    return readdirSync(target);
  } catch {
    return null;
  }
};

try {
  main();
} catch (error) {
  console.error(`Markdown link check failed: ${error.message}`);
  process.exit(1);
}
