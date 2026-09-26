/** Whether a value is an object whose fields can be checked, e.g. a mutation's variables (stored untyped). */
export const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
