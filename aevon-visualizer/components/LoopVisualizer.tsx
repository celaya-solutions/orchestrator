
import React, { useEffect, useRef } from 'react';
import * as d3 from 'd3';
import { RunStatus } from '../types';

interface LoopVisualizerProps {
  status: RunStatus;
  iteration: number;
}

const LoopVisualizer: React.FC<LoopVisualizerProps> = ({ status, iteration }) => {
  const svgRef = useRef<SVGSVGElement>(null);

  useEffect(() => {
    if (!svgRef.current) return;

    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();

    const width = 400;
    const height = 400;
    const centerX = width / 2;
    const centerY = height / 2;
    const radius = 130;

    const stages = [
      { id: 'PROMPT', label: 'STRATEGIC FEED', angle: -90 },
      { id: 'AGENT', label: 'AUTONOMOUS AGENT', angle: 0 },
      { id: 'CHECKPOINT', label: 'DATA CHECKPOINT', angle: 90 },
      { id: 'EVAL', label: 'EVALUATION', angle: 180 },
    ];

    // Background Architecture
    svg.append('circle')
      .attr('cx', centerX)
      .attr('cy', centerY)
      .attr('r', radius)
      .attr('fill', 'none')
      .attr('stroke', '#1a1a1a')
      .attr('stroke-width', 1);

    // Subtle Grid Lines
    svg.append('line')
      .attr('x1', centerX - radius - 20).attr('y1', centerY)
      .attr('x2', centerX + radius + 20).attr('y2', centerY)
      .attr('stroke', '#111').attr('stroke-width', 1);

    svg.append('line')
      .attr('x1', centerX).attr('y1', centerY - radius - 20)
      .attr('x2', centerX).attr('y2', centerY + radius + 20)
      .attr('stroke', '#111').attr('stroke-width', 1);

    // Node Points
    stages.forEach((stage) => {
      const x = centerX + radius * Math.cos((stage.angle * Math.PI) / 180);
      const y = centerY + radius * Math.sin((stage.angle * Math.PI) / 180);

      const isActive = status === RunStatus.RUNNING;

      const group = svg.append('g')
        .attr('transform', `translate(${x}, ${y})`);

      group.append('circle')
        .attr('r', 4)
        .attr('fill', '#000')
        .attr('stroke', isActive ? '#2563eb' : '#27272a')
        .attr('stroke-width', 1.5);

      group.append('text')
        .attr('y', 20)
        .attr('text-anchor', 'middle')
        .attr('fill', '#52525b')
        .attr('font-size', '8px')
        .attr('font-weight', '700')
        .attr('letter-spacing', '0.2em')
        .text(stage.label);
    });

    // Loop Indicator (The Spark)
    if (status === RunStatus.RUNNING) {
      const spark = svg.append('circle')
        .attr('r', 2.5)
        .attr('fill', '#fff')
        .attr('style', 'filter: drop-shadow(0 0 4px #2563eb)');

      const animate = () => {
        spark.transition()
          .duration(2500)
          .ease(d3.easeCubicInOut)
          .attrTween('transform', () => {
            return (t: number) => {
              const angle = (t * 360 - 90) * (Math.PI / 180);
              const x = centerX + radius * Math.cos(angle);
              const y = centerY + radius * Math.sin(angle);
              return `translate(${x}, ${y})`;
            };
          })
          .on('end', animate);
      };
      animate();
    }

    // Central Data Readout
    const centerGroup = svg.append('g')
      .attr('transform', `translate(${centerX}, ${centerY})`);

    centerGroup.append('text')
      .attr('y', -5)
      .attr('text-anchor', 'middle')
      .attr('fill', '#fff')
      .attr('font-size', '72px')
      .attr('font-weight', '700')
      .attr('letter-spacing', '-0.04em')
      .text(iteration);

    centerGroup.append('text')
      .attr('y', 22)
      .attr('text-anchor', 'middle')
      .attr('fill', '#3f3f46')
      .attr('font-size', '10px')
      .attr('font-weight', '700')
      .attr('letter-spacing', '0.4em')
      .text('CYCLE LEVEL');

  }, [status, iteration]);

  return (
    <div className="flex flex-col items-center justify-center w-full h-full select-none">
      <svg 
        ref={svgRef} 
        viewBox="0 0 400 400" 
        className="w-full h-full max-w-[380px] drop-shadow-2xl" 
      />
      <div className="mt-6">
        <span className={`px-5 py-2 rounded-full text-[10px] font-bold tracking-[0.3em] border transition-all duration-700 ${
          status === RunStatus.RUNNING ? 'bg-blue-600 text-white border-blue-500 shadow-[0_0_30px_rgba(37,99,235,0.2)]' :
          status === RunStatus.COMPLETED ? 'bg-emerald-500/10 border-emerald-500/50 text-emerald-500' :
          'bg-white/5 border-white/10 text-zinc-500'
        }`}>
          {status === RunStatus.RUNNING ? 'SYSTEM ACTIVE' : status}
        </span>
      </div>
    </div>
  );
};

export default LoopVisualizer;
