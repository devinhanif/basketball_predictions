import raw from "./learned.json";

export type LearnedStatus = "passed_holdout" | "replicated" | "real_but_small" | "one_season_only" | "failed_informative";

export interface Evidence {
  label: string;
  point: string;
  lo: string | null;
  hi: string | null;
  n: string;
  n_source: string;
  interval: string;
}

export interface Learned {
  id: string;
  ledger_ids: string[];
  status: LearnedStatus;
  title: string;
  claim: string;
  evidence: Evidence[];
  caveat: string;
  tokens: string[];
}

export const STATUS_LABEL: Record<LearnedStatus, string> = {
  passed_holdout: "Passed the holdout",
  replicated: "Held in two seasons, holdout untouched",
  real_but_small: "Real but small",
  one_season_only: "Passed one season only",
  failed_informative: "Failed, but informative",
};

/** The order a reader meets them: strongest evidence first. */
export const STATUS_ORDER: LearnedStatus[] = ["passed_holdout", "replicated", "real_but_small", "one_season_only", "failed_informative"];

export const LEARNED: Learned[] = (raw.items as Learned[]).slice().sort((a, b) => STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status));

/** "-0.0133 [-0.0158, -0.0109]" or the point alone when the ledger row has no interval. */
export function evidenceText(e: Evidence): string {
  return e.lo !== null && e.hi !== null ? `${e.point} [${e.lo}, ${e.hi}]` : e.point;
}

/** The rule from the spec: a claim with no n, or no interval and no stated alternative, is not shown. */
export function isShowable(l: Learned): boolean {
  return l.evidence.length > 0 && l.evidence.every((e) => e.n.trim() !== "" && e.interval.trim() !== "" && (e.lo !== null || e.interval.startsWith("none:")));
}
