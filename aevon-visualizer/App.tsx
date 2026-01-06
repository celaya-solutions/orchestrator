import React, { useState, useEffect, useCallback } from 'react';
import {
  AgentType,
  RunStatus,
  OrchestratorConfig,
  OrchestratorState,
  IterationLog,
  VisualizerSnapshot,
} from './types';
import { DEFAULT_CONFIG, ICONS } from './constants';
import { fetchVisualizerSnapshot, startVisualizerRun, cancelVisualizerRun } from './services/visualizerService';
import MetricsVisualizer from './components/MetricsVisualizer';
import LoopVisualizer from './components/LoopVisualizer';

const POLL_INTERVAL_MS = 5000;

const mapStatus = (status?: string): RunStatus => {
  const normalized = (status || '').toLowerCase();
  if (normalized.includes('run')) return RunStatus.RUNNING;
  if (normalized.includes('complete')) return RunStatus.COMPLETED;
  if (normalized.includes('fail')) return RunStatus.FAILED;
  if (normalized.includes('cancel')) return RunStatus.FAILED;
  return RunStatus.IDLE;
};

const App: React.FC = () => {
  const [config, setConfig] = useState<OrchestratorConfig>(DEFAULT_CONFIG as any);
  const [promptFile, setPromptFile] = useState<string>('PROMPT.md');
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);
  const [state, setState] = useState<OrchestratorState>({
    status: RunStatus.IDLE,
    currentIteration: 0,
    totalIterations: 0,
    elapsedTime: 0,
    totalTokens: 0,
    totalCost: 0,
    logs: [],
  });
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [isCancelling, setIsCancelling] = useState(false);

  const applySnapshot = useCallback((snapshot: VisualizerSnapshot | null) => {
    if (!snapshot) {
      setRunId(null);
      setState(prev => ({ ...prev, status: RunStatus.IDLE, logs: [], currentIteration: 0, totalIterations: 0 }));
      return;
    }

    const logs: IterationLog[] = (snapshot.iterations || [])
      .map((iter) => ({
        id: `${snapshot.run_id || 'run'}-${iter.iteration}-${iter.timestamp || Math.random().toString(36).slice(2)}`,
        iteration: iter.iteration || 0,
        timestamp: iter.timestamp ? new Date(iter.timestamp).getTime() : Date.now(),
        tokens: iter.tokens || 0,
        cost: iter.cost || 0,
        status: (iter.status || '').toLowerCase() === 'success' ? 'success' : 'retry',
        message: iter.message || '',
      }))
      .reverse()
      .slice(0, 50);

    setRunId(snapshot.run_id || null);
    setState({
      status: mapStatus(snapshot.status),
      currentIteration: snapshot.current_iteration || 0,
      totalIterations: snapshot.total_iterations || snapshot.current_iteration || 0,
      elapsedTime: snapshot.elapsed_seconds || 0,
      totalTokens: snapshot.total_tokens || 0,
      totalCost: snapshot.total_cost || 0,
      logs,
    });
  }, []);

  const refreshSnapshot = useCallback(async () => {
    setIsLoading(true);
    try {
      const snapshot = await fetchVisualizerSnapshot();
      applySnapshot(snapshot);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to sync visualizer');
    } finally {
      setIsLoading(false);
    }
  }, [applySnapshot]);

  const resetView = useCallback(() => {
    setRunId(null);
    setState({
      status: RunStatus.IDLE,
      currentIteration: 0,
      totalIterations: 0,
      elapsedTime: 0,
      totalTokens: 0,
      totalCost: 0,
      logs: [],
    });
  }, []);

  const handleStartRun = useCallback(async () => {
    setIsStarting(true);
    try {
      const snapshot = await startVisualizerRun({
        prompt_file: promptFile,
        agent: config.agent,
        max_iterations: config.maxIterations,
      });
      applySnapshot(snapshot);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to start run');
    } finally {
      setIsStarting(false);
    }
  }, [applySnapshot, config.agent, config.maxIterations, promptFile]);

  const handleCancelRun = useCallback(async () => {
    if (!runId) return;
    setIsCancelling(true);
    try {
      await cancelVisualizerRun(runId);
      resetView();
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to cancel run');
    } finally {
      setIsCancelling(false);
    }
  }, [resetView, runId]);

  useEffect(() => {
    refreshSnapshot();
    const interval = window.setInterval(refreshSnapshot, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [refreshSnapshot]);

  return (
    <div className="flex h-screen bg-black overflow-hidden font-sans">
      {/* Mobile Nav Overlay */}
      <div
        className={`lg:hidden fixed inset-0 z-50 bg-black/60 backdrop-blur-sm transition-opacity ${isSidebarOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}
        onClick={() => setIsSidebarOpen(false)}
      />

      {/* Navigation / Control Sidebar */}
      <aside
        className={`fixed lg:static inset-y-0 left-0 z-50 w-72 bg-[#050505] retina-border border-y-0 border-l-0 transition-transform duration-300 transform ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'} flex flex-col`}
      >
        <div className="p-8 border-b border-white/5">
          <div className="flex items-center gap-3 group">
            <div className="p-2 bg-blue-600 rounded flex items-center justify-center transition-transform group-hover:scale-110">
              <ICONS.Loop className="w-5 h-5 text-white" />
            </div>
            <div>
              <h1 className="text-sm font-bold tracking-tight text-white uppercase">RALPH CORE</h1>
              <p className="text-[9px] mono text-zinc-500 tracking-[0.2em] uppercase">Autonomous Loop</p>
            </div>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-8 space-y-10">
          <section className="space-y-6">
            <div className="flex items-center gap-2 mb-2">
              <div className="w-1 h-3 bg-blue-600 rounded-full" />
              <h2 className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">Parameters</h2>
            </div>

            <div className="space-y-5">
              <div className="space-y-2">
                <label className="text-[11px] font-medium text-zinc-400">Prompt File</label>
                <input
                  type="text"
                  className="w-full bg-[#0d0d0d] border border-white/10 rounded-lg px-4 py-2 text-xs"
                  value={promptFile}
                  onChange={e => setPromptFile(e.target.value)}
                  placeholder="PROMPT.md"
                />
              </div>

              <div className="space-y-2">
                <label className="text-[11px] font-medium text-zinc-400">Agent Interface</label>
                <select
                  className="w-full bg-[#0d0d0d] border border-white/10 rounded-lg px-4 py-2.5 text-xs focus:ring-1 focus:ring-blue-600 outline-none transition-all appearance-none"
                  value={config.agent}
                  onChange={e => setConfig({ ...config, agent: e.target.value as AgentType })}
                >
                  <option value="gemini">Gemini Flash (L-09)</option>
                  <option value="claude">Claude 3.5 Sonnet</option>
                  <option value="ollama">Local Compute</option>
                </select>
              </div>

              <div className="space-y-2">
                <label className="text-[11px] font-medium text-zinc-400">Objective Definition</label>
                <textarea
                  className="w-full bg-[#0d0d0d] border border-white/10 rounded-lg px-4 py-3 text-xs h-32 resize-none focus:ring-1 focus:ring-blue-600 outline-none transition-all placeholder:text-zinc-700 leading-relaxed"
                  value={config.prompt}
                  onChange={e => setConfig({ ...config, prompt: e.target.value })}
                  placeholder="Input strategic recursive prompt..."
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <label className="text-[11px] font-medium text-zinc-400">Max Cycles</label>
                  <input
                    type="number"
                    className="w-full bg-[#0d0d0d] border border-white/10 rounded-lg px-4 py-2 text-xs"
                    value={config.maxIterations}
                    onChange={e => setConfig({ ...config, maxIterations: parseInt(e.target.value) })}
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-[11px] font-medium text-zinc-400">Delay (s)</label>
                  <input
                    type="number"
                    className="w-full bg-[#0d0d0d] border border-white/10 rounded-lg px-4 py-2 text-xs"
                    value={config.retryDelay}
                    onChange={e => setConfig({ ...config, retryDelay: parseInt(e.target.value) })}
                  />
                </div>
              </div>
            </div>
          </section>
        </div>

        <div className="p-8 space-y-3 bg-[#050505] border-t border-white/5">
          <button
            onClick={handleStartRun}
            className={`w-full py-3.5 rounded-xl text-[11px] font-bold tracking-widest transition-all flex items-center justify-center gap-2 ${
              isStarting ? 'bg-zinc-800 text-white' : 'bg-emerald-600 text-white hover:bg-emerald-500 shadow-xl shadow-emerald-600/10'
            }`}
          >
            {isStarting ? 'STARTING…' : 'START RUN'}
          </button>

          <button
            onClick={handleCancelRun}
            disabled={!runId || isCancelling}
            className="w-full py-3 rounded-xl text-[11px] font-bold border border-white/5 text-zinc-500 hover:text-white hover:bg-white/5 transition-all disabled:opacity-30"
          >
            {isCancelling ? 'CANCELLING…' : 'CANCEL RUN'}
          </button>

          <button
            onClick={refreshSnapshot}
            className={`w-full py-3.5 rounded-xl text-[11px] font-bold tracking-widest transition-all flex items-center justify-center gap-2 ${
              isLoading ? 'bg-zinc-800 text-white' : 'bg-blue-600 text-white hover:bg-blue-500 shadow-xl shadow-blue-600/10'
            }`}
          >
            {isLoading ? 'SYNCING…' : 'SYNC SNAPSHOT'}
          </button>

          <button
            onClick={resetView}
            className="w-full py-3 rounded-xl text-[11px] font-bold border border-white/5 text-zinc-500 hover:text-white hover:bg-white/5 transition-all"
          >
            RESET VIEW
          </button>
        </div>
      </aside>

      {/* Main Orchestration Panel */}
      <main className="flex-1 flex flex-col bg-black overflow-hidden">
        {/* Top Intelligence Header */}
        <header className="grid grid-cols-2 md:grid-cols-4 bg-black/50 border-b border-white/5 lg:pt-0 pt-16">
          {[
            { label: 'Uptime', value: new Date(state.elapsedTime * 1000).toISOString().substr(11, 8), color: 'text-white' },
            { label: 'Progress', value: `${state.currentIteration} / ${state.totalIterations || '∞'}`, color: 'text-white' },
            { label: 'Token Density', value: state.totalTokens.toLocaleString(), color: 'text-white' },
            { label: 'Compute Cost', value: `$${state.totalCost.toFixed(4)}`, color: 'text-emerald-400' },
          ].map((stat, i) => (
            <div key={stat.label} className={`p-6 ${i < 3 ? 'border-r border-white/5' : ''}`}>
              <p className="text-[9px] font-bold text-zinc-600 uppercase tracking-widest mb-1">{stat.label}</p>
              <p className={`text-xl font-medium tracking-tight ${stat.color}`}>{stat.value}</p>
            </div>
          ))}
        </header>

        {/* Dashboard Content */}
        <div className="flex-1 overflow-y-auto p-6 md:p-10 space-y-10 no-scrollbar">
          {/* Main Visualization Grid */}
          <div className="grid grid-cols-1 xl:grid-cols-12 gap-8 md:gap-10">
            {/* Visualizer Module */}
            <div className="xl:col-span-5 bg-[#080808] border border-white/5 rounded-3xl p-10 flex flex-col items-center justify-center relative overflow-hidden group">
              <div className="absolute top-0 left-0 w-full h-[1px] bg-gradient-to-r from-transparent via-blue-500/20 to-transparent" />
              <LoopVisualizer status={state.status} iteration={state.currentIteration} />
              {runId && (
                <div className="mt-4 text-[10px] text-zinc-500 uppercase tracking-[0.2em]">
                  Run {runId.slice(0, 8)}
                </div>
              )}
            </div>

            {/* Iteration Logs Feed */}
            <div className="xl:col-span-7 bg-[#080808] border border-white/5 rounded-3xl flex flex-col h-[500px] md:h-auto min-h-[500px]">
              <div className="p-6 border-b border-white/5 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className={`w-2 h-2 rounded-full ${state.status === RunStatus.RUNNING ? 'bg-blue-600 animate-pulse' : 'bg-zinc-700'}`} />
                  <h3 className="text-[10px] font-bold text-zinc-400 uppercase tracking-[0.2em]">
                    {runId ? 'Live Stream' : 'Standby'}
                  </h3>
                </div>
                <div className="px-2 py-0.5 bg-white/5 border border-white/10 rounded text-[9px] mono text-zinc-500">
                  {isLoading ? 'Syncing' : runId ? 'Active' : 'Idle'}
                </div>
              </div>

              <div className="flex-1 overflow-y-auto p-6 space-y-4 no-scrollbar">
                {error && (
                  <div className="p-3 text-[11px] text-amber-400 bg-amber-500/10 border border-amber-500/30 rounded-lg">
                    {error}
                  </div>
                )}
                {state.logs.length === 0 ? (
                  <div className="h-full flex flex-col items-center justify-center opacity-20 scale-90">
                    <ICONS.Loop className="w-12 h-12 mb-4" />
                    <p className="text-xs font-medium tracking-widest uppercase">Awaiting telemetry</p>
                  </div>
                ) : (
                  state.logs.map((log) => (
                    <div
                      key={log.id}
                      className="p-5 bg-black/40 border border-white/5 rounded-2xl transition-all hover:bg-black/60 hover:border-white/10 animate-in fade-in duration-500"
                    >
                      <div className="flex items-center justify-between mb-4">
                        <div className="flex items-center gap-3">
                          <span className="text-[10px] font-bold bg-white/5 px-2 py-0.5 rounded text-white tracking-widest">
                            CYC-0{log.iteration}
                          </span>
                          <span
                            className={`px-2 py-0.5 rounded-full text-[9px] font-bold tracking-widest uppercase ${
                              log.status === 'success' ? 'text-emerald-500' : 'text-red-500'
                            }`}
                          >
                            {log.status}
                          </span>
                        </div>
                        <span className="text-[10px] mono text-zinc-600">
                          {new Date(log.timestamp).toLocaleTimeString([], { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                        </span>
                      </div>
                      <p className="text-zinc-300 text-xs leading-relaxed font-normal">{log.message || 'No output captured.'}</p>
                      <div className="mt-5 pt-4 border-t border-white/5 flex gap-6 text-[9px] font-bold text-zinc-600 tracking-widest uppercase">
                        <span>L-Tokens: {log.tokens}</span>
                        <span>Burn: ${log.cost.toFixed(5)}</span>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>

          {/* Performance Telemetry */}
          <div className="space-y-6">
            <div className="flex items-center justify-between px-2">
              <h3 className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">Performance Telemetry</h3>
              <div className="flex gap-6 text-[9px] font-bold text-zinc-600 uppercase tracking-widest">
                <div className="flex items-center gap-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-blue-600" /> Velocity
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-1.5 h-1.5 rounded-full bg-emerald-500" /> Capital
                </div>
              </div>
            </div>
            <MetricsVisualizer logs={state.logs} />
          </div>

          {/* Strategic Insight Module (CLOS Reflective Partner) */}
          <div className="bg-[#080808] border border-white/5 rounded-[2.5rem] p-10 md:p-14 relative overflow-hidden group">
            <div className="absolute -top-24 -right-24 w-64 h-64 bg-blue-600/5 blur-[120px] group-hover:bg-blue-600/10 transition-colors duration-1000" />

            <div className="flex flex-col md:flex-row items-start md:items-center gap-6 mb-12">
              <div className="p-4 bg-white/5 border border-white/10 rounded-3xl">
                <ICONS.Settings className="w-6 h-6 text-blue-500" />
              </div>
              <div>
                <h4 className="text-[11px] font-bold text-blue-500 uppercase tracking-[0.4em] mb-2">Metacognitive Observer</h4>
                <p className="text-xl font-medium tracking-tight text-white">Pattern Recognition & Semantic Drift Analysis</p>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-12 md:gap-16">
              {[
                { title: 'Reasoning Stability', desc: 'Analyzing the recursive path. System is currently maintaining a high semantic coherence score (0.94).', label: 'COHERENCE' },
                { title: 'Resource Efficiency', desc: 'Prompt engineering delta is positive. Token-to-insight ratio has improved by 14% this session.', label: 'EFFICIENCY' },
                { title: 'Predictive Convergence', desc: 'Estimating 12 more cycles to reach specified objective goal based on current pattern slope.', label: 'CONVERGENCE' },
              ].map(insight => (
                <div key={insight.title} className="space-y-4">
                  <span className="text-[9px] font-bold text-zinc-600 uppercase tracking-widest">{insight.label}</span>
                  <h5 className="text-sm font-semibold text-white tracking-tight">{insight.title}</h5>
                  <p className="text-xs text-zinc-500 leading-relaxed">{insight.desc}</p>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Mobile Toggle Button */}
        <button
          onClick={() => setIsSidebarOpen(true)}
          className="lg:hidden fixed bottom-6 right-6 p-4 bg-blue-600 rounded-2xl shadow-2xl z-50 text-white"
        >
          <ICONS.Settings className="w-6 h-6" />
        </button>
      </main>
    </div>
  );
};

export default App;
