
import React from 'react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LineChart, Line } from 'recharts';
import { IterationLog } from '../types';

interface MetricsVisualizerProps {
  logs: IterationLog[];
}

const MetricsVisualizer: React.FC<MetricsVisualizerProps> = ({ logs }) => {
  // Prep data for the time-series charts
  const data = logs.length > 0 
    ? logs.slice().reverse().map((l, idx, arr) => ({
        name: l.iteration,
        tokens: l.tokens,
        cost: l.cost,
        cumulativeCost: arr.slice(0, idx + 1).reduce((acc, curr) => acc + curr.cost, 0)
      }))
    : [{ name: 0, tokens: 0, cost: 0, cumulativeCost: 0 }];

  const CustomTooltip = ({ active, payload, label }: any) => {
    if (active && payload && payload.length) {
      return (
        <div className="bg-[#0c0c0c] border border-white/10 p-4 rounded-xl shadow-2xl backdrop-blur-xl">
          <p className="text-[9px] font-bold text-zinc-500 uppercase mb-3 tracking-widest">Cycle-0{label}</p>
          {payload.map((item: any, index: number) => (
            <div key={index} className="flex items-center justify-between gap-6 mb-1 last:mb-0">
              <span className="text-[10px] font-medium text-zinc-400 uppercase tracking-wider">{item.name}</span>
              <span className="text-xs font-bold text-white">
                {typeof item.value === 'number' && item.value < 1 ? item.value.toFixed(5) : item.value.toLocaleString()}
              </span>
            </div>
          ))}
        </div>
      );
    }
    return null;
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-8 h-[320px]">
      <div className="bg-[#080808] p-8 rounded-3xl border border-white/5">
        <h3 className="text-[9px] font-bold text-zinc-600 mb-8 uppercase tracking-[0.2em]">Throughput Velocity</h3>
        <ResponsiveContainer width="100%" height="80%">
          <AreaChart data={data} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
            <defs>
              <linearGradient id="colorTokens" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor="#2563eb" stopOpacity={0.2}/>
                <stop offset="95%" stopColor="#2563eb" stopOpacity={0}/>
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="0" stroke="#18181b" vertical={false} />
            <XAxis dataKey="name" stroke="#3f3f46" fontSize={9} tickLine={false} axisLine={false} dy={10} hide={logs.length < 2} />
            <YAxis stroke="#3f3f46" fontSize={9} tickLine={false} axisLine={false} hide />
            <Tooltip content={<CustomTooltip />} cursor={{ stroke: '#27272a', strokeWidth: 1 }} />
            <Area 
              type="monotone" 
              dataKey="tokens" 
              name="tokens"
              stroke="#2563eb" 
              strokeWidth={2}
              fillOpacity={1} 
              fill="url(#colorTokens)" 
              animationDuration={1500}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <div className="bg-[#080808] p-8 rounded-3xl border border-white/5">
        <h3 className="text-[9px] font-bold text-zinc-600 mb-8 uppercase tracking-[0.2em]">Capital Depletion</h3>
        <ResponsiveContainer width="100%" height="80%">
          <LineChart data={data} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
            <CartesianGrid strokeDasharray="0" stroke="#18181b" vertical={false} />
            <XAxis dataKey="name" stroke="#3f3f46" fontSize={9} tickLine={false} axisLine={false} dy={10} hide={logs.length < 2} />
            <YAxis stroke="#3f3f46" fontSize={9} tickLine={false} axisLine={false} hide />
            <Tooltip content={<CustomTooltip />} cursor={{ stroke: '#27272a', strokeWidth: 1 }} />
            <Line 
              type="monotone" 
              dataKey="cumulativeCost" 
              name="burn"
              stroke="#10b981" 
              strokeWidth={2.5}
              dot={false}
              animationDuration={2000}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
};

export default MetricsVisualizer;
