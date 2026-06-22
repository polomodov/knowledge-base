import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import {
  ALLOWED_ADR_STATUSES,
  DEFAULT_ADR_DIR,
  DEFAULT_ADR_README,
  formatAdrDocument,
  getOptionValue,
  getOptionValues,
  loadAdrs,
  parseCliOptions,
  slugifyTitle,
  updateAdrIndexFile,
  validateAdrs,
} from "./lib/adr.mjs";

const root = process.cwd();
const DEFAULT_DECIDER = "knowledge-base maintainer";

const usage = `Usage: npm run adr:new -- --title-ru "..." --title-en "..."

Options:
  --status <status>      Default: proposed
  --date <YYYY-MM-DD>    Default: today
  --decider <name>       Repeatable. Default: knowledge-base maintainer
  --tag <tag>            Repeatable. Default: architecture, decision
  --dir <path>           Default: docs/adr
  --readme <path>        Default: docs/adr/README.md
  --no-index             Do not update docs/adr/README.md`;

const nextAdrId = (adrs) => {
  const maxId = adrs.reduce((max, adr) => {
    const numericId = Number.parseInt(adr.meta.id, 10);
    return Number.isFinite(numericId) ? Math.max(max, numericId) : max;
  }, 0);
  return String(maxId + 1).padStart(4, "0");
};

const main = () => {
  const options = parseCliOptions(process.argv.slice(2));

  if (options.flags.has("help")) {
    console.log(usage);
    return;
  }

  const titleRu = getOptionValue(options, "title-ru", "")?.trim();
  const titleEn = getOptionValue(options, "title-en", "")?.trim();
  const status = getOptionValue(options, "status", "proposed");
  const date = getOptionValue(options, "date", new Date().toISOString().slice(0, 10));
  const adrDir = getOptionValue(options, "dir", DEFAULT_ADR_DIR);
  const readmePath = getOptionValue(options, "readme", DEFAULT_ADR_README);
  const deciders = getOptionValues(options, "decider");
  const tags = getOptionValues(options, "tag");

  if (!titleRu || !titleEn) {
    throw new Error("Both --title-ru and --title-en are required");
  }

  if (!ALLOWED_ADR_STATUSES.has(status)) {
    throw new Error(`Invalid --status "${status}" (allowed: ${[...ALLOWED_ADR_STATUSES].join(", ")})`);
  }

  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    throw new Error("--date must use YYYY-MM-DD");
  }

  const existingAdrs = loadAdrs({ root, adrDir });
  const existingErrors = validateAdrs(existingAdrs, { allowEmpty: true });
  if (existingErrors.length > 0) {
    throw new Error(`Existing ADRs are invalid:\n${existingErrors.map((error) => `  - ${error}`).join("\n")}`);
  }

  const id = nextAdrId(existingAdrs);
  const filename = `${id}-${slugifyTitle(titleEn)}.md`;
  const absoluteDir = path.resolve(root, adrDir);
  const absolutePath = path.join(absoluteDir, filename);

  if (existsSync(absolutePath)) {
    throw new Error(`ADR file already exists: ${path.relative(root, absolutePath)}`);
  }

  mkdirSync(absoluteDir, { recursive: true });
  writeFileSync(
    absolutePath,
    formatAdrDocument({
      id,
      titleRu,
      titleEn,
      status,
      date,
      deciders: deciders.length > 0 ? deciders : [DEFAULT_DECIDER],
      tags: tags.length > 0 ? tags : ["architecture", "decision"],
    }),
    "utf8",
  );

  if (!options.flags.has("no-index")) {
    const adrs = loadAdrs({ root, adrDir });
    const errors = validateAdrs(adrs);
    if (errors.length > 0) {
      throw new Error(`Created ADR is invalid:\n${errors.map((error) => `  - ${error}`).join("\n")}`);
    }
    updateAdrIndexFile({ root, readmePath, adrs });
  }

  console.log(`Created ${path.relative(root, absolutePath)}`);
};

try {
  main();
} catch (error) {
  console.error(error.message);
  console.error(usage);
  process.exit(1);
}
