import type { ComponentChildren } from "preact";
import { useState } from "preact/hooks";
import { count } from "../format";
import { nextSort, sortRows, type SortDir, type SortState } from "./logic";

export interface Column<T> {
  key: string;
  head: ComponentChildren;
  cell: (r: T) => ComponentChildren;
  /** The stored value to sort by; a column without one is not sortable. */
  sort?: (r: T) => number | string | null | undefined;
  first?: SortDir;
  num?: boolean;
  /** Stacked layout (phones): the cell's label when `head` is not plain text. */
  label?: string;
  /** Stacked layout: a lead cell (the row's name) spans the block and carries no label; the first one is bold. */
  lead?: boolean;
  /** Stacked layout: the cell spans the whole block (long text, an interval). */
  wide?: boolean;
}

/** The label a stacked cell shows: `label`, else the head when it is plain text. */
export function stackLabel<T>(c: Column<T>): string | null {
  if (c.lead) return null;
  if (c.label !== undefined) return c.label;
  return typeof c.head === "string" || typeof c.head === "number" ? String(c.head) : null;
}

/**
 * A table with a caption, its row count, and sortable headers that say their state (`aria-sort`). `sticky` keeps the
 * header row in view inside a scrolling frame (long tables). `stack` (opt-in): below 640 px each row becomes a labelled
 * block (lead cells on top, every other cell under its column's name); the header row stays as the sort buttons, and the
 * explicit table roles keep rows, cells and column headers available to assistive tech when CSS changes the display.
 */
export function Table<T>(props: { caption: ComponentChildren; rows: readonly T[]; columns: readonly Column<T>[]; rowKey: (r: T) => string; initial?: SortState | null; noun?: string; class?: string; sticky?: boolean; stack?: boolean }) {
  const [sort, setSort] = useState<SortState | null>(props.initial ?? null);
  const col = sort ? props.columns.find((c) => c.key === sort.key) : undefined;
  const rows = sort && col?.sort ? sortRows(props.rows, col.sort, sort.dir) : [...props.rows];
  const stack = props.stack === true;
  const role = (r: "table" | "rowgroup" | "row" | "columnheader" | "cell"): typeof r | undefined => (stack ? r : undefined);
  const cellClass = (c: Column<T>): string | undefined => [c.num ? "num" : "", stack && c.lead ? "st-lead" : "", stack && c.wide ? "st-wide" : ""].filter(Boolean).join(" ") || undefined;
  return (
    <div class={`table-wrap${props.sticky ? " sticky" : ""}${stack ? " stack" : ""}`}>
      <table class={`ktable${stack ? " stack" : ""}${props.class ? ` ${props.class}` : ""}`} role={role("table")}>
        <caption>
          {props.caption} <span class="muted small num">({count(props.rows.length)} {props.noun ?? (props.rows.length === 1 ? "row" : "rows")})</span>
        </caption>
        <thead role={role("rowgroup")}>
          <tr role={role("row")}>
            {props.columns.map((c) => {
              const state = sort?.key === c.key ? sort.dir : "none";
              return (
                <th key={c.key} scope="col" role={role("columnheader")} aria-sort={c.sort ? state : undefined} class={c.num ? "num" : undefined}>
                  {c.sort ? (
                    <button
                      type="button"
                      class="sort-btn"
                      onClick={() => {
                        setSort((s) => nextSort(s, c.key, c.first));
                      }}
                    >
                      {c.head}
                      <span class="sort-mark" aria-hidden="true">{state === "ascending" ? "▲" : state === "descending" ? "▼" : "↕"}</span>
                    </button>
                  ) : (
                    c.head
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody role={role("rowgroup")}>
          {rows.map((r) => (
            <tr key={props.rowKey(r)} role={role("row")}>
              {props.columns.map((c) => {
                const label = stack ? stackLabel(c) : null;
                return (
                  <td key={c.key} class={cellClass(c)} role={role("cell")}>
                    {label !== null ? (
                      <span class="st-label" aria-hidden="true">
                        {label}
                      </span>
                    ) : null}
                    {c.cell(r)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
