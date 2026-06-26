import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import type { ComposerStatusItem } from '@/store/composer-status'

import { StatusItemRow } from './status-row'

const inProgressTodo: ComposerStatusItem = {
  id: 'todo:1',
  state: 'running',
  title: 'Wire the status stack',
  todoStatus: 'in_progress',
  type: 'todo'
}

function renderRow(item: ComposerStatusItem, sessionWorking: boolean) {
  return render(<StatusItemRow item={item} sessionWorking={sessionWorking} />)
}

describe('StatusItemRow in-progress todo spinner', () => {
  afterEach(() => {
    cleanup()
  })

  it('spins while the owning session is working', () => {
    renderRow(inProgressTodo, true)

    expect(screen.getByRole('status')).toBeTruthy()
  })

  it('settles when the owning session is idle', () => {
    renderRow(inProgressTodo, false)

    expect(screen.queryByRole('status')).toBeNull()
    expect(screen.getByText('Wire the status stack')).toBeTruthy()
  })

  it('keeps running background rows spinning regardless of sessionWorking', () => {
    renderRow(
      {
        id: 'bg:1',
        state: 'running',
        title: 'npm run dev',
        type: 'background'
      },
      false
    )

    expect(screen.getByRole('status')).toBeTruthy()
  })

  it('does not spin for a completed todo', () => {
    renderRow(
      {
        ...inProgressTodo,
        state: 'done',
        todoStatus: 'completed'
      },
      true
    )

    expect(screen.queryByRole('status')).toBeNull()
  })
})
