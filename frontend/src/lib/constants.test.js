import { BOOKING_CODES, bookingCodeTone } from './constants';

/**
 * The booking-code colour resolver.
 *
 * The rule this guards is PRECEDENCE. Every compound code in BOOKING_CODES
 * contains the word "Speaker", so a resolver that tested for a speaker before it
 * tested for a sponsorship tier would colour every sponsor as a speaker and
 * nothing would look broken — the cells would all be filled, just wrong. That is
 * exactly the kind of failure nobody spots in a screenshot.
 */
describe('bookingCodeTone', () => {
  test('the sponsorship outranks the speaker in a compound code', () => {
    expect(bookingCodeTone('SLV SpEx')).toBe('green');
    expect(bookingCodeTone('Speaker / SLV SpEx')).toBe('green');
    expect(bookingCodeTone('Upgraded to SLV SpEx')).toBe('green');
    expect(bookingCodeTone('Speaker')).toBe('amber');
  });

  test('every tier is the same green', () => {
    const tones = ['GLD', 'SLV', 'PLT', 'PTN'].map((t) => bookingCodeTone(`${t} SpEx`));
    expect(new Set(tones)).toEqual(new Set(['green']));
  });

  // A code with no tone renders as an uncoloured cell, which reads as a missing
  // value rather than as a category. Every stored code must resolve.
  test('every code in BOOKING_CODES resolves to a tone', () => {
    BOOKING_CODES.forEach((code) => {
      expect(typeof bookingCodeTone(code)).toBe('string');
    });
  });

  test('an empty or unknown code falls back rather than throwing', () => {
    expect(bookingCodeTone('')).toBe('neutral');
    expect(bookingCodeTone(null)).toBe('neutral');
    expect(bookingCodeTone('Something Sales Invented')).toBe('neutral');
  });

  // Red means a problem across this CRM. No pass type is a problem.
  test('no booking code is red', () => {
    BOOKING_CODES.forEach((code) => {
      expect(bookingCodeTone(code)).not.toBe('red');
    });
  });
});
