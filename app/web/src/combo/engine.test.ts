// The port against the reference's golden vectors (docs/contracts/combo_export_v1.md section 5.6): exact methods to 2e-6
// absolute, QMC within 4 * se + 1e-4 of the stored p_joint, rankings and searches with the same legs in the same order.
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import golden from "./golden/combo_golden_v1.json";
import {
  bivariateRect,
  candidateLegs,
  cholesky,
  evaluateCombo,
  evaluateGameLegs,
  genzRect,
  legAboveTestedRange,
  liftRanking,
  normCdf,
  normPpf,
  repairToPd,
  scriptDefinitions,
  scriptPrior,
  targetSearch,
} from "./engine";
import type { ComboGame, ComboShared, Leg } from "./types";

interface Expect {
  p_joint: number;
  p_independent: number;
  lift: number | null;
  marginals: number[];
  se: number;
  method: string;
  p_all_play: number | null;
  p_joint_incl_dnp: number | null;
  label: string | null;
  above_tested_range: boolean[];
}
interface Case {
  name: string;
  legs: Leg[];
  script?: string;
  expect: Expect;
}
const G = golden as unknown as {
  cases: Case[];
  game: ComboGame;
  shared: ComboShared;
  script_priors: Record<string, number>;
  script_definitions: Record<string, { rule: string }>;
  lift_ranking_top5: { legs: Leg[]; p_joint: number; p_independent: number; lift: number; method: string; reason: string; above_tested_range: boolean; label: string | null }[];
  target_search: { feasible: boolean; target: number; k_legs: number; combos: { legs: Leg[]; p_joint: number; lift: number; method: string }[] };
  tolerance: { exact: number; qmc: string };
};
const EXACT = 2e-6;
const games = { G1: G.game };

describe("the golden file is the reference's", () => {
  it("pins the sha256 of the vectors (update with the reference file only, in review)", () => {
    const bytes = readFileSync(join(import.meta.dirname, "golden/combo_golden_v1.json"));
    expect(createHash("sha256").update(bytes).digest("hex")).toBe("d94d948b172d5ff6b7ab440175734ccd19633d1b131bbf96a0df446add89eacb");
  });
  it("states the contract tolerances", () => {
    expect(G.tolerance).toEqual({ exact: 2e-6, qmc: "4*se + 1e-4" });
  });
});

describe("normal distribution primitives", () => {
  it("cdf matches the reference values to 1e-15 absolute", () => {
    const ref: [number, number][] = [
      [-30, 4.9067139271487634e-198], [-8, 6.22096057427182e-16], [-7.5, 3.1908916729109203e-14], [-5, 2.866515718791945e-7],
      [-3, 0.0013498980316300957], [-1, 0.15865525393145705], [-0.3, 0.3820885778110474], [0, 0.5], [0.4, 0.6554217416103242],
      [2, 0.9772498680518208], [6.9, 0.9999999999973999], [7.2, 0.9999999999996989],
    ];
    for (const [x, p] of ref) {
      expect(Math.abs(normCdf(x) - p)).toBeLessThan(1e-15); // absolute
      expect(Math.abs(normCdf(x) - p) / p).toBeLessThan(2e-8); // relative, worst in the far tail (|x| > 7)
    }
    expect(normCdf(Infinity)).toBe(1);
    expect(normCdf(-Infinity)).toBe(0);
  });
  it("ppf matches the reference (AS241) to 1e-9 and is infinite at 0 and 1", () => {
    const ref: [number, number][] = [
      [1e-300, -37.0470962993612], [1e-12, -7.034483825301132], [1e-6, -4.753424308822899], [0.001, -3.090232306167813],
      [0.02, -2.0537489106318225], [0.1, -1.2815515655446006], [0.3, -0.5244005127080408], [0.49, -0.025068908258711053],
      [0.5, 0], [0.7, 0.5244005127080407], [0.9, 1.2815515655446006], [0.99, 2.3263478740408408], [0.999999, 4.753424308817086],
    ];
    for (const [p, x] of ref) expect(Math.abs(normPpf(p) - x)).toBeLessThan(1e-9);
    expect(normPpf(0)).toBe(-Infinity);
    expect(normPpf(1)).toBe(Infinity);
  });
  it("cholesky refuses a matrix that is not positive definite and the repair shrinks it to one", () => {
    const m = [
      [1, 0.99, -0.99],
      [0.99, 1, 0.99],
      [-0.99, 0.99, 1],
    ];
    expect(cholesky(m)).toBeNull();
    const [r, t] = repairToPd(m.map((x) => [...x]));
    expect(t).toBeGreaterThan(0);
    expect(cholesky(r)).not.toBeNull();
  });
  it("the bivariate rectangle at rho 0 is the product and the QMC agrees with the exact rule", () => {
    expect(bivariateRect(-Infinity, 0.3, -Infinity, -0.2, 0)).toBeCloseTo(normCdf(0.3) * normCdf(-0.2), 9);
    const chol = cholesky([
      [1, 0.4],
      [0.4, 1],
    ]);
    if (chol === null) throw new Error("not PD");
    const [p] = genzRect(chol, [-Infinity, -Infinity], [0.3, -0.2]);
    expect(Math.abs(p - bivariateRect(-Infinity, 0.3, -Infinity, -0.2, 0.4))).toBeLessThan(3e-4);
  });
});

