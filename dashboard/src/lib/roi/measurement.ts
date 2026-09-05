/**
 * Which measurement parameters an ROI figure is computed from.
 *
 * ## Why this is one function and not two reads
 *
 * There are two copies of these numbers. The backend holds the authority — an
 * operator may have corrected the cost from another machine, and
 * `useSessionReport` has always said so: *"the report must divide by what was
 * actually agreed, not by whatever this laptop last saw."* The browser store
 * holds the other, which is the whole configuration on a deployment with no
 * backend, i.e. the laptop demo.
 *
 * `/report` resolved that precedence inline and `/live` did not resolve it at
 * all — it read the local store only. So the moment an activation became
 * editable, a corrected cost would move the report and leave the live tile
 * showing the old one. That is precisely what the live-ROI tile exists to
 * prevent: it renders *the same* scorecard the report does, "so an operator
 * cannot optimise against a number their client will never see."
 *
 * One rule, in one place: **remote if the backend answered, local otherwise**,
 * field by field. Not object-by-object — a remote config with no cost set must
 * fall through to a local one that has it, or an activation configured before
 * this screen existed would lose its figures on the first read.
 */

import type { Measurement } from "@/lib/session/types";
import type { RemoteSessionConfig } from "@/lib/session/publish";

export interface ResolvedMeasurement {
  engagedThresholdSec?: number;
  activationCost?: number;
  currency?: string;
  attributionModel?: string;
  /** The client's own figure, never measured here. */
  revenueInfluenced?: number;
  qualifiedLeads?: number;
}

/** `remote ?? local`, per field. `null` from the backend means "not set". */
export function resolveMeasurement(
  config: RemoteSessionConfig | null | undefined,
  local: Measurement | undefined
): ResolvedMeasurement {
  const m = local ?? {};
  return {
    engagedThresholdSec:
      config?.engagedThresholdSeconds ?? m.engagedThresholdSec,
    activationCost: config?.activationCost ?? m.activationCost,
    currency: config?.currency ?? m.currency,
    attributionModel: config?.attributionModel ?? m.attributionModel,
    revenueInfluenced: config?.revenueInfluenced ?? m.revenueInfluenced,
    qualifiedLeads: config?.qualifiedLeads ?? m.qualifiedLeads,
  };
}
