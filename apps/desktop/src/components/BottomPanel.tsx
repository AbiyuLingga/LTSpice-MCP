import { type ReactNode } from "react";
import { Gauge, Square, TerminalSquare } from "lucide-react";

import { type BottomTab } from "./WorkspaceSurface";

export interface BottomPanelProps {
  bottomTab: BottomTab;
  jobMessage: string;
  activeJobId: string | null;
  onCancelJob: () => void;
  onBottomTabChange: (next: BottomTab) => void;
}

export function BottomPanel(props: BottomPanelProps): ReactNode {
  return (
    <section className="bottom-panel panel" aria-label="Problems jobs and console">
      <div className="bottom-tabs" role="tablist">
        <button
          aria-selected={props.bottomTab === "problems"}
          onClick={() => props.onBottomTabChange("problems")}
          role="tab"
          type="button"
        >
          Problems <span>0</span>
        </button>
        <button
          aria-selected={props.bottomTab === "jobs"}
          onClick={() => props.onBottomTabChange("jobs")}
          role="tab"
          type="button"
        >
          Jobs
        </button>
        <button
          aria-selected={props.bottomTab === "console"}
          onClick={() => props.onBottomTabChange("console")}
          role="tab"
          type="button"
        >
          <TerminalSquare size={14} />Console
        </button>
      </div>
      <div aria-live="polite" className="bottom-content">
        {props.bottomTab === "problems" ? "No validation problems" : null}
        {props.bottomTab === "jobs" ? (
          <span className="job-line">
            <Gauge size={15} />
            {props.jobMessage}
            {props.activeJobId ? (
              <button aria-label="Cancel job" className="icon-button compact" onClick={props.onCancelJob} title="Cancel job" type="button">
                <Square size={12} />
              </button>
            ) : null}
          </span>
        ) : null}
        {props.bottomTab === "console" ? (
          <code>Engine bridge ready. Simulator jobs will appear here.</code>
        ) : null}
      </div>
    </section>
  );
}
