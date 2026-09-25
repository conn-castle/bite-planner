/** "5 meals", "1 meal" */
export function mealCount(count: number): string {
  return `${count} ${count === 1 ? 'meal' : 'meals'}`
}

/** "9:41 AM" */
export function timeOfDay(date: Date): string {
  return date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

/** "1:05" for 65 seconds. */
export function elapsed(from: Date, to: Date): string {
  const seconds = Math.max(0, Math.floor((to.getTime() - from.getTime()) / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}
