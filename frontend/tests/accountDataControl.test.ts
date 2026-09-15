import assert from "node:assert/strict";
import { accountExportFilename, ACCOUNT_DELETE_CONFIRMATION, isAccountDeleteConfirmation, serializeAccountExport } from "../src/accountDataControl.ts";

assert.equal(isAccountDeleteConfirmation(ACCOUNT_DELETE_CONFIRMATION), true);
assert.equal(isAccountDeleteConfirmation(" 永久删除 "), true);
assert.equal(isAccountDeleteConfirmation("删除账号"), false);
assert.equal(accountExportFilename("2026-09-02T10:00:00+08:00"), "diet-account-export-2026-09-02.json");
assert.match(serializeAccountExport({ schema_version: "v1", profile: { id: "only-me" } }), /"only-me"/);

console.log("account data control tests passed");
