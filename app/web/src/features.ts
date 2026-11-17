// The app's feature switches, read at build time. This is the ONLY web file that reads the betting switch; every other
// file imports BETTING from here.
//
// The betting tools module (src/betting/) is OFF unless the build ran with the switch set to "1" (see app/README.md,
// "The betting switch"). Vite replaces the env read below with a literal at build time and the minifier folds BETTING
// to `false`, so everything written as `BETTING ? ... : ...` or `if (BETTING)` drops out of both builds (the regular
// one and the single-file artifact) together with the module it imports. scripts/flag-parity.mjs proves that on real
// builds: the OFF bundles carry none of the strings in src/betting/markers.ts, the ON bundles carry them all.

const betting: unknown = import.meta.env.VITE_PH_BETTING;

/** True only in a build made with the betting switch on. A build-time constant: never changes while the page runs. */
export const BETTING: boolean = betting === "1";

/** The switch's name, for the test that checks no other source file reads it. Not used by the app. */
export const BETTING_ENV_NAME = "VITE_PH_BETTING";
