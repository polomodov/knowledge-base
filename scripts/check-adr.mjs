import { readFileSync } from "node:fs";
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

const usage = `Usage: node scripts/check-adr.mjs [--dir docs/adr] [--readme docs/adr/README.md]`;

const main = () => {
  const options = parseCliOptions(process.argv.slice(2));
  const adrDir = getOptionValue(options, "dir", DEFAULT_ADR_DIR);
  const readmePath = getOptionValue(options, "readme", DEFAULT_ADR_README);

  if (options.flags.has("help")) {
    console.log(usage);
    return;
  }

  const adrs = loadAdrs({ root, adrDir });
  const errors = validateAdrs(adrs);

  try {
    const absoluteReadmePath = path.resolve(root, readmePath);
    const current = readFileSync(absoluteReadmePath, "utf8");
    const expected = replaceAdrIndex(current, adrs);
    if (current !== expected) {
      errors.push(`${readmePath}: ADR index is stale. Run: npm run generate:adr-index`);
    }
  } catch (error) {
    errors.push(`${readmePath}: ${error.message}`);
  }

  if (errors.length > 0) {
    console.error("ADR check failed:");
    for (const error of errors) {
      console.error(`  - ${error}`);
    }
    process.exit(1);
  }

  console.log(`ADR check passed (${adrs.length} ADRs)`);
};

try {
  main();
} catch (error) {
  console.error(error.message);
  console.error(usage);
  process.exit(1);
}