describe("evaluate against the golden cases", () => {
  for (const c of G.cases) {
    it(c.name, () => {
      const r = evaluateCombo(c.legs, games, G.shared, c.script ?? null);
      const e = c.expect;
      expect(r.method).toBe(e.method);
      expect(r.label).toBe(e.label);
      expect(r.above_tested_range).toEqual(e.above_tested_range);
      expect(r.marginals.length).toBe(e.marginals.length);
      r.marginals.forEach((m, i) => {
        expect(Math.abs(m - (e.marginals[i] as number))).toBeLessThan(EXACT);
      });
      expect(Math.abs(r.p_independent - e.p_independent)).toBeLessThan(EXACT);
      const tol = r.method === "qmc" ? 4 * e.se + 1e-4 : EXACT;
      expect(Math.abs(r.p_joint - e.p_joint)).toBeLessThan(tol);
      if (r.method === "qmc") {
        // a port that copies the lattice and the shifts agrees to about 1e-9
        expect(Math.abs(r.p_joint - e.p_joint)).toBeLessThan(1e-7);
        expect(Math.abs(r.se - e.se)).toBeLessThan(1e-7);
        expect(r.range_2se).not.toBeNull();
      } else {
        expect(r.range_2se).toBeNull();
      }
      if (e.lift === null) expect(r.lift).toBeNull();
      else expect(Math.abs((r.lift ?? NaN) - e.lift)).toBeLessThan(r.method === "qmc" ? 1e-5 : 1e-4);
      if (e.p_all_play === null) {
        expect(r.p_all_play).toBeNull();
        expect(r.p_joint_incl_dnp).toBeNull();
      } else {
        expect(Math.abs((r.p_all_play ?? NaN) - e.p_all_play)).toBeLessThan(1e-12);
        expect(Math.abs((r.p_joint_incl_dnp ?? NaN) - (e.p_joint_incl_dnp as number))).toBeLessThan(tol);
      }
    });
  }
  it("covers the cases the contract names (one entity, two, three or more, game legs, scripts)", () => {
    const methods = new Set(G.cases.map((c) => c.expect.method));
    expect([...methods].sort()).toEqual(["empty", "exact_bivariate", "marginal", "qmc"]);
    expect(G.cases.some((c) => c.script !== undefined)).toBe(true);
    expect(G.cases.some((c) => c.legs.some((l) => l.kind !== "player"))).toBe(true);
  });
});

describe("script priors, definitions and the searches", () => {
  it("script priors to 2e-6 and definitions verbatim", () => {
    for (const [s, p] of Object.entries(G.script_priors)) expect(Math.abs(scriptPrior(s, G.game, G.shared) - p)).toBeLessThan(EXACT);
    const d = scriptDefinitions(G.game, G.shared);
    for (const [s, v] of Object.entries(G.script_definitions)) expect(d[s]?.rule).toBe(v.rule);
  });
  it("lift ranking: the same legs in the same order, numbers to the QMC tolerance", () => {
    const got = liftRanking(G.game, G.shared, 5);
    expect(got.map((r) => r.legs.map((l) => `${l.player_id.toString()}|${l.stat}|${l.line.toString()}|${l.side}`))).toEqual(
      G.lift_ranking_top5.map((r) =>
        r.legs.map((l) => {
          const p = l as Extract<Leg, { kind: "player" }>;
          return `${p.player_id.toString()}|${p.stat}|${p.line.toString()}|${p.side}`;
        }),
      ),
    );
    got.forEach((r, i) => {
      const e = G.lift_ranking_top5[i];
      if (e === undefined) throw new Error("missing");
      expect(Math.abs(r.p_joint - e.p_joint)).toBeLessThan(r.method === "qmc" ? 4 * r.se + 1e-4 : EXACT);
      expect(r.method).toBe(e.method);
      expect(r.reason).toBe(e.reason);
      expect(r.above_tested_range).toBe(e.above_tested_range);
      expect(r.label).toBe(e.label);
    });
  });
  it("target search (k = 2): the same legs in the same order and the same feasibility", () => {
    const t = G.target_search;
    const got = targetSearch(G.game, G.shared, t.target, t.k_legs, 3);
    expect(got.feasible).toBe(t.feasible);
    expect(got.combos.map((c) => c.legs.map((l) => (l.kind === "player" ? `${l.player_id.toString()}|${l.stat}|${l.line.toString()}` : "")))).toEqual(
      t.combos.map((c) => c.legs.map((l) => (l.kind === "player" ? `${l.player_id.toString()}|${l.stat}|${l.line.toString()}` : ""))),
    );
    got.combos.forEach((c, i) => {
      expect(Math.abs(c.p_joint - (t.combos[i]?.p_joint ?? NaN))).toBeLessThan(EXACT);
    });
    expect(got.combos.every((c) => c.p_joint >= t.target)).toBe(true);
  });
  it("target candidates stay in the 0.35 to 0.75 band and never include a leg above the tested range", () => {
    const c = candidateLegs(G.game);
    expect(c.length).toBeGreaterThan(0);
    for (const l of c) {
      expect(l.p).toBeGreaterThanOrEqual(0.35);
      expect(l.p).toBeLessThanOrEqual(0.75);
      expect(legAboveTestedRange(l, G.game)).toBe(false);
    }
  });
  it("an unreachable target says infeasible and returns the highest joint found", () => {
    const got = targetSearch(G.game, G.shared, 0.99, 2, 3);
    expect(got.feasible).toBe(false);
    expect(got.combos.length).toBeGreaterThan(0);
    expect(got.combos[0]?.p_joint).toBeLessThan(0.99);
  });
  it("whole-number lines and game legs inside a script are refused", () => {
    const leg = (line: number): Leg => ({ kind: "player", game_id: "G1", player_id: 1, stat: "pts", line, side: "over" });
    expect(() => evaluateGameLegs([leg(24)], G.game, G.shared)).toThrow();
    expect(() =>
      evaluateGameLegs([{ kind: "total", game_id: "G1", line: 227.5, side: "over" }], G.game, G.shared, "blowout"),
    ).toThrow();
  });
});
