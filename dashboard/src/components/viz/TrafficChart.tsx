"use client";

import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { peopleSeries } from "@/lib/mock/session";

export function TrafficChart() {
  const data = peopleSeries.map((v, i) => ({
    minute: i - peopleSeries.length,
    people: v,
  }));
  // Recharts cannot measure the container during SSR — only render after mount
  // to avoid the "width(-1) height(-1)" warning and the related hydration noise.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) {
    return <div className="h-44 -mx-2 -mb-2" aria-hidden />;
  }
  return (
    <div className="h-44 -mx-2 -mb-2">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 6, right: 10, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="trafficFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#0a6dd6" stopOpacity={0.22} />
              <stop offset="100%" stopColor="#0a6dd6" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="#e6e6ea" vertical={false} />
          <XAxis
            dataKey="minute"
            stroke="#86868b"
            tickLine={false}
            axisLine={false}
            fontSize={10}
            tickFormatter={(v) => (v === 0 ? "now" : `${v}m`)}
            ticks={[-60, -45, -30, -15, 0]}
          />
          <YAxis
            stroke="#86868b"
            tickLine={false}
            axisLine={false}
            fontSize={10}
            width={30}
          />
          <Tooltip
            cursor={{
              stroke: "#0a6dd6",
              strokeWidth: 1,
              strokeDasharray: "2 2",
            }}
            contentStyle={{
              background: "#ffffff",
              border: "1px solid #d9d9df",
              borderRadius: 8,
              fontSize: 11,
              padding: "6px 10px",
              boxShadow: "0 4px 16px -8px rgba(0,0,0,0.15)",
            }}
            labelStyle={{ color: "#1d1d1f" }}
            labelFormatter={(v) => `${v}m ago`}
            formatter={(v) => [`${v}`, "people"]}
          />
          <Area
            type="monotone"
            dataKey="people"
            stroke="#0a6dd6"
            strokeWidth={1.75}
            fill="url(#trafficFill)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
