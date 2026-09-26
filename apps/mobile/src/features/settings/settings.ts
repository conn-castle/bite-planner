import { type QueryClient, queryOptions, useMutation, useMutationState, useQuery } from '@tanstack/react-query'
import { apiRequest } from '../../lib/api/client'
import { isRetryable } from '../../lib/api/errors'
import { auth } from '../../lib/auth/auth'
import type { ClockTime, Household, HouseholdSettings, PersonalSettings } from '../../lib/api/types'
import { isRecord } from '../../lib/isRecord'
import { queryKeys } from '../../lib/query/keys'
import { type Session, sessionQuery, useSession } from '../session/session'

/**
 * The household's settings (shared by everyone in it) and each member's own.
 * A change shows at once: the cache holds only what the server sent, and the
 * screen shows it with the changes still being saved laid over it, in the order
 * they were made (like order edits; see orders/edits.ts). So an answer that
 * arrives while newer taps are still on their way can't undo them, and a
 * change that fails stops being shown, with why it failed shown until that
 * setting is saved (useHouseholdError, usePersonalError).
 */

const CHANGE_HOUSEHOLD = ['household', 'change']
const CHANGE_PERSONAL = ['personal-settings', 'change']
const CHANGE_NAME = ['user', 'change-name']

// Changes are sent one at a time, in the order they were made, so the last tap
// is the one the server keeps.
const HOUSEHOLD_CHANGES = { id: 'household-settings' }
const PERSONAL_CHANGES = { id: 'personal-settings' }
const NAME_CHANGES = { id: 'user-name' }

/** One or more household settings (or its name) to change; the others stay as they are. */
export type HouseholdChange = Partial<HouseholdSettings & Pick<Household, 'name'>>

/**
 * A member's own setting to change: their reminder time, or whether they get
 * daily reminders at all (their time zone is kept up to date by the session;
 * see session.ts).
 */
export type PersonalChange = { notification_time: ClockTime } | { reminders_on: boolean }

const HOUSEHOLD_SETTINGS: readonly (keyof HouseholdSettings)[] = [
  'decide_by_offset_days',
  'reminder_lead_days',
  'default_servings',
]

/** Whether a pending mutation's variables are a household change (they are stored untyped). */
function isHouseholdChange(value: unknown): value is HouseholdChange {
  return (
    isRecord(value) &&
    Object.entries(value).every(([key, setting]) =>
      key === 'name'
        ? typeof setting === 'string'
        : HOUSEHOLD_SETTINGS.some((name) => name === key) && typeof setting === 'number',
    )
  )
}

/** A change of the signed-in user's own name (stored by the sign-in provider only). */
export interface NameChange {
  name: string
}

function isNameChange(value: unknown): value is NameChange {
  return isRecord(value) && typeof value.name === 'string'
}

function isPersonalChange(value: unknown): value is PersonalChange {
  if (!isRecord(value)) return false
  if ('reminders_on' in value) return typeof value.reminders_on === 'boolean'
  return typeof value.notification_time === 'string' && /^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$/.test(value.notification_time)
}

export const householdQuery = queryOptions({
  queryKey: queryKeys.household,
  queryFn: () => apiRequest<Household>('GET', '/v1/household'),
  // Another member may have changed them: checked at every launch and return to the foreground.
  staleTime: 0,
})

