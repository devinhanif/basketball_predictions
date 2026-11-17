import type { ComponentChildren } from "preact";

/**
 * A chart with what it is for: a title, a one-line takeaway (built by the caller from stored fields), the chart itself,
 * and a text alternative: the same numbers as a table behind "Show the numbers", so nothing is only in the picture.
 */
export function ChartFrame(props: { title: string; takeaway?: ComponentChildren; children: ComponentChildren; table?: { head: readonly string[]; rows: readonly (readonly ComponentChildren[])[]; caption?: string } }) {
  return (
    <figure class="chart-frame">
      <figcaption>
        <strong class="chart-title">{props.title}</strong>
        {props.takeaway ? <span class="chart-take small muted">{props.takeaway}</span> : null}
      </figcaption>
      {props.children}
      {props.table ? (
        <details class="chart-numbers">
          <summary>Show the numbers</summary>
          <div class="table-wrap">
            <table class="ktable">
              <caption class="sr">{props.table.caption ?? props.title}</caption>
              <thead>
                <tr>
                  {props.table.head.map((h) => (
                    <th key={h} scope="col">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {props.table.rows.map((r, i) => (
                  <tr key={i}>
                    {r.map((c, j) => (
                      <td key={j} class="num">
                        {c}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </figure>
  );
}
