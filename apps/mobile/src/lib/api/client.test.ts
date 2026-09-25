import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../auth/auth', () => ({ auth: { getToken: async () => 'token' } }))

const fetchMock = vi.fn<typeof fetch>()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
  vi.stubEnv('VITE_API_URL', 'https://api.example.test/')
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.unstubAllEnvs()
})

const sentHeaders = () => new Headers(fetchMock.mock.calls[0]?.[1]?.headers)

describe('apiRequest', () => {
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
