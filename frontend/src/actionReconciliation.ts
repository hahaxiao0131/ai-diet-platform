export type ActionStatus = "proposed" | "confirmed" | "cancelled";

export type ActionIdentity = {
  id: string;
  status: ActionStatus;
  revision?: number;
};

export type MessageWithAction<A extends ActionIdentity> = {
  response?: { action: A | null };
};

function revisionOf(action: ActionIdentity) {
  return action.revision ?? 1;
}

function isTerminal(action: ActionIdentity) {
  return action.status === "confirmed" || action.status === "cancelled";
}

export function newestAction<A extends ActionIdentity>(actions: readonly A[]): A {
  if (actions.length === 0) throw new Error("至少需要一个动作版本");
  const terminal = actions.filter(isTerminal);
  const candidates = terminal.length ? terminal : actions;
  return candidates.reduce((selected, candidate) => (
    revisionOf(candidate) >= revisionOf(selected) ? candidate : selected
  ));
}

export function reconcileMessagesByActionId<
  A extends ActionIdentity,
  M extends MessageWithAction<A>,
>(messages: readonly M[], incoming: A): M[] {
  const matching = messages
    .map((message) => message.response?.action)
    .filter((action): action is A => action?.id === incoming.id);
  const canonical = newestAction([...matching, incoming]);
  return messages.map((message) => message.response?.action?.id === incoming.id
    ? { ...message, response: { ...message.response, action: canonical } } as M
    : message);
}

export function appendMessageReconcilingAction<
  A extends ActionIdentity,
  M extends MessageWithAction<A>,
>(messages: readonly M[], incomingMessage: M): M[] {
  const incoming = incomingMessage.response?.action;
  if (!incoming) return [...messages, incomingMessage];
  const reconciled = reconcileMessagesByActionId(messages, incoming);
  const canonical = newestAction([
    ...reconciled
      .map((message) => message.response?.action)
      .filter((action): action is A => action?.id === incoming.id),
    incoming,
  ]);
  return [
    ...reconciled,
    { ...incomingMessage, response: { ...incomingMessage.response, action: canonical } } as M,
  ];
}

export const updateActionEverywhere = reconcileMessagesByActionId;
