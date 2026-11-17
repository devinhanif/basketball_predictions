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
}

/** A table with a caption, its row count, and sortable headers that say their state (`aria-sort`). */
export function Table<T>(props: { caption: ComponentChildren; rows: readonly T[]; columns: readonly Column<T>[]; rowKey: (r: T) => string; initial?: SortState | null; noun?: string; class?: string }) {
  const [sort, setSort] = useState<SortState | null>(props.initial ?? null);
  const col = sort ? props.columns.find((c) => c.key === sort.key) : undefined;
  const rows = sort && col?.sort ? sortRows(props.rows, col.sort, sort.dir) : [...props.rows];
  return (
    <div class="table-wrap">
      <table class={`ktable${props.class ? ` ${props.class}` : ""}`}>
        <caption>
          {props.caption} <span class="muted small num">({count(props.rows.length)} {props.noun ?? (props.rows.length === 1 ? "row" : "rows")})</span>
        </caption>
        <thead>
          <tr>
            {props.columns.map((c) => {
              const state = sort?.key === c.key ? sort.dir : "none";
              return (
                <th key={c.key} scope="col" aria-sort={c.sort ? state : undefined} class={c.num ? "num" : undefined}>
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
        <tbody>
          {rows.map((r) => (
            <tr key={props.rowKey(r)}>
              {props.columns.map((c) => (
                <td key={c.key} class={c.num ? "num" : undefined}>
                  {c.cell(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
