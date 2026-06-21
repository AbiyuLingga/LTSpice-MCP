import { type ReactNode, useEffect, useState } from "react";
import { Bot, Check, FlaskConical, Sparkles, X } from "lucide-react";

import { type EngineBridge, type EngineProject } from "../engine";

type ContextPreview = {
  documents: Array<{ document: string; redacted: boolean; size: number }>;
  estimatedBytes: number;
  snapshotId: string;
};

type AIProposalResult = {
  changeSet: Record<string, unknown>;
  proposal: {
    operations: Array<{ document: string; payload: Record<string, unknown>; type: string }>;
    rationale: string;
    warnings: string[];
  };
  validation: { isValid: boolean; issues: string[] };
};

export interface AIPanelProps {
  bridge: EngineBridge;
  project: EngineProject | null;
  onApplied(project: EngineProject): void;
}

export function AIPanel({ bridge, project, onApplied }: AIPanelProps): ReactNode {
  const [configured, setConfigured] = useState(false);
  const [baseUrl, setBaseUrl] = useState("https://api.openai.com");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [prompt, setPrompt] = useState("");
  const [preview, setPreview] = useState<ContextPreview | null>(null);
  const [proposal, setProposal] = useState<AIProposalResult | null>(null);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    bridge.request<{ configured: boolean }>("ai.provider.status", {})
      .then((result) => setConfigured(result.configured))
      .catch(() => setConfigured(false));
  }, [bridge]);

  async function configure() {
    setBusy(true);
    try {
      await bridge.request("ai.provider.configure", { apiKey, baseUrl, model });
      setApiKey("");
      setConfigured(true);
      setStatus("Provider saved");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Provider setup failed");
    } finally {
      setBusy(false);
    }
  }

  async function selfTest() {
    setBusy(true);
    try {
      const result = await bridge.request<{ notes: string; status: string }>("ai.provider.selfTest", {});
      setStatus(result.status === "pass" ? "Provider connected" : result.notes);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Provider test failed");
    } finally {
      setBusy(false);
    }
  }

  async function previewContext() {
    if (!project) return;
    setBusy(true);
    setProposal(null);
    try {
      const result = await bridge.request<ContextPreview>("ai.contextPreview", {
        documents: ["requirements", "analog", "schematic", "digital"],
        projectId: project.projectId,
        prompt,
      });
      setPreview(result);
      setStatus("Context ready");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Context preview failed");
    } finally {
      setBusy(false);
    }
  }

  async function propose() {
    if (!preview) return;
    setBusy(true);
    try {
      const result = await bridge.request<AIProposalResult>("ai.propose", {
        snapshotId: preview.snapshotId,
      });
      setProposal(result);
      setStatus(result.validation.isValid ? "Proposal validated" : "Proposal rejected by validation");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Proposal failed");
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!project || !proposal?.validation.isValid) return;
    setBusy(true);
    try {
      const result = await bridge.request<{ revision: number }>("design.applyChanges", {
        changeSet: proposal.changeSet,
        projectId: project.projectId,
      });
      onApplied({ ...project, revision: result.revision });
      setProposal(null);
      setPreview(null);
      setStatus("Proposal applied");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Apply failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="ai-panel">
      <header className="panel-heading"><Bot size={15} /><h2>AI proposal</h2></header>
      {!configured ? (
        <form className="inspector-form" onSubmit={(event) => { event.preventDefault(); void configure(); }}>
          <label>Endpoint<input aria-label="AI endpoint" onChange={(event) => setBaseUrl(event.target.value)} value={baseUrl} /></label>
          <label>Model<input aria-label="AI model" onChange={(event) => setModel(event.target.value)} value={model} /></label>
          <label>API key<input aria-label="AI API key" autoComplete="off" onChange={(event) => setApiKey(event.target.value)} type="password" value={apiKey} /></label>
          <button className="text-button" disabled={busy || !apiKey || !model} type="submit"><Check size={15} />Save provider</button>
        </form>
      ) : (
        <div className="ai-workflow">
          <button aria-label="Test AI provider" className="icon-button" disabled={busy} onClick={() => void selfTest()} title="Test provider" type="button"><FlaskConical size={15} /></button>
          <label>Design request<textarea aria-label="AI design request" onChange={(event) => { setPrompt(event.target.value); setPreview(null); setProposal(null); }} value={prompt} /></label>
          {!preview ? <button className="text-button" disabled={busy || !project || !prompt.trim()} onClick={() => void previewContext()} type="button"><Sparkles size={15} />Preview context</button> : null}
          {preview && !proposal ? (
            <div className="ai-preview">
              <strong>{preview.documents.length} documents</strong><span>{preview.estimatedBytes} bytes</span>
              <button className="text-button" disabled={busy} onClick={() => void propose()} type="button"><Sparkles size={15} />Generate proposal</button>
            </div>
          ) : null}
          {proposal ? (
            <div className="ai-diff" aria-label="AI proposal diff">
              {proposal.proposal.operations.map((operation, index) => <code key={`${operation.type}-${index}`}>{operation.document}: {operation.type}</code>)}
              {proposal.validation.issues.map((issue) => <p className="problem" key={issue}>{issue}</p>)}
              <div className="ai-actions">
                <button aria-label="Reject AI proposal" className="icon-button" disabled={busy} onClick={() => { setProposal(null); setStatus("Proposal rejected"); }} title="Reject" type="button"><X size={16} /></button>
                <button className="text-button" disabled={busy || !proposal.validation.isValid} onClick={() => void apply()} type="button"><Check size={15} />Apply</button>
              </div>
            </div>
          ) : null}
        </div>
      )}
      {status ? <output className="muted">{status}</output> : null}
    </section>
  );
}
