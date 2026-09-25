import { createContext, type ReactNode } from 'react'

/** A problem with the whole flow (e.g. the order couldn't be refreshed), shown at the top of every step. */
export const FlowNotice = createContext<ReactNode>(null)
