import { VisualizerSnapshot } from '../types';

const base =
  (import.meta.env.VITE_VISUALIZER_API_BASE || 'http://localhost:8081').replace(/\/$/, '');
const endpointFor = (runId?: string) => (runId ? `/visualizer/runs/${runId}` : '/visualizer/latest');

export async function fetchVisualizerSnapshot(runId?: string): Promise<VisualizerSnapshot | null> {
  const endpoint = endpointFor(runId);
  const url = base ? `${base}${endpoint}` : endpoint;
  const headers: Record<string, string> = {};
  const apiKey = import.meta.env.VITE_ACTIONS_API_KEY;
  if (apiKey) {
    headers['x-api-key'] = apiKey;
  }

  const response = await fetch(url, { headers });
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw new Error(`Visualizer request failed (${response.status})`);
  }
  return response.json();
}

export async function startVisualizerRun(options?: {
  prompt_file?: string;
  agent?: string;
  max_iterations?: number;
}) {
  const url = `${base}/visualizer/start`;
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const apiKey = import.meta.env.VITE_ACTIONS_API_KEY;
  if (apiKey) {
    headers['x-api-key'] = apiKey;
  }
  const response = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(options || {}),
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Failed to start run (${response.status})`);
  }
  return response.json() as Promise<VisualizerSnapshot>;
}

export async function cancelVisualizerRun(runId: string) {
  const url = `${base}/runs/${runId}`;
  const headers: Record<string, string> = {};
  const apiKey = import.meta.env.VITE_ACTIONS_API_KEY;
  if (apiKey) {
    headers['x-api-key'] = apiKey;
  }
  const response = await fetch(url, { method: 'DELETE', headers });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `Failed to cancel run (${response.status})`);
  }
  return response.json();
}
