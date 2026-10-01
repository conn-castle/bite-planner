import {
  MutationObserver,
  type QueryClient,
  queryOptions,
  skipToken,
  useIsMutating,
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { apiRequest } from '../../lib/api/client'
import { ApiError, isRetryable } from '../../lib/api/errors'
import type {
  ClockTime,
  GroceryOrder,
  ISODate,
  Job,
  NextOrder,
  OrderLock,
  OrderStep,
  PlacedOrder,
} from '../../lib/api/types'
import { logger } from '../../lib/log'
import { queryKeys } from '../../lib/query/keys'
import { storeMealRating } from '../meals/ratings'
import { hasRunningBatch } from './batches'
import {
  applyEdits,
  type InSession,
  isOrderEdit,
  type SetDates,
  type SetItem,
  type SetMeal,
  type SetNotForUs,
  type SetServings,
} from './edits'
import { LOCK_RENEW_MS } from './lock'

/**
 * Grocery orders: the household's next order and its draft, everything the
 * create-order flow changes on it, and the placed orders.
 */

// How often an open order is checked while a batch of suggestions is being made.
const POLL_MS = 3_000

export const nextQuery = queryOptions({
  queryKey: queryKeys.nextOrder,
  queryFn: () => apiRequest<NextOrder>('GET', '/v1/grocery-orders/next'),
})

/**
 * The household's placed orders, oldest first: those whose meals cover
 * `coveringFrom` or a later day, or all of them (History).
 */
export const placedQuery = (coveringFrom?: ISODate) =>
  queryOptions({
    queryKey: queryKeys.placedOrdersFrom(coveringFrom),
    queryFn: () =>
      apiRequest<PlacedOrder[]>(
        'GET',
        coveringFrom ? `/v1/grocery-orders?covering_from=${encodeURIComponent(coveringFrom)}` : '/v1/grocery-orders',
      ),
  })

const orderKey = queryKeys.order

/**
 * The household's next order, with its draft once someone has started one;
 * checked every `refetchInterval` ms if given (Home, to see who is ordering).
 */
export function useNextOrder(refetchInterval: number | false = false) {
  return useQuery({ ...nextQuery, refetchInterval })
}

/** The household's placed orders whose meals cover `coveringFrom` or a later day, or all of them. */
export function usePlacedOrders(coveringFrom?: ISODate) {
  return useQuery(placedQuery(coveringFrom))
}

/**
 * One order as the server has it, checked every few seconds while a batch of
 * suggestions is being made, and `shown`: that order with the changes still
 * being saved applied over it (see edits.ts). In the order flow (its ordering
 * `session` given), it's also checked every 30 seconds while someone else has
 * its lock, to see when they're done.
 */
export function useOrder(orderId: string | undefined, session?: string) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: orderKey(orderId ?? ''),
    // The draft is usually cached already, as the next order: shown at once while
    // the order itself loads (a placeholder, never stored as the order).
    placeholderData: () => {
      const draft = queryClient.getQueryData<NextOrder>(nextQuery.queryKey)?.draft
      return draft && draft.id === orderId ? draft : undefined
    },
    queryFn: orderId ? () => apiRequest<GroceryOrder>('GET', `/v1/grocery-orders/${orderId}`) : skipToken,
    refetchInterval: ({ state: { data } }) =>
      data && hasRunningBatch(data)
        ? POLL_MS
        : session !== undefined && data?.lock && data.lock.session !== session
          ? LOCK_RENEW_MS
          : false,
    // A 404 (the draft was discarded) won't change on a retry.
    retry: (failureCount, error) => isRetryable(error, failureCount),
  })
  const edits = useMutationState({
    filters: { status: 'pending', predicate: isEdit },
    select: (mutation) => mutation.state.variables,
  }).filter(isOrderEdit)
  return { ...query, shown: query.data && applyEdits(query.data, edits) }
}

/** Whether changes to an order are still being saved; moving on or placing waits for them. */
export function useEditsPending(): boolean {
  return useIsMutating({ predicate: isEdit }) > 0
}

