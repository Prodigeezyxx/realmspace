"use client";

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
  const data = peopleSeries.map((v, i) => ({ minute: i - peopleSeries.length, people: v }));
  return (
    <div className="h-44 -mx-2 -mb-2">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 6, right: 10, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="trafficFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#00d4ff" stopOpacity={0.5} />
              <stop offset="100%" stopColor="#00d4ff" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="#1c1c20" vertical={false} />
          <XAxis
            dataKey="minute"
            stroke="#6c6c75"
            tickLine={false}
            axisLine={false}
            fontSize={10}
            tickFormatter={(v) => (v === 0 ? "now" : `${v}m`)}
            ticks={[-60, -45, -30, -15, 0]}
          />
          <YAxis
            stroke="#6c6c75"
            tickLine={false}
            axisLine={false}
            fontSize={10}
            width={30}
          />
          <Tooltip
            cursor={{ stroke: "#3e83f7", strokeWidth: 1, strokeDasharray: "2 2" }}
            contentStyle={{
              background: "#16161a",
              border: "1px solid #25252b",
              borderRadius: 8,
              fontSize: 11,
              padding: "6px 10px",
            }}
            labelFormatter={(v) => `${v}m ago`}
            formatter={(v) => [`${v}`, "people"]}
          />
          <Area
            type="monotone"
            dataKey="people"
            stroke="#00d4ff"
            strokeWidth={1.75}
            fill="url(#trafficFill)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
