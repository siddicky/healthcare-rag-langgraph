"use client";

import { useState } from "react";
import type { ThreadSummary } from "@/chat/coachApi";
import { LogoutIcon, PlusIcon, UserIcon, XIcon } from "./Icons";

export function ThreadSidebar({
  threads,
  activeThreadId,
  email,
  threadTitle,
  onNewConversation,
  onSelectThread,
  onDeleteThread,
  onSignOut,
}: {
  threads: ThreadSummary[];
  activeThreadId: string | null;
  email: string;
  threadTitle: (threadId: string) => string;
  onNewConversation: () => void;
  onSelectThread: (threadId: string) => void;
  onDeleteThread: (threadId: string) => void;
  onSignOut: () => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const initials = email.slice(0, 2).toUpperCase();

  return (
    <aside className="sidebar">
      <button className="new-chat" onClick={onNewConversation}>
        <PlusIcon /> New conversation
      </button>
      <div className="thread-group">Conversations</div>
      <div className="thread-list" role="list">
        {threads.map((thread) => (
          <div className="thread-row" key={thread.thread_id} role="listitem">
            <button
              className={`thread-item${thread.thread_id === activeThreadId ? " active" : ""}`}
              onClick={() => onSelectThread(thread.thread_id)}
              title={threadTitle(thread.thread_id)}
            >
              {threadTitle(thread.thread_id)}
            </button>
            <button
              className="thread-delete"
              aria-label={`Delete ${threadTitle(thread.thread_id)}`}
              title="Delete conversation"
              onClick={() => onDeleteThread(thread.thread_id)}
            >
              <XIcon />
            </button>
          </div>
        ))}
      </div>
      <div className="sidebar-footer">
        {menuOpen && (
          <button
            className="user-menu-backdrop"
            aria-label="Close menu"
            onClick={() => setMenuOpen(false)}
          />
        )}
        {menuOpen && (
          <div className="user-menu" role="menu">
            <span className="user-menu-note">Signed in as {email}</span>
            <div className="user-menu-divider" />
            <button className="user-menu-item" role="menuitem" disabled>
              <UserIcon /> View profile
            </button>
            <button
              className="user-menu-item"
              role="menuitem"
              onClick={() => {
                setMenuOpen(false);
                onSignOut();
              }}
            >
              <LogoutIcon /> Log out
            </button>
          </div>
        )}
        <button className="user-row" aria-label="Account menu" onClick={() => setMenuOpen((open) => !open)}>
          <div className="user-avatar">{initials}</div>
          <div className="user-info">
            <div className="user-name">Member</div>
            <div className="user-email">{email}</div>
          </div>
        </button>
      </div>
    </aside>
  );
}
