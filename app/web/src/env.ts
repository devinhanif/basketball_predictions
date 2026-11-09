/** True only in the single-file artifact build (`npm run build:artifact`). Set by vite at build time. */
const flag: unknown = import.meta.env.VITE_ARTIFACT;
export const IS_ARTIFACT: boolean = flag === "1";
