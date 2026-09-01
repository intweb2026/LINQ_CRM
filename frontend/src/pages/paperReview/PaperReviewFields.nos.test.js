/**
 * PaperReviewFields.nos.test.js
 * ─────────────────────────────
 * NOS zeroes the rubric, and unchecking does not leave the zeroes behind.
 *
 * WHY THIS FILE EXISTS
 * The scores are REQUIRED, and firstMissing() rejects only blanks — 0 is a legal
 * score. So a NOS form has to reach 0, not '', or it cannot be saved at all; and
 * a form that stops being NOS has to go back to '', or the six zeroes NOS put
 * there sail past validation and save a real speaker at 0 / 45, grade E, with
 * nobody having typed a single number. Both directions are the check.
 */
import { scoreReset, scoreOf, gradeFor, firstMissing, BLANK } from './PaperReviewFields';
import { PAPER_REVIEW_CRITERIA } from '../../lib/constants';

/** A form that is complete apart from whatever the rubric holds. */
const filled = (scores) => ({
  ...BLANK, ...scores,
  event_code: 'BIU', speaker_name: 'A', company_name: 'B', email: 'a@b.com',
  linkedin_speaker: 'https://linkedin.com/in/a', linkedin_followers: 10,
  session_location_on_agenda: 'Day 1, Morning Session',
  proposal_received: 'x', theme: 'y', agenda_addition: 'z',
});

test('checking NOS zeroes every criterion and the form still validates', () => {
  const form = filled(scoreReset(true));
  PAPER_REVIEW_CRITERIA.forEach((c) => expect(form[c.key]).toBe(0));
  expect(firstMissing(form)).toBeNull();
  expect(scoreOf(form)).toBe(0);
  expect(gradeFor(scoreOf(form))).toBe('E');
});

test('unchecking NOS blanks them, so the zeroes cannot be saved unnoticed', () => {
  const form = filled(scoreReset(false));
  PAPER_REVIEW_CRITERIA.forEach((c) => expect(form[c.key]).toBe(''));
  expect(firstMissing(form)).toMatch(/score is required/);
});
