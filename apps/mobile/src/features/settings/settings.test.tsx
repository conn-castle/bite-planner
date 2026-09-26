import { MutationObserver, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToString } from 'react-dom/server'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { apiRequest } from '../../lib/api/client'
import { auth } from '../../lib/auth/auth'
import { ApiError } from '../../lib/api/errors'
import type { Household, PersonalSettings } from '../../lib/api/types'
import { queryKeys } from '../../lib/query/keys'
import { type Session, sessionQuery } from '../session/session'
import {
  type HouseholdChange,
  type NameChange,
  householdQuery,
  type PersonalChange,
  registerSettingsMutations,
  useHousehold,
  useHouseholdError,
  usePersonalSettings,
} from './settings'

vi.mock('../../lib/api/client', () => ({ apiRequest: vi.fn() }))
vi.mock('../../lib/log', () => ({ logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } }))
// session.ts (imported for its query key) signs in through the real provider otherwise.
vi.mock('../../lib/auth/auth', () => ({ auth: { updateName: vi.fn() } }))
vi.mock('../../lib/logUpload', () => ({ uploadLogs: vi.fn() }))
vi.mock('../../lib/query/queryClient', () => ({ persister: {} }))
const request = vi.mocked(apiRequest)

const household = (settings: Partial<Household> = {}): Household => ({
  id: 'castle',
  name: 'Conn Castle',
  decide_by_offset_days: 1,
  reminder_lead_days: 2,
  default_servings: 4,
  members: [{ user_id: 'sam', name: 'Sam', email: 'sam@example.com' }],
  ...settings,
})

let queryClient: QueryClient

beforeEach(() => {
  queryClient = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
  registerSettingsMutations(queryClient)
})

afterEach(() => request.mockReset())

const changeHousehold = (change: HouseholdChange) =>
  new MutationObserver<Household, Error, HouseholdChange>(queryClient, { mutationKey: ['household', 'change'] }).mutate(
    change,
  )

const changePersonal = (change: PersonalChange) =>
  new MutationObserver<PersonalSettings, Error, PersonalChange>(queryClient, {
    mutationKey: ['personal-settings', 'change'],
  }).mutate(change)

const later = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
const never = () => new Promise<never>(() => {})

interface Shown {
  household?: Household
  error?: string
  personal: PersonalSettings | null
}

function Probe({ report }: { report: (shown: Shown) => void }) {
  report({ household: useHousehold().shown, error: useHouseholdError()?.message, personal: usePersonalSettings() })
  return null
}

/** What the Settings screen shows now: the hooks rendered once. */
function onScreen(): Shown {
  let shown: Shown = { personal: null }
  renderToString(
    <QueryClientProvider client={queryClient}>
      <Probe report={(rendered) => (shown = rendered)} />
    </QueryClientProvider>,
  )
  return shown
}

describe('the settings on screen', () => {
  it('show the newest changes still being saved over what the server sent', async () => {
    queryClient.setQueryData(queryKeys.household, household())
    request.mockImplementation(never)

    void changeHousehold({ reminder_lead_days: 3 })
    void changeHousehold({ reminder_lead_days: 4 })
    void changeHousehold({ default_servings: 6 })
    await later(0)

    const { household: shown } = onScreen()
    expect([shown?.decide_by_offset_days, shown?.reminder_lead_days, shown?.default_servings]).toEqual([1, 4, 6])
  })

  it("go back to the server's when a change is refused, which stays said until that setting is saved", async () => {
    queryClient.setQueryData(queryKeys.household, household())
    request.mockRejectedValueOnce(new ApiError(422, 'Out of range.'))
    await changeHousehold({ reminder_lead_days: 9 }).catch(() => undefined)

    expect(onScreen().household?.reminder_lead_days).toBe(2)
    expect(onScreen().error).toBe('Out of range.')

    // Another setting saved meanwhile doesn't hide it.
    request.mockResolvedValueOnce(household({ default_servings: 6 }))
    await changeHousehold({ default_servings: 6 })
    expect(onScreen().error).toBe('Out of range.')

    request.mockResolvedValueOnce(household({ default_servings: 6, reminder_lead_days: 3 }))
    await changeHousehold({ reminder_lead_days: 3 })
    expect(onScreen().error).toBeUndefined()
  })

  it('show a new notification time at once, and keep it with the session once saved', async () => {
    const settings: PersonalSettings = { notification_time: '08:00:00', time_zone: 'Europe/Lisbon' }
    queryClient.setQueryData<Session>(sessionQuery.queryKey, {
      user: { id: 'sam', email: 'sam@example.com', name: 'Sam' },
      household: { id: 'castle', name: 'Conn Castle' },
      settings,
    })
    let answer = (_saved: PersonalSettings) => {}
    request.mockImplementation(() => new Promise((resolve) => (answer = resolve)))

    const saving = changePersonal({ notification_time: '06:30' })
    await later(0)
    expect(onScreen().personal?.notification_time).toBe('06:30')

    answer({ ...settings, notification_time: '06:30:00' })
    await saving
    expect(request.mock.calls).toEqual([['PATCH', '/v1/me/settings', { notification_time: '06:30' }]])
    expect(queryClient.getQueryData<Session>(sessionQuery.queryKey)?.settings?.notification_time).toBe('06:30:00')
    expect(onScreen().personal?.notification_time).toBe('06:30:00')
  })
})

