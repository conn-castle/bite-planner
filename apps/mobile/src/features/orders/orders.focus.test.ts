import type { StateChangeListener } from '@capacitor/app'
import { QueryClientProvider, QueryObserver, type QueryKey } from '@tanstack/react-query'
import { createElement } from 'react'
import { renderToString } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from '../../lib/api/client'
import type { GroceryOrder, JobStatus, NextOrder } from '../../lib/api/types'
import { queryKeys } from '../../lib/query/keys'
import { queryClient } from '../../lib/query/queryClient'
import { useNextOrder, useOrder } from './orders'

vi.mock('../../lib/api/client', () => ({ apiRequest: vi.fn() }))
vi.mock('../../lib/log', () => ({ logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }))
const app = vi.hoisted((): { listener: StateChangeListener | undefined } => ({ listener: undefined }))
vi.mock('@capacitor/app', () => ({
  App: {
    addListener: async (_event: string, listener: StateChangeListener) => {
      app.listener = listener
      return { remove: async () => { app.listener = undefined } }
    },
  },
}))
vi.mock('@capacitor/network', () => ({
  Network: {
    getStatus: async () => ({ connected: true, connectionType: 'wifi' }),
    addListener: async () => ({ remove: async () => {} }),
  },
}))
vi.mock('@capacitor/preferences', () => ({ Preferences: {} }))

const request = vi.mocked(apiRequest)
const unsubscribe: (() => void)[] = []
const idle: GroceryOrder = {
  id: 'draft',
  number: 4,
  status: 'draft',
  created_by: { user_id: 'member', name: 'Member' },
  created_at: '2026-09-24T09:00:00Z',
  target_date: '2026-09-27',
  decide_by_date: '2026-09-26',
  reminders_start_date: null,
  earliest_delivery_date: '2026-09-25',
  delivery_date: null,
  delivery_by: null,
  next_delivery_date: null,
  recipe_changes: [], recipes_editable: true, reached_step: 'pick',
  book_state: null,
  book_on: null,
  booking: null,
  book_issue: null,
  jobs: [],
  picks: [],
  lock: null,
  shopping_list: null,
  quote: null,
  best_window: null,
  fallback_window: null,
}
const running: GroceryOrder = {
  ...idle,
  jobs: [{
    id: 'batch',
    meal_id: null, started_at: null, finished_at: null, kind: 'generate_meals',
    status: 'running',
    error: null,
    requested_by: idle.created_by,
    created_at: idle.created_at,
    meals: [],
  }],
}

beforeEach(() => {
  vi.useFakeTimers()
  request.mockReset()
  queryClient.mount()
  setActive(true)
})

afterEach(() => {
  for (const stop of unsubscribe.splice(0)) stop()
  queryClient.clear()
  queryClient.unmount()
  vi.useRealTimers()
})

function setActive(isActive: boolean): void {
  if (!app.listener) throw new Error('The app state listener was not registered')
  app.listener({ isActive })
}

// Render the real hook to get its query options, then subscribe without a DOM.
async function observe(hook: () => unknown, key: QueryKey): Promise<void> {
  function Probe(): null {
    hook()
    return null
  }
  renderToString(createElement(QueryClientProvider, { client: queryClient }, createElement(Probe)))
  const query = queryClient.getQueryCache().find({ queryKey: key })
  if (!query) throw new Error('The hook did not create its query')
  const observer = new QueryObserver(queryClient, { ...query.options, queryKey: query.queryKey })
  unsubscribe.push(observer.subscribe(() => {}))
  await vi.advanceTimersByTimeAsync(0)
  expect(request).toHaveBeenCalledOnce()
}

async function returnToApp(): Promise<void> {
  setActive(false)
  setActive(true)
  await vi.advanceTimersByTimeAsync(0)
}

describe('returning to the app while an order is polled', () => {
  it('fetches a freshly fetched running batch at once without waiting for a poll', async () => {
    request.mockResolvedValue(running)
    await observe(() => useOrder('draft'), queryKeys.order('draft'))

    await returnToApp()

    expect(request.mock.calls).toEqual([
      ['GET', '/v1/grocery-orders/draft'],
      ['GET', '/v1/grocery-orders/draft'],
    ])
  })

  it('stops forcing fresh fetches once the order has nothing running', async () => {
    request.mockResolvedValueOnce(running).mockResolvedValue(idle)
    await observe(() => useOrder('draft'), queryKeys.order('draft'))
    await returnToApp()
    expect(request).toHaveBeenCalledTimes(2)
    expect(queryClient.getQueryData(queryKeys.order('draft'))).toEqual(idle)

    await returnToApp()

    expect(request).toHaveBeenCalledTimes(2)
  })

  it("fetches Home's freshly fetched next order at once", async () => {
    const next: NextOrder = {
      number: idle.number,
      target_date: idle.target_date,
      decide_by_date: idle.decide_by_date,
      reminders_start_date: idle.reminders_start_date,
      earliest_delivery_date: idle.earliest_delivery_date,
      draft: running,
    }
    request.mockResolvedValue(next)
    await observe(() => useNextOrder(3_000), queryKeys.nextOrder)

    await returnToApp()

    expect(request.mock.calls).toEqual([
      ['GET', '/v1/grocery-orders/next'],
      ['GET', '/v1/grocery-orders/next'],
    ])
  })
})


describe('recipe edit polling', () => {
  it.each<JobStatus>(['pending', 'running'])('refreshes a freshly fetched active %s edit immediately on focus', async (status) => {
    const editing: GroceryOrder = { ...idle, jobs: [{ ...running.jobs[0], kind: 'edit_meal', meal_id: 'soup', status }] }
    request.mockResolvedValue(editing)
    await observe(() => useOrder('draft'), queryKeys.order('draft'))
    await returnToApp()
    expect(request).toHaveBeenCalledTimes(2)
  })

  it.each(['ready', 'failed', 'placed'] as const)('uses normal freshness on focus for a terminal unselected %s change', async (state) => {
    const terminal: GroceryOrder = {
      ...idle,
      status: state === 'placed' ? 'placed' : 'draft',
      jobs: [{ ...running.jobs[0], kind: 'edit_meal', meal_id: 'soup', status: state === 'failed' ? 'failed' : 'succeeded' }],
      recipe_changes: [{ meal_id: 'soup', job_id: 'edit', state: state === 'failed' ? 'failed' : 'ready', picked: false, blocks: false,
        feedback: 'Less salt', requested_by: idle.created_by, created_at: idle.created_at, started_at: null, error: null, base_revision: 0, proposal: null }],
    }
    request.mockResolvedValue(terminal)
    await observe(() => useOrder('draft'), queryKeys.order('draft'))
    await returnToApp()
    expect(request).toHaveBeenCalledOnce()
    // Focus still refreshes normally once the cached order has gone stale.
    await vi.advanceTimersByTimeAsync(60_000)
    await returnToApp()
    expect(request).toHaveBeenCalledTimes(2)
  })
})
