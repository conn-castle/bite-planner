import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'
import type { GroceryOrder, Meal, MealSummary } from '../../lib/api/types'
import { queryKeys } from '../../lib/query/keys'
import { MealPage } from '../meals/MealPage'
import { OrderSession } from './lock'
import { PickStep } from './PickStep'

vi.mock('../../lib/api/client', () => ({ apiRequest: vi.fn() }))
vi.mock('../../lib/auth/auth', () => ({ auth: { getUser: vi.fn() } }))
vi.mock('../../lib/log', () => ({
  logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
  errorFields: vi.fn(() => ({})),
}))

const summary: MealSummary = {
  id: 'chicken',
  name: 'Roast chicken',
  description: null,
  active_time_minutes: 10,
  total_time_minutes: 50,
  ingredient_count: 2,
  rating: null,
  picture: { dish: 'plate', ingredients: [{ icon: 'drumstick', color: 'tan' }] },
}

function order(meal: MealSummary, added: boolean): GroceryOrder {
  return {
    id: 'draft', number: 4, status: 'draft',
    created_by: { user_id: 'nick', name: 'Nick' }, created_at: '2026-10-13T12:00:00Z',
    target_date: '2026-10-16', decide_by_date: '2026-10-15', reminders_start_date: null,
    earliest_delivery_date: '2026-10-14', delivery_date: null, delivery_by: null,
    next_delivery_date: null, reached_step: 'pick', picks: added ? [{ meal_id: meal.id, servings: 4 }] : [],
    lock: null, shopping_list: null, quote: null, best_window: null, fallback_window: null,
    book_state: null, book_on: null, booking: null, book_issue: null,
    jobs: [{
      id: 'batch', kind: 'generate_meals', status: 'succeeded', error: null,
      requested_by: { user_id: 'nick', name: 'Nick' }, created_at: '2026-10-13T12:00:00Z', meals: [meal],
    }],
  }
}

function pickScreen(meal: MealSummary, added = true): string {
  const queryClient: QueryClient = new QueryClient()
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <OrderSession.Provider value="phone">
        <PickStep order={order(meal, added)} onOpenMeal={() => {}} onNext={() => {}} onClose={() => {}} />
      </OrderSession.Provider>
    </QueryClientProvider>,
  )
}

interface ShownFact {
  value: string
  label: string
  icon: boolean
}

/** Read each prominent value and its description within the same rendered cell. */
function cardFacts(html: string): ShownFact[] {
  return Array.from(html.matchAll(/<b\b[^>]*>([\s\S]*?)<\/b><small\b[^>]*>([^<]*)<\/small>/g), (cell): ShownFact => ({
    value: cell[1].replace(/<[^>]*>/g, ''),
    label: cell[2],
    icon: /<svg\b[^>]*>(?:(?!<\/svg>)[\s\S])*<\/svg>/.test(cell[1]),
  }))
}

describe('Pick meal cards', () => {
  it('show three distinct icon/value/description cells, keeping illustrated artwork and the selected action', () => {
    const html: string = pickScreen(summary)
    expect(cardFacts(html)).toEqual([
      { value: '2', label: 'Ingredients', icon: true },
      { value: '50 min', label: 'Total', icon: true },
      { value: '10 min', label: 'Hands-on', icon: true },
    ])
    expect(html).toContain('aria-label="Remove Roast chicken"')
    expect(html).toContain('aria-pressed="true"')
    expect(html).toContain('Added')
    expect(html).toContain('<svg viewBox="0 0 64 64"')
    // An illustrated meal contains ingredient groups; the legacy plate contains only circles.
    expect(html).toMatch(/<svg viewBox="0 0 64 64"[^>]*>(?:(?!<\/svg>)[\s\S])*?<g\b[^>]*>(?:(?!<\/svg>)[\s\S])*?<(?:path|rect|circle)\b/)
  })

  it.each([
    { total_time_minutes: null, active_time_minutes: 10, total: '—', active: '10 min' },
    { total_time_minutes: 50, active_time_minutes: null, total: '50 min', active: '—' },
    { total_time_minutes: null, active_time_minutes: null, total: '—', active: '—' },
  ])('identify each missing time without substituting the other ($total, $active)', (times) => {
    const html: string = pickScreen({ ...summary, ...times })
    expect(cardFacts(html)).toEqual([
      { value: '2', label: 'Ingredients', icon: true },
      { value: times.total, label: 'Total', icon: true },
      { value: times.active, label: 'Hands-on', icon: true },
    ])
    // The visible symbol has an explicit accessible meaning for each missing fact.
    expect(html.includes('role="img" aria-label="Total time not listed"')).toBe(times.total_time_minutes === null)
    expect(html.includes('role="img" aria-label="Hands-on time not listed"')).toBe(times.active_time_minutes === null)
  })

  it('keeps minutes numeric even above an hour and treats recorded zero values as available', () => {
    expect(cardFacts(pickScreen({ ...summary, total_time_minutes: 125, active_time_minutes: 5 })).map((fact) => fact.value)).toEqual(['2', '125 min', '5 min'])
    expect(cardFacts(pickScreen({ ...summary, ingredient_count: 0, total_time_minutes: 0, active_time_minutes: 0 })).map((fact) => fact.value)).toEqual(['0', '0 min', '0 min'])
  })

  it('keeps Add and not-for-us actions, with the legacy plate when a meal has no picture', () => {
    const add: string = pickScreen(summary, false)
    expect(add).toContain('aria-label="Add Roast chicken"')
    expect(add).toContain('aria-pressed="false"')
    const dismissed: string = pickScreen({ ...summary, rating: 'not_for_us', picture: null }, false)
    expect(dismissed).toContain('No more meals like this')
    expect(dismissed).toContain('aria-label="Show meals like Roast chicken again"')
    expect(dismissed).toMatch(/disabled=""[^>]*aria-pressed="false"/)
    expect(dismissed).toContain('<svg viewBox="0 0 64 64"')
    expect(cardFacts(dismissed)).toHaveLength(3)
  })
})

it('shows the same ingredient count on the full recipe while keeping all ingredient lines and recipe servings', () => {
  const queryClient: QueryClient = new QueryClient()
  const recipe: Meal = {
    ...summary, servings: 2, steps: [{ text: 'Roast.', timer_minutes: 30, ingredients: [0] }],
    ingredients: [
      { name: 'chicken', quantity: 1, unit: 'lb', preparation: null },
      { name: 'garlic', quantity: 2, unit: null, preparation: 'minced' },
      { name: 'salt', quantity: null, unit: null, preparation: null },
    ],
  }
  queryClient.setQueryData(queryKeys.meal(recipe.id), recipe)
  const html: string = renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MealPage mealId={recipe.id} servings={null} picking />
    </QueryClientProvider>,
  )
  expect(html).toMatch(/<dt[^>]*>ingredients<\/dt><dd[^>]*><span[^>]*>2<\/span><\/dd>/)
  expect(html).toContain('salt')
  expect(html).toContain('This recipe is for 2 servings')
})
