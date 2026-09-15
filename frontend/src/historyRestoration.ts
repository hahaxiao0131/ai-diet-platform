import type { AIHistoryTurn } from "./api";
import { appendMessageReconcilingAction } from "./actionReconciliation.ts";

export type RestorableAssistantMessage = {
  id: string;
  role: "user" | "assistant";
  text?: string;
  response?: AIHistoryTurn["assistant_response"];
};

export function historyTurnsToMessages(turns: readonly AIHistoryTurn[]): RestorableAssistantMessage[] {
  return [...turns]
    .sort((left, right) => left.created_at.localeCompare(right.created_at) || left.trace_id.localeCompare(right.trace_id))
    .flatMap((turn) => [
      { id: `history-user-${turn.trace_id}`, role: "user" as const, text: turn.user_message },
      { id: `history-assistant-${turn.trace_id}`, role: "assistant" as const, response: turn.assistant_response },
    ]);
}

export function mergeRestoredMessages<
  M extends RestorableAssistantMessage,
>(current: readonly M[], restored: readonly M[]): M[] {
  const restoredIds = new Set(restored.map((message) => message.id));
  const opening = current.filter((message) => message.id === "opening");
  const live = current.filter((message) => message.id !== "opening" && !restoredIds.has(message.id));
  const ordered = [...opening, ...restored, ...live];
  const seen = new Set<string>();
  return ordered.reduce<M[]>((messages, message) => {
    if (seen.has(message.id)) return messages;
    seen.add(message.id);
    return appendMessageReconcilingAction(messages, message);
  }, []);
}
