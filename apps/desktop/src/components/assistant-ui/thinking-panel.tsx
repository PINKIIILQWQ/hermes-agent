'use client'

import { useAuiState } from '@assistant-ui/react'
import { createContext, type FC, type ReactNode, useContext, useEffect, useRef, useState } from 'react'

import { useElapsedSeconds } from '@/components/chat/activity-timer'
import { ActivityTimerText } from '@/components/chat/activity-timer-text'
import { DisclosureRow } from '@/components/chat/disclosure-row'
import { useI18n } from '@/i18n'
import { useEnterAnimation } from '@/lib/use-enter-animation'
import { cn } from '@/lib/utils'

// ── Context ───────────────────────────────────────────────────────────────
// Reason: ReasoningAccordionGroup (thread.tsx) renders its own "Thinking…"
// DisclosureRow — that must be suppressed when the message-level ThinkingPanel
// is already providing a unified header. Components inside the panel read this
// context and skip their own per-group disclosure.
export const ThinkingPanelContext = createContext(false)

// ── Public API ────────────────────────────────────────────────────────────

/**
 * Unified collapsible panel that wraps reasoning + tool call steps inside a
 * single disclosure. Place it around <MessagePrimitive.Parts> to collect
 * every thinking and tool step under a single header that:
 *
 *  - Auto-opens while the message is streaming (scrollable preview, latest
 *    ~3 entries visible via max-h + pin-to-bottom).
 *  - Auto-collapses to a compact summary line when all steps complete.
 *  - Can be toggled manually via the DisclosureRow caret.
 *  - Reads parts directly from the assistant-ui store (not thread.isRunning)
 *    so external-store reimports don't flicker state.
 */
export const ThinkingPanel: FC<{ children: ReactNode }> = ({ children }) => {
  // Read parts from the AUI store — avoids flicker on external-store reimports
  const partsInfo = useAuiState(s => {
    const parts = s.message.parts
    if (!parts || !Array.isArray(parts) || parts.length === 0) {
      return { hasAny: false, totalCount: 0, isRunning: false }
    }

    let reasoningCount = 0
    let toolCount = 0
    let runningReasoning = false
    let runningTool = false

    for (const part of parts) {
      if (!part) continue

      if ((part as { type?: string }).type === 'reasoning') {
        reasoningCount++
        const status = (part as { status?: { type?: string } }).status
        if (status?.type !== 'complete') runningReasoning = true
      }

      if ((part as { type?: string }).type === 'tool-call') {
        toolCount++
        // A tool is still running if it has no result while the thread streams
        if ((part as { result?: unknown }).result === undefined && s.thread.isRunning) {
          runningTool = true
        }
      }
    }

    const totalCount = reasoningCount + toolCount
    const isRunning = runningReasoning || runningTool

    return { hasAny: totalCount > 0, totalCount, isRunning }
  })

  const [userOpen, setUserOpen] = useState<boolean | null>(null)

  // When no thinking/tool content exists, pass through without a panel
  if (!partsInfo.hasAny) {
    return <>{children}</>
  }

  return (
    <ThinkingPanelContext.Provider value={true}>
      <ThinkingPanelInner
        isRunning={partsInfo.isRunning}
        totalCount={partsInfo.totalCount}
        userOpen={userOpen}
        onUserToggle={() => setUserOpen(prev => !(prev ?? partsInfo.isRunning))}
      >
        {children}
      </ThinkingPanelInner>
    </ThinkingPanelContext.Provider>
  )
}

// ── Implementation ────────────────────────────────────────────────────────

const ThinkingPanelInner: FC<{
  children: ReactNode
  isRunning: boolean
  totalCount: number
  userOpen: boolean | null
  onUserToggle: () => void
}> = ({ children, isRunning, totalCount, userOpen, onUserToggle }) => {
  const { t } = useI18n()

  // Open state: auto-open while streaming, collapse when done
  const open = userOpen ?? isRunning
  const isPreview = isRunning && userOpen === null

  // Keyed on message identity so a new turn resets the panel state
  const messageId = useAuiState(s => s.message.id)
  const messageRunning = useAuiState(s => s.message.status?.type === 'running')
  const enterRef = useEnterAnimation(messageRunning, `thinking-panel:${messageId}`)

  // Timer
  const elapsed = useElapsedSeconds(isRunning, `thinking-panel-timer:${messageId}`)

  // Auto-scroll to bottom while preview is active
  const scrollRef = useRef<HTMLDivElement>(null)
  const contentRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!isPreview) return
    const el = scrollRef.current
    if (!el) return

    const pin = () => {
      el.scrollTop = el.scrollHeight
    }

    pin()
    const observer = new ResizeObserver(pin)
    observer.observe(el)

    return () => observer.disconnect()
  }, [isPreview, open, messageId, totalCount])

  // Summary label: "Thinking" with timer while running, "N steps" when done
  const label = isRunning ? t.assistant.thread.thinking : t.assistant.thread.thinkingSteps(totalCount)

  return (
    <div
      className="text-[length:var(--conversation-tool-font-size)] text-(--ui-text-tertiary)"
      data-slot="aui_thinking-panel"
      ref={enterRef}
    >
      <DisclosureRow onToggle={onUserToggle} open={open}>
        <span className="flex min-w-0 items-baseline gap-1.5">
          <span
            className={cn(
              'text-[length:var(--conversation-tool-font-size)] font-medium leading-(--conversation-line-height) text-(--ui-text-secondary)',
              isRunning && 'shimmer text-foreground/55'
            )}
          >
            {label}
          </span>
          {isRunning && (
            <ActivityTimerText
              className="text-[length:var(--conversation-caption-font-size)] tabular-nums text-(--ui-text-tertiary)"
              seconds={elapsed}
            />
          )}
        </span>
      </DisclosureRow>

      <div
        className={cn(
          open && 'mt-0.5 w-full min-w-0 max-w-full overflow-hidden wrap-anywhere pb-1',
          // Auto-collapse after completion: content stays in the DOM (for
          // tool-entry interactivity on re-expand) but is visually hidden so
          // the response text below reclaims the viewport.
          !open && 'sr-only'
        )}
        ref={open ? scrollRef : undefined}
      >
        <div
          className={cn(isPreview && 'thinking-preview')}
          ref={contentRef}
        >
          {children}
        </div>
      </div>
    </div>
  )
}
