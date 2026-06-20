import { type ReactNode } from "react";
import { Bot, Sparkles } from "lucide-react";

export function AIPanel(): ReactNode {
  return (
    <section className="ai-panel">
      <header className="panel-heading">
        <Bot size={15} />
        <h2>AI proposal</h2>
      </header>
      <p className="muted">AI remains off until a provider and a validated change proposal are configured.</p>
      <button className="text-button" disabled type="button">
        <Sparkles size={15} />Generate proposal
      </button>
    </section>
  );
}
