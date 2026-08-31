// The grade preview shown beside the rubric must agree with the server, which is
// the only thing that actually stores a letter (PaperReview.save() derives it
// from GRADE_BANDS in paper_review/models.py). A form that previews B while the
// row saves as B+ is worse than showing nothing, because it looks authoritative.
//
// Both ends of every band are asserted. A band expressed as a floor is exactly
// where an off-by-one hides, and this table is a copy of one that lives in
// Python, so the drift this catches is a real possibility rather than a
// hypothetical one.
import { gradeFor } from './PaperReviewFields';

describe('gradeFor', () => {
  const cases = [
    [45, 'A'], [36, 'A'],
    [35, 'B+'], [31, 'B+'],
    [30, 'B'], [26, 'B'],
    [25, 'C'], [21, 'C'],
    [20, 'D'], [11, 'D'],
    [10, 'E'], [0, 'E'],
  ];

  cases.forEach(([score, letter]) => {
    it(`grades ${score} of 45 as ${letter}`, () => {
      expect(gradeFor(score)).toBe(letter);
    });
  });
});
