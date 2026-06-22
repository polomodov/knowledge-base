import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import path from "node:path";

export const DEFAULT_ADR_DIR = "docs/adr";
export const DEFAULT_ADR_README = "docs/adr/README.md";
export const ADR_INDEX_START = "<!-- ADR_INDEX_START -->";
export const ADR_INDEX_END = "<!-- ADR_INDEX_END -->";

export const ALLOWED_ADR_STATUSES = new Set([
  "proposed",
  "accepted",
  "rejected",
  "deprecated",
  "superseded",
]);

export const REQUIRED_RU_HEADINGS = [
  "## RU",
  "### Контекст и проблема",
  "### Y-statement",
  "### Драйверы решения",
  "### Рассмотренные варианты",
  "### Итоговое решение",
  "### Последствия",
  "### План пересмотра",
  "### Ссылки",
];

export const REQUIRED_EN_HEADINGS = [
  "## EN",
  "### Context and Problem Statement",
  "### Y-statement",
  "### Decision Drivers",
  "### Considered Options",
  "### Decision Outcome",
  "### Consequences",
  "### Review Plan",
  "### Links",
];

const META_BLOCK_RE = /```(?:json\s+)?adr-meta\s*\n([\s\S]*?)\n```/;
const ADR_FILENAME_RE = /^(\d{4})-[a-z0-9][a-z0-9-]*\.md$/;

export const toRelativePath = (root, absolutePath) => path.relative(root, absolutePath).replaceAll(path.sep, "/");

export const parseCliOptions = (argv) => {
  const options = {
    flags: new Set(),
    values: new Map(),
  };

  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    if (!token.startsWith("--")) {
      throw new Error(`Unexpected argument: ${token}`);
    }

    const key = token.slice(2);
    const next = argv[index + 1];
    if (!next || next.startsWith("--")) {
      options.flags.add(key);
      continue;
    }

    if (!options.values.has(key)) {
      options.values.set(key, []);
    }
    options.values.get(key).push(next);
    index += 1;
  }

  return options;
};

export const getOptionValue = (options, key, fallback = undefined) => {
  const values = options.values.get(key);
  return values?.[values.length - 1] ?? fallback;
};

export const getOptionValues = (options, key) => options.values.get(key) ?? [];

export const isPlainObject = (value) =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export const escapeMarkdownTableCell = (value) =>
  String(value).replaceAll("|", "\\|").replaceAll("\n", " ").trim();

export const escapeRegExp = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export const hasExactHeading = (text, heading) => {
  const re = new RegExp(`^${escapeRegExp(heading)}\\s*$`, "m");
  return re.test(text);
};

export const slugifyTitle = (title) => {
  const slug = title
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-");

  return slug || "decision";
};

export const readAdrFiles = ({ root = process.cwd(), adrDir = DEFAULT_ADR_DIR } = {}) => {
  const absoluteDir = path.resolve(root, adrDir);
  if (!existsSync(absoluteDir)) {
    return [];
  }

  return readdirSync(absoluteDir)
    .filter((entry) => ADR_FILENAME_RE.test(entry))
    .sort((a, b) => a.localeCompare(b))
    .map((filename) => {
      const absolutePath = path.join(absoluteDir, filename);
      return {
        filename,
        absolutePath,
        relativePath: toRelativePath(root, absolutePath),
        text: readFileSync(absolutePath, "utf8"),
      };
    });
};

export const parseAdrMeta = (file) => {
  const match = file.text.match(META_BLOCK_RE);
  if (!match) {
    throw new Error(`${file.relativePath}: missing \`adr-meta\` JSON block`);
  }

  try {
    return JSON.parse(match[1]);
  } catch (error) {
    throw new Error(`${file.relativePath}: invalid \`adr-meta\` JSON (${error.message})`, {
      cause: error,
    });
  }
};

export const loadAdrs = (options = {}) =>
  readAdrFiles(options)
    .map((file) => ({
      ...file,
      meta: parseAdrMeta(file),
    }))
    .sort((left, right) => String(left.meta.id).localeCompare(String(right.meta.id)));

const requireString = (errors, adr, key) => {
  if (typeof adr.meta[key] !== "string" || !adr.meta[key].trim()) {
    errors.push(`${adr.relativePath}: \`${key}\` must be a non-empty string`);
    return "";
  }
  return adr.meta[key].trim();
};

