import { useMemo, useState } from "preact/hooks";
import { Avatar } from "../Avatar";
import { DeskGate, SectionNote } from "../DeskUI";
import {
  barScale, calibration, cents, clvRate, dayBars, dayTally, filterLedger, ledgerCsv, LEDGER_FILTERS, playLabel, signedCents, usd, winRate,
  type DayBar, type Desk, type DeskPlay, type LedgerFilter,
} from "../desk";
import { longDate, pct } from "../format";
import { Icon } from "../icons";
import { saveTextFile } from "../saveFile";
import { pushToast } from "../toast";

const PAGE = 25;

function ciText(ci: [number, number] | null, unit: "c" | "%"): string {
  if (!ci) return "";
  return unit === "c" ? `95% interval ${ci[0].toFixed(2)}c to ${ci[1].toFixed(2)}c` : `95% interval ${pct(ci[0])} to ${pct(ci[1])}`;
}

function Tiles({ desk }: { desk: Desk }) {
  const clv = clvRate(desk);
  const rec = winRate(desk.record);
  const mvm = desk.track_record?.model_vs_market ?? null;
  const ks = desk.status.kill_switch;
  const c = desk.clv_summary;
  return (
    <section class="tiles three" aria-label="The numbers">
      <div class="tile big">
        <div class="label">Mean closing-line value</div>
        <div class="tile-v num">{clv.value !== null ? signedCents(clv.value, 2) : "not enough to say"}</div>
        <div class="tile-l">
          {clv.n > 0 ? `n = ${clv.n.toLocaleString("en-US")} settled plays with a stored close` : "No settled plays with a stored close"}
        </div>
        <div class="tile-n muted num">
          {clv.ci ? `${ciText(clv.ci, "c")}, clustered on ${c?.clustered_on.replace("_", " ") ?? "date"}${c?.n_dates ? ` (${c.n_dates.toString()} dates)` : ""}` : (clv.reason ?? "")}
        </div>
        <div class="tile-n muted">All settled plays with a stored close. JARVIS's kill-switch rule uses a different sample (below).</div>
      </div>
      <div class="tile big">
        <div class="label">Paper record</div>
        <div class="tile-v num">
          {desk.record ? `${desk.record.n_won.toString()}-${desk.record.n_lost.toString()}` : "not stored"}
        </div>
        <div class="tile-l">
          {desk.record ? `n = ${(desk.record.n_won + desk.record.n_lost).toLocaleString("en-US")} decided, ${desk.record.n_void.toString()} void` : ""}
        </div>
        <div class="tile-n muted num">
          {rec.value !== null ? `Won ${pct(rec.value)}. ` : ""}
          {rec.ci ? ciText(rec.ci, "%") : (rec.reason ?? "")}
        </div>
        <div class="tile-n muted">Win rate is not the measure of edge: price matters more than hits.</div>
      </div>
      {desk.balance ? (
        <div class="tile big">
          <div class="label">Paper balance (private)</div>
          <div class="tile-v num">{`$${desk.balance.current_usd.toFixed(2)}`}</div>
          <div class="tile-l num">{`$${desk.balance.start_usd.toFixed(0)} start, $${desk.balance.floor_usd.toFixed(0)} floor`}</div>
          <div class="tile-n muted">Shown only in the private build; the shared page carries no money.</div>
        </div>
      ) : (
        <div class="tile big">
          <div class="label">Model against the market</div>
          <div class="tile-v num">{mvm?.diff != null ? `${mvm.diff >= 0 ? "+" : "-"}${Math.abs(mvm.diff).toFixed(4)}` : "not stored"}</div>
          <div class="tile-l num">
            {mvm ? `Brier difference, n = ${mvm.n.toLocaleString("en-US")}${mvm.games ? ` forecasts in ${mvm.games.toString()} games` : ""}` : (desk.section_notes["track_record.model_vs_market"] ?? "")}
          </div>
          <div class="tile-n muted num">
            {mvm?.ci95 ? `95% interval ${mvm.ci95[0].toFixed(4)} to ${mvm.ci95[1].toFixed(4)}, clustered on ${mvm.clustered_on.replace("_", " ")}. ` : (mvm?.reason ?? "")}
            {mvm?.note ?? ""}
          </div>
          <div class="tile-n muted">Positive means the model scored worse than the market.</div>
        </div>
      )}
      <div class={`tile big${ks.on ? " alarm" : ""}`}>
        <div class="label">Discipline: JARVIS's kill switch</div>
        <div class="tile-v">{ks.on ? "On" : "Off"}</div>
        <div class="tile-l num">
          {ks.n !== null && ks.mean_clv_cents !== null
            ? `its own sample: n = ${ks.n.toString()} bets, mean ${signedCents(ks.mean_clv_cents, 2)}`
            : "no sample stored"}
          {ks.since ? `, since ${longDate(ks.since.slice(0, 10))}` : ""}
        </div>
        <div class="tile-n muted">
          {ks.note ?? ""} This is a different population from the closing-line value tile. The model weight is {desk.status.w_model ?? "not stored"}
          {Object.keys(desk.status.w_model_by_stat).length > 0 ? ` (${Object.entries(desk.status.w_model_by_stat).map(([k, v]) => `${k} ${v.toString()}`).join(", ")})` : ""}.
        </div>
      </div>
    </section>
  );
}

