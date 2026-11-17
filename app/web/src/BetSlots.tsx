// The two places the betting module draws on other screens, each guarded by the build-time switch so that with it off
// they render nothing and the module is not in the bundle. Pages render <AddToSlipSlot .../> beside a leg; the shell
// renders <BetShellPanel .../> once beside the page.
import { BETTING } from "./features";
import { BETTING_MODULE, type AddToSlipProps, type ShellPanelProps } from "./betting";

export function AddToSlipSlot(props: AddToSlipProps) {
  if (!BETTING) return null;
  const Slot = BETTING_MODULE.AddToSlip;
  return <Slot {...props} />;
}

export function BetShellPanel(props: ShellPanelProps) {
  if (!BETTING) return null;
  const Panel = BETTING_MODULE.ShellPanel;
  return <Panel {...props} />;
}
