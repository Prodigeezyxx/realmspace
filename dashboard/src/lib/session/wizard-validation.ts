/**
 * realmspace — why the session wizard will not advance.
 *
 * ## The failure this exists for
 *
 * The Phase 6 acceptance walk wrote this one down rather than fixing it: the
 * Continue button disables itself and says nothing. Step 3 is where it bites,
 * and the reason it looks like a bug rather than a requirement is one line
 * two steps earlier — `selectType()` hydrates `zones` from the experience
 * type's preset, so step 3 opens with a full, correctly-named zone list. The
 * page looks finished. `prefabId` is still null, and the only signal anywhere
 * is a greyed-out button.
 *
 * A wizard that refuses to advance should say what it is waiting on. So the
 * rule is a sentence rather than a boolean, and it lives here rather than in
 * the 1,000-line page so it can be read and tested on its own.
 *
 * ## The messages name the field, not the rule
 *
 * "Choose a space template" tells an operator where to look. "prefabId is
 * required" tells them we have a variable. The step-3 message goes further and
 * explains the zones, because their presence is the whole reason the refusal
 * is confusing.
 */

import type { Touchpoint, Zone } from "./types";

/** Everything the wizard's per-step rules read. A view of its state, not a Session. */
export interface WizardDraftState {
  /** Step 1 — the experience type, null until one is picked. */
  type: string | null;
  name: string;
  /** Step 2 */
  venue: string;
  startAtLocal: string;
  /** Step 3 — the space template, null until one is picked. */
  prefabId: string | null;
  zones: Zone[];
  /** Step 4 */
  touchpoints: Touchpoint[];
}

function list(items: string[]): string {
  if (items.length <= 1) return items[0] ?? "";
  return `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`;
}

/**
 * Why this step cannot advance, or `null` when it can.
 *
 * The caller derives "is the button disabled" from this rather than the other
 * way round, so a step cannot be blocked without a reason existing.
 */
export function blockedReason(
  step: number,
  draft: WizardDraftState
): string | null {
  if (step === 1) {
    const missing: string[] = [];
    if (!draft.type) missing.push("an experience type");
    if (!draft.name.trim()) missing.push("a name for this activation");
    return missing.length ? `Choose ${list(missing)}.` : null;
  }

  if (step === 2) {
    const missing: string[] = [];
    if (!draft.venue.trim()) missing.push("the venue");
    if (!draft.startAtLocal) missing.push("a start date and time");
    return missing.length ? `Fill in ${list(missing)}.` : null;
  }

  if (step === 3) {
    if (!draft.prefabId) {
      // The sentence has to explain the zones. They arrived from the
      // experience type at step 1, so this screen looks complete, and an
      // operator told only "choose a template" would go looking for the thing
      // they thought they had already done.
      return (
        "Choose a space template above. The zones below came from the " +
        "experience type you picked — the template is what gives them a " +
        "footprint to sit in."
      );
    }
    if (!draft.zones.length) {
      return "Add at least one zone. There is nothing to attribute dwell to without one.";
    }
    const unnamed = draft.zones.filter((z) => !z.name.trim()).length;
    if (unnamed) {
      return unnamed === 1
        ? "One zone has no name. A zone with no name cannot be read on a report."
        : `${unnamed} zones have no name. A zone with no name cannot be read on a report.`;
    }
    return null;
  }

  if (step === 4) {
    const unnamed = draft.touchpoints.filter((t) => !t.name.trim()).length;
    if (!unnamed) return null;
    return unnamed === 1
      ? "One touchpoint has no name."
      : `${unnamed} touchpoints have no name.`;
  }

  return null;
}