const requireStringArray = (errors, adr, key) => {
  const value = adr.meta[key];
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string" || !item.trim())) {
    errors.push(`${adr.relativePath}: \`${key}\` must be an array of non-empty strings`);
    return [];
  }
  return value.map((item) => item.trim());
};

export const validateAdrs = (adrs, { allowEmpty = false } = {}) => {
  const errors = [];
  const byId = new Map();
  const seenIds = new Set();

  if (adrs.length === 0 && !allowEmpty) {
    errors.push(`No ADR files found in ${DEFAULT_ADR_DIR}`);
  }

  for (const adr of adrs) {
    if (!isPlainObject(adr.meta)) {
      errors.push(`${adr.relativePath}: \`adr-meta\` must be a JSON object`);
      continue;
    }

    const filenameMatch = adr.filename.match(ADR_FILENAME_RE);
    const filenameId = filenameMatch?.[1] ?? "";
    const id = requireString(errors, adr, "id");
    requireString(errors, adr, "titleRu");
    requireString(errors, adr, "titleEn");
    const status = requireString(errors, adr, "status");
    const date = requireString(errors, adr, "date");
    requireStringArray(errors, adr, "deciders");
    requireStringArray(errors, adr, "tags");
    const supersedes = requireStringArray(errors, adr, "supersedes");
    const supersededBy = requireStringArray(errors, adr, "supersededBy");

    if (id && !/^\d{4}$/.test(id)) {
      errors.push(`${adr.relativePath}: \`id\` must use four digits, e.g. "0001"`);
    }

    if (id && filenameId && id !== filenameId) {
      errors.push(`${adr.relativePath}: filename prefix ${filenameId} must match \`id\` ${id}`);
    }

    if (id && seenIds.has(id)) {
      errors.push(`${adr.relativePath}: duplicate ADR id ${id}`);
    }
    if (id) {
      seenIds.add(id);
      byId.set(id, adr);
    }

    if (status && !ALLOWED_ADR_STATUSES.has(status)) {
      errors.push(
        `${adr.relativePath}: invalid status "${status}" (allowed: ${[...ALLOWED_ADR_STATUSES].join(", ")})`,
      );
    }

    if (date && !/^\d{4}-\d{2}-\d{2}$/.test(date)) {
      errors.push(`${adr.relativePath}: \`date\` must use YYYY-MM-DD`);
    } else if (date && Number.isNaN(new Date(`${date}T00:00:00Z`).getTime())) {
      errors.push(`${adr.relativePath}: \`date\` is not a valid calendar date`);
    }

    for (const heading of [...REQUIRED_RU_HEADINGS, ...REQUIRED_EN_HEADINGS]) {
      if (!hasExactHeading(adr.text, heading)) {
        errors.push(`${adr.relativePath}: missing required heading "${heading}"`);
      }
    }

    if (id && [...supersedes, ...supersededBy].includes(id)) {
      errors.push(`${adr.relativePath}: ADR cannot supersede itself`);
    }

    if (status === "superseded" && supersededBy.length === 0) {
      errors.push(`${adr.relativePath}: status \`superseded\` requires at least one \`supersededBy\` entry`);
    }

    if (status !== "superseded" && supersededBy.length > 0) {
      errors.push(`${adr.relativePath}: \`supersededBy\` is only allowed when status is \`superseded\``);
    }
  }

  for (const adr of adrs) {
    if (!isPlainObject(adr.meta) || typeof adr.meta.id !== "string") {
      continue;
    }

    const id = adr.meta.id;
    const supersedes = Array.isArray(adr.meta.supersedes) ? adr.meta.supersedes : [];
    const supersededBy = Array.isArray(adr.meta.supersededBy) ? adr.meta.supersededBy : [];

    for (const targetId of supersedes) {
      const target = byId.get(targetId);
      if (!target) {
        errors.push(`${adr.relativePath}: \`supersedes\` references unknown ADR ${targetId}`);
        continue;
      }
      if (!Array.isArray(target.meta.supersededBy) || !target.meta.supersededBy.includes(id)) {
        errors.push(`${adr.relativePath}: ADR ${targetId} must list ${id} in \`supersededBy\``);
      }
    }

    for (const sourceId of supersededBy) {
      const source = byId.get(sourceId);
      if (!source) {
        errors.push(`${adr.relativePath}: \`supersededBy\` references unknown ADR ${sourceId}`);
        continue;
      }
      if (!Array.isArray(source.meta.supersedes) || !source.meta.supersedes.includes(id)) {
        errors.push(`${adr.relativePath}: ADR ${sourceId} must list ${id} in \`supersedes\``);
      }
    }
  }

  return errors;
};

