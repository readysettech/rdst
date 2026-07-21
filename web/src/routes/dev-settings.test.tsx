import { describe, expect, it } from 'vitest';
import { Route } from './dev-settings';

// The standalone /dev-settings screen was merged into the Settings page (its
// tools now render as the Developer settings section under /configure#dev).
// The route survives only as a redirect so old deep links keep working.
describe('dev-settings route (redirect after merge)', () => {
  it('beforeLoad redirects to /configure', () => {
    const beforeLoad = Route.options.beforeLoad as () => void;

    let thrown: unknown;
    try {
      beforeLoad();
    } catch (e) {
      thrown = e;
    }

    expect(thrown).toBeDefined();
    expect(JSON.stringify(thrown)).toContain('/configure');
  });

  it('redirect carries the #dev hash so the link lands on the Developer settings section', () => {
    const beforeLoad = Route.options.beforeLoad as () => void;

    let thrown: unknown;
    try {
      beforeLoad();
    } catch (e) {
      thrown = e;
    }

    expect(JSON.stringify(thrown)).toContain('dev');
  });
});
