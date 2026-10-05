/**
 * The API's ranges (backend/src/castle_chef/models.py), for the controls that
 * stay inside them, and the other numbers of the backend's that the app says.
 */

export interface Range {
  min: number
  max: number
}

export const DECIDE_BY_OFFSET_DAYS: Range = { min: 1, max: 7 }
export const REMINDER_LEAD_DAYS: Range = { min: 0, max: 7 }
/** A pick's servings, and the household's default for new picks. */
export const SERVINGS: Range = { min: 1, max: 12 }
/** The tip's percentage of the items' total, and its maximum in cents. */
export const TIP_PERCENT: Range = { min: 0, max: 30 }
export const TIP_MAX_CENTS: Range = { min: 0, max: 10_000 }
/** The meals in a batch of suggestions (backend settings.meals_per_batch). */
export const BATCH_SIZE = 20

/** The most characters in a household's name, and (in the app) a person's. */
export const NAME_LENGTH = 60

/** An email address, at most (backend household.EMAIL_LENGTH). */
export const EMAIL_LENGTH = 254