export const buildAdrIndex = (adrs) => {
  const lines = [
    "Generated by `npm run generate:adr-index`. Do not edit manually between markers.",
    "",
    "| ID | Status | Date | Decision | Tags |",
    "| --- | --- | --- | --- | --- |",
  ];

  for (const adr of adrs) {
    const { id, status, date, titleRu, titleEn, tags } = adr.meta;
    const tagText = Array.isArray(tags) ? tags.join(", ") : "";
    const decision = `[${escapeMarkdownTableCell(titleRu)}](${adr.filename})<br />${escapeMarkdownTableCell(titleEn)}`;
    lines.push(
      `| [${id}](${adr.filename}) | \`${escapeMarkdownTableCell(status)}\` | ${escapeMarkdownTableCell(date)} | ${decision} | ${escapeMarkdownTableCell(tagText)} |`,
    );
  }

  return lines.join("\n");
};

export const replaceAdrIndex = (readmeText, adrs) => {
  const startIndex = readmeText.indexOf(ADR_INDEX_START);
  const endIndex = readmeText.indexOf(ADR_INDEX_END);

  if (startIndex === -1 || endIndex === -1 || endIndex < startIndex) {
    throw new Error(`ADR README must contain ${ADR_INDEX_START} and ${ADR_INDEX_END} markers`);
  }

  const before = readmeText.slice(0, startIndex + ADR_INDEX_START.length);
  const after = readmeText.slice(endIndex);
  return `${before}\n${buildAdrIndex(adrs)}\n${after}`;
};

export const updateAdrIndexFile = ({ root = process.cwd(), readmePath = DEFAULT_ADR_README, adrs }) => {
  const absoluteReadmePath = path.resolve(root, readmePath);
  const current = readFileSync(absoluteReadmePath, "utf8");
  const next = replaceAdrIndex(current, adrs);
  writeFileSync(absoluteReadmePath, next, "utf8");
  return current !== next;
};

export const formatAdrDocument = ({
  id,
  titleRu,
  titleEn,
  status,
  date,
  deciders,
  tags,
  supersedes = [],
  supersededBy = [],
}) => `# ${id}. ${titleRu} / ${titleEn}

\`\`\`json adr-meta
${JSON.stringify(
  {
    id,
    titleRu,
    titleEn,
    status,
    date,
    deciders,
    tags,
    supersedes,
    supersededBy,
  },
  null,
  2,
)}
\`\`\`

## RU

### Контекст и проблема

Опишите контекст, архитектурную развилку и почему решение значимо для проекта.

### Y-statement

В контексте \`<сценарий>\`, столкнувшись с \`<проблема/ограничение>\`, мы решили выбрать \`<вариант>\`, чтобы достичь \`<качество/цель>\`, принимая \`<компромисс>\`.

### Драйверы решения

- Драйвер 1.
- Драйвер 2.

### Рассмотренные варианты

- Вариант 1.
- Вариант 2.

### Итоговое решение

Выбран вариант: \`<вариант>\`, потому что \`<обоснование>\`.

### Последствия

- Хорошо: \`<положительное последствие>\`.
- Плохо: \`<отрицательное последствие или цена>\`.
- Нейтрально: \`<что нужно помнить при реализации>\`.

### План пересмотра

Когда и по каким сигналам решение нужно пересмотреть.

### Ссылки

- [Architectural Decision Records](https://adr.github.io/)

## EN

### Context and Problem Statement

Describe the context, architectural fork, and why the decision matters for the project.

### Y-statement

In the context of \`<scenario>\`, facing \`<concern/constraint>\`, we decided for \`<option>\` to achieve \`<quality/goal>\`, accepting \`<trade-off>\`.

### Decision Drivers

- Driver 1.
- Driver 2.

### Considered Options

- Option 1.
- Option 2.

### Decision Outcome

Chosen option: \`<option>\`, because \`<rationale>\`.

### Consequences

- Good: \`<positive consequence>\`.
- Bad: \`<negative consequence or cost>\`.
- Neutral: \`<implementation note>\`.

### Review Plan

When and by which signals the decision should be revisited.

### Links

- [Architectural Decision Records](https://adr.github.io/)
`;
