/** The API's ranges (backend/src/castle_chef/models.py), for the controls that stay inside them. */

export interface Range {
  min: number
  max: number
}

export const DECIDE_BY_OFFSET_DAYS: Range = { min: 1, max: 7 }
export const REMINDER_LEAD_DAYS: Range = { min: 0, max: 7 }
/** A pick's servings, and the household's default for new picks. */
export const SERVINGS: Range = { min: 1, max: 12 }
/** The most characters in a household's name, and (in the app) a person's. */
export const NAME_LENGTH = 60
