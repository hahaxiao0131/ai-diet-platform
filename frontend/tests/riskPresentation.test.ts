import assert from "node:assert/strict";
import { presentSafetyRisk, primaryRecordedWarning } from "../src/riskPresentation.ts";

const recordedRisk = {
  level: "block",
  evaluation_mode: "record",
  decision_effect: "recorded_with_warning",
  message: "已阻止：花生命中过敏档案",
  alternative: "改选不含花生的食物",
} as const;
const blockedRisk = {
  level: "block",
  evaluation_mode: "recommendation",
  decision_effect: "blocked",
  message: "高风险记录：花生命中过敏档案",
  alternative: "改选不含花生的食物",
} as const;

assert.match(presentSafetyRisk(recordedRisk, "proposal"), /^这是已发生事实的待确认记录/);
assert.match(presentSafetyRisk(recordedRisk, "recorded"), /^已记录高风险饮食/);
assert.ok(!presentSafetyRisk(recordedRisk, "recorded").includes("已阻止"));
assert.match(presentSafetyRisk(blockedRisk, "recommendation"), /^已阻止/);
assert.equal(primaryRecordedWarning([blockedRisk, recordedRisk]), recordedRisk);

console.log("risk presentation tests passed");
