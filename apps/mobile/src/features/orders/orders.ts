import {
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
import type { GroceryOrder, ISODate, Job, NextOrder, PlacedOrder } from '../../lib/api/types'
import { logger } from '../../lib/log'
import { queryKeys } from '../../lib/query/keys'
import { storeMealRating } from '../meals/ratings'
import { hasRunningBatch } from './batches'
import { applyEdits, isOrderEdit, type SetDates, type SetMeal, type SetNotForUs } from './edits'

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

/** The household's next order, with its draft once someone has started one. */
export function useNextOrder() {
  return useQuery(nextQuery)
}

/** The household's placed orders whose meals cover `coveringFrom` or a later day, or all of them. */
export function usePlacedOrders(coveringFrom?: ISODate) {
  return useQuery(placedQuery(coveringFrom))
}

/**
 * One order as the server has it, checked every few seconds while a batch of
 * suggestions is being made, and `shown`: that order with the changes still
 * being saved applied over it (see edits.ts).
 */
export function useOrder(orderId: string | undefined) {
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
    refetchInterval: (query) => (query.state.data && hasRunningBatch(query.state.data) ? POLL_MS : false),
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
const SET_DATES = ['orders', 'set-dates']
const SET_NOT_FOR_US = ['orders', 'set-not-for-us']
const PLACE = ['orders', 'place']
const DISCARD = ['orders', 'discard']

// Changes to an order run one at a time, in the order they were made, so each
// response (the whole order) includes every earlier change, and later ones
// still being saved are applied over it. Opening, placing, and discarding the
// draft take their turn too.
const EDITS = { id: 'grocery-order-edits' }

export interface OpenOrder {
  /** For the first batch, if the draft has none; chosen here so a resent request can't start a second. */
  jobId: string
}
export interface GenerateMeals {
  orderId: string
  /** Chosen here, so a resent request (e.g. after going offline) can't start a second batch. */
  jobId: string
}
export interface OrderAction {
  orderId: string
}

async function startBatch(orderId: string, jobId: string): Promise<Job> {
  const job = await apiRequest<Job>('POST', `/v1/grocery-orders/${orderId}/jobs`, { job_id: jobId })
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
  const retry = (failureCount: number, error: Error) => isRetryable(error, failureCount)

  queryClient.setMutationDefaults(OPEN, {
    // Gets or creates the household's draft, and starts its first batch if it has none.
    mutationFn: async ({ jobId }: OpenOrder): Promise<GroceryOrder> => {
      const order = await apiRequest<GroceryOrder>('POST', '/v1/grocery-orders')
      if (order.jobs.length > 0) return order
      try {
        return withJob(order, await startBatch(order.id, jobId))
      } catch (error) {
        // Another member opened the same draft a moment earlier and started its batch.
        if (!(error instanceof ApiError && error.status === 429)) throw error
        return apiRequest<GroceryOrder>('GET', `/v1/grocery-orders/${order.id}`)
      }
    },
    retry,
    // Changes made while it opens (the cached draft is on screen) are saved after it,
    // so its answer (the draft as it was) never lands on top of theirs.
    scope: EDITS,
    onSuccess: (order: GroceryOrder) => storeOrder(queryClient, order),
  })

  queryClient.setMutationDefaults(GENERATE, {
    mutationFn: ({ orderId, jobId }: GenerateMeals) => startBatch(orderId, jobId),
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
    mutationFn: ({ orderId, mealId, picked }: SetMeal) =>
      apiRequest<GroceryOrder>(picked ? 'PUT' : 'DELETE', `/v1/grocery-orders/${orderId}/meals/${mealId}`),
    retry,
    scope: EDITS,
    onSuccess: (order: GroceryOrder) => storeOrder(queryClient, order),
    // Refused (e.g. someone placed the order meanwhile): show the order as it is now.
    onError: (_error: Error, { orderId }: SetMeal) => queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  queryClient.setMutationDefaults(SET_NOT_FOR_US, {
    // The mark is the household's rating of the meal; the server also unpicks
    // it. Its answer has no order, so the order is fetched after it.
    mutationFn: async ({ orderId, mealId, notForUs }: SetNotForUs) => {
      if (notForUs) await apiRequest<void>('PUT', `/v1/meals/${mealId}/rating`, { rating: 'not_for_us' })
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

  queryClient.setMutationDefaults(SET_DATES, {
    mutationFn: ({ orderId, dates }: SetDates) =>
      apiRequest<GroceryOrder>('PUT', `/v1/grocery-orders/${orderId}/dates`, dates),
    retry,
    scope: EDITS,
    onSuccess: (order: GroceryOrder) => storeOrder(queryClient, order),
    // Refused (e.g. someone placed the order meanwhile): show the order as it is now.
    onError: (_error: Error, { orderId }: SetDates) => queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  queryClient.setMutationDefaults(PLACE, {
    // Placing an order that is already placed returns it unchanged, so a resend is safe.
    mutationFn: ({ orderId }: OrderAction) => apiRequest<GroceryOrder>('POST', `/v1/grocery-orders/${orderId}/place`),
    retry,
    scope: EDITS,
    onSuccess: async (order: GroceryOrder) => {
      logger.info('orders.placed', { orderId: order.id, number: order.number, meals: order.meal_ids.length })
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
    onError: (_error: Error, { orderId }: OrderAction) => queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
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

/** Marks a suggestion "don't show meals like this", or undoes it; it shows at once (see edits.ts). */
export function useSetNotForUs() {
  return useMutation<GroceryOrder, Error, SetNotForUs>({ mutationKey: SET_NOT_FOR_US })
}

/** Saves both dates; they show at once (see edits.ts). */
export function useSetDates() {
  return useMutation<GroceryOrder, Error, SetDates>({ mutationKey: SET_DATES })
}

export function usePlaceOrder() {
  return useMutation<GroceryOrder, Error, OrderAction>({ mutationKey: PLACE })
}

export function useDiscardOrder() {
  return useMutation<void, Error, OrderAction>({ mutationKey: DISCARD })
}
