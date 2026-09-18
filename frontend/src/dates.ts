// Date-only values ("2026-09-15") are calendar dates, not instants.
//
// `new Date('2026-09-15')` parses a bare date as midnight UTC, which in Eastern
// time is the evening of the 14th — every log_date rendered that way showed a
// day early. A bare date is built in local time instead; anything carrying a
// time component is parsed exactly as before.

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/

export function toLocalDate(value: string): Date {
  const m = DATE_ONLY.exec(value)
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : new Date(value)
}

export function formatDate(value: string, options: Intl.DateTimeFormatOptions): string {
  return toLocalDate(value).toLocaleDateString('en-US', options)
}
