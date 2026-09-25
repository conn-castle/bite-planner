import {
  type QueryClient,
  queryOptions,
  skipToken,
  useIsMutating,
  useMutation,
  useMutationState,
  useQuery,
} from '@tanstack/react-query'
import { apiRequest } from '../../lib/api/client'
import { ApiError, isRetryable } from '../../lib/api/errors'
import type { GroceryOrder, Job } from '../../lib/api/types'
import { logger } from '../../lib/log'
import { hasRunningBatch } from './batches'
import { applyEdits, isOrderEdit, type SetDates, type SetMeal } from './edits'

/** Grocery orders: the household's draft, and everything the create-order flow changes on it. */

// How often an open order is checked while a batch of suggestions is being made.
const POLL_MS = 3_000

export const draftQuery = queryOptions({
  queryKey: ['draft-order'],
  queryFn: () => apiRequest<GroceryOrder | null>('GET', '/v1/grocery-orders/draft'),
})

const orderKey = (orderId: string) => ['grocery-order', orderId]

/** The household's draft, or null. */
export function useDraft() {
  return useQuery(draftQuery)
}

/**
 * One order as the server has it, checked every few seconds while a batch of
 * suggestions is being made, and `shown`: that order with the changes still
 * being saved applied over it (see edits.ts).
 */
export function useOrder(orderId: string | undefined) {
  const query = useQuery({
    queryKey: orderKey(orderId ?? ''),
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
 * Stops fetches of an order (and the draft) that are already on their way:
 * they started before a change, so their answer is older than its result.
 * Cancelling reverts to the data from before the fetch, so it comes before
 * the result is stored.
 */
async function cancelFetches(queryClient: QueryClient, orderId: string) {
  await Promise.all([
    queryClient.cancelQueries({ queryKey: orderKey(orderId) }),
    queryClient.cancelQueries({ queryKey: draftQuery.queryKey }),
  ])
}

/** Keeps both views of an order current: the order itself, and the draft (null once it is placed). */
async function storeOrder(queryClient: QueryClient, order: GroceryOrder) {
  await cancelFetches(queryClient, order.id)
  queryClient.setQueryData<GroceryOrder>(orderKey(order.id), order)
  queryClient.setQueryData(draftQuery.queryKey, order.status === 'draft' ? order : null)
}

const OPEN = ['orders', 'open']
const GENERATE = ['orders', 'generate']
const SET_MEAL = ['orders', 'set-meal']
const SET_DATES = ['orders', 'set-dates']
const PLACE = ['orders', 'place']
const DISCARD = ['orders', 'discard']

// Changes to an order run one at a time, in the order they were made, so each
// response (the whole order) includes every earlier change, and later ones
// still being saved are applied over it.
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
      await storeOrder(queryClient, order)
    },
    // Refused (e.g. the delivery date is no longer available): show the order as it is now.
    onError: (_error: Error, { orderId }: OrderAction) => queryClient.invalidateQueries({ queryKey: orderKey(orderId) }),
  })

  queryClient.setMutationDefaults(DISCARD, {
    mutationFn: ({ orderId }: OrderAction) => apiRequest<void>('DELETE', `/v1/grocery-orders/${orderId}`),
    retry,
    onSuccess: async (_result: void, { orderId }: OrderAction) => {
      logger.info('orders.discarded', { orderId })
      await cancelFetches(queryClient, orderId)
      queryClient.removeQueries({ queryKey: orderKey(orderId) })
      queryClient.setQueryData(draftQuery.queryKey, null)
    },
    // A 409 means someone placed it meanwhile.
    onError: () => queryClient.invalidateQueries({ queryKey: draftQuery.queryKey }),
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