/**
 * Stops fetches of an order (and the next order) that are already on their way:
 * they started before a change, so their answer is older than its result.
 * Cancelling reverts to the data from before the fetch, so it comes before
 * the result is stored.
 */
async function cancelFetches(queryClient: QueryClient, orderId: string) {
  await Promise.all([
    queryClient.cancelQueries({ queryKey: orderKey(orderId) }),
    queryClient.cancelQueries({ queryKey: nextQuery.queryKey }),
  ])
}

/**
 * Keeps both views of a draft current: the order itself, and the next order,
 * which is the draft (its number and dates are the draft's own).
 */
async function storeOrder(queryClient: QueryClient, order: GroceryOrder) {
  await cancelFetches(queryClient, order.id)
  queryClient.setQueryData<GroceryOrder>(orderKey(order.id), order)
  queryClient.setQueryData<NextOrder>(nextQuery.queryKey, {
    number: order.number,
    target_date: order.target_date,
    decide_by_date: order.decide_by_date,
    reminders_start_date: order.reminders_start_date,
    earliest_delivery_date: order.earliest_delivery_date,
    draft: order,
  })
}

/** Fetches again everything an order that may have just been placed shows in. */
function refetchAfterPlaced(queryClient: QueryClient, orderId: string) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
    queryClient.invalidateQueries({ queryKey: nextQuery.queryKey }),
    queryClient.invalidateQueries({ queryKey: queryKeys.placedOrders }),
  ])
}

const OPEN = ['orders', 'open']
const GENERATE = ['orders', 'generate']
const SET_MEAL = ['orders', 'set-meal']
const SET_SERVINGS = ['orders', 'set-servings']
const SET_DATES = ['orders', 'set-dates']
const SET_NOT_FOR_US = ['orders', 'set-not-for-us']
const PLACE = ['orders', 'place']
const DISCARD = ['orders', 'discard']
const START_LIST = ['orders', 'start-list']
const SHOP_LIST = ['orders', 'shop-list']
const QUOTE = ['orders', 'quote']
const SET_ITEM = ['orders', 'set-item']
const TAKE = ['orders', 'take']
const RELEASE = ['orders', 'release']

// Requests that only make sense while the flow that made them is open: never
// saved to be sent after a relaunch (they'd take the lock with no flow open).
export const NOT_PERSISTED = { persist: false }

// Ordering sessions whose flow has closed: nothing takes the lock for them any more.
const closedSessions = new Set<string>()

/** Taking the lock (or opening) for a flow that has closed meanwhile: not sent. */
export class FlowClosed extends Error {
  constructor() {
    super('The order flow was closed.')
  }
}

// Changes to an order run one at a time, in the order they were made, so each
// response (the whole order) includes every earlier change, and later ones
// still being saved are applied over it. Opening, placing, and discarding the
// draft take their turn too.
const EDITS = { id: 'grocery-order-edits' }

export interface OpenOrder extends InSession {
  /** For the first batch, if the draft has none; chosen here so a resent request can't start a second. */
  jobId: string
}
export interface GenerateMeals extends InSession {
  orderId: string
  /** Chosen here, so a resent request (e.g. after going offline) can't start a second batch. */
  jobId: string
}
export interface StartList extends InSession {
  orderId: string
  /** Chosen here, so a resent request can't start a second build. */
  jobId: string
}
export interface OrderAction {
  orderId: string
}
/** The dates and latest time an order is placed with: all of them set. */
export interface PlacedDates {
  delivery_date: ISODate
  delivery_by: ClockTime
  next_delivery_date: ISODate
}
export interface PlaceOrder extends InSession {
  orderId: string
  /** The dates and time shown when Place was tapped (they may be untouched defaults), saved with it. */
  dates: PlacedDates
  /** The quote whose total was shown; the server refuses it if it's no longer the order's current one. */
  quoteId: string
}

async function startBatch(orderId: string, jobId: string, session: string): Promise<Job> {
  const job = await apiRequest<Job>('POST', `/v1/grocery-orders/${orderId}/jobs`, { job_id: jobId }, session)
  logger.info('orders.batch_started', { orderId, jobId: job.id })
  return job
}

