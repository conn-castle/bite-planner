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
import { localDate } from '../../lib/dates'
import { ApiError, isRetryable } from '../../lib/api/errors'
import type {
  BookFields,
  EditMealIn,
  AcceptEditIn,
  ClockTime,
  GroceryOrder,
  ISODate,
  ItemIn,
  Job,
  JobIn,
  LockIn,
  NextOrder,
  OrderLock,
  OrderStep,
  PlacedOrder,
  PlaceIn,
  RatingIn,
  ServingsIn,
  SettleIn,
} from '../../lib/api/types'
import { logger } from '../../lib/log'
import { isRecord } from '../../lib/isRecord'
import { queryKeys } from '../../lib/query/keys'
import { storeMealRating } from '../meals/ratings'
import { hasRunningJob } from './batches'
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
import { LOCK_RENEW_MS, withLock } from './lock'

/**
 * Grocery orders: the household's next order and its draft, everything the
 * create-order flow changes on it, and the placed orders.
 */

// How often an open order is checked while one of its jobs runs (hasRunningJob).
const POLL_MS = 3_000
// How often an order being booked at the store is checked (it takes a minute or two).
const BOOKING_POLL_MS = 5_000
// How often one that may be booked any time now is checked (mayBookSoon).
const BOOK_DAY_POLL_MS = 60_000

/**
 * Whether an approved order may start booking any time now, from elsewhere:
 * waiting, with its booking day come (the hourly run books it), or shown not
 * booked (a late one is still waiting, so the run may book it; and anyone's Try
 * again books it).
 */
function mayBookSoon(order: BookFields, today: ISODate): boolean {
  return (
    order.book_state === 'not_booked' ||
    (order.book_state === 'waiting' && order.book_on !== null && order.book_on <= today)
  )
}

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

// How often a query is checked for the data it has (false: not polled). A polled
// query is also fetched again as soon as the app is back in the foreground,
// however recent its copy: iOS stops timers while the app is away, and a tick
// that comes before the app is active again is skipped, so the poll alone could
// leave a finished batch unseen for a whole interval.
function placedPollInterval(data: PlacedOrder[] | undefined): number | false {
  if (data?.some((order) => order.book_state === 'booking')) return BOOKING_POLL_MS
  const today = localDate(new Date())
  return data?.some((order) => mayBookSoon(order, today)) ? BOOK_DAY_POLL_MS : false
}

function orderPollInterval(data: GroceryOrder | undefined, session: string | undefined): number | false {
  return data && hasRunningJob(data)
    ? POLL_MS
    : data?.book_state === 'booking'
      ? BOOKING_POLL_MS
      : session !== undefined && data?.lock && data.lock.session !== session
        ? LOCK_RENEW_MS
        : data && mayBookSoon(data, localDate(new Date()))
          ? BOOK_DAY_POLL_MS
          : false
}

/**
 * The household's next order, with its draft once someone has started one;
 * checked every `refetchInterval` ms if given (Home, to see who is ordering).
 */
export function useNextOrder(refetchInterval: number | false = false) {
  return useQuery({
    ...nextQuery,
    refetchInterval,
    refetchOnWindowFocus: refetchInterval === false ? true : 'always',
  })
}

/**
 * The household's placed orders whose meals cover `coveringFrom` or a later day,
 * or all of them; checked every few seconds while one is being booked, and every
 * minute while one may start booking (mayBookSoon).
 */
export function usePlacedOrders(coveringFrom?: ISODate) {
  return useQuery({
    ...placedQuery(coveringFrom),
    refetchInterval: ({ state: { data } }) => placedPollInterval(data),
    refetchOnWindowFocus: ({ state: { data } }) => placedPollInterval(data) === false ? true : 'always',
  })
}

