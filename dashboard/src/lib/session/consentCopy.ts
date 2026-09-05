/**
 * The wording a consent kiosk shows, and the version stamped on every consent
 * given through it.
 *
 * ## Why the version is derived and not typed
 *
 * `docs/event-bus-spec.md` §3: *"`copy_version` is the load-bearing field, not
 * the tier … it is the only thing that settles a withdrawal argued after the
 * fact"*. A version an operator types by hand is one they can forget to change,
 * and the failure is silent and total: two different sentences recorded under
 * one version, with nothing afterwards able to say which a person read. So it
 * is a function of the text. Edit a word and the version moves on its own.
 *
 * The hash is FNV-1a, which is not a security primitive and is not being used
 * as one — nothing here defends against somebody choosing two texts that
 * collide. It answers "is this the same wording as before", and it does that
 * with no dependency and no async.
 */

/** The wording a new activation starts with. Deliberately short, and specific
 * about what is kept — `docs/consent-and-identity.md` §4 asks the surface to
 * show what the tier actually means, not a paragraph nobody reads at a stand. */
export const DEFAULT_CONSENT_COPY = `We measure how people move around this stand. That part is anonymous and never names you.

If you leave your details here, we will keep them with your visit and the brand running this stand may contact you about it. You can ask us to remove them at any time.`;

/** `consent-YYYY-MM-xxxxxxxx`, stable for a given text. */
export function copyVersionFor(text: string, at: Date = new Date()): string {
  const normalized = text.trim().replace(/\s+/g, " ");
  if (!normalized) return "";

  // FNV-1a, 32-bit. `>>> 0` after each step keeps it unsigned in a language
  // whose bitwise operators are signed.
  let hash = 0x811c9dc5;
  for (let i = 0; i < normalized.length; i += 1) {
    hash ^= normalized.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }

  const month = `${at.getUTCFullYear()}-${String(at.getUTCMonth() + 1).padStart(2, "0")}`;
  return `consent-${month}-${hash.toString(16).padStart(8, "0")}`;
}