describe('changing a household setting', () => {
  it('sends only that setting, and keeps the household as the server answers', async () => {
    request.mockResolvedValue(household({ reminder_lead_days: 0 }))

    await changeHousehold({ reminder_lead_days: 0 })

    expect(request.mock.calls).toEqual([['PATCH', '/v1/household', { reminder_lead_days: 0 }]])
    expect(queryClient.getQueryData<Household>(queryKeys.household)?.reminder_lead_days).toBe(0)
  })

  it('sends quick taps in order, so the last one is what the server keeps and the app shows', async () => {
    const sent: number[] = []
    request.mockImplementation(async (_method, _path, body) => {
      const { default_servings: servings } = body as HouseholdChange
      sent.push(servings ?? 0)
      // The first answer is the slowest.
      await later(servings === 5 ? 20 : 0)
      return household({ default_servings: servings })
    })

    await Promise.all([changeHousehold({ default_servings: 5 }), changeHousehold({ default_servings: 6 })])

    expect(sent).toEqual([5, 6])
    expect(queryClient.getQueryData<Household>(queryKeys.household)?.default_servings).toBe(6)
  })

  it("isn't overwritten by a fetch that was already on its way", async () => {
    request.mockImplementation(async (method) => {
      if (method === 'GET') {
        await later(20)
        return household()
      }
      return household({ decide_by_offset_days: 3 })
    })
    const fetching = queryClient.fetchQuery(householdQuery).catch(() => undefined)

    await changeHousehold({ decide_by_offset_days: 3 })
    await fetching

    expect(queryClient.getQueryData<Household>(queryKeys.household)?.decide_by_offset_days).toBe(3)
  })

  it('refreshes the decide-by dates when the offset changes, and only then', async () => {
    queryClient.setQueryData(queryKeys.nextOrder, { number: 2 })
    request.mockImplementation(async (_method, _path, body) => household(body as HouseholdChange))

    await changeHousehold({ default_servings: 2 })
    expect(queryClient.getQueryState(queryKeys.nextOrder)?.isInvalidated).toBe(false)

    await changeHousehold({ decide_by_offset_days: 2 })
    expect(queryClient.getQueryState(queryKeys.nextOrder)?.isInvalidated).toBe(true)
  })

  it('shows the settings as the server has them when a change is refused', async () => {
    queryClient.setQueryData(queryKeys.household, household())
    request.mockRejectedValue(new ApiError(422, 'Out of range.'))

    await changeHousehold({ reminder_lead_days: 9 }).catch(() => undefined)

    expect(queryClient.getQueryState(queryKeys.household)?.isInvalidated).toBe(true)
  })
})

describe('renaming', () => {
  const session: Session = {
    user: { id: 'sam', email: 'sam@example.com', name: 'Sam' },
    household: { id: 'castle', name: 'Conn Castle' },
    settings: { notification_time: '08:00:00', time_zone: 'Europe/Lisbon' },
  }

  it("the household shows its new name everywhere the session shows it", async () => {
    queryClient.setQueryData<Session>(sessionQuery.queryKey, session)
    request.mockResolvedValue(household({ name: 'The Conns' }))

    await changeHousehold({ name: 'The Conns' })

    expect(queryClient.getQueryData<Session>(sessionQuery.queryKey)?.household?.name).toBe('The Conns')
    expect(queryClient.getQueryData<Household>(queryKeys.household)?.name).toBe('The Conns')
  })

  it('your own name is saved with the sign-in provider, and everything showing names is fetched again', async () => {
    for (const key of [sessionQuery.queryKey, queryKeys.household, queryKeys.nextOrder, queryKeys.placedOrdersFrom()]) {
      queryClient.setQueryData(key, {})
    }

    await new MutationObserver<void, Error, NameChange>(queryClient, { mutationKey: ['user', 'change-name'] }).mutate({
      name: 'Samantha',
    })

    expect(vi.mocked(auth.updateName)).toHaveBeenCalledWith('Samantha')
    expect(request).not.toHaveBeenCalled()
    for (const key of [sessionQuery.queryKey, queryKeys.household, queryKeys.nextOrder, queryKeys.placedOrdersFrom()]) {
      expect(queryClient.getQueryState(key)?.isInvalidated).toBe(true)
    }
  })
})
