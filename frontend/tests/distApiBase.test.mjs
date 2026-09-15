import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

async function readTree(directory) {
  const entries = await readdir(directory, { withFileTypes: true });
  const contents = await Promise.all(entries.map(async (entry) => {
    const path = join(directory, entry.name);
    return entry.isDirectory() ? readTree(path) : readFile(path, "utf8");
  }));
  return contents.flat().join("\n");
}

const productionBundle = await readTree(fileURLToPath(new URL("../dist", import.meta.url)));
assert.ok(!productionBundle.includes("127.0.0.1:8000"), "production bundle must not contain the local API address");

console.log("production API base test passed");
