// Placeholders for panels that need fields the export does not carry yet (docs/contracts/PROPOSAL_export_fields_2026-11-17.md
// items 10 and 11). Collapsed, labelled, and empty of anything that looks like data.
export function NeedsField(props: { title: string; will: string; item: number }) {
  return (
    <details class="panel flat needs-field">
      <summary>
        {props.title} <span class="tag small">[needs export fields]</span>
      </summary>
      <p>
        This panel is empty on purpose: the nightly export does not carry the fields it needs yet, and nothing is filled in here in their place.
      </p>
      <p class="small">{props.will}</p>
      <p class="small muted">Export-field proposal, item {props.item.toString()} (docs/contracts/PROPOSAL_export_fields_2026-11-17.md).</p>
    </details>
  );
}

export function LegPlaceholders() {
  return (
    <>
      <NeedsField
        title="What moved it"
        item={10}
        will="It will show the stored parts of this forecast's middle: the recent-games average it starts from and the model's adjustment on top, each as the engine stored it."
      />
      <NeedsField
        title="Why this label"
        item={11}
        will="It will show the desk's own written reason for the label it gave this prop, copied from the engine rather than re-derived here."
      />
    </>
  );
}