/**
 * One order as the server has it, checked every few seconds while one of its
 * jobs runs (a batch of suggestions, its list being built or shopped, a quote) or
 * it's being booked, and every minute while it may start booking (mayBookSoon);
 * and `shown`: that order with the changes still
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
    refetchInterval: ({ state: { data } }) => orderPollInterval(data, session),
    refetchOnWindowFocus: ({ state: { data } }) => orderPollInterval(data, session) === false ? true : 'always',
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
const CHANGE = ['orders', 'change']
const CANCEL = ['orders', 'cancel']
const BOOK_AGAIN = ['orders', 'book-again']
const SETTLE = ['orders', 'settle']
const START_LIST = ['orders', 'start-list']
const SHOP_LIST = ['orders', 'shop-list']
const QUOTE = ['orders', 'quote']
const EDIT_RECIPE = ['orders', 'edit-recipe']
const DECIDE_RECIPE = ['orders', 'decide-recipe']
const SET_ITEM = ['orders', 'set-item']
const TAKE = ['orders', 'take']
const RELEASE = ['orders', 'release']

// Requests that only make sense while the flow that made them is open: never
// saved to be sent after a relaunch (they'd take the lock with no flow open).
const NOT_PERSISTED = { persist: false }

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
/** Starts one of the order's jobs: a batch of suggestions, building or shopping its list, or a quote. */
export interface StartJob extends InSession {
  orderId: string
  /** Chosen here, so a resent request (e.g. after going offline) can't start a second. */
  jobId: string
}
export interface EditRecipe extends StartJob {
  mealId: string
  feedback: string
  replaces: string | null
}
export interface DecideRecipe extends StartJob {
  mealId: string
  decision: 'accept' | 'reject'
  baseRevision: number
}

export interface OrderAction {
  orderId: string
}
/** Whether Instacart has an order booking couldn't confirm. */
export interface SettleOrder extends OrderAction {
  placed: boolean
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
  const job = await apiRequest<Job>('POST', `/v1/grocery-orders/${orderId}/jobs`, { job_id: jobId } satisfies JobIn, session)
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