function DayChart(props: { bars: DayBar[]; selected: string | null; onSelect: (d: string) => void }) {
  const { bars } = props;
  const scale = barScale(bars);
  const W = Math.max(560, bars.length * 52 + 70);
  const H = 240;
  const mid = 120;
  const unit = 78 / scale;
  const x0 = 56;
  const step = (W - x0 - 10) / Math.max(1, bars.length);
  return (
    <div class="chart-wrap">
      <svg viewBox={`0 0 ${W.toString()} ${H.toString()}`} width={W} height={H} role="group" aria-label="Mean closing-line value by day, in cents">
        <line class="axis" x1={x0} x2={W - 6} y1={mid} y2={mid} />
        <text class="tick" x={x0 - 6} y={mid + 4} text-anchor="end">0c</text>
        <text class="tick" x={x0 - 6} y={mid - 78 + 4} text-anchor="end">{`+${scale.toFixed(1)}c`}</text>
        <text class="tick" x={x0 - 6} y={mid + 78 + 4} text-anchor="end">{`-${scale.toFixed(1)}c`}</text>
        {bars.map((b, i) => {
          const cx = x0 + step * i + step / 2;
          const h = b.value === null ? 0 : Math.abs(b.value) * unit;
          const up = (b.value ?? 0) >= 0;
          const label = b.value === null ? "none" : signedCents(b.value, 1);
          const sel = props.selected === b.date;
          return (
            <g
              key={b.date}
              role="button"
              tabIndex={0}
              aria-pressed={sel}
              aria-label={`${longDate(b.date)}: ${b.n.toString()} bets, mean closing-line value ${label}`}
              class={`bar${sel ? " sel" : ""}`}
              onClick={() => {
                props.onSelect(b.date);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  props.onSelect(b.date);
                }
              }}
            >
              {sel ? <rect class="sel-bg" x={cx - step / 2 + 2} y={8} width={step - 4} height={H - 40} rx="6" /> : null}
              <rect class={up ? "up" : "down"} x={cx - 14} y={up ? mid - h : mid} width="28" height={Math.max(h, b.value === null ? 0 : 2)} rx="3" />
              <text class="val" x={cx} y={up ? mid - h - 5 : mid + h + 14} text-anchor="middle">{label}</text>
              <text class="tick" x={cx} y={H - 14} text-anchor="middle">{b.date.slice(5).replace("-", "/")}</text>
              <text class="tick" x={cx} y={H - 1} text-anchor="middle">{`n=${b.n.toString()}`}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function DaysCard({ desk }: { desk: Desk }) {
  const bars = useMemo(() => dayBars(desk), [desk]);
  const [sel, setSel] = useState<string | null>(bars.at(-1)?.date ?? null);
  const t = dayTally(bars);
  const day = bars.find((b) => b.date === sel);
  const plays = desk.plays.filter((p) => p.slate_date === sel && p.result !== null);
  return (
    <section class="panel" aria-labelledby="days-h">
      <div class="row between wrap">
        <div>
          <h2 id="days-h">A day in perspective</h2>
          <p class="small muted">Mean closing-line value of each day's settled plays, in cents. Each bar says its sign in words.</p>
        </div>
        <p class="small num tally">
          <span class="pos">{t.up} up</span> · <span class="neg">{t.down} down</span> · {t.flat} flat{t.none > 0 ? ` · ${t.none.toString()} no close stored` : ""}
        </p>
      </div>
      {bars.length === 0 ? (
        <p class="muted">No settled days are stored yet.</p>
      ) : (
        <>
          <DayChart bars={bars} selected={sel} onSelect={setSel} />
          {day ? (
            <div class="selected">
              <div class="label">Selected day</div>
              <h3 class="num">
                {longDate(day.date)}: {day.value === null ? "no close stored" : signedCents(day.value, 2)}
              </h3>
              <p class="small muted num">
                {day.n} settled, {day.won}-{day.n - day.won} won-lost
                {day.pnl !== null ? `, net ${usd(day.pnl)}` : ""}. A single day is a small sample; it says little by itself.
              </p>
              {plays.length > 0 ? (
                <ul class="plain small">
                  {plays.slice(0, 6).map((p) => (
                    <li key={p.play_id} class="num">
                      {playLabel(p)} ({p.matchup ?? "game not stored"}): entry {cents(p.price_cents)}, closing-line value {signedCents(p.result?.clv_cents ?? null)}
                    </li>
                  ))}
                  {plays.length > 6 ? <li class="muted">and {plays.length - 6} more in the ledger below</li> : null}
                </ul>
              ) : null}
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function CalibrationCard({ desk }: { desk: Desk }) {
  const cal = calibration(desk);
  const settled = desk.record ? desk.record.n_won + desk.record.n_lost : 0;
  if (!cal) {
    return (
      <section class="panel" aria-labelledby="cal-h">
        <div class="row between">
          <h2 id="cal-h">Calibration</h2>
          <span class="chip">Building</span>
        </div>
        <svg class="cal-ghost" viewBox="0 0 200 70" aria-hidden="true">
          {[0, 1, 2, 3, 4, 5].map((i) => (
            <rect key={i} x={10 + i * 31} y={62 - 8 - i * 8} width="22" height={8 + i * 8} rx="3" />
          ))}
        </svg>
        <h3>A fuller picture takes time</h3>
        <p class="small muted">
          {desk.section_notes["track_record.calibration"] ?? "Calibration needs timestamped predictions paired with resolved outcomes."} Until bins are stored, no calibration curve is drawn.
        </p>
        <ul class="status-list">
          <li>
            <span>Paper results</span>
            <span class="chip ok num">{settled.toLocaleString("en-US")} settled</span>
          </li>
          <li>
            <span>Prediction timestamps</span>
            <span class="chip">Stored per play</span>
          </li>
          <li>
            <span>Binned forecast scorecard</span>
            <span class="chip off">Not stored</span>
          </li>
        </ul>
      </section>
    );
  }
  const S = 240;
  const pt = (v: number): number => 24 + v * (S - 40);
  return (
    <section class="panel" aria-labelledby="cal-h">
      <h2 id="cal-h">Calibration</h2>
      <p class="small muted num">
        Predicted chance against how often it happened, n = {cal.total.toLocaleString("en-US")}
        {desk.track_record?.scorecard_date ? `, scorecard of ${longDate(desk.track_record.scorecard_date)}` : ""}. On the dashed line the forecast is right.
      </p>
      <svg viewBox={`0 0 ${S.toString()} ${S.toString()}`} width="100%" role="img" aria-label="Calibration: predicted against observed" class="cal">
        <line class="axis" x1={pt(0)} y1={S - 16} x2={pt(1)} y2={S - 16} />
        <line class="axis" x1={24} y1={S - 16} x2={24} y2={S - pt(1) + 24 - 16} />
        <line class="diag" x1={pt(0)} y1={S - pt(0) + 8} x2={pt(1)} y2={S - pt(1) + 8} />
        {cal.bins.map((b) => (
          <circle key={b.bin_lo} class="pt" cx={pt(b.mean_pred)} cy={S - pt(b.hit_rate) + 8} r={Math.min(12, 3 + Math.sqrt(b.n))} />
        ))}
        <text class="tick" x={pt(0)} y={S - 2}>0%</text>
        <text class="tick" x={pt(1)} y={S - 2} text-anchor="end">100%</text>
        <text class="tick" x={4} y={S - pt(1) + 12}>100%</text>
      </svg>
      <div class="table-wrap">
        <table class="num small">
          <thead>
            <tr>
              <th>Predicted</th>
              <th class="r">Said</th>
              <th class="r">Happened</th>
              <th class="r">n</th>
            </tr>
          </thead>
          <tbody>
            {cal.bins.map((b) => (
              <tr key={b.bin_lo}>
                <td>
                  {pct(b.bin_lo)} to {pct(b.bin_hi)}
                </td>
                <td class="r">{pct(b.mean_pred)}</td>
                <td class="r">{pct(b.hit_rate)}</td>
                <td class="r">{b.n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function resultWord(p: DeskPlay): string {
  const r = p.result;
  if (!r) return "Open";
  if (r.won === true) return "Hit";
  if (r.won === false) return "Miss";
  return r.status === "open" ? "Open" : "Void";
}

function LedgerCard({ desk }: { desk: Desk }) {
  const withMoney = desk.privacy === "private";
  const rows = useMemo(() => [...desk.plays].sort((a, b) => b.ts.localeCompare(a.ts)), [desk]);
  const [f, setF] = useState<LedgerFilter>("all");
  const [shown, setShown] = useState(PAGE);
  const view = useMemo(() => filterLedger(rows, f), [rows, f]);
  const doExport = (): void => {
    void saveTextFile("paper-ledger.csv", ledgerCsv(view, withMoney)).then((r) => {
      if (r.ok) pushToast("Ledger", r.how === "downloaded" ? "The ledger was downloaded." : "The ledger was handed to your save dialog.");
      else pushToast("Ledger not saved", r.reason);
    });
  };
  return (
    <section class="panel" aria-labelledby="led-h">
      <div class="row between wrap">
        <div>
          <h2 id="led-h">Paper trade ledger</h2>
          <p class="small muted num">
            {view.length.toLocaleString("en-US")} of {rows.length.toLocaleString("en-US")} records. Filters sort by {withMoney ? "net P&L" : "closing-line value"}.
          </p>
        </div>
        <button type="button" class="btn" onClick={doExport} disabled={view.length === 0}>
          <Icon name="download" /> Export ledger
        </button>
      </div>
      <div class="tabs filters" role="group" aria-label="Filter the ledger">
        {LEDGER_FILTERS.map((x) => (
          <button
            key={x.value}
            type="button"
            class="tab"
            aria-current={f === x.value ? "true" : undefined}
            onClick={() => {
              setF(x.value);
              setShown(PAGE);
            }}
          >
            {x.label}
          </button>
        ))}
      </div>
      {view.length === 0 ? (
        <p class="muted empty-line">No records match this filter.</p>
      ) : (
        <div class="table-wrap">
          <table class="ledger num">
            <thead>
              <tr>
                <th>Player / contract</th>
                <th class="r">Entry</th>
                <th>Signal</th>
                <th class="r">Closing-line value</th>
                <th>Result</th>
                {withMoney ? <th class="r">Net P&amp;L</th> : null}
              </tr>
            </thead>
            <tbody>
              {view.slice(0, shown).map((p) => (
                <tr key={p.play_id}>
                  <td>
                    <span class="who">
                      <Avatar name={p.player_name ?? "Unnamed"} playerId={p.player_id} size="sm" />
                      <span>
                        <strong>{playLabel(p)}</strong>
                        <span class="muted small"> {p.matchup ?? ""} · {longDate(p.slate_date).replace(/, \d{4}$/, "")}</span>
                      </span>
                    </span>
                  </td>
                  <td class="r">{cents(p.price_cents)}</td>
                  <td>
                    {p.edge_label ?? "none"} {p.edge_cents !== null ? signedCents(p.edge_cents, 1) : ""}
                  </td>
                  <td class="r">{signedCents(p.result?.clv_cents ?? null)}</td>
                  <td>{resultWord(p)}</td>
                  {withMoney ? <td class="r">{p.result?.pnl_usd != null ? usd(p.result.pnl_usd) : "not stored"}</td> : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {view.length > shown ? (
        <button
          type="button"
          class="btn"
          onClick={() => {
            setShown((s) => s + PAGE);
          }}
        >
          Show more ({(view.length - shown).toLocaleString("en-US")} left)
        </button>
      ) : null}
      <SectionNote desk={desk} k="shared_cut" />
    </section>
  );
}

function Body({ desk }: { desk: Desk }) {
  const clv = clvRate(desk);
  return (
    <div class="stack">
      <header class="page-head">
        <div class="kicker">Model history</div>
        <h1 class="answer">
          {desk.record ? `${desk.record.n_won.toString()}-${desk.record.n_lost.toString()} on ${(desk.record.n_won + desk.record.n_lost).toLocaleString("en-US")} paper bets` : "No paper record is stored"}
          {clv.value !== null ? ` · closing-line value ${signedCents(clv.value, 2)} (n = ${clv.n.toLocaleString("en-US")})` : ""}
          {desk.status.kill_switch.on ? " · kill switch on" : ""}
        </h1>
        <p class="muted answer-src">
          A record of paper results, costs and discipline. As of {longDate(desk.data_as_of.slice(0, 10))}.
        </p>
      </header>
      <Tiles desk={desk} />
      <div class="two-col wide-left">
        <DaysCard desk={desk} />
        <CalibrationCard desk={desk} />
      </div>
      <LedgerCard desk={desk} />
    </div>
  );
}

export function History() {
  return <DeskGate title="Model History">{(desk) => <Body desk={desk} />}</DeskGate>;
}
