import assert from "node:assert/strict";
import { resolveApiBaseUrl } from "../src/apiBase.ts";

assert.equal(resolveApiBaseUrl(), "/api/v1");
assert.equal(resolveApiBaseUrl(""), "/api/v1");
assert.equal(resolveApiBaseUrl(" /api/v1/// "), "/api/v1");
assert.equal(resolveApiBaseUrl("https://diet-api.example.com/api/v1/"), "https://diet-api.example.com/api/v1");

console.log("api base tests passed");
