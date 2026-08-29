import { describe, expect, it } from 'vitest'
import { duration } from './format'

describe('duration', () => {
  it('renders minutes under an hour', () => {
    expect(duration(0)).toBe('0m')
    expect(duration(9 * 60_000)).toBe('9m')
    expect(duration(59 * 60_000 + 20_000)).toBe('59m')
  })

  it('renders hours and minutes under a day', () => {
    expect(duration(60 * 60_000)).toBe('1h 0m')
    expect(duration((3 * 60 + 25) * 60_000)).toBe('3h 25m')
  })

  it('renders days and hours past 24h', () => {
    expect(duration(26 * 60 * 60_000)).toBe('1d 2h')
    expect(duration(3 * 24 * 60 * 60_000)).toBe('3d 0h')
  })

  it('guards against negative or non-finite spans', () => {
    expect(duration(-1)).toBe('—')
    expect(duration(Number.NaN)).toBe('—')
  })
})
