"use client";

export type Tab = "activity" | "settings" | "learnings";

interface Props {
  activeTab: Tab;
  onTabChange: (tab: Tab) => void;
}

export function SidebarTabs({ activeTab, onTabChange }: Props) {
  return (
    <div className="flex border-b border-[var(--border)]">
      <button
        onClick={() => onTabChange("activity")}
        className={`flex-1 flex items-center justify-center py-2.5 transition-colors relative
          ${activeTab === "activity" ? "text-[var(--accent)]" : "text-[var(--text-dim)] hover:text-[var(--text)]"}`}
        title="Activity"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
        {activeTab === "activity" && (
          <span className="absolute bottom-0 left-2 right-2 h-0.5 bg-[var(--accent)] rounded-full" />
        )}
      </button>
      <button
        onClick={() => onTabChange("learnings")}
        className={`flex-1 flex items-center justify-center py-2.5 transition-colors relative
          ${activeTab === "learnings" ? "text-[var(--accent)]" : "text-[var(--text-dim)] hover:text-[var(--text)]"}`}
        title="Learnings"
      >
        {/* Brain icon */}
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M12 2a7 7 0 0 1 7 7c0 2.38-1.19 4.47-3 5.74V17a1 1 0 0 1-1 1H9a1 1 0 0 1-1-1v-2.26C6.19 13.47 5 11.38 5 9a7 7 0 0 1 7-7z" />
          <path d="M9 21h6" />
          <path d="M10 17v4" />
          <path d="M14 17v4" />
        </svg>
        {activeTab === "learnings" && (
          <span className="absolute bottom-0 left-2 right-2 h-0.5 bg-[var(--accent)] rounded-full" />
        )}
      </button>
      <button
        onClick={() => onTabChange("settings")}
        className={`flex-1 flex items-center justify-center py-2.5 transition-colors relative
          ${activeTab === "settings" ? "text-[var(--accent)]" : "text-[var(--text-dim)] hover:text-[var(--text)]"}`}
        title="Settings"
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
        {activeTab === "settings" && (
          <span className="absolute bottom-0 left-2 right-2 h-0.5 bg-[var(--accent)] rounded-full" />
        )}
      </button>
    </div>
  );
}
