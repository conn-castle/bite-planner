import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../auth/auth', () => ({ auth: { getToken: async () => 'token' } }))
const logged = vi.hoisted(() => ({ info: vi.fn(), warn: vi.fn(), error: vi.fn() }))
vi.mock('../log', () => ({
  logger: logged,
  errorFields: (error: unknown) => ({ error: error instanceof Error ? error.message : String(error) }),
}))

const fetchMock = vi.fn<typeof fetch>()

beforeEach(() => {
  fetchMock.mockReset()
  for (const log of Object.values(logged)) log.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  vi.stubEnv('VITE_API_URL', 'https://api.example.test/')
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

const sentHeaders = () => new Headers(fetchMock.mock.calls[0]?.[1]?.headers)

describe('apiRequest', () => {
  it('cancels an obsolete search without recording it as a request failure', async () => {
    const { apiRequest } = await import('./client')
    const controller: AbortController = new AbortController()
    fetchMock.mockImplementation((_input, init) => new Promise<Response>((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')))
    }))
    const pending: Promise<unknown> = apiRequest('POST', '/v1/household/delivery-address/suggestions',
      { query: '1600 Penn' }, undefined, controller.signal)
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    controller.abort()
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' })
    expect(logged.warn).not.toHaveBeenCalled()
    await expect(apiRequest('POST', '/v1/household/delivery-address/suggestions',
      { query: '1600 Penn' }, undefined, controller.signal)).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("sends the phone's time zone with every request", async () => {
    vi.stubEnv('TZ', 'Pacific/Auckland') // Node applies a new TZ at once
    fetchMock.mockResolvedValue(new Response('null', { status: 200 }))
    const { apiRequest } = await import('./client')

    await apiRequest('GET', '/v1/grocery-orders/draft')

    expect(sentHeaders().get('Time-Zone')).toBe('Pacific/Auckland')
  })

  it('sends PUT with a JSON body and returns the parsed answer', async () => {
    fetchMock.mockResolvedValue(Response.json({ id: 'o1' }))
    const { apiRequest } = await import('./client')

    const answer = await apiRequest<{ id: string }>('PUT', '/v1/grocery-orders/o1/dates', {
      delivery_date: '2026-09-28',
      next_delivery_date: null,
    })

    expect(answer).toEqual({ id: 'o1' })
    const [url, init] = fetchMock.mock.calls[0] ?? []
    expect(url).toBe('https://api.example.test/v1/grocery-orders/o1/dates')
    expect(init?.method).toBe('PUT')
    expect(sentHeaders().get('Content-Type')).toBe('application/json')
    expect(init?.body).toBe('{"delivery_date":"2026-09-28","next_delivery_date":null}')
  })

  it('treats a 204 answer to DELETE as success with no body', async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }))
    const { apiRequest } = await import('./client')

    await expect(apiRequest<void>('DELETE', '/v1/grocery-orders/o1')).resolves.toBeUndefined()
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe('DELETE')
  })

  it("turns a refusal into an ApiError carrying the API's detail", async () => {
    fetchMock.mockResolvedValue(Response.json({ detail: 'A batch is already running.' }, { status: 429 }))
    const { apiRequest } = await import('./client')
    const { ApiError } = await import('./errors')

    const failure = apiRequest('POST', '/v1/grocery-orders/o1/jobs', { job_id: 'j1' })

    await expect(failure).rejects.toBeInstanceOf(ApiError)
    await expect(failure).rejects.toMatchObject({ status: 429, message: 'A batch is already running.' })
  })
})

describe('request logging', () => {
  const ORDER = '701c7fd5-b606-4c7f-a420-27c59be911cc'

  it('logs a request that fails to reach the server, with how long it took and no ids', async () => {
    fetchMock.mockRejectedValue(new TypeError('Load failed'))
    const { apiRequest } = await import('./client')

    await expect(apiRequest('GET', `/v1/grocery-orders/${ORDER}?x=1`)).rejects.toThrow('Load failed')

    expect(logged.warn).toHaveBeenCalledWith('api.request_failed', {
      method: 'GET',
      route: '/v1/grocery-orders/{id}',
      ms: expect.any(Number),
      error: 'Load failed',
    })
  })

  it('logs a refused request with its status, keeping device tokens out', async () => {
    fetchMock.mockResolvedValue(Response.json({ detail: 'Nope.' }, { status: 409 }))
    const { apiRequest } = await import('./client')

    await expect(apiRequest('PUT', `/v1/me/devices/${'ab'.repeat(32)}`)).rejects.toThrow('Nope.')

    expect(logged.warn).toHaveBeenCalledWith('api.request_failed', {
      method: 'PUT',
      route: '/v1/me/devices/{token}',
      ms: expect.any(Number),
      status: 409,
    })
  })

  it('logs an answer that breaks off while it arrives', async () => {
    fetchMock.mockResolvedValue(new Response('{"id": "o', { status: 200 }))
    const { apiRequest } = await import('./client')

    await expect(apiRequest('GET', '/v1/grocery-orders/next')).rejects.toThrow()

    expect(logged.warn).toHaveBeenCalledTimes(1)
    expect(logged.warn.mock.calls[0]?.[0]).toBe('api.request_failed')
  })

  it('logs a slow request, and nothing for a quick one or the log upload itself', async () => {
    const { apiRequest } = await import('./client')
    const clock = vi.spyOn(performance, 'now')

    clock.mockReturnValueOnce(0).mockReturnValue(4500)
    fetchMock.mockResolvedValue(Response.json({}))
    await apiRequest('GET', '/v1/grocery-orders/next')
    expect(logged.info).toHaveBeenCalledWith('api.request_slow', {
      method: 'GET',
      route: '/v1/grocery-orders/next',
      ms: 4500,
    })

    logged.info.mockReset()
    clock.mockReturnValueOnce(0).mockReturnValue(100)
    fetchMock.mockResolvedValue(Response.json({}))
    await apiRequest('GET', '/v1/grocery-orders/next')
    fetchMock.mockRejectedValue(new TypeError('Load failed'))
    await expect(apiRequest('POST', '/v1/logs', {})).rejects.toThrow()
    expect(logged.info).not.toHaveBeenCalled()
    expect(logged.warn).not.toHaveBeenCalled()
    clock.mockRestore()
  })
})
