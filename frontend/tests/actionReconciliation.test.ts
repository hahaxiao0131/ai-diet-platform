import assert from "node:assert/strict";
import {
  appendMessageReconcilingAction,
  reconcileMessagesByActionId,
} from "../src/actionReconciliation.ts";

type TestAction = {
  id: string;
  status: "proposed" | "confirmed" | "cancelled";
  revision: number;
  payload: { weight: number };
};

type TestMessage = {
  id: string;
  response?: { action: TestAction | null };
};

const action = (id: string, status: TestAction["status"], revision: number, weight: number): TestAction => ({
  id,
  status,
  revision,
  payload: { weight },
});

const twoCards: TestMessage[] = [
  { id: "card-1", response: { action: action("same", "proposed", 1, 120) } },
  { id: "card-2", response: { action: action("same", "proposed", 2, 50) } },
  { id: "other", response: { action: action("other", "proposed", 1, 80) } },
];

const confirmed = reconcileMessagesByActionId(twoCards, action("same", "confirmed", 3, 50));
assert.deepEqual(confirmed.slice(0, 2).map((item) => item.response?.action?.status), ["confirmed", "confirmed"]);
assert.equal(confirmed[2].response?.action?.status, "proposed", "不同 action.id 不应受影响");

const cancelled = reconcileMessagesByActionId(twoCards, action("same", "cancelled", 3, 50));
assert.deepEqual(cancelled.slice(0, 2).map((item) => item.response?.action?.status), ["cancelled", "cancelled"]);

const edited = reconcileMessagesByActionId(twoCards, action("same", "proposed", 3, 35));
assert.deepEqual(edited.slice(0, 2).map((item) => item.response?.action?.payload.weight), [35, 35]);

const lateProposed = appendMessageReconcilingAction(confirmed, {
  id: "late",
  response: { action: action("same", "proposed", 1, 120) },
});
assert.equal(lateProposed.at(-1)?.response?.action?.status, "confirmed", "迟到的 proposed 不得回退终态");
assert.equal(lateProposed.at(-1)?.response?.action?.revision, 3);

console.log("action reconciliation tests passed");
