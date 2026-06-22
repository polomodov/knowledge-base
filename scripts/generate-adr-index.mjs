import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import {
  DEFAULT_ADR_DIR,
  DEFAULT_ADR_README,
  getOptionValue,
  loadAdrs,
  parseCliOptions,
  replaceAdrIndex,
  validateAdrs,
} from "./lib/adr.mjs";

const root = process.cwd();

const usage = `Usage: node scripts/generate-adr-index.mjs [--check] [--dir docs/adr] [--readme docs/adr/README.md]`;

const main = () => {
  const options = parseCliOptions(process.argv.slice(2));
  const adrDir = getOptionValue(options, "dir", DEFAULT_ADR_DIR);
  const readmePath = getOptionValue(options, "readme", DEFAULT_ADR_README);
  const checkMode = options.flags.has("check");

  if (options.flags.has("help")) {
    console.log(usage);
    return;
  }

  const adrs = loadAdrs({ root, adrDir });
  const errors = validateAdrs(adrs);
  if (errors.length > 0) {
    console.error("ADR index generation failed:");
    for (const error of errors) {
      console.error(`  - ${error}`);
    }
    process.exit(1);
  }

  const absoluteReadmePath = path.resolve(root, readmePath);
  const current = readFileSync(absoluteReadmePath, "utf8");
  const next = replaceAdrIndex(current, adrs);

  if (checkMode) {
    if (current !== next) {
      console.error("ADR index is stale. Run: npm run generate:adr-index");
      process.exit(1);
    }
    console.log(`ADR index is up to date (${adrs.length} ADRs)`);
    return;
  }

  writeFileSync(absoluteReadmePath, next, "utf8");
  console.log(`ADR index updated in ${readmePath} (${adrs.length} ADRs)`);
};

try {
  main();
} catch (error) {
  console.error(error.message);
  console.error(usage);
  process.exit(1);
}
