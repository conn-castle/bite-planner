import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { GroceryOrder, Quote } from '../../lib/api/types'
import { DeliveryStep } from './DeliveryStep'
import { OrderSession } from './lock'
import { useNotifiedOfJob } from '../notifications/notifications'

vi.mock('../../lib/auth/neonAuth', () => ({ neonAuth: {} }))
vi.mock('../../lib/log', () => ({ logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }))
vi.mock('../notifications/notifications', () => ({ useNotifiedOfJob: vi.fn((startedBy: string | null): boolean => startedBy !== null) }))
vi.mock('../session/session', () => ({ useSession: () => ({ data: { user: { id: 'nick' } } }) }))
vi.mock('../settings/settings', () => ({ useHousehold: () => ({ shown: { delivery_time: '18:00:00' } }) }))

const quote: Quote = {
  job: { id: 'times', meal_id: null, kind: 'quote', status: 'succeeded', error: null,
    requested_by: { user_id: 'nick', name: 'Nick' }, created_at: '2026-11-07T17:00:00Z', started_at: null, finished_at: null, meals: [] },
  current: true, signed_out: false, blocked_because: null, checkout: null,
  availability_current: true, availability_valid_until: '2026-11-08T05:00:00Z',
  availability: { observed_at: '2026-11-07T17:00:00Z', time_zone: 'America/New_York', days: [
    { day: '2026-11-07', windows: [{ start: '13:00:00', end: '15:00:00' }, { start: '16:00:00', end: '18:00:00' }] },
  ] },
}
const draft: GroceryOrder = {
  id: 'draft', number: 1, status: 'draft', created_by: { user_id: 'nick', name: 'Nick' }, created_at: '2026-11-07T12:00:00Z',
  target_date: null, decide_by_date: null, reminders_start_date: null, earliest_delivery_date: '2026-11-07',
  delivery_date: '2026-11-07', delivery_by: null, delivery_time_zone: 'America/New_York', next_delivery_date: null,
  recipe_changes: [], recipes_editable: true, reached_step: 'delivery', picks: [], lock: null, shopping_list: null,
  quote, jobs: [quote.job], book_state: null, book_on: null, booking: null, book_issue: null, can_confirm_absent: false,
}
function screen(quoted: Quote | null): string {
  const client: QueryClient = new QueryClient()
  return renderToStaticMarkup(<QueryClientProvider client={client}>
    <OrderSession.Provider value="phone">
      <DeliveryStep order={{ ...draft, quote: quoted }} onBack={() => {}} onNext={() => {}} onClose={() => {}} />
    </OrderSession.Provider>
  </QueryClientProvider>)
}

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-07T19:00:00Z')) })
afterEach(() => { vi.useRealTimers(); vi.mocked(useNotifiedOfJob).mockImplementation((startedBy: string | null): boolean => startedBy !== null) })

describe('Delivery', () => {
  it.each(['none', 'pending', 'running', 'expired'] as const)('shows the normal waiting screen instead of disabled days when %s', (state) => {
    const quoted: Quote | null = state === 'none' ? null : state === 'expired'
      ? { ...quote, availability_current: false }
      : { ...quote, availability: null, availability_current: false, job: { ...quote.job, status: state } }
    const html: string = screen(quoted)
    expect(html).toContain('Checking delivery and total')
    expect(html).toContain('role="timer"')
    expect(html).toContain('a few minutes')
    expect(html).not.toContain('aria-label="Delivery date"')
    if (state === 'pending' || state === 'running') {
      expect(html).toContain('Notify me')
      expect(html).toContain('notify you when the check finishes')
    }
  })

  it('shows selectable cached windows hours later, removing windows whose start passed', () => {
    const html: string = screen(quote)
    expect(html).toContain('aria-label="Delivery date"')
    expect(html).toContain('4–6 PM')
    expect(html).not.toContain('1–3 PM')
    expect(html).not.toContain('Checking delivery and total')
  })

  it('offers recovery when the check fails', () => {
    const html: string = screen({ ...quote, availability: null, availability_current: false, job: { ...quote.job, status: 'failed', error: 'Store unavailable' } })
    expect(html).toContain('Try again')
    expect(html).toContain("couldn&#x27;t check delivery")
    expect(html).not.toContain('role="timer"')
    expect(html).not.toContain('Notify me')
    expect(html).not.toContain('aria-label="Delivery date"')
  })

  it.each(['succeeded', 'failed'] as const)('offers an explicit retry after inputs changed during a check that %s', (status) => {
    const html: string = screen({ ...quote, current: false, job: { ...quote.job, status } })
    expect(html).toContain('Your order changed during the check.')
    expect(html).toContain('Try again')
    expect(html).not.toContain('role="timer"')
    expect(html).not.toContain('aria-label="Delivery date"')
  })

  it('does not promise notifications when this phone cannot receive them', () => {
    vi.mocked(useNotifiedOfJob).mockReturnValue(false)
    const html: string = screen({ ...quote, job: { ...quote.job, status: 'running' }, availability_current: false })
    expect(html).toContain('You can close this')
    expect(html).not.toContain('Notify me')
    expect(html).not.toContain('notify you when')
  })
})
