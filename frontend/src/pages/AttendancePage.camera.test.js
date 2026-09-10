import { TAB, tabPlan, cameraLiveOn } from './AttendancePage';

describe('the tab strip', () => {
  test('Scan comes first for somebody who may work the door', () => {
    expect(tabPlan(true).tabs.map((t) => t.id))
      .toEqual([TAB.SCAN, TAB.LOG, TAB.ROSTER]);
  });

  test('Scan is absent entirely without the create right', () => {
    const ids = tabPlan(false).tabs.map((t) => t.id);
    expect(ids).toEqual([TAB.LOG, TAB.ROSTER]);
    expect(ids).not.toContain(TAB.SCAN);
  });

  test('the counts land on the two tabs that have them', () => {
    const tabs = tabPlan(true, { arrived: 12, expected: 77 }).tabs;
    expect(tabs.find((t) => t.id === TAB.LOG).count).toBe(12);
    expect(tabs.find((t) => t.id === TAB.ROSTER).count).toBe(77);
    // Scan counts nothing; a number beside it would read as a scan tally.
    expect(tabs.find((t) => t.id === TAB.SCAN).count).toBeUndefined();
  });
});

describe('the camera on page load', () => {
  test('the landing tab is not Scan, however the strip is ordered', () => {
    expect(tabPlan(true).landing).not.toBe(TAB.SCAN);
    expect(tabPlan(false).landing).not.toBe(TAB.SCAN);
  });

  test('THE ASSERTION THAT MATTERS: the camera is off on the landing tab', () => {
    expect(cameraLiveOn(tabPlan(true).landing)).toBe(false);
  });

  test('the first tab in the strip is not what decides the camera', () => {
    // Scan being first must not be enough to start anything. If someone ever
    // wires the landing tab to tabs[0], this is the test that fails.
    expect(cameraLiveOn(tabPlan(true).tabs[0].id)).toBe(true);
    expect(tabPlan(true).landing).not.toBe(tabPlan(true).tabs[0].id);
  });
});

describe('the camera lifecycle', () => {
  test('only Scan is live, so every other tab releases the device', () => {
    expect(cameraLiveOn(TAB.SCAN)).toBe(true);
    expect(cameraLiveOn(TAB.LOG)).toBe(false);
    expect(cameraLiveOn(TAB.ROSTER)).toBe(false);
  });

  test('nothing unknown is ever treated as live', () => {
    // A fail-CLOSED default. A tab key added later, or a stale value read back
    // from anywhere, must not open a camera by falling through.
    for (const value of [undefined, null, '', 'Scan', 'SCAN', 'camera', 0]) {
      expect(cameraLiveOn(value)).toBe(false);
    }
  });
});