const withJob = (order: GroceryOrder, job: Job): GroceryOrder => ({
  ...order,
  jobs: [...order.jobs.filter((existing) => existing.id !== job.id), job],
})

const isEdit = (mutation: { options: { scope?: { id: string } } }) => mutation.options.scope?.id === EDITS.id

/**
 * How each order request is sent and stored. Registered at startup rather than
 * in the hooks: a request made offline is persisted, and after a relaunch it is
 * restored without the hooks' options, yet still needs to know how to send
 * itself and where its answer goes.
 */
export function registerOrderMutations(queryClient: QueryClient) {
  const retry = (failureCount: number, error: Error) => !(error instanceof FlowClosed) && isRetryable(error, failureCount)

  queryClient.setMutationDefaults(OPEN, {
    // Gets or creates the household's draft, taking its lock (unless another
    // member has it), and starts its first batch if it has none.
    mutationFn: async ({ jobId, session }: OpenOrder): Promise<GroceryOrder> => {
      if (closedSessions.has(session)) throw new FlowClosed()
      const order = await apiRequest<GroceryOrder>('POST', '/v1/grocery-orders', undefined, session)
      if (order.jobs.length > 0 || order.lock?.session !== session) return order
      try {
        return withJob(order, await startBatch(order.id, jobId, session))
      } catch (error) {
        // Someone started its batch a moment earlier (429), or took the lock (423): show it as it is.
        if (!(error instanceof ApiError && (error.status === 429 || error.status === 423))) throw error
        return apiRequest<GroceryOrder>('GET', `/v1/grocery-orders/${order.id}`)
      }
    },
    retry,
    meta: NOT_PERSISTED,
    // Changes made while it opens (the cached draft is on screen) are saved after it,
    // so its answer (the draft as it was) never lands on top of theirs.
    scope: EDITS,
    onSuccess: async (order: GroceryOrder, { session }: OpenOrder) => {
      await storeOrder(queryClient, order)
      // Closed while it opened: the lock it took goes at once.
      if (closedSessions.has(session) && order.lock?.session === session) {
        releaseLock(queryClient, { orderId: order.id, session })
      }
    },
  })

  queryClient.setMutationDefaults(GENERATE, {
    mutationFn: ({ orderId, jobId, session }: GenerateMeals) => startBatch(orderId, jobId, session),
    retry,
    onSuccess: async (job: Job, { orderId }: GenerateMeals) => {
      await cancelFetches(queryClient, orderId)
      queryClient.setQueryData<GroceryOrder>(orderKey(orderId), (order) => order && withJob(order, job))
    },
    // A refusal (e.g. someone else's batch is already running) means the order changed.
    onError: (_error: Error, { orderId }: GenerateMeals) =>
      queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  queryClient.setMutationDefaults(SET_MEAL, {
    mutationFn: ({ orderId, mealId, picked, session }: SetMeal) =>
      apiRequest<GroceryOrder>(
        picked ? 'PUT' : 'DELETE',
        `/v1/grocery-orders/${orderId}/meals/${mealId}`,
        undefined,
        session,
      ),
    retry,
    scope: EDITS,
    onSuccess: (order: GroceryOrder) => storeOrder(queryClient, order),
    // Refused (e.g. someone placed the order meanwhile): show the order as it is now.
    onError: (_error: Error, { orderId }: SetMeal) => queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  queryClient.setMutationDefaults(SET_SERVINGS, {
    mutationFn: ({ orderId, mealId, servings, session }: SetServings) =>
      apiRequest<GroceryOrder>(
        'PUT',
        `/v1/grocery-orders/${orderId}/meals/${mealId}/servings`,
        { servings },
        session,
      ),
    retry,
    scope: EDITS,
    onSuccess: (order: GroceryOrder) => storeOrder(queryClient, order),
    // Refused (e.g. the meal was just unpicked on another phone): show the order as it is now.
    onError: (_error: Error, { orderId }: SetServings) =>
      queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  queryClient.setMutationDefaults(SET_NOT_FOR_US, {
    // The mark is the household's rating of the meal; the server also unpicks
    // it. Its answer has no order, so the order is fetched after it.
    mutationFn: async ({ orderId, mealId, notForUs, session }: SetNotForUs) => {
      if (notForUs) await apiRequest<void>('PUT', `/v1/meals/${mealId}/rating`, { rating: 'not_for_us' }, session)
      else await apiRequest<void>('DELETE', `/v1/meals/${mealId}/rating`)
      return apiRequest<GroceryOrder>('GET', `/v1/grocery-orders/${orderId}`)
    },
    retry,
    scope: EDITS,
    onSuccess: async (order: GroceryOrder, { mealId, notForUs }: SetNotForUs) => {
      await storeMealRating(queryClient, { mealId, rating: notForUs ? 'not_for_us' : null })
      // Someone placed it meanwhile (a rating can still be saved then): it's no
      // longer the draft, so it isn't stored as one.
      if (order.status === 'draft') await storeOrder(queryClient, order)
      else await refetchAfterPlaced(queryClient, order.id)
    },
    // Refused, or the rating was saved but the order couldn't be fetched: show
    // everything as the server has it now.
    onError: (_error: Error, { orderId }: SetNotForUs) => refetchAfterPlaced(queryClient, orderId),
  })

  queryClient.setMutationDefaults(START_LIST, {
    // Builds the list from the picks and servings now (after the changes to them
    // before it); a build for the same picks already made or being made is kept.
    mutationFn: async ({ orderId, jobId, session }: StartList): Promise<Job> => {
      const job = await apiRequest<Job>('POST', `/v1/grocery-orders/${orderId}/list`, { job_id: jobId }, session)
      logger.info('orders.list_started', { orderId, jobId: job.id, status: job.status })
      return job
    },
    retry,
    scope: EDITS,
    // The order's list is its newest build: fetch the order to show it.
    onSettled: (_job: Job | undefined, _error: Error | null, { orderId }: StartList) =>
      queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  queryClient.setMutationDefaults(SHOP_LIST, {
    // Shops the current list again (after shopping failed); shopping already on
    // its way for it is kept.
    mutationFn: async ({ orderId, jobId, session }: StartList): Promise<Job> => {
      const job = await apiRequest<Job>('POST', `/v1/grocery-orders/${orderId}/list/shop`, { job_id: jobId }, session)
      logger.info('orders.shopping_started', { orderId, jobId: job.id, status: job.status })
      return job
    },
    retry,
    scope: EDITS,
    onSettled: (_job: Job | undefined, _error: Error | null, { orderId }: StartList) =>
      queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  queryClient.setMutationDefaults(QUOTE, {
    // Fills the cart and reads checkout for the order as it is now (after the
    // changes before it); a quote for the same products already read or being read is kept.
    mutationFn: async ({ orderId, jobId, session }: StartList): Promise<Job> => {
      const job = await apiRequest<Job>('POST', `/v1/grocery-orders/${orderId}/quote`, { job_id: jobId }, session)
      logger.info('orders.quote_started', { orderId, jobId: job.id, status: job.status })
      return job
    },
    retry,
    scope: EDITS,
    // Only while the final review that asked for it is open.
    meta: NOT_PERSISTED,
    onSettled: (_job: Job | undefined, _error: Error | null, { orderId }: StartList) =>
      queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  queryClient.setMutationDefaults(SET_ITEM, {
    mutationFn: ({ orderId, itemId, removed, quantity, count, alternative, session }: SetItem) =>
      apiRequest<GroceryOrder>(
        'PATCH',
        `/v1/grocery-orders/${orderId}/list/items/${itemId}`,
        {
          ...(removed === undefined ? {} : { removed }),
          ...(quantity === undefined ? {} : { quantity }),
          ...(count === undefined ? {} : { count }),
          ...(alternative === undefined ? {} : { alternative }),
        },
        session,
      ),
    retry,
    scope: EDITS,
    onSuccess: (order: GroceryOrder) => storeOrder(queryClient, order),
    // Refused (e.g. the list was replaced by a new one): show the order as it is now.
    onError: (_error: Error, { orderId }: SetItem) => queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  queryClient.setMutationDefaults(SET_DATES, {
    mutationFn: ({ orderId, dates, session }: SetDates) =>
      apiRequest<GroceryOrder>('PUT', `/v1/grocery-orders/${orderId}/dates`, dates, session),
    retry,
    scope: EDITS,
    onSuccess: (order: GroceryOrder) => storeOrder(queryClient, order),
    // Refused (e.g. someone placed the order meanwhile): show the order as it is now.
    onError: (_error: Error, { orderId }: SetDates) => queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  queryClient.setMutationDefaults(PLACE, {
    // With the dates on screen, which the server saves as it places the order, so
    // what's placed is what was shown. Placing an order that is already placed
    // returns it unchanged, so a resend is safe.
    mutationFn: ({ orderId, dates, quoteId, session }: PlaceOrder) =>
      apiRequest<GroceryOrder>('POST', `/v1/grocery-orders/${orderId}/place`, { ...dates, quote_id: quoteId }, session),
    retry,
    scope: EDITS,
    onSuccess: async (order: GroceryOrder) => {
      logger.info('orders.placed', { orderId: order.id, number: order.number, meals: order.picks.length })
      await cancelFetches(queryClient, order.id)
      queryClient.setQueryData<GroceryOrder>(orderKey(order.id), order)
      // It's now a placed order, and the next order is the one after it. The
      // cached next order was this draft, so it's dropped (not kept until the
      // refetch) and fetched again.
      await Promise.all([
        queryClient.resetQueries({ queryKey: nextQuery.queryKey }),
        queryClient.invalidateQueries({ queryKey: queryKeys.placedOrders }),
      ])
    },
    // Refused (e.g. the delivery date is no longer available): show the order as it is now.
    onError: (_error: Error, { orderId }: PlaceOrder) => queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  queryClient.setMutationDefaults(DISCARD, {
    mutationFn: ({ orderId }: OrderAction) => apiRequest<void>('DELETE', `/v1/grocery-orders/${orderId}`),
    retry,
    // After the changes still being saved, e.g. a "not for us" mark, which must reach the meal while its draft exists.
    scope: EDITS,
    onSuccess: async (_result: void, { orderId }: OrderAction) => {
      logger.info('orders.discarded', { orderId })
      await cancelFetches(queryClient, orderId)
      queryClient.removeQueries({ queryKey: orderKey(orderId) })
      queryClient.setQueryData<NextOrder>(nextQuery.queryKey, (next) => next && { ...next, draft: null })
    },
    // A 409 means someone placed it meanwhile.
    onError: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: nextQuery.queryKey }),
        queryClient.invalidateQueries({ queryKey: queryKeys.placedOrders }),
      ]),
  })

  queryClient.setMutationDefaults(TAKE, {
    mutationFn: ({ orderId, session, step }: TakeLock) => {
      if (closedSessions.has(session)) throw new FlowClosed()
      return apiRequest<OrderLock>('POST', `/v1/grocery-orders/${orderId}/lock`, { step }, session)
    },
    // The flow tries again at its next check.
    retry: false,
    // A release sent as the flow closes goes after it.
    scope: EDITS,
    meta: NOT_PERSISTED,
    onSuccess: (lock: OrderLock, { orderId }: TakeLock) =>
      queryClient.setQueryData<GroceryOrder>(orderKey(orderId), (order) => order && { ...order, lock }),
  })

  queryClient.setMutationDefaults(RELEASE, {
    // Lets the others in the household order once the flow closes; the lock
    // lapses anyway 2 minutes after its last renewal.
    mutationFn: ({ orderId, session }: ReleaseLock) =>
      apiRequest<void>('DELETE', `/v1/grocery-orders/${orderId}/lock`, undefined, session),
    retry,
    // After the changes made before closing, which need the lock.
    scope: EDITS,
    meta: NOT_PERSISTED,
    onSuccess: (_result: void, { orderId, session }: ReleaseLock) =>
      queryClient.setQueryData<GroceryOrder>(
        orderKey(orderId),
        (order) => order && (order.lock?.session === session ? { ...order, lock: null } : order),
      ),
  })
}

export interface ReleaseLock extends InSession {
  orderId: string
}

export interface TakeLock extends InSession {
  orderId: string
  step: OrderStep
}

/** Takes the order's lock for the flow's session (after the changes still being saved). */
export function takeLock(queryClient: QueryClient, take: TakeLock): Promise<OrderLock> {
  return new MutationObserver<OrderLock, Error, TakeLock>(queryClient, { mutationKey: TAKE }).mutate(take)
}

/**
 * The flow has closed: nothing takes the order's lock for its session any more,
 * and the lock it has (on `orderId`, the order it showed, if any) is released.
 * One still being taken (opening) is released as soon as it's taken.
 */
export function closeFlowSession(queryClient: QueryClient, session: string, orderId: string | undefined) {
  closedSessions.add(session)
  if (orderId !== undefined) releaseLock(queryClient, { orderId, session })
}

/** Releases the order's lock, if the flow's session still has it. */
function releaseLock(queryClient: QueryClient, release: ReleaseLock) {
  new MutationObserver<void, Error, ReleaseLock>(queryClient, { mutationKey: RELEASE })
    .mutate(release)
    // A failure is logged by the mutation cache; the lock lapses on its own.
    .catch(() => undefined)
}

export function useOpenOrder() {
  return useMutation<GroceryOrder, Error, OpenOrder>({ mutationKey: OPEN })
}

export function useGenerateMeals() {
  return useMutation<Job, Error, GenerateMeals>({ mutationKey: GENERATE })
}

/** Adds or removes a pick; it shows at once (see edits.ts). */
export function useSetMeal() {
  return useMutation<GroceryOrder, Error, SetMeal>({ mutationKey: SET_MEAL })
}

/** Changes how many to make of a pick; it shows at once (see edits.ts). */
export function useSetServings() {
  return useMutation<GroceryOrder, Error, SetServings>({ mutationKey: SET_SERVINGS })
}

/** Marks a suggestion "don't show meals like this", or undoes it; it shows at once (see edits.ts). */
export function useSetNotForUs() {
  return useMutation<GroceryOrder, Error, SetNotForUs>({ mutationKey: SET_NOT_FOR_US })
}

/** Saves both dates; they show at once (see edits.ts). */
export function useSetDates() {
  return useMutation<GroceryOrder, Error, SetDates>({ mutationKey: SET_DATES })
}

/** Starts building the shopping list from the picks and servings now. */
export function useStartList() {
  return useMutation<Job, Error, StartList>({ mutationKey: START_LIST })
}

/** Shops the order's list again at the store, after shopping failed. */
export function useShopList() {
  return useMutation<Job, Error, StartList>({ mutationKey: SHOP_LIST })
}

/** Finds out what the order would cost at the store now (the final review's total). */
export function useQuoteOrder() {
  return useMutation<Job, Error, StartList>({ mutationKey: QUOTE })
}

/** Takes an item off the list or puts it back, or changes how much of it to buy; it shows at once. */
export function useSetItem() {
  return useMutation<GroceryOrder, Error, SetItem>({ mutationKey: SET_ITEM })
}

/**
 * Why the newest change to a list item failed, or null: from wherever it was
 * made (the list, or a product's page since left), until the next change.
 */
export function useSetItemError(): Error | null {
  const errors = useMutationState({ filters: { mutationKey: SET_ITEM }, select: (mutation) => mutation.state.error })
  return errors.at(-1) ?? null
}

export function usePlaceOrder() {
  return useMutation<GroceryOrder, Error, PlaceOrder>({ mutationKey: PLACE })
}

export function useDiscardOrder() {
  return useMutation<void, Error, OrderAction>({ mutationKey: DISCARD })
}