/** How settings changes are sent and stored; registered at startup, like the order requests (see orders.ts). */
export function registerSettingsMutations(queryClient: QueryClient) {
  const retry = (failureCount: number, error: Error) => isRetryable(error, failureCount)

  queryClient.setMutationDefaults(CHANGE_HOUSEHOLD, {
    mutationFn: (change: HouseholdChange) => apiRequest<Household>('PATCH', '/v1/household', change),
    retry,
    scope: HOUSEHOLD_CHANGES,
    onSuccess: async (household: Household, change: HouseholdChange) => {
      // A fetch already on its way was read before this change: stop it, or it lands on top.
      await queryClient.cancelQueries({ queryKey: queryKeys.household })
      queryClient.setQueryData(queryKeys.household, household)
      // The session carries the household's name too (the page's overline).
      if (change.name !== undefined) {
        await queryClient.cancelQueries({ queryKey: sessionQuery.queryKey })
        queryClient.setQueryData<Session | null>(
          sessionQuery.queryKey,
          (session) =>
            session?.household ? { ...session, household: { ...session.household, name: household.name } } : session,
        )
      }
      // Decide-by and reminder dates are computed from the offset and the lead.
      if (change.decide_by_offset_days !== undefined || change.reminder_lead_days !== undefined) {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: queryKeys.nextOrder }),
          queryClient.invalidateQueries({ queryKey: queryKeys.orders }),
        ])
      }
    },
    // Refused: show the settings as the server has them (the page says why).
    onError: () => queryClient.invalidateQueries({ queryKey: queryKeys.household }),
  })

  queryClient.setMutationDefaults(CHANGE_PERSONAL, {
    mutationFn: (change: PersonalChange) => apiRequest<PersonalSettings>('PATCH', '/v1/me/settings', change),
    retry,
    scope: PERSONAL_CHANGES,
    onSuccess: async (settings: PersonalSettings) => {
      await queryClient.cancelQueries({ queryKey: sessionQuery.queryKey })
      queryClient.setQueryData<Session | null>(sessionQuery.queryKey, (session) => session && { ...session, settings })
    },
    onError: () => queryClient.invalidateQueries({ queryKey: sessionQuery.queryKey }),
  })

  queryClient.setMutationDefaults(CHANGE_NAME, {
    mutationFn: ({ name }: NameChange) => auth.updateName(name),
    retry,
    scope: NAME_CHANGES,
    // Everything showing a person's name reads it from the provider: fetch them again.
    onSuccess: () =>
      Promise.all(
        [sessionQuery.queryKey, queryKeys.household, queryKeys.nextOrder, queryKeys.orders, queryKeys.placedOrders].map(
          (queryKey) => queryClient.invalidateQueries({ queryKey }),
        ),
      ),
  })
}

/** The household's settings and members, with the changes still being saved applied. */
export function useHousehold() {
  const query = useQuery(householdQuery)
  const changes = useMutationState({
    filters: { mutationKey: CHANGE_HOUSEHOLD, status: 'pending' },
    select: (mutation) => mutation.state.variables,
  }).filter(isHouseholdChange)
  return { ...query, shown: query.data && changes.reduce<Household>((shown, change) => ({ ...shown, ...change }), query.data) }
}

/** The member's own settings, with the changes still being saved applied; null when not in a household. */
export function usePersonalSettings(): PersonalSettings | null {
  const settings = useSession().data?.settings ?? null
  const changes = useMutationState({
    filters: { mutationKey: CHANGE_PERSONAL, status: 'pending' },
    select: (mutation) => mutation.state.variables,
  }).filter(isPersonalChange)
  return settings && changes.reduce<PersonalSettings>((shown, change) => ({ ...shown, ...change }), settings)
}

export function useChangeHousehold() {
  return useMutation<Household, Error, HouseholdChange>({ mutationKey: CHANGE_HOUSEHOLD })
}

/** The signed-in user's name, with a change still being saved applied. */
export function useUserName(): string | null {
  const name = useSession().data?.user.name ?? null
  const changes = useMutationState({
    filters: { mutationKey: CHANGE_NAME, status: 'pending' },
    select: (mutation) => mutation.state.variables,
  }).filter(isNameChange)
  return changes.at(-1)?.name ?? name
}

export function useChangeName() {
  return useMutation<void, Error, NameChange>({ mutationKey: CHANGE_NAME })
}

export function useChangePersonal() {
  return useMutation<PersonalSettings, Error, PersonalChange>({ mutationKey: CHANGE_PERSONAL })
}

/**
 * Why a setting couldn't be saved, while its latest change is one that
 * failed: a later change to another setting doesn't hide it.
 */
function useFailure(mutationKey: readonly string[]): Error | null {
  const latest = new Map<string, Error | null>()
  for (const { variables, error } of useMutationState({
    filters: { mutationKey },
    select: (mutation) => ({ variables: mutation.state.variables, error: mutation.state.error }),
  })) {
    if (isRecord(variables)) for (const setting of Object.keys(variables)) latest.set(setting, error)
  }
  return [...latest.values()].find((error) => error !== null) ?? null
}

export const useHouseholdError = () => useFailure(CHANGE_HOUSEHOLD)
export const usePersonalError = () => useFailure(CHANGE_PERSONAL)
export const useNameError = () => useFailure(CHANGE_NAME)
