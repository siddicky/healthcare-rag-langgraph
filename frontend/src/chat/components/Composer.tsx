"use client";

import { useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import { PaperclipIcon, SendIcon } from "./Icons";

/**
 * The floating composer: textarea + attach + send. ONE active run per
 * thread — send and attach are disabled while a run streams or an upload
 * is in flight.
 */
export function Composer({
  disabled,
  attachmentReady,
  onSend,
  onAttach,
}: {
  disabled: boolean;
  attachmentReady: boolean;
  onSend: (text: string) => void;
  onAttach: (file: File) => void;
}) {
  const [input, setInput] = useState("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  function send() {
    const text = input.trim();
    if (disabled || (text === "" && !attachmentReady)) return;
    onSend(text);
    setInput("");
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file !== undefined && !disabled) onAttach(file);
  }

  const sendDisabled = disabled || (input.trim() === "" && !attachmentReady);

  return (
    <div className="composer-wrap">
      <div className="composer">
        <textarea
          rows={1}
          placeholder={attachmentReady ? "Add a note, or press send to review the document" : "Message your coach…"}
          value={input}
          aria-label="Message your coach"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
        />
        <div className="composer-actions">
          <div className="composer-left">
            <input
              ref={fileInputRef}
              type="file"
              accept="application/pdf,image/jpeg,image/png"
              aria-label="Attach a document"
              onChange={handleFileChange}
              hidden
            />
            <button
              className="icon-btn"
              title="Attach a document"
              aria-label="Attach a document"
              disabled={disabled}
              onClick={() => fileInputRef.current?.click()}
            >
              <PaperclipIcon />
            </button>
            {attachmentReady && (
              <span style={{ fontSize: 12, color: "var(--camel)", paddingLeft: 4 }}>
                Document ready to review
              </span>
            )}
          </div>
          <button
            className="send-btn"
            title="Send"
            aria-label="Send"
            disabled={sendDisabled}
            onClick={() => send()}
          >
            <SendIcon />
          </button>
        </div>
      </div>
    </div>
  );
}
