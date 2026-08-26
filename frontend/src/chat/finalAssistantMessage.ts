import {
  aiDisplayText,
  isAiMessage,
  messageReasoning,
  parseMemoryConfirmation,
  type WireMessage,
} from "./model";

export function selectFinalAssistantMessage(
  messages: readonly WireMessage[],
): WireMessage | undefined {
  let fallback: WireMessage | undefined;
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message === undefined || !isAiMessage(message)) continue;
    fallback ??= message;
    const reasoning = messageReasoning(message);
    if (
      aiDisplayText(message.content) !== "" ||
      (reasoning !== null && reasoning.trim() !== "") ||
      parseMemoryConfirmation(message.content) !== null
    ) {
      return message;
    }
  }
  return fallback;
}
