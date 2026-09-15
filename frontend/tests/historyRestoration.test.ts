import assert from "node:assert/strict";
import { historyTurnsToMessages, mergeRestoredMessages } from "../src/historyRestoration.ts";

const response = (id: string, status: "proposed" | "confirmed", revision: number, weight: number) => ({
  kind: "meal_record_proposal",
  message: `回复-${revision}`,
  action: {
    id,
    status,
    revision,
    payload: { weight },
  },
});

const turns = [
  {
    trace_id: "trace-2",
    user_message: "50克一个",
    assistant_response: response("same", "confirmed", 3, 50),
    created_at: "2026-09-01T10:01:00Z",
    feedback_rating: "helpful",
  },
  {
    trace_id: "trace-1",
    user_message: "我吃了一个馅饼",
    assistant_response: response("same", "proposed", 1, 120),
    created_at: "2026-09-01T10:00:00Z",
    feedback_rating: null,
  },
] as any;

const restored = historyTurnsToMessages(turns);
assert.deepEqual(restored.filter((item) => item.role === "user").map((item) => item.text), ["我吃了一个馅饼", "50克一个"]);

const opening = [{ id: "opening", role: "assistant" as const, text: "欢迎" }];
const merged = mergeRestoredMessages(opening, restored as any);
const actionCards = merged.filter((item) => item.response?.action?.id === "same");
assert.equal(actionCards.length, 2);
assert.ok(actionCards.every((item) => item.response?.action?.status === "confirmed"), "恢复时终态必须覆盖旧 proposed");
assert.ok(actionCards.every((item) => item.response?.action?.revision === 3));
assert.ok(actionCards.every((item) => item.response?.action?.payload.weight === 50));

const loadedTwice = mergeRestoredMessages(merged as any, restored as any);
assert.equal(loadedTwice.length, merged.length, "重复加载不得重复插入历史");

const withLive = mergeRestoredMessages([
  ...merged,
  { id: "live-user", role: "user" as const, text: "新的问题" },
] as any, restored as any);
assert.equal(withLive.at(-1)?.id, "live-user", "恢复历史不得丢失加载期间的新消息");

console.log("history restoration tests passed");