  // A change to the draft, sent in turn with the others; its answer is the whole
  // order. Refused (e.g. someone placed the order meanwhile, the meal was just
  // unpicked on another phone, or the list was replaced by a new one): the order
  // is fetched to show it as it is now.
  const edit = <V extends { orderId: string }>(send: (variables: V) => Promise<GroceryOrder>) => ({
    mutationFn: send,
    retry,
    scope: EDITS,
    onSuccess: (order: GroceryOrder) => storeOrder(queryClient, order),
    onError: (_error: Error, { orderId }: V) => queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  // Starts a job at `path` under the order, in turn after the changes before it,
  // logged as `event`; the job shows in the order, which is fetched after it.
  const startsJob = (path: string, event: string) => ({
    mutationFn: async ({ orderId, jobId, session }: StartJob): Promise<Job> => {
      const body = { job_id: jobId } satisfies JobIn
      const job = await apiRequest<Job>('POST', `/v1/grocery-orders/${orderId}/${path}`, body, session)
      logger.info(event, { orderId, jobId: job.id, status: job.status })
      return job
    },
    retry,
    scope: EDITS,
    onSettled: (_job: Job | undefined, _error: Error | null, { orderId }: StartJob) =>
      queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  queryClient.setMutationDefaults(EDIT_RECIPE, {
    ...edit(({ orderId, mealId, jobId, feedback, replaces, session }: EditRecipe) =>
      apiRequest<GroceryOrder>('POST', `/v1/grocery-orders/${orderId}/meals/${mealId}/edits`,
        { job_id: jobId, feedback, replaces } satisfies EditMealIn, session)),
    // A nonpersistent Send must fail after bounded transport retries, even offline,
    // rather than pause indefinitely with its dialog locked open.
    networkMode: 'always',
    meta: NOT_PERSISTED,
  })
  queryClient.setMutationDefaults(DECIDE_RECIPE, {
    ...edit(({ orderId, mealId, jobId, decision, baseRevision, session }: DecideRecipe) =>
      apiRequest<GroceryOrder>('POST', `/v1/grocery-orders/${orderId}/meals/${mealId}/edits/${jobId}/${decision}`,
        decision === 'accept' ? { base_revision: baseRevision } satisfies AcceptEditIn : undefined, session)),
    meta: NOT_PERSISTED,
    onSuccess: async (order: GroceryOrder, { mealId }: DecideRecipe) => {
      // Cancel before publishing the newer summary; a delayed old recipe must never return.
      await queryClient.cancelQueries({ queryKey: queryKeys.meal(mealId), exact: true })
      await storeOrder(queryClient, order)
      await queryClient.invalidateQueries({ queryKey: queryKeys.meal(mealId), exact: true })
    },
  })

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
    mutationFn: ({ orderId, jobId, session }: StartJob) => startBatch(orderId, jobId, session),
    retry,
    onSuccess: async (job: Job, { orderId }: StartJob) => {
      await cancelFetches(queryClient, orderId)
      queryClient.setQueryData<GroceryOrder>(orderKey(orderId), (order) => order && withJob(order, job))
    },
    // A refusal (e.g. someone else's batch is already running) means the order changed.
    onError: (_error: Error, { orderId }: StartJob) => queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  queryClient.setMutationDefaults(
    SET_MEAL,
    edit(({ orderId, mealId, picked, session }: SetMeal) =>
      apiRequest<GroceryOrder>(picked ? 'PUT' : 'DELETE', `/v1/grocery-orders/${orderId}/meals/${mealId}`, undefined, session),
    ),
  )

  queryClient.setMutationDefaults(
    SET_SERVINGS,
    edit(({ orderId, mealId, servings, session }: SetServings) =>
      apiRequest<GroceryOrder>(
        'PUT',
        `/v1/grocery-orders/${orderId}/meals/${mealId}/servings`,
        { servings } satisfies ServingsIn,
        session,
      ),
    ),
  )

  queryClient.setMutationDefaults(SET_NOT_FOR_US, {
    // The mark is the household's rating of the meal; the server also unpicks
    // it. Its answer has no order, so the order is fetched after it.
    mutationFn: async ({ orderId, mealId, notForUs, session }: SetNotForUs) => {
      if (notForUs) await apiRequest<void>('PUT', `/v1/meals/${mealId}/rating`, { rating: 'not_for_us' } satisfies RatingIn, session)
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

  // Builds the list from the picks and servings now (after the changes to them
  // before it); a build for the same picks already made or being made is kept.
  // The order's list is its newest build.
  queryClient.setMutationDefaults(START_LIST, startsJob('list', 'orders.list_started'))

  // Shops the current list again (after shopping failed); shopping already on
  // its way for it is kept.
  queryClient.setMutationDefaults(SHOP_LIST, startsJob('list/shop', 'orders.shopping_started'))

  // Fills the cart and reads checkout for the order as it is now (after the
  // changes before it); a quote for the same products already read or being read
  // is kept. Only while the final review that asked for it is open.
  queryClient.setMutationDefaults(QUOTE, { ...startsJob('quote', 'orders.quote_started'), meta: NOT_PERSISTED })

  queryClient.setMutationDefaults(
    SET_ITEM,
    edit(({ orderId, itemId, removed, count, alternative, session }: SetItem) =>
      apiRequest<GroceryOrder>(
        'PATCH',
        `/v1/grocery-orders/${orderId}/list/items/${itemId}`,
        {
          ...(removed === undefined ? {} : { removed }),
          ...(count === undefined ? {} : { count }),
          ...(alternative === undefined ? {} : { alternative }),
        } satisfies ItemIn,
        session,
      ),
    ),
  )

  queryClient.setMutationDefaults(
    SET_DATES,
    edit(({ orderId, dates, session }: SetDates) =>
      apiRequest<GroceryOrder>('PUT', `/v1/grocery-orders/${orderId}/dates`, dates, session),
    ),
  )

  queryClient.setMutationDefaults(PLACE, {
    // With the dates on screen, which the server saves as it places the order, so
    // what's placed is what was shown. Placing an order that is already placed
    // returns it unchanged, so a resend is safe.
    mutationFn: ({ orderId, dates, quoteId, session }: PlaceOrder) =>
      apiRequest<GroceryOrder>('POST', `/v1/grocery-orders/${orderId}/place`, { ...dates, quote_id: quoteId } satisfies PlaceIn, session),
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

  // An approved order: changed (back into the draft), cancelled, booked again,
  // or settled ("It was placed"). Each answer can move it on Home and History.
  queryClient.setMutationDefaults(CHANGE, {
    mutationFn: ({ orderId }: OrderAction) => apiRequest<GroceryOrder>('POST', `/v1/grocery-orders/${orderId}/change`),
    onSuccess: async (order: GroceryOrder) => {
      logger.info('orders.reopened', { orderId: order.id })
      await storeOrder(queryClient, order)
      await queryClient.invalidateQueries({ queryKey: queryKeys.placedOrders })
    },
    onError: (_error: Error, { orderId }: OrderAction) => refetchAfterPlaced(queryClient, orderId),
  })
  queryClient.setMutationDefaults(CANCEL, {
    mutationFn: ({ orderId }: OrderAction) => apiRequest<void>('DELETE', `/v1/grocery-orders/${orderId}`),
    onSuccess: async (_result: void, { orderId }: OrderAction) => {
      logger.info('orders.cancelled', { orderId })
      await cancelFetches(queryClient, orderId)
      queryClient.removeQueries({ queryKey: orderKey(orderId) })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: nextQuery.queryKey }),
        queryClient.invalidateQueries({ queryKey: queryKeys.placedOrders }),
      ])
    },
    onError: (_error: Error, { orderId }: OrderAction) => refetchAfterPlaced(queryClient, orderId),
  })
  queryClient.setMutationDefaults(BOOK_AGAIN, {
    mutationFn: ({ orderId }: OrderAction) => apiRequest<GroceryOrder>('POST', `/v1/grocery-orders/${orderId}/book`),
    onSettled: (_order: GroceryOrder | undefined, _error: Error | null, { orderId }: OrderAction) =>
      refetchAfterPlaced(queryClient, orderId),
  })
  queryClient.setMutationDefaults(SETTLE, {
    mutationFn: ({ orderId, placed }: SettleOrder) =>
      apiRequest<GroceryOrder>('POST', `/v1/grocery-orders/${orderId}/settle`, { placed } satisfies SettleIn),
    onSettled: (_order: GroceryOrder | undefined, _error: Error | null, { orderId }: SettleOrder) =>
      refetchAfterPlaced(queryClient, orderId),
  })

  queryClient.setMutationDefaults(TAKE, {
    mutationFn: ({ orderId, session, step }: TakeLock) => {
      if (closedSessions.has(session)) throw new FlowClosed()
      return apiRequest<OrderLock>('POST', `/v1/grocery-orders/${orderId}/lock`, { step } satisfies LockIn, session)
    },
    // The flow tries again at its next check.
    retry: false,
    // A release sent as the flow closes goes after it.
    scope: EDITS,
    meta: NOT_PERSISTED,
    onSuccess: (lock: OrderLock, { orderId }: TakeLock) =>
      queryClient.setQueryData<GroceryOrder>(orderKey(orderId), (order) => order && withLock(order, lock)),
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

/** This session is still opening the draft or taking its lock from an earlier opening. */
export function useAcquiringOrder(orderId: string, session: string): boolean {
  const opening: number = useIsMutating({
    mutationKey: OPEN,
    predicate: (mutation) => {
      const variables: unknown = mutation.state.variables
      return isRecord(variables) && variables.session === session
    },
  })
  const taking: number = useIsMutating({
    mutationKey: TAKE,
    predicate: (mutation) => {
      const variables: unknown = mutation.state.variables
      return isRecord(variables) && variables.session === session && variables.orderId === orderId
    },
  })
  return opening + taking > 0
}

export function useGenerateMeals() {
  return useMutation<Job, Error, StartJob>({ mutationKey: GENERATE })
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
  return useMutation<Job, Error, StartJob>({ mutationKey: START_LIST })
}

/** Shops the order's list again at the store, after shopping failed. */
export function useShopList() {
  return useMutation<Job, Error, StartJob>({ mutationKey: SHOP_LIST })
}

/** Finds out what the order would cost at the store now (the final review's total). */
export function useQuoteOrder() {
  return useMutation<Job, Error, StartJob>({ mutationKey: QUOTE })
}

/** Takes an item off the list or puts it back, changes how many of its product, or switches it to an alternative; it shows at once. */
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

/** Makes an approved order not booked yet the household's draft again, to change it. */
export function useChangeOrder() {
  return useMutation<GroceryOrder, Error, OrderAction>({ mutationKey: CHANGE })
}

/** Cancels an approved order not booked yet. */
export function useCancelOrder() {
  return useMutation<void, Error, OrderAction>({ mutationKey: CANCEL })
}

/** Books an approved order again, after it wasn't booked. */
export function useBookAgain() {
  return useMutation<GroceryOrder, Error, OrderAction>({ mutationKey: BOOK_AGAIN })
}

/** Says whether Instacart has an order booking couldn't confirm. */
export function useSettleOrder() {
  return useMutation<GroceryOrder, Error, SettleOrder>({ mutationKey: SETTLE })
}

export function useEditRecipe() {
  return useMutation<GroceryOrder, Error, EditRecipe>({ mutationKey: EDIT_RECIPE })
}
export function useDecideRecipe() {
  return useMutation<GroceryOrder, Error, DecideRecipe>({ mutationKey: DECIDE_RECIPE })
}
